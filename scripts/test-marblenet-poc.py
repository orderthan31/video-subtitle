"""Window preparation checks against NVIDIA's own collate function; no model."""
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
import wave

spec = importlib.util.spec_from_file_location('poc',Path(__file__).with_name('poc-marblenet-cpu.py'))
poc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)
from nemo.collections.asr.data.audio_to_label import _vad_frame_seq_collate_fn


class WindowTests(unittest.TestCase):
    def test_windows_match_official_collate(self):
        samples = poc.np.random.default_rng(1).integers(-20000,20000,(32000,2),dtype=poc.np.int16)
        data = io.BytesIO()
        with wave.open(data,'wb') as target:
            target.setnchannels(2)
            target.setsampwidth(2)
            target.setframerate(16000)
            target.writeframes(samples.astype('<i2').tobytes())
        data.seek(0)
        with wave.open(data,'rb') as source:
            actual = poc.window_batch(source,poc.np.arange(0,32000,1280))
        mono = poc.torch.from_numpy(samples.astype(poc.np.float32).mean(axis=1)/32768)
        config = SimpleNamespace(featurizer=SimpleNamespace(sample_rate=16000),
                                 window_length_in_sec=.63,shift_length_in_sec=.08,normalize_audio=False)
        expected = _vad_frame_seq_collate_fn(config,[(mono,poc.torch.tensor(32000),poc.torch.tensor(0),poc.torch.tensor(1))])[0]
        poc.np.testing.assert_array_equal(actual,expected.numpy())


if __name__ == '__main__':
    unittest.main()
