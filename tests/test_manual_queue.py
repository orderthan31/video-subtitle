from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from urllib.parse import unquote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api"), str(ROOT / "workers/media")]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from app.services.upload_service import UploadService
from app.services.result_response import ResultResponse
from media_worker.worker import Worker
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.locking import job_lock
from video_service.capacity import StorageLimitError


class ManualQueueTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        for patcher in (patch.object(routes, "repository", self.repo),
                        patch.object(routes, "upload_service", UploadService(self.repo))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def upload(self):
        record = self.repo.create_job(original_filename="movie.mov", expected_size=3,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.repo.source_path(record).write_bytes(b"abc")
        response = self.client.post(f"/api/uploads/{record.job_id}/complete")
        self.assertEqual(response.json(), {"status": "ready"})
        return record.job_id

    def test_upload_waits_for_explicit_start_and_survives_collection(self):
        job = self.upload()
        worker = Worker(self.repo, None)
        with patch.object(worker, "process") as process:
            worker.tick()
        process.assert_not_called()
        self.assertEqual(self.repo.read(job).status, JobStatus.READY)
        self.assertTrue(self.repo.source_path(self.repo.read(job)).exists())
        self.assertEqual(self.client.post(f"/api/jobs/{job}/start").status_code, 200)
        first = self.repo.read(job).metadata["queued_at"]
        self.assertEqual(self.client.post(f"/api/jobs/{job}/start").status_code, 200)
        self.assertEqual(self.repo.read(job).metadata["queued_at"], first)

    def test_start_order_not_upload_or_updated_order(self):
        a, b = self.upload(), self.upload()
        self.client.post(f"/api/jobs/{b}/start")
        self.client.post(f"/api/jobs/{a}/start")
        self.repo.heartbeat(b)
        worker = Worker(FilesystemJobRepository(self.root), None)
        with patch.object(worker, "process") as process:
            worker.tick()
        self.assertEqual([call.args[0] for call in process.call_args_list], [b, a])

    def test_second_worker_cannot_process_while_first_has_slot(self):
        job = self.upload()
        self.client.post(f"/api/jobs/{job}/start")
        worker = Worker(self.repo, None)
        with job_lock(self.repo, "f" * 32, "execution"), patch.object(worker, "process") as process:
            worker.tick()
        process.assert_not_called()
        self.assertEqual(self.repo.read(job).status, JobStatus.QUEUED)

    def test_invalid_start_missing_source_and_capacity_leave_state_unchanged(self):
        job = self.upload()
        with patch.object(routes, "assert_capacity", side_effect=StorageLimitError("full")):
            self.assertEqual(self.client.post(f"/api/jobs/{job}/start").status_code, 507)
        self.assertEqual(self.repo.read(job).status, JobStatus.READY)
        self.repo.source_path(self.repo.read(job)).write_bytes(b"a")
        self.assertEqual(self.client.post(f"/api/jobs/{job}/start").status_code, 409)
        self.client.post(f"/api/jobs/{job}/cancel")
        self.assertEqual(self.repo.read(job).status, JobStatus.CANCELLED)
        self.assertEqual(self.client.post(f"/api/jobs/{job}/start").status_code, 409)

    def test_download_filename_preserves_unicode_and_output_extension(self):
        job = self.upload()
        record = self.repo.read(job)
        for original, expected in [("movie.mov", "subtitle_movie.mp4"),
                ("여행 영상.mp4", "subtitle_여행 영상.mp4"),
                ("C:\\folder\\movie.mkv", "subtitle_movie.mp4"),
                ("bad\r\nname.mp4", "subtitle_bad__name.mp4")]:
            record.original_filename = original
            self.repo.save(record)
            response = ResultResponse(self.repo, job, "final.mp4")
            self.assertIn(expected, unquote(response.headers["content-disposition"]))
            self.assertEqual(response.path.name, "final.mp4")
