from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packages/shared'), str(ROOT / 'workers/media')]
from media_worker.progress import encoding_progress
from media_worker.worker import Worker
from video_service.repository import FilesystemJobRepository
from video_service.models import JobStatus, QualityProfile


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.path = self.root / 'progress.txt'

    def test_latest_output_time_used(self):
        self.path.write_text('out_time_us=1000000\nprogress=continue\nout_time_us=5000000\nprogress=continue\n')
        self.assertEqual(encoding_progress(self.path, 10), 0.5)

    def test_missing_or_unknown_time(self):
        self.assertIsNone(encoding_progress(self.path, 10))
        self.path.write_text('out_time_us=N/A\n')
        self.assertIsNone(encoding_progress(self.path, 10))

    def test_progress_is_bounded(self):
        self.path.write_text('out_time_us=11000000\n')
        self.assertEqual(encoding_progress(self.path, 10), 1)
        self.assertIsNone(encoding_progress(self.path, 0))

    def test_state_transition_clears_previous_progress(self):
        repo = FilesystemJobRepository(self.root / 'jobs')
        record = repo.create_job(original_filename='x.mp4', expected_size=1,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        repo.update_status(record.job_id, JobStatus.ENCODING, metadata={'stage_progress': 0.8})
        Worker(repo, None).transition(record.job_id, JobStatus.VALIDATING)
        self.assertIsNone(repo.read(record.job_id).metadata['stage_progress'])


if __name__ == '__main__':
    unittest.main()
