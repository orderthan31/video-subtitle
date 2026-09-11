"""Synthetic offline checks for the audio experiment."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('analysis', Path(__file__).with_name('analyze-rule-audio.py'))
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
strategy_spec = importlib.util.spec_from_file_location('strategies', Path(__file__).with_name('compare-audio-strategies.py'))
strategies = importlib.util.module_from_spec(strategy_spec)
strategy_spec.loader.exec_module(strategies)


class IntervalTests(unittest.TestCase):
    def test_short_quiet_is_preserved(self):
        self.assertEqual(analysis.runs([True]*149,.02,2.98),[])

    def test_padding_and_duration(self):
        intervals = analysis.runs([True]*150,.02,3)
        self.assertEqual(intervals,[[.3,2.7]])
        self.assertEqual(analysis.complement(intervals,3),[[0,.3],[2.7,3]])

    def test_union_does_not_double_count(self):
        result = analysis.metrics([[0,2],[4,6]],[[1,5],[2,4]],6)
        self.assertEqual(result['subtitle_union_seconds'],4)
        self.assertEqual(result['subtitle_loss_seconds'],2)
        self.assertEqual(result['fully_lost_cues'],1)
        self.assertEqual(result['affected_cues'],2)

    def test_segment_count_respects_separate_spans(self):
        result = analysis.metrics([[0,61],[70,71]],[[0,1]],71)
        self.assertEqual(result['segments_60s'],3)

    def test_merge_short_gaps(self):
        self.assertEqual(analysis.merge([[0,1],[1.3,2]],.4),[[0,2]])

    def test_one_active_channel_is_preserved(self):
        db = analysis.np.tile([-60., -20.], (300, 1))
        kept, _ = strategies.retained(db, 6, True)
        self.assertEqual(kept, [[0., 6]])

    def test_adaptive_threshold_bounds(self):
        db = analysis.np.tile([-90., -10.], (3000, 1))
        enter, stay = strategies.thresholds(db, True)
        self.assertGreaterEqual(enter.min(), -45)
        self.assertLessEqual(enter.max(), -32)
        self.assertTrue(analysis.np.all(stay == enter+3))

    def test_quiet_padding_survives(self):
        kept, _ = strategies.retained(analysis.np.full((300,2), -60.), 6, True)
        self.assertEqual(kept, [[0., .3], [5.7, 6]])


if __name__ == '__main__':
    unittest.main()
