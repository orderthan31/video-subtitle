from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api')]
from fastapi.testclient import TestClient
from app.api import routes, asset_routes
from app.main import app
from video_service.capacity import StorageLimitError
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class AssetApiTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        self.job = self.repo.create_job(original_filename='sample.mp4', expected_size=5,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        self.repo.source_path(self.job).write_bytes(b'video')
        (self.repo.job_dir(self.job.job_id) / 'output/final.mp4').write_bytes(b'encoded')
        self.repo.update_status(self.job.job_id, JobStatus.COMPLETED)
        for target, attribute, value in [(routes, 'repository', self.repo),
                                         (asset_routes, 'reserve_copy', lambda _: None)]:
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.base = '/api/jobs/' + self.job.job_id

    def test_register_list_detail_and_delete_protection(self):
        response = self.client.post(self.base + '/register-source')
        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()
        self.assertEqual(self.client.post(self.base + '/register-source').json(), asset)
        self.assertEqual(self.client.get('/api/videos').json()['videos'], [asset])
        detail = self.client.get('/api/videos/' + asset['asset_id']).json()
        self.assertEqual(detail['jobs'][0]['job_id'], self.job.job_id)
        self.assertEqual(self.client.delete('/api/videos/' + asset['asset_id']).status_code, 409)

    def test_promotion_survives_job_deletion(self):
        asset = self.client.post(self.base + '/promote-video').json()
        self.repo.delete_job_dir(self.job.job_id)
        self.assertEqual(self.client.get('/api/videos/' + asset['asset_id']).status_code, 200)
        self.assertEqual(self.client.delete('/api/videos/' + asset['asset_id']).status_code, 204)

    def test_foreign_job_and_video_are_hidden(self):
        asset = self.client.post(self.base + '/register-source').json()
        with patch.object(asset_routes.VideoAssetRepository, 'read',
                          side_effect=asset_routes.AssetNotFoundError('hidden')):
            self.assertEqual(self.client.get('/api/videos/' + asset['asset_id']).status_code, 404)
        self.repo.update_status(self.job.job_id, JobStatus.COMPLETED, metadata={'owner_id': 'other'})
        self.assertEqual(self.client.post(self.base + '/register-source').status_code, 404)
        self.assertEqual(self.client.post(self.base + '/promote-video').status_code, 404)

    def test_capacity_failure_is_507_and_no_asset(self):
        with patch.object(asset_routes, 'reserve_copy', side_effect=StorageLimitError('full')):
            self.assertEqual(self.client.post(self.base + '/register-source').status_code, 507)
        self.assertEqual(self.client.get('/api/videos').json()['videos'], [])


if __name__ == '__main__':
    unittest.main()
