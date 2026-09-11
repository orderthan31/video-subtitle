"""Offline timestamp mapping tests. No model instantiation or network."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('poc',Path(__file__).with_name('compare-packed-transcription.py'))
poc=importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)


class MappingTests(unittest.TestCase):
    spans=[dict(processed_start=0,processed_end=10,original_start=100,original_end=110),
           dict(processed_start=10,processed_end=30,original_start=500,original_end=520)]

    def test_internal(self):
        self.assertEqual(poc.project(12,15,self.spans),[[502,505]])

    def test_discontinuous(self):
        self.assertEqual(poc.project(8,12,self.spans),[[108,110],[500,502]])

    def test_exact_boundary(self):
        self.assertEqual(poc.project(0,10,self.spans),[[100,110]])
        self.assertEqual(poc.project(10,30,self.spans),[[500,520]])

    def test_duration_preserved(self):
        pieces=poc.project(5,25,self.spans)
        self.assertEqual(sum(b-a for a,b in pieces),20)

    def test_repeated_text_does_not_match_distant_scene(self):
        before=[poc.TranscriptSegment(100,101,'yes')]
        after=[poc.TranscriptSegment(3000,3001,'yes')]
        self.assertEqual(poc.compare(before,after)['exact_normalized_sentences_in_order'],0)

    def test_local_matching_reports_timing(self):
        before=[poc.TranscriptSegment(100,101,'Hello!')]
        after=[poc.TranscriptSegment(100.2,101.3,'Hello')]
        result=poc.compare(before,after)
        self.assertEqual(result['exact_normalized_sentences_in_order'],1)
        self.assertAlmostEqual(result['matched_timing'][0]['start_delta'],.2)


if __name__=='__main__':
    unittest.main()
