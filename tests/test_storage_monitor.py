from pathlib import Path
from types import SimpleNamespace
import shutil
import sys
import unittest
import anyio
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api')]
from app.services.storage_monitor import storage_snapshot, monitor_storage
from video_service.capacity import used_bytes
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class StorageMonitorTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        self.settings = SimpleNamespace(storage_root=self.root, service_quota_bytes=100000,
            min_free_space_bytes=100)

    def job(self):
        return self.repo.create_job(original_filename='clip.mp4', expected_size=10000,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED,
            metadata={'reserved_bytes': 40000})

    def test_partial_upload_consumes_reservation_and_audit_is_read_only(self):
        job = self.job()
        self.repo.source_path(job).write_bytes(bytes(5000))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = storage_snapshot(self.settings)
        self.assertEqual(result['used_bytes'], used_bytes(self.root))
        self.assertEqual(result['used_bytes'] + result['remaining_reserved_bytes'], 40000)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_terminal_jobs_keep_actual_files_but_release_unused_reservations(self):
        for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            job = self.job()
            self.repo.source_path(job).write_bytes(bytes(5000))
            self.repo.update_status(job.job_id, status)
        result = storage_snapshot(self.settings)
        self.assertEqual(result['remaining_reserved_bytes'], 0)
        self.assertGreaterEqual(result['used_bytes'], 15000)
        self.assertEqual(len(self.repo.list()), 3)

    def test_detects_external_growth_and_low_space(self):
        (self.root / 'external.bin').write_bytes(bytes(100001))
        with patch('shutil.disk_usage', return_value=shutil._ntuple_diskusage(200000, 199950, 50)):
            result = storage_snapshot(self.settings)
        self.assertTrue(result['over_quota'])
        self.assertTrue(result['low_space'])


class StorageMonitorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_audit_recovers_and_stops_cleanly(self):
        app = SimpleNamespace(state=SimpleNamespace())
        settings = SimpleNamespace(storage_audit_interval_seconds=0.01)
        snapshot = {'over_quota': False, 'low_space': False}
        with patch('app.services.storage_monitor.storage_snapshot', side_effect=[OSError('temporary'), snapshot, snapshot]) as scan:
            with patch('app.services.storage_monitor.logger') as log:
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(monitor_storage, app, settings)
                    with anyio.fail_after(2):
                        while not hasattr(app.state, 'storage_snapshot'):
                            await anyio.sleep(0.005)
                    tasks.cancel_scope.cancel()
                self.assertGreaterEqual(scan.call_count, 2)
                log.exception.assert_called_once()
                self.assertEqual(app.state.storage_snapshot, snapshot)


if __name__ == '__main__':
    unittest.main()
