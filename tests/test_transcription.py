from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import parse_word_transcriptions


class TranscriptionTests(unittest.TestCase):
    def test_sentence_and_speaker_boundaries(self):
        parts = [{"audioTranscription": {"speakerLabel": "A", "words": [
            {"word": "Hello", "startOffset": "0.1s", "endOffset": "0.3s"},
            {"word": "world.", "startOffset": "0.4s", "endOffset": "0.9s"},
            {"word": "Welcome", "startOffset": "1s", "endOffset": "1.5s"}]}},
            {"audioTranscription": {"speakerLabel": "B", "words": [
                {"word": "Yes", "startOffset": "1.6s", "endOffset": "2s"}]}}]
        result = parse_word_transcriptions(parts)
        self.assertEqual([s["text"] for s in result], ["Hello world.", "Welcome", "Yes"])
        self.assertEqual(result[0]["start"], 0.1)
        self.assertEqual(result[0]["end"], 0.9)

    def test_text_without_timings_rejected(self):
        with self.assertRaises(ValueError):
            parse_word_transcriptions([{"text": "Hello"}])

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            parse_word_transcriptions([{"audioTranscription": {"words": [
                {"word": "Hello", "startOffset": "NaNs", "endOffset": "2s"}]}}])


if __name__ == "__main__":
    unittest.main()
