from datetime import datetime, timezone
import logging
import math
import os
from pathlib import Path
import time
from threading import Event

from video_service.locking import job_lock, JobBusyError, encoding_slot, wait_for_job_lock
from video_service.models import JobStatus, TERMINAL_STATUSES, utc_now_iso
from video_service.review import read_draft, write_draft
from video_service.repository import FilesystemJobRepository, JobNotFoundError
from video_service.storage import remove_path_inside, write_json_atomic
from video_service.subtitles import segments_to_srt, segment_subtitles
from video_service.sami import segments_to_sami
from video_service.timeline import map_segment_to_original, TimelineSpan
from video_service.transcript import filter_transcript_segments, TranscriptSegment
from .media import probe, extract_audio, preprocess_audio, encoding_args, select_encoder
from .process import run_process, Cancelled
from .providers import GeminiProvider
from .llm_trace import capture_calls
from .checkpoints import Checkpoints
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
        if self.repository.preserve_artifacts:
            return
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
        with job_lock(repo, job_id, "execution"), capture_calls(repo.storage_root,
                repo.job_dir(job_id) / "work" / "llm" if repo.preserve_artifacts else None):
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
                checkpoints = Checkpoints(work, {"version": 1, "source_size": source.stat().st_size,
                    "source_mtime": source.stat().st_mtime_ns, "options": record.options.to_dict(),
                    "stt": getattr(self.provider, "transcription_model", None),
                    "translation": getattr(self.provider, "translation_model", None),
                    "filter_model": getattr(self.provider, "audio_filter_model", None),
                    "filter_enabled": os.getenv("VOCALIZATION_FILTER_ENABLED", "false")},
                    lambda size: assert_capacity(repo.storage_root,
                        int(os.getenv("VIDEO_SERVICE_QUOTA_BYTES", str(300 * 1024**3))),
                        int(os.getenv("MIN_FREE_SPACE_BYTES", str(50 * 1024**3))), additional=size))
                metadata = probe(source, work, check)
                videos = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
                if not videos or not any(s["codec_type"] == "audio" for s in metadata["streams"]):
                    raise ValueError("Video and audio streams are required")
                write_json_atomic(work / "metadata.json", metadata)
                reserve_workspace(repo, job_id, metadata["duration"],
                    int(os.getenv("VIDEO_SERVICE_QUOTA_BYTES", str(300 * 1024**3))),
                    int(os.getenv("MIN_FREE_SPACE_BYTES", str(50 * 1024**3))), check)
                languages = [record.options.target_language, *record.options.additional_languages]
                if record.metadata.get("review_ready"):
                    draft = read_draft(repo, record)
                    subtitle_sets = {name: [TranscriptSegment.from_dict(cue) for cue in cues]
                        for name, cues in draft["tracks"].items()}
                else:
                    self.transition(job_id, JobStatus.EXTRACTING_AUDIO)
                    audio = work / checkpoints.run("extract", lambda: extract_audio(source, work, check).name,
                        files=("audio.wav",))
                    self.transition(job_id, JobStatus.PREPROCESSING_AUDIO)
                    vocalizations = []
                    protected_audio = []
                    filter_enabled = os.getenv("VOCALIZATION_FILTER_ENABLED", "false").lower()
                    if filter_enabled not in {"true", "false"}:
                        raise ValueError("VOCALIZATION_FILTER_ENABLED must be true or false")
                    if filter_enabled == "true" and record.options.audio_filter != "off":
                        analysis = checkpoints.run("vocalizations", lambda: self.provider.detect_vocalizations(
                            audio, record.options.source_language, check, strength=record.options.audio_filter))
                        vocalizations, protected_audio = analysis["removals"], analysis["protected"]
                        write_json_atomic(work / "vocalization-analysis.json", analysis)
                    def prepare_audio():
                        processed, timeline = preprocess_audio(audio, work, check, audio_filter=record.options.audio_filter,
                            vocalizations=vocalizations, protected_audio=protected_audio)
                        return {"audio": processed.name, "spans": [span.to_dict() for span in timeline]}
                    prepared = checkpoints.run("preprocess", prepare_audio,
                        files=("processed-audio.wav", "timeline-map.json"))
                    audio = work / prepared["audio"]
                    spans = [TimelineSpan.from_dict(span) for span in prepared["spans"]]
                    self.transition(job_id, JobStatus.PREPROCESSING_AUDIO, metadata={
                        "vocalization_filter_enabled": filter_enabled == "true" and record.options.audio_filter != "off",
                        "vocalization_removal_count": len(vocalizations)})
                    self.transition(job_id, JobStatus.TRANSCRIBING)
                    segments = [TranscriptSegment.from_dict(item) for item in checkpoints.run("transcribe", lambda: [
                        s.to_dict() for s in self.provider.transcribe(audio, record.options.source_language, check, spans=spans)])]
                    segments = [s.with_times(*map_segment_to_original(s.start, s.end, spans)) for s in segments]
                    self.transition(job_id, JobStatus.FILTERING_TRANSCRIPT)
                    segments = filter_transcript_segments(segments, record.options.audio_filter)
                    write_json_atomic(work / "transcript.json", [s.to_dict() for s in segments])
                    self.transition(job_id, JobStatus.TRANSLATING)
                    subtitle_sets = {"original": segment_subtitles(segments,
                        line_width=24 if record.options.source_language in {"auto", "ko", "ja", "zh"} else 42)}
                    for index, language in enumerate(languages):
                        check()
                        translated = [TranscriptSegment.from_dict(item) for item in checkpoints.run("translate-" + language,
                            lambda: [s.to_dict() for s in self.provider.translate(segments, language, check)])]
                        stem = "translated" if index == 0 else f"translated.{language}"
                        write_json_atomic(work / f"{stem}.json", [s.to_dict() for s in translated])
                        subtitle_sets[stem] = segment_subtitles(translated,
                            line_width=24 if language.split("-")[0] in {"ko", "ja", "zh"} else 42)
                    if record.options.review_subtitles:
                        with wait_for_job_lock(repo, "0" * 32, check=check):
                            write_draft(repo, record, {name: [cue.to_dict() for cue in cues]
                                for name, cues in subtitle_sets.items()}, metadata["duration"], 1,
                                capacity_check=lambda size: assert_capacity(repo.storage_root,
                                    int(os.getenv("VIDEO_SERVICE_QUOTA_BYTES", str(300 * 1024**3))),
                                    int(os.getenv("MIN_FREE_SPACE_BYTES", str(50 * 1024**3))), additional=size))
                        self.transition(job_id, JobStatus.AWAITING_REVIEW,
                            metadata={"awaiting_review_at": utc_now_iso()})
                        return
                self.transition(job_id, JobStatus.GENERATING_SUBTITLE)
                output = repo.job_dir(job_id) / "output"
                output.mkdir(exist_ok=True)
                result_files = ["final.mp4", "translated.srt", "original.srt", "translated.smi"]
                for index, language in enumerate(languages):
                    check()
                    stem = "translated" if index == 0 else f"translated.{language}"
                    cues = subtitle_sets[stem]
                    validate_cues(cues, metadata["duration"])
                    srt = segments_to_srt(cues)
                    (output / f"{stem}.srt").write_text(srt, encoding="utf-8")
                    (output / f"{stem}.smi").write_text(segments_to_sami(cues, language), encoding="utf-8")
                    (work / f"{stem}.srt").write_text(srt, encoding="utf-8")
                    if index:
                        result_files.extend([f"{stem}.srt", f"{stem}.smi"])
                original_cues = subtitle_sets["original"]
                validate_cues(original_cues, metadata["duration"])
                (output / "original.srt").write_text(segments_to_srt(original_cues), encoding="utf-8")
                self.transition(job_id, JobStatus.ENCODING, message="인코딩 슬롯 대기 중")
                target = (output / "final.mp4").resolve()
                software = encoder in {"libx265", "libx264"}
                with encoding_slot(repo, int(os.getenv("MAX_ENCODING_JOBS", "1")), check):
                    self.transition(job_id, JobStatus.ENCODING, message="영상 인코딩 중")
                    progress_path = work / "encode.log"
                    progress_duration = metadata["duration"]
                    run_process(encoding_args(source, target, videos[0], record.options.quality_profile.value, software,
                        record.options.video_codec, record.options.subtitle_mode, record.options.target_language,
                        record.options.resolution, record.options.additional_languages),
                        cwd=work, log_name="encode.log", check=check)
                    progress_path = None
                self.transition(job_id, JobStatus.VALIDATING)
                final = probe(target, work, check)
                validate_output(metadata, final, target.stat().st_size, record.options.video_codec,
                    record.options.subtitle_mode, record.options.resolution,
                    subtitle_count=len(languages))
                validate_decodable(target, work, check)
                self.transition(job_id, JobStatus.CLEANING)
                size = target.stat().st_size
                self.cleanup(job_id, keep_output=True)
                self.transition(job_id, JobStatus.COMPLETED, metadata={"output_bytes": size,
                    "duration": final["duration"], "encoder": encoder,
                    "result_files": result_files})
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
                            "failed_stage": current.status.value,
                            "cleanup_pending": cleanup_error is not None, "cleanup_error": cleanup_error})
                if isinstance(exc, KeyboardInterrupt):
                    raise

    def collect(self):
        if self.stop.is_set():
            return
        result_ttl = float(os.getenv("RESULT_TTL_HOURS", "24")) * 3600
        history_ttl = float(os.getenv("HISTORY_TTL_DAYS", "90")) * 86400
        upload_ttl = float(os.getenv("UPLOAD_TTL_HOURS", "6")) * 3600
        review_ttl = float(os.getenv("REVIEW_TTL_HOURS", "24")) * 3600
        if any(not math.isfinite(value) or value < 0 for value in (result_ttl, history_ttl, upload_ttl, review_ttl)):
            raise ValueError("Retention periods must be finite and nonnegative")
        history_ttl = max(history_ttl, result_ttl)
        collect_orphans(self.repository, grace_seconds=float(os.getenv("ORPHAN_GRACE_HOURS", "2")) * 3600)
        now = datetime.now(timezone.utc)
        for record in self.repository.list():
            if self.stop.is_set():
                return
            try:
                with job_lock(self.repository, record.job_id, "execution"), job_lock(self.repository, record.job_id):
                    record = self.repository.read(record.job_id)
                    if self.repository.preserve_artifacts and record.status in (
                        *TERMINAL_STATUSES, JobStatus.UPLOADING, JobStatus.AWAITING_REVIEW,
                    ):
                        continue
                    age = (now - datetime.fromisoformat(record.completed_at or record.updated_at)).total_seconds()
                    if record.status in TERMINAL_STATUSES:
                        if age > history_ttl:
                            self.repository.delete_job_dir(record.job_id)
                            continue
                        expired = record.status == JobStatus.COMPLETED and (
                            age > result_ttl or bool(record.metadata.get("results_expired_at")))
                        self.cleanup(record.job_id, keep_output=record.status == JobStatus.COMPLETED and not expired)
                        changed = False
                        if record.metadata.get("cleanup_pending"):
                            record.metadata.update(cleanup_pending=False, cleanup_error=None)
                            changed = True
                        if expired and not record.metadata.get("results_expired_at"):
                            record.metadata["results_expired_at"] = now.isoformat()
                            changed = True
                        if changed:
                            self.repository.save(record)
                    elif record.status == JobStatus.AWAITING_REVIEW:
                        review_age = (now - datetime.fromisoformat(record.metadata.get("awaiting_review_at", record.updated_at))).total_seconds()
                        if review_age > review_ttl:
                            self.cleanup(record.job_id)
                            self.repository.update_status(record.job_id, JobStatus.CANCELLED,
                                message="자막 검토 보관 기간이 만료되었습니다.",
                                metadata={"review_expired_at": now.isoformat()})
                    elif record.status == JobStatus.UPLOADING:
                        if age > upload_ttl:
                            self.cleanup(record.job_id)
                            self.repository.update_status(record.job_id, JobStatus.CANCELLED,
                                message="업로드 보관 기간이 만료되었습니다.",
                                metadata={"upload_expired_at": now.isoformat()})
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
