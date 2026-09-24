import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'workers/media'), str(ROOT / 'packages/shared')]
from media_worker import translation_transport as transport
from media_worker.providers import GeminiProvider, retry_delay
from media_worker.translation_requests import SCHEMA, align_result
from media_worker.content_block import ContentBlockedError
from media_worker.transcription_queue import RetryableTranscriptionError
from media_worker.llm_diagnostics import attempt_context, new_context
from media_worker.llm_trace import capture_calls
from video_service.llm_settings import LLMSettingsStore
from video_service.transcript import TranscriptSegment


VALUES = [{'id': 'cue-000001', 'text': 'Translated'}]


def response_body(provider, values=VALUES):
    content = json.dumps({'translations': values})
    if provider == 'anthropic':
        return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': content}],
                'usage': {'input_tokens': 12, 'output_tokens': 5}}
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 5}}


class TranslationTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.factory = patch.object(transport.httpx, 'AsyncClient', return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.flag = patch.dict(os.environ, {'PAID_LLM_ENABLED': 'true'})
        self.flag.start()
        self.addCleanup(self.flag.stop)

    async def request(self, provider='openai', check=lambda: None):
        return await transport.request_translation(provider, 'test-secret-not-real', 'test-model',
            [{'text': 'Translate targets'}], SCHEMA, check, 1, retry_delay)

    async def test_all_external_protocols_and_key_isolation(self):
        for provider in ('openai', 'xai', 'openrouter', 'anthropic'):
            with self.subTest(provider=provider):
                self.client.post.return_value = httpx.Response(200, json=response_body(provider))
                self.assertEqual(await self.request(provider), VALUES)
                call = self.client.post.call_args
                payload = call.kwargs['json']
                self.assertNotIn('test-secret', json.dumps(payload))
                if provider == 'anthropic':
                    self.assertEqual(call.kwargs['headers']['x-api-key'], 'test-secret-not-real')
                    schema = payload['output_config']['format']['schema']
                    self.assertNotIn('response_format', payload)
                else:
                    self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer test-secret-not-real')
                    schema = payload['response_format']['json_schema']['schema']
                self.assertEqual(schema['type'], 'object')
                self.assertFalse(schema['properties']['translations']['items']['additionalProperties'])
                if provider == 'openrouter':
                    self.assertEqual(payload['provider'], {'require_parameters': True, 'allow_fallbacks': False})

    async def test_paid_lock_never_creates_client(self):
        with patch.dict(os.environ, {'PAID_LLM_ENABLED': 'false'}), patch.object(transport.httpx, 'AsyncClient') as factory:
            with self.assertRaisesRegex(RuntimeError, 'Paid LLM calls are disabled'):
                await self.request()
            factory.assert_not_called()

    async def test_429_is_one_request_and_shared_cooldown(self):
        self.client.post.return_value = httpx.Response(429, headers={'retry-after': '5'})
        with self.assertRaises(RetryableTranscriptionError) as error:
            await self.request()
        self.assertEqual(error.exception.delay, 5)
        self.assertTrue(error.exception.shared_cooldown)
        self.assertEqual(self.client.post.call_count, 1)

    async def test_401_does_not_echo_secret_or_retry(self):
        self.client.post.return_value = httpx.Response(401, text='test-secret-not-real')
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as error:
            await self.request()
        self.assertNotIn('test-secret', str(error.exception))
        self.assertEqual(self.client.post.call_count, 1)

    async def test_blocks_are_distinct_from_truncation_and_invalid_json(self):
        cases = [
            ('anthropic', {'stop_reason': 'refusal'}, ContentBlockedError),
            ('openai', {'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'No'}}]}, ContentBlockedError),
            ('xai', {'choices': [{'finish_reason': 'content_filter'}]}, ContentBlockedError),
            ('openrouter', {'choices': [{'finish_reason': 'length'}]}, ValueError),
            ('anthropic', {'stop_reason': 'max_tokens'}, ValueError),
            ('openai', {'choices': [{'finish_reason': 'stop', 'message': {'content': 'broken'}}]}, ValueError),
        ]
        for provider, body, kind in cases:
            self.client.post.return_value = httpx.Response(200, json=body)
            with self.assertRaises(kind) as error:
                await self.request(provider)
            if kind is ContentBlockedError:
                self.assertEqual(error.exception.provider, provider)

    async def test_transport_errors_are_sanitized(self):
        self.client.post.side_effect = httpx.ConnectError('test-secret-not-real')
        with self.assertRaises(RetryableTranscriptionError) as error:
            await self.request()
        self.assertNotIn('test-secret', str(error.exception))

    async def test_cancel_stops_inflight_request(self):
        stopped = asyncio.Event()
        async def never(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.client.post.side_effect = never
        task = asyncio.create_task(self.request())
        await asyncio.sleep(.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_trace_contains_provider_usage_and_redacts_key(self):
        folder = ROOT / 'data/test-runs' / uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        body = response_body('openai')
        body['echo'] = 'test-secret-not-real'
        self.client.post.return_value = httpx.Response(200, json=body)
        with capture_calls(folder, folder):
            await self.request()
        request = json.loads(next(folder.glob('*/request.json')).read_text())
        response = next(folder.glob('*/response.json')).read_text()
        self.assertEqual(request['provider'], 'openai')
        self.assertIn('prompt_tokens', response)
        self.assertNotIn('test-secret-not-real', response)


class MultiProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = GeminiProvider.__new__(GeminiProvider)
        self.provider.preferences = {'translation_provider': 'gemini', 'translation_fallback_provider': 'xai',
            'translation_fallback_model': 'same-model', 'fallback_on_error': True, 'fallback_on_block': True,
            'api_keys': {'xai': 'xai-test-secret'}}
        self.provider._request_once = AsyncMock(side_effect=RetryableTranscriptionError('busy'))
        self.external = AsyncMock(side_effect=[RetryableTranscriptionError('busy'), VALUES])
        self.patcher = patch('media_worker.providers.request_translation', self.external)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        token = attempt_context.set(new_context(stage='translation', segment=1, queue_id='test'))
        self.addCleanup(attempt_context.reset, token)

    async def request(self, attempt):
        return await self.provider._request([{'text': 'test'}], SCHEMA, lambda: None,
            'same-model', max_attempts=1, trace_attempt=attempt)

    async def test_same_model_name_different_provider_and_retry_budget(self):
        with self.assertRaises(RetryableTranscriptionError):
            await self.request(1)
        self.assertEqual(await self.request(2), VALUES)
        self.assertEqual(self.provider._request_once.call_count, 1)
        self.assertEqual(self.external.call_count, 2)
        self.assertEqual(self.external.call_args.args[:3], ('xai', 'xai-test-secret', 'same-model'))

    async def test_translation_setting_never_routes_stt_to_external_provider(self):
        self.provider.preferences['translation_provider'] = 'xai'
        self.provider._request_once.side_effect = None
        self.provider._request_once.return_value = VALUES
        token = attempt_context.set(new_context(stage='transcription', segment=1))
        try:
            self.assertEqual(await self.request(1), VALUES)
            self.external.assert_not_called()
        finally:
            attempt_context.reset(token)

    async def test_auth_failure_does_not_switch_provider(self):
        self.provider._request_once.side_effect = RuntimeError('HTTP 401')
        with self.assertRaises(RuntimeError):
            await self.request(1)
        self.external.assert_not_called()


class MultiProviderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.store = LLMSettingsStore(self.root)
        self.payload = {'revision': 0, 'transcription_model': 'test-stt', 'transcription_fallback_model': '',
            'translation_provider': 'openrouter', 'translation_model': 'vendor/model:free',
            'translation_fallback_provider': 'anthropic', 'translation_fallback_model': 'test-model',
            'fallback_on_error': True, 'fallback_on_block': True}

    def test_encrypted_multi_credentials_and_environment_fallback(self):
        keys = {p: f'test-{p}-secret-not-real-1234' for p in ('gemini', 'openai', 'xai', 'openrouter', 'anthropic')}
        public = self.store.update(None, {**self.payload, 'credential_updates': {p: {'api_key': k} for p, k in keys.items()}})
        for key in keys.values():
            self.assertNotIn(key, json.dumps(public))
            self.assertNotIn(key, self.store.path(None).read_text())
        self.assertEqual(self.store.resolve()['api_keys'], keys)
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'environment-router'}):
            self.store.update(None, {**self.payload, 'revision': 1, 'credential_updates': {'openrouter': {'remove_api_key': True}}})
            self.assertEqual(self.store.resolve()['api_keys']['openrouter'], 'environment-router')
            self.assertEqual(self.store.resolve()['api_keys']['anthropic'], keys['anthropic'])

    def test_unknown_providers_urls_and_credential_actions_rejected(self):
        for update in [
            {'translation_provider': 'https://evil.example'}, {'translation_model': '../model'},
            {'credential_updates': {'unknown': {'api_key': 'test-secret-not-real-12345'}}},
            {'credential_updates': {'openai': {'api_key': 'test-secret-not-real-12345', 'remove_api_key': True}}},
            {'transcription_model': 'vendor/model'}, {'base_url': 'https://evil.example'},
        ]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.store.update(None, {**self.payload, **update})

    def test_translation_only_does_not_require_gemini_key(self):
        self.store.update(None, {**self.payload, 'credential_updates': {
            'openrouter': {'api_key': 'router-test-not-real-12345'}, 'anthropic': {'api_key': 'anthropic-test-not-real-12345'}}})
        preferences = self.store.resolve()
        preferences['api_key'] = ''
        with patch('media_worker.providers.load_environment'):
            provider = GeminiProvider(preferences)
        provider.validate_for_stages(['translate'])
        with self.assertRaisesRegex(RuntimeError, 'gemini API key'):
            provider.validate_for_stages(['transcribe'])

    def test_primary_cache_identity_separates_providers(self):
        with patch('media_worker.providers.load_environment'):
            a = GeminiProvider({'translation_provider': 'gemini', 'translation_model': 'same-model'})
            b = GeminiProvider({'translation_provider': 'openai', 'translation_model': 'same-model'})
        self.assertEqual(a.translation_identity, 'same-model')
        self.assertEqual(b.translation_identity, 'openai:same-model')

    def test_external_translation_uses_existing_queue_and_checkpoint(self):
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.translation_provider = 'openrouter'
        provider.translation_model = 'vendor/model'
        provider.preferences = {'translation_provider': 'openrouter', 'api_keys': {'openrouter': 'router-key'}}
        work = self.root / 'job/work'
        work.mkdir(parents=True)
        segments = [TranscriptSegment(1, 2, 'Original')]
        progress = []
        with patch('media_worker.providers.request_translation', AsyncMock(return_value=VALUES)) as call:
            translated = provider.translate(segments, 'ko', lambda: None, work=work, progress=progress.append)
            again = provider.translate(segments, 'ko', lambda: None, work=work)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(translated, again)
        self.assertEqual(translated[0].text, 'Translated')
        self.assertEqual((translated[0].start, translated[0].end), (1, 2))
        self.assertEqual(progress[-1]['completed'], 1)
