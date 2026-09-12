from contextlib import ExitStack
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'apps/api'), str(ROOT / 'workers/media')]
from fastapi.testclient import TestClient
from app.api import routes, asset_routes, workflow_routes
from app.main import app
from media_worker.worker import Worker
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.timeline import build_timeline_from_kept_intervals
from video_service.transcript import TranscriptSegment


class WorkflowExecutionTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.repo = FilesystemJobRepository(self.root)
        self.addCleanup(shutil.rmtree, self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(routes, 'repository', self.repo))
        self.stack.enter_context(patch.object(asset_routes, 'reserve_copy', lambda _: None))
        self.stack.enter_context(patch.object(workflow_routes, 'assert_capacity', lambda *a: None))
        self.stack.enter_context(patch.dict('os.environ', {'PAID_LLM_ENABLED': 'false',
            'VOCALIZATION_FILTER_ENABLED': 'false', 'MIN_FREE_SPACE_BYTES': '0'}))
        job = self.repo.create_job(original_filename='sample.mp4', expected_size=1,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        self.repo.source_path(job).write_bytes(b'v')
        self.repo.update_status(job.job_id, JobStatus.READY)
        self.client = TestClient(app)
        self.asset = self.client.post('/api/jobs/' + job.job_id + '/register-source').json()
        self.base = '/api/videos/' + self.asset['asset_id']
        self.provider = Mock(transcription_model='fake-stt', translation_model='fake-translation', audio_filter_model='fake-filter')
        self.provider.transcribe.return_value = [TranscriptSegment(0, 1, 'Source speech.')]
        self.provider.translate.side_effect = lambda segments, *a, **k: [s.with_text('Translated speech.') for s in segments]
        self.media = {'duration': 5, 'streams': [
            {'codec_type': 'video', 'width': 640, 'height': 360, 'avg_frame_rate': '30/1',
             'codec_name': 'hevc', 'codec_tag_string': 'hvc1', 'pix_fmt': 'yuv420p'},
            {'codec_type': 'audio', 'codec_name': 'aac'}]}

    def create(self, template, *, subtitle=False, none=False):
        payload = {'request_id': str(uuid4()), 'template': template,
                   'options': {'subtitle_mode': 'none' if none else 'burn'}}
        if subtitle:
            response = self.client.post(self.base + '/subtitles', json={'filename': 'input.srt',
                'language': 'en', 'content': '1\n00:00:01,250 --> 00:00:02,750\nSelected input.\n'})
            payload['subtitle_artifact_id'] = response.json()['artifact_id']
        response = self.client.post(self.base + '/jobs', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['job_id'], payload

    def execute(self, job_id):
        work = self.repo.job_dir(job_id) / 'work'
        audio = work / 'audio.wav'
        def extract(*args):
            audio.write_bytes(b'audio')
            return audio
        def preprocess(*args, **kwargs):
            (work / 'processed-audio.wav').write_bytes(b'processed')
            (work / 'timeline-map.json').write_text('[]')
            return audio, build_timeline_from_kept_intervals([(0, 5)])
        def encode(args, **kwargs):
            Path(args[-1]).write_bytes(b'encoded')
        with ExitStack() as stack:
            stack.enter_context(patch('media_worker.worker.probe', return_value=self.media))
            extraction = stack.enter_context(patch('media_worker.worker.extract_audio', side_effect=extract))
            preprocessing = stack.enter_context(patch('media_worker.worker.preprocess_audio', side_effect=preprocess))
            encoder = stack.enter_context(patch('media_worker.worker.select_encoder', return_value='libx265'))
            encoding = stack.enter_context(patch('media_worker.worker.run_process', side_effect=encode))
            stack.enter_context(patch('media_worker.worker.validate_decodable'))
            Worker(self.repo, self.provider).process(job_id)
        return self.repo.read(job_id), extraction, preprocessing, encoder, encoding

    def test_all_templates_execute_only_selected_stages(self):
        for template, subtitle, none, counts in [
            ('extract_audio', False, False, (1, 0, 0, 0)),
            ('transcribe', False, False, (1, 1, 0, 0)),
            ('transcribe_translate', False, False, (1, 1, 1, 0)),
            ('translate', True, False, (0, 0, 1, 0)),
            ('encode', True, False, (0, 0, 0, 1)),
            ('encode', False, True, (0, 0, 0, 1)),
            ('full', False, False, (1, 1, 1, 1)),
            ('full', True, False, (0, 0, 1, 1)),
        ]:
            with self.subTest(template=template, subtitle=subtitle):
                self.provider.reset_mock()
                job_id, _ = self.create(template, subtitle=subtitle, none=none)
                record, extraction, preprocessing, encoder, encoding = self.execute(job_id)
                self.assertEqual(record.status, JobStatus.COMPLETED, record.error)
                self.assertEqual((extraction.call_count, self.provider.transcribe.call_count,
                    self.provider.translate.call_count, encoding.call_count), counts)
                self.assertEqual(encoder.call_count, counts[-1])
                self.assertEqual(preprocessing.call_count, counts[1])
                for filename in record.metadata['result_files']:
                    self.assertEqual(self.client.get('/api/jobs/' + job_id + '/results/' + filename).status_code, 200)

    def test_creation_idempotency_and_snapshot_survives_attachment_change(self):
        job_id, payload = self.create('translate', subtitle=True)
        self.assertEqual(self.client.post(self.base + '/jobs', json=payload).json()['job_id'], job_id)
        self.assertEqual(self.client.post(self.base + '/jobs', json={**payload, 'template': 'full'}).status_code, 409)
        artifact = self.root / '.assets' / self.asset['asset_id'] / 'subtitles' / (payload['subtitle_artifact_id'] + '.json')
        artifact.unlink()
        self.assertEqual(self.execute(job_id)[0].status, JobStatus.COMPLETED)
        self.assertEqual(self.provider.translate.call_args.args[0][0].start, 1.25)
        self.assertEqual(self.client.delete(self.base).status_code, 409)

    def test_translation_failure_retry_reuses_transcription(self):
        job_id, _ = self.create('transcribe_translate')
        self.provider.translate.side_effect = RuntimeError('mock failure')
        self.assertEqual(self.execute(job_id)[0].status, JobStatus.FAILED)
        self.provider.translate.side_effect = lambda segments, *a, **k: segments
        self.repo.update_status(job_id, JobStatus.QUEUED)
        self.assertEqual(self.execute(job_id)[0].status, JobStatus.COMPLETED)
        self.assertEqual(self.provider.transcribe.call_count, 1)

    def test_video_without_audio_can_encode_without_subtitles(self):
        self.media['streams'] = self.media['streams'][:1]
        job_id, _ = self.create('encode', none=True)
        self.assertEqual(self.execute(job_id)[0].status, JobStatus.COMPLETED)
        self.provider.transcribe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
