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
from video_service.storage import write_json_atomic


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
                                         (asset_routes, 'inspect_video', lambda _: {'duration': 60, 'has_audio': True}),
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

    def test_attachment_and_plan_are_video_scoped(self):
        asset = self.client.post(self.base + '/register-source').json()
        base = '/api/videos/' + asset['asset_id']
        payload = {'filename': 'input.srt', 'language': 'en',
                   'content': '1\n00:00:00,000 --> 00:00:01,000\nHello\n'}
        response = self.client.post(base + '/subtitles', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        artifact_id = response.json()['artifact_id']
        self.assertEqual(self.client.get(base + '/subtitles').json()['subtitles'][0]['artifact_id'], artifact_id)
        plan = self.client.post(base + '/workflow-plan', json={
            'template': 'translate', 'subtitle_artifact_id': artifact_id})
        self.assertEqual(plan.json()['stages'], ['analyze', 'translate', 'generate_subtitle'])
        self.assertEqual(self.client.post(base + '/workflow-plan', json={'template': 'translate'}).status_code, 422)
        self.assertEqual(self.client.post('/api/videos/' + uuid4().hex + '/subtitles', json=payload).status_code, 404)
        self.assertEqual(self.client.post(base + '/subtitles', json={**payload, 'content': 'invalid'}).status_code, 422)

    def test_import_job_revision_retained_and_stale_selection_rejected(self):
        asset = self.client.post(self.base + '/register-source').json()
        base = '/api/videos/' + asset['asset_id']
        work = self.repo.job_dir(self.job.job_id) / 'work'
        cues = [{'start': 0, 'end': 1, 'text': 'Original version'}]
        for filename in ['transcript.json', 'translated.json']:
            write_json_atomic(work / filename, cues)
        self.repo.update_status(self.job.job_id, JobStatus.COMPLETED, metadata={'duration': 2})
        payload = {'job_id': self.job.job_id, 'track': 'translated', 'revision': 0}
        response = self.client.post(base + '/subtitle-inputs', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        artifact_id = response.json()['artifact_id']
        self.repo.update_status(self.job.job_id, JobStatus.COMPLETED, metadata={'subtitle_revision': 1})
        self.assertEqual(self.client.post(base + '/subtitle-inputs', json=payload).status_code, 409)
        self.repo.delete_job_dir(self.job.job_id)
        self.assertEqual(self.client.get(base + '/subtitles/' + artifact_id).json()['cues'][0]['text'], 'Original version')


if __name__ == '__main__':
    unittest.main()
