"""Create isolated synthetic status fixtures for history UI checks; no AI calls."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from media_worker.worker import Worker


def main():
    root = ROOT / "data/history-fixtures" / uuid4().hex
    repo = FilesystemJobRepository(root / "jobs")
    jobs = {}
    for name, status, days in (
        ("expired-history.mp4", JobStatus.COMPLETED, 2),
        ("failed-history.mp4", JobStatus.FAILED, 2),
        ("cancelled-history.mp4", JobStatus.CANCELLED, 2),
        ("queued-history.mp4", JobStatus.QUEUED, 0),
    ):
        record = repo.create_job(original_filename=name, expected_size=7,
            source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED)
        repo.source_path(record).write_bytes(b"fixture")
        (repo.job_dir(record.job_id) / "output/final.mp4").write_bytes(b"fixture")
        record = repo.update_status(record.job_id, status,
            error="Synthetic failure" if status == JobStatus.FAILED else None,
            metadata={"result_files": ["final.mp4"]} if status == JobStatus.COMPLETED else None)
        if days:
            record.completed_at = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            repo.save(record)
        jobs[name] = record.job_id
    os.environ.update(RESULT_TTL_HOURS="24", HISTORY_TTL_DAYS="90")
    Worker(repo, None).collect()
    expired = repo.read(jobs["expired-history.mp4"])
    if not expired.metadata.get("results_expired_at") or (repo.job_dir(expired.job_id) / "output").exists():
        raise RuntimeError("Fixture history expiration failed")
    report = {"storage_root": str(repo.storage_root), "jobs": jobs,
        "scope": "Synthetic statuses and placeholder files, not encoded media"}
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
