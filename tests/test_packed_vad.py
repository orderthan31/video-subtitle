from pathlib import Path
import json
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from media_worker.nvidia_vad import retained_intervals, runtime, select_device, batch_size
from media_worker.packed_timeline import restore_segments
from video_service.timeline import build_timeline_from_kept_intervals, project_segment_to_original
from video_service.transcript import TranscriptSegment


class PackedVadTests(unittest.TestCase):
    def test_cpu_does_not_probe_cuda(self):
        torch = Mock()
        self.assertEqual(select_device(torch, "cpu"), "cpu")
        torch.cuda.is_available.assert_not_called()

    def test_explicit_cuda_fails_closed_and_auto_falls_back(self):
        torch = Mock()
        torch.cuda.is_available.return_value = False
        with self.assertRaisesRegex(RuntimeError, "CUDA unavailable"):
            select_device(torch, "cuda")
        self.assertEqual(select_device(torch, "auto"), "cpu")
        torch.cuda.is_available.return_value = True
        self.assertEqual(select_device(torch, "cuda"), "cuda")
        self.assertEqual(select_device(torch, "auto"), "cuda")
        with self.assertRaises(ValueError):
            select_device(torch, "gpu")

    def test_batch_size_is_bounded(self):
        for value in ("0", "257", "bad"):
            with patch.dict("os.environ", {"NVIDIA_VAD_BATCH_SIZE": value}), self.assertRaises(ValueError):
                batch_size()
        with patch.dict("os.environ", {"NVIDIA_VAD_BATCH_SIZE": "16"}):
            self.assertEqual(batch_size(), 16)

    def test_hysteresis_retains_weak_speech_after_onset(self):
        self.assertEqual(retained_intervals([.8] + [.2] * 49, 4), [(0, 4)])
        self.assertEqual(retained_intervals([.2] * 50, 4), [(0, .3), (3.7, 4)])

    def test_short_quiet_is_kept_and_long_quiet_padded(self):
        self.assertEqual(retained_intervals([0] * 7, .56), [(0, .56)])
        self.assertEqual(retained_intervals([0] * 25, 2), [(0, 2)])
        self.assertEqual(retained_intervals([1] * 25 + [0] * 50 + [1] * 25, 8), [(0, 2.3), (5.7, 8)])

    def test_invalid_probabilities_fail_closed(self):
        for values, duration in [([float("nan")], .08), ([2], .08), ([], 1), ([0], -1)]:
            with self.assertRaises(ValueError):
                retained_intervals(values, duration)

    def test_missing_model_is_explicit(self):
        with patch.dict("os.environ", {"NVIDIA_VAD_MODEL": str(ROOT / "data/nonexistent-vad-model")}):
            with self.assertRaisesRegex(RuntimeError, "NVIDIA_VAD_MODEL"):
                runtime()

    def test_exact_join_and_contiguous_spans(self):
        spans = build_timeline_from_kept_intervals([(0, 1), (21, 22), (22, 23)])
        self.assertEqual(project_segment_to_original(0, 1, spans), [(0, 1)])
        self.assertEqual(project_segment_to_original(1, 3, spans), [(21, 23)])
        self.assertEqual(project_segment_to_original(.5, 1.5, spans), [(.5, 1), (21, 21.5)])

    def test_split_text_policy_is_traced(self):
        work = ROOT / "data/test-runs" / uuid4().hex
        work.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, work)
        spans = build_timeline_from_kept_intervals([(0, 1), (21, 22)])
        segments = restore_segments([TranscriptSegment(.5, 1.5, "Hello.")], spans, work / "trace.json")
        self.assertEqual([(s.start, s.end, s.text) for s in segments], [(.5, 1, "Hello."), (21, 21.5, "Hello.")])
        self.assertEqual(len(json.loads((work / "trace.json").read_text())), 1)
