from pathlib import Path
import subprocess
import time


class Cancelled(Exception):
    pass


def run_process(args, *, cwd: Path, log_name: str, check=lambda: None, timeout=7200):
    """Drain output to disk, checking cancellation without unbounded pipe buffers."""
    log = cwd / log_name
    started = time.monotonic()
    with log.open("wb") as output:
        process = subprocess.Popen(
            [str(arg) for arg in args], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            while process.poll() is None:
                check()
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"Process exceeded {timeout} seconds")
                time.sleep(0.2)
            check()
            if process.returncode:
                raise RuntimeError(f"{Path(str(args[0])).name} exited with {process.returncode}; see {log.name}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return log
