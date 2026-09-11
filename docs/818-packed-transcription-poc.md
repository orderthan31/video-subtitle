# 818.mp4: no VAD, pack existing filtered audio into 60-second requests

2026-09-12. Explicit user-authorized paid transcription-only POC. No translation,
VAD, production edits, job state changes or published output replacement.

## Method

Reused the exact existing processed-audio.wav and timeline-map.json. The existing
silence filter is unchanged: this is not unfiltered original audio. Input remains
4545.09275 seconds. Six retained original-time spans are already concatenated in
that WAV; the experiment changes only request boundaries from per-span splitting
to continuous, maximum 60-second chunks. No added silence, overlap or samples lost.

Same Gemini 3.8 Flash provider, transcription prompt generator, source language,
schema, SDK, safety settings, concurrency three and bounded retry scheduler.
New isolated trace/cache directory prevents reuse of baseline Gemini responses.
Existing transcript text filtering and SRT formatting are applied to both sides.
The comparison references source-language transcript.json and original.srt, not
the translated subtitles. The baseline has 500 transcript entries / 501 SRT cues.

Each request has a piecewise original-time manifest. Returned sentences are
intersected with original spans to avoid bridging deleted time. Cross-boundary
sentences would be flagged; their text is not silently divided by guessing.
There were zero such sentences in this run, so no duplicate split text was needed.

## Results

| Item | Existing | Packed |
| --- | ---: | ---: |
| Audio duration | 4545.09275 s | 4545.09275 s |
| Planned requests | 80 | 76 |
| Observed HTTP calls with response | 82 | 76 |
| Transcript entries | 500 | 501 |
| Formatted SRT cues | 501 | 502 |
| Entries starting at/after 20 minutes | 483 | 476 |

All 76 new requests succeeded, no retry. Four requests contain multiple original
spans, crossing five original-time discontinuities in total. The final request
contains three original spans. No returned sentence crossed a discontinuity.
Live transcription wall time: 208.30 seconds (3:28.30).

111 baseline sentences exactly match a nearby new sentence after Unicode NFKC,
punctuation and whitespace normalization, preserving order and one-to-one matches.
Their mean absolute start/end differences are 0.245 / 0.242 seconds; maxima
1.012 / 1.000 seconds. This is a selected subset, not overall timestamp accuracy.

189 of 500 baseline sentences have >=0.8 character similarity to a candidate
whose time range overlaps or lies within 2 seconds. This heuristic is many-to-one
and not an accuracy score. Splitting/merging sentences can reduce this number even
when wording is preserved. Exact-match alignment is time-bounded because repeated
short replies can otherwise match unrelated scenes far apart.

13 baseline sentences have no candidate within that time tolerance, of which
7 start after 20 minutes. Those seven ranges (original time, seconds) are:
3196.085-3198.285, 3886.935-3888.385, 4428.785-4430.185,
4456.385-4457.985, 4459.585-4461.985, 4558.785-4560.085,
4636.899-4641.869. These warrant manual audio review; they are not proven omissions
of real speech because the old transcript is not independently verified truth.

## Timeline Coverage

After the first 20 minutes, old/new subtitle union durations are 1274.23 / 1292.90 s.
157.24 s of old coverage is absent in new coverage, while 175.91 s of new coverage
is absent in old coverage. Total new coverage is not reduced, but its placement
differs. This is transcription timing/output variation, not removed input audio.
Across the entire file the corresponding unmatched durations are 188.95 / 203.43 s.

## Tokens

| Usage | Existing observed | Packed observed |
| --- | ---: | ---: |
| Input | 138356 | 133768 |
| Output text | 20716 | 20326 |
| Thinking | 66088 | 63699 |
| Total | 225160 | 217793 |

Observed total token difference: 7367 fewer (3.27%). Baseline includes two extra
HTTP responses and 30 earlier transport failures, so this is not a controlled
packing-only token saving. Baseline unique-window input is 134826; against that,
packed input saves 1058 tokens (0.78%), consistent with four fewer prompts.
Output/thinking differences cannot be attributed to packing alone.

## Interpretation and Verification

Piecewise timestamp restoration worked for this run, and no source audio was
discarded by packing. However, the resulting transcript is not identical and the
seven later unmatched entries need listening review before a quality conclusion.
One stochastic rerun cannot separate packing effects from Gemini variability.
No manual listening or semantic ground-truth grading was performed.

Six offline tests pass: within-span mapping, discontinuities, exact boundaries,
duration conservation, repeated-text alignment and local timing differences.
Hashes of source processed audio, transcript, original SRT and timeline map
match before/after. Generated private comparison files remain outside Git.

Artifacts: data/transcription-comparison/818-packed60-v1/work/
- asis.srt / asis.json: unchanged source-language baseline snapshots.
- packed-mapped.srt / mapped.json: comparable new source-language result.
- packed-clock.json / mapped-raw.json: raw provider and mapped output.
- requests.json / timeline-map.json: original-time mappings.
- cross-boundary.json: empty in this run.
- differences.json: detailed private text comparisons and timing matches.
- report.json / llm / transcription: summary, full traces and retry checkpoints.

The initial postprocessing was corrected after live completion to apply the same
text filter/SRT wrapping as production and restrict repeated-text matches to nearby
times. Analysis was rerun with --analyze-only, without additional API requests;
the original live wall time and usage were retained.

```powershell
python scripts/compare-packed-transcription.py --run-live --job data/user-preview/jobs/3a884b7af6414e469e6d0b06f32a6070 --output data/transcription-comparison/818-packed60-new
python scripts/test-packed-transcription.py
```

Use --analyze-only alongside --run-live with an existing completed POC directory
to regenerate comparisons without network calls. Production remains unchanged.
