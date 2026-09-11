from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from uuid import uuid4

from video_service.capacity import assert_capacity
from video_service.storage import write_json_atomic


_destination = ContextVar("llm_trace_destination", default=None)
_window = ContextVar("llm_trace_window", default=None)


@contextmanager
def capture_calls(storage_root, directory):
    token = _destination.set((storage_root, directory) if directory is not None else None)
    try:
        yield
    finally:
        _destination.reset(token)


@contextmanager
def audio_window(left, right, rate, depth):
    token = _window.set({"start_seconds": left / rate, "end_seconds": right / rate,
        "clock": "preprocessed_audio", "split_depth": depth})
    try:
        yield
    finally:
        _window.reset(token)


def save_trace(path, data, secret):
    destination = _destination.get()
    if destination is None:
        return
    # No headers are captured; also redact the key if a server ever echoes it.
    if secret:
        data = json.loads(json.dumps(data, ensure_ascii=False).replace(secret, "[REDACTED]"))
    root, _ = destination
    write_json_atomic(path, data, before_write=lambda size: assert_capacity(root,
        int(os.getenv("VIDEO_SERVICE_QUOTA_BYTES", str(300 * 1024**3))),
        int(os.getenv("MIN_FREE_SPACE_BYTES", str(50 * 1024**3))), additional=size))


def begin_call(model, attempt, payload, secret):
    destination = _destination.get()
    if destination is None:
        return None
    _, directory = destination
    folder = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid4().hex)
    folder.mkdir(parents=True)
    save_trace(folder / "request.json", {"model": model, "attempt": attempt,
        "window": _window.get(), "payload": payload}, secret)
    return folder


def finish_call(folder, secret, **data):
    if folder is not None:
        save_trace(folder / "response.json", {"received_at": datetime.now(timezone.utc).isoformat(), **data}, secret)
