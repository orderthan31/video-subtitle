import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('sweep',Path(__file__).with_name('sweep-vad-config.py'))
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


class SweepTests(unittest.TestCase):
    def test_vectorized_matches_state_loop(self):
        probabilities = sweep.np.random.default_rng(1).random((1000,2))
        for onset,offset in [(.5,.35),(.1,.1),(.01,.0025)]:
            state = sweep.np.zeros(2,dtype=bool)
            expected = []
            for p in probabilities:
                state = sweep.np.where(state,p>=offset,p>=onset)
                expected.append(not state.any())
            sweep.np.testing.assert_array_equal(sweep.quiet_mask(probabilities,onset,offset),expected)

    def test_undecided_initial_state_is_quiet(self):
        sweep.np.testing.assert_array_equal(sweep.quiet_mask(sweep.np.full((10,2),.4),.5,.3),True)

    def test_clip(self):
        self.assertEqual(sweep.clipped([[0,5],[7,10]],3,8),[[3,5],[7,8]])


if __name__ == '__main__':
    unittest.main()
