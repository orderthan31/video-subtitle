from typing import Literal
from uuid import uuid4
from contextlib import ExitStack
import hashlib
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, UUID4

from app.api import routes
from app.api.asset_routes import asset_errors
from app.core.config import settings
from video_service.artifacts import SubtitleArtifactRepository
from video_service.assets import VideoAssetRepository
from video_service.capacity import assert_capacity, remaining_reservations
from video_service.locking import job_lock
from video_service.models import JobOptions, JobRecord, JobStatus, utc_now_iso
from video_service.workflows import WorkflowTemplate, workflow_plan


router = APIRouter(prefix='/api/videos', dependencies=[Depends(routes.authorize_job_request)])


class WorkflowOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_language: str = Field(default='auto', pattern=r'^(auto|[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$')
    target_language: str = Field(default='ko', pattern=r'^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$')
    quality_profile: Literal['balanced', 'high', 'compact'] = 'balanced'
    video_codec: Literal['hevc', 'h264'] = 'hevc'
    subtitle_mode: Literal['burn', 'soft', 'none'] = 'burn'
    resolution: Literal['original', '1080p', '720p'] = 'original'
    audio_filter: Literal['off', 'conservative', 'strong', 'silence3'] = 'silence3'
    vad_mode: Literal['off', 'nvidia'] = 'off'
    video_description: str = Field(default='', max_length=2000)


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID4
    template: WorkflowTemplate
    subtitle_artifact_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    audio_job_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    options: WorkflowOptions = Field(default_factory=WorkflowOptions)


@router.post('/{asset_id}/jobs', status_code=201)
def create_workflow(asset_id: str, payload: WorkflowCreate, request: Request):
    repo = routes.repository
    assets = VideoAssetRepository(repo)
    owner = request.state.owner_id
    with asset_errors(), job_lock(repo, '0' * 32), assets.lock(), ExitStack() as input_locks:
        asset = assets.read(asset_id, owner_id=owner)
        identity = payload.model_dump(mode='json')
        existing = next((r for r in repo.list() if r.metadata.get('owner_id') == owner
                         and r.metadata.get('workflow_request_id') == str(payload.request_id)), None)
        if existing:
            if existing.metadata.get('asset_id') != asset_id or existing.metadata.get('workflow_request') != identity:
                raise HTTPException(status_code=409, detail='Workflow request ID already used.')
            return routes.job_to_response(existing)
        try:
            options = JobOptions.from_dict(payload.options.model_dump())
            plan = workflow_plan(payload.template, subtitle_input=bool(payload.subtitle_artifact_id),
                                 subtitle_mode=options.subtitle_mode, audio_input=bool(payload.audio_job_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source = assets.source_path(asset)
        if not source.is_file() or source.stat().st_size != asset['size_bytes']:
            raise HTTPException(status_code=409, detail='Original video is unavailable.')
        artifacts = SubtitleArtifactRepository(assets)
        if payload.subtitle_artifact_id:
            artifacts.read(asset_id, payload.subtitle_artifact_id, owner_id=owner)
        audio_source = None
        if payload.audio_job_id:
            input_locks.enter_context(job_lock(repo, payload.audio_job_id, 'execution'))
            input_locks.enter_context(job_lock(repo, payload.audio_job_id))
            audio_job = repo.read(payload.audio_job_id)
            if audio_job.metadata.get('owner_id') != owner or audio_job.metadata.get('asset_id') != asset_id:
                raise HTTPException(status_code=404, detail='Audio job not found for this video.')
            audio_source = repo.job_dir(audio_job.job_id) / 'work/audio.wav'
            if audio_job.status != JobStatus.COMPLETED or audio_job.metadata.get('results_expired_at') or not audio_source.is_file():
                raise HTTPException(status_code=409, detail='Completed extracted audio is unavailable.')
        reservation = asset['size_bytes'] * 4 + 1024**2 + (audio_source.stat().st_size if audio_source else 0)
        assert_capacity(repo.storage_root, settings.service_quota_bytes, settings.min_free_space_bytes,
                        remaining_reservations(repo) + reservation)
        job_id = uuid4().hex
        directory = repo.job_dir(job_id)
        for name in ['input', 'work', 'output']:
            (directory / name).mkdir(parents=True, exist_ok=True)
        metadata = {'owner_id': owner, 'asset_id': asset_id, 'source_asset_id': asset_id,
                    'workflow': plan, 'workflow_request_id': str(payload.request_id),
                    'workflow_request': identity, 'reserved_bytes': reservation, 'queued_at': utc_now_iso()}
        if payload.subtitle_artifact_id:
            metadata['subtitle_input'] = artifacts.snapshot(asset_id, payload.subtitle_artifact_id,
                directory / 'input/subtitle.json', owner_id=owner,
                before_write=lambda size: assert_capacity(repo.storage_root, settings.service_quota_bytes,
                    settings.min_free_space_bytes, remaining_reservations(repo) + reservation + size))
        if audio_source is not None:
            destination = directory / 'input/audio.wav'
            shutil.copyfile(audio_source, destination)
            if destination.stat().st_size != audio_source.stat().st_size:
                raise HTTPException(status_code=409, detail='Audio snapshot size mismatch.')
            with destination.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            metadata['audio_input'] = {'job_id': payload.audio_job_id, 'sha256': digest,
                                       'size_bytes': destination.stat().st_size}
        record = JobRecord(job_id=job_id, status=JobStatus.QUEUED,
            original_filename=asset['original_filename'], source_filename=asset['source_filename'],
            expected_size=asset['size_bytes'], uploaded_bytes=asset['size_bytes'], options=options, metadata=metadata)
        # Publish a runnable job only after every immutable input has been persisted.
        repo.save(record)
        return routes.job_to_response(record)


@router.get('/{asset_id}/audio-inputs')
def list_audio_inputs(asset_id: str, request: Request):
    repo = routes.repository
    with asset_errors():
        VideoAssetRepository(repo).read(asset_id, owner_id=request.state.owner_id)
        inputs = []
        for job in repo.list():
            if (job.metadata.get('owner_id') == request.state.owner_id
                    and job.metadata.get('asset_id') == asset_id and job.status == JobStatus.COMPLETED
                    and not job.metadata.get('results_expired_at')):
                path = repo.job_dir(job.job_id) / 'work/audio.wav'
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                inputs.append({'job_id': job.job_id, 'created_at': job.created_at, 'size_bytes': size})
        return {'audio_inputs': inputs}
