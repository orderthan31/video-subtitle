from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/shared"))
from video_service.subtitles import segment_subtitles, segments_to_srt
from video_service.transcript import TranscriptSegment


class SubtitleLayoutTests(unittest.TestCase):
    def test_long_text_preserved_and_wrapped(self):
        text = "A subtitle contains several words and needs enough room for a reader. " * 5
        cues = segment_subtitles([TranscriptSegment(1, 20, text)], line_width=30)
        self.assertGreater(len(cues), 1)
        self.assertEqual(" ".join(" ".join(c.text.split()) for c in cues), text.strip())
        self.assertEqual(cues[0].start, 1)
        self.assertEqual(cues[-1].end, 20)
        for cue in cues:
            self.assertLessEqual(len(cue.text.splitlines()), 2)
            self.assertTrue(all(len(line) <= 30 for line in cue.text.splitlines()))
        self.assertTrue(all(a.end <= b.start for a, b in zip(cues, cues[1:])))

    def test_cjk_without_spaces_keeps_every_character(self):
        text = "字幕翻訳動画" * 30
        cues = segment_subtitles([TranscriptSegment(0, 10, text)], line_width=24)
        self.assertEqual("".join(c.text.replace("\n", "") for c in cues), text)

    def test_overlapping_dialogue_keeps_both_texts(self):
        cues = segment_subtitles([TranscriptSegment(2, 5, "Second"), TranscriptSegment(1, 3, "First")])
        self.assertEqual([(c.start, c.end, c.text) for c in cues],
                         [(1, 2, 'First'), (2, 3, 'First\nSecond'), (3, 5, 'Second')])

    def test_nested_overlap_and_touching_boundaries(self):
        cues = segment_subtitles([TranscriptSegment(0, 5, 'A'),
            TranscriptSegment(1, 2, 'B'), TranscriptSegment(2, 3, 'C')])
        self.assertEqual([(c.start, c.end, c.text) for c in cues],
            [(0, 1, 'A'), (1, 2, 'A\nB'), (2, 3, 'A\nC'), (3, 5, 'A')])

    def test_real_overlap_boundaries_and_repeat_layout(self):
        cues = segment_subtitles([TranscriptSegment(54.34, 57.17, 'First'),
                                  TranscriptSegment(56.45, 57.5, 'Second')])
        self.assertEqual([(c.start, c.end, c.text) for c in cues],
            [(54.34, 56.45, 'First'), (56.45, 57.17, 'First\nSecond'), (57.17, 57.5, 'Second')])
        self.assertEqual([c.to_dict() for c in segment_subtitles(cues)], [c.to_dict() for c in cues])

    def test_invalid_times_rejected(self):
        for start, end in [(float('nan'), 1), (0, float('inf')), (-1, 2), (2, 1)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                segment_subtitles([TranscriptSegment(start, end, "Text")])

    def test_submillisecond_cue_rejected(self):
        with self.assertRaises(ValueError):
            segments_to_srt([TranscriptSegment(1, 1.0001, "Text")])


if __name__ == "__main__":
    unittest.main()
