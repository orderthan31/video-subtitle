from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/shared'))
from video_service.thumbnails import candidate_times, frame_score, generate_thumbnail, FILENAME


class ThumbnailTests(unittest.TestCase):
    def test_sampling_scales_to_duration_and_avoids_endpoints(self):
        self.assertEqual(candidate_times(100), [10, 35, 60])
        self.assertTrue(all(0 < x < .01 for x in candidate_times(.01)))
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                candidate_times(value)

    def test_nonblank_frame_beats_black_and_white(self):
        black, white = bytes([0, 0, 0]) * 100, bytes([255, 255, 255]) * 100
        textured = bytes([50, 80, 90]) * 48 + bytes([160, 180, 140]) * 52
        self.assertGreater(frame_score(textured), frame_score(black))
        self.assertGreater(frame_score(textured), frame_score(white))

    def test_failure_is_nonfatal_and_existing_poster_is_not_regenerated(self):
        directory = ROOT / 'data/test-runs' / uuid4().hex
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory)
        with patch('video_service.thumbnails.subprocess.run', side_effect=OSError('unavailable')) as run:
            self.assertIsNone(generate_thumbnail(directory / 'source.mp4', directory, 10))
            self.assertEqual(run.call_count, 3)
            (directory / FILENAME).write_bytes(b'existing')
            run.reset_mock()
            self.assertEqual(generate_thumbnail(directory / 'source.mp4', directory, 10), directory / FILENAME)
            run.assert_not_called()
