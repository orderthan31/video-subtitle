import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch, AsyncMock
from uuid import uuid4
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api')]
from fastapi.testclient import TestClient
from app.main import create_app
from app.api import settings_routes
from app.api.auth_routes import AuthConfig
from video_service.auth import AuthStore
from video_service.llm_settings import LLMSettingsStore, SettingsConflict


class LLMSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.store = LLMSettingsStore(self.root)
        self.payload = {'revision': 0, 'transcription_model': 'test-stt', 'translation_model': 'test-translation',
            'transcription_fallback_model': 'fallback-stt', 'translation_fallback_model': 'fallback-translation',
            'fallback_on_error': True, 'fallback_on_block': False}
        self.key = 'test-secret-key-not-real-123456'

    def test_key_encrypted_write_only_and_owner_isolated(self):
        owner = 'a' * 32
        public = self.store.update(owner, {**self.payload, 'api_key': self.key})
        self.assertNotIn(self.key, json.dumps(public))
        self.assertNotIn(self.key, self.store.path(owner).read_text())
        self.assertEqual(self.store.resolve(owner)['api_key'], self.key)
        self.assertEqual(public['key_source'], 'registered')
        self.assertEqual(self.store.public('b' * 32)['revision'], 0)
        with self.assertRaises(SettingsConflict):
            self.store.update(owner, self.payload)
        if os.name != 'nt':
            self.assertEqual(self.store.path(owner).stat().st_mode & 0o777, 0o600)

    def test_blank_key_is_not_overwrite_and_removal_returns_to_environment(self):
        self.store.update(None, {**self.payload, 'api_key': self.key})
        self.store.update(None, {**self.payload, 'revision': 1})
        self.assertEqual(self.store.resolve()['api_key'], self.key)
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'environment-key'}):
            public = self.store.update(None, {**self.payload, 'revision': 2, 'remove_api_key': True})
            self.assertEqual(public['key_source'], 'environment')
            self.assertEqual(self.store.resolve()['api_key'], 'environment-key')

    def test_reject_path_and_invalid_models(self):
        with self.assertRaises(ValueError):
            self.store.public('../escape')
        for update in ({'translation_model': '../bad'}, {'translation_fallback_model': 'test-translation'},
                       {'api_key': ''}, {'fallback_on_error': 'true'}):
            with self.assertRaises(ValueError):
                self.store.update(None, {**self.payload, **update})

    def test_api_has_no_secret_echo_and_requires_same_origin(self):
        app = create_app()
        app.state.auth = AuthConfig(None)
        client = TestClient(app)
        with patch.object(settings_routes, 'settings', type('Config', (), {'storage_root': self.root})()):
            response = client.put('/api/settings/llm', json={**self.payload, 'api_key': self.key})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(self.key, response.text)
            self.assertNotIn(self.key, client.get('/api/settings/llm').text)
            invalid = client.put('/api/settings/llm', json={**self.payload, 'api_key': [self.key]})
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn(self.key, invalid.text)
            self.assertEqual(client.put('/api/settings/llm', json=self.payload, headers={'Origin': 'https://evil.example'}).status_code, 403)
            self.assertEqual(client.put('/api/settings/llm', content='x' * 9000, headers={'Content-Type': 'application/json'}).status_code, 413)

    def test_authentication_and_csrf_required_when_enabled(self):
        app = create_app()
        auth = AuthStore(self.root / 'auth.sqlite3')
        auth.create_user('alice', 'test-password-12345')
        app.state.auth = AuthConfig(auth, secure=False)
        client = TestClient(app)
        with patch.object(settings_routes, 'settings', type('Config', (), {'storage_root': self.root})()):
            self.assertEqual(client.get('/api/settings/llm').status_code, 401)
            login = client.post('/api/auth/login', json={'username': 'alice', 'password': 'test-password-12345'})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(client.put('/api/settings/llm', json=self.payload).status_code, 403)
            response = client.put('/api/settings/llm', json=self.payload, headers={'X-CSRF-Token': login.json()['csrf_token']})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.store.public()['revision'], 0)

    def test_model_list_uses_header_secret_and_sanitizes_upstream_errors(self):
        self.store.update(None, {**self.payload, 'api_key': self.key})
        app = create_app()
        app.state.auth = AuthConfig(None)
        client = TestClient(app)
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream
        upstream.get.return_value = httpx.Response(200, json={'models': [
            {'name': 'models/test-model', 'supportedGenerationMethods': ['generateContent']},
            {'name': 'models/embedding-only', 'supportedGenerationMethods': ['embedContent']}]},
            request=httpx.Request('GET', 'https://generativelanguage.googleapis.com/v1beta/models'))
        with patch.object(settings_routes, 'settings', type('Config', (), {'storage_root': self.root})()), \
                patch.object(settings_routes.httpx, 'AsyncClient', return_value=upstream):
            response = client.get('/api/settings/llm/models')
            self.assertEqual(response.json(), {'models': ['test-model']})
            self.assertEqual(upstream.get.call_args.kwargs['headers']['x-goog-api-key'], self.key)
            self.assertNotIn(self.key, upstream.get.call_args.args[0])
            upstream.get.side_effect = RuntimeError(self.key)
            error = client.get('/api/settings/llm/models')
            self.assertEqual(error.status_code, 502)
            self.assertNotIn(self.key, error.text)
