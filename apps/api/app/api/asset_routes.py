from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api import routes
from app.core.config import settings
from video_service.assets import AssetConflictError, AssetNotFoundError, VideoAssetRepository
from video_service.capacity import StorageLimitError, assert_capacity, remaining_reservations
from video_service.locking import job_lock
from video_service.artifacts import SubtitleArtifactRepository
from video_service.workflows import WorkflowTemplate, workflow_plan
from video_service.models import JobStatus
from app.services.final_subtitles import read_final_draft


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


class SubtitleAttachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=255)
    language: str = Field(default='auto', max_length=40)
    content: str = Field(min_length=1, max_length=4 * 1024 * 1024)


class WorkflowPlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template: WorkflowTemplate
    subtitle_artifact_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    subtitle_mode: str = 'burn'


class JobSubtitleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    track: str = Field(pattern=r'^(original|translated(?:\.[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)?)$')
    revision: int = Field(ge=0)


@router.post('/videos/{asset_id}/subtitle-inputs', status_code=201)
def import_job_subtitle(asset_id: str, payload: JobSubtitleInput, request: Request):
    with asset_errors(), job_lock(routes.repository, '0' * 32), \
            job_lock(routes.repository, payload.job_id, 'execution'), job_lock(routes.repository, payload.job_id):
        assets = VideoAssetRepository(routes.repository)
        assets.read(asset_id, owner_id=request.state.owner_id)
        job = routes.repository.read(payload.job_id)
        if job.metadata.get('owner_id') != request.state.owner_id or job.metadata.get('asset_id') != asset_id:
            raise HTTPException(status_code=404, detail='Subtitle job not found for this video.')
        if job.status != JobStatus.COMPLETED or job.metadata.get('results_expired_at'):
            raise HTTPException(status_code=409, detail='Completed subtitles are required.')
        if job.metadata.get('subtitle_revision', 0) != payload.revision:
            raise HTTPException(status_code=409, detail='Subtitle revision changed. Select it again.')
        try:
            draft = read_final_draft(routes.repository, job)
            if payload.track not in draft['tracks']:
                raise HTTPException(status_code=404, detail='Subtitle track not found.')
            return SubtitleArtifactRepository(assets).publish(asset_id, owner_id=request.state.owner_id,
                filename=payload.track + '.srt', language=draft['languages'][payload.track],
                cues=draft['tracks'][payload.track], before_write=reserve_copy,
                provenance={'kind': 'job_subtitle', 'job_id': job.job_id,
                            'revision': payload.revision, 'track': payload.track})
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail='Subtitle files are unavailable.') from exc
        except (StorageLimitError, AssetNotFoundError):
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/videos/{asset_id}/subtitles')
def list_subtitles(asset_id: str, request: Request):
    with asset_errors():
        artifacts = SubtitleArtifactRepository(VideoAssetRepository(routes.repository))
        return {'subtitles': artifacts.list(asset_id, owner_id=request.state.owner_id)}


@router.get('/videos/{asset_id}/subtitles/{artifact_id}')
def get_subtitle(asset_id: str, artifact_id: str, request: Request):
    with asset_errors():
        return SubtitleArtifactRepository(VideoAssetRepository(routes.repository)).read(
            asset_id, artifact_id, owner_id=request.state.owner_id)


@router.post('/videos/{asset_id}/subtitles', status_code=201)
def attach_subtitle(asset_id: str, payload: SubtitleAttachment, request: Request):
    with asset_errors(), job_lock(routes.repository, '0' * 32):
        try:
            return SubtitleArtifactRepository(VideoAssetRepository(routes.repository)).attach_srt(
                asset_id, owner_id=request.state.owner_id, filename=payload.filename,
                language=payload.language, content=payload.content, before_write=reserve_copy)
        except (AssetNotFoundError, StorageLimitError):
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/videos/{asset_id}/workflow-plan')
def preview_workflow(asset_id: str, payload: WorkflowPlanRequest, request: Request):
    with asset_errors():
        assets = VideoAssetRepository(routes.repository)
        assets.read(asset_id, owner_id=request.state.owner_id)
        if payload.subtitle_artifact_id:
            SubtitleArtifactRepository(assets).read(asset_id, payload.subtitle_artifact_id,
                                                    owner_id=request.state.owner_id)
        try:
            return workflow_plan(payload.template, subtitle_input=bool(payload.subtitle_artifact_id),
                                 subtitle_mode=payload.subtitle_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
