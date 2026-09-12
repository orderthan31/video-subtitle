"""Video-owned storage independent of disposable workflow directories."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import shutil
from uuid import NAMESPACE_URL, uuid4, uuid5

from .locking import _file_lock, job_lock
from .models import JobStatus, utc_now_iso
from .storage import ensure_dir, read_json, resolve_under, safe_suffix, write_json_atomic


class AssetNotFoundError(KeyError):
    pass


class AssetConflictError(ValueError):
    pass


class VideoAssetRepository:
    def __init__(self, jobs):
        self.jobs = jobs
        self.root = ensure_dir(jobs.storage_root / '.assets')

    @contextmanager
    def lock(self):
        with _file_lock(self.root / '.registry.lock'):
            yield

    def directory(self, asset_id: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{32}', asset_id):
            raise AssetNotFoundError(asset_id)
        return resolve_under(self.root, asset_id)

    def read(self, asset_id: str, *, owner_id=None) -> dict:
        path = self.directory(asset_id) / 'asset.json'
        if not path.exists():
            raise AssetNotFoundError(asset_id)
        record = read_json(path)
        if record['asset_id'] != asset_id or record.get('owner_id') != owner_id:
            raise AssetNotFoundError(asset_id)
        return record

    def list(self, *, owner_id=None) -> list[dict]:
        # A corrupt manifest is surfaced instead of silently dropping references.
        records = [read_json(path) for path in self.root.glob('*/asset.json')]
        return sorted((r for r in records if r.get('owner_id') == owner_id),
                      key=lambda r: (r['created_at'], r['asset_id']), reverse=True)

    def source_path(self, record: dict) -> Path:
        directory = self.directory(record['asset_id'])
        path = resolve_under(directory, record['source_filename'])
        if path.parent != directory or path.name == 'asset.json':
            raise ValueError('Invalid asset source path')
        return path

    def _publish_copy(self, source: Path, *, asset_id: str, filename: str,
                      owner_id, provenance: dict, before_copy, created_at=None) -> dict:
        """Caller holds registry and source execution locks; manifest publishes last."""
        manifest = self.directory(asset_id) / 'asset.json'
        if manifest.exists():
            return self.read(asset_id, owner_id=owner_id)
        size = source.stat().st_size
        before_copy(size + 4096)
        directory = ensure_dir(self.directory(asset_id))
        destination = directory / ('source' + safe_suffix(filename))
        temporary = directory / ('copy-' + uuid4().hex + '.tmp')
        try:
            shutil.copyfile(source, temporary)
            if temporary.stat().st_size != size or source.stat().st_size != size:
                raise AssetConflictError('Source changed during registration')
            temporary.replace(destination)
            record = {
                'schema_version': 1, 'asset_id': asset_id, 'owner_id': owner_id,
                'original_filename': filename, 'source_filename': destination.name,
                'size_bytes': size, 'status': 'ready',
                'created_at': created_at or utc_now_iso(), 'provenance': provenance,
            }
            write_json_atomic(manifest, record)
            return record
        finally:
            temporary.unlink(missing_ok=True)

    def register_upload(self, job_id: str, *, owner_id=None, before_copy) -> dict:
        """Also migrates legacy jobs without changing their status or artifacts."""
        with job_lock(self.jobs, job_id, 'execution'), job_lock(self.jobs, job_id), self.lock():
            job = self.jobs.read(job_id)
            if job.metadata.get('owner_id') != owner_id:
                raise AssetNotFoundError(job_id)
            asset_id = job.metadata.get('asset_id')
            if asset_id:
                return self.read(asset_id, owner_id=owner_id)
            if job.status == JobStatus.UPLOADING:
                raise AssetConflictError('Upload is not complete')
            source = self.jobs.source_path(job)
            if not source.is_file() or source.stat().st_size != job.expected_size:
                raise AssetConflictError('Complete original video is unavailable')
            asset_id = uuid5(NAMESPACE_URL, 'video-subtitle:upload:' + job_id).hex
            record = self._publish_copy(source, asset_id=asset_id,
                filename=job.original_filename, owner_id=owner_id,
                provenance={'kind': 'upload', 'upload_id': job_id},
                before_copy=before_copy, created_at=job.created_at)
            job.metadata['asset_id'] = asset_id
            self.jobs.save(job)
            return record

    def promote_result(self, job_id: str, *, owner_id=None, before_copy) -> dict:
        with job_lock(self.jobs, job_id, 'execution'), job_lock(self.jobs, job_id), self.lock():
            job = self.jobs.read(job_id)
            if job.metadata.get('owner_id') != owner_id:
                raise AssetNotFoundError(job_id)
            asset_id = uuid5(NAMESPACE_URL, 'video-subtitle:result:' + job_id + ':final.mp4').hex
            if (self.directory(asset_id) / 'asset.json').exists():
                return self.read(asset_id, owner_id=owner_id)
            if job.status != JobStatus.COMPLETED or job.metadata.get('results_expired_at'):
                raise AssetConflictError('Completed output video is unavailable')
            source = resolve_under(self.jobs.job_dir(job_id), 'output', 'final.mp4')
            if not source.is_file():
                raise AssetConflictError('Completed output video is unavailable')
            return self._publish_copy(source, asset_id=asset_id,
                filename=Path(job.original_filename).stem + '_processed.mp4', owner_id=owner_id,
                provenance={'kind': 'result', 'job_id': job_id,
                            'parent_asset_id': job.metadata.get('asset_id'), 'result': 'final.mp4'},
                before_copy=before_copy)

    def delete(self, asset_id: str, *, owner_id=None) -> None:
        with self.lock():
            self.read(asset_id, owner_id=owner_id)
            # Do not use jobs.list(): it skips invalid manifests and could miss a reference.
            for path in self.jobs.storage_root.glob('*/job.json'):
                job = read_json(path)
                if job.get('metadata', {}).get('asset_id') == asset_id:
                    raise AssetConflictError('Video is referenced by job ' + job['job_id'])
            shutil.rmtree(self.directory(asset_id))
