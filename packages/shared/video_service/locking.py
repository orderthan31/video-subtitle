"""Non-blocking OS locks; released automatically when a process exits."""
from contextlib import contextmanager, ExitStack
import os
import time
import math


class JobBusyError(Exception):
    pass


@contextmanager
def wait_for_job_lock(repository, job_id, *, timeout=30, check=lambda: None):
    """Wait for a state lock without retrying errors raised by its protected body."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Lock timeout must be positive and finite")
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        while True:
            check()
            try:
                stack.enter_context(job_lock(repository, job_id))
                break
            except JobBusyError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for job state lock") from None
                time.sleep(min(0.05, remaining))
        check()
        yield


@contextmanager
def job_lock(repository, job_id, scope="state"):
    repository.job_dir(job_id)
    if scope not in {"state", "execution"}:
        raise ValueError("Invalid lock scope")
    directory = repository.storage_root / ".locks"
    directory.mkdir(exist_ok=True)
    with _file_lock(directory / f"{job_id}.{scope}.lock"):
        if scope == "execution":
            # Registration is serialized by this lock; abandoned leases are reclaimable.
            for lease in directory.glob(f"{job_id}.download.*.lock"):
                with _file_lock(lease):
                    pass
                lease.unlink(missing_ok=True)
        yield


@contextmanager
def download_lock(repository, job_id):
    """Allow concurrent readers while excluding execution, deletion and collection."""
    from uuid import uuid4

    repository.job_dir(job_id)
    directory = repository.storage_root / ".locks"
    directory.mkdir(exist_ok=True)
    lease = directory / f"{job_id}.download.{uuid4().hex}.lock"
    try:
        with ExitStack() as reader:
            # Register under the raw execution lock without excluding existing readers.
            with _file_lock(directory / f"{job_id}.execution.lock"):
                reader.enter_context(_file_lock(lease))
            yield
    finally:
        try:
            lease.unlink(missing_ok=True)
        except PermissionError:
            # A collector may briefly have the released lease open on Windows.
            pass


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
