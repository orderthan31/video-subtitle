from __future__ import annotations

from collections.abc import AsyncIterator
import errno
from typing import Callable

from video_service.models import JobStatus
from video_service.locking import job_lock
from video_service.repository import FilesystemJobRepository
from video_service.capacity import StorageLimitError


class UploadConflictError(ValueError):
    def __init__(self, expected_offset: int) -> None:
        super().__init__("업로드 offset이 서버 상태와 일치하지 않습니다.")
        self.expected_offset = expected_offset


class UploadService:
    def __init__(self, repository: FilesystemJobRepository, capacity_check: Callable[[int], None] | None = None) -> None:
        self.repository = repository
        self.capacity_check = capacity_check

    async def append_chunk(
        self,
        *,
        job_id: str,
        expected_offset: int,
        body: AsyncIterator[bytes],
    ) -> int:
        with job_lock(self.repository, job_id):
            return await self._append_chunk(job_id=job_id, expected_offset=expected_offset, body=body)

    async def _append_chunk(self, *, job_id: str, expected_offset: int, body: AsyncIterator[bytes]) -> int:
        record = self.repository.read(job_id)
        if record.status != JobStatus.UPLOADING:
            raise ValueError("업로드 중인 Job만 chunk를 받을 수 있습니다.")

        source_path = self.repository.source_path(record)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        current_size = source_path.stat().st_size if source_path.exists() else 0
        if current_size != expected_offset:
            raise UploadConflictError(current_size)

        uploaded = current_size
        with source_path.open("ab") as file:
            try:
                async for chunk in body:
                    if not chunk:
                        continue
                    uploaded += len(chunk)
                    if uploaded > record.expected_size:
                        raise ValueError("업로드 크기가 선언된 파일 크기를 초과했습니다.")
                    if self.capacity_check:
                        file.flush()
                        self.capacity_check(len(chunk))
                    file.write(chunk)
                file.flush()
            except OSError as exc:
                file.truncate(current_size)
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                    raise StorageLimitError("Storage became full during upload") from exc
                raise
            except BaseException:
                file.truncate(current_size)
                raise

        record = self.repository.update_upload_progress(job_id, uploaded)
        return record.uploaded_bytes

    def complete(self, job_id: str) -> None:
        with job_lock(self.repository, job_id):
            self._complete(job_id)

    def _complete(self, job_id: str) -> None:
        record = self.repository.read(job_id)
        if record.status != JobStatus.UPLOADING:
            raise ValueError("업로드 중인 Job만 완료 처리할 수 있습니다.")

        source_path = self.repository.source_path(record)
        uploaded = source_path.stat().st_size if source_path.exists() else 0
        if uploaded != record.expected_size:
            raise UploadConflictError(uploaded)

        record.uploaded_bytes = uploaded
        self.repository.save(record)
        self.repository.update_status(job_id, JobStatus.READY, message="업로드 완료, 실행 대기 중")
