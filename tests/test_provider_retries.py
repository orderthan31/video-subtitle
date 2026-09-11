from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
import json
import base64
import shutil
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider, retry_delay
from media_worker.transcription_queue import RetryableTranscriptionError
from media_worker.process import Cancelled
from media_worker.llm_trace import capture_calls, audio_window
from sdk_fixture import sdk_fixture


class ProviderRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = GeminiProvider.__new__(GeminiProvider)
        self.provider.key = "test-only"
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.patcher = patch.object(self.provider, "_client", sdk_fixture(self.client.post))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.success = httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": '["Translated"]'}]}}]})

    async def request(self, check=lambda: None):
        return await self.provider._request([], {}, check, "test-model")

    async def test_transient_connections_retry_and_succeed(self):
        self.client.post.side_effect = [httpx.ConnectError("offline"), httpx.ReadTimeout("timeout"), self.success]
        with patch("media_worker.providers.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertEqual(await self.request(), ["Translated"])
        self.assertEqual(self.client.post.call_count, 3)
        self.assertEqual(sum(call.args[0] for call in sleep.call_args_list), 3)

    async def test_exhausted_transport_does_not_expose_error_details(self):
        self.client.post.side_effect = httpx.ConnectError("sensitive diagnostic")
        with patch("media_worker.providers.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(RuntimeError, "connection failed after 3 attempts") as error:
                await self.request()
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(self.client.post.call_count, 3)

    async def test_rate_limit_respects_retry_after(self):
        self.client.post.side_effect = [httpx.Response(429, headers={"Retry-After": "2.5"}), self.success]
        with patch("media_worker.providers.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await self.request()
        self.assertEqual(sum(call.args[0] for call in sleep.call_args_list), 2.5)

    async def test_authentication_failure_is_not_retried(self):
        self.client.post.return_value = httpx.Response(401)
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            await self.request()
        self.assertEqual(self.client.post.call_count, 1)

    async def test_parallel_attempt_has_no_nested_transport_retries(self):
        self.client.post.return_value = httpx.Response(429, headers={"Retry-After": "2"})
        with self.assertRaises(RetryableTranscriptionError) as error:
            await self.provider._request([], {}, lambda: None, "test-model", max_attempts=1, trace_attempt=3)
        self.assertTrue(error.exception.shared_cooldown)
        self.assertEqual(error.exception.delay, 2)
        self.assertEqual(self.client.post.call_count, 1)

    def transcription_response(self, start, end):
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"audioTranscription": {"words": [{"word": "Private speech.",
                "startOffset": start, "endOffset": end}]}}]}}]})

    async def test_invalid_word_timing_retries_same_audio_without_dropping_speech(self):
        self.client.post.side_effect = [self.transcription_response("1s", "1s"),
            self.transcription_response("1s", "0.5s"), self.transcription_response("1s", "2s")]
        with patch("media_worker.providers.asyncio.sleep", new_callable=AsyncMock):
            result = await self.provider._request([{"text": "audio placeholder"}], None,
                lambda: None, "test-model", {"wordTimestamp": True})
        self.assertEqual(result[0]["text"], "Private speech.")
        self.assertEqual((result[0]["start"], result[0]["end"]), (1, 2))
        calls = self.client.post.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call.kwargs["json"] == calls[0].kwargs["json"] for call in calls))

    async def test_sdk_serializes_native_audio_and_transcription_config(self):
        self.client.post.return_value = self.transcription_response("0s", "1s")
        audio = b"RIFF-test-audio"
        await self.provider._request([{"inlineData": {"mimeType": "audio/wav",
            "data": base64.b64encode(audio).decode("ascii")}}], None, lambda: None,
            "gemini-3.5-transcribe", {"wordTimestamp": True, "mode": "VERBATIM", "languageCodes": ["en-US"]})
        call = self.client.post.call_args
        self.assertIn("/models/gemini-3.5-transcribe:generateContent", call.args[0])
        payload = call.kwargs["json"]
        self.assertEqual(len(payload["safetySettings"]), 4)
        self.assertTrue(all(setting["threshold"] == "OFF" for setting in payload["safetySettings"]))
        inline = payload["contents"][0]["parts"][0]["inlineData"]
        self.assertEqual(base64.b64decode(inline["data"]), audio)
        self.assertEqual(inline["mimeType"], "audio/wav")
        # The official SDK uses protobuf snake_case for this nested config.
        self.assertEqual(payload["generationConfig"]["audioTranscriptionConfig"],
            {"word_timestamp": True, "mode": "VERBATIM", "language_codes": ["en-US"]})

    async def test_persistent_invalid_timing_fails_bounded_without_private_text(self):
        self.client.post.return_value = self.transcription_response("1s", "1s")
        with patch("media_worker.providers.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(ValueError, "after 3 attempts:.*zero-duration") as error:
                await self.provider._request([], None, lambda: None, "test-model", {"wordTimestamp": True})
        self.assertNotIn("Private speech", str(error.exception))
        self.assertEqual(self.client.post.call_count, 3)

    async def test_invalid_timing_retry_can_be_cancelled(self):
        self.client.post.return_value = self.transcription_response("1s", "1s")
        with patch("media_worker.providers.wait_for_retry", side_effect=Cancelled):
            with self.assertRaises(Cancelled):
                await self.provider._request([], None, lambda: None, "test-model", {"wordTimestamp": True})
        self.assertEqual(self.client.post.call_count, 1)

    async def test_preserved_traces_capture_each_raw_response_before_validation(self):
        root = ROOT / "data/test-runs" / uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        self.client.post.return_value = self.transcription_response("2s", "1s")
        with capture_calls(root, root / "llm"), audio_window(100, 200, 100, 2), \
                patch("media_worker.providers.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(ValueError):
                await self.provider._request([{"text": "prompt test-only"}], None,
                    lambda: None, "test-model", {"wordTimestamp": True})
        folders = sorted((root / "llm").iterdir())
        self.assertEqual(len(folders), 3)
        for index, folder in enumerate(folders):
            request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
            response = json.loads((folder / "response.json").read_text(encoding="utf-8"))
            self.assertEqual(request["attempt"], index + 1)
            self.assertEqual(request["window"]["split_depth"], 2)
            self.assertEqual(request["payload"]["contents"][0]["parts"][0]["text"], "prompt [REDACTED]")
            self.assertEqual(response["status"], 200)
            self.assertEqual(response["body"], self.client.post.return_value.text)
            self.assertNotIn("test-only", (folder / "request.json").read_text())
        self.client.post.return_value = self.success
        await self.request()
        self.assertEqual(len(list((root / "llm").iterdir())), 3)

    async def test_incomplete_output_is_not_retried(self):
        self.client.post.return_value = httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]})
        with self.assertRaisesRegex(ValueError, "incomplete or blocked"):
            await self.request()
        self.assertEqual(self.client.post.call_count, 1)

    async def test_cancellation_during_backoff_stops_retry(self):
        cancelled = False
        def check():
            if cancelled:
                raise Cancelled()
        async def stop(delay):
            nonlocal cancelled
            cancelled = True
        self.client.post.return_value = httpx.Response(503)
        with patch("media_worker.providers.asyncio.sleep", side_effect=stop), self.assertRaises(Cancelled):
            await self.request(check)
        self.assertEqual(self.client.post.call_count, 1)

    def test_retry_after_date_invalid_and_excessive_values(self):
        with patch("media_worker.providers.time.time", return_value=0):
            self.assertEqual(retry_delay(httpx.Response(503, headers={
                "retry-after": "Thu, 01 Jan 1970 00:00:10 GMT"}), 0), 10)
        for value in ("invalid", "NaN", "-1"):
            self.assertEqual(retry_delay(httpx.Response(503, headers={"retry-after": value}), 1), 2)
        with self.assertRaisesRegex(RuntimeError, "over 60 seconds"):
            retry_delay(httpx.Response(429, headers={"retry-after": "120"}), 0)
