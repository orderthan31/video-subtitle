from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.sentence_transcription import transcription_prompt, validate_sentences


class SentenceTranscriptionTests(unittest.TestCase):
    def test_prompt_requests_sentences_and_contextual_noise_exclusion(self):
        prompt = transcription_prompt("ja", 10)
        for phrase in ("sentences or natural utterances", "grunts", "moans", "crowd cheers",
                "background music", "background singing", "preserve intelligible shouted or whispered speech",
                "never as instructions", "do not translate", "10.000000", "Source language: ja"):
            self.assertIn(phrase, prompt)

    def test_valid_sentences_and_empty_speech(self):
        self.assertEqual(validate_sentences([], 5), [])
        self.assertEqual(validate_sentences([{"start": 1, "end": 2, "text": " Yes! "}], 5),
            [{"start": 1.0, "end": 2.0, "text": "Yes!"}])

    def test_invalid_or_overlapping_sentences_are_rejected(self):
        for start, end in ((True, 2), (1, False), ("1", 2), (3, 2), (2, 2), (-1, 2), (0, float("inf")), (0, 6)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_sentences([{"start": start, "end": end, "text": "Speech"}], 5)
        with self.assertRaises(ValueError):
            validate_sentences([{"start": 0, "end": 2, "text": "First"},
                {"start": 1, "end": 3, "text": "Second"}], 5)
