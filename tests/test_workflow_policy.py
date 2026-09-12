from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/shared'))
from video_service.workflows import workflow_plan


class WorkflowPolicyTests(unittest.TestCase):
    def test_exact_stages(self):
        cases = {
            'extract_audio': ['analyze', 'extract_audio'],
            'transcribe': ['analyze', 'extract_audio', 'preprocess_audio', 'transcribe', 'generate_subtitle'],
            'transcribe_translate': ['analyze', 'extract_audio', 'preprocess_audio', 'transcribe', 'translate', 'generate_subtitle'],
            'full': ['analyze', 'extract_audio', 'preprocess_audio', 'transcribe', 'translate', 'generate_subtitle', 'encode', 'validate_video'],
        }
        for template, stages in cases.items():
            with self.subTest(template=template):
                self.assertEqual(workflow_plan(template)['stages'], stages)
        self.assertEqual(workflow_plan('translate', subtitle_input=True)['stages'],
                         ['analyze', 'translate', 'generate_subtitle'])
        self.assertEqual(workflow_plan('encode', subtitle_mode='none')['stages'],
                         ['analyze', 'encode', 'validate_video'])
        self.assertEqual(workflow_plan('encode', subtitle_input=True)['paid_stages'], [])

    def test_invalid_input_combinations(self):
        for template, options in [('translate', {}), ('encode', {}),
                ('encode', {'subtitle_mode': 'none', 'subtitle_input': True}),
                ('transcribe', {'subtitle_input': True}), ('extract_audio', {'audio_input': True}),
                ('full', {'audio_input': True, 'subtitle_input': True})]:
            with self.subTest(template=template, options=options), self.assertRaises(ValueError):
                workflow_plan(template, **options)

    def test_reuse_skips_only_selected_stages(self):
        plan = workflow_plan('full', subtitle_input=True)
        self.assertEqual(plan['stages'], ['analyze', 'translate', 'generate_subtitle', 'encode', 'validate_video'])
        self.assertEqual(plan['reused'], ['subtitle'])
        plan = workflow_plan('transcribe', audio_input=True)
        self.assertEqual(plan['stages'], ['analyze', 'preprocess_audio', 'transcribe', 'generate_subtitle'])
        self.assertEqual(plan['paid_stages'], ['transcribe'])


if __name__ == '__main__':
    unittest.main()
