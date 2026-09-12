from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/shared'))
from video_service.assets import AssetConflictError, AssetNotFoundError, VideoAssetRepository
from video_service.locking import JobBusyError, download_lock
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository


class VideoAssetTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.jobs = FilesystemJobRepository(self.root)
        self.assets = VideoAssetRepository(self.jobs)
        self.addCleanup(shutil.rmtree, self.root)
        self.job = self.jobs.create_job(original_filename='sample.mp4', expected_size=5,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED,
            metadata={'owner_id': 'alice'})
        self.jobs.source_path(self.job).write_bytes(b'video')
        self.jobs.update_status(self.job.job_id, JobStatus.COMPLETED)
        self.copy_sizes = []

    def register(self):
        return self.assets.register_upload(self.job.job_id, owner_id='alice',
                                           before_copy=self.copy_sizes.append)

    def promote(self):
        return self.assets.promote_result(self.job.job_id, owner_id='alice',
                                          before_copy=self.copy_sizes.append)

    def test_registration_preserves_job_artifacts_and_is_idempotent(self):
        first = self.register()
        self.assertEqual(first, self.register())
        self.assertEqual(self.copy_sizes, [4101])
        self.assertEqual(self.jobs.read(self.job.job_id).status, JobStatus.COMPLETED)
        self.assertEqual(self.jobs.source_path(self.job).read_bytes(), b'video')
        self.assertEqual(self.assets.source_path(first).read_bytes(), b'video')
        self.assertEqual(len(self.jobs.list()), 1)
        self.assertEqual(len(self.assets.list(owner_id='alice')), 1)

    def test_asset_survives_source_job_deletion(self):
        asset = self.register()
        self.jobs.delete_job_dir(self.job.job_id)
        self.assertEqual(self.assets.source_path(asset).read_bytes(), b'video')
        self.assets.delete(asset['asset_id'], owner_id='alice')
        self.assertEqual(self.assets.list(owner_id='alice'), [])

    def test_referenced_asset_deletion_is_rejected(self):
        asset = self.register()
        with self.assertRaises(AssetConflictError):
            self.assets.delete(asset['asset_id'], owner_id='alice')
        self.assertTrue(self.assets.source_path(asset).exists())

    def test_owner_and_path_isolation(self):
        asset = self.register()
        self.assertEqual(self.assets.list(owner_id='bob'), [])
        for owner in ['bob', None]:
            with self.assertRaises(AssetNotFoundError):
                self.assets.read(asset['asset_id'], owner_id=owner)
            with self.assertRaises(AssetNotFoundError):
                self.assets.register_upload(self.job.job_id, owner_id=owner, before_copy=lambda _: None)
        with self.assertRaises(AssetNotFoundError):
            self.assets.directory('../outside')
        with self.assertRaises(ValueError):
            self.assets.source_path({**asset, 'source_filename': '../source.mp4'})

    def test_uploading_and_incomplete_sources_rejected(self):
        self.jobs.update_status(self.job.job_id, JobStatus.UPLOADING)
        with self.assertRaises(AssetConflictError):
            self.register()
        self.jobs.update_status(self.job.job_id, JobStatus.READY)
        self.jobs.source_path(self.job).write_bytes(b'x')
        with self.assertRaises(AssetConflictError):
            self.register()
        self.assertEqual(self.copy_sizes, [])

    def test_capacity_failure_does_not_publish(self):
        with self.assertRaises(OSError):
            self.assets.register_upload(self.job.job_id, owner_id='alice',
                before_copy=lambda _: (_ for _ in ()).throw(OSError('full')))
        self.assertEqual(self.assets.list(owner_id='alice'), [])
        self.assertNotIn('asset_id', self.jobs.read(self.job.job_id).metadata)

    def test_manifest_publish_failure_is_retryable(self):
        with patch('video_service.assets.write_json_atomic', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.register()
        self.assertEqual(self.assets.list(owner_id='alice'), [])
        self.assertEqual(self.assets.source_path(self.register()).read_bytes(), b'video')

    def test_job_link_failure_reuses_published_asset(self):
        with patch.object(self.jobs, 'save', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.register()
        asset = self.assets.list(owner_id='alice')[0]
        self.assertEqual(self.register(), asset)
        self.assertEqual(len(self.copy_sizes), 1)

    def test_promotion_independent_idempotent_and_without_inherited_state(self):
        parent = self.register()
        (self.jobs.job_dir(self.job.job_id) / 'output/final.mp4').write_bytes(b'encoded')
        promoted = self.promote()
        self.assertEqual(self.promote(), promoted)
        self.assertNotEqual(promoted['asset_id'], parent['asset_id'])
        self.assertEqual(promoted['provenance']['parent_asset_id'], parent['asset_id'])
        self.assertNotIn('transcript', promoted)
        self.jobs.delete_job_dir(self.job.job_id)
        self.assets.delete(parent['asset_id'], owner_id='alice')
        self.assertEqual(self.assets.source_path(promoted).read_bytes(), b'encoded')

    def test_live_download_blocks_registration(self):
        with download_lock(self.jobs, self.job.job_id):
            with self.assertRaises(JobBusyError):
                self.register()

    def test_corrupt_job_manifest_fails_closed_on_delete(self):
        asset = self.register()
        self.jobs.job_file(self.job.job_id).write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.assets.delete(asset['asset_id'], owner_id='alice')
        self.assertTrue(self.assets.source_path(asset).exists())


if __name__ == '__main__':
    unittest.main()
