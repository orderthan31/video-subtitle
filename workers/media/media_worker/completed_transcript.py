"""Completed, original-clock transcripts survive model changes on job retry."""
import logging

from video_service.artifacts import cue_digest
from video_service.storage import read_json, write_json_atomic


def transcript_identity(source, record):
    stat = source.stat()
    return {"version": 1, "source_size": stat.st_size, "source_mtime": stat.st_mtime_ns,
            "options": record.options.to_dict(),
            "audio_input": record.metadata.get("audio_input")}


def save_completed_transcript(work, identity, cues, model, before_write=None):
    write_json_atomic(work / "completed-transcript.json", {
        "identity": identity, "coordinate_system": "original", "model": model,
        "cues": cues, "sha256": cue_digest(cues)}, before_write=before_write)


def load_completed_transcript(work, identity):
    path = work / "completed-transcript.json"
    if not path.exists():
        if (work / "transcript.json").exists():
            raise ValueError("Existing transcript needs verified legacy recovery; refusing paid re-transcription")
        return None
    saved = read_json(path)
    if saved["coordinate_system"] != "original" or cue_digest(saved["cues"]) != saved["sha256"]:
        raise ValueError("Completed transcript integrity check failed; refusing paid re-transcription")
    if saved["identity"] != identity:
        return None
    logging.info("Reusing completed original-clock transcript: model=%s cues=%s", saved["model"], len(saved["cues"]))
    return saved["cues"]
