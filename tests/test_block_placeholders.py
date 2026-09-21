import asyncio
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'workers/media'), str(ROOT / 'packages/shared')]
from media_worker.providers import GeminiProvider
from media_worker.content_block import ContentBlockedError, BLOCKED_TEXT
from video_service.transcript import TranscriptSegment


class BlockPlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex / 'job' / 'work'
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root.parents[1])
        self.provider = GeminiProvider.__new__(GeminiProvider)
        self.provider.transcription_model = self.provider.translation_model = 'mock-model'
        self.provider._request = AsyncMock(side_effect=ContentBlockedError('PROHIBITED_CONTENT'))

    def test_translation_marks_whole_blocked_batches_and_preserves_source_markers(self):
        cues = [TranscriptSegment(i, i + .8, f'Speech {i}') for i in range(81)]
        cues.insert(0, TranscriptSegment(0, .1, BLOCKED_TEXT))
        progress = []
        result = self.provider.translate(cues, 'ko', lambda: None, work=self.root, progress=progress.append)
        self.assertEqual(len(result), 82)
        self.assertTrue(all(s.text == BLOCKED_TEXT for s in result))
        self.assertEqual(self.provider._request.call_count, 3)
        self.assertEqual(len(progress[-1]['content_blocks']), 3)
        for call in self.provider._request.call_args_list:
            self.assertNotIn(BLOCKED_TEXT, call.args[0][0]['text'])

    def test_all_source_markers_need_no_translation_calls(self):
        cues = [TranscriptSegment(0, 60, BLOCKED_TEXT)]
        self.assertEqual(self.provider.translate(cues, 'en', lambda: None, work=self.root), cues)
        self.provider._request.assert_not_called()

    def test_transcription_placeholders_cover_each_request_window(self):
        source = self.root / 'audio.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\0\0' * 8000 * 125)
        result = self.provider.transcribe(source, 'auto', lambda: None, work=self.root)
        self.assertEqual([(s.start, s.end) for s in result], [(0, 60), (60, 120), (120, 125)])
        self.assertTrue(all(s.text == BLOCKED_TEXT for s in result))
        self.assertEqual(self.provider._request.call_count, 3)
