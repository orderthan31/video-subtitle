from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media"), str(ROOT / "apps/api")]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from media_worker.worker import Worker
from video_service.models import JobStatus, QualityProfile, ACTIVE_STATUSES
from video_service.repository import FilesystemJobRepository
from video_service.review import read_draft, write_draft
from video_service.timeline import build_timeline_from_kept_intervals
from video_service.transcript import TranscriptSegment
from video_service.locking import job_lock


class SubtitleReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.record = self.repo.create_job(original_filename="review.mp4", expected_size=1,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED, review_subtitles=True)
        self.job = self.record.job_id
        self.repo.source_path(self.record).write_bytes(b"x")
        self.tracks = {name: [{"start": 0, "end": 1, "text": name}] for name in ("original", "translated")}
        write_draft(self.repo, self.record, self.tracks, 3, 1)
        self.repo.update_status(self.job, JobStatus.AWAITING_REVIEW, metadata={"awaiting_review_at": "2026-09-11T00:00:00+00:00"})
        p = patch.object(routes, "repository", self.repo)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app)
        self.url = f"/api/jobs/{self.job}/subtitles"
        self.worker = Worker(self.repo, None)

    def test_versioned_save_rejects_lost_update_and_render_queues(self):
        self.assertEqual(self.client.get(self.url).json()["revision"], 1)
        tracks = deepcopy(self.tracks)
        tracks["translated"][0]["text"] = "Edited"
        saved = self.client.put(self.url, json={"revision": 1, "tracks": tracks})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["revision"], 2)
        self.assertEqual(self.client.put(self.url, json={"revision": 1, "tracks": self.tracks}).status_code, 409)
        self.assertEqual(read_draft(self.repo, self.repo.read(self.job))["tracks"]["translated"][0]["text"], "Edited")
        rendered = self.client.put(self.url, json={"revision": 2, "tracks": tracks, "action": "render"})
        self.assertEqual(rendered.status_code, 200)
        self.assertEqual(self.repo.read(self.job).status, JobStatus.QUEUED)
        self.assertTrue(self.repo.read(self.job).metadata["review_ready"])
        self.assertEqual(self.client.get(self.url).status_code, 409)

    def test_invalid_cues_do_not_change_revision_or_files(self):
        for cue in ({"start": 2, "end": 1, "text": "x"}, {"start": 0, "end": 4, "text": "x"},
                    {"start": 0, "end": 1, "text": " "}, {"start": 0, "end": 1, "text": "a\nb\nc"},
                    {"start": 0, "end": 0.00001, "text": "x"}):
            tracks = deepcopy(self.tracks)
            tracks["translated"] = [cue]
            self.assertEqual(self.client.put(self.url, json={"revision": 1, "tracks": tracks}).status_code, 422)
        tracks = deepcopy(self.tracks)
        tracks["../../escape"] = tracks.pop("translated")
        self.assertEqual(self.client.put(self.url, json={"revision": 1, "tracks": tracks}).status_code, 422)
        self.assertEqual(self.client.get(self.url).json()["revision"], 1)

    def test_review_cancellation_and_expiry_keep_history_but_release_media(self):
        self.assertIn(JobStatus.AWAITING_REVIEW, ACTIVE_STATUSES)
        with patch.dict("os.environ", {"REVIEW_TTL_HOURS": "100000"}):
            self.worker.collect()
        self.assertTrue(self.repo.source_path(self.record).exists())
        with patch.dict("os.environ", {"REVIEW_TTL_HOURS": "0"}):
            self.worker.collect()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.CANCELLED)
        self.assertTrue(self.repo.read(self.job).metadata["review_expired_at"])
        self.assertFalse(self.repo.source_path(self.record).exists())
        self.assertEqual(self.client.get(self.url).status_code, 409)

    def test_cancel_waiting_review_is_immediate(self):
        self.assertEqual(self.client.post(f"/api/jobs/{self.job}/cancel").status_code, 200)
        self.assertEqual(self.repo.read(self.job).status, JobStatus.CANCELLED)
        self.worker.collect()
        self.assertTrue(self.repo.source_path(self.record).exists())

    def test_live_execution_lock_blocks_editor_write(self):
        with job_lock(self.repo, self.job, "execution"):
            self.assertEqual(self.client.put(self.url, json={"revision": 1, "tracks": self.tracks}).status_code, 409)

    def test_capacity_rejection_preserves_draft_revision_and_review_status(self):
        from video_service.capacity import StorageLimitError
        before = self.client.get(self.url).json()
        with patch.object(routes, "assert_capacity", side_effect=StorageLimitError("quota")) as capacity:
            response = self.client.put(self.url, json={"revision": 1, "tracks": self.tracks, "action": "render"})
        self.assertEqual(response.status_code, 507)
        self.assertGreater(capacity.call_args.kwargs["additional"], 0)
        self.assertEqual(self.client.get(self.url).json(), before)
        self.assertEqual(self.repo.read(self.job).status, JobStatus.AWAITING_REVIEW)
        self.assertFalse((self.repo.job_dir(self.job) / "work/subtitle-draft.json.tmp").exists())

    def test_admission_lock_blocks_draft_write(self):
        with job_lock(self.repo, "0" * 32):
            response = self.client.put(self.url, json={"revision": 1, "tracks": self.tracks})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(self.url).json()["revision"], 1)

    def test_disk_full_during_write_returns_507_without_approval(self):
        import errno
        with patch("video_service.storage.Path.write_bytes", side_effect=OSError(errno.ENOSPC, "full")):
            response = self.client.put(self.url, json={"revision": 1, "tracks": self.tracks, "action": "render"})
        self.assertEqual(response.status_code, 507)
        self.assertEqual(self.client.get(self.url).json()["revision"], 1)
        self.assertEqual(self.repo.read(self.job).status, JobStatus.AWAITING_REVIEW)

    def test_long_edited_text_uses_existing_readable_layout(self):
        tracks = deepcopy(self.tracks)
        tracks["translated"][0]["text"] = "가" * 100
        response = self.client.put(self.url, json={"revision": 1, "tracks": tracks})
        self.assertEqual(response.status_code, 200)
        cues = response.json()["tracks"]["translated"]
        self.assertGreater(len(cues), 1)
        self.assertEqual(cues[0]["start"], 0)
        self.assertEqual(cues[-1]["end"], 1)
        for cue in cues:
            self.assertLessEqual(len(cue["text"].splitlines()), 2)
            self.assertTrue(all(len(line) <= 24 for line in cue["text"].splitlines()))

    def test_prepare_waits_and_approved_resume_does_not_repeat_ai(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        provider = Mock(transcription_model="test-stt", translation_model="test-translation", translation_identity="test-translation", audio_filter_model="test-filter")
        provider.transcribe.return_value = [TranscriptSegment(0, 1, "Original")]
        provider.translate.return_value = [TranscriptSegment(0, 1, "Translated")]
        worker = Worker(self.repo, provider)
        metadata = {"duration": 3, "streams": [
            {"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "30/1",
             "codec_name": "hevc", "codec_tag_string": "hvc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"}]}
        audio = self.repo.job_dir(self.job) / "work/audio.wav"
        audio.write_bytes(b"audio")
        (audio.parent / "processed-audio.wav").write_bytes(b"processed")
        (audio.parent / "timeline-map.json").write_text("[]")
        with ExitStack() as stack:
            stack.enter_context(patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"))
            stack.enter_context(patch("media_worker.worker.probe", return_value=metadata))
            extract = stack.enter_context(patch("media_worker.worker.extract_audio", return_value=audio))
            stack.enter_context(patch("media_worker.worker.preprocess_audio", return_value=(audio,
                build_timeline_from_kept_intervals([(0, 3)]))))
            encode = stack.enter_context(patch("media_worker.worker.run_process", side_effect=lambda args, **kw: Path(args[-1]).write_bytes(b"out")))
            stack.enter_context(patch("media_worker.worker.validate_decodable"))
            worker.process(self.job)
            self.assertEqual(self.repo.read(self.job).status, JobStatus.AWAITING_REVIEW)
            encode.assert_not_called()
            self.assertTrue(self.repo.source_path(self.record).exists())
            draft = self.client.get(self.url).json()
            draft["tracks"]["translated"][0]["text"] = "Human edit"
            self.assertEqual(self.client.put(self.url, json={**draft, "action": "render"}).status_code, 200)
            worker.process(self.job)
        provider.transcribe.assert_called_once()
        provider.translate.assert_called_once()
        extract.assert_called_once()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.COMPLETED)
        self.assertIn("Human edit", (self.repo.job_dir(self.job) / "output/translated.srt").read_text())
