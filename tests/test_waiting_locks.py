from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sys
from threading import Event
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/shared"))
from video_service.locking import JobBusyError, job_lock, wait_for_job_lock
from video_service.repository import FilesystemJobRepository


class WaitingLockTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.job = uuid4().hex

    def test_waiter_acquires_after_owner_releases(self):
        checking = Event()
        def acquire():
            with wait_for_job_lock(self.repo, self.job, timeout=2, check=checking.set):
                return "acquired"
        with ThreadPoolExecutor(max_workers=1) as pool:
            with job_lock(self.repo, self.job):
                future = pool.submit(acquire)
                self.assertTrue(checking.wait(1))
                self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=3), "acquired")

    def test_timeout_does_not_release_owner_lock(self):
        with job_lock(self.repo, self.job):
            with self.assertRaises(TimeoutError):
                with wait_for_job_lock(self.repo, self.job, timeout=0.02):
                    self.fail("Lock is occupied")
            with self.assertRaises(JobBusyError):
                with job_lock(self.repo, self.job):
                    pass

    def test_waiter_can_cancel(self):
        attempts = 0
        def cancel():
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise InterruptedError("cancelled")
        with job_lock(self.repo, self.job):
            with self.assertRaises(InterruptedError):
                with wait_for_job_lock(self.repo, self.job, check=cancel):
                    self.fail("Lock is occupied")

    def test_body_busy_error_is_not_retried_and_lock_is_released(self):
        calls = 0
        with self.assertRaises(JobBusyError):
            with wait_for_job_lock(self.repo, self.job):
                calls += 1
                raise JobBusyError("body")
        self.assertEqual(calls, 1)
        with job_lock(self.repo, self.job):
            pass

    def test_invalid_timeout_rejected(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                with wait_for_job_lock(self.repo, self.job, timeout=timeout):
                    pass
