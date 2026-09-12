from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/shared'))
from video_service.assets import AssetNotFoundError, VideoAssetRepository
from video_service.artifacts import SubtitleArtifactRepository
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.storage import read_json, write_json_atomic

SRT = '\ufeff1\n00:00:01,250 --> 00:00:02,750\nFirst line\nSecond line\n\n'


class SubtitleArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'data/test-runs' / uuid4().hex
        self.jobs = FilesystemJobRepository(self.root)
        self.assets = VideoAssetRepository(self.jobs)
        self.artifacts = SubtitleArtifactRepository(self.assets)
        self.addCleanup(shutil.rmtree, self.root)
        job = self.jobs.create_job(original_filename='v.mp4', expected_size=1,
            source_language='en', target_language='ko', quality_profile=QualityProfile.BALANCED)
        self.jobs.source_path(job).write_bytes(b'v')
        self.jobs.update_status(job.job_id, JobStatus.READY)
        self.asset = self.assets.register_upload(job.job_id, before_copy=lambda _: None)

    def attach(self, content=SRT):
        return self.artifacts.attach_srt(self.asset['asset_id'], filename='dialogue.srt',
                                        language='en', content=content, before_write=lambda _: None)

    def test_preserves_original_and_exact_timestamps(self):
        artifact = self.attach()
        self.assertEqual(artifact['original_srt'], SRT)
        self.assertEqual(artifact['cues'], [{'start': 1.25, 'end': 2.75, 'text': 'First line\nSecond line'}])
        listing = self.artifacts.list(self.asset['asset_id'])
        self.assertEqual(listing[0]['artifact_id'], artifact['artifact_id'])
        self.assertNotIn('cues', listing[0])

    def test_invalid_srt_and_out_of_range_rejected(self):
        for text in ['garbage', '1\n00:00:02,000 --> 00:00:01,000\nBad\n', '\n\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.attach(text)
        self.asset['media'] = {'duration': 1}
        write_json_atomic(self.assets.directory(self.asset['asset_id']) / 'asset.json', self.asset)
        with self.assertRaises(ValueError):
            self.attach()

    def test_asset_and_owner_scope(self):
        artifact = self.attach()
        with self.assertRaises(AssetNotFoundError):
            self.artifacts.read(uuid4().hex, artifact['artifact_id'])
        with self.assertRaises(AssetNotFoundError):
            self.artifacts.read(self.asset['asset_id'], artifact['artifact_id'], owner_id='other')

    def test_snapshot_independent_of_later_versions_and_source_deletion(self):
        artifact = self.attach()
        destination = self.root / 'snapshot.json'
        with self.assets.lock():
            manifest = self.artifacts.snapshot(self.asset['asset_id'], artifact['artifact_id'], destination,
                                               before_write=lambda _: None)
        self.attach(SRT.replace('First line', 'Changed'))
        self.artifacts._path(self.asset['asset_id'], artifact['artifact_id']).unlink()
        snapshot = read_json(destination)
        self.assertEqual(snapshot['sha256'], manifest['sha256'])
        self.assertIn('First line', snapshot['cues'][0]['text'])

    def test_corrupt_artifact_fails_integrity_check(self):
        artifact = self.attach()
        artifact['cues'][0]['text'] = 'tampered'
        write_json_atomic(self.artifacts._path(self.asset['asset_id'], artifact['artifact_id']), artifact)
        with self.assertRaises(ValueError):
            self.artifacts.read(self.asset['asset_id'], artifact['artifact_id'])


if __name__ == '__main__':
    unittest.main()
