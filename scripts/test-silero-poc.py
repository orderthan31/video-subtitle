"""Synthetic postprocessing tests. No model inference or network."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('poc',Path(__file__).with_name('poc-silero-cpu.py'))
poc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)


class VadTests(unittest.TestCase):
    def test_one_channel_preserves_speech(self):
        data = poc.np.tile([.9,.01],(200,1))
        self.assertEqual(poc.retained(data,6.4,.5),[[0.,6.4]])

    def test_short_pause_preserved(self):
        self.assertEqual(poc.retained(poc.np.zeros((90,2)),2.88,.5),[[0.,2.88]])

    def test_padding_and_partial_frame(self):
        kept = poc.retained(poc.np.zeros((200,2)),6.39,.5)
        self.assertEqual(kept,[[0.,.3],[6.09,6.39]])

    def test_short_speech_is_not_discarded(self):
        data = poc.np.zeros((400,2))
        data[200,0] = .9
        kept = poc.retained(data,12.8,.5)
        self.assertTrue(any(a <= 6.4 and b >= 6.432 for a,b in kept))

    def test_hysteresis(self):
        data = poc.np.full((200,2),.4)
        data[0] = .8
        self.assertEqual(poc.retained(data,6.4,.5),[[0.,6.4]])


if __name__ == '__main__':
    unittest.main()
