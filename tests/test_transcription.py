from pathlib import Path
import sys
import unittest
import base64
import io
import shutil
import uuid
import wave
import asyncio
import json
import hashlib
import httpx
from unittest.mock import Mock, AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider, WordTimestampError, parse_word_transcriptions, transcription_windows
from video_service.timeline import build_timeline_from_kept_intervals, project_segment_to_original
from sdk_fixture import sdk_fixture
from media_worker.sentence_transcription import transcription_prompt
from video_service.storage import write_json_atomic


class TranscriptionTests(unittest.TestCase):
    def window_fixture(self, seconds=20):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(seconds * 200))
        buffer.seek(0)
        audio = wave.open(buffer, "rb")
        self.addCleanup(audio.close)
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = "test-model"
        return provider, audio

    def test_invalid_sentence_window_retries_once_without_word_splitting(self):
        provider, audio = self.window_fixture()
        provider.request = Mock(side_effect=[[{"start": 4, "end": 3, "text": "Bad timing."}],
            [{"start": 9.5, "end": 10.4, "text": "Keep both."}]])
        result = provider.transcribe_window(audio, 0, 2000, "en", lambda: None)
        self.assertEqual([word["text"] for word in result], ["Keep both."])
        self.assertAlmostEqual(result[0]["start"], 9.5)
        self.assertAlmostEqual(result[0]["end"], 10.4)
        durations = []
        for call in provider.request.call_args_list:
            with wave.open(io.BytesIO(base64.b64decode(call.args[0][1]["inlineData"]["data"]))) as chunk:
                durations.append(chunk.getnframes() / chunk.getframerate())
        self.assertEqual(durations, [20, 20])
        self.assertEqual(len(provider.request.call_args.args), 4)

    def test_persistent_bad_sentences_have_bounded_retries(self):
        provider, audio = self.window_fixture(61)
        provider.request = Mock(side_effect=WordTimestampError("reversed interval"))
        with self.assertRaises(WordTimestampError):
            provider.transcribe_window(audio, 0, 6100, "en", lambda: None)
        self.assertEqual(provider.request.call_count, 2)

    def test_auth_failure_does_not_trigger_subdivision(self):
        provider, audio = self.window_fixture()
        provider.request = Mock(side_effect=RuntimeError("HTTP 401"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            provider.transcribe_window(audio, 0, 2000, "en", lambda: None)
        self.assertEqual(provider.request.call_count, 1)

    def test_sentence_and_speaker_boundaries(self):
        parts = [{"audioTranscription": {"speakerLabel": "A", "words": [
            {"word": "Hello", "startOffset": "0.1s", "endOffset": "0.3s"},
            {"word": "world.", "startOffset": "0.4s", "endOffset": "0.9s"},
            {"word": "Welcome", "startOffset": "1s", "endOffset": "1.5s"}]}},
            {"audioTranscription": {"speakerLabel": "B", "words": [
                {"word": "Yes", "startOffset": "1.6s", "endOffset": "2s"}]}}]
        result = parse_word_transcriptions(parts)
        self.assertEqual([s["text"] for s in result], ["Hello world.", "Welcome", "Yes"])
        self.assertEqual(result[0]["start"], 0.1)
        self.assertEqual(result[0]["end"], 0.9)

    def test_text_without_timings_rejected(self):
        with self.assertRaises(ValueError):
            parse_word_transcriptions([{"text": "Hello"}])

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            parse_word_transcriptions([{"audioTranscription": {"words": [
                {"word": "Hello", "startOffset": "NaNs", "endOffset": "2s"}]}}])

    def test_overlap_stays_inside_retained_regions(self):
        spans = build_timeline_from_kept_intervals([(0, 125), (145, 147)])
        windows = list(transcription_windows(12700, 100, spans))
        self.assertEqual([(a, b, c) for a, b, c, _ in windows], [
            (0, 0, 6100), (6000, 5900, 12100), (12000, 11900, 12500),
            (12500, 12500, 12700)])

    def test_incomplete_timeline_rejected_before_requests(self):
        spans = build_timeline_from_kept_intervals([(0, 1)])
        with self.assertRaises(ValueError):
            next(transcription_windows(200, 100, spans))

    def test_packed_request_crosses_join_but_restored_cues_do_not_cross_silence(self):
        folder = ROOT / "data/test-runs" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        source = folder / "audio.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00" * 100 + b"\x02\x00" * 100)
        frames = []

        async def request(parts, *args, **kwargs):
            self.assertIn('[1.0]', parts[0]['text'])
            self.assertIn('without resetting', parts[0]['text'])
            with wave.open(io.BytesIO(base64.b64decode(parts[1]["inlineData"]["data"]))) as chunk:
                frames.append(chunk.readframes(chunk.getnframes()))
            return [{"start": 0, "end": 2, "text": "Speech"}]

        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = "test-model"
        provider._request = request
        spans = build_timeline_from_kept_intervals([(0, 1), (21, 22)])
        check = Mock()
        segments = provider.transcribe(source, "en", check, spans=spans)
        self.assertEqual(frames, [b"\x01\x00" * 100 + b"\x02\x00" * 100])
        self.assertEqual([piece for s in segments for piece in project_segment_to_original(s.start, s.end, spans)],
            [(0, 1), (21, 22)])
        self.assertGreaterEqual(check.call_count, 1)

    def test_empty_audio_has_no_requests(self):
        self.assertEqual(list(transcription_windows(0, 100, [])), [])

    def test_legacy_partial_transcription_resumes_without_new_prompt(self):
        folder = ROOT / 'data/test-runs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        source = folder / 'audio.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes(63 * 200))
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = 'test-model'
        identity = ['packed-sentence-60-v2', 'test-model', transcription_prompt('en', 60),
            source.stat().st_size, source.stat().st_mtime_ns, [(0, 0, 6000), (6000, 6000, 6300)]]
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        work = folder / 'job/work'
        saved = [{'start': 1, 'end': 2, 'text': 'Saved'}]
        write_json_atomic(work / 'transcription' / (key + '.json'), {'count': 2, 'results': {'0': saved}})
        provider._request = AsyncMock(return_value=[{'start': 0, 'end': 1, 'text': 'Remaining'}])
        spans = build_timeline_from_kept_intervals([(0, 61), (70, 72)])
        result = provider.transcribe(source, 'en', lambda: None, spans=spans, work=work)
        self.assertEqual([s.text for s in result], ['Saved', 'Remaining'])
        self.assertEqual(provider._request.call_count, 1)
        self.assertEqual(provider._request.call_args.args[0][0]['text'], transcription_prompt('en', 3))
        provider._request.reset_mock()
        provider.transcribe(source, 'en', lambda: None, spans=spans, work=work)
        provider._request.assert_not_called()

    def test_new_splice_prompts_use_clip_clock_and_resume_v3(self):
        folder = ROOT / 'data/test-runs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        source = folder / 'audio.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
            audio.writeframes(bytes(63 * 200))
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = 'test-model'
        provider._request = AsyncMock(return_value=[])
        spans = build_timeline_from_kept_intervals([(0, 30), (40, 71), (90, 92)])
        work = folder / 'job/work'
        provider.transcribe(source, 'en', lambda: None, spans=iter(spans), work=work)
        prompts = [call.args[0][0]['text'] for call in provider._request.call_args_list]
        self.assertIn('[30.0]', prompts[0])
        self.assertIn('[1.0]', prompts[1])
        provider._request.reset_mock()
        provider.transcribe(source, 'en', lambda: None, spans=spans, work=work)
        provider._request.assert_not_called()

    def test_native_response_keeps_words_until_chunk_ownership(self):
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.key = "test-only"
        parts = [{"audioTranscription": {"words": [
            {"word": "Hello", "startOffset": "0.1s", "endOffset": "0.3s"},
            {"word": "world.", "startOffset": "0.4s", "endOffset": "0.9s"}]}}]
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = httpx.Response(200, json={"candidates": [{
            "finishReason": "STOP", "content": {"parts": parts}}]})
        with patch.object(provider, "_client", sdk_fixture(client.post)):
            words = asyncio.run(provider._request([], None, lambda: None, "test-model", {"wordTimestamp": True}))
        self.assertEqual([word["text"] for word in words], ["Hello", "world."])

    def test_sentence_clips_restore_time_without_duplicated_context(self):
        folder = ROOT / "data/test-runs" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        source = folder / "audio.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(63 * 100 * 2))
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = "test-model"
        first = [{"start": 58.8, "end": 59.8, "text": "We keep"}]
        second = [{"start": 0.1, "end": 0.9, "text": "every word."}]
        provider._request = AsyncMock(side_effect=[first, second])
        result = provider.transcribe(source, "en", lambda: None)
        self.assertEqual(provider._request.call_count, 2)
        self.assertEqual([segment.text for segment in result], ["We keep", "every word."])
        self.assertAlmostEqual(result[0].start, 58.8)
        self.assertAlmostEqual(result[1].start, 60.1)
        self.assertAlmostEqual(result[1].end, 60.9)

    def test_raw_words_keep_intentional_repetition(self):
        parts = [{"audioTranscription": {"words": [
            {"word": "very", "startOffset": "0s", "endOffset": "0.3s"},
            {"word": "very", "startOffset": "0.4s", "endOffset": "0.7s"},
            {"word": "good.", "startOffset": "0.8s", "endOffset": "1s"}]}}]
        self.assertEqual(parse_word_transcriptions(parts)[0]["text"], "very very good.")


if __name__ == "__main__":
    unittest.main()
