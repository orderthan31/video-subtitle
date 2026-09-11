from __future__ import annotations

from functools import partial
import hashlib
import errno

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.core.config import settings
from app.api.auth_routes import require_session
from app.schemas.jobs import (
    ChunkUploadResponse,
    JobListResponse,
    UploadCreateRequest,
    UploadCreateResponse,
    UploadStatusResponse,
    UploadVerifyRequest,
    SubtitleUpdate,
    job_to_response,
)
from app.services.storage_guard import StorageGuard, StorageLimitError
from app.services.upload_service import UploadConflictError, UploadService
from app.services.result_response import ResultResponse
from video_service.models import JobStatus, TERMINAL_STATUSES
from video_service.locking import job_lock
from video_service.repository import FilesystemJobRepository, JobNotFoundError
from video_service.capacity import assert_capacity, remaining_reservations, used_bytes
from video_service.review import read_draft, write_draft

def authorize_job_request(request: Request):
    if request.scope.get("route").path == "/api/health":
        return
    session = require_session(request) if request.app.state.auth.store is not None else None
    request.state.owner_id = session["user"]["id"] if session else None
    job_id = request.path_params.get("job_id")
    if job_id is not None:
        record = _read_job_or_404(job_id)
        if record.metadata.get("owner_id") != request.state.owner_id:
            raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")


router = APIRouter(prefix="/api", dependencies=[Depends(authorize_job_request)])
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
def create_upload(payload: UploadCreateRequest, request: Request) -> UploadCreateResponse:
    try:
        with job_lock(repository, "0" * 32):
            request_id = str(payload.request_id) if payload.request_id else None
            record = next((job for job in repository.list()
                if job.metadata.get("upload_request_id") == request_id
                and job.metadata.get("owner_id") == request.state.owner_id), None) if request_id else None
            if record is not None:
                actual = (record.original_filename, record.expected_size, record.options.source_language,
                    record.options.target_language, record.options.quality_profile, record.options.video_codec,
                    record.options.subtitle_mode, record.options.resolution, record.options.additional_languages,
                    record.options.audio_filter, record.options.review_subtitles)
                expected = (payload.filename, payload.size, payload.source_language,
                    payload.target_language, payload.quality_profile, payload.video_codec, payload.subtitle_mode,
                    payload.resolution, payload.additional_languages, payload.audio_filter, payload.review_subtitles)
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
                    video_codec=payload.video_codec,
                    subtitle_mode=payload.subtitle_mode,
                    resolution=payload.resolution,
                    additional_languages=payload.additional_languages,
                    audio_filter=payload.audio_filter,
                    review_subtitles=payload.review_subtitles,
                    metadata={"upload_request_id": request_id, "owner_id": request.state.owner_id},
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
def list_jobs(request: Request) -> JobListResponse:
    return JobListResponse(jobs=[job_to_response(record) for record in repository.list()
        if record.metadata.get("owner_id") == request.state.owner_id])


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return job_to_response(_read_job_or_404(job_id))


@router.get("/jobs/{job_id}/subtitles")
def get_subtitle_draft(job_id: str):
    with job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        if record.status != JobStatus.AWAITING_REVIEW:
            raise HTTPException(status_code=409, detail="자막 검토 대기 상태가 아닙니다.")
        return read_draft(repository, record)


@router.put("/jobs/{job_id}/subtitles")
def update_subtitle_draft(job_id: str, payload: SubtitleUpdate):
    with job_lock(repository, job_id, "execution"), job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        if record.status != JobStatus.AWAITING_REVIEW:
            raise HTTPException(status_code=409, detail="자막 검토 대기 상태가 아닙니다.")
        current = read_draft(repository, record)
        if current["revision"] != payload.revision:
            raise HTTPException(status_code=409, detail="다른 창에서 자막이 변경되었습니다. 최신 자막을 다시 불러오세요.")
        tracks = {name: [cue.model_dump(exclude_none=True) for cue in cues] for name, cues in payload.tracks.items()}
        try:
            with job_lock(repository, "0" * 32):
                draft = write_draft(repository, record, tracks, current["duration"], current["revision"] + 1,
                    capacity_check=lambda size: assert_capacity(repository.storage_root,
                        settings.service_quota_bytes, settings.min_free_space_bytes, additional=size))
        except StorageLimitError as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except OSError as exc:
            if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == 112:
                raise HTTPException(status_code=507, detail="초안을 저장할 디스크 공간이 부족합니다.") from exc
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.action == "render":
            repository.update_status(job_id, JobStatus.QUEUED, metadata={"review_ready": True})
        return {**draft, "status": "QUEUED" if payload.action == "render" else "AWAITING_REVIEW"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    with job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        if record.status not in TERMINAL_STATUSES:
            if record.status in {JobStatus.UPLOADING, JobStatus.QUEUED, JobStatus.AWAITING_REVIEW}:
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
        if repository.preserve_artifacts:
            raise HTTPException(status_code=409, detail="개발 중 파일 보존 설정이 켜져 있어 삭제할 수 없습니다.")
        repository.delete_job_dir(job_id)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, str]:
    with job_lock(repository, job_id, "execution"), job_lock(repository, "0" * 32), job_lock(repository, job_id):
        record = _read_job_or_404(job_id)
        if record.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="실패하거나 중단된 작업만 재시도할 수 있습니다.")
        source = repository.source_path(record)
        if not source.is_file() or source.stat().st_size != record.expected_size or record.uploaded_bytes != record.expected_size:
            raise HTTPException(status_code=409, detail="완전한 원본 파일이 없어 업로드를 다시 진행해야 합니다.")
        reserved = record.metadata.get("reserved_bytes", record.expected_size * 4)
        assert_capacity(repository.storage_root, settings.service_quota_bytes, settings.min_free_space_bytes,
            remaining_reservations(repository) + max(0, reserved - used_bytes(repository.job_dir(job_id))))
        record.metadata.update(cancel_requested=False, interrupted=False, cleanup_pending=False,
            cleanup_error=None, stage_progress=None, retry_count=record.metadata.get("retry_count", 0) + 1)
        record.completed_at = None
        repository.save(record)
        repository.update_status(job_id, JobStatus.QUEUED, message="보존된 체크포인트에서 재시도합니다.")
        return {"status": "queued"}


@router.get("/jobs/{job_id}/results/{filename}")
def download_result(job_id: str, filename: str) -> FileResponse:
    _read_job_or_404(job_id)
    return ResultResponse(repository, job_id, filename)
