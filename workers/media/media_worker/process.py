from pathlib import Path
from contextlib import ExitStack
import os
import subprocess
from threading import Event, Thread
import time


class Cancelled(Exception):
    pass


class ProcessLogLimitError(RuntimeError):
    pass


def run_process(args, *, cwd: Path, log_name: str, check=lambda: None, timeout=7200, max_log_bytes=None, stderr_log_name=None):
    """Bound saved output and fail rather than parse an incomplete process log."""
    limit = int(os.getenv("MAX_PROCESS_LOG_BYTES", str(8 * 1024**2))) if max_log_bytes is None else max_log_bytes
    if type(limit) is not int or not 4096 <= limit <= 64 * 1024**2:
        raise ValueError("MAX_PROCESS_LOG_BYTES must be between 4096 and 67108864")
    log = cwd / log_name
    if stderr_log_name is not None and (cwd / stderr_log_name).resolve() == log.resolve():
        raise ValueError('stdout and stderr require different log files')
    started = time.monotonic()
    overflow = Event()
    failures = []
    tails = {}
    with ExitStack() as stack:
        output = stack.enter_context(log.open('wb'))
        errors = stack.enter_context((cwd / stderr_log_name).open('wb')) if stderr_log_name else None
        process = subprocess.Popen(
            [str(arg) for arg in args], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE if errors is not None else subprocess.STDOUT, bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        def drain(stream, destination, name):
            written = 0
            try:
                while chunk := stream.read(65536):
                    tails[name] = (tails.get(name, b'') + chunk)[-3000:]
                    remaining = max(0, limit - written)
                    if remaining:
                        saved = chunk[:remaining]
                        destination.write(saved)
                        destination.flush()
                        written += len(saved)
                    if len(chunk) > remaining:
                        overflow.set()
            except (OSError, ValueError) as exc:
                failures.append(exc)

        # Drain both pipes concurrently: diagnostics must never corrupt structured stdout.
        streams = [(process.stdout, output, 'stdout')]
        if errors is not None:
            streams.append((process.stderr, errors, 'stderr'))
        readers = [Thread(target=drain, args=entry, name='media-process-' + entry[2], daemon=True)
                   for entry in streams]
        try:
            for reader in readers:
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
            for reader in readers:
                reader.join(timeout=5)
            if any(reader.is_alive() for reader in readers):
                raise RuntimeError("Process output stream did not close after exit")
            if failures:
                raise OSError("Cannot save media process output") from failures[0]
            if overflow.is_set():
                raise ProcessLogLimitError(f"Process log exceeded {limit} bytes ({log_name})")
            check()
            if process.returncode:
                detail = (tails.get('stderr') or tails.get('stdout', b'')).decode("utf-8", errors="replace")
                raise RuntimeError(f"{Path(str(args[0])).name} exited with {process.returncode}: {detail}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=5)
            for stream, _, _ in streams:
                stream.close()
            for reader in readers:
                if reader.ident is not None and reader.is_alive():
                    reader.join(timeout=1)
    return log
