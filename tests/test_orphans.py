from pathlib import Path
import os
import shutil
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.repository import FilesystemJobRepository
from video_service.locking import job_lock
from video_service.models import QualityProfile
from media_worker.cleanup import collect_orphans


class OrphanTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)

    def orphan(self, metadata=None):
        directory = self.root / uuid4().hex
        directory.mkdir()
        (directory / 'partial.bin').write_bytes(b'partial')
        if metadata is not None:
            (directory / 'job.json').write_text(metadata)
        for path in [*directory.iterdir(), directory]:
            os.utime(path, (1000, 1000))
        return directory

    def test_stale_missing_or_corrupt_metadata_removed(self):
        for metadata in (None, '{broken', 'null', '[]', '{}'):
            with self.subTest(metadata=metadata):
                directory = self.orphan(metadata)
                removed = collect_orphans(self.repo, grace_seconds=3600, now=10000)
                self.assertIn(directory.name, removed)
                self.assertFalse(directory.exists())

    def test_recent_child_preserves_directory(self):
        directory = self.orphan()
        os.utime(directory / 'partial.bin', (9900, 9900))
        self.assertEqual(collect_orphans(self.repo, grace_seconds=3600, now=10000), [])
        self.assertTrue(directory.exists())

    def test_execution_lock_preserves_stale_directory(self):
        directory = self.orphan()
        with job_lock(self.repo, directory.name, 'execution'):
            self.assertEqual(collect_orphans(self.repo, grace_seconds=3600, now=10000), [])
        self.assertTrue(directory.exists())

    def test_valid_job_and_unknown_directory_preserved(self):
        job = self.repo.create_job(original_filename='clip.mp4', expected_size=1,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        unknown = self.root / 'my-files'
        unknown.mkdir()
        self.assertEqual(collect_orphans(self.repo, now=10**12), [])
        self.assertTrue(self.repo.job_dir(job.job_id).exists())
        self.assertTrue(unknown.exists())


if __name__ == '__main__':
    unittest.main()
