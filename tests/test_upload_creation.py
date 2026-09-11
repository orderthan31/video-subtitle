from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api")]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from video_service.models import JobStatus
from video_service.repository import FilesystemJobRepository
from video_service.locking import job_lock


class UploadCreationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.guard = Mock(spec=["assert_can_accept_upload"])
        for patcher in (patch.object(routes, "repository", self.repo), patch.object(routes, "storage_guard", self.guard)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.payload = {"filename": "movie.mp4", "size": 6, "request_id": str(uuid4())}

    def test_replay_preserves_job_and_checks_capacity_only_once(self):
        first = self.client.post("/api/uploads", json=self.payload)
        job_id = first.json()["job_id"]
        self.repo.update_upload_progress(job_id, 3)
        self.repo.update_status(job_id, JobStatus.QUEUED)
        replay = self.client.post("/api/uploads", json=self.payload)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json()["job_id"], job_id)
        self.assertEqual(replay.json()["status"], "QUEUED")
        self.assertEqual(replay.json()["uploaded_bytes"], 3)
        self.assertEqual(len(self.repo.list()), 1)
        self.guard.assert_can_accept_upload.assert_called_once_with(6)

    def test_changed_payload_conflicts_without_mutation(self):
        first = self.client.post("/api/uploads", json=self.payload).json()
        for changes in ({"filename": "other.mp4"}, {"size": 7}, {"source_language": "en"},
                        {"target_language": "ja"}, {"quality_profile": "high"}, {"video_codec": "h264"},
                        {"subtitle_mode": "soft"}, {"resolution": "720p"}, {"additional_languages": ["ja"]},
                        {"audio_filter": "off"}):
            response = self.client.post("/api/uploads", json={**self.payload, **changes})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.repo.list()), 1)
        self.assertEqual(self.repo.read(first["job_id"]).expected_size, 6)

    def test_new_repository_instance_recovers_idempotency(self):
        first = self.client.post("/api/uploads", json=self.payload).json()
        with patch.object(routes, "repository", FilesystemJobRepository(self.root)):
            second = self.client.post("/api/uploads", json=self.payload).json()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.repo.read(first["job_id"]).metadata["upload_request_id"], self.payload["request_id"])

    def test_optional_key_preserves_legacy_creation(self):
        payload = {"filename": "movie.mp4", "size": 6}
        first = self.client.post("/api/uploads", json=payload).json()
        second = self.client.post("/api/uploads", json=payload).json()
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.client.post("/api/uploads", json={**payload, "request_id": "invalid"}).status_code, 422)

    def test_codec_is_validated_persisted_and_returned(self):
        response = self.client.post("/api/uploads", json={**self.payload, "video_codec": "h264"})
        job_id = response.json()["job_id"]
        self.assertEqual(self.repo.read(job_id).options.video_codec, "h264")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").json()["video_codec"], "h264")
        self.assertEqual(self.client.post("/api/uploads", json={**self.payload, "video_codec": "av1"}).status_code, 422)

    def test_old_records_default_to_hevc(self):
        from video_service.models import JobOptions
        self.assertEqual(JobOptions.from_dict({}).video_codec, "hevc")
        with self.assertRaises(ValueError):
            JobOptions.from_dict({"video_codec": "av1"})

    def test_subtitle_mode_validation_and_persistence(self):
        from video_service.models import JobOptions
        self.assertEqual(JobOptions.from_dict({}).subtitle_mode, "burn")
        with self.assertRaises(ValueError):
            JobOptions.from_dict({"subtitle_mode": "unknown"})
        response = self.client.post("/api/uploads", json={**self.payload, "subtitle_mode": "soft"})
        self.assertEqual(response.status_code, 201)
        job_id = response.json()["job_id"]
        self.assertEqual(self.repo.read(job_id).options.subtitle_mode, "soft")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").json()["subtitle_mode"], "soft")
        self.assertEqual(self.client.post("/api/uploads", json={**self.payload, "subtitle_mode": "unknown"}).status_code, 422)

    def test_admission_lock_prevents_racing_creation(self):
        with job_lock(self.repo, "0" * 32):
            self.assertEqual(self.client.post("/api/uploads", json=self.payload).status_code, 409)
        first = self.client.post("/api/uploads", json=self.payload).json()
        second = self.client.post("/api/uploads", json=self.payload).json()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.repo.list()), 1)

    def test_resolution_validation_persistence_and_legacy_default(self):
        from video_service.models import JobOptions
        self.assertEqual(JobOptions.from_dict({}).resolution, "original")
        with self.assertRaises(ValueError):
            JobOptions.from_dict({"resolution": "invalid"})
        response = self.client.post("/api/uploads", json={**self.payload, "resolution": "720p"})
        self.assertEqual(response.status_code, 201)
        job_id = response.json()["job_id"]
        self.assertEqual(self.repo.read(job_id).options.resolution, "720p")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").json()["resolution"], "720p")
        self.assertEqual(self.client.post("/api/uploads", json={**self.payload, "resolution": "invalid"}).status_code, 422)

    def test_additional_languages_persist_and_reject_duplicates_or_paths(self):
        from video_service.models import JobOptions
        self.assertEqual(JobOptions.from_dict({}).additional_languages, [])
        payload = {**self.payload, "additional_languages": ["ja", "es"]}
        first = self.client.post("/api/uploads", json=payload).json()
        self.assertEqual(self.client.post("/api/uploads", json=payload).json()["job_id"], first["job_id"])
        self.assertEqual(self.repo.read(first["job_id"]).options.additional_languages, ["ja", "es"])
        self.assertEqual(self.client.get(f"/api/jobs/{first['job_id']}").json()["additional_languages"], ["ja", "es"])
        for invalid in (["ko"], ["ja", "ja"], ["en-US", "en-us"], ["../en"], ["en.srt"],
                        ["en", "ja", "es", "zh", "fr"], None, "ja"):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/api/uploads", json={**self.payload,
                    "additional_languages": invalid}).status_code, 422)

    def test_audio_filter_validated_persisted_and_returned(self):
        from video_service.models import JobOptions
        self.assertEqual(JobOptions.from_dict({}).audio_filter, "conservative")
        with self.assertRaises(ValueError):
            JobOptions.from_dict({"audio_filter": "invalid"})
        response = self.client.post("/api/uploads", json={**self.payload, "audio_filter": "strong"})
        self.assertEqual(response.status_code, 201)
        job_id = response.json()["job_id"]
        self.assertEqual(self.repo.read(job_id).options.audio_filter, "strong")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").json()["audio_filter"], "strong")
        self.assertEqual(self.client.post("/api/uploads", json={**self.payload, "audio_filter": "invalid"}).status_code, 422)
