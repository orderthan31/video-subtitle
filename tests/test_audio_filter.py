from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from media_worker.media import preprocess_audio
from video_service.transcript import TranscriptSegment, filter_transcript_segments
from video_service.timeline import map_segment_to_original


class AudioFilterTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / "data/test-runs" / uuid4().hex
        self.work.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.work)
        self.source = self.work / "source.wav"
        self.pcm = b"\x01\x00" * 20000
        with wave.open(str(self.source), "wb") as audio:
            audio.setparams((1, 2, 1000, 0, "NONE", "not compressed"))
            audio.writeframes(self.pcm)

    def test_off_preserves_every_sample_without_silence_detector(self):
        with patch("media_worker.media.run_process") as run:
            output, spans = preprocess_audio(self.source, self.work, audio_filter="off")
        run.assert_not_called()
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.readframes(audio.getnframes()), self.pcm)
        self.assertEqual(map_segment_to_original(3, 4, spans), (3, 4))

    def test_strong_removes_six_second_pause_but_conservative_preserves_it(self):
        log = self.work / "silence.log"
        log.write_text("silence_start: 2\nsilence_end: 8\n", encoding="utf-8")
        for mode, expected_frames, minimum in (("conservative", 20000, 10), ("strong", 14400, 5)):
            with patch("media_worker.media.run_process", return_value=log) as run:
                output, spans = preprocess_audio(self.source, self.work, audio_filter=mode)
            self.assertIn(f"silencedetect=noise=-45dB:d={minimum}", run.call_args.args[0])
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getnframes(), expected_frames)
            if mode == "strong":
                start, end = map_segment_to_original(3, 4, spans)
                self.assertAlmostEqual(start, 8.6)
                self.assertAlmostEqual(end, 9.6)

    def test_plain_interjections_are_preserved_at_every_strength(self):
        words = ["아", "어", "음", "핫", "Yes", "Ah!", "[breath]", ""]
        segments = [TranscriptSegment(i, i + 1, text) for i, text in enumerate(words)]
        for mode in ("off", "conservative", "strong"):
            actual = [s.text for s in filter_transcript_segments(segments, mode)]
            self.assertEqual(actual, words[:-1] if mode == "off" else words[:-2])

    def test_invalid_filter_is_rejected_before_processing(self):
        with self.assertRaises(ValueError):
            preprocess_audio(self.source, self.work, audio_filter="invalid")
        with self.assertRaises(ValueError):
            filter_transcript_segments([], "invalid")
