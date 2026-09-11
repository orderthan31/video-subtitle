import logging
import re
import time

from video_service.locking import job_lock, JobBusyError
from video_service.models import JobRecord
from video_service.storage import read_json


def collect_orphans(repository, *, grace_seconds=7200, now=None):
    """Collect only stale UUID directories lacking usable metadata, under both job locks."""
    if grace_seconds < 60:
        raise ValueError("Orphan grace period must be at least 60 seconds")
    now = time.time() if now is None else now
    removed = []
    for candidate in repository.storage_root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{32}", candidate.name) or candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            # Resolve before inspecting children, including Windows junctions.
            directory = repository.job_dir(candidate.name)
            with job_lock(repository, candidate.name, "execution"), job_lock(repository, candidate.name):
                metadata = directory / "job.json"
                if metadata.exists():
                    try:
                        record = JobRecord.from_dict(read_json(metadata))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        pass
                    else:
                        if record.job_id == candidate.name:
                            continue
                latest = directory.stat().st_mtime
                for child in directory.rglob("*"):
                    if child.is_symlink():
                        continue
                    latest = max(latest, child.stat().st_mtime)
                if now - latest >= grace_seconds:
                    repository.delete_job_dir(candidate.name)
                    removed.append(candidate.name)
        except (JobBusyError, FileNotFoundError):
            continue
        except (OSError, ValueError):
            logging.warning("Skipping inaccessible or unsafe orphan directory %s", candidate.name)
    return removed
