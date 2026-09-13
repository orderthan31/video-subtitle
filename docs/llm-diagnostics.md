# LLM failure diagnostics

The initial diagnostics revision (44357ab) was deployed on 2026-09-13. Subsequent
storage/deadline and input changes are source-only until explicitly deployed.
Existing logs cannot retroactively provide missing connection causes.

## Where to look

- Worker logs contain payload-free JSON after the `llm_diagnostic` prefix (INFO).
- `work/transcription/<key>.json` and `work/translation/<key>.json` retain the
  existing `history` fields and add `diagnostics.identity` and `diagnostics.events`
  for each completed attempt, including failed attempts.
- Identity includes job ID, queue ID, stage, zero-based segment, attempt number
  and a unique attempt ID. A manual resume generates new attempt IDs even when
  its attempt counter starts over. Old checkpoint history remains readable.
- `request_started.trace_id` links to `work/llm/<trace_id>/request.json` and
  `response.json`. Request files also include `diagnostic_context`.
- Raw request/response preservation is unchanged. Unlike those intentional raw
  traces, new diagnostic events do not contain audio, subtitle text, prompts,
  arbitrary exception messages, URL query strings, cookies or authorization.

## Evidence and interpretation

| Evidence | Meaning |
| --- | --- |
| `remote_rate_limit`, HTTP 429 | The HTTP endpoint returned a rate limit; not inferred from a generic retry exception. |
| `remote_http_error`, HTTP 5xx | An HTTP server returned an error. Check endpoint host and request ID; an intermediary may be responsible. |
| `authentication_or_permission_rejected`, HTTP 401/403 | Authentication or permission rejection, not proof of a provider outage. |
| `http_request_rejected`, other HTTP 4xx | Request rejected; inspect existing raw response to distinguish request/configuration issues. |
| `network_path_unknown` | Connection/read/transport failure. Does not establish whether our network, a proxy or Gemini caused it. |
| `dns_resolution_failure` / `tls_failure` | Nested exception identifies DNS/TLS failure. Still not an attribution to a specific operator. |
| `local_connection_pool_timeout` | Local HTTP connection pool acquisition timed out. |
| `remote_output_invalid_json` / incomplete or blocked output | Received output failed parsing/completeness requirements. |
| `output_rejected_by_local_validator` | Our validator rejected returned content. A safe reason code distinguishes sentence timestamps, sentence structure/text, or translation output shape. This is not proof the validator is correct. |
| `local_queue_deadline` | The queue cancelled its operation at the existing 125-second deadline. The underlying reason it was slow remains unknown. |
| `local_request_deadline` | The provider's network/body-read deadline expired, excluding raw trace persistence in the revised worker. |
| `local_storage_limit` | Local quota or free-space guard stopped an attempt without a paid retry. |
| `operation_timeout` | The operation itself raised TimeoutError; not mislabeled as the queue deadline. |
| `cancellation_cause_unknown` | Request cancellation observed. Correlate a queue deadline or worker cancellation; do not assume Gemini failed. |
| `unclassified_exception` | Insufficient classification evidence; inspect other events in the same attempt. |

Exception chains include bounded type names and numeric errno/winerror, never
free-form messages. Headers are allowlisted to request/trace IDs and Retry-After,
with character/length checks and API-key exclusion. HTTP status is captured as
soon as headers arrive, even when reading the response body later fails.

Elapsed durations use a monotonic clock; event timestamps are UTC. Queue finish
events record retry eligibility, exhaustion, scheduling, delay and shared
cooldown. A queued retry can still be prevented by another exhausted segment,
cancellation or shutdown. Existing retry/admission/drain behavior is retained.
Inspect the entire event sequence: a final generic exception does not override
earlier concrete HTTP or transport evidence.

Console logging failures are suppressed so a logging handler cannot trigger an
extra paid request. Checkpoint writes retain existing disk-capacity enforcement.
On cancellation or process termination before checkpoint publication, recent
events may exist only in worker logs; a hard process kill can lose buffered logs.

## Verification

Tests use the official SDK with HTTP mock transport, no external model calls.
Coverage includes 429/503/401, nested DNS/TLS exceptions, body-read failures after
HTTP 200, invalid model JSON, local validation, queue deadlines, cancellation,
three-attempt exhaustion, parallel trace correlation and logging-handler failure.

Run with `PAID_LLM_ENABLED=false` in the test subprocess only. Do not modify the
live environment or rebuild/restart Docker as part of this change.
