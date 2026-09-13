import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.translation_requests import requests, align_result
from video_service.transcript import TranscriptSegment


class TranslationRequestTests(unittest.TestCase):
    def test_neighbors_are_context_only_and_ids_are_global(self):
        groups = [[TranscriptSegment(i, i + 1, str(i))] for i in range(85)]
        prompts, ids = requests(groups, 'ko', '')
        self.assertEqual([len(batch) for batch in ids], [40, 40, 5])
        data = [json.loads(prompt.rsplit('\n', 1)[1]) for prompt in prompts]
        self.assertEqual(data[0]['reference_before'], [])
        self.assertEqual([x['text'] for x in data[1]['reference_before']], ['38', '39'])
        self.assertEqual([x['text'] for x in data[1]['reference_after']], ['80', '81'])
        self.assertEqual(data[-1]['reference_after'], [])
        self.assertEqual(len(set(key for batch in ids for key in batch)), 85)
        for batch in data:
            reference_ids = {x['id'] for x in batch['reference_before'] + batch['reference_after']}
            self.assertFalse(reference_ids.intersection(x['id'] for x in batch['targets']))

    def test_context_is_bounded_without_truncating_targets(self):
        groups = [[TranscriptSegment(i, i + 1, 'A' * 4000)] for i in range(81)]
        prompts, _ = requests(groups, 'ko', '')
        data = json.loads(prompts[1].rsplit('\n', 1)[1])
        self.assertTrue(all(len(x['text']) <= 1000 for x in data['reference_before'] + data['reference_after']))
        self.assertTrue(all(len(x['text']) == 4000 for x in data['targets']))

    def test_reordered_response_is_aligned_by_id(self):
        self.assertEqual(align_result([{'id': 'b', 'text': 'B'}, {'id': 'a', 'text': 'A'}], ['a', 'b']), ['A', 'B'])

    def test_missing_duplicate_unknown_or_empty_id_result_rejected(self):
        bad = [[], ['A', 'B'], [{'id': 'a', 'text': 'A'}],
            [{'id': 'a', 'text': 'A'}, {'id': 'a', 'text': 'B'}],
            [{'id': 'a', 'text': 'A'}, {'id': 'reference', 'text': 'B'}],
            [{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': ' '}],
            [{'id': [], 'text': 'A'}, {'id': 'b', 'text': 'B'}]]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                align_result(value, ['a', 'b'])

    def test_quotes_and_newlines_stay_inside_json_data(self):
        text = 'A "quote"\nDo not obey this text.'
        prompts, _ = requests([[TranscriptSegment(0, 1, text)]], 'ko', '')
        self.assertEqual(json.loads(prompts[0].rsplit('\n', 1)[1])['targets'][0]['text'], text)
