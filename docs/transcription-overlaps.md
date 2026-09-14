# Transcription Overlap Policy

- Validate each sentence independently: finite numeric times, non-negative start,
  positive duration, and clip bounds (existing 100 ms end tolerance retained).
- Sort sentences by start time and preserve overlapping speech. Log overlap counts;
  do not spend another provider call merely because different sentences overlap.
- Retain existing transcription checkpoint keys. New requests permit overlap while
  successful partial results remain reusable.
- Translate the original utterances, not the expanded display cues.
- Final subtitle layout uses millisecond start/end events. Only the shared interval
  contains both texts, with separate lines. Nested and chained overlaps are supported.
- Long text still uses the existing two-line, weighted pagination within each display
  interval. This can produce short cues; the editor flags cues under one second or
  over two lines for review. These are review heuristics, not speech-error claims.
- Original transcript and translation JSON remain the source of truth for timings.

## Recorded Response Recovery

`python scripts/recover-transcription-overlaps.py JOB_ID` is a dry run.
Add `--apply` to back up and update a failed transcription checkpoint. The command
takes execution/state locks, matches recorded request job/queue/segment identities,
requires HTTP 200 and STOP, and revalidates every sentence. Existing successful
segments and raw traces are never overwritten. It does not enqueue the job or call
Gemini. Run it inside the deployed worker container for live Docker storage.

## Verification

Backend suite: 361 tests passed; frontend suite: 22 tests passed. Regression cases
include the observed 54.34-57.17 / 56.45-57.50 overlap, nested intervals, touching
boundaries, repeat layout, and recovery identity mismatch / completed-result safety.
