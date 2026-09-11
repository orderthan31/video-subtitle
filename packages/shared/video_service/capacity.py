import shutil
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
