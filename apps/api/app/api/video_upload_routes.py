from functools import partial
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, UUID4

from app.api import routes
from app.api.asset_routes import asset_errors
from app.core.config import settings
from app.schemas.jobs import UploadVerifyRequest
from app.services.upload_service import UploadConflictError, UploadService
from video_service.assets import VideoAssetRepository
from video_service.capacity import StorageLimitError, assert_capacity, assert_free_space, remaining_reservations
from video_service.locking import job_lock
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from video_service.video_probe import inspect_video


router = APIRouter(prefix='/api/video-uploads', dependencies=[Depends(routes.authorize_job_request)])


class VideoUploadCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID4
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


def upload_repository():
    return FilesystemJobRepository(routes.repository.storage_root / '.uploads')


def owned_upload(upload_id, request):
    record = upload_repository().read(upload_id)
    if record.metadata.get('owner_id') != request.state.owner_id:
        raise HTTPException(status_code=404, detail='Upload not found.')
    return record


def summary(record):
    return {'upload_id': record.job_id, 'filename': record.original_filename,
            'status': record.status.value, 'uploaded_bytes': record.uploaded_bytes,
            'expected_size': record.expected_size, 'asset_id': record.metadata.get('asset_id'),
            'created_at': record.created_at, 'resumable': record.status == JobStatus.UPLOADING}


@router.post('', status_code=201)
def create_upload(payload: VideoUploadCreate, request: Request):
    repo = upload_repository()
    with asset_errors(), job_lock(routes.repository, '0' * 32):
        record = next((r for r in repo.list() if r.metadata.get('upload_request_id') == str(payload.request_id)
                       and r.metadata.get('owner_id') == request.state.owner_id), None)
        if record:
            if (record.original_filename, record.expected_size) != (payload.filename, payload.size):
                raise HTTPException(status_code=409, detail='Upload request ID already used.')
        else:
            if payload.size > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail='Video exceeds upload limit.')
            reservation = payload.size * 2 + 4096
            assert_capacity(routes.repository.storage_root, settings.service_quota_bytes,
                settings.min_free_space_bytes, remaining_reservations(routes.repository) + reservation)
            record = repo.create_job(original_filename=payload.filename, expected_size=payload.size,
                source_language='auto', target_language='ko', quality_profile=QualityProfile.BALANCED,
                metadata={'kind': 'video_upload', 'owner_id': request.state.owner_id,
                          'upload_request_id': str(payload.request_id), 'reserved_bytes': reservation})
        return summary(record)


@router.get('')
def list_uploads(request: Request):
    return {'uploads': [summary(r) for r in upload_repository().list()
                       if r.metadata.get('owner_id') == request.state.owner_id]}


@router.get('/{upload_id}')
def get_upload(upload_id: str, request: Request):
    repo = upload_repository()
    with job_lock(repo, upload_id):
        record = owned_upload(upload_id, request)
        source = repo.source_path(record)
        if record.status == JobStatus.UPLOADING:
            record.uploaded_bytes = source.stat().st_size if source.exists() else 0
        return summary(record)


@router.delete('/{upload_id}', status_code=204)
def delete_upload(upload_id: str, request: Request):
    repo = upload_repository()
    with job_lock(routes.repository, '0' * 32), job_lock(repo, upload_id, 'execution'), job_lock(repo, upload_id):
        record = owned_upload(upload_id, request)
        if record.metadata.get('asset_id'):
            raise HTTPException(status_code=409, detail='Delete the registered video instead.')
        # A failed link write may have published a video already. Preserve its session.
        assets = VideoAssetRepository(routes.repository)
        with assets.lock():
            if any(a.get('provenance', {}).get('upload_id') == upload_id
                   for a in assets.list(owner_id=request.state.owner_id)):
                raise HTTPException(status_code=409, detail='Finish video registration before deleting.')
            repo.delete_job_dir(upload_id)


@router.put('/{upload_id}/chunks')
async def upload_chunk(upload_id: str, request: Request, offset: int = Query(ge=0)):
    owned_upload(upload_id, request)
    repo = upload_repository()
    service = UploadService(repo, partial(assert_free_space, routes.repository.storage_root,
                                         settings.min_free_space_bytes))
    with asset_errors():
        try:
            await service.append_chunk(job_id=upload_id, expected_offset=offset, body=request.stream())
        except UploadConflictError as exc:
            raise HTTPException(status_code=409, detail={'expected_offset': exc.expected_offset}) from exc
        except StorageLimitError:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return summary(repo.read(upload_id))


@router.post('/{upload_id}/verify', status_code=204)
def verify_upload(upload_id: str, payload: UploadVerifyRequest, request: Request):
    repo = upload_repository()
    with job_lock(repo, upload_id):
        record = owned_upload(upload_id, request)
        source = repo.source_path(record)
        if record.status != JobStatus.UPLOADING or not source.exists() or source.stat().st_size != payload.uploaded_bytes:
            raise HTTPException(status_code=409, detail='Upload offset changed.')
        if payload.offset + payload.length > payload.uploaded_bytes:
            raise HTTPException(status_code=400, detail='Invalid verification range.')
        with source.open('rb') as stream:
            stream.seek(payload.offset)
            digest = hashlib.sha256(stream.read(payload.length)).hexdigest()
        if digest != payload.sha256:
            raise HTTPException(status_code=422, detail='Uploaded video does not match the selected file.')


@router.post('/{upload_id}/complete')
def complete_upload(upload_id: str, request: Request):
    repo = upload_repository()
    with asset_errors(), job_lock(routes.repository, '0' * 32):
        with job_lock(repo, upload_id):
            record = owned_upload(upload_id, request)
            if record.status == JobStatus.UPLOADING:
                try:
                    UploadService(repo)._complete(upload_id)
                except UploadConflictError as exc:
                    raise HTTPException(status_code=409, detail={'expected_offset': exc.expected_offset}) from exc
        def reserve_copy(size):
            assert_capacity(routes.repository.storage_root, settings.service_quota_bytes,
                settings.min_free_space_bytes, remaining_reservations(routes.repository, {upload_id: 0}) + size)
        asset = VideoAssetRepository(routes.repository).register_upload(upload_id,
            owner_id=request.state.owner_id, before_copy=reserve_copy, upload_repository=repo, inspect_source=inspect_video)
        return {'status': 'ready', 'video': asset}
