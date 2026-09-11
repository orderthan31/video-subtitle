from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(StrEnum):
    UPLOADING = "UPLOADING"
    READY = "READY"
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    EXTRACTING_AUDIO = "EXTRACTING_AUDIO"
    PREPROCESSING_AUDIO = "PREPROCESSING_AUDIO"
    TRANSCRIBING = "TRANSCRIBING"
    FILTERING_TRANSCRIPT = "FILTERING_TRANSCRIPT"
    TRANSLATING = "TRANSLATING"
    GENERATING_SUBTITLE = "GENERATING_SUBTITLE"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    ENCODING = "ENCODING"
    VALIDATING = "VALIDATING"
    CLEANING = "CLEANING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.ANALYZING,
    JobStatus.EXTRACTING_AUDIO,
    JobStatus.PREPROCESSING_AUDIO,
    JobStatus.TRANSCRIBING,
    JobStatus.FILTERING_TRANSCRIPT,
    JobStatus.TRANSLATING,
    JobStatus.GENERATING_SUBTITLE,
    JobStatus.AWAITING_REVIEW,
    JobStatus.ENCODING,
    JobStatus.VALIDATING,
    JobStatus.CLEANING,
}

TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


class QualityProfile(StrEnum):
    BALANCED = "balanced"
    HIGH = "high"
    COMPACT = "compact"


def validate_additional_languages(primary, languages):
    if not isinstance(languages, list) or len(languages) > 4:
        raise ValueError("At most four additional languages are supported")
    for language in languages:
        if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
            raise ValueError("Invalid additional language")
    normalized = [language.lower() for language in [primary, *languages]]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Translation languages must be unique")


@dataclass(slots=True)
class JobOptions:
    source_language: str = "auto"
    target_language: str = "ko"
    quality_profile: QualityProfile = QualityProfile.BALANCED
    video_codec: str = "hevc"
    subtitle_mode: str = "burn"
    resolution: str = "original"
    additional_languages: list[str] = field(default_factory=list)
    audio_filter: str = "conservative"
    review_subtitles: bool = False

    def __post_init__(self):
        validate_additional_languages(self.target_language, self.additional_languages)
        if self.audio_filter not in {"off", "conservative", "strong"}:
            raise ValueError("Unsupported audio filter")
        if self.video_codec not in {"hevc", "h264"}:
            raise ValueError("Unsupported video codec")
        if self.subtitle_mode not in {"burn", "soft"}:
            raise ValueError("Unsupported subtitle mode")
        if self.resolution not in {"original", "1080p", "720p"}:
            raise ValueError("Unsupported resolution")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JobOptions":
        data = data or {}
        return cls(
            source_language=str(data.get("source_language", "auto")),
            target_language=str(data.get("target_language", "ko")),
            quality_profile=QualityProfile(data.get("quality_profile", QualityProfile.BALANCED)),
            video_codec=str(data.get("video_codec", "hevc")),
            subtitle_mode=str(data.get("subtitle_mode", "burn")),
            resolution=str(data.get("resolution", "original")),
            additional_languages=data.get("additional_languages", []),
            audio_filter=str(data.get("audio_filter", "conservative")),
            review_subtitles=bool(data.get("review_subtitles", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "quality_profile": self.quality_profile.value,
            "video_codec": self.video_codec,
            "subtitle_mode": self.subtitle_mode,
            "resolution": self.resolution,
            "additional_languages": list(self.additional_languages),
            "audio_filter": self.audio_filter,
            "review_subtitles": self.review_subtitles,
        }


@dataclass(slots=True)
class JobRecord:
    job_id: str
    status: JobStatus
    original_filename: str
    source_filename: str
    expected_size: int
    uploaded_bytes: int = 0
    options: JobOptions = field(default_factory=JobOptions)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    status_message: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    heartbeat_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        return cls(
            job_id=str(data["job_id"]),
            status=JobStatus(data["status"]),
            original_filename=str(data["original_filename"]),
            source_filename=str(data["source_filename"]),
            expected_size=int(data["expected_size"]),
            uploaded_bytes=int(data.get("uploaded_bytes", 0)),
            options=JobOptions.from_dict(data.get("options")),
            metadata=dict(data.get("metadata", {})),
            error=data.get("error"),
            status_message=data.get("status_message"),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            completed_at=data.get("completed_at"),
            heartbeat_at=data.get("heartbeat_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "original_filename": self.original_filename,
            "source_filename": self.source_filename,
            "expected_size": self.expected_size,
            "uploaded_bytes": self.uploaded_bytes,
            "options": self.options.to_dict(),
            "metadata": self.metadata,
            "error": self.error,
            "status_message": self.status_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "heartbeat_at": self.heartbeat_at,
        }

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def heartbeat(self) -> None:
        now = utc_now_iso()
        self.updated_at = now
        self.heartbeat_at = now
