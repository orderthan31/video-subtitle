from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, UUID4

from video_service.models import JobRecord, JobStatus, QualityProfile


class UploadCreateRequest(BaseModel):
    request_id: UUID4 | None = None
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    source_language: str = "auto"
    target_language: str = Field(default="ko", pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    quality_profile: QualityProfile = QualityProfile.BALANCED
    video_codec: Literal["hevc", "h264"] = "hevc"
    subtitle_mode: Literal["burn", "soft"] = "burn"
    resolution: Literal["original", "1080p", "720p"] = "original"


class UploadCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    uploaded_bytes: int
    expected_size: int


class UploadStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    uploaded_bytes: int
    expected_size: int
    resumable: bool


class ChunkUploadResponse(BaseModel):
    job_id: str
    uploaded_bytes: int
    expected_size: int


class UploadVerifyRequest(BaseModel):
    uploaded_bytes: int = Field(gt=0)
    offset: int = Field(ge=0)
    length: int = Field(gt=0, le=4 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    original_filename: str
    expected_size: int
    uploaded_bytes: int
    source_language: str
    target_language: str
    quality_profile: str
    video_codec: str
    subtitle_mode: str
    resolution: str
    status_message: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    completed_at: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


def job_to_response(record: JobRecord) -> JobResponse:
    return JobResponse(
        job_id=record.job_id,
        status=record.status,
        original_filename=record.original_filename,
        expected_size=record.expected_size,
        uploaded_bytes=record.uploaded_bytes,
        source_language=record.options.source_language,
        target_language=record.options.target_language,
        quality_profile=record.options.quality_profile.value,
        video_codec=record.options.video_codec,
        subtitle_mode=record.options.subtitle_mode,
        resolution=record.options.resolution,
        status_message=record.status_message,
        error=record.error,
        metadata=record.metadata,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )
