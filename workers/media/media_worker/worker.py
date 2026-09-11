from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time

from video_service.locking import job_lock, JobBusyError, encoding_slot
from video_service.models import JobStatus, TERMINAL_STATUSES
from video_service.repository import FilesystemJobRepository, JobNotFoundError
from video_service.storage import remove_path_inside, write_json_atomic
from video_service.subtitles import segments_to_srt, segment_subtitles
from video_service.timeline import map_segment_to_original
from video_service.transcript import filter_transcript_segments
from .media import probe, extract_audio, preprocess_audio, encoding_args, select_encoder
from .process import run_process, Cancelled
from .providers import GeminiProvider
from .cleanup import collect_orphans
from video_service.config import load_environment
from video_service.capacity import assert_capacity


class Worker:
    def __init__(self, repository, provider):
        self.repository = repository
        self.provider = provider

    def transition(self, job_id, status, **kwargs):
        with job_lock(self.repository, job_id):
            record = self.repository.read(job_id)
            if record.metadata.get("cancel_requested") or record.status == JobStatus.CANCELLED:
                raise Cancelled()
            return self.repository.update_status(job_id, status, **kwargs)

    def cleanup(self, job_id, keep_output=False):
        root = self.repository.storage_root
        for name in (["input", "work"] if keep_output else ["input", "work", "output"]):
            remove_path_inside(root, self.repository.job_dir(job_id) / name)

    def process(self, job_id):
        repo = self.repository
        with job_lock(repo, job_id, "execution"):
            with job_lock(repo, job_id):
                record = repo.read(job_id)
                if record.status != JobStatus.QUEUED:
                    return
                repo.update_status(job_id, JobStatus.ANALYZING)
            last_heartbeat = 0

            def check():
                nonlocal last_heartbeat
                current = repo.read(job_id)
                if current.metadata.get("cancel_requested") or current.status == JobStatus.CANCELLED:
                    raise Cancelled()
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
                    encoder = select_encoder(work, check)
                source = repo.source_path(record).resolve()
                metadata = probe(source, work, check)
                videos = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
                if not videos or not any(s["codec_type"] == "audio" for s in metadata["streams"]):
                    raise ValueError("Video and audio streams are required")
                write_json_atomic(work / "metadata.json", metadata)
                self.transition(job_id, JobStatus.EXTRACTING_AUDIO)
                audio = extract_audio(source, work, check)
                self.transition(job_id, JobStatus.PREPROCESSING_AUDIO)
                audio, spans = preprocess_audio(audio, work, check)
                self.transition(job_id, JobStatus.TRANSCRIBING)
                segments = self.provider.transcribe(audio, record.options.source_language, check)
                segments = [s.with_times(*map_segment_to_original(s.start, s.end, spans)) for s in segments]
                self.transition(job_id, JobStatus.FILTERING_TRANSCRIPT)
                segments = filter_transcript_segments(segments)
                write_json_atomic(work / "transcript.json", [s.to_dict() for s in segments])
                self.transition(job_id, JobStatus.TRANSLATING)
                translated = self.provider.translate(segments, record.options.target_language, check)
                write_json_atomic(work / "translated.json", [s.to_dict() for s in translated])
                self.transition(job_id, JobStatus.GENERATING_SUBTITLE)
                cues = segment_subtitles(translated, line_width=24 if record.options.target_language in {"ko", "ja", "zh"} else 42)
                srt = segments_to_srt(cues)
                if not srt.strip():
                    raise ValueError("No speech subtitles detected")
                output = repo.job_dir(job_id) / "output"
                output.mkdir(exist_ok=True)
                (output / "translated.srt").write_text(srt, encoding="utf-8")
                (work / "translated.srt").write_text(srt, encoding="utf-8")
                self.transition(job_id, JobStatus.ENCODING, message="인코딩 슬롯 대기 중")
                target = (output / "final.mp4").resolve()
                software = encoder == "libx265"
                with encoding_slot(repo, int(os.getenv("MAX_ENCODING_JOBS", "1")), check):
                    self.transition(job_id, JobStatus.ENCODING, message="영상 인코딩 중")
                    run_process(encoding_args(source, target, videos[0], record.options.quality_profile.value, software),
                        cwd=work, log_name="encode.log", check=check)
                self.transition(job_id, JobStatus.VALIDATING)
                final = probe(target, work, check)
                kinds = {s["codec_type"] for s in final["streams"]}
                if not {"video", "audio"}.issubset(kinds) or not target.stat().st_size:
                    raise ValueError("Invalid output streams")
                if abs(final["duration"] - metadata["duration"]) > max(1, metadata["duration"] * 0.01):
                    raise ValueError("Output duration mismatch")
                self.transition(job_id, JobStatus.CLEANING)
                size = target.stat().st_size
                self.cleanup(job_id, keep_output=True)
                self.transition(job_id, JobStatus.COMPLETED, metadata={"output_bytes": size, "duration": final["duration"], "encoder": encoder})
            except (Exception, KeyboardInterrupt) as exc:
                cancelled = isinstance(exc, (Cancelled, KeyboardInterrupt))
                self.cleanup(job_id)
                with job_lock(repo, job_id):
                    current = repo.read(job_id)
                    cancelled = cancelled or current.metadata.get("cancel_requested", False)
                    repo.update_status(job_id, JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                        error=None if cancelled else str(exc))
                if isinstance(exc, KeyboardInterrupt):
                    raise

    def collect(self):
        collect_orphans(self.repository, grace_seconds=float(os.getenv("ORPHAN_GRACE_HOURS", "2")) * 3600)
        now = datetime.now(timezone.utc)
        for record in self.repository.list():
            try:
                with job_lock(self.repository, record.job_id, "execution"), job_lock(self.repository, record.job_id):
                    record = self.repository.read(record.job_id)
                    age = (now - datetime.fromisoformat(record.completed_at or record.updated_at)).total_seconds()
                    if record.status in TERMINAL_STATUSES:
                        self.cleanup(record.job_id, keep_output=record.status == JobStatus.COMPLETED)
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

    def tick(self):
        self.collect()
        jobs = self.repository.find_by_statuses([JobStatus.QUEUED])
        for job in reversed(jobs):
            try:
                self.process(job.job_id)
            except (JobBusyError, JobNotFoundError):
                continue


def main():
    load_environment()
    logging.basicConfig(level=logging.INFO)
    repo = FilesystemJobRepository(Path(os.getenv("VIDEO_STORAGE_ROOT", "data/video-jobs")).resolve())
    worker = Worker(repo, GeminiProvider())
    while True:
        try:
            worker.tick()
            time.sleep(float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5")))
        except KeyboardInterrupt:
            break
        except Exception:
            logging.exception("Worker iteration failed")
            time.sleep(5)


if __name__ == "__main__":
    main()
