"""Explicit, resumable legacy registration; never changes job execution status."""
from .assets import AssetConflictError, VideoAssetRepository
from .locking import JobBusyError, job_lock
from .models import ACTIVE_STATUSES, JobStatus
from .video_probe import inspect_video


def migrate_legacy_assets(repository, *, before_copy, inspect_source=inspect_video, dry_run=True):
    assets = VideoAssetRepository(repository)
    report = {'dry_run': dry_run, 'registered': [], 'already_linked': [], 'pending': [], 'skipped': [], 'failed': []}
    jobs = []
    for manifest in repository.storage_root.glob('*/job.json'):
        try:
            jobs.append(repository.read(manifest.parent.name))
        except (KeyError, ValueError, TypeError, OSError):
            report['failed'].append({'job_id': manifest.parent.name, 'reason': 'Invalid job manifest'})
    for job in jobs:
        if job.metadata.get('asset_id'):
            try:
                assets.read(job.metadata['asset_id'], owner_id=job.metadata.get('owner_id'))
                report['already_linked'].append(job.job_id)
            except (KeyError, ValueError, OSError):
                report['failed'].append({'job_id': job.job_id, 'reason': 'Invalid existing asset reference'})
            continue
        if job.status == JobStatus.UPLOADING or job.status in ACTIVE_STATUSES:
            report['skipped'].append({'job_id': job.job_id, 'reason': 'Job is uploading or active'})
            continue
        try:
            source = repository.source_path(job)
            if not source.is_file() or source.stat().st_size != job.expected_size:
                raise AssetConflictError('Complete original video is unavailable')
            if dry_run:
                report['pending'].append({'job_id': job.job_id, 'copy_bytes': job.expected_size})
            else:
                with job_lock(repository, '0' * 32):
                    asset = assets.register_upload(job.job_id, owner_id=job.metadata.get('owner_id'),
                        before_copy=before_copy, inspect_source=inspect_source)
                report['registered'].append({'job_id': job.job_id, 'asset_id': asset['asset_id']})
        except JobBusyError:
            report['skipped'].append({'job_id': job.job_id, 'reason': 'Job is busy'})
        except (KeyError, ValueError, OSError) as exc:
            report['failed'].append({'job_id': job.job_id, 'reason': str(exc)})
    return report
