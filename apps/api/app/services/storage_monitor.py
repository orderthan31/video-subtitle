"""Read-only storage audits, independent of upload request latency."""
import logging
import shutil
import time

import anyio
from starlette.concurrency import run_in_threadpool

from video_service.capacity import used_bytes, remaining_reservations
from video_service.repository import FilesystemJobRepository

logger = logging.getLogger("uvicorn.error")


def storage_snapshot(settings):
    repository = FilesystemJobRepository(settings.storage_root)
    actual = used_bytes(settings.storage_root)
    reserved = remaining_reservations(repository)
    free = shutil.disk_usage(settings.storage_root).free
    return {
        "checked_at": time.time(),
        "used_bytes": actual,
        "remaining_reserved_bytes": reserved,
        "free_bytes": free,
        "over_quota": actual + reserved > settings.service_quota_bytes,
        "low_space": free - reserved < settings.min_free_space_bytes,
    }


async def monitor_storage(app, settings):
    while True:
        # Admission checks remain authoritative; the audit is an advisory snapshot.
        await anyio.sleep(settings.storage_audit_interval_seconds)
        try:
            snapshot = await run_in_threadpool(storage_snapshot, settings)
            app.state.storage_snapshot = snapshot
            log = logger.warning if snapshot["over_quota"] or snapshot["low_space"] else logger.info
            log("Storage audit: %s", snapshot)
        except Exception:
            logger.exception("Storage audit failed; admission checks remain enabled")
