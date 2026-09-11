from __future__ import annotations

import shutil
from pathlib import Path
from video_service.capacity import StorageLimitError, used_bytes
from video_service.repository import FilesystemJobRepository
from video_service.models import TERMINAL_STATUSES


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
        # Reserve input plus an initial working/output allowance for active jobs.
        reserved = 0
        repository = FilesystemJobRepository(self.root)
        for record in repository.list():
            if record.status not in TERMINAL_STATUSES:
                reserved += max(0, record.expected_size * 4 - used_bytes(repository.job_dir(record.job_id)))
        additional = reserved + expected_size * 4
        if total_used + additional > self.quota_bytes:
            raise StorageLimitError("서비스 저장공간 할당량을 초과합니다.")

        free_bytes = shutil.disk_usage(self.root).free
        if free_bytes - additional < self.min_free_space_bytes:
            raise StorageLimitError("디스크 여유 공간이 부족합니다.")

    def _service_used_bytes(self) -> int:
        return used_bytes(self.root)
