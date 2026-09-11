from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.media import kept_intervals, copy_audio_intervals, encoding_args, select_encoder
from media_worker.process import run_process, Cancelled


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / "data/test-runs" / uuid4().hex
        self.work.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.work)

    def test_short_silence_preserved(self):
        self.assertEqual(kept_intervals(["silence_start: 2", "silence_end: 5"], 20), [(0, 20)])

    def test_long_silence_with_padding(self):
        self.assertEqual(kept_intervals(["silence_start: 2", "silence_end: 15"], 20), [(0, 2.2), (14.8, 20)])

    def test_trailing_silence(self):
        self.assertEqual(kept_intervals(["silence_start: 2"], 20), [(0, 2.2), (19.8, 20)])

    def test_pcm_copy_and_mapping(self):
        source, target = self.work / "source.wav", self.work / "target.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 1000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00" * 10000)
        spans = copy_audio_intervals(source, target, [(0, 2), (8, 10)])
        with wave.open(str(target), "rb") as audio:
            self.assertEqual(audio.getnframes(), 4000)
        self.assertEqual(spans[1].processed_start, 2)
        self.assertEqual(spans[1].original_start, 8)

    def test_encoding_preserves_input_and_uses_single_filter(self):
        args = encoding_args("movie.mp4", "final.mp4", {"avg_frame_rate": "30000/1001"})
        self.assertIn("hevc_nvenc", args)
        self.assertEqual(args[args.index("-g") + 1], "60")
        self.assertNotIn("-r", args)
        self.assertEqual(args.count("-vf"), 1)

    def test_process_failure(self):
        with self.assertRaises(RuntimeError):
            run_process([sys.executable, "-c", "raise SystemExit(3)"], cwd=self.work, log_name="failed.log")

    def test_encoder_fallback_requires_configuration(self):
        with patch.dict("os.environ", {"VIDEO_ENCODER": "hevc_nvenc", "ALLOW_SOFTWARE_ENCODER_FALLBACK": "false"}), patch("media_worker.media.run_process", side_effect=RuntimeError("driver")):
            with self.assertRaises(RuntimeError):
                select_encoder(self.work)
        with patch.dict("os.environ", {"VIDEO_ENCODER": "hevc_nvenc", "ALLOW_SOFTWARE_ENCODER_FALLBACK": "true"}), patch("media_worker.media.run_process", side_effect=RuntimeError("driver")):
            self.assertEqual(select_encoder(self.work), "libx265")

    def test_encoder_preflight_uses_supported_frame_size(self):
        with patch.dict("os.environ", {"VIDEO_ENCODER": "hevc_nvenc"}), \
                patch("media_worker.media.run_process") as run:
            self.assertEqual(select_encoder(self.work), "hevc_nvenc")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-i") + 1], "color=size=640x360:rate=30")
        self.assertIn("hevc_nvenc", args)

    def test_process_cancel_terminates_child(self):
        def cancel():
            raise Cancelled()
        with self.assertRaises(Cancelled):
            run_process([sys.executable, "-c", "import time; time.sleep(60)"], cwd=self.work, log_name="cancel.log", check=cancel)


if __name__ == "__main__":
    unittest.main()
