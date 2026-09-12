from pathlib import Path
import hashlib
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api')]
from fastapi.testclient import TestClient
from app.api import routes, video_upload_routes
from app.main import app
from video_service.capacity import StorageLimitError, remaining_reservations
from video_service.models import JobStatus
from video_service.repository import FilesystemJobRepository


class VideoUploadTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        for target, name, value in [(routes, 'repository', self.repo),
                (video_upload_routes, 'assert_capacity', lambda *a: None),
                (video_upload_routes, 'assert_free_space', lambda *a: None)]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app)
        self.payload = {'filename': 'sample.mp4', 'size': 10000, 'request_id': str(uuid4())}

    def create(self):
        response = self.client.post('/api/video-uploads', json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        return '/api/video-uploads/' + response.json()['upload_id']

    def test_upload_only_resume_verify_complete_idempotent(self):
        base = self.create()
        self.assertEqual(self.create(), base)
        response = self.client.put(base + '/chunks?offset=0', content=b'a' * 4000)
        self.assertEqual(response.json()['uploaded_bytes'], 4000)
        self.assertEqual(self.client.get(base).json()['uploaded_bytes'], 4000)
        check = {'uploaded_bytes': 4000, 'offset': 0, 'length': 4000,
                 'sha256': hashlib.sha256(b'a' * 4000).hexdigest()}
        self.assertEqual(self.client.post(base + '/verify', json=check).status_code, 204)
        self.assertEqual(self.client.post(base + '/complete').status_code, 409)
        self.assertEqual(self.client.put(base + '/chunks?offset=0', content=b'x').status_code, 409)
        self.assertEqual(self.client.put(base + '/chunks?offset=4000', content=b'a' * 6000).status_code, 200)
        result = self.client.post(base + '/complete')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), self.client.post(base + '/complete').json())
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])
        self.assertEqual(self.repo.find_by_statuses([JobStatus.QUEUED]), [])
        self.assertEqual(len(self.client.get('/api/videos').json()['videos']), 1)
        self.assertEqual(remaining_reservations(self.repo), 0)
        upload_id = base.rsplit('/', 1)[-1]
        self.assertEqual(self.client.post('/api/jobs/' + upload_id + '/start').status_code, 404)

    def test_reject_workflow_options_and_duplicate_id_mismatch(self):
        self.assertEqual(self.client.post('/api/video-uploads', json={**self.payload, 'vad_mode': 'nvidia'}).status_code, 422)
        self.create()
        self.assertEqual(self.client.post('/api/video-uploads', json={**self.payload, 'size': 9}).status_code, 409)

    def test_foreign_upload_hidden_on_all_endpoints(self):
        base = self.create()
        upload_id = base.rsplit('/', 1)[-1]
        uploads = video_upload_routes.upload_repository()
        record = uploads.read(upload_id)
        record.metadata['owner_id'] = 'other'
        uploads.save(record)
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.put(base + '/chunks?offset=0', content=b'x').status_code, 404)
        self.assertEqual(self.client.post(base + '/complete').status_code, 404)
        self.assertEqual(self.client.get('/api/video-uploads').json()['uploads'], [])

    def test_reservations_and_copy_failure_resume_without_reupload(self):
        base = self.create()
        self.assertGreater(remaining_reservations(self.repo), 10000)
        self.client.put(base + '/chunks?offset=0', content=b'x' * 10000)
        with patch.object(video_upload_routes, 'assert_capacity', side_effect=StorageLimitError('full')):
            self.assertEqual(self.client.post(base + '/complete').status_code, 507)
        self.assertEqual(self.client.get(base).json()['uploaded_bytes'], 10000)
        self.assertEqual(self.client.get('/api/videos').json()['videos'], [])
        self.assertEqual(self.client.post(base + '/complete').status_code, 200)

    def test_disk_full_returns_507_and_keeps_offset(self):
        base = self.create()
        with patch.object(video_upload_routes, 'assert_free_space', side_effect=StorageLimitError('full')):
            self.assertEqual(self.client.put(base + '/chunks?offset=0', content=b'x').status_code, 507)
        self.assertEqual(self.client.get(base).json()['uploaded_bytes'], 0)

    def test_explicit_delete_cleans_upload_but_cannot_bypass_asset(self):
        base = self.create()
        self.client.put(base + '/chunks?offset=0', content=b'x')
        self.assertEqual(self.client.delete(base).status_code, 204)
        self.assertEqual(self.client.get(base).status_code, 404)
        base = self.create()
        self.client.put(base + '/chunks?offset=0', content=b'x' * 10000)
        asset_id = self.client.post(base + '/complete').json()['video']['asset_id']
        self.assertEqual(self.client.delete(base).status_code, 409)
        self.assertEqual(self.client.delete('/api/videos/' + asset_id).status_code, 204)
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.get('/api/videos').json()['videos'], [])


if __name__ == '__main__':
    unittest.main()
