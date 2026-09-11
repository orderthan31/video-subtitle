import asyncio
from pathlib import Path
import shutil
import sys
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api"), str(ROOT / "workers/media")]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from app.services.result_response import ResultResponse
from media_worker.worker import Worker
from video_service.locking import download_lock, job_lock, JobBusyError
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.record = self.repo.create_job(original_filename="test.mp4", expected_size=10,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.job = self.record.job_id
        self.output = self.repo.job_dir(self.job) / "output/final.mp4"
        self.output.write_bytes(b"0123456789")
        self.repo.update_status(self.job, JobStatus.COMPLETED)
        self.worker = Worker(self.repo, None)

    def test_multiple_downloads_exclude_delete_and_gc(self):
        with download_lock(self.repo, self.job), download_lock(self.repo, self.job):
            with self.assertRaises(JobBusyError):
                with job_lock(self.repo, self.job, "execution"):
                    pass
            with patch.object(routes, "repository", self.repo):
                response = TestClient(app).delete(f"/api/jobs/{self.job}")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.headers["retry-after"], "1")
            with patch.dict("os.environ", {"RESULT_TTL_HOURS": "0"}):
                self.worker.collect()
            self.assertTrue(self.output.exists())
        with patch.dict("os.environ", {"RESULT_TTL_HOURS": "0"}):
            self.worker.collect()
        self.assertFalse(self.repo.job_dir(self.job).exists())

    def test_abandoned_lease_is_reclaimed(self):
        directory = self.root / ".locks"
        directory.mkdir(exist_ok=True)
        lease = directory / f"{self.job}.download.{uuid4().hex}.lock"
        lease.write_bytes(b"0")
        with job_lock(self.repo, self.job, "execution"):
            self.assertFalse(lease.exists())

    def test_process_crash_releases_download_lease(self):
        code = """
import os
import sys
sys.path.insert(0, sys.argv[1])
from video_service.locking import download_lock
from video_service.repository import FilesystemJobRepository
with download_lock(FilesystemJobRepository(sys.argv[2]), sys.argv[3]):
    os._exit(0)
"""
        child = subprocess.run([sys.executable, "-c", code, str(ROOT / "packages/shared"),
            str(self.root), self.job], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors="replace"))
        self.assertEqual(len(list((self.root / ".locks").glob("*.download.*.lock"))), 1)
        with job_lock(self.repo, self.job, "execution"):
            self.assertEqual(list((self.root / ".locks").glob("*.download.*.lock")), [])

    def test_active_execution_excludes_new_download(self):
        with job_lock(self.repo, self.job, "execution"), self.assertRaises(JobBusyError):
            with download_lock(self.repo, self.job):
                pass

    def test_range_response_and_missing_results(self):
        url = f"/api/jobs/{self.job}/results/"
        with patch.object(routes, "repository", self.repo):
            client = TestClient(app)
            response = client.get(url + "final.mp4", headers={"Range": "bytes=2-5"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"2345")
            self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
            self.assertEqual(client.get(url + "final.mp4", headers={"Range": "bytes=30-40"}).status_code, 416)
            self.assertEqual(client.get(url + "translated.srt").status_code, 404)
            self.assertEqual(client.get(url + "original.srt").status_code, 404)
            original = "1\n00:00:01,000 --> 00:00:02,000\nHello.\n"
            (self.output.parent / "original.srt").write_text(original, encoding="utf-8", newline="\n")
            response = client.get(url + "original.srt")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content.decode("utf-8"), original)
            self.assertIn("original.srt", response.headers["content-disposition"])
            self.assertEqual(client.get(url + "other.txt").status_code, 404)
            self.repo.update_status(self.job, JobStatus.QUEUED)
            self.assertEqual(client.get(url + "final.mp4").status_code, 409)
        with job_lock(self.repo, self.job, "execution"):
            pass

    async def test_transfer_holds_lease_until_final_body_and_disables_path_handoff(self):
        response = ResultResponse(self.repo, self.job, "final.mp4")
        response.chunk_size = 3
        messages = []
        async def send(message):
            messages.append(message)
            with self.assertRaises(JobBusyError):
                with job_lock(self.repo, self.job, "execution"):
                    pass
            with patch.dict("os.environ", {"RESULT_TTL_HOURS": "0"}):
                self.worker.collect()
            self.assertTrue(self.output.exists())
        async def receive():
            return {"type": "http.disconnect"}
        await response({"type": "http", "method": "GET", "headers": [],
            "extensions": {"http.response.pathsend": {}}}, receive, send)
        self.assertNotIn("http.response.pathsend", [m["type"] for m in messages])
        self.assertEqual(b"".join(m.get("body", b"") for m in messages), b"0123456789")
        with job_lock(self.repo, self.job, "execution"):
            pass

    async def test_disconnected_transfer_releases_lease(self):
        response = ResultResponse(self.repo, self.job, "final.mp4")
        async def send(message):
            if message["type"] == "http.response.body":
                raise asyncio.CancelledError()
        async def receive():
            return {"type": "http.disconnect"}
        with self.assertRaises(asyncio.CancelledError):
            await response({"type": "http", "method": "GET", "headers": []}, receive, send)
        with job_lock(self.repo, self.job, "execution"):
            pass
