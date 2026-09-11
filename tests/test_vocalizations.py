import base64
import io
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from media_worker.media import preprocess_audio
from media_worker.process import Cancelled
from media_worker.vocalizations import detect_vocalizations, validated_events, candidates_without_speech, subtract_intervals, exclude_protected
from video_service.timeline import map_segment_to_original


def event(start, end, kind="breath", certain=True):
    return {"start": start, "end": end, "kind": kind, "certain": certain}


class VocalizationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.audio = self.root / "source.wav"
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 16000 * 4)
        self.provider = Mock(audio_filter_model="gemini-3.8-flash")

    def test_ambiguous_and_adjacent_speech_veto_removal(self):
        for other in (event(2.1, 3, "speech"), event(1.5, 3, "uncertain"), event(1, 2, "grunt", False)):
            self.assertEqual(candidates_without_speech([event(1, 2), other], "conservative"), [])
        self.assertEqual(candidates_without_speech([event(1, 1.5)], "conservative"), [])
        self.assertEqual(candidates_without_speech([event(1, 2, "speech")], "strong"), [])
        self.assertEqual(candidates_without_speech([event(1, 2)], "conservative"), [event(1.3, 1.7)])

    def test_untrusted_event_shapes_rejected_before_cuts(self):
        for value in (None, {}, [None], [event(-1, 2)], [event(1, float("nan"))], [event(True, 2)],
                      [event(2, 1)], [event(1, 5)], [event(1, 2, "noise")], [event(1, 2, certain="true")]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validated_events(value, 4)
        self.assertEqual(validated_events([event(1, 2)], 4, 30), [event(31, 32)])

    def test_independent_confirmation_uses_bounded_context_and_exact_candidate(self):
        self.provider.request.side_effect = [[event(1, 2)], {"kind": "breath", "safe_to_remove": True, "speech_overlap": False}]
        result = detect_vocalizations(self.provider, self.audio, "en", lambda: None)
        self.assertEqual(result, {"removals": [{"start": 1.3, "end": 1.7, "kind": "breath"}], "protected": []})
        first, second = self.provider.request.call_args_list
        self.assertEqual(first.args[3], "gemini-3.8-flash")
        clips = second.args[0][1:]
        durations = []
        for part in clips:
            with wave.open(io.BytesIO(base64.b64decode(part["inlineData"]["data"]))) as clip:
                durations.append(clip.getnframes() / clip.getframerate())
        self.assertEqual(durations, [3.7, 0.4])

    def test_confirmation_disagreement_or_any_speech_preserves_audio(self):
        for decision in ({"kind": "speech", "safe_to_remove": True, "speech_overlap": False},
                         {"kind": "breath", "safe_to_remove": False, "speech_overlap": False},
                         {"kind": "breath", "safe_to_remove": True, "speech_overlap": True},
                         {"kind": "grunt", "safe_to_remove": True, "speech_overlap": False}):
            self.provider.request.side_effect = [[event(1, 2)], decision]
            result = detect_vocalizations(self.provider, self.audio, "auto", lambda: None)
            self.assertEqual(result["removals"], [])
            self.assertEqual(result["protected"], [{"start": 1.3, "end": 1.7}])
        self.provider.request.side_effect = [[event(1, 2)], {"kind": "breath", "safe_to_remove": "true", "speech_overlap": False}]
        with self.assertRaises(ValueError):
            detect_vocalizations(self.provider, self.audio, "en", lambda: None)

    def test_off_and_cancellation_never_call_remote_model(self):
        self.assertEqual(detect_vocalizations(self.provider, Path("missing.wav"), "en", lambda: None, "off"), {"removals": [], "protected": []})
        with self.assertRaises(Cancelled):
            detect_vocalizations(self.provider, self.audio, "en", Mock(side_effect=Cancelled()))
        self.provider.request.assert_not_called()

    def test_original_clock_union_with_silence_and_overlapping_removals(self):
        result = subtract_intervals([(0, 2), (12, 20)], [event(1, 1.5), event(13, 15), event(14, 16)], 20)
        self.assertEqual(result, [(0, 1), (1.5, 2), (12, 13), (16, 20)])
        for item in (event(-1, 2), event(1, float("inf")), event(False, 2)):
            with self.assertRaises(ValueError):
                subtract_intervals([(0, 20)], [item], 20)

    def test_pcm_samples_and_original_timestamp_mapping_after_removal(self):
        log = self.root / "silence.log"
        log.write_text("", encoding="utf-8")
        with patch("media_worker.media.run_process", return_value=log):
            output, spans = preprocess_audio(self.audio, self.root, vocalizations=[event(1, 2)])
        with wave.open(str(output), "rb") as clip:
            self.assertEqual(clip.getnframes(), 48000)
            self.assertEqual(clip.readframes(48000), b"\x01\x00" * 48000)
        self.assertEqual(map_segment_to_original(1, 2, spans), (2, 3))
        with patch("media_worker.media.run_process") as run:
            _, spans = preprocess_audio(self.audio, self.root, audio_filter="off", vocalizations=[event(1, 2)])
        run.assert_not_called()
        self.assertEqual(map_segment_to_original(1, 2, spans), (1, 2))

    def test_neighbor_window_speech_vetoes_boundary_candidate(self):
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00\x00" * 16000 * 34)
        self.provider.request.side_effect = [[event(29, 30)], [event(1, 2, "speech")]]
        result = detect_vocalizations(self.provider, self.audio, "en", lambda: None)
        self.assertEqual(result["removals"], [])
        self.assertEqual(result["protected"], [{"start": 29, "end": 30}])
        self.assertEqual(self.provider.request.call_count, 2)

    def test_detected_quiet_speech_overrides_amplitude_based_silence(self):
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 16000 * 20)
        log = self.root / "silence.log"
        log.write_text("silence_start: 0\nsilence_end: 20\n", encoding="utf-8")
        with patch("media_worker.media.run_process", return_value=log):
            _, spans = preprocess_audio(self.audio, self.root, protected_audio=[event(2, 18, "speech")])
        self.assertTrue(any(s.original_start <= 2 and s.original_end >= 18 for s in spans))
        self.assertAlmostEqual(sum(s.original_duration for s in spans), 17)

    def test_later_confirmation_veto_retracts_an_overlapping_accepted_candidate(self):
        candidates = [event(1, 2), event(1.2, 2.2)]
        decisions = [{"kind": "breath", "safe_to_remove": True, "speech_overlap": False},
            {"kind": "speech", "safe_to_remove": False, "speech_overlap": True}]
        for reverse in (False, True):
            self.provider.request.side_effect = [list(reversed(candidates)) if reverse else candidates,
                *(list(reversed(decisions)) if reverse else decisions)]
            result = detect_vocalizations(self.provider, self.audio, "en", lambda: None)
            self.assertEqual(result["removals"], [])
            self.assertEqual(len(result["protected"]), 1)
        self.assertEqual(exclude_protected([event(1, 2), event(3.2, 3.8)], [event(1.5, 2.5)], 4), [event(3.2, 3.8)])

    def test_pcm_layer_preserves_protected_audio_even_with_conflicting_removal(self):
        log = self.root / "silence.log"
        log.write_text("", encoding="utf-8")
        with patch("media_worker.media.run_process", return_value=log):
            output, spans = preprocess_audio(self.audio, self.root,
                vocalizations=[event(1, 2)], protected_audio=[event(1.5, 2.5, "speech")])
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 64000)
        self.assertEqual(map_segment_to_original(1, 2, spans), (1, 2))


if __name__ == "__main__":
    unittest.main()
