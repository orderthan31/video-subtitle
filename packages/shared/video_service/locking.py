"""Non-blocking OS locks; released automatically when a process exits."""
from contextlib import contextmanager
import os


class JobBusyError(Exception):
    pass


@contextmanager
def job_lock(repository, job_id, scope="state"):
    repository.job_dir(job_id)
    if scope not in {"state", "execution"}:
        raise ValueError("Invalid lock scope")
    directory = repository.storage_root / ".locks"
    directory.mkdir(exist_ok=True)
    with (directory / f"{job_id}.{scope}.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise JobBusyError(job_id) from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise JobBusyError(job_id) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
