from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.validation import validate_cues, validate_output, display_dimensions, validate_decodable
from video_service.transcript import TranscriptSegment


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.source = {"duration": 10, "streams": [{"codec_type": "video", "width": 640, "height": 360}]}
        self.output = {"duration": 10, "streams": [
            {"codec_type": "video", "width": 640, "height": 360,
             "codec_name": "hevc", "codec_tag_string": "hvc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"}]}

    def test_expected_output_passes(self):
        validate_output(self.source, self.output, 100)

    def test_soft_output_requires_single_default_mov_text_track(self):
        with self.assertRaises(ValueError):
            validate_output(self.source, self.output, 100, subtitle_mode="soft")
        subtitle = {"codec_type": "subtitle", "codec_name": "mov_text", "disposition": {"default": 1}}
        self.output["streams"].append(subtitle)
        validate_output(self.source, self.output, 100, subtitle_mode="soft")
        with self.assertRaises(ValueError):
            validate_output(self.source, self.output, 100)
        for change in ({"codec_name": "ass"}, {"disposition": {"default": 0}}):
            output = deepcopy(self.output)
            output["streams"][-1].update(change)
            with self.assertRaises(ValueError):
                validate_output(self.source, output, 100, subtitle_mode="soft")
        self.output["streams"].append(subtitle)
        with self.assertRaises(ValueError):
            validate_output(self.source, self.output, 100, subtitle_mode="soft")

    def test_soft_encoding_maps_only_translated_subtitles_without_burning(self):
        from media_worker.media import encoding_args
        for codec in ("hevc", "h264"):
            args = encoding_args("source.mp4", "final.mp4", {"avg_frame_rate": "30/1"},
                video_codec=codec, subtitle_mode="soft", target_language="ko")
            self.assertNotIn("-vf", args)
            self.assertEqual(args.count("-i"), 2)
            self.assertIn("1:s:0", args)
            self.assertEqual(args[args.index("-c:s") + 1], "mov_text")
            self.assertIn("language=kor", args)
            self.assertNotIn("-shortest", args)
        with self.assertRaises(ValueError):
            encoding_args("source", "output", {"avg_frame_rate": "30/1"}, subtitle_mode="unknown")

    def test_requested_h264_requires_h264_and_avc1(self):
        self.output["streams"][0].update(codec_name="h264", codec_tag_string="avc1")
        validate_output(self.source, self.output, 100, "h264")
        with self.assertRaises(ValueError):
            validate_output(self.source, self.output, 100, "hevc")

    def test_decode_validation_propagates_failure_and_cancellation_check(self):
        check = lambda: None
        with patch("media_worker.process.run_process", side_effect=RuntimeError("corrupt frame")) as run:
            with self.assertRaisesRegex(RuntimeError, "corrupt frame"):
                validate_decodable("final.mp4", Path("work"), check)
        self.assertIn("-xerror", run.call_args.args[0])
        self.assertIs(run.call_args.kwargs["check"], check)

    def test_wrong_codec_tag_pixel_format_and_geometry_fail(self):
        for change in ({"codec_name": "h264"}, {"codec_tag_string": "hev1"},
                       {"pix_fmt": "yuv420p10le"}, {"width": 320}, {"height": 0}):
            output = deepcopy(self.output)
            output["streams"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_output(self.source, output, 100)

    def test_audio_empty_output_and_duration_fail(self):
        self.output["streams"][1]["codec_name"] = "opus"
        with self.assertRaises(ValueError):
            validate_output(self.source, self.output, 100)
        self.output["streams"][1]["codec_name"] = "aac"
        with self.assertRaises(ValueError):
            validate_output(self.source, self.output, 0)
        self.output["duration"] = 8
        with self.assertRaisesRegex(ValueError, "duration mismatch"):
            validate_output(self.source, self.output, 100)

    def test_rotated_source_expects_transposed_output(self):
        self.source["streams"][0]["side_data_list"] = [{"rotation": -90}]
        self.output["streams"][0].update(width=360, height=640)
        validate_output(self.source, self.output, 100)
        self.assertEqual(display_dimensions({"width": 640, "height": 360, "tags": {"rotate": "90"}}), (360, 640))

    def test_cues_stay_inside_media(self):
        validate_cues([TranscriptSegment(0, 1, "Hello"), TranscriptSegment(1, 2, "World")], 2)
        for cue in (TranscriptSegment(-1, 1, "Hello"), TranscriptSegment(0, 3, "Hello"),
                    TranscriptSegment(float("nan"), 1, "Hello"), TranscriptSegment(1, 1, "Hello")):
            with self.assertRaises(ValueError):
                validate_cues([cue], 2)

    def test_overlaps_empty_and_excessive_lines_fail(self):
        for cues in ([], [TranscriptSegment(0, 1, "")], [TranscriptSegment(0, 1, "a\nb\nc")],
                     [TranscriptSegment(0, 2, "Hello"), TranscriptSegment(1, 3, "World")]):
            with self.assertRaises(ValueError):
                validate_cues(cues, 3)
