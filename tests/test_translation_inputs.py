from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'workers/media'), str(ROOT / 'packages/shared')]
from media_worker.translation_inputs import translation_groups
from video_service.transcript import TranscriptSegment as Cue


class TranslationInputTests(unittest.TestCase):
    def test_close_exact_duplicates_share_translation_not_timestamps(self):
        cues = [Cue(1, 2, 'Hello'), Cue(2.1, 3, 'Hello'), Cue(3, 4, 'Other')]
        groups = translation_groups(cues)
        self.assertEqual([len(g) for g in groups], [2, 1])
        self.assertEqual([cue for group in groups for cue in group], cues)

    def test_wide_gap_requires_exact_restoration_provenance(self):
        cues = [Cue(1, 2, 'Hello'), Cue(32, 32.05, 'Hello')]
        self.assertEqual(len(translation_groups(cues)), 2)
        crossing = {'segment': {'text': 'Hello'}, 'original_intervals': [[1, 2], [32, 32.05]]}
        self.assertEqual(len(translation_groups(cues, [crossing])), 1)
        crossing['segment']['text'] = 'Unrelated'
        self.assertEqual(len(translation_groups(cues, [crossing])), 2)

    def test_do_not_share_nonadjacent_different_or_overlapping_cues(self):
        for cues in ([Cue(0, 1, 'A'), Cue(1, 2, 'B'), Cue(2, 3, 'A')],
                     [Cue(0, 1, 'Hello'), Cue(1, 2, 'hello')],
                     [Cue(0, 2, 'A'), Cue(1, 3, 'A')],
                     [Cue(0, 1, 'Hello.'), Cue(1, 2, 'Hello')]):
            self.assertEqual(len(translation_groups(cues)), len(cues))

    def test_repeated_phrase_chain_is_bounded(self):
        cues = [Cue(i, i + 1, 'A') for i in range(20)]
        self.assertEqual([len(g) for g in translation_groups(cues)], [8, 8, 4])
        self.assertEqual(translation_groups([]), [])

    def test_touching_float_boundaries_are_tolerated(self):
        self.assertEqual(len(translation_groups([Cue(0, 1.000000000001, 'A'), Cue(1, 2, 'A')])), 1)
