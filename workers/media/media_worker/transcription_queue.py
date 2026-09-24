"""Bounded Gemini segment execution with durable results and stop-and-drain."""
import asyncio
from collections import deque
import time

from video_service.storage import read_json, write_json_atomic
from video_service.capacity import StorageLimitError
from .llm_diagnostics import attempt_context, new_context, emit, exception_details
from .worker_storage import disk_call
from .content_block import ContentBlockedError


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
                            failure_type=PartialTranscriptionError, operation_timeout=125, blocked_result=None):
    results = {}
    history = []
    saved_blocks = []
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
                saved_blocks = [b for b in saved.get("content_blocks", []) if b["segment"] - 1 in results]
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            results = {}
            history = []
    pending = deque(index for index in range(count) if index not in results)
    running = {}
    retries = {}
    counts = {}
    failed = []
    content_blocks = saved_blocks
    halted = False
    cooldown = 0
    diagnostics = {}

    def record(index, outcome, **fields):
        context = diagnostics[index]
        token = attempt_context.set(context)
        try:
            emit("queue_attempt_finished", outcome=outcome,
                elapsed_seconds=round(time.monotonic() - context["started"], 3), **fields)
        finally:
            attempt_context.reset(token)
        history.append({"segment": index, "attempt": counts[index], "outcome": outcome,
            "diagnostics": {"identity": context["identity"], "events": context["events"]}})

    async def publish():
        snapshot = {"total": count, "completed": len(results), "in_flight": len(running),
                    "retrying": len(retries), "failed": len(failed), "draining": halted and bool(running),
                    "content_blocks": list(content_blocks)}
        if path is not None:
            await disk_call(write_json_atomic, path, {"count": count, "results": {str(k): v for k, v in results.items()},
                "failed": failed, "content_blocks": content_blocks,
                "attempts": counts, "history": history, "progress": snapshot},
                before_write=before_write)
        await disk_call(progress, snapshot)

    async def invoke(index):
        # A total deadline also bounds requests whose server streams very slowly.
        token = attempt_context.set(diagnostics[index])
        task = asyncio.create_task(operation(index, counts[index]))
        try:
            if operation_timeout is None:
                return await task
            return await asyncio.wait_for(task, timeout=operation_timeout)
        except TimeoutError:
            emit("operation_timeout", category="local_queue_deadline" if task.cancelled() else "operation_timeout",
                deadline_seconds=operation_timeout)
            raise
        finally:
            attempt_context.reset(token)

    try:
        await publish()
        while pending or retries or running:
            await disk_call(check)
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
                    diagnostics[index] = new_context(segment=index, attempt=counts[index],
                        stage="translation" if issubclass(failure_type, PartialTranslationError) else "transcription",
                        job_id=path.parent.parent.parent.name if path is not None else None,
                        queue_id=path.stem if path is not None else None)
                    running[asyncio.create_task(invoke(index))] = index
                    admitted = True
                if admitted:
                    await publish()
            if not running:
                if halted:
                    break
                await asyncio.sleep(0.25)
                continue
            done, _ = await asyncio.wait(running, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            # Inspect every completed request before admitting any new work.
            for task in sorted(done, key=lambda item: running[item]):
                index = running.pop(task)
                validating = False
                try:
                    result = task.result()
                    validating = True
                    results[index] = validate(index, result)
                    record(index, "success", retryable=False)
                except ContentBlockedError as exc:
                    content_blocks.append({"segment": index + 1, "reason": exc.reason, "provider": exc.provider})
                    record(index, type(exc).__name__, retryable=False, retry_scheduled=False,
                        category="remote_content_blocked", block_reason=exc.reason, failure_phase="operation")
                    if blocked_result is None:
                        failed.append(index)
                        halted = True
                    else:
                        results[index] = validate(index, blocked_result(index))
                except StorageLimitError as exc:
                    record(index, type(exc).__name__, retryable=False, retry_scheduled=False,
                        category="local_storage_limit", failure_phase="storage")
                    failed.append(index)
                    halted = True
                except (RetryableTranscriptionError, ValueError, TimeoutError) as exc:
                    exhausted = counts[index] >= attempts
                    delay = max(2 ** (counts[index] - 1), getattr(exc, "delay", 0))
                    record(index, type(exc).__name__, retryable=True, exhausted=exhausted,
                        retry_scheduled=not exhausted, delay_seconds=None if exhausted else delay,
                        shared_cooldown=getattr(exc, "shared_cooldown", False),
                        failure_phase="output_validation" if validating else "operation",
                        **exception_details(exc))
                    if counts[index] >= attempts:
                        failed.append(index)
                        halted = True
                    else:
                        delay = max(2 ** (counts[index] - 1), getattr(exc, "delay", 0))
                        retries[index] = time.monotonic() + delay
                        if getattr(exc, "shared_cooldown", False):
                            cooldown = max(cooldown, retries[index])
                except Exception as exc:
                    record(index, type(exc).__name__, retryable=False, retry_scheduled=False,
                        failure_phase="output_validation" if validating else "operation", **exception_details(exc))
                    failed.append(index)
                    halted = True
            if done:
                await publish()
        if failed:
            error = failure_type(results, sorted(failed))
            error.content_blocks = sorted(content_blocks, key=lambda item: item["segment"])
            raise error
        return results
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


run_transcription_queue = run_segment_queue
