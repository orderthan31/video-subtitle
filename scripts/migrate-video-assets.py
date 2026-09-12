"""Run inside the API container, or with the project's Python packages installed."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/shared'))
from video_service.capacity import assert_capacity, remaining_reservations
from video_service.config import load_environment
from video_service.migration import migrate_legacy_assets
from video_service.repository import FilesystemJobRepository


def main():
    parser = argparse.ArgumentParser(description='Register legacy source videos without changing job status or deleting files.')
    parser.add_argument('--storage-root', required=True)
    parser.add_argument('--apply', action='store_true', help='Copy sources and persist links; otherwise only report candidates.')
    args = parser.parse_args()
    load_environment()
    repo = FilesystemJobRepository(args.storage_root)
    def reserve(size):
        assert_capacity(repo.storage_root, int(os.getenv('VIDEO_SERVICE_QUOTA_BYTES', str(300 * 1024**3))),
                        int(os.getenv('MIN_FREE_SPACE_BYTES', str(50 * 1024**3))), remaining_reservations(repo) + size)
    report = migrate_legacy_assets(repo, before_copy=reserve, dry_run=not args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
