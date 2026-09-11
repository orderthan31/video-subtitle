from pathlib import Path
import os
import subprocess
from threading import Event, Thread
import time


class Cancelled(Exception):
    pass


class ProcessLogLimitError(RuntimeError):
    pass


def run_process(args, *, cwd: Path, log_name: str, check=lambda: None, timeout=7200, max_log_bytes=None):
    """Bound saved output and fail rather than parse an incomplete process log."""
    limit = int(os.getenv("MAX_PROCESS_LOG_BYTES", str(8 * 1024**2))) if max_log_bytes is None else max_log_bytes
    if type(limit) is not int or not 4096 <= limit <= 64 * 1024**2:
        raise ValueError("MAX_PROCESS_LOG_BYTES must be between 4096 and 67108864")
    log = cwd / log_name
    started = time.monotonic()
    overflow = Event()
    failures = []
    tail = b""
    with log.open("wb") as output:
        process = subprocess.Popen(
            [str(arg) for arg in args], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        def drain():
            nonlocal tail
            written = 0
            try:
                while chunk := process.stdout.read(65536):
                    tail = (tail + chunk)[-3000:]
                    remaining = max(0, limit - written)
                    if remaining:
                        saved = chunk[:remaining]
                        output.write(saved)
                        output.flush()
                        written += len(saved)
                    if len(chunk) > remaining:
                        overflow.set()
            except (OSError, ValueError) as exc:
                failures.append(exc)

        reader = Thread(target=drain, name="media-process-output", daemon=True)
        try:
            reader.start()
            while process.poll() is None:
                check()
                if failures:
                    raise OSError("Cannot save media process output") from failures[0]
                if overflow.is_set():
                    raise ProcessLogLimitError(f"Process log exceeded {limit} bytes ({log_name})")
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"Process exceeded {timeout} seconds")
                time.sleep(0.2)
            reader.join(timeout=5)
            if reader.is_alive():
                raise RuntimeError("Process output stream did not close after exit")
            if failures:
                raise OSError("Cannot save media process output") from failures[0]
            if overflow.is_set():
                raise ProcessLogLimitError(f"Process log exceeded {limit} bytes ({log_name})")
            check()
            if process.returncode:
                detail = tail.decode("utf-8", errors="replace")
                raise RuntimeError(f"{Path(str(args[0])).name} exited with {process.returncode}: {detail}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if reader.ident is not None:
                reader.join(timeout=5)
            process.stdout.close()
            if reader.is_alive():
                reader.join(timeout=1)
    return log
