from pathlib import Path
import sys
import shutil
from uuid import uuid4
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api")]

from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository, JobNotFoundError
from video_service.locking import job_lock, JobBusyError
from video_service.storage import remove_path_inside
from video_service.timeline import build_timeline_from_kept_intervals, map_segment_to_original
from video_service.transcript import TranscriptSegment, filter_transcript_segments
from video_service.subtitles import segments_to_srt
from app.services.upload_service import UploadService, UploadConflictError


async def chunks(*items):
    for item in items:
        yield item


class FoundationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_path = ROOT / "data" / "test-runs" / uuid4().hex
        self.temp_path.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.temp_path)
        self.repo = FilesystemJobRepository(self.temp_path)
        self.job = self.repo.create_job(original_filename="movie.mp4", expected_size=6,
            source_language="auto", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.upload = UploadService(self.repo)

    async def test_resume_and_queue(self):
        await self.upload.append_chunk(job_id=self.job.job_id, expected_offset=0, body=chunks(b"abc"))
        with self.assertRaises(UploadConflictError):
            await self.upload.append_chunk(job_id=self.job.job_id, expected_offset=0, body=chunks(b"def"))
        await self.upload.append_chunk(job_id=self.job.job_id, expected_offset=3, body=chunks(b"def"))
        self.upload.complete(self.job.job_id)
        self.assertEqual(self.repo.read(self.job.job_id).status, JobStatus.QUEUED)
        self.assertEqual(self.repo.source_path(self.job).read_bytes(), b"abcdef")
        self.assertEqual(self.repo.read(self.job.job_id).options.target_language, "ko")

    async def test_disconnect_rolls_back_request(self):
        async def interrupted():
            yield b"abc"
            raise ConnectionError("disconnect")
        with self.assertRaises(ConnectionError):
            await self.upload.append_chunk(job_id=self.job.job_id, expected_offset=0, body=interrupted())
        self.assertEqual(self.repo.source_path(self.job).stat().st_size, 0)

    async def test_oversize_rolls_back(self):
        with self.assertRaises(ValueError):
            await self.upload.append_chunk(job_id=self.job.job_id, expected_offset=0, body=chunks(b"abc", b"1234"))
        self.assertEqual(self.repo.source_path(self.job).stat().st_size, 0)

    async def test_concurrent_chunk_rejected(self):
        with job_lock(self.repo, self.job.job_id):
            with self.assertRaises(JobBusyError):
                await self.upload.append_chunk(job_id=self.job.job_id, expected_offset=0, body=chunks(b"abc"))

    def test_incomplete_upload_not_queued(self):
        with self.assertRaises(UploadConflictError):
            self.upload.complete(self.job.job_id)
        self.assertEqual(self.repo.read(self.job.job_id).status, JobStatus.UPLOADING)

    def test_storage_root_and_path_traversal_rejected(self):
        for value in ("", ".", "..", "../other", "/", "a/b"):
            with self.subTest(value=value), self.assertRaises(JobNotFoundError):
                self.repo.delete_job_dir(value)
        with self.assertRaises(ValueError):
            remove_path_inside(self.temp_path, self.temp_path)
        self.assertTrue(self.repo.job_file(self.job.job_id).exists())

    def test_splice_boundary_mapping(self):
        spans = build_timeline_from_kept_intervals([(0, 5), (20, 25)])
        self.assertEqual(map_segment_to_original(5, 7, spans), (20, 22))
        self.assertEqual(map_segment_to_original(3, 5, spans), (3, 5))

    def test_conservative_filter(self):
        segments = [TranscriptSegment(0, 1, text) for text in ("[breathing]", "어", "숨소리", "Hello")]
        self.assertEqual([s.text for s in filter_transcript_segments(segments)], ["어", "숨소리", "Hello"])

    def test_srt_keeps_short_cues_and_contiguous_numbers(self):
        result = segments_to_srt([TranscriptSegment(0, 1, ""), TranscriptSegment(1, 1.2, "Hello")])
        self.assertEqual(result, "1\n00:00:01,000 --> 00:00:01,200\nHello\n")

    def test_api_upload_cancel_delete(self):
        from fastapi.testclient import TestClient
        from app.api import routes
        from app.main import app
        with patch.object(routes, "repository", self.repo), patch.object(routes, "upload_service", self.upload):
            client = TestClient(app)
            job_id = self.job.job_id
            self.assertEqual(client.put(f"/api/uploads/{job_id}/chunks?offset=0", content=b"abc").status_code, 200)
            self.assertEqual(client.get(f"/api/uploads/{job_id}").json()["uploaded_bytes"], 3)
            self.assertEqual(client.delete(f"/api/jobs/{job_id}").status_code, 409)
            self.assertEqual(client.post(f"/api/jobs/{job_id}/cancel").status_code, 200)
            self.assertEqual(client.delete(f"/api/jobs/{job_id}").status_code, 204)
            self.assertEqual(client.get(f"/api/jobs/{job_id}").status_code, 404)
            self.assertEqual(client.post("/api/jobs/invalid/cancel").status_code, 404)


if __name__ == "__main__":
    unittest.main()
