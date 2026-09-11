from __future__ import annotations

import shutil
from pathlib import Path


class StorageLimitError(ValueError):
    pass


class StorageGuard:
    def __init__(
        self,
        *,
        root: Path,
        max_upload_bytes: int,
        quota_bytes: int,
        min_free_space_bytes: int,
    ) -> None:
        self.root = root
        self.max_upload_bytes = max_upload_bytes
        self.quota_bytes = quota_bytes
        self.min_free_space_bytes = min_free_space_bytes

    def assert_can_accept_upload(self, expected_size: int) -> None:
        if expected_size > self.max_upload_bytes:
            raise StorageLimitError("파일 크기가 업로드 제한을 초과했습니다.")

        self.root.mkdir(parents=True, exist_ok=True)
        total_used = self._service_used_bytes()
        if total_used + expected_size > self.quota_bytes:
            raise StorageLimitError("서비스 저장공간 할당량을 초과합니다.")

        free_bytes = shutil.disk_usage(self.root).free
        if free_bytes - expected_size < self.min_free_space_bytes:
            raise StorageLimitError("디스크 여유 공간이 부족합니다.")

    def _service_used_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

