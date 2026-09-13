import asyncio
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.worker_storage import CapacityEstimate, capacity_estimate, check_capacity, disk_call
from media_worker.transcription_queue import run_segment_queue, PartialTranscriptionError
from video_service.capacity import StorageLimitError


class StorageTests(unittest.IsolatedAsyncioTestCase):
    def test_writes_are_charged_without_repeated_scans(self):
        with patch("media_worker.worker_storage.used_bytes", return_value=100) as scan, \
                patch("media_worker.worker_storage.assert_free_space") as free:
            estimate = CapacityEstimate(ROOT)
            estimate.check(150, 0, 20)
            estimate.check(150, 0, 20)
            with self.assertRaises(StorageLimitError):
                estimate.check(150, 0, 20)
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(free.call_count, 2)

    def test_refresh_accounts_for_external_writes(self):
        with patch("media_worker.worker_storage.used_bytes", side_effect=[100, 190]) as scan, \
                patch("media_worker.worker_storage.assert_free_space"), \
                patch("media_worker.worker_storage.time.monotonic", side_effect=[0, 0, 61, 61]):
            estimate = CapacityEstimate(ROOT)
            estimate.check(200, 0, 20)
            with self.assertRaises(StorageLimitError):
                estimate.check(200, 0, 20)
            self.assertEqual(scan.call_count, 2)

    async def test_context_is_shared_across_io_threads(self):
        with patch("media_worker.worker_storage.used_bytes", return_value=100) as scan, \
                patch("media_worker.worker_storage.assert_free_space"), capacity_estimate(ROOT):
            await asyncio.gather(*(disk_call(check_capacity, ROOT, 1000, 0, 10) for _ in range(3)))
            self.assertEqual(scan.call_count, 1)

    async def test_cancel_drains_disk_thread_before_return(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        def work():
            started.set()
            release.wait(2)
            finished.set()
        task = asyncio.create_task(disk_call(work))
        while not started.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())

    async def test_slow_progress_does_not_block_requests_or_expire_them(self):
        async def operation(index, attempt):
            await asyncio.sleep(.01)
            return [index]
        result = await run_segment_queue(3, operation, lambda: None,
            lambda _: time.sleep(.08), operation_timeout=.04)
        self.assertEqual(len(result), 3)

    async def test_storage_error_is_terminal_not_a_paid_retry(self):
        calls = []
        async def operation(index, attempt):
            calls.append(attempt)
            raise StorageLimitError("full")
        with self.assertRaises(PartialTranscriptionError):
            await run_segment_queue(1, operation, lambda: None, lambda _: None)
        self.assertEqual(calls, [1])
