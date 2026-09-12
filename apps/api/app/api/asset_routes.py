from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import routes
from app.core.config import settings
from video_service.assets import AssetConflictError, AssetNotFoundError, VideoAssetRepository
from video_service.capacity import StorageLimitError, assert_capacity, remaining_reservations
from video_service.locking import job_lock


router = APIRouter(prefix='/api', dependencies=[Depends(routes.authorize_job_request)])


@contextmanager
def asset_errors():
    try:
        yield
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Video not found.') from exc
    except AssetConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorageLimitError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail='Video registration could not be persisted.') from exc


def reserve_copy(size):
    repo = routes.repository
    assert_capacity(repo.storage_root, settings.service_quota_bytes,
                    settings.min_free_space_bytes, remaining_reservations(repo) + size)


@router.get('/videos')
def list_videos(request: Request):
    return {'videos': VideoAssetRepository(routes.repository).list(owner_id=request.state.owner_id)}


@router.get('/videos/{asset_id}')
def get_video(asset_id: str, request: Request):
    with asset_errors():
        asset = VideoAssetRepository(routes.repository).read(asset_id, owner_id=request.state.owner_id)
        jobs = [routes.job_to_response(job) for job in routes.repository.list()
                if job.metadata.get('asset_id') == asset_id
                and job.metadata.get('owner_id') == request.state.owner_id]
        return {'video': asset, 'jobs': jobs}


@router.post('/jobs/{job_id}/register-source')
def register_source(job_id: str, request: Request):
    with asset_errors(), job_lock(routes.repository, '0' * 32):
        return VideoAssetRepository(routes.repository).register_upload(job_id,
            owner_id=request.state.owner_id, before_copy=reserve_copy)


@router.post('/jobs/{job_id}/promote-video')
def promote_video(job_id: str, request: Request):
    with asset_errors(), job_lock(routes.repository, '0' * 32):
        return VideoAssetRepository(routes.repository).promote_result(job_id,
            owner_id=request.state.owner_id, before_copy=reserve_copy)


@router.delete('/videos/{asset_id}', status_code=204)
def delete_video(asset_id: str, request: Request):
    with asset_errors(), job_lock(routes.repository, '0' * 32):
        VideoAssetRepository(routes.repository).delete(asset_id, owner_id=request.state.owner_id)
