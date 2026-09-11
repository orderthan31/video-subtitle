from fastapi import HTTPException
from fastapi.responses import FileResponse
import re

from video_service.locking import download_lock
from video_service.models import JobStatus
from video_service.storage import resolve_under


class ResultResponse(FileResponse):
    def __init__(self, repository, job_id, filename):
        if filename not in {"final.mp4", "translated.srt", "original.srt", "translated.smi"} and not re.fullmatch(
                r"translated\.[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\.(?:srt|smi)", filename):
            raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
        self.repository = repository
        self.job_id = job_id
        self.filename = filename
        path = resolve_under(repository.storage_root, job_id, "output", filename)
        super().__init__(path=path, filename=filename, media_type="text/plain" if filename.endswith(".smi") else None)

    async def __call__(self, scope, receive, send):
        with download_lock(self.repository, self.job_id):
            record = self.repository.read(self.job_id)
            if record.status != JobStatus.COMPLETED:
                raise HTTPException(status_code=409, detail="아직 완료되지 않은 Job입니다.")
            if record.metadata.get("results_expired_at"):
                raise HTTPException(status_code=410, detail="결과 파일의 보관 기간이 만료되었습니다.")
            if self.filename.startswith("translated.") and self.filename.count(".") == 2:
                if self.filename not in record.metadata.get("result_files", []):
                    raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
            if not self.path.is_file():
                raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
            # Do not hand off a path whose deferred transfer could outlive the lease.
            scope = {**scope, "extensions": {key: value for key, value in scope.get("extensions", {}).items()
                if key != "http.response.pathsend"}}
            await super().__call__(scope, receive, send)
