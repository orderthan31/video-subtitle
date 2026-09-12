"""Versioned subtitle drafts shared by the API and media worker."""
import math

from .storage import read_json, write_json_atomic
from .subtitles import segments_to_srt, segment_subtitles
from .transcript import TranscriptSegment


def track_languages(options):
    return {"original": options.source_language, "translated": options.target_language,
        **{f"translated.{language}": language for language in options.additional_languages}}


def validate_tracks(tracks, options, duration, *, allow_empty=False, languages=None, allow_imported_layout=False):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid subtitle media duration")
    if set(tracks) != set(track_languages(options) if languages is None else languages):
        raise ValueError("Subtitle tracks do not match job languages")
    for cues in tracks.values():
        if not isinstance(cues, list) or not (0 if allow_empty else 1) <= len(cues) <= 10000:
            raise ValueError("Each subtitle track requires 1 to 10000 cues")
        previous_end = 0
        segments = []
        for cue in cues:
            segment = TranscriptSegment.from_dict(cue)
            if not (math.isfinite(segment.start) and math.isfinite(segment.end)
                    and 0 <= segment.start < segment.end <= duration + 0.05):
                raise ValueError("Subtitle outside media timeline")
            if segment.start < previous_end - 0.001:
                raise ValueError("Subtitle cues overlap")
            if not segment.text.strip() or len(segment.text) > 1000 or (not allow_imported_layout and len(segment.text.splitlines()) > 2):
                raise ValueError("Subtitle text must contain at most two nonempty lines and 1000 characters")
            previous_end = segment.start if allow_imported_layout else segment.end
            segments.append(segment)
        segments_to_srt(segments)


def draft_path(repository, job_id):
    return repository.job_dir(job_id) / "work/subtitle-draft.json"


def read_draft(repository, record):
    draft = read_json(draft_path(repository, record.job_id))
    validate_tracks(draft["tracks"], record.options, float(draft["duration"]))
    return draft


def write_draft(repository, record, tracks, duration, revision, *, capacity_check=None):
    validate_tracks(tracks, record.options, duration)
    languages = track_languages(record.options)
    normalized = {}
    for name, language in languages.items():
        width = 24 if language.split("-")[0] in {"auto", "ko", "ja", "zh"} else 42
        normalized[name] = [cue.to_dict() for cue in segment_subtitles(
            [TranscriptSegment.from_dict(cue) for cue in tracks[name]], line_width=width)]
    validate_tracks(normalized, record.options, duration)
    draft = {"revision": revision, "duration": duration, "languages": languages, "tracks": normalized}
    write_json_atomic(draft_path(repository, record.job_id), draft, before_write=capacity_check)
    return draft
