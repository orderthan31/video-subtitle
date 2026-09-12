# Translation input deduplication

Status: implemented and tested locally; NOT deployed. The user requires the
current two jobs and their Docker services to keep running uninterrupted.
Do not recreate/restart the worker or backend to apply this change yet.

## Policy

- Leave transcription, original timing restoration and saved transcript unchanged.
- Group only adjacent, exactly equal text. No punctuation stripping, fuzzy
  matching, cross-video cache or global repeated-phrase dictionary.
- Share translation when both intervals are proven pieces of the same source
  sentence in `cross-boundary.json`, even if a removed gap separates them.
- Otherwise share only across gaps of 0 to 0.3 seconds (floating-point tolerance),
  with an at-most-eight-second group span. Keep genuine overlaps separate.
- Send one text per group, in order, through the existing 40-item, three-concurrent
  request queue. Expand each translated string back onto every original interval.
  Do not stretch subtitles across silence, remove a cue, or alter timestamps.
- Separately spoken but nearby identical phrases may share a translation. Without
  speaker/word alignment this does not prove they were one utterance. The policy
  avoids semantic deduplication and preserves every display interval.

## Retry and audit

Completed worker checkpoints remain reusable. Before changing batching, the
provider checks for its pre-change cache namespace using the exact original
prompts, model, target language and video-description context. If that checkpoint
exists, it continues the old layout and reuses successful batches, including
partial results. Existing paid work is not invalidated merely to save new tokens.
New grouped requests have a distinct cache namespace.

Both partial completed output and the contiguous successful prefix are expanded
back to the original intervals. Existing retry, stop-and-drain and progress batch
counts are retained. Progress adds input-segment, translation-unit and saved-unit
counts. `translation-input-plan.json` records interval groups and savings without
duplicating source dialogue text.

## Read-only 1094 estimate

The current 1094 transcript was read without changing any live file or invoking
Gemini. With this policy:

| Measure | Before | After |
| --- | ---: | ---: |
| Translation entries | 1,110 | 1,047 |
| 40-entry requests | 28 | 27 |
| Source text characters | 11,442 | 10,789 |
| Output subtitle intervals | 1,110 | 1,110 |

This removes 63 entries (5.68%) and 5.71% of source characters. It is not a billed
token or latency measurement: prompts, output lengths, model reasoning, retries
and three-way scheduling affect actual cost and elapsed time. No paid A/B test
was run. The currently processing jobs will not receive this change automatically.

## Verification

Full backend suite: 329 tests passed with paid calls disabled in the test process.

Tests cover provenance matching, long-gap protection, exact-text rules, overlap,
bounded repeated chains, floating-point boundaries, unchanged original intervals,
translation expansion, grouped failure/retry, legacy cache reuse, and the existing
parallel translation suite. All providers in these tests are local mocks; the live
worker's paid-enabled configuration is left untouched.
