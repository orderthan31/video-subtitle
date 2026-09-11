# Parallel Translation

Translation now reuses the transcription scheduler: at most three concurrent
requests, immediate refill, at most three total attempts per batch per run,
shared 429 cooldown, no nested transport retries, and stop-and-drain on exhaustion.
Non-retryable failures stop admission immediately. The batch size remains 40
sentences and, without a video description, the prompt is byte-for-byte unchanged from sequential translation.
Target languages still run one at a time, preserving the three-request cap.

Successful batches are retained in work/translation/<identity>.json. Identity
includes model and all prompts. Results are validated for count and nonempty
strings, then assembled in source order with unchanged sentence timestamps.
Retry skips successful batches, including those after a failed batch. A partial
failure stores partial-translated*.json and a contiguous-prefix partial SRT;
encoding is not started with missing translations. Development preservation
continues to protect failed job files.

The web UI reports completed/total batches, requests in flight, retry wait and
draining state for the current target language. This is batch progress, not ETA.

## Optional Video Description

Uploads accept `video_description`, an optional string of up to 2000 characters.
Whitespace is trimmed; blank input retains the original translation prompt.
The description is persisted in job options, returned by the API and restored
when resuming an upload. Upload creation idempotency includes the description.
The form locks it after creation and clears it on successful upload completion.

Every translation batch and target language receives the same description as
quoted contextual reference for idiomatic transcreation: tone, register, humor
and character relationships. Explicit dialogue takes priority over conflicting
context. Meaning, negation, names, facts, entry count and order must be preserved;
the prompt forbids invented dialogue and instructions embedded in the description.
Transcription is unchanged. Description-bearing prompts have separate batch cache
identities and are retained in the existing private LLM traces. Empty descriptions
are omitted from whole-job checkpoint identity to preserve legacy retry caches.
No paid translation was used in the feature's unit tests.

## 1001.mp4 Comparison Results

The explicit comparison script is scripts/compare-parallel-translation.py.
It reads a completed job, snapshots the old translation and source transcript,
then uses a new work folder so no existing translation cache can stand in for
new Gemini requests. It never retranscribes or re-encodes the video and never
replaces published results. Reusing the comparison folder resumes its results;
use a new folder for a fresh performance experiment.

For the 79m12s 1001.mp4 recording, both runs used Gemini 3.8 Flash and identical
sets of 40-sentence prompts. Sequential: 11 calls, 79.27 seconds from first trace
start to last response. Parallel: 11 calls, 28.33 seconds on the same metric,
28.81 seconds for the full translation function. All parallel calls succeeded
without retry. The observed API-stage speedup is 2.80x (64.3% less elapsed time).
This is one historical-vs-new comparison, not a controlled repeated benchmark
or a measurement of total video pipeline speedup.

All 426 sentences and all 426 SRT timestamps were retained in order. 226 texts
were identical and 200 differed. Manual text review of the 200 differences found
mostly phrasing/spacing changes, but also semantic concerns. In particular cue
135 changes a negative expression into a positive assertion. Cues 108/110 show
divergent interpretations of an unusual source phrase; cue 387 expands a repeated
assertion into a less certain expression. Cue 390 changes a passive/potential
interpretation and needs audio/context review. Some new wording is better, such
as cue 241's grammar and cue 320's idiomatic expression.

Conclusion: concurrency/order/timing passed; semantic equivalence is NOT fully
approved. Identical prompts do not imply deterministic outputs, so one comparison
does not establish that parallelism caused the semantic changes. No source-audio
listening audit was done, and unchanged translations were not certified correct.
The original completed video/SRT remain untouched. Private comparison artifacts
are retained under data/translation-comparison/1001-parallel40-v1/work.
