from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from video_service.locking import JobBusyError
from video_service.repository import JobNotFoundError

from app.api.routes import router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Video Subtitle API", version="0.1.0")
    @app.exception_handler(JobBusyError)
    async def job_busy(request, exc):
        return JSONResponse(status_code=409, content={"detail": "Job is busy; retry shortly."}, headers={"Retry-After": "1"})

    @app.exception_handler(JobNotFoundError)
    async def job_missing(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Job not found."})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
