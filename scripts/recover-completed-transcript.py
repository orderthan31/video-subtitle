"""Verify legacy stage receipts against the retained transcript. No LLM calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from media_worker.completed_transcript import transcript_identity, save_completed_transcript
from media_worker.sentence_transcription import transcription_prompt
from video_service.locking import job_lock
from video_service.models import JobStatus
from video_service.repository import FilesystemJobRepository
from video_service.storage import read_json
from video_service.timeline import TimelineSpan, project_segment_to_original
from video_service.transcript import TranscriptSegment, filter_transcript_segments


def recover(repo, job_id, model, apply=False):
    with job_lock(repo, job_id, "execution"), job_lock(repo, job_id):
        record = repo.read(job_id)
        if record.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("Recovery requires a stopped job")
        source = repo.source_path(record).resolve()
        work = repo.job_dir(job_id) / "work"
        identity = {"version": 1, "source_size": source.stat().st_size,
            "source_mtime": source.stat().st_mtime_ns,
            "options": {k: v for k, v in record.options.to_dict().items()
                        if (k != "video_description" or v) and (k != "vad_mode" or v != "off")},
            "stt": model, "transcription_policy": transcription_prompt(record.options.source_language, 0),
            "translation": model, "filter_model": model,
            "filter_enabled": os.getenv("VOCALIZATION_FILTER_ENABLED", "false")}
        def receipt(name):
            key = hashlib.sha256(json.dumps([identity, name], sort_keys=True).encode()).hexdigest()
            return read_json(work / "checkpoints" / (key + ".json"))["result"]
        # These receipts are bound to the original source/options/model. Never
        # remap old transcription with the newly generated audio timeline.
        spans = [TimelineSpan.from_dict(item) for item in receipt("preprocess")["spans"]]
        restored = []
        for item in receipt("transcribe-packed-60-v2"):
            segment = TranscriptSegment.from_dict(item)
            restored.extend(segment.with_times(a, b)
                for a, b in project_segment_to_original(segment.start, segment.end, spans)
                if round(b * 1000) > round(a * 1000))
        cues = [s.to_dict() for s in filter_transcript_segments(restored, record.options.audio_filter)]
        if cues != read_json(work / "transcript.json"):
            raise ValueError("Retained transcript does not match the completed legacy receipts")
        if apply:
            if (work / "completed-transcript.json").exists():
                raise ValueError("Completed transcript receipt already exists; refusing overwrite")
            save_completed_transcript(work, transcript_identity(source, record), cues, model)
        return {"job_id": job_id, "verified_cues": len(cues), "model": model, "applied": apply}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--legacy-model", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(recover(FilesystemJobRepository(Path(os.environ["VIDEO_STORAGE_ROOT"])),
                             args.job_id, args.legacy_model, args.apply)))
