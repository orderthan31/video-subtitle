from __future__ import annotations

from functools import partial
import hashlib

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.core.config import settings
from app.schemas.jobs import (
    ChunkUploadResponse,
    JobListResponse,
    UploadCreateRequest,
    UploadCreateResponse,
    UploadStatusResponse,
    UploadVerifyRequest,
    job_to_response,
)
from app.services.storage_guard import StorageGuard, StorageLimitError
from app.services.upload_service import UploadConflictError, UploadService
from app.services.result_response import ResultResponse
from video_service.models import JobStatus, TERMINAL_STATUSES
from video_service.locking import job_lock
from video_service.repository import FilesystemJobRepository, JobNotFoundError
from video_service.capacity import assert_capacity

router = APIRouter(prefix="/api")
repository = FilesystemJobRepository(settings.storage_root)
upload_service = UploadService(repository, partial(assert_capacity, settings.storage_root,
    settings.service_quota_bytes, settings.min_free_space_bytes))
storage_guard = StorageGuard(
    root=settings.storage_root,
    max_upload_bytes=settings.max_upload_bytes,
    quota_bytes=settings.service_quota_bytes,
    min_free_space_bytes=settings.min_free_space_bytes,
)


def _read_job_or_404(job_id: str):
    try:
        return repository.read(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job을 찾을 수 없습니다.") from exc


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/uploads", response_model=UploadCreateResponse, status_code=status.HTTP_201_CREATED)
def create_upload(payload: UploadCreateRequest) -> UploadCreateResponse:
    try:
        with job_lock(repository, "0" * 32):
            request_id = str(payload.request_id) if payload.request_id else None
            record = next((job for job in repository.list()
                if job.metadata.get("upload_request_id") == request_id), None) if request_id else None
            if record is not None:
                actual = (record.original_filename, record.expected_size, record.options.source_language,
                    record.options.target_language, record.options.quality_profile)
                expected = (payload.filename, payload.size, payload.source_language,
                    payload.target_language, payload.quality_profile)
                if actual != expected:
                    raise HTTPException(status_code=409, detail="생성 요청 식별자가 다른 업로드 설정에 사용되었습니다.")
            else:
                storage_guard.assert_can_accept_upload(payload.size)
                record = repository.create_job(
                    original_filename=payload.filename,
                    expected_size=payload.size,
                    source_language=payload.source_language,
                    target_language=payload.target_language,
                    quality_profile=payload.quality_profile,
                    metadata={"upload_request_id": request_id} if request_id else None,
                )
    except StorageLimitError as exc:
        raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail=str(exc)) from exc

    return UploadCreateResponse(
        job_id=record.job_id,
        status=record.status,
        uploaded_bytes=record.uploaded_bytes,
        expected_size=record.expected_size,
    )


@router.get("/uploads/{job_id}", response_model=UploadStatusResponse)
def get_upload_status(job_id: str) -> UploadStatusResponse:
    record = _read_job_or_404(job_id)
    if record.status == JobStatus.UPLOADING:
        with job_lock(repository, job_id):
            record = _read_job_or_404(job_id)
            source = repository.source_path(record)
            record.uploaded_bytes = source.stat().st_size if source.exists() else 0
    return UploadStatusResponse(
        job_id=record.job_id,
        status=record.status,
        uploaded_bytes=record.uploaded_bytes,
        expected_size=record.expected_size,
        resumable=record.status == JobStatus.UPLOADING,
    )


@router.post("/uploads/{job_id}/verify", status_code=status.HTTP_204_NO_CONTENT)
def verify_upload_block(job_id: str, payload: UploadVerifyRequest) -> None:
    with job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        source = repository.source_path(record)
        if record.status != JobStatus.UPLOADING or not source.exists():
            raise HTTPException(status_code=409, detail="업로드 상태가 변경되었습니다.")
        if source.stat().st_size != payload.uploaded_bytes:
            raise HTTPException(status_code=409, detail="업로드 크기가 변경되었습니다.")
        if payload.offset + payload.length > payload.uploaded_bytes:
            raise HTTPException(status_code=400, detail="검증 범위가 업로드 크기를 초과합니다.")
        with source.open("rb") as stream:
            stream.seek(payload.offset)
            digest = hashlib.sha256(stream.read(payload.length)).hexdigest()
        if digest != payload.sha256:
            raise HTTPException(status_code=422, detail="저장된 영상과 파일 내용이 다릅니다. 원본 파일을 다시 선택하세요.")


@router.put("/uploads/{job_id}/chunks", response_model=ChunkUploadResponse)
async def upload_chunk(
    job_id: str,
    request: Request,
    offset: int = Query(ge=0),
) -> ChunkUploadResponse:
    try:
        uploaded_bytes = await upload_service.append_chunk(
            job_id=job_id,
            expected_offset=offset,
            body=request.stream(),
        )
        record = repository.read(job_id)
    except StorageLimitError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except UploadConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "expected_offset": exc.expected_offset},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job을 찾을 수 없습니다.") from exc

    return ChunkUploadResponse(
        job_id=job_id,
        uploaded_bytes=uploaded_bytes,
        expected_size=record.expected_size,
    )


@router.post("/uploads/{job_id}/complete")
def complete_upload(job_id: str) -> dict[str, str]:
    try:
        upload_service.complete(job_id)
    except UploadConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "expected_offset": exc.expected_offset},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job을 찾을 수 없습니다.") from exc
    return {"status": "queued"}


@router.get("/jobs", response_model=JobListResponse)
def list_jobs() -> JobListResponse:
    return JobListResponse(jobs=[job_to_response(record) for record in repository.list()])


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return job_to_response(_read_job_or_404(job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    with job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        if record.status not in TERMINAL_STATUSES:
            if record.status in {JobStatus.UPLOADING, JobStatus.QUEUED}:
                record = repository.update_status(job_id, JobStatus.CANCELLED, message="사용자가 작업을 취소했습니다.")
            else:
                record.metadata["cancel_requested"] = True
                repository.save(record)
                return {"status": "cancelling"}
        return {"status": record.status.value.lower()}


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str) -> None:
    with job_lock(repository, job_id, "execution"), job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        if record.status not in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="작업을 취소한 후 삭제하세요.")
        repository.delete_job_dir(job_id)


@router.get("/jobs/{job_id}/results/{filename}")
def download_result(job_id: str, filename: str) -> FileResponse:
    _read_job_or_404(job_id)
    return ResultResponse(repository, job_id, filename)
