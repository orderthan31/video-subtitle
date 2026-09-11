from contextlib import contextmanager
import signal


class WorkerStopping(Exception):
    def __init__(self):
        super().__init__("Worker shutting down; upload again")


@contextmanager
def shutdown_signals(stop):
    """Signal handlers only request shutdown; normal worker code performs cleanup."""
    previous = {}
    def request_stop(signum, frame):
        stop.set()
    try:
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        for signum in signals:
            previous[signum] = signal.signal(signum, request_stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
