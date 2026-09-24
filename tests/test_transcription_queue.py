import asyncio
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.transcription_queue import run_transcription_queue, PartialTranscriptionError, RetryableTranscriptionError
from video_service.storage import read_json
from media_worker.content_block import ContentBlockedError
from media_worker.transcription_queue import PartialTranslationError


class TranscriptionQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.path = self.root / "state.json"

    async def test_three_slots_refill_without_waiting_for_slowest(self):
        release = asyncio.Event()
        active = 0
        peak = 0
        order = []
        async def operation(index, attempt):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append(index)
            if index == 0:
                await release.wait()
            elif index == 3:
                release.set()
            await asyncio.sleep(0)
            active -= 1
            return [index]
        progress = Mock()
        result = await asyncio.wait_for(run_transcription_queue(6, operation, lambda: None, progress), 2)
        self.assertEqual(peak, 3)
        self.assertEqual(sorted(result), list(range(6)))
        self.assertEqual(order[:3], [0, 1, 2])
        self.assertEqual(progress.call_args.args[0]["completed"], 6)

    async def test_exhausted_segment_stops_admission_drains_and_resumes_only_missing(self):
        release = asyncio.Event()
        called = []
        async def operation(index, attempt):
            called.append((index, attempt))
            if index == 0:
                if attempt == 3:
                    release.set()
                raise RetryableTranscriptionError("429", delay=1, shared_cooldown=True)
            if index in (1, 2):
                await release.wait()
                await asyncio.sleep(0.05)
            return [index]
        snapshots = []
        with self.assertRaises(PartialTranscriptionError) as error:
            await run_transcription_queue(7, operation, lambda: None, snapshots.append, path=self.path)
        self.assertEqual(error.exception.failed, [0])
        self.assertEqual(sorted(error.exception.results), [1, 2])
        self.assertEqual([item for item in called if item[0] == 0], [(0, 1), (0, 2), (0, 3)])
        self.assertTrue(all(index < 3 for index, _ in called))
        self.assertTrue(any(item["draining"] for item in snapshots))
        failures = [item for item in read_json(self.path)["history"] if item["segment"] == 0]
        self.assertEqual([item["diagnostics"]["events"][-1]["exhausted"] for item in failures], [False, False, True])
        self.assertEqual(len({item["diagnostics"]["identity"]["attempt_id"] for item in failures}), 3)
        called.clear()
        async def resume(index, attempt):
            called.append((index, attempt))
            return [index]
        result = await run_transcription_queue(7, resume, lambda: None, lambda value: None, path=self.path)
        self.assertEqual(len(result), 7)
        self.assertEqual(called[0], (0, 1))
        self.assertFalse(any(index in (1, 2) for index, _ in called))
        self.assertEqual(read_json(self.path)["progress"]["completed"], 7)

    async def test_fatal_error_is_not_retried_and_preserves_other_successes(self):
        async def operation(index, attempt):
            if index == 0:
                raise RuntimeError("HTTP 401")
            return [index]
        with self.assertRaises(PartialTranscriptionError) as error:
            await run_transcription_queue(9, operation, lambda: None, lambda value: None, path=self.path)
        self.assertEqual(sorted(error.exception.results), [1, 2])
        self.assertEqual(read_json(self.path)["attempts"]["0"], 1)

    async def test_content_block_stops_admission_and_drains_for_both_stages(self):
        for failure_type in (PartialTranscriptionError, PartialTranslationError):
            path = self.root / (failure_type.__name__ + '.json')
            calls = []
            async def operation(index, attempt):
                calls.append((index, attempt))
                if index == 0:
                    raise ContentBlockedError("PROHIBITED_CONTENT")
                await asyncio.sleep(.03)
                return [index]
            with self.assertRaises(failure_type) as error:
                await run_transcription_queue(9, operation, lambda: None, lambda value: None,
                    path=path, failure_type=failure_type)
            self.assertEqual(calls, [(0, 1), (1, 1), (2, 1)])
            self.assertEqual(sorted(error.exception.results), [1, 2])
            self.assertEqual(error.exception.content_blocks, [{"segment": 1, "reason": "PROHIBITED_CONTENT", "provider": "Gemini"}])
            saved = read_json(path)
            self.assertEqual(saved['content_blocks'], error.exception.content_blocks)
            blocked = saved['history'][0]['diagnostics']['events'][-1]
            self.assertFalse(blocked['retry_scheduled'])
            self.assertEqual(blocked['category'], 'remote_content_blocked')

    async def test_placeholder_policy_continues_and_resume_keeps_blocks_without_calls(self):
        calls = []
        async def operation(index, attempt):
            calls.append((index, attempt))
            if index == 0:
                raise ContentBlockedError('PROHIBITED_CONTENT')
            return [index]
        snapshots = []
        result = await run_transcription_queue(6, operation, lambda: None, snapshots.append,
            path=self.path, blocked_result=lambda index: ['blocked'])
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0], ['blocked'])
        self.assertEqual(len(calls), 6)
        self.assertEqual(snapshots[-1]['content_blocks'], [{'segment': 1, 'reason': 'PROHIBITED_CONTENT', 'provider': 'Gemini'}])
        self.assertEqual(snapshots[-1]['failed'], 0)
        calls.clear()
        await run_transcription_queue(6, operation, lambda: None, snapshots.append,
            path=self.path, blocked_result=lambda index: ['blocked'])
        self.assertEqual(calls, [])
        self.assertEqual(len(snapshots[-1]['content_blocks']), 1)

    async def test_cancel_cancels_inflight_without_leaking_tasks(self):
        started = asyncio.Event()
        stopped = []
        async def operation(index, attempt):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(index)
        def check():
            if started.is_set():
                raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            await run_transcription_queue(9, operation, check, lambda value: None)
        self.assertEqual(sorted(stopped), [0, 1, 2])

    async def test_cached_empty_speech_is_success_and_bad_cache_reprocessed(self):
        async def operation(index, attempt):
            return []
        await run_transcription_queue(2, operation, lambda: None, lambda value: None, path=self.path)
        never = Mock(side_effect=AssertionError("cached result must not request"))
        self.assertEqual(await run_transcription_queue(2, never, lambda: None, lambda value: None, path=self.path), {0: [], 1: []})

    async def test_429_delays_new_admission_for_all_slots(self):
        calls = []
        async def operation(index, attempt):
            calls.append((index, attempt, asyncio.get_running_loop().time()))
            if index == 0 and attempt == 1:
                raise RetryableTranscriptionError("429", delay=1, shared_cooldown=True)
            return []
        await run_transcription_queue(4, operation, lambda: None, lambda value: None)
        first = calls[0][2]
        next_start = next(t for index, attempt, t in calls if index == 3)
        self.assertGreaterEqual(next_start - first, 0.95)
