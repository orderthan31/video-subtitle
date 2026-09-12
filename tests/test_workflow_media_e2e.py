"""Real media processing; only model responses are deterministic local substitutes."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api'), str(ROOT / 'workers/media')]
from fastapi.testclient import TestClient
from app.api import routes, asset_routes, video_upload_routes, workflow_routes
from app.main import app
from media_worker.worker import Worker
from video_service.models import JobStatus
from video_service.repository import FilesystemJobRepository
from video_service.transcript import TranscriptSegment
from video_service.video_probe import inspect_video

FFMPEG = shutil.which('ffmpeg') or next((str(p) for p in (ROOT / '.tools').glob('ffmpeg-*/bin/ffmpeg.exe')), None)


class LocalProvider:
    transcription_model = translation_model = audio_filter_model = 'local-test-only'
    def __init__(self):
        self.transcriptions = self.translations = 0

    def transcribe(self, audio, language, check, **kwargs):
        check()
        with wave.open(str(audio)) as source:
            assert source.getnchannels() == 1 and source.getframerate() == 16000
        self.transcriptions += 1
        kwargs['progress']({'total': 1, 'completed': 1, 'in_flight': 0, 'retrying': 0, 'failed': 0, 'draining': False})
        return [TranscriptSegment(0.25, 1.5, 'Synthetic dialogue.')]

    def translate(self, segments, language, check, **kwargs):
        check()
        self.translations += 1
        kwargs['progress']({'total': 1, 'completed': 1, 'in_flight': 0, 'retrying': 0, 'failed': 0, 'draining': False})
        return [s.with_text('Translated dialogue.') for s in segments]


@unittest.skipUnless(FFMPEG, 'Install FFmpeg for media E2E verification')
class WorkflowMediaE2E(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(routes, 'repository', self.repo))
        settings = SimpleNamespace(service_quota_bytes=128*1024**2, min_free_space_bytes=0, max_upload_bytes=16*1024**2)
        for module in [asset_routes, video_upload_routes, workflow_routes]:
            stack.enter_context(patch.object(module, 'settings', settings))
        stack.enter_context(patch.dict('os.environ', {'PAID_LLM_ENABLED': 'false', 'VIDEO_ENCODER': 'libx264',
            'MIN_FREE_SPACE_BYTES': '0', 'VIDEO_SERVICE_QUOTA_BYTES': str(128*1024**2), 'VOCALIZATION_FILTER_ENABLED': 'false'}))
        # A regression that reaches a network-capable provider must fail this test.
        stack.enter_context(patch('media_worker.providers.GeminiProvider._client', side_effect=AssertionError('Real LLM client forbidden')))
        self.client = TestClient(app)
        self.provider = LocalProvider()
        source = self.root / 'fixture.mp4'
        subprocess.run([FFMPEG, '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000', '-t', '3', '-c:v', 'libx264',
            '-c:a', 'aac', '-pix_fmt', 'yuv420p', str(source)], capture_output=True, check=True, timeout=30)
        self.original = source.read_bytes()
        response = self.client.post('/api/video-uploads', json={'request_id': str(uuid4()),
            'filename': 'fixture.mp4', 'size': len(self.original)})
        self.assertEqual(response.status_code, 201, response.text)
        upload = '/api/video-uploads/' + response.json()['upload_id']
        self.assertEqual(self.client.put(upload + '/chunks?offset=0', content=self.original).status_code, 200)
        response = self.client.post(upload + '/complete')
        self.assertEqual(response.status_code, 200, response.text)
        self.asset = response.json()['video']

    def run_job(self, template, *, asset=None, **inputs):
        asset_id = (asset or self.asset)['asset_id']
        options = {'source_language': 'en', 'target_language': 'en', 'video_codec': 'h264',
                   'quality_profile': 'compact', 'audio_filter': 'off', **inputs.pop('options', {})}
        response = self.client.post('/api/videos/' + asset_id + '/jobs', json={
            'request_id': str(uuid4()), 'template': template, 'options': options, **inputs})
        self.assertEqual(response.status_code, 201, response.text)
        job_id = response.json()['job_id']
        Worker(self.repo, self.provider).process(job_id)
        record = self.repo.read(job_id)
        self.assertEqual(record.status, JobStatus.COMPLETED, record.error)
        for filename in record.metadata['result_files']:
            self.assertEqual(self.client.get(f'/api/jobs/{job_id}/results/{filename}').status_code, 200)
        return record

    def test_extract_reuse_transcribe_translate_encode_promote(self):
        audio = self.run_job('extract_audio')
        transcript = self.run_job('transcribe', audio_job_id=audio.job_id)
        self.assertFalse((self.repo.job_dir(transcript.job_id) / 'work/audio.wav').exists())
        self.assertEqual((self.provider.transcriptions, self.provider.translations), (1, 0))
        imported = self.client.post('/api/videos/' + self.asset['asset_id'] + '/subtitle-inputs', json={
            'job_id': transcript.job_id, 'track': 'original', 'revision': 0})
        self.assertEqual(imported.status_code, 201, imported.text)
        translation = self.run_job('translate', subtitle_artifact_id=imported.json()['artifact_id'])
        selected = self.client.post('/api/videos/' + self.asset['asset_id'] + '/subtitle-inputs', json={
            'job_id': translation.job_id, 'track': 'translated', 'revision': 0}).json()
        encoded = self.run_job('encode', subtitle_artifact_id=selected['artifact_id'], options={'subtitle_mode': 'soft'})
        self.assertEqual((self.provider.transcriptions, self.provider.translations), (1, 1))
        response = self.client.post('/api/jobs/' + encoded.job_id + '/promote-video')
        self.assertEqual(response.status_code, 200, response.text)
        promoted = response.json()
        self.repo.delete_job_dir(encoded.job_id)
        stream = self.client.get('/api/videos/' + promoted['asset_id'] + '/stream', headers={'Range': 'bytes=0-31'})
        self.assertEqual(stream.status_code, 206)
        self.run_job('encode', asset=promoted, options={'subtitle_mode': 'none'})
        self.assertEqual((self.provider.transcriptions, self.provider.translations), (1, 1))

    def test_full_and_transcription_translation_without_encoding(self):
        full = self.run_job('full')
        final = self.repo.job_dir(full.job_id) / 'output/final.mp4'
        self.assertEqual(inspect_video(final)['video_codec'], 'h264')
        partial = self.run_job('transcribe_translate')
        self.assertNotIn('final.mp4', partial.metadata['result_files'])
        self.assertFalse((self.repo.job_dir(partial.job_id) / 'work/encode.log').exists())
        self.assertEqual((self.provider.transcriptions, self.provider.translations), (2, 2))
        original = self.client.get('/api/videos/' + self.asset['asset_id'] + '/stream')
        self.assertEqual(original.content, self.original)


if __name__ == '__main__':
    unittest.main()
