"""Per-account model preferences and encrypted, write-only API credentials."""
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from .locking import _file_lock
from .storage import read_json
from .llm_providers import PROVIDERS, valid_model


MODEL_FIELDS = ('transcription_model', 'translation_model', 'transcription_fallback_model', 'translation_fallback_model')
FLAGS = ('fallback_on_error', 'fallback_on_block')
PROVIDER_FIELDS = ('translation_provider', 'translation_fallback_provider')


class SettingsConflict(ValueError):
    pass


class LLMSettingsStore:
    def __init__(self, root):
        self.directory = Path(root) / '.llm-settings'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, owner):
        if owner is not None and not re.fullmatch(r'[a-f0-9]{32}', owner):
            raise ValueError('Invalid settings owner')
        return self.directory / ((owner or 'local') + '.json')

    def _write(self, path, value):
        temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def _cipher(self):
        path = self.directory / 'master.key'
        if not path.exists():
            raise ValueError('Credential encryption key is missing')
        return Fernet(path.read_bytes())

    def _read(self, owner):
        try:
            return read_json(self.path(owner))
        except FileNotFoundError:
            return {'revision': 0}

    def resolve(self, owner=None):
        saved = self._read(owner)
        result = {
            'transcription_model': os.getenv('GEMINI_TRANSCRIPTION_MODEL', 'gemini-3.8-flash'),
            'translation_model': os.getenv('GEMINI_TRANSLATION_MODEL', 'gemini-3.8-flash'),
            'transcription_fallback_model': '', 'translation_fallback_model': '',
            'fallback_on_error': False, 'fallback_on_block': False,
            'translation_provider': 'gemini', 'translation_fallback_provider': 'gemini',
            **{key: saved[key] for key in (*MODEL_FIELDS, *FLAGS, *PROVIDER_FIELDS) if key in saved},
            'revision': saved['revision'],
        }
        encrypted = dict(saved.get('credentials', {}))
        if saved.get('credential'):
            encrypted['gemini'] = saved['credential']
        result['api_keys'] = {}
        result['credentials'] = {}
        for provider, config in PROVIDERS.items():
            key = self._cipher().decrypt(encrypted[provider].encode()).decode() if encrypted.get(provider) else os.getenv(config['env'], '')
            source = 'registered' if encrypted.get(provider) else 'environment' if key else 'none'
            result['api_keys'][provider] = key
            result['credentials'][provider] = {'has_api_key': bool(key), 'key_source': source}
        result['api_key'] = result['api_keys']['gemini']
        result['key_source'] = result['credentials']['gemini']['key_source']
        return result

    def public(self, owner=None):
        result = self.resolve(owner)
        result['has_api_key'] = bool(result.pop('api_key'))
        result.pop('api_keys')
        return result

    def update(self, owner, payload):
        if not isinstance(payload, dict) or set(payload) - {*MODEL_FIELDS, *FLAGS, *PROVIDER_FIELDS, 'revision', 'api_key', 'remove_api_key', 'credential_updates'}:
            raise ValueError('Invalid settings fields')
        saved_providers = self._read(owner)
        providers = {field: payload.get(field, saved_providers.get(field, 'gemini')) for field in PROVIDER_FIELDS}
        if any(not isinstance(value, str) or value not in PROVIDERS for value in providers.values()):
            raise ValueError('Invalid translation provider')
        for field in MODEL_FIELDS:
            value = payload.get(field)
            provider = providers['translation_fallback_provider' if 'fallback' in field else 'translation_provider'] if field.startswith('translation') else 'gemini'
            if not isinstance(value, str) or (not value and not field.endswith('fallback_model')) or (value and not valid_model(provider, value)):
                raise ValueError('Invalid model name')
        for field in FLAGS:
            if type(payload.get(field)) is not bool:
                raise ValueError('Invalid fallback policy')
        for stage in ('transcription', 'translation'):
            same_provider = stage == 'transcription' or providers['translation_provider'] == providers['translation_fallback_provider']
            if same_provider and payload[stage + '_model'] == payload[stage + '_fallback_model']:
                raise ValueError('Fallback model must differ from primary model')
        key = payload.get('api_key')
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', key)):
            raise ValueError('Invalid API key format')
        if type(payload.get('remove_api_key', False)) is not bool or (key and payload.get('remove_api_key')):
            raise ValueError('Invalid key action')
        actions = payload.get('credential_updates', {})
        if not isinstance(actions, dict) or set(actions) - set(PROVIDERS):
            raise ValueError('Invalid credential providers')
        if 'gemini' in actions and (key is not None or payload.get('remove_api_key')):
            raise ValueError('Duplicate Gemini credential action')
        actions = dict(actions)
        if key is not None or payload.get('remove_api_key'):
            actions['gemini'] = {'api_key': key, 'remove_api_key': payload.get('remove_api_key', False)}
        for action in actions.values():
            if not isinstance(action, dict) or set(action) - {'api_key', 'remove_api_key'}:
                raise ValueError('Invalid credential action')
            value = action.get('api_key')
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{20,512}', value)):
                raise ValueError('Invalid API key format')
            if type(action.get('remove_api_key', False)) is not bool or (value and action.get('remove_api_key')):
                raise ValueError('Invalid key action')
        with _file_lock(self.directory / 'settings.lock'):
            saved = self._read(owner)
            if type(payload.get('revision')) is not int or payload['revision'] != saved['revision']:
                raise SettingsConflict('Settings changed; reload before saving')
            updated = {**saved, **providers, **{field: payload[field] for field in (*MODEL_FIELDS, *FLAGS)}, 'revision': saved['revision'] + 1}
            if any(action.get('api_key') for action in actions.values()):
                master = self.directory / 'master.key'
                if not master.exists():
                    if any(self.directory.glob('*.json')):
                        # Do not silently replace a lost key for existing credentials.
                        if any(read_json(p).get('credential') or read_json(p).get('credentials') for p in self.directory.glob('*.json')):
                            raise ValueError('Credential encryption key is missing')
                    self._write(master, Fernet.generate_key())
            credentials = dict(updated.get('credentials', {}))
            if updated.get('credential'):
                credentials['gemini'] = updated.pop('credential')
            for provider, action in actions.items():
                if action.get('api_key'):
                    credentials[provider] = self._cipher().encrypt(action['api_key'].encode()).decode()
                if action.get('remove_api_key'):
                    credentials.pop(provider, None)
            updated['credentials'] = credentials
            self._write(self.path(owner), json.dumps(updated).encode())
            return self.public(owner)
