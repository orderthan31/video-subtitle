from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api")]
from app.services.storage_guard import StorageGuard
from video_service.capacity import StorageLimitError, assert_capacity, reserve_workspace, workspace_reservation, remaining_reservations
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

    def make_job(self):
        return self.repo.create_job(original_filename="long.mp4", expected_size=1000,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)

    def test_hour_of_pcm_reserves_both_audio_copies(self):
        self.assertEqual(workspace_reservation(1000, 3600), 4000 + 3600 * 16000 * 2 * 2 + 1024**2)
        for duration in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                workspace_reservation(1000, duration)

    def test_failed_expansion_does_not_publish_reservation(self):
        record = self.make_job()
        with self.assertRaises(StorageLimitError):
            reserve_workspace(self.repo, record.job_id, 3600, 1024**2, 0)
        self.assertNotIn("reserved_bytes", self.repo.read(record.job_id).metadata)

    def test_api_admission_uses_worker_expansion_until_terminal(self):
        record = self.make_job()
        reserved = reserve_workspace(self.repo, record.job_id, 60, 100 * 1024**2, 0)
        self.guard.quota_bytes = reserved + 10000
        with self.assertRaises(StorageLimitError):
            self.guard.assert_can_accept_upload(10000)
        self.repo.update_status(record.job_id, JobStatus.FAILED)
        self.guard.assert_can_accept_upload(10000)

    def test_written_audio_consumes_reservation_without_double_counting(self):
        record = self.make_job()
        reserve_workspace(self.repo, record.job_id, 60, 100 * 1024**2, 0)
        before = remaining_reservations(self.repo)
        (self.repo.job_dir(record.job_id) / "work/audio.wav").write_bytes(bytes(2000))
        self.assertEqual(remaining_reservations(self.repo), before - 2000)

    def test_second_analysis_accounts_for_first_reservation(self):
        first, second = self.make_job(), self.make_job()
        quota = workspace_reservation(1000, 60) + 10000
        reserve_workspace(self.repo, first.job_id, 60, quota, 0)
        with self.assertRaises(StorageLimitError):
            reserve_workspace(self.repo, second.job_id, 60, quota, 0)
        self.assertNotIn("reserved_bytes", self.repo.read(second.job_id).metadata)

    def test_expansion_respects_disk_watermark(self):
        record = self.make_job()
        with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(10**9, 0, 2 * 1024**2)):
            with self.assertRaisesRegex(StorageLimitError, "free disk"):
                reserve_workspace(self.repo, record.job_id, 60, 10**9, 1024**2)
        self.assertNotIn("reserved_bytes", self.repo.read(record.job_id).metadata)


if __name__ == '__main__':
    unittest.main()
