from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'packages/shared'), str(ROOT/'apps/api')]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from video_service.repository import FilesystemJobRepository
from video_service.models import JobStatus, QualityProfile
from video_service.storage import write_json_atomic
from video_service.capacity import StorageLimitError


class FinalSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'data/test-runs'/uuid4().hex
        self.repo=FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree,self.root)
        self.job=self.repo.create_job(original_filename='final.mp4',expected_size=10,
            source_language='en',target_language='ko',quality_profile=QualityProfile.BALANCED)
        self.dir=self.repo.job_dir(self.job.job_id)
        cues=[{'start':0,'end':1,'text':'First'},{'start':2,'end':3,'text':'Second'}]
        for name in ['transcript','translated']:
            write_json_atomic(self.dir/f'work/{name}.json',cues)
        (self.dir/'output/final.mp4').write_bytes(b'video')
        (self.dir/'output/translated.srt').write_text('old subtitle',encoding='utf-8')
        self.repo.update_status(self.job.job_id,JobStatus.COMPLETED,metadata={'duration':10})
        p=patch.object(routes,'repository',self.repo)
        p.start();self.addCleanup(p.stop)
        self.client=TestClient(app)
        self.base=f'/api/jobs/{self.job.job_id}'

    def save(self,draft):
        return self.client.put(self.base+'/subtitles',json={k:draft[k] for k in ['revision','tracks']})

    def test_edit_times_text_delete_and_download_preview_consistency(self):
        draft=self.client.get(self.base+'/subtitles').json()
        draft['tracks']['translated']=[{'start':1.25,'end':2.75,'text':'Edited'}]
        response=self.save(draft)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['revision'],1)
        srt=self.client.get(self.base+'/results/translated.srt').text
        self.assertIn('00:00:01,250 --> 00:00:02,750',srt)
        self.assertIn('Edited',srt)
        self.assertNotIn('Second',srt)
        self.assertIn('Edited',self.client.get(self.base+'/results/translated.smi').text)
        self.assertEqual(self.client.get(self.base+'/preview').json()['tracks']['translated']['cues'],draft['tracks']['translated'])
        self.assertEqual((self.dir/'output/translated.srt').read_text(),'old subtitle')
        self.assertEqual((self.dir/'output/final.mp4').read_bytes(),b'video')
        self.assertEqual(self.repo.read(self.job.job_id).status,JobStatus.COMPLETED)
        self.assertEqual(self.save(draft).status_code,409)

    def test_delete_all_cues_and_reopen(self):
        draft=self.client.get(self.base+'/subtitles').json()
        draft['tracks']['translated']=[]
        self.assertEqual(self.save(draft).status_code,200)
        self.assertEqual(self.client.get(self.base+'/results/translated.srt').content,b'')
        self.assertEqual(self.client.get(self.base+'/subtitles').json()['tracks']['translated'],[])

    def test_invalid_times_and_capacity_failure_preserve_published_revision(self):
        original=self.client.get(self.base+'/subtitles').json()
        for start,end in [(3,2),(-1,1),(0,11),(0.9999,1.0001)]:
            draft=deepcopy(original)
            draft['tracks']['translated'][0].update(start=start,end=end)
            self.assertEqual(self.save(draft).status_code,422)
        with patch.object(routes,'assert_capacity',side_effect=StorageLimitError('full')):
            self.assertEqual(self.save(original).status_code,507)
        self.assertNotIn('subtitle_revision_dir',self.repo.read(self.job.job_id).metadata)
        self.assertEqual((self.dir/'output/translated.srt').read_text(),'old subtitle')

    def test_expired_or_render_requests_rejected(self):
        draft=self.client.get(self.base+'/subtitles').json()
        response=self.client.put(self.base+'/subtitles',json={'revision':0,'tracks':draft['tracks'],'action':'render'})
        self.assertEqual(response.status_code,409)
        self.repo.update_status(self.job.job_id,JobStatus.COMPLETED,metadata={'results_expired_at':'expired'})
        self.assertEqual(self.client.get(self.base+'/subtitles').status_code,410)
        self.assertEqual(self.save(draft).status_code,410)

    def test_failed_publish_keeps_previous_revision_visible(self):
        draft=self.client.get(self.base+'/subtitles').json()
        self.assertEqual(self.save(draft).status_code,200)
        draft=self.client.get(self.base+'/subtitles').json()
        draft['tracks']['translated'][0]['text']='unpublished'
        with patch.object(self.repo,'save',side_effect=OSError(28,'disk full')):
            self.assertEqual(self.save(draft).status_code,507)
        self.assertEqual(self.client.get(self.base+'/subtitles').json()['revision'],1)
        self.assertNotIn('unpublished',self.client.get(self.base+'/results/translated.srt').text)


if __name__=='__main__':unittest.main()
