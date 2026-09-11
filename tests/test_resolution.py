from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.media import encoding_args
from media_worker.validation import output_dimensions, validate_output


class ResolutionTests(unittest.TestCase):
    def test_landscape_portrait_and_square_bounds(self):
        for width, height, preset, expected in (
            (3840, 2160, "1080p", (1920, 1080)),
            (1920, 1080, "720p", (1280, 720)),
            (2160, 3840, "720p", (720, 1280)),
            (1440, 1440, "720p", (720, 720)),
            (1920, 800, "720p", (1280, 532)),
            (640, 360, "1080p", (640, 360)),
            (641, 361, "720p", (640, 360)),
        ):
            with self.subTest(width=width, height=height, preset=preset):
                self.assertEqual(output_dimensions({"width": width, "height": height}, preset), expected)

    def test_rotation_applied_before_scaling(self):
        self.assertEqual(output_dimensions({"width": 1920, "height": 1080,
            "side_data_list": [{"rotation": 90}]}, "720p"), (720, 1280))
        with self.assertRaises(ValueError):
            output_dimensions({"width": 1920, "height": 1080, "tags": {"rotate": "45"}}, "720p")

    def test_scaling_precedes_burn_and_is_independent_of_codec(self):
        video = {"width": 1920, "height": 1080, "avg_frame_rate": "30/1"}
        for codec in ("hevc", "h264"):
            for mode in ("burn", "soft"):
                args = encoding_args("source", "output", video, video_codec=codec,
                    subtitle_mode=mode, resolution="720p")
                expected = "scale=1280:720:flags=lanczos"
                if mode == "burn":
                    expected += ",subtitles=translated.srt"
                self.assertEqual(args[args.index("-vf") + 1], expected)
        args = encoding_args("source", "output", video, subtitle_mode="soft")
        self.assertNotIn("-vf", args)

    def test_validator_requires_selected_resolution(self):
        source = {"duration": 10, "streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
        output = {"duration": 10, "streams": [
            {"codec_type": "video", "width": 1280, "height": 720, "codec_name": "h264",
             "codec_tag_string": "avc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"}]}
        validate_output(source, output, 100, "h264", resolution="720p")
        with self.assertRaisesRegex(ValueError, "resolution mismatch"):
            validate_output(source, output, 100, "h264")
