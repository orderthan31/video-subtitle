from pathlib import Path
from contextlib import ExitStack
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api')]
from fastapi.testclient import TestClient
from app.api import routes, asset_routes, video_upload_routes
from app.main import app
from video_service.assets import VideoAssetRepository
from video_service.locking import download_lock
from video_service.migration import migrate_legacy_assets
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.video_probe import inspect_video

FFMPEG = shutil.which('ffmpeg') or next((str(p) for p in (ROOT / '.tools').glob('ffmpeg-*/bin/ffmpeg.exe')), None)


class VideoRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(routes, 'repository', self.repo))
        self.stack.enter_context(patch.object(asset_routes, 'reserve_copy', lambda _: None))
        self.stack.enter_context(patch.object(video_upload_routes, 'assert_capacity', lambda *a: None))
        self.stack.enter_context(patch.object(video_upload_routes, 'assert_free_space', lambda *a: None))
        self.client = TestClient(app)

    def job(self, status=JobStatus.COMPLETED):
        job = self.repo.create_job(original_filename='test.mp4', expected_size=5,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        self.repo.source_path(job).write_bytes(b'video')
        return self.repo.update_status(job.job_id, status)

    def test_migration_dry_run_apply_repeat_and_preservation(self):
        job = self.job()
        uploading = self.job(JobStatus.UPLOADING)
        dry = migrate_legacy_assets(self.repo, before_copy=lambda _: None)
        self.assertEqual(dry['pending'], [{'job_id': job.job_id, 'copy_bytes': 5}])
        self.assertEqual(dry['skipped'][0]['job_id'], uploading.job_id)
        self.assertNotIn('asset_id', self.repo.read(job.job_id).metadata)
        inspect = lambda _: {'duration': 5, 'has_audio': True}
        report = migrate_legacy_assets(self.repo, before_copy=lambda _: None, inspect_source=inspect, dry_run=False)
        self.assertEqual(len(report['registered']), 1)
        self.assertEqual(self.repo.read(job.job_id).status, JobStatus.COMPLETED)
        self.assertEqual(self.repo.source_path(job).read_bytes(), b'video')
        repeated = migrate_legacy_assets(self.repo, before_copy=lambda _: self.fail('Duplicate copy'),
                                          inspect_source=inspect, dry_run=False)
        self.assertEqual(repeated['already_linked'], [job.job_id])

    def test_invalid_video_does_not_publish_or_delete_uploaded_bytes(self):
        job = self.job()
        with patch.object(asset_routes, 'inspect_video', side_effect=ValueError('not video')):
            self.assertEqual(self.client.post('/api/jobs/' + job.job_id + '/register-source').status_code, 422)
        self.assertEqual(VideoAssetRepository(self.repo).list(), [])
        self.assertEqual(self.repo.source_path(job).read_bytes(), b'video')

    def test_migration_reports_corrupt_manifest(self):
        job = self.job()
        self.repo.job_file(job.job_id).write_text('{invalid')
        report = migrate_legacy_assets(self.repo, before_copy=lambda _: None)
        self.assertEqual(report['failed'], [{'job_id': job.job_id, 'reason': 'Invalid job manifest'}])

    def test_range_stream_and_delete_lease(self):
        job = self.job()
        assets = VideoAssetRepository(self.repo)
        asset = assets.register_upload(job.job_id, before_copy=lambda _: None)
        self.repo.delete_job_dir(job.job_id)
        base = '/api/videos/' + asset['asset_id']
        response = self.client.get(base + '/stream', headers={'Range': 'bytes=1-3'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'ide')
        self.assertEqual(response.headers['content-range'], 'bytes 1-3/5')
        self.assertTrue(response.headers['content-disposition'].startswith('inline'))
        with download_lock(assets, asset['asset_id']):
            self.assertEqual(self.client.delete(base).status_code, 409)
        self.assertEqual(self.client.delete(base).status_code, 204)
        self.assertEqual(self.client.get(base + '/stream').status_code, 404)

    def test_cover_art_or_audio_only_is_not_a_video(self):
        for streams in [[{'codec_type': 'audio'}],
                        [{'codec_type': 'video', 'disposition': {'attached_pic': 1}}]]:
            response = subprocess.CompletedProcess([], 0, stdout=json.dumps({'format': {'duration': 5}, 'streams': streams}).encode())
            with patch('video_service.video_probe.subprocess.run', return_value=response), self.assertRaises(ValueError):
                inspect_video(self.root / 'fake.mp4')

    @unittest.skipUnless(FFMPEG, 'FFmpeg is needed for synthetic media verification')
    def test_real_synthetic_video_upload_probe_and_range_stream(self):
        source = self.root / 'synthetic.mp4'
        subprocess.run([FFMPEG, '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
            '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
            check=True, capture_output=True, timeout=30)
        content = source.read_bytes()
        created = self.client.post('/api/video-uploads', json={
            'request_id': str(uuid4()), 'filename': 'sample.mp4', 'size': len(content)})
        base = '/api/video-uploads/' + created.json()['upload_id']
        self.assertEqual(self.client.put(base + '/chunks?offset=0', content=content).status_code, 200)
        completed = self.client.post(base + '/complete')
        self.assertEqual(completed.status_code, 200, completed.text)
        asset = completed.json()['video']
        self.assertFalse(asset['media']['has_audio'])
        self.assertEqual((asset['media']['width'], asset['media']['height']), (160, 90))
        self.assertAlmostEqual(asset['media']['duration'], 1, places=1)
        response = self.client.get('/api/videos/' + asset['asset_id'] + '/stream', headers={'Range': 'bytes=0-63'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, content[:64])
        self.assertEqual(self.repo.list(), [])


if __name__ == '__main__':
    unittest.main()
