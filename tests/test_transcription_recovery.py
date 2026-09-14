import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'workers/media'), str(ROOT / 'packages/shared')]
from media_worker.transcription_recovery import recover_recorded_sentences


class RecordedRecoveryTests(unittest.TestCase):
    def test_recovery_is_scoped_and_never_overwrites_completed_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = '20260914T133217270320Z-' + 'a' * 32
            folder = root / trace
            folder.mkdir()
            request = {'diagnostic_context': {'job_id': 'job', 'queue_id': 'queue',
                'stage': 'transcription', 'segment': 0}, 'window': {'clock': 'preprocessed_audio',
                'split_depth': 0, 'start_seconds': 0, 'end_seconds': 60}}
            items = [{'start': 54.34, 'end': 57.17, 'text': 'First'},
                     {'start': 56.45, 'end': 57.5, 'text': 'Second'}]
            response = {'status': 200, 'body': json.dumps({'candidates': [{'finishReason': 'STOP',
                'content': {'parts': [{'text': json.dumps(items)}]}}]})}
            (folder / 'request.json').write_text(json.dumps(request))
            (folder / 'response.json').write_text(json.dumps(response))
            saved = {'count': 1, 'results': {}, 'history': [{'segment': 0,
                'diagnostics': {'events': [{'trace_id': trace}]}}]}
            self.assertEqual(recover_recorded_sentences(saved, 'queue', 'job', root), {0: items})
            self.assertEqual(recover_recorded_sentences(saved, 'other', 'job', root), {})
            saved['results']['0'] = []
            self.assertEqual(recover_recorded_sentences(saved, 'queue', 'job', root), {})
            saved['results'] = {}
            response['status'] = 429
            (folder / 'response.json').write_text(json.dumps(response))
            self.assertEqual(recover_recorded_sentences(saved, 'queue', 'job', root), {})
