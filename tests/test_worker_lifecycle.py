from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.worker import Worker
from video_service.locking import job_lock
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.record = self.repo.create_job(original_filename="clip.mp4", expected_size=1,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.job = self.record.job_id
        self.worker = Worker(self.repo, None)
        self.repo.source_path(self.record).write_bytes(b"x")

    def test_failed_process_cleans_all_media(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        with patch("media_worker.worker.select_encoder", side_effect=RuntimeError("encoder unavailable")):
            self.worker.process(self.job)
        self.assertEqual(self.repo.read(self.job).status, JobStatus.FAILED)
        self.assertIn("encoder unavailable", self.repo.read(self.job).error)
        self.assertEqual({p.name for p in self.repo.job_dir(self.job).iterdir()}, {"job.json"})

    def test_cancel_during_pipeline_stops_before_audio(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        def cancel(work, check):
            with job_lock(self.repo, self.job):
                record = self.repo.read(self.job)
                record.metadata["cancel_requested"] = True
                self.repo.save(record)
            check()
        with patch("media_worker.worker.select_encoder", side_effect=cancel), patch("media_worker.worker.extract_audio") as extraction:
            self.worker.process(self.job)
        extraction.assert_not_called()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.CANCELLED)
        self.assertFalse(self.repo.source_path(self.record).exists())

    def test_collection_skips_live_execution(self):
        self.repo.update_status(self.job, JobStatus.ENCODING)
        with job_lock(self.repo, self.job, "execution"):
            self.worker.collect()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.ENCODING)
        self.assertTrue(self.repo.source_path(self.record).exists())
        self.worker.collect()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.FAILED)

    def test_completed_results_remain_until_expiry(self):
        output = self.repo.job_dir(self.job) / "output/final.mp4"
        output.write_bytes(b"video")
        self.repo.update_status(self.job, JobStatus.COMPLETED)
        self.worker.collect()
        self.assertTrue(output.exists())
        self.assertFalse(self.repo.source_path(self.record).exists())
        record = self.repo.read(self.job)
        record.completed_at = "2000-01-01T00:00:00+00:00"
        self.repo.save(record)
        self.worker.collect()
        self.assertFalse(self.repo.job_dir(self.job).exists())


if __name__ == "__main__":
    unittest.main()
