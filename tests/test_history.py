from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api"), str(ROOT / "workers/media")]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from media_worker.worker import Worker
from video_service.locking import download_lock
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        job = self.repo.create_job(original_filename="history.mp4", expected_size=3,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.job_id = job.job_id
        self.output = self.repo.job_dir(job.job_id) / "output/final.mp4"
        self.output.write_bytes(b"mp4")
        self.repo.source_path(job).write_bytes(b"src")
        self.repo.update_status(job.job_id, JobStatus.COMPLETED, metadata={"result_files": ["final.mp4"]})
        self.worker = Worker(self.repo, None)
        for p in (patch.dict("os.environ", {"RESULT_TTL_HOURS": "24", "HISTORY_TTL_DAYS": "90", "UPLOAD_TTL_HOURS": "6"}),
                  patch.object(routes, "repository", self.repo)):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app)

    def age(self, days):
        record = self.repo.read(self.job_id)
        record.completed_at = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.repo.save(record)
        return record.completed_at

    def test_list_order_stays_fixed_during_upload_and_transcription_updates(self):
        transcribing = self.repo.read(self.job_id)
        transcribing.created_at = "2026-09-11T00:00:00+00:00"
        self.repo.save(transcribing)
        self.repo.update_status(self.job_id, JobStatus.TRANSCRIBING)
        uploading = self.repo.create_job(original_filename="upload.mp4", expected_size=3,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        uploading.created_at = "2026-09-11T00:01:00+00:00"
        self.repo.save(uploading)
        expected = [uploading.job_id, self.job_id]
        for progress in (1, 2, 3):
            self.repo.update_upload_progress(uploading.job_id, progress)
            self.repo.heartbeat(self.job_id)
            self.assertEqual([job["job_id"] for job in self.client.get("/api/jobs").json()["jobs"]], expected)
        self.repo.update_status(self.job_id, JobStatus.COMPLETED)
        with patch.object(routes, "repository", FilesystemJobRepository(self.root)):
            self.assertEqual([job["job_id"] for job in self.client.get("/api/jobs").json()["jobs"]], expected)

    def test_equal_creation_times_have_stable_id_tiebreaker(self):
        other = self.repo.create_job(original_filename="other.mp4", expected_size=3,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        other.created_at = self.repo.read(self.job_id).created_at
        self.repo.save(other)
        expected = sorted([self.job_id, other.job_id], reverse=True)
        for job_id in (self.job_id, other.job_id):
            self.repo.heartbeat(job_id)
            self.assertEqual([job["job_id"] for job in self.client.get("/api/jobs").json()["jobs"]], expected)

    def test_expired_media_keeps_readable_history_and_returns_gone(self):
        completed_at = self.age(2)
        self.worker.collect()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.repo.job_dir(self.job_id) / "input").exists())
        job = self.client.get(f"/api/jobs/{self.job_id}").json()
        self.assertEqual(job["status"], "COMPLETED")
        self.assertEqual(job["completed_at"], completed_at)
        self.assertEqual(job["original_filename"], "history.mp4")
        self.assertTrue(job["metadata"]["results_expired_at"])
        self.assertEqual(len(self.client.get("/api/jobs").json()["jobs"]), 1)
        self.assertEqual(self.client.get(f"/api/jobs/{self.job_id}/results/final.mp4").status_code, 410)
        self.assertEqual(FilesystemJobRepository(self.root).read(self.job_id).options.target_language, "ko")

    def test_repeated_collection_does_not_extend_history_or_restore_expired_files(self):
        self.age(2)
        self.worker.collect()
        first = self.repo.read(self.job_id).to_dict()
        with patch.dict("os.environ", {"RESULT_TTL_HOURS": "72"}):
            self.worker.collect()
        self.assertEqual(self.repo.read(self.job_id).to_dict(), first)
        self.age(91)
        self.worker.collect()
        self.assertFalse(self.repo.job_dir(self.job_id).exists())

    def test_history_expiry_and_user_delete_remove_metadata(self):
        self.age(2)
        self.worker.collect()
        self.assertEqual(self.client.delete(f"/api/jobs/{self.job_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/jobs/{self.job_id}").status_code, 404)
        self.assertEqual(self.repo.list(), [])

    def test_manual_delete_is_allowed_with_development_marker(self):
        (self.root / ".preserve-artifacts").touch()
        self.assertEqual(self.client.delete(f"/api/jobs/{self.job_id}").status_code, 204)
        self.assertFalse(self.repo.job_dir(self.job_id).exists())

    def test_worker_poll_preserves_expired_artifacts_until_explicit_collection(self):
        (self.root / ".preserve-artifacts").touch()
        self.age(2)
        self.worker.tick()
        self.assertTrue(self.output.exists())
        self.assertTrue(self.repo.source_path(self.repo.read(self.job_id)).exists())
        self.worker.collect()
        self.assertFalse(self.output.exists())

    def test_recent_terminal_jobs_keep_all_artifacts_without_marker(self):
        work = self.repo.job_dir(self.job_id) / "work"
        (work / "audio.wav").write_bytes(b"audio")
        (work / "original.srt").write_text("subtitle")
        for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            self.repo.update_status(self.job_id, status)
            self.worker.collect()
            self.assertTrue(self.output.exists())
            self.assertTrue(self.repo.source_path(self.repo.read(self.job_id)).exists())
            self.assertEqual((work / "audio.wav").read_bytes(), b"audio")
            self.assertEqual((work / "original.srt").read_text(), "subtitle")

    def test_download_protects_history_expiry(self):
        self.age(91)
        with download_lock(self.repo, self.job_id):
            self.worker.collect()
            self.assertTrue(self.output.exists())
        self.worker.collect()
        self.assertFalse(self.repo.job_dir(self.job_id).exists())

    def test_short_history_setting_never_shortens_result_ttl(self):
        self.age(0.5)
        with patch.dict("os.environ", {"HISTORY_TTL_DAYS": "0"}):
            self.worker.collect()
        self.assertTrue(self.output.exists())

    def test_cleanup_failure_retries_without_marking_results_expired(self):
        self.age(2)
        with patch.object(self.worker, "cleanup", side_effect=PermissionError("busy")), self.assertLogs(level="ERROR"):
            self.worker.collect()
        self.assertNotIn("results_expired_at", self.repo.read(self.job_id).metadata)
        self.assertTrue(self.output.exists())
        self.worker.collect()
        self.assertFalse(self.output.exists())
        self.assertTrue(self.repo.read(self.job_id).metadata["results_expired_at"])

    def test_failed_cancelled_and_expired_upload_history(self):
        for status in (JobStatus.FAILED, JobStatus.CANCELLED):
            self.repo.update_status(self.job_id, status, error="failure" if status == JobStatus.FAILED else None)
            self.age(2)
            self.worker.collect()
            record = self.repo.read(self.job_id)
            self.assertEqual(record.status, status)
            self.assertEqual(record.error, "failure" if status == JobStatus.FAILED else None)
        record = self.repo.read(self.job_id)
        record.status = JobStatus.UPLOADING
        record.completed_at = None
        self.repo.save(record)
        with patch.dict("os.environ", {"UPLOAD_TTL_HOURS": "0"}):
            self.worker.collect()
        record = self.repo.read(self.job_id)
        self.assertEqual(record.status, JobStatus.CANCELLED)
        self.assertTrue(record.metadata["upload_expired_at"])
        self.assertTrue(record.completed_at)

    def test_invalid_retention_rejected_before_deletion(self):
        self.age(91)
        for value in ("-1", "nan", "inf"):
            with patch.dict("os.environ", {"HISTORY_TTL_DAYS": value}), self.assertRaises(ValueError):
                self.worker.collect()
            self.assertTrue(self.output.exists())
