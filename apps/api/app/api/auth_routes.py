from dataclasses import dataclass
import hmac
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from video_service.auth import AuthStore, LoginThrottled


def boolean_env(name, default):
    value = os.getenv(name, default).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


@dataclass
class AuthConfig:
    store: AuthStore | None
    secure: bool = True
    lifetime: int = 28800

    @property
    def cookie_name(self):
        return "__Host-video-session" if self.secure else "video-session"


def configured_auth(storage_root):
    enabled = boolean_env("AUTH_ENABLED", "false")
    secure = boolean_env("AUTH_COOKIE_SECURE", "true")
    hours = int(os.getenv("AUTH_SESSION_HOURS", "8"))
    if not 1 <= hours <= 168:
        raise ValueError("AUTH_SESSION_HOURS must be between 1 and 168")
    path = Path(os.getenv("AUTH_DATABASE_PATH", str(Path(storage_root) / ".auth/accounts.sqlite3")))
    return AuthConfig(AuthStore(path) if enabled else None, secure, hours * 3600)


def current_session(request):
    auth = request.app.state.auth
    return auth.store.session(request.cookies.get(auth.cookie_name)) if auth.store else None


def require_session(request):
    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        actual = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(actual.encode("utf-8"), session["csrf_token"].encode("utf-8")):
            raise HTTPException(status_code=403, detail="요청 인증 토큰을 확인하세요.")
    return session


router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.get("/session")
def session_info(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": request.app.state.auth.store is not None, **(current_session(request) or {"user": None})}


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    auth = request.app.state.auth
    if auth.store is None:
        raise HTTPException(status_code=409, detail="인증이 활성화되지 않았습니다.")
    origin = request.headers.get("origin")
    allowed = [*request.app.state.cors_origins, str(request.base_url).rstrip("/")]
    if request.headers.get("content-type", "").split(";")[0] != "application/json" or (origin and origin not in allowed):
        raise HTTPException(status_code=403, detail="허용되지 않은 로그인 요청입니다.")
    try:
        session = auth.store.login(payload.username, payload.password,
            request.client.host if request.client else "unknown", auth.lifetime)
    except LoginThrottled as exc:
        raise HTTPException(status_code=429, detail="로그인 시도가 너무 많습니다.", headers={"Retry-After": str(exc.retry_after)}) from exc
    if session is None:
        raise HTTPException(status_code=401, detail="사용자 이름 또는 비밀번호를 확인하세요.")
    response.set_cookie(auth.cookie_name, session.pop("token"), max_age=auth.lifetime,
        secure=auth.secure, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": True, **session}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    auth = request.app.state.auth
    if auth.store is not None:
        require_session(request)
        auth.store.logout(request.cookies.get(auth.cookie_name))
    response.delete_cookie(auth.cookie_name, path="/", secure=auth.secure, httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
