# LLM input and request reliability

Source-only improvement following the 1093 input audit. Do not deploy implicitly.

## Storage and deadlines

- Worker capacity estimates are job-scoped, shared across I/O threads and audited
  at most once per 60 seconds on demand. Every write is conservatively charged in
  full until the next audit; actual free space is checked for each write/check.
- External writers can change usage between audits. This is not a cross-process
  quota reservation. Existing admission/reservation checks remain unchanged.
- Queue checks, progress callbacks, checkpoint writes and raw trace writes run
  outside the event loop. Cancellation drains started disk operations before
  releasing the worker's execution lock. No unbounded background writer is added.
- Gemini's 125-second total deadline surrounds network execution/body reading,
  not raw request/response persistence. HTTP inactivity timeout remains 120s.
  Production segment queues delegate the deadline to the provider; generic queue
  callers retain their original configurable operation deadline.
- Storage-limit failures stop/drain without retrying a paid operation. Original
  request/response traces and checkpoints remain preserved. A storage failure
  after remote success can still require a manual retry if no result was saved.
- A job-scoped cache removes repeated full scans; the first/periodic audit still
  takes time, but no longer blocks other in-flight Gemini network tasks.

Verification uses mocked Gemini transport and synthetic slow I/O, never paid
model calls. Tests cover charged writes, audit refresh, shared thread context,
cancellation draining, slow progress/trace persistence, actual network timeouts,
non-retryable storage failure, existing retries and checkpoint resume.

## Input protocol

- New transcription queues describe discontinuities in packed audio using
  clip-relative splice times. No silence or padding is inserted, and audio bytes
  and timeline restoration remain unchanged. The instruction asks for separate
  audible fragments without resetting the clip clock.
- New translation queues use global group IDs with `{id, text}` targets and
  responses. Missing, duplicate, unknown IDs or empty translations fail validation;
  reordered responses are safely realigned before expanding grouped cues.
- Two source cues before and after each batch are reference-only context. Each
  reference is capped at 1000 characters; target text is never truncated. No
  translated batch is needed, so three-way parallel execution remains possible.
- Context and IDs increase prompt size. They are quality/association safeguards,
  not a claim of lower per-request token cost. Live quality and latency have not
  been measured; unit tests cannot establish translation quality.
- Existing transcription v2 and translation v1/adjacent-v2 checkpoints keep their
  old prompts and batch layout, including missing-batch retries. New queues use
  separate v3 namespaces whose identities include actual prompts/context.
- Completed results are not invalidated or regenerated. Reusing a v3 checkpoint
  still skips every completed batch; stored aligned translations remain strings.

`local_io_finished` records trace persistence durations and other disk callbacks
that take at least one second. `local_request_deadline` identifies the provider's
network deadline; generic queue callers can still report `local_queue_deadline`.
