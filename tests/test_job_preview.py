from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api')]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.storage import write_json_atomic


class JobPreviewTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        self.job = self.repo.create_job(original_filename='clip.mp4', expected_size=10,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        self.directory = self.repo.job_dir(self.job.job_id)
        self.cues = [{'start':1.0,'end':2.5,'text':'Hello'}]
        patcher = patch.object(routes, 'repository', self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.url = f'/api/jobs/{self.job.job_id}'

    def test_pending_then_transcription_then_translation(self):
        value = self.client.get(self.url+'/preview').json()
        self.assertFalse(value['video_available'])
        self.assertFalse(value['tracks']['original']['available'])
        write_json_atomic(self.directory/'work/transcript.json', self.cues)
        value = self.client.get(self.url+'/preview').json()
        self.assertEqual(value['tracks']['original']['cues'], self.cues)
        self.assertFalse(value['tracks']['translated']['available'])
        write_json_atomic(self.directory/'work/translated.json', self.cues)
        self.assertTrue(self.client.get(self.url+'/preview').json()['tracks']['translated']['available'])

    def test_partial_results_are_explicit_and_full_overrides_partial(self):
        write_json_atomic(self.directory/'work/partial-transcript.json', {'segments':self.cues})
        write_json_atomic(self.directory/'work/partial-translated.json', {'segments':self.cues})
        value = self.client.get(self.url+'/preview').json()
        self.assertTrue(value['tracks']['original']['partial'])
        self.assertTrue(value['tracks']['translated']['partial'])
        write_json_atomic(self.directory/'work/transcript.json', [])
        track = self.client.get(self.url+'/preview').json()['tracks']['original']
        self.assertFalse(track['partial'])
        self.assertTrue(track['available'])
        self.assertEqual(track['cues'], [])

    def test_reviewed_text_takes_precedence(self):
        write_json_atomic(self.directory/'work/translated.json', self.cues)
        edited = [{'start':1.0,'end':2.5,'text':'Edited'}]
        write_json_atomic(self.directory/'work/subtitle-draft.json', {'tracks':{'translated':edited}})
        self.assertEqual(self.client.get(self.url+'/preview').json()['tracks']['translated']['cues'], edited)

    def test_inline_stream_supports_range_and_completion_gate(self):
        (self.directory/'output/final.mp4').write_bytes(b'0123456789')
        self.assertEqual(self.client.get(self.url+'/stream').status_code,409)
        self.repo.update_status(self.job.job_id, JobStatus.COMPLETED)
        self.assertTrue(self.client.get(self.url+'/preview').json()['video_available'])
        response = self.client.get(self.url+'/stream',headers={'Range':'bytes=2-5'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,b'2345')
        self.assertEqual(response.headers['content-range'],'bytes 2-5/10')
        self.assertTrue(response.headers['content-disposition'].startswith('inline'))
        self.assertEqual(response.headers['content-type'],'video/mp4')
        self.assertEqual(self.client.get(self.url+'/stream',headers={'Range':'bytes=99-100'}).status_code,416)

    def test_owner_and_expiration_rules_apply_to_both_endpoints(self):
        write_json_atomic(self.directory/'work/transcript.json', self.cues)
        (self.directory/'output/final.mp4').write_bytes(b'0123456789')
        self.repo.update_status(self.job.job_id,JobStatus.COMPLETED,metadata={'results_expired_at':'2026-09-12'})
        value = self.client.get(self.url+'/preview').json()
        self.assertFalse(value['tracks']['original']['available'])
        self.assertFalse(value['video_available'])
        self.assertTrue(value['expired'])
        self.assertEqual(self.client.get(self.url+'/stream').status_code,410)
        record = self.repo.read(self.job.job_id)
        record.metadata['owner_id']='someone-else'
        self.repo.save(record)
        for endpoint in ['preview','stream']:
            self.assertEqual(self.client.get(self.url+'/'+endpoint).status_code,404)


if __name__ == '__main__':
    unittest.main()
