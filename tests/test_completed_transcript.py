from pathlib import Path
import runpy
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.checkpoints import Checkpoints
from media_worker.completed_transcript import transcript_identity, load_completed_transcript, save_completed_transcript
from media_worker.sentence_transcription import transcription_prompt
from media_worker.worker import Worker
from media_worker.transcription_queue import PartialTranslationError
from media_worker.content_block import BLOCKED_TEXT
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.storage import read_json, write_json_atomic
from video_service.timeline import build_timeline_from_kept_intervals
from video_service.transcript import TranscriptSegment


class CompletedTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.record = self.repo.create_job(original_filename="clip.mp4", expected_size=1,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.source = self.repo.source_path(self.record)
        self.source.write_bytes(b"x")
        self.work = self.repo.job_dir(self.record.job_id) / "work"
        self.identity = transcript_identity(self.source, self.record)
        self.cues = [{"start": 12.0, "end": 13.0, "text": "Speech"}]

    def test_snapshot_survives_model_change_and_transient_file_replacement(self):
        save_completed_transcript(self.work, self.identity, self.cues, "old-model")
        write_json_atomic(self.work / "transcript.json", [])
        self.assertEqual(load_completed_transcript(self.work, self.identity), self.cues)

    def test_changed_source_or_options_does_not_reuse(self):
        save_completed_transcript(self.work, self.identity, self.cues, "old")
        self.record.options.source_language = "ja"
        self.assertIsNone(load_completed_transcript(self.work, transcript_identity(self.source, self.record)))
        self.source.write_bytes(b"changed")
        self.assertIsNone(load_completed_transcript(self.work, transcript_identity(self.source, self.record)))

    def test_unverified_legacy_and_corrupt_receipts_fail_closed(self):
        write_json_atomic(self.work / "transcript.json", self.cues)
        with self.assertRaisesRegex(ValueError, "verified legacy"):
            load_completed_transcript(self.work, self.identity)
        save_completed_transcript(self.work, self.identity, self.cues, "old")
        path = self.work / "completed-transcript.json"
        saved = read_json(path)
        saved["cues"] = []
        write_json_atomic(path, saved)
        with self.assertRaisesRegex(ValueError, "integrity"):
            load_completed_transcript(self.work, self.identity)

    def test_empty_completed_transcript_is_not_missing(self):
        save_completed_transcript(self.work, self.identity, [], "old")
        self.assertEqual(load_completed_transcript(self.work, self.identity), [])

    def test_retry_after_translation_failure_skips_stt_with_new_models(self):
        provider = Mock(transcription_model="old", translation_model="old", audio_filter_model="old")
        provider.transcribe.return_value = [TranscriptSegment(0, 1, "Speech")]
        provider.translate.side_effect = RuntimeError("translation unavailable")
        for name in ("audio.wav", "processed-audio.wav", "timeline-map.json"):
            (self.work / name).write_bytes(b"test")
        metadata = {"duration": 20, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        with patch.dict("os.environ", {"PAID_LLM_ENABLED": "false", "VOCALIZATION_FILTER_ENABLED": "false"}), \
                patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"), \
                patch("media_worker.worker.probe", return_value=metadata), \
                patch("media_worker.worker.extract_audio", return_value=self.work / "audio.wav") as extract, \
                patch("media_worker.worker.preprocess_audio", return_value=(self.work / "processed-audio.wav",
                    build_timeline_from_kept_intervals([(12, 13)]))) as preprocess:
            self.repo.update_status(self.record.job_id, JobStatus.QUEUED)
            Worker(self.repo, provider).process(self.record.job_id)
            self.assertEqual(self.repo.read(self.record.job_id).metadata["failed_stage"], "TRANSLATING")
            provider.transcription_model = provider.translation_model = provider.audio_filter_model = "new"
            self.repo.update_status(self.record.job_id, JobStatus.QUEUED)
            Worker(self.repo, provider).process(self.record.job_id)
        provider.transcribe.assert_called_once()
        extract.assert_called_once()
        preprocess.assert_called_once()
        self.assertEqual(provider.translate.call_count, 2)
        self.assertEqual([s.to_dict() for s in provider.translate.call_args.args[0]], self.cues)
        self.assertTrue(self.repo.read(self.record.job_id).metadata["transcription_reused"])

    def test_legacy_recovery_requires_exact_source_bound_completed_receipts(self):
        recover = runpy.run_path(str(ROOT / "scripts/recover-completed-transcript.py"))["recover"]
        old = {"version": 1, "source_size": self.source.stat().st_size,
            "source_mtime": self.source.stat().st_mtime_ns,
            "options": {k: v for k, v in self.record.options.to_dict().items()
                        if (k != "video_description" or v) and (k != "vad_mode" or v != "off")},
            "stt": "old", "transcription_policy": transcription_prompt("en", 0),
            "translation": "old", "filter_model": "old", "filter_enabled": "false"}
        checkpoints = Checkpoints(self.work, old, None)
        checkpoints.run("preprocess", lambda: {"spans": [s.to_dict()
            for s in build_timeline_from_kept_intervals([(12, 13)])]})
        checkpoints.run("transcribe-packed-60-v2", lambda: [{"start": 0, "end": 1, "text": "Speech"}])
        write_json_atomic(self.work / "transcript.json", self.cues)
        self.repo.update_status(self.record.job_id, JobStatus.CANCELLED)
        with patch.dict("os.environ", {"VOCALIZATION_FILTER_ENABLED": "false"}):
            self.assertEqual(recover(self.repo, self.record.job_id, "old")["verified_cues"], 1)
            self.assertFalse((self.work / "completed-transcript.json").exists())
            with self.assertRaises(OSError):
                recover(self.repo, self.record.job_id, "wrong")
            write_json_atomic(self.work / "transcript.json", [])
            with self.assertRaisesRegex(ValueError, "does not match"):
                recover(self.repo, self.record.job_id, "old", True)
            write_json_atomic(self.work / "transcript.json", self.cues)
            recover(self.repo, self.record.job_id, "old", True)
        self.assertEqual(load_completed_transcript(self.work, self.identity), self.cues)

    def test_block_metadata_reaches_job_and_is_cleared_on_retry(self):
        save_completed_transcript(self.work, self.identity, self.cues, "old")
        error = PartialTranslationError({}, [3])
        error.content_blocks = [{"segment": 4, "reason": "PROHIBITED_CONTENT"}]
        provider = Mock(transcription_model="new", translation_model="new", audio_filter_model="new")
        provider.translate.side_effect = [error, RuntimeError("network failure")]
        metadata = {"duration": 20, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        with patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"), \
                patch("media_worker.worker.probe", return_value=metadata):
            self.repo.update_status(self.record.job_id, JobStatus.QUEUED)
            Worker(self.repo, provider).process(self.record.job_id)
            job = self.repo.read(self.record.job_id)
            self.assertEqual(job.metadata["failure"], {"kind": "content_blocked", "provider": "Gemini",
                "stage": "TRANSLATING", "blocks": error.content_blocks})
            self.assertIn("Gemini", job.error)
            self.repo.update_status(self.record.job_id, JobStatus.QUEUED)
            Worker(self.repo, provider).process(self.record.job_id)
            self.assertIsNone(self.repo.read(self.record.job_id).metadata["failure"])
        provider.transcribe.assert_not_called()

    def test_placeholder_finishes_subtitles_and_pauses_before_encoding_without_review_option(self):
        save_completed_transcript(self.work, self.identity, self.cues, "old")
        provider = Mock(transcription_model="new", translation_model="new", audio_filter_model="new")
        provider.translate.return_value = [TranscriptSegment(12, 13, BLOCKED_TEXT)]
        metadata = {"duration": 20, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        self.assertFalse(self.record.options.review_subtitles)
        with patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"), \
                patch("media_worker.worker.probe", return_value=metadata), \
                patch("media_worker.worker.run_process") as encode:
            self.repo.update_status(self.record.job_id, JobStatus.QUEUED)
            Worker(self.repo, provider).process(self.record.job_id)
        job = self.repo.read(self.record.job_id)
        self.assertEqual(job.status, JobStatus.AWAITING_REVIEW, job.error)
        self.assertTrue(job.metadata['content_block_review'])
        encode.assert_not_called()
        self.assertIn(BLOCKED_TEXT, (self.work.parent / 'output/translated.srt').read_text(encoding='utf-8'))
        self.assertTrue((self.work / 'subtitle-draft.json').exists())
