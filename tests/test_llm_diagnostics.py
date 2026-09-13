import asyncio
import json
from pathlib import Path
import socket
import ssl
import sys
import shutil
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.llm_diagnostics import attempt_context, new_context, exception_details, emit
from media_worker.llm_trace import capture_calls
from media_worker.providers import GeminiProvider
from media_worker.transcription_queue import run_segment_queue, PartialTranscriptionError, PartialTranslationError
from sdk_fixture import sdk_fixture


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = GeminiProvider.__new__(GeminiProvider)
        self.provider.key = "test-only"
        self.post = AsyncMock()
        fixture = patch.object(self.provider, "_client", sdk_fixture(self.post))
        fixture.start()
        self.addCleanup(fixture.stop)
        self.context = new_context()
        token = attempt_context.set(self.context)
        self.addCleanup(attempt_context.reset, token)
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.path = self.root / "job" / "work" / "transcription" / "state.json"
        self.success = lambda: httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": '["private dialogue"]'}]}}]})

    async def request(self, index=0, attempt=1):
        return await self.provider._request([], {}, lambda: None, "test-model", max_attempts=1, trace_attempt=attempt)

    def events(self):
        return self.context["events"]

    def test_nested_causes_have_codes_but_no_messages(self):
        for cause, category in ((socket.gaierror(11001, "secret hostname"), "dns_resolution_failure"),
                                (ssl.SSLError(1, "private certificate"), "tls_failure")):
            error = httpx.ConnectError("key=test-only")
            error.__cause__ = cause
            details = exception_details(error)
            self.assertEqual(details["category"], category)
            self.assertEqual(details["exception_chain"][1]["errno"], cause.errno)
            self.assertNotIn("secret", json.dumps(details))
            self.assertNotIn("private", json.dumps(details))
            self.assertNotIn("test-only", json.dumps(details))
            cause.__context__ = error
            self.assertEqual(len(exception_details(error)["exception_chain"]), 2)

    async def test_http_attribution_and_header_allowlist(self):
        for status, category in ((429, "remote_rate_limit"), (503, "remote_http_error"),
                                 (401, "authentication_or_permission_rejected")):
            self.post.return_value = httpx.Response(status, headers={"retry-after": "2",
                "x-request-id": "request-123", "authorization": "secret", "set-cookie": "private",
                "x-goog-request-id": "test-only"})
            with self.assertRaises(Exception):
                await self.request()
            event = [e for e in self.events() if e["event"] == "http_headers"][-1]
            self.assertEqual(event["category"], category)
            self.assertEqual(event["status"], status)
            self.assertEqual(event["response_identifiers"], {"retry-after": "2", "x-request-id": "request-123"})
        self.assertNotIn("test-only", json.dumps(self.events()))

    async def test_body_read_failure_retains_status_evidence(self):
        class BrokenBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{'
                raise httpx.ReadError("secret response body")
        self.post.return_value = httpx.Response(200, stream=BrokenBody())
        with self.assertRaises(Exception):
            await self.request()
        self.assertTrue(any(e.get("status") == 200 for e in self.events()))
        self.assertTrue(any(e.get("category") == "network_path_unknown" for e in self.events()))
        self.assertNotIn("secret response", json.dumps(self.events()))

    async def test_invalid_model_json_is_distinct_from_http_error(self):
        self.post.return_value = httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": "not-json private dialogue"}]}}]})
        with self.assertRaises(ValueError):
            await self.request()
        self.assertTrue(any(e.get("category") == "remote_output_invalid_json" for e in self.events()))
        self.assertNotIn("private dialogue", json.dumps(self.events()))

    async def test_parallel_trace_correlation_is_persisted_without_payload(self):
        self.post.side_effect = lambda *args, **kwargs: self.success()
        with capture_calls(self.root, self.root / "llm"):
            await run_segment_queue(3, self.request, lambda: None, lambda _: None, path=self.path)
        history = json.loads(self.path.read_text())["history"]
        ids = set()
        for entry in history:
            identity = entry["diagnostics"]["identity"]
            ids.add(identity["attempt_id"])
            self.assertEqual(identity["segment"], entry["segment"])
            events = entry["diagnostics"]["events"]
            self.assertTrue(all(e["attempt_id"] == identity["attempt_id"] for e in events))
            trace = next(e["trace_id"] for e in events if e["event"] == "request_started")
            request = json.loads((self.root / "llm" / trace / "request.json").read_text())
            self.assertEqual(request["diagnostic_context"], identity)
            self.assertNotIn("private dialogue", json.dumps(events))
        self.assertEqual(len(ids), 3)

    async def test_local_validator_failure_and_exhaustion_are_explicit(self):
        self.post.return_value = self.success()
        def validate(index, result):
            raise ValueError("Invalid STT sentence timestamps")
        with self.assertRaises(PartialTranscriptionError):
            await run_segment_queue(1, self.request, lambda: None, lambda _: None,
                path=self.path, validate=validate, attempts=1)
        event = json.loads(self.path.read_text())["history"][0]["diagnostics"]["events"][-1]
        self.assertEqual(event["failure_phase"], "output_validation")
        self.assertEqual(event["reason"], "stt_sentence_timestamps")
        self.assertTrue(event["exhausted"])
        self.assertFalse(event["retry_scheduled"])

    async def test_deadline_is_distinct_from_operation_timeout(self):
        wait_for = asyncio.wait_for
        async def short_wait(task, timeout):
            return await wait_for(task, .01)
        async def operation(index, attempt):
            await asyncio.Event().wait()
        with patch("media_worker.transcription_queue.asyncio.wait_for", short_wait):
            with self.assertRaises(PartialTranscriptionError):
                await run_segment_queue(1, operation, lambda: None, lambda _: None, path=self.path, attempts=1)
        events = json.loads(self.path.read_text())["history"][0]["diagnostics"]["events"]
        self.assertTrue(any(e.get("category") == "local_queue_deadline" for e in events))

    async def test_cancellation_does_not_claim_provider_failure(self):
        self.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.request()
        self.assertTrue(any(e.get("category") == "cancellation_cause_unknown" for e in self.events()))
        self.assertFalse(any(e.get("category") == "remote_http_error" for e in self.events()))

    async def test_operation_timeout_is_not_mislabeled_as_queue_deadline(self):
        async def operation(index, attempt):
            raise TimeoutError("private diagnostic")
        with self.assertRaises(PartialTranslationError):
            await run_segment_queue(1, operation, lambda: None, lambda _: None,
                path=self.path, attempts=1, failure_type=PartialTranslationError)
        diagnostic = json.loads(self.path.read_text())["history"][0]["diagnostics"]
        self.assertEqual(diagnostic["identity"]["stage"], "translation")
        self.assertTrue(any(e.get("category") == "operation_timeout" for e in diagnostic["events"]))
        self.assertFalse(any(e.get("category") == "local_queue_deadline" for e in diagnostic["events"]))
        self.assertNotIn("private diagnostic", json.dumps(diagnostic))

    async def test_logging_failure_does_not_fail_successful_request(self):
        self.post.return_value = self.success()
        with patch("media_worker.llm_diagnostics.logging.getLogger", side_effect=OSError("disk full")):
            self.assertEqual(await self.request(), ["private dialogue"])
        self.assertEqual(self.post.call_count, 1)
