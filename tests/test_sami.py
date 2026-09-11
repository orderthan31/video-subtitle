from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/shared"))
from video_service.sami import segments_to_sami
from video_service.transcript import TranscriptSegment


class SamiTests(unittest.TestCase):
    def test_millisecond_timing_gap_and_final_clear(self):
        text = segments_to_sami([TranscriptSegment(1.25, 2.5, "Hello"), TranscriptSegment(4, 5, "World")], "en")
        for value in (0, 1250, 2500, 4000, 5000):
            self.assertIn(f'<SYNC Start="{value}">', text)
        self.assertIn('Start="2500"><P Class="SUBTTL">&nbsp;</P>', text)
        self.assertIn("lang: en;", text)

    def test_touching_cues_have_one_event_and_keep_next_text(self):
        text = segments_to_sami([TranscriptSegment(0, 1, "First"), TranscriptSegment(1, 2, "Second")])
        self.assertEqual(text.count('Start="1000"'), 1)
        self.assertIn('Start="1000"><P Class="SUBTTL">Second', text)

    def test_markup_is_escaped_and_linebreaks_preserved(self):
        text = segments_to_sami([TranscriptSegment(0, 1, "<script>& hello\nSecond line")])
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;&amp; hello<BR>Second line", text)

    def test_invalid_intervals_and_language_rejected(self):
        for cues in ([TranscriptSegment(0, 0.0001, "tiny")], [TranscriptSegment(-1, 1, "bad")],
                     [TranscriptSegment(0, 2, "first"), TranscriptSegment(1, 3, "overlap")]):
            with self.assertRaises(ValueError):
                segments_to_sami(cues)
        with self.assertRaises(ValueError):
            segments_to_sami([], "ko;} bad")
