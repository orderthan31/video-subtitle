from fastapi import HTTPException
from fastapi.responses import FileResponse

from video_service.locking import download_lock
from video_service.models import JobStatus
from video_service.storage import resolve_under


class ResultResponse(FileResponse):
    def __init__(self, repository, job_id, filename):
        if filename not in {"final.mp4", "translated.srt", "original.srt"}:
            raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
        self.repository = repository
        self.job_id = job_id
        path = resolve_under(repository.storage_root, job_id, "output", filename)
        super().__init__(path=path, filename=filename)

    async def __call__(self, scope, receive, send):
        with download_lock(self.repository, self.job_id):
            record = self.repository.read(self.job_id)
            if record.status != JobStatus.COMPLETED:
                raise HTTPException(status_code=409, detail="아직 완료되지 않은 Job입니다.")
            if not self.path.is_file():
                raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
            # Do not hand off a path whose deferred transfer could outlive the lease.
            scope = {**scope, "extensions": {key: value for key, value in scope.get("extensions", {}).items()
                if key != "http.response.pathsend"}}
            await super().__call__(scope, receive, send)
