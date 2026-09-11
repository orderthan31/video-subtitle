"""Bounded Gemini segment execution with durable results and stop-and-drain."""
import asyncio
from collections import deque
import time

from video_service.storage import read_json, write_json_atomic


class RetryableTranscriptionError(Exception):
    def __init__(self, message, delay=1, shared_cooldown=False):
        super().__init__(message)
        self.delay = delay
        self.shared_cooldown = shared_cooldown


class PartialTranscriptionError(RuntimeError):
    def __init__(self, results, failed):
        super().__init__(f"Transcription stopped at segment(s) {', '.join(str(i + 1) for i in failed)}; retry to resume.")
        self.results = results
        self.failed = failed
        self.segments = []
        self.prefix = []


class PartialTranslationError(PartialTranscriptionError):
    def __init__(self, results, failed):
        super().__init__(results, failed)
        self.args = (f"Translation stopped at batch(es) {', '.join(str(i + 1) for i in failed)}; retry to resume.",)


async def run_segment_queue(count, operation, check, progress, *, path=None, before_write=None,
                            validate=lambda index, result: result, concurrency=3, attempts=3,
                            failure_type=PartialTranscriptionError):
    results = {}
    history = []
    if path is not None:
        try:
            saved = read_json(path)
            if saved["count"] == count:
                history = saved.get("history", [])
                if not isinstance(history, list):
                    history = []
                for key, result in saved["results"].items():
                    index = int(key)
                    if 0 <= index < count:
                        try:
                            results[index] = validate(index, result)
                        except (ValueError, TypeError, KeyError):
                            pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            results = {}
            history = []
    pending = deque(index for index in range(count) if index not in results)
    running = {}
    retries = {}
    counts = {}
    failed = []
    halted = False
    cooldown = 0

    def publish():
        snapshot = {"total": count, "completed": len(results), "in_flight": len(running),
                    "retrying": len(retries), "failed": len(failed), "draining": halted and bool(running)}
        if path is not None:
            write_json_atomic(path, {"count": count, "results": {str(k): v for k, v in results.items()},
                "failed": failed, "attempts": counts, "history": history, "progress": snapshot},
                before_write=before_write)
        progress(snapshot)

    async def invoke(index):
        # A total deadline also bounds requests whose server streams very slowly.
        return await asyncio.wait_for(operation(index, counts[index]), timeout=125)

    try:
        publish()
        while pending or retries or running:
            check()
            now = time.monotonic()
            if not halted and now >= cooldown:
                admitted = False
                while len(running) < concurrency:
                    ready = [index for index, due in retries.items() if due <= now]
                    if ready:
                        index = min(ready)
                        del retries[index]
                    elif pending:
                        index = pending.popleft()
                    else:
                        break
                    counts[index] = counts.get(index, 0) + 1
                    running[asyncio.create_task(invoke(index))] = index
                    admitted = True
                if admitted:
                    publish()
            if not running:
                if halted:
                    break
                await asyncio.sleep(0.25)
                continue
            done, _ = await asyncio.wait(running, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            # Inspect every completed request before admitting any new work.
            for task in sorted(done, key=lambda item: running[item]):
                index = running.pop(task)
                try:
                    results[index] = validate(index, task.result())
                    history.append({"segment": index, "attempt": counts[index], "outcome": "success"})
                except (RetryableTranscriptionError, ValueError, TimeoutError) as exc:
                    history.append({"segment": index, "attempt": counts[index], "outcome": type(exc).__name__})
                    if counts[index] >= attempts:
                        failed.append(index)
                        halted = True
                    else:
                        delay = max(2 ** (counts[index] - 1), getattr(exc, "delay", 0))
                        retries[index] = time.monotonic() + delay
                        if getattr(exc, "shared_cooldown", False):
                            cooldown = max(cooldown, retries[index])
                except Exception as exc:
                    history.append({"segment": index, "attempt": counts[index], "outcome": type(exc).__name__})
                    failed.append(index)
                    halted = True
            if done:
                publish()
        if failed:
            raise failure_type(results, sorted(failed))
        return results
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


run_transcription_queue = run_segment_queue
