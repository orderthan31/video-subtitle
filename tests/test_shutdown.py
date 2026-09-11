from concurrent.futures import ThreadPoolExecutor
import asyncio
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from threading import Event
import unittest
from unittest.mock import patch, AsyncMock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.process import run_process
from media_worker.shutdown import shutdown_signals, WorkerStopping
from media_worker.providers import GeminiProvider
from media_worker.worker import Worker
from video_service.locking import job_lock
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.worker = Worker(self.repo, None)

    def create_job(self):
        record = self.repo.create_job(original_filename="test.mp4", expected_size=1,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.repo.source_path(record).write_bytes(b"x")
        self.repo.update_status(record.job_id, JobStatus.QUEUED)
        return record

    def test_termination_signal_cleans_current_job_and_preserves_queue(self):
        current = self.create_job()
        queued = self.create_job()
        before = signal.getsignal(signal.SIGTERM)
        def stop_during_probe(work, check, **kwargs):
            signal.raise_signal(signal.SIGTERM)
            check()
        with shutdown_signals(self.worker.stop), \
                patch("media_worker.worker.select_encoder", side_effect=stop_during_probe) as encoder, \
                patch.object(self.repo, "find_by_statuses", return_value=[queued, current]):
            self.worker.tick()
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        encoder.assert_called_once()
        record = self.repo.read(current.job_id)
        self.assertEqual(record.status, JobStatus.FAILED)
        self.assertTrue(record.metadata["interrupted"])
        self.assertIn("shutting down", record.error)
        self.assertFalse(self.repo.source_path(current).exists())
        self.assertEqual(self.repo.read(queued.job_id).status, JobStatus.QUEUED)
        self.assertTrue(self.repo.source_path(queued).exists())
        with job_lock(self.repo, current.job_id, "execution"):
            pass

    def test_stop_before_claim_leaves_input_untouched(self):
        record = self.create_job()
        self.worker.stop.set()
        self.worker.process(record.job_id)
        self.assertEqual(self.repo.read(record.job_id).status, JobStatus.QUEUED)
        self.assertTrue(self.repo.source_path(record).exists())

    def test_idle_poll_wait_wakes_immediately(self):
        polled = Event()
        with patch.object(self.worker, "tick", side_effect=polled.set), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.worker.run, 600)
            try:
                self.assertTrue(polled.wait(1))
            finally:
                self.worker.stop.set()
            future.result(timeout=2)

    def test_running_child_is_terminated_before_cleanup(self):
        record = self.create_job()
        children = []
        popen = subprocess.Popen
        def capture(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            self.worker.stop.set()
            return child
        def encoder(work, check, **kwargs):
            run_process([sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=work, log_name="child.log", check=check)
        with patch("media_worker.worker.select_encoder", side_effect=encoder), \
                patch("media_worker.process.subprocess.Popen", side_effect=capture):
            self.worker.process(record.job_id)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertFalse((self.repo.job_dir(record.job_id) / "work").exists())
        self.assertTrue(self.repo.read(record.job_id).metadata["interrupted"])

    def test_signal_handlers_restore_after_error(self):
        before = signal.getsignal(signal.SIGINT)
        with self.assertRaises(RuntimeError):
            with shutdown_signals(Event()):
                raise RuntimeError("test")
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_pending_gemini_request_is_cancelled_and_awaited(self):
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.key = "test-only"
        ended = []
        async def pending(*args, **kwargs):
            self.worker.stop.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.append(True)
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = pending
        with patch("media_worker.providers.httpx.AsyncClient", return_value=client), \
                self.assertRaises(WorkerStopping):
            asyncio.run(provider._request([], {}, self.worker.check_stopping, "test-model"))
        self.assertEqual(ended, [True])
