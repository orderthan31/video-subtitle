from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from threading import Event

from video_service.locking import job_lock, JobBusyError, encoding_slot, wait_for_job_lock
from video_service.models import JobStatus, TERMINAL_STATUSES
from video_service.repository import FilesystemJobRepository, JobNotFoundError
from video_service.storage import remove_path_inside, write_json_atomic
from video_service.subtitles import segments_to_srt, segment_subtitles
from video_service.sami import segments_to_sami
from video_service.timeline import map_segment_to_original
from video_service.transcript import filter_transcript_segments
from .media import probe, extract_audio, preprocess_audio, encoding_args, select_encoder
from .process import run_process, Cancelled
from .providers import GeminiProvider
from .cleanup import collect_orphans
from .progress import encoding_progress
from video_service.config import load_environment
from video_service.capacity import assert_capacity, reserve_workspace
from .shutdown import shutdown_signals, WorkerStopping
from .validation import validate_cues, validate_output, validate_decodable


class Worker:
    def __init__(self, repository, provider, stop=None):
        self.repository = repository
        self.provider = provider
        self.stop = stop if stop is not None else Event()

    def check_stopping(self):
        if self.stop.is_set():
            raise WorkerStopping()

    def transition(self, job_id, status, **kwargs):
        def check_cancel():
            self.check_stopping()
            record = self.repository.read(job_id)
            if record.metadata.get("cancel_requested") or record.status == JobStatus.CANCELLED:
                raise Cancelled()
        with wait_for_job_lock(self.repository, job_id, check=check_cancel):
            metadata = {"stage_progress": None, **kwargs.pop("metadata", {})}
            return self.repository.update_status(job_id, status, metadata=metadata, **kwargs)

    def cleanup(self, job_id, keep_output=False):
        root = self.repository.storage_root
        errors = []
        for name in (["input", "work"] if keep_output else ["input", "work", "output"]):
            try:
                remove_path_inside(root, self.repository.job_dir(job_id) / name)
            except OSError as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            raise OSError("; ".join(errors))

    def process(self, job_id):
        repo = self.repository
        with job_lock(repo, job_id, "execution"):
            with job_lock(repo, job_id):
                if self.stop.is_set():
                    return
                record = repo.read(job_id)
                if record.status != JobStatus.QUEUED:
                    return
                repo.update_status(job_id, JobStatus.ANALYZING)
            last_heartbeat = 0
            last_progress = 0
            progress_path = None
            progress_duration = 0

            def check():
                nonlocal last_heartbeat, last_progress
                self.check_stopping()
                current = repo.read(job_id)
                if current.metadata.get("cancel_requested") or current.status == JobStatus.CANCELLED:
                    raise Cancelled()
                if progress_path is not None and time.monotonic() - last_progress >= 1:
                    fraction = encoding_progress(progress_path, progress_duration)
                    if fraction is not None:
                        try:
                            with job_lock(repo, job_id):
                                latest = repo.read(job_id)
                                latest.metadata["stage_progress"] = fraction
                                repo.save(latest)
                            last_progress = time.monotonic()
                        except JobBusyError:
                            pass
                if time.monotonic() - last_heartbeat > 5:
                    assert_capacity(repo.storage_root,
                        int(os.getenv("VIDEO_SERVICE_QUOTA_BYTES", str(300 * 1024**3))),
                        int(os.getenv("MIN_FREE_SPACE_BYTES", str(50 * 1024**3))))
                    try:
                        with job_lock(repo, job_id):
                            repo.heartbeat(job_id)
                        last_heartbeat = time.monotonic()
                    except JobBusyError:
                        pass

            try:
                work = repo.job_dir(job_id) / "work"
                work.mkdir(exist_ok=True)
                with encoding_slot(repo, int(os.getenv("MAX_ENCODING_JOBS", "1")), check):
                    encoder = select_encoder(work, check, video_codec=record.options.video_codec)
                source = repo.source_path(record).resolve()
                metadata = probe(source, work, check)
                videos = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
                if not videos or not any(s["codec_type"] == "audio" for s in metadata["streams"]):
                    raise ValueError("Video and audio streams are required")
                write_json_atomic(work / "metadata.json", metadata)
                reserve_workspace(repo, job_id, metadata["duration"],
                    int(os.getenv("VIDEO_SERVICE_QUOTA_BYTES", str(300 * 1024**3))),
                    int(os.getenv("MIN_FREE_SPACE_BYTES", str(50 * 1024**3))), check)
                self.transition(job_id, JobStatus.EXTRACTING_AUDIO)
                audio = extract_audio(source, work, check)
                self.transition(job_id, JobStatus.PREPROCESSING_AUDIO)
                audio, spans = preprocess_audio(audio, work, check)
                self.transition(job_id, JobStatus.TRANSCRIBING)
                segments = self.provider.transcribe(audio, record.options.source_language, check, spans=spans)
                segments = [s.with_times(*map_segment_to_original(s.start, s.end, spans)) for s in segments]
                self.transition(job_id, JobStatus.FILTERING_TRANSCRIPT)
                segments = filter_transcript_segments(segments)
                write_json_atomic(work / "transcript.json", [s.to_dict() for s in segments])
                self.transition(job_id, JobStatus.TRANSLATING)
                translated = self.provider.translate(segments, record.options.target_language, check)
                write_json_atomic(work / "translated.json", [s.to_dict() for s in translated])
                self.transition(job_id, JobStatus.GENERATING_SUBTITLE)
                cues = segment_subtitles(translated, line_width=24 if record.options.target_language in {"ko", "ja", "zh"} else 42)
                validate_cues(cues, metadata["duration"])
                srt = segments_to_srt(cues)
                if not srt.strip():
                    raise ValueError("No speech subtitles detected")
                output = repo.job_dir(job_id) / "output"
                output.mkdir(exist_ok=True)
                (output / "translated.srt").write_text(srt, encoding="utf-8")
                (output / "translated.smi").write_text(segments_to_sami(cues, record.options.target_language), encoding="utf-8")
                original_cues = segment_subtitles(segments,
                    line_width=24 if record.options.source_language in {"auto", "ko", "ja", "zh"} else 42)
                validate_cues(original_cues, metadata["duration"])
                (output / "original.srt").write_text(segments_to_srt(original_cues), encoding="utf-8")
                (work / "translated.srt").write_text(srt, encoding="utf-8")
                self.transition(job_id, JobStatus.ENCODING, message="인코딩 슬롯 대기 중")
                target = (output / "final.mp4").resolve()
                software = encoder in {"libx265", "libx264"}
                with encoding_slot(repo, int(os.getenv("MAX_ENCODING_JOBS", "1")), check):
                    self.transition(job_id, JobStatus.ENCODING, message="영상 인코딩 중")
                    progress_path = work / "encode-progress.txt"
                    progress_duration = metadata["duration"]
                    run_process(encoding_args(source, target, videos[0], record.options.quality_profile.value, software,
                        record.options.video_codec, record.options.subtitle_mode, record.options.target_language,
                        record.options.resolution),
                        cwd=work, log_name="encode.log", check=check)
                    progress_path = None
                self.transition(job_id, JobStatus.VALIDATING)
                final = probe(target, work, check)
                validate_output(metadata, final, target.stat().st_size, record.options.video_codec,
                    record.options.subtitle_mode, record.options.resolution)
                validate_decodable(target, work, check)
                self.transition(job_id, JobStatus.CLEANING)
                size = target.stat().st_size
                self.cleanup(job_id, keep_output=True)
                self.transition(job_id, JobStatus.COMPLETED, metadata={"output_bytes": size,
                    "duration": final["duration"], "encoder": encoder,
                    "result_files": ["final.mp4", "translated.srt", "original.srt", "translated.smi"]})
            except (Exception, KeyboardInterrupt) as exc:
                cancelled = isinstance(exc, (Cancelled, KeyboardInterrupt))
                cleanup_error = None
                try:
                    self.cleanup(job_id)
                except OSError as cleanup_exc:
                    cleanup_error = str(cleanup_exc)
                    logging.warning("Media cleanup deferred for job %s: %s", job_id, cleanup_error)
                with wait_for_job_lock(repo, job_id):
                    current = repo.read(job_id)
                    cancelled = cancelled or current.metadata.get("cancel_requested", False)
                    repo.update_status(job_id, JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                        error=None if cancelled else str(exc), metadata={"stage_progress": None,
                            "interrupted": isinstance(exc, WorkerStopping),
                            "cleanup_pending": cleanup_error is not None, "cleanup_error": cleanup_error})
                if isinstance(exc, KeyboardInterrupt):
                    raise

    def collect(self):
        if self.stop.is_set():
            return
        collect_orphans(self.repository, grace_seconds=float(os.getenv("ORPHAN_GRACE_HOURS", "2")) * 3600)
        now = datetime.now(timezone.utc)
        for record in self.repository.list():
            if self.stop.is_set():
                return
            try:
                with job_lock(self.repository, record.job_id, "execution"), job_lock(self.repository, record.job_id):
                    record = self.repository.read(record.job_id)
                    age = (now - datetime.fromisoformat(record.completed_at or record.updated_at)).total_seconds()
                    if record.status in TERMINAL_STATUSES:
                        self.cleanup(record.job_id, keep_output=record.status == JobStatus.COMPLETED)
                        if record.metadata.get("cleanup_pending"):
                            record.metadata.update(cleanup_pending=False, cleanup_error=None)
                            self.repository.save(record)
                        if age > float(os.getenv("RESULT_TTL_HOURS", "24")) * 3600:
                            self.repository.delete_job_dir(record.job_id)
                    elif record.status == JobStatus.UPLOADING:
                        if age > float(os.getenv("UPLOAD_TTL_HOURS", "6")) * 3600:
                            self.repository.delete_job_dir(record.job_id)
                    elif record.status != JobStatus.QUEUED:
                        self.cleanup(record.job_id)
                        self.repository.update_status(record.job_id, JobStatus.FAILED, error="Worker interrupted; upload again")
            except (JobBusyError, JobNotFoundError):
                continue
            except OSError:
                logging.exception("Collection deferred for job %s", record.job_id)

    def tick(self):
        if self.stop.is_set():
            return
        self.collect()
        jobs = self.repository.find_by_statuses([JobStatus.QUEUED])
        for job in reversed(jobs):
            if self.stop.is_set():
                return
            try:
                self.process(job.job_id)
            except (JobBusyError, JobNotFoundError):
                continue

    def run(self, poll_interval=5):
        while not self.stop.is_set():
            try:
                self.tick()
                self.stop.wait(poll_interval)
            except KeyboardInterrupt:
                self.stop.set()
            except Exception:
                logging.exception("Worker iteration failed")
                self.stop.wait(5)


def main():
    load_environment()
    logging.basicConfig(level=logging.INFO)
    repo = FilesystemJobRepository(Path(os.getenv("VIDEO_STORAGE_ROOT", "data/video-jobs")).resolve())
    worker = Worker(repo, GeminiProvider())
    with shutdown_signals(worker.stop):
        worker.run(float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5")))


if __name__ == "__main__":
    main()
