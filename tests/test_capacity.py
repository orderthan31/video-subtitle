from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api")]
from app.services.storage_guard import StorageGuard
from video_service.capacity import StorageLimitError, assert_capacity
from video_service.repository import FilesystemJobRepository
from video_service.models import QualityProfile, JobStatus


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.guard = StorageGuard(root=self.root, max_upload_bytes=10000, quota_bytes=100000,
            min_free_space_bytes=0)

    def test_pending_upload_reserves_working_space(self):
        record = self.repo.create_job(original_filename="clip.mp4", expected_size=10000,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.guard.quota_bytes = 70000
        with self.assertRaises(StorageLimitError):
            self.guard.assert_can_accept_upload(10000)
        self.repo.update_status(record.job_id, JobStatus.CANCELLED)
        self.guard.assert_can_accept_upload(10000)

    def test_actual_partial_upload_not_counted_twice(self):
        record = self.repo.create_job(original_filename="clip.mp4", expected_size=10000,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.repo.source_path(record).write_bytes(b'x' * 5000)
        self.guard.quota_bytes = 81000
        self.guard.assert_can_accept_upload(10000)

    def test_disk_watermark_prevents_admission(self):
        with patch('shutil.disk_usage', return_value=shutil._ntuple_diskusage(100000, 70000, 30000)):
            with self.assertRaises(StorageLimitError):
                self.guard.assert_can_accept_upload(10000)

    def test_running_job_checks_actual_quota(self):
        (self.root / "temporary.bin").write_bytes(b'x' * 100)
        with self.assertRaises(StorageLimitError):
            assert_capacity(self.root, 99, 0)


if __name__ == '__main__':
    unittest.main()
