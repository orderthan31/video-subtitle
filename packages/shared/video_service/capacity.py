import shutil
import math
from pathlib import Path


class StorageLimitError(ValueError):
    pass


def used_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def assert_capacity(root: Path, quota: int, minimum_free: int, additional=0):
    if used_bytes(root) + additional > quota:
        raise StorageLimitError("Service storage quota exceeded")
    if shutil.disk_usage(root).free - additional < minimum_free:
        raise StorageLimitError("Insufficient free disk space")


def workspace_reservation(expected_size: int, duration: float) -> int:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Duration must be positive and finite")
    # Source/output allowance plus extracted and processed mono 16-bit 16kHz PCM.
    pcm_bytes = math.ceil(duration * 16000) * 2
    return expected_size * 4 + pcm_bytes * 2 + 1024**2


def remaining_reservations(repository, overrides=None):
    from .models import TERMINAL_STATUSES

    overrides = overrides or {}
    remaining = 0
    for record in repository.list():
        if record.status in TERMINAL_STATUSES:
            continue
        reserved = overrides.get(record.job_id, record.metadata.get("reserved_bytes", record.expected_size * 4))
        if not isinstance(reserved, int) or reserved < 0:
            raise StorageLimitError("Invalid workspace reservation")
        remaining += max(0, reserved - used_bytes(repository.job_dir(record.job_id)))
    return remaining


def reserve_workspace(repository, job_id, duration, quota, minimum_free, check=lambda: None):
    from .locking import wait_for_job_lock

    # Share the API admission lock so simultaneous analyses cannot oversubscribe.
    with wait_for_job_lock(repository, "0" * 32, check=check), wait_for_job_lock(repository, job_id, check=check):
        record = repository.read(job_id)
        reserved = max(record.metadata.get("reserved_bytes", 0), workspace_reservation(record.expected_size, duration))
        additional = remaining_reservations(repository, {job_id: reserved})
        assert_capacity(repository.storage_root, quota, minimum_free, additional)
        record.metadata.update(reserved_bytes=reserved, source_duration=duration)
        repository.save(record)
        return reserved
