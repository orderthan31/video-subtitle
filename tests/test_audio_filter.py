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
        for mode in ("off", "conservative", "strong", "silence3"):
            actual = [s.text for s in filter_transcript_segments(segments, mode)]
            self.assertEqual(actual, words[:-1] if mode == "off" else words[:-2])

    def test_invalid_filter_is_rejected_before_processing(self):
        with self.assertRaises(ValueError):
            preprocess_audio(self.source, self.work, audio_filter="invalid")
        with self.assertRaises(ValueError):
            filter_transcript_segments([], "invalid")

    def test_vad_off_never_loads_ml(self):
        with patch("media_worker.nvidia_vad.detect_intervals") as detect:
            preprocess_audio(self.source, self.work, audio_filter="off")
        detect.assert_not_called()

    def test_vad_intersects_silence_in_original_clock(self):
        log = self.work / "silence.log"
        log.write_text("silence_start: 2\nsilence_end: 6\n", encoding="utf-8")
        with patch("media_worker.media.run_process", return_value=log), patch(
                "media_worker.nvidia_vad.detect_intervals", return_value=[(1, 3), (10, 12)]) as detect:
            output, spans = preprocess_audio(self.source, self.work, audio_filter="silence3", vad_mode="nvidia")
        self.assertEqual(detect.call_args.args[0], self.source)
        self.assertEqual([(s.original_start, s.original_end) for s in spans], [(1, 2.2), (10, 12)])
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 3200)
        self.assertEqual(self.source.stat().st_size, len(self.pcm) + 44)

    def test_vad_errors_do_not_silently_fall_back(self):
        with patch("media_worker.nvidia_vad.detect_intervals", side_effect=RuntimeError("missing model")):
            with self.assertRaisesRegex(RuntimeError, "missing model"):
                preprocess_audio(self.source, self.work, audio_filter="off", vad_mode="nvidia")

    def test_three_second_filter_removes_four_second_pause(self):
        log = self.work / "silence.log"
        log.write_text("silence_start: 2\nsilence_end: 6\n", encoding="utf-8")
        with patch("media_worker.media.run_process", return_value=log) as run:
            output, spans = preprocess_audio(self.source, self.work, audio_filter="silence3")
        self.assertIn("silencedetect=noise=-45dB:d=3", run.call_args.args[0])
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 16400)
        self.assertAlmostEqual(map_segment_to_original(3, 4, spans)[0], 6.6)
