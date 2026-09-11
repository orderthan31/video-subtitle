"""Local-only large MP4 upload, interrupted chunk rollback and API restart/resume check."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from video_service.storage import write_json_atomic
from media_worker.media import executable, probe
from media_worker.process import run_process

BLOCK = 4 * 1024**2


def peak_memory(pid):
    if os.name != "nt":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), ("sizes", ctypes.c_size_t * 8)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.sizes[0])
    finally:
        kernel.CloseHandle(handle)


class LocalApi:
    def __init__(self, work):
        self.work = work
        self.process = None
        self.log = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}/api"

    def start(self):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "packages/shared"), str(ROOT / "apps/api")]),
            "VIDEO_STORAGE_ROOT": str(self.work / "jobs"), "AUTH_ENABLED": "false",
            "VIDEO_SERVICE_QUOTA_BYTES": str(32 * 1024**3), "MIN_FREE_SPACE_BYTES": str(1024**3)}
        self.log = (self.work / "api.log").open("ab")
        self.process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(self.port), "--no-access-log"], cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Isolated API exited during startup; see api.log")
            try:
                if httpx.get(self.base + "/health", timeout=1).status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.1)
        raise TimeoutError("Isolated API startup timed out")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log is not None:
            self.log.close()


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--gib", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--minutes", type=int, choices=[30, 60], default=30)
    args = parser.parse_args()
    if not args.run_local:
        parser.error("--run-local is required; creates and transfers a large local fixture")
    target = args.gib * 1024**3
    if shutil.disk_usage(ROOT).free < target * 5 + 1024**3:
        raise RuntimeError("Not enough free disk space for isolated test files")
    load_environment()
    work = ROOT / "data/large-upload-validation" / uuid4().hex
    work.mkdir(parents=True)
    print(f"Evidence directory: {work}", flush=True)
    seed, source = work / "seed.mp4", work / "large.mp4"
    run_process([executable("ffmpeg"), "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "1", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", seed], cwd=work, log_name="seed.log", timeout=60)
    run_process([executable("ffmpeg"), "-nostdin", "-y", "-stream_loop", "-1", "-i", seed,
        "-t", str(args.minutes * 60), "-c", "copy", "-movflags", "+faststart", source],
        cwd=work, log_name="fixture.log", timeout=180)
    media_bytes = source.stat().st_size
    padding = target - media_bytes
    if padding < 16:
        raise RuntimeError("Fixture media exceeded requested test size")
    # MP4 large-size free box is real transferred padding, not a sparse file.
    with source.open("ab") as stream:
        stream.write(struct.pack(">I4sQ", 1, b"free", padding))
        remaining = padding - 16
        block = bytes(1024**2)
        while remaining:
            count = min(remaining, len(block))
            stream.write(block[:count])
            remaining -= count
    metadata = probe(source, work)
    if abs(metadata["duration"] - args.minutes * 60) > 1:
        raise RuntimeError("Fixture duration does not match the test")
    api = LocalApi(work)
    report = {"expected_bytes": target, "duration": metadata["duration"], "encoded_media_bytes": media_bytes,
        "padding_bytes": padding, "api_peak_bytes": 0, "client_peak_bytes": 0, "passed": False}
    try:
        api.start()
        with httpx.Client(base_url=api.base, timeout=60) as client:
            created = request(client, "POST", "/uploads", json={"filename": "large.mp4", "size": target})
            job = created["job_id"]
            report["job_id"] = job
            upload = f"/uploads/{job}"
            digest = hashlib.sha256()
            offset = 0
            with source.open("rb") as stream:
                while offset < target:
                    block = stream.read(BLOCK)
                    state = request(client, "PUT", upload + f"/chunks?offset={offset}", content=block)
                    digest.update(block)
                    offset += len(block)
                    if state["uploaded_bytes"] != offset:
                        raise RuntimeError("Unexpected upload offset")
                    report["api_peak_bytes"] = max(report["api_peak_bytes"], peak_memory(api.process.pid) or 0)
                    report["client_peak_bytes"] = max(report["client_peak_bytes"], peak_memory(os.getpid()) or 0)
                    if offset == 64 * 1024**2:
                        with socket.create_connection(("127.0.0.1", api.port), timeout=5) as connection:
                            header = (f"PUT /api/uploads/{job}/chunks?offset={offset} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                f"Content-Length: {BLOCK}\r\nContent-Type: application/octet-stream\r\n\r\n")
                            connection.sendall(header.encode("ascii") + bytes(65536))
                        deadline = time.monotonic() + 10
                        while True:
                            status = client.get(upload)
                            if status.status_code != 409:
                                status.raise_for_status()
                                break
                            if time.monotonic() > deadline:
                                raise TimeoutError("Interrupted chunk lock was not released")
                            time.sleep(0.1)
                        report["interrupted_chunk_rolled_back"] = status.json()["uploaded_bytes"] == offset
                        if not report["interrupted_chunk_rolled_back"]:
                            raise RuntimeError("Interrupted chunk changed the committed prefix")
                    if offset == target // 2:
                        api.stop()
                        api.start()
                        state = request(client, "GET", upload)
                        report["restart_resumed_at"] = state["uploaded_bytes"]
                        if state["uploaded_bytes"] != offset:
                            raise RuntimeError("Restart lost the upload position")
                        with source.open("rb") as prefix:
                            for first in range(0, offset, BLOCK):
                                part = prefix.read(min(BLOCK, offset - first))
                                request(client, "POST", upload + "/verify", json={"uploaded_bytes": offset,
                                    "offset": first, "length": len(part), "sha256": hashlib.sha256(part).hexdigest()})
                    if offset % (256 * 1024**2) == 0:
                        print(f"Uploaded {offset // 1024**2} MiB", flush=True)
            report["completed"] = request(client, "POST", upload + "/complete")["status"]
            stored = work / "jobs" / job / "input/source.mp4"
            with stored.open("rb") as file:
                report["stored_sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
            report["source_sha256"] = digest.hexdigest()
            report["stored_bytes"] = stored.stat().st_size
            checks_passed = (report["stored_sha256"] == report["source_sha256"] and report["stored_bytes"] == target
                and report["completed"] == "queued" and report["interrupted_chunk_rolled_back"]
                and report["restart_resumed_at"] == target // 2
                and 0 < report["api_peak_bytes"] < 256 * 1024**2 and 0 < report["client_peak_bytes"] < 256 * 1024**2)
            request(client, "POST", f"/jobs/{job}/cancel")
            request(client, "DELETE", f"/jobs/{job}")
            report["server_job_removed"] = not stored.parent.parent.exists()
        source.unlink()
        report["fixture_removed"] = not source.exists()
        report["passed"] = checks_passed and report["server_job_removed"] and report["fixture_removed"]
    finally:
        api.stop()
        write_json_atomic(work / "report.json", report)
        print(report, flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
