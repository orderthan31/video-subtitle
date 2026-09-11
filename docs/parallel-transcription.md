# Parallel Transcription

Checkpoint before this change: 7a39622, pushed to origin/master.

## Runtime Contract

- A job still owns the single global worker slot. Only its transcription requests
  run concurrently; translation remains sequential in batches of up to 40 cues.
- Audio windows are at most 60 seconds, non-overlapping and bounded by retained
  timeline regions. Output cues remain sentence-level, not 60-second subtitles.
- At most three requests run concurrently. A completed request frees its slot
  immediately. Eligible retries are admitted before untouched windows; during a
  retry delay other windows may proceed. A 429 introduces shared cooldown.
- Each window gets at most three attempts INCLUDING the initial attempt per run.
  The SDK and transport retry loops do not multiply this budget. Validation,
  transient HTTP/network errors and total-request timeouts consume attempts.
  Authentication and other non-retryable failures stop admission immediately.
- On exhaustion, no further requests (including retries) start. Already running
  requests finish within a 125-second total deadline. Successful responses are
  saved even when a lower-numbered window failed. User cancellation/shutdown
  instead cancels in-flight requests promptly.
- Results are merged by window number, never by response completion order.

## Persistence and Progress

work/transcription/<identity>.json stores successful window results, attempts,
failure indices, attempt history and current counts. Identity includes model,
prompt, source size/mtime and window frame boundaries. Retry skips every valid
successful window, starts from the earliest missing window and grants a new
three-attempt budget while preserving history. Empty speech is a valid success.
Changing model, prompt, audio or window layout invalidates this receipt.

The worker stores transcription_progress in job metadata: total, completed,
in_flight, retrying, failed, draining. The existing frontend polling renders a
segment-count progress bar, not an estimated remaining-time or audio-duration
percentage. Short final/retained-region windows count as one segment too.

On partial failure, work/partial-transcript.json stores all successful cues with
original-timeline timestamps. work/partial-original.srt contains only the
contiguous successful prefix, filtered and laid out as subtitles. Neither is
published as a complete result. Translation and encoding do not start. The
development preservation marker must remain enabled to retain failed job media;
ordinary cleanup policy is otherwise unchanged.

Legacy sequential receipts and experimental 120-second receipts are retained
but not reused by the parallel 60-second stage. Completed jobs are not rerun automatically. Deploy only
when the worker is idle, so an active job is not interrupted to change policy.

## Verification

Offline tests cover concurrent refill, the three-request ceiling, retry budget,
stop-and-drain, resume with later successes, cancellation, shared 429 cooldown,
empty speech, timeline restoration and partial worker outputs. SDK MockTransport
tests verify that one scheduler attempt makes one transport attempt.

An opt-in live check is available as scripts/check-parallel-transcription.py.
It retains up to six minutes of source audio and all request/response traces.
It never runs from unit tests. Reusing its output folder reuses the retained
excerpt and successful receipts, so use a fresh folder for a different input.

The first live 120x3 check returned two successful windows. Window 1 exhausted
three attempts: although HTTP responses were 200/STOP, timestamps extended to
about 147 seconds for a 120-second input. Strict validation rejected them; no
timestamp stretching, truncation or silent cue deletion was used. This confirms
failure preservation, NOT transcription quality parity with 60-second windows.
Translation parallelism remains deferred pending real transcription evaluation.

The subsequent 60x3 check used the exact same retained six-minute excerpt in a
fresh output folder. All six windows passed on their first attempt: 28 sentence
cues, 20.3 seconds elapsed. There were no out-of-range timing validation errors.
This checks response structure and timestamp bounds, not listening-based timing
or transcript accuracy. The deployed window size is now 60 seconds; concurrency,
retry budget, drain behavior and progress reporting are unchanged.
