from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep
import shutil
import sys
import unittest
from unittest.mock import patch, Mock
from contextlib import ExitStack
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.worker import Worker
from media_worker.process import Cancelled
from video_service.storage import remove_path_inside
from video_service.locking import job_lock
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.timeline import build_timeline_from_kept_intervals
from video_service.transcript import TranscriptSegment


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)
        self.record = self.repo.create_job(original_filename="clip.mp4", expected_size=1,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.job = self.record.job_id
        self.worker = Worker(self.repo, None)
        self.repo.source_path(self.record).write_bytes(b"x")

    def test_failed_process_cleans_all_media(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        with patch("media_worker.worker.select_encoder", side_effect=RuntimeError("encoder unavailable")):
            self.worker.process(self.job)
        self.assertEqual(self.repo.read(self.job).status, JobStatus.FAILED)
        self.assertIn("encoder unavailable", self.repo.read(self.job).error)
        self.assertEqual({p.name for p in self.repo.job_dir(self.job).iterdir()}, {"job.json"})

    def test_pcm_capacity_failure_stops_before_extraction(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        metadata = {"duration": 3600, "streams": [
            {"codec_type": "video", "avg_frame_rate": "30/1"}, {"codec_type": "audio"}]}
        with patch.dict("os.environ", {"VIDEO_SERVICE_QUOTA_BYTES": str(1024**2), "MIN_FREE_SPACE_BYTES": "0"}), \
                patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"), \
                patch("media_worker.worker.probe", return_value=metadata), \
                patch("media_worker.worker.extract_audio") as extract:
            self.worker.process(self.job)
        extract.assert_not_called()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.FAILED)
        self.assertIn("quota", self.repo.read(self.job).error)
        self.assertFalse(self.repo.source_path(self.record).exists())

    def test_completed_pipeline_preserves_original_and_translated_subtitles(self):
        self.record.options.additional_languages = ["ja", "es"]
        self.repo.save(self.record)
        self.repo.update_status(self.job, JobStatus.QUEUED)
        provider = Mock()
        provider.detect_vocalizations.return_value = {"removals": [{"start": 2.3, "end": 2.7, "kind": "breath"}],
            "protected": [{"start": 12, "end": 13}]}
        provider.transcribe.return_value = [TranscriptSegment(0, 1, "Original speech.")]
        provider.translate.side_effect = lambda segments, language, check: [s.with_text(f"Translated speech. {language}") for s in segments]
        worker = Worker(self.repo, provider)
        audio = self.repo.job_dir(self.job) / "work/audio.wav"
        metadata = {"duration": 14, "streams": [
            {"codec_type": "video", "avg_frame_rate": "30/1", "width": 640, "height": 360,
             "codec_name": "hevc", "codec_tag_string": "hvc1", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"}]}
        def encode(args, **kwargs):
            Path(args[-1]).write_bytes(b"output")
        with ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"VOCALIZATION_FILTER_ENABLED": "true"}))
            stack.enter_context(patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"))
            stack.enter_context(patch("media_worker.worker.probe", return_value=metadata))
            stack.enter_context(patch("media_worker.worker.extract_audio", return_value=audio))
            preprocess = stack.enter_context(patch("media_worker.worker.preprocess_audio", return_value=(audio,
                build_timeline_from_kept_intervals([(12, 13)]))))
            stack.enter_context(patch("media_worker.worker.run_process", side_effect=encode))
            decode = stack.enter_context(patch("media_worker.worker.validate_decodable"))
            worker.process(self.job)
        decode.assert_called_once()
        record = self.repo.read(self.job)
        self.assertEqual(record.status, JobStatus.COMPLETED, record.error)
        output = self.repo.job_dir(self.job) / "output"
        original = (output / "original.srt").read_text(encoding="utf-8")
        translated = (output / "translated.srt").read_text(encoding="utf-8")
        self.assertIn("00:00:12,000 --> 00:00:13,000", original)
        self.assertIn("Original speech.", original)
        self.assertNotIn("Translated speech.", original)
        self.assertIn("Translated speech.", translated)
        self.assertIn("speech. ko", translated)
        provider.transcribe.assert_called_once()
        provider.detect_vocalizations.assert_called_once()
        self.assertEqual(preprocess.call_args.kwargs["vocalizations"], provider.detect_vocalizations.return_value["removals"])
        self.assertEqual(preprocess.call_args.kwargs["protected_audio"], provider.detect_vocalizations.return_value["protected"])
        self.assertEqual(record.metadata["vocalization_removal_count"], 1)
        self.assertEqual([call.args[1] for call in provider.translate.call_args_list], ["ko", "ja", "es"])
        for language in ("ja", "es"):
            text = (output / f"translated.{language}.srt").read_text(encoding="utf-8")
            self.assertIn(f"speech. {language}", text)
            self.assertIn("00:00:12,000 --> 00:00:13,000", text)
            self.assertIn(f"translated.{language}.srt", record.metadata["result_files"])
            self.assertTrue((output / f"translated.{language}.smi").exists())
        self.assertIn("original.srt", record.metadata["result_files"])
        self.assertIn("translated.smi", record.metadata["result_files"])
        self.assertIn('Start="12000"', (output / "translated.smi").read_text(encoding="utf-8"))
        self.assertFalse((self.repo.job_dir(self.job) / "work").exists())

    def test_cancel_during_pipeline_stops_before_audio(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        def cancel(work, check, **kwargs):
            with job_lock(self.repo, self.job):
                record = self.repo.read(self.job)
                record.metadata["cancel_requested"] = True
                self.repo.save(record)
            check()
        with patch("media_worker.worker.select_encoder", side_effect=cancel), patch("media_worker.worker.extract_audio") as extraction:
            self.worker.process(self.job)
        extraction.assert_not_called()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.CANCELLED)
        self.assertFalse(self.repo.source_path(self.record).exists())

    def test_additional_translation_failure_fails_entire_job_and_cleans_media(self):
        self.record.options.additional_languages = ["ja"]
        self.repo.save(self.record)
        self.repo.update_status(self.job, JobStatus.QUEUED)
        provider = Mock()
        provider.transcribe.return_value = [TranscriptSegment(0, 1, "Speech")]
        provider.translate.side_effect = [[TranscriptSegment(0, 1, "Primary")], RuntimeError("additional translation failed")]
        audio = self.repo.job_dir(self.job) / "work/audio.wav"
        metadata = {"duration": 2, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        with ExitStack() as stack:
            stack.enter_context(patch("media_worker.worker.select_encoder", return_value="hevc_nvenc"))
            stack.enter_context(patch("media_worker.worker.probe", return_value=metadata))
            stack.enter_context(patch("media_worker.worker.extract_audio", return_value=audio))
            stack.enter_context(patch("media_worker.worker.preprocess_audio", return_value=(audio,
                build_timeline_from_kept_intervals([(0, 2)]))))
            encode = stack.enter_context(patch("media_worker.worker.run_process"))
            Worker(self.repo, provider).process(self.job)
        encode.assert_not_called()
        record = self.repo.read(self.job)
        self.assertEqual(record.status, JobStatus.FAILED)
        self.assertIn("additional translation failed", record.error)
        self.assertEqual({p.name for p in self.repo.job_dir(self.job).iterdir()}, {"job.json"})

    def test_collection_skips_live_execution(self):
        self.repo.update_status(self.job, JobStatus.ENCODING)
        with job_lock(self.repo, self.job, "execution"):
            self.worker.collect()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.ENCODING)
        self.assertTrue(self.repo.source_path(self.record).exists())
        self.worker.collect()
        self.assertEqual(self.repo.read(self.job).status, JobStatus.FAILED)

    def test_completed_results_remain_until_expiry(self):
        output = self.repo.job_dir(self.job) / "output/final.mp4"
        output.write_bytes(b"video")
        self.repo.update_status(self.job, JobStatus.COMPLETED)
        self.worker.collect()
        self.assertTrue(output.exists())
        self.assertFalse(self.repo.source_path(self.record).exists())
        record = self.repo.read(self.job)
        record.completed_at = "2000-01-01T00:00:00+00:00"
        self.repo.save(record)
        self.worker.collect()
        self.assertFalse(self.repo.job_dir(self.job).exists())

    def test_cleanup_failure_preserves_original_error_and_gc_retries(self):
        self.repo.update_status(self.job, JobStatus.QUEUED)
        def fail_input(root, path):
            if path.name == "input":
                raise PermissionError("file in use")
            remove_path_inside(root, path)
        with patch("media_worker.worker.select_encoder", side_effect=RuntimeError("encoder failed")), \
                patch("media_worker.worker.remove_path_inside", side_effect=fail_input), \
                self.assertLogs(level="WARNING"):
            self.worker.process(self.job)
        record = self.repo.read(self.job)
        self.assertEqual(record.status, JobStatus.FAILED)
        self.assertEqual(record.error, "encoder failed")
        self.assertTrue(record.metadata["cleanup_pending"])
        self.assertIn("input", record.metadata["cleanup_error"])
        self.assertTrue(self.repo.source_path(record).exists())
        self.assertFalse((self.repo.job_dir(self.job) / "work").exists())
        self.assertFalse((self.repo.job_dir(self.job) / "output").exists())
        self.worker.collect()
        self.assertFalse(self.repo.source_path(record).exists())
        self.assertFalse(self.repo.read(self.job).metadata["cleanup_pending"])
        self.assertEqual(self.repo.read(self.job).error, "encoder failed")

    def test_collection_failure_does_not_skip_later_jobs(self):
        other = self.repo.create_job(original_filename="other.mp4", expected_size=1,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        self.repo.update_status(self.job, JobStatus.FAILED)
        self.repo.update_status(other.job_id, JobStatus.FAILED)
        cleanup = self.worker.cleanup
        def fail_one(job_id, keep_output=False):
            if job_id == self.job:
                raise PermissionError("busy")
            cleanup(job_id, keep_output)
        with patch.object(self.repo, "list", return_value=[self.repo.read(self.job), self.repo.read(other.job_id)]), \
                patch.object(self.worker, "cleanup", side_effect=fail_one), self.assertLogs(level="ERROR"):
            self.worker.collect()
        self.assertFalse((self.repo.job_dir(other.job_id) / "input").exists())

    def test_cancelled_transition_does_not_wait_for_state_lock(self):
        record = self.repo.read(self.job)
        record.metadata["cancel_requested"] = True
        self.repo.save(record)
        with job_lock(self.repo, self.job), self.assertRaises(Cancelled):
            self.worker.transition(self.job, JobStatus.TRANSLATING)

    def test_transition_survives_actual_state_lock_contention(self):
        waiting = Event()
        def pause(seconds):
            waiting.set()
            sleep(seconds)
        with ThreadPoolExecutor(max_workers=1) as pool, \
                patch("video_service.locking.time.sleep", side_effect=pause):
            with job_lock(self.repo, self.job):
                future = pool.submit(self.worker.transition, self.job, JobStatus.TRANSLATING)
                self.assertTrue(waiting.wait(1), "Worker did not retry the occupied lock")
                self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=2).status, JobStatus.TRANSLATING)


if __name__ == "__main__":
    unittest.main()
