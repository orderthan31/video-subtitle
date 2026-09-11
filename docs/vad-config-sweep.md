# Cached Silero configuration sweep

2026-09-12. No inference, training, paid API requests, production edits or audio
changes. Reused the per-channel probabilities from the CPU POC.

Compared 306 configurations: 17 onset thresholds (0.000001 through 0.5), release
threshold equal to 25%, 50% or 100% of onset, minimum non-speech 3 or 5 seconds,
padding 0.3, 0.6 or 1 second. Retained-gap merge stays 0.4 seconds. Short speech
detections are never discarded. Either channel preserves audio.

The original 234-configuration sweep stopped at onset 0.0001 and found no
configuration with <=1 second of loss in the first half. After inspecting the
probability distribution, four lower thresholds were added. All results are
exploratory, not independent validation. Although selection uses first-half
metrics and reports second-half metrics separately, this recording and its
whole-file distribution have already been examined.

## Results

| Setting | Retained seconds | Reference loss seconds |
| --- | ---: | ---: |
| Existing saved silence filter | 4528.87 | 0 |
| Best zero-loss configuration in grid | 4698.75 | 0 |
| Lowest loss among grid settings shorter than existing filter | 4478.45 | 3.504 |
| Onset 0.00001, release 0.000005, 3s, 0.3s padding | 4607.06 | 2.532 |
| Onset 0.0001, release 0.00005, 3s, 0.3s padding | 2594.26 | 309.037 |
| Onset 0.001, release 0.0005, 3s, 0.3s padding | 409.79 | 949.538 |

Best zero-loss setting is onset/release 0.000003, minimum non-speech 3 seconds,
padding 0.3 seconds. It preserves 169.88 more seconds than the existing silence
filter and creates 86 segments versus 80. It removes just 53.656 seconds from the
original 4752.405 seconds. First-half selection for loss limits 0, 0.1 and 1 second
all chooses this setting; the second half also has zero reference overlap loss.

The lowest-loss configuration shorter than the existing filter uses onset 0.00003,
release 0.0000075, minimum 5 seconds and padding 1 second. It saves only 50.42
additional seconds while clipping 3.504 seconds of reference subtitle time.
This choice used full-file outcomes and is not a validated recommendation.

The frame-center distribution uses the maximum channel probability. Median
probability inside subtitle intervals is 0.00005245, versus 0.00002229 outside.
The interquartile ranges overlap substantially: inside 0.00002381-0.00014946,
outside 0.00001019-0.00005457. A higher score inside cues does not provide clean
separation. Subtitle intervals are imperfect labels and include pauses; outside
intervals may also contain speech omitted by the existing transcript.

Conclusion: configuration-only tuning can restore subtitle coverage on this file,
but the tested settings do not beat the existing filter without reference loss.
This is not proof that every possible configuration fails. Input gain, full-file
mono preprocessing and different models remain untested here because they require
new inference; raw cached probabilities cannot evaluate those changes.

## Artifacts and Checks

`data/rule-based-audio-analysis/1001-vad-config-v2/` contains report.json,
grid.json with all 306 results, and selected-intervals.json. The initial narrower
sweep remains in 1001-vad-config-v1. Existing data hashes match before/after.
Sweep runtime was 3.49 seconds, excluding interpreter startup/imports.
Three synthetic tests validate vectorized hysteresis against a sequential state
machine, initial state behavior and interval clipping.

```powershell
.tools/vad-poc/Scripts/python.exe scripts/sweep-vad-config.py --poc data/rule-based-audio-analysis/1001-silero-cpu-v1 --srt data/user-preview/jobs/aa9c3aec0d5f4372b175aef4a8ca1472/output/translated.srt --out data/rule-based-audio-analysis/1001-vad-config-new
.tools/vad-poc/Scripts/python.exe scripts/test-vad-sweep.py
```

Only NumPy is required. No model runtime is imported by the sweep script.
