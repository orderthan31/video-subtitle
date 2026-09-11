import asyncio
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider
from media_worker.transcription_queue import PartialTranslationError
from video_service.transcript import TranscriptSegment


class ParallelTranslationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.work = self.root / "job/work"
        self.work.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.provider = GeminiProvider.__new__(GeminiProvider)
        self.provider.translation_model = "test-model"
        self.segments = [TranscriptSegment(i, i + 0.5, str(i)) for i in range(125)]

    def test_refill_out_of_order_results_preserve_timing_and_resume_without_requests(self):
        active = 0
        peak = 0
        started = []
        async def request(parts, *args, **kwargs):
            nonlocal active, peak
            texts = json.loads(parts[0]["text"].split("\n", 1)[1])
            active += 1
            peak = max(peak, active)
            started.append(int(texts[0]))
            await asyncio.sleep(0.03 if texts[0] == "0" else 0)
            active -= 1
            self.assertEqual(kwargs["max_attempts"], 1)
            return ["T" + text for text in texts]
        self.provider._request = AsyncMock(side_effect=request)
        result = self.provider.translate(self.segments, "ko", lambda: None, work=self.work)
        self.assertEqual(peak, 3)
        self.assertEqual(started, [0, 40, 80, 120])
        self.assertEqual([cue.text for cue in result], ["T" + str(i) for i in range(125)])
        self.assertEqual([(cue.start, cue.end) for cue in result], [(cue.start, cue.end) for cue in self.segments])
        self.provider._request.reset_mock()
        self.provider.translate(self.segments, "ko", lambda: None, work=self.work)
        self.provider._request.assert_not_called()

    def test_bad_response_gets_three_attempts_and_uses_translation_error(self):
        self.provider._request = AsyncMock(return_value=[])
        with self.assertRaises(PartialTranslationError) as error:
            self.provider.translate(self.segments[:2], "ko", lambda: None, work=self.work)
        self.assertEqual(self.provider._request.call_count, 3)
        self.assertEqual(error.exception.failed, [0])
        self.assertIn("Translation", str(error.exception))
        self.assertEqual([c.kwargs["trace_attempt"] for c in self.provider._request.call_args_list], [1, 2, 3])

    def test_failure_keeps_later_success_and_retry_only_fills_missing_batch(self):
        async def request(parts, *args, **kwargs):
            texts = json.loads(parts[0]["text"].split("\n", 1)[1])
            if texts[0] == "0":
                raise RuntimeError("HTTP 401")
            return texts
        self.provider._request = AsyncMock(side_effect=request)
        with self.assertRaises(PartialTranslationError) as error:
            self.provider.translate(self.segments[:85], "ko", lambda: None, work=self.work)
        self.assertEqual(len(error.exception.segments), 45)
        self.assertEqual(error.exception.prefix, [])
        self.provider._request = AsyncMock(return_value=[str(i) for i in range(40)])
        result = self.provider.translate(self.segments[:85], "ko", lambda: None, work=self.work)
        self.assertEqual(len(result), 85)
        self.provider._request.assert_called_once()

    def test_empty_transcript_makes_no_requests(self):
        self.provider._request = AsyncMock()
        self.assertEqual(self.provider.translate([], "ko", lambda: None, work=self.work), [])
        self.provider._request.assert_not_called()
