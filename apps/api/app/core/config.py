from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from video_service.config import load_environment

load_environment()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _list_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    storage_root: Path = Path(os.getenv("VIDEO_STORAGE_ROOT", "data/video-jobs"))
    max_upload_bytes: int = _int_env("MAX_UPLOAD_BYTES", 10 * 1024 * 1024 * 1024)
    service_quota_bytes: int = _int_env("VIDEO_SERVICE_QUOTA_BYTES", 300 * 1024 * 1024 * 1024)
    min_free_space_bytes: int = _int_env("MIN_FREE_SPACE_BYTES", 50 * 1024 * 1024 * 1024)
    cors_origins: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cors_origins is None:
            object.__setattr__(
                self,
                "cors_origins",
                _list_env("WEB_CORS_ORIGINS", ["http://localhost:5173"]),
            )


settings = Settings()
