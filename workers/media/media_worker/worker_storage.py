"""Job-scoped capacity estimates and cancellation-safe off-loop disk work."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time

from video_service.capacity import used_bytes, assert_capacity, assert_free_space, StorageLimitError


_capacity = ContextVar("worker_capacity", default=None)


class CapacityEstimate:
    def __init__(self, root, interval=60):
        self.root = root
        self.interval = interval
        self.total = 0
        self.updated = None
        self.lock = threading.Lock()

    def check(self, quota, minimum_free, additional=0):
        with self.lock:
            now = time.monotonic()
            if self.updated is None or now - self.updated >= self.interval:
                self.total = used_bytes(self.root)
                self.updated = time.monotonic()
            if self.total + additional > quota:
                raise StorageLimitError("Service storage quota exceeded")
            assert_free_space(self.root, minimum_free, additional)
            # Charge the whole write, even on replacement/failure, until the next audit.
            self.total += additional


@contextmanager
def capacity_estimate(root):
    token = _capacity.set(CapacityEstimate(root))
    try:
        yield
    finally:
        _capacity.reset(token)


def check_capacity(root, quota, minimum_free, additional=0):
    estimate = _capacity.get()
    if estimate is not None and root == estimate.root:
        estimate.check(quota, minimum_free, additional)
    else:
        assert_capacity(root, quota, minimum_free, additional)


async def disk_call(function, *args, **kwargs):
    # A cancelled thread cannot be stopped; drain it before releasing job locks.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
