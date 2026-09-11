"""Non-blocking OS locks; released automatically when a process exits."""
from contextlib import contextmanager, ExitStack
import os
import time


class JobBusyError(Exception):
    pass


@contextmanager
def job_lock(repository, job_id, scope="state"):
    repository.job_dir(job_id)
    if scope not in {"state", "execution"}:
        raise ValueError("Invalid lock scope")
    directory = repository.storage_root / ".locks"
    directory.mkdir(exist_ok=True)
    with _file_lock(directory / f"{job_id}.{scope}.lock"):
        yield


@contextmanager
def _file_lock(path):
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise JobBusyError(path.name) from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise JobBusyError(path.name) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def encoding_slot(repository, limit, check=lambda: None):
    """Share an encoder limit across local workers without holding job state locks."""
    if not isinstance(limit, int) or not 1 <= limit <= 64:
        raise ValueError("MAX_ENCODING_JOBS must be between 1 and 64")
    directory = repository.storage_root / ".locks"
    directory.mkdir(exist_ok=True)
    with ExitStack() as stack:
        while True:
            check()
            for index in range(limit):
                try:
                    stack.enter_context(_file_lock(directory / f"encoder-{index}.lock"))
                except JobBusyError:
                    continue
                check()
                yield index
                return
            time.sleep(0.2)
