from pathlib import Path
import sys
import unittest
import base64
import io
import shutil
import uuid
import wave
import asyncio
import httpx
from unittest.mock import Mock, AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider, parse_word_transcriptions, transcription_windows
from video_service.timeline import build_timeline_from_kept_intervals, map_segment_to_original


class TranscriptionTests(unittest.TestCase):
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

    def test_join_requests_and_restored_cues_do_not_cross_silence(self):
        folder = ROOT / "data/test-runs" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        source = folder / "audio.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00" * 100 + b"\x02\x00" * 100)
        frames = []

        def request(parts, *args):
            with wave.open(io.BytesIO(base64.b64decode(parts[0]["inlineData"]["data"]))) as chunk:
                frames.append(chunk.readframes(chunk.getnframes()))
            # Model rounding can extend slightly past the submitted audio.
            return [{"start": 0, "end": 1.05, "text": "Speech"}]

        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = "test-model"
        provider.request = request
        spans = build_timeline_from_kept_intervals([(0, 1), (21, 22)])
        check = Mock()
        segments = provider.transcribe(source, "en", check, spans=spans)
        self.assertEqual(frames, [b"\x01\x00" * 100, b"\x02\x00" * 100])
        self.assertEqual([map_segment_to_original(s.start, s.end, spans) for s in segments],
            [(0, 1), (21, 22)])
        self.assertEqual(check.call_count, 2)

    def test_empty_audio_has_no_requests(self):
        self.assertEqual(list(transcription_windows(0, 100, [])), [])

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
        with patch("media_worker.providers.httpx.AsyncClient", return_value=client):
            words = asyncio.run(provider._request([], None, lambda: None, "test-model", {"wordTimestamp": True}))
        self.assertEqual([word["text"] for word in words], ["Hello", "world."])

    def test_sentence_crossing_sixty_seconds_keeps_each_word_once(self):
        folder = ROOT / "data/test-runs" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, folder)
        source = folder / "audio.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(63 * 100 * 2))
        provider = GeminiProvider.__new__(GeminiProvider)
        provider.transcription_model = "test-model"
        # The second request starts at 59s and repeats context from the first.
        first = [{"start": 58.8, "end": 59.2, "text": "We"},
            {"start": 59.4, "end": 59.8, "text": "keep"},
            {"start": 60.1, "end": 60.4, "text": "every"},
            {"start": 60.6, "end": 60.9, "text": "word."}]
        second = [{**word, "start": word["start"] - 59, "end": word["end"] - 59} for word in first[1:]]
        provider.request = Mock(side_effect=[first, second])
        result = provider.transcribe(source, "en", lambda: None)
        self.assertEqual(provider.request.call_count, 2)
        self.assertEqual([segment.text for segment in result], ["We keep every word."])
        self.assertAlmostEqual(result[0].start, 58.8)
        self.assertAlmostEqual(result[0].end, 60.9)

    def test_raw_words_keep_intentional_repetition(self):
        parts = [{"audioTranscription": {"words": [
            {"word": "very", "startOffset": "0s", "endOffset": "0.3s"},
            {"word": "very", "startOffset": "0.4s", "endOffset": "0.7s"},
            {"word": "good.", "startOffset": "0.8s", "endOffset": "1s"}]}}]
        self.assertEqual(parse_word_transcriptions(parts)[0]["text"], "very very good.")


if __name__ == "__main__":
    unittest.main()
