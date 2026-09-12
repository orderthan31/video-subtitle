from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4
import asyncio
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api")]
from app.services.upload_service import UploadService
from video_service.capacity import StorageLimitError
from video_service.models import QualityProfile
from video_service.repository import FilesystemJobRepository


async def body():
    yield b"first"
    yield b"second"


class UploadCapacityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.job = self.repo.create_job(original_filename="clip.mp4", expected_size=20,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)

    async def test_full_disk_mid_request_preserves_previous_request(self):
        path = self.repo.source_path(self.job)
        path.write_bytes(b"previous")
        self.repo.update_upload_progress(self.job.job_id, 8)
        seen = []
        def check(size):
            seen.append((path.stat().st_size, size))
            if len(seen) == 2:
                raise StorageLimitError("full")
        service = UploadService(self.repo, check)
        with patch('app.services.upload_service.CAPACITY_BATCH_BYTES', 6):
            with self.assertRaises(StorageLimitError):
                await service.append_chunk(job_id=self.job.job_id, expected_offset=8, body=body())
        self.assertEqual(path.read_bytes(), b"previous")
        self.assertEqual(self.repo.read(self.job.job_id).uploaded_bytes, 8)
        self.assertEqual(seen, [(8, 5), (13, 6)])

    async def test_small_packets_share_scan_without_blocking_event_loop(self):
        seen = []
        ticks = []
        def check(size):
            seen.append(size)
            time.sleep(0.1)
        async def heartbeat():
            await asyncio.sleep(0.02)
            ticks.append(True)
        async def packets():
            for _ in range(20):
                yield b'x'
        pulse = asyncio.create_task(heartbeat())
        uploaded = await UploadService(self.repo, check).append_chunk(
            job_id=self.job.job_id, expected_offset=0, body=packets())
        self.assertTrue(ticks, 'Capacity scan blocked the API event loop')
        await pulse
        self.assertEqual(seen, [20])
        self.assertEqual(uploaded, 20)
        self.assertEqual(self.repo.source_path(self.job).read_bytes(), b'x' * 20)

    async def test_disconnected_body_rolls_back_flushed_batches(self):
        async def disconnected():
            yield b'abcdef'
            raise ConnectionError('disconnected')
        with patch('app.services.upload_service.CAPACITY_BATCH_BYTES', 3):
            with self.assertRaises(ConnectionError):
                await UploadService(self.repo).append_chunk(
                    job_id=self.job.job_id, expected_offset=0, body=disconnected())
        self.assertEqual(self.repo.source_path(self.job).stat().st_size, 0)
        self.assertEqual(self.repo.read(self.job.job_id).uploaded_bytes, 0)

    def test_api_returns_507_on_capacity_failure(self):
        from fastapi.testclient import TestClient
        from app.api import routes
        from app.main import app
        def check(size):
            raise StorageLimitError("Storage unavailable")
        with patch.object(routes, "repository", self.repo), patch.object(routes, "upload_service", UploadService(self.repo, check)):
            response = TestClient(app).put(f"/api/uploads/{self.job.job_id}/chunks?offset=0", content=b"test")
        self.assertEqual(response.status_code, 507)
        self.assertEqual(self.repo.source_path(self.job).stat().st_size, 0)


if __name__ == '__main__':
    unittest.main()
