from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'workers/media'), str(ROOT / 'packages/shared')]
from media_worker.providers import GeminiProvider, ModelRequestError
from media_worker.content_block import ContentBlockedError
from media_worker.transcription_queue import RetryableTranscriptionError
from media_worker.llm_diagnostics import attempt_context, new_context


class ModelFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = GeminiProvider.__new__(GeminiProvider)
        self.provider.preferences = {'translation_fallback_model': 'backup', 'fallback_on_error': True, 'fallback_on_block': True}
        self.provider._request_once = AsyncMock()
        token = attempt_context.set(new_context(stage='translation', segment=3, queue_id='test'))
        self.addCleanup(attempt_context.reset, token)

    async def request(self, attempt=1):
        return await self.provider._request([], {}, lambda: None, 'primary', max_attempts=1, trace_attempt=attempt)

    async def test_block_falls_back_once_and_success_is_returned(self):
        self.provider._request_once.side_effect = [ContentBlockedError('SAFETY'), ['translated']]
        self.assertEqual(await self.request(), ['translated'])
        self.assertEqual([c.args[3] for c in self.provider._request_once.call_args_list], ['primary', 'backup'])

    async def test_fallback_block_is_propagated_for_placeholder_without_loop(self):
        self.provider._request_once.side_effect = ContentBlockedError('SAFETY')
        with self.assertRaises(ContentBlockedError):
            await self.request()
        self.assertEqual(self.provider._request_once.call_count, 2)

    async def test_disabled_block_policy_does_not_try_fallback(self):
        self.provider.preferences['fallback_on_block'] = False
        self.provider._request_once.side_effect = ContentBlockedError('SAFETY')
        with self.assertRaises(ContentBlockedError):
            await self.request()
        self.assertEqual(self.provider._request_once.call_count, 1)

    async def test_retry_stays_on_fallback_and_never_repeats_primary(self):
        self.provider._request_once.side_effect = [RetryableTranscriptionError('busy'), RetryableTranscriptionError('busy'), ['ok']]
        with self.assertRaises(RetryableTranscriptionError):
            await self.request()
        self.assertEqual(await self.request(2), ['ok'])
        self.assertEqual([c.args[3] for c in self.provider._request_once.call_args_list], ['primary', 'backup', 'backup'])

    async def test_local_storage_and_auth_failures_do_not_trigger_fallback(self):
        self.provider._request_once.side_effect = RuntimeError('Gemini request failed (HTTP 401)')
        with self.assertRaises(RuntimeError):
            await self.request()
        self.assertEqual(self.provider._request_once.call_count, 1)

    async def test_unavailable_primary_model_uses_fallback(self):
        self.provider._request_once.side_effect = [ModelRequestError('HTTP 404'), ['ok']]
        self.assertEqual(await self.request(), ['ok'])
