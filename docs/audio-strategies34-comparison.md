# 1001.mp4: frequency filtering and adaptive background thresholds

2026-09-12. Offline experiment only. No AI models, transcription, translation,
paid requests, production changes, or server restarts.

## Fixed Method

Reused the preserved original-time 16 kHz stereo analysis WAV from the previous
experiment. Each channel must independently qualify for exclusion. Parameters
were fixed before checking subtitle overlap; no tuning to the reference SRT.

Strategy 3 applies FFmpeg two-pole highpass at 150 Hz and two-pole lowpass at
4500 Hz to an analysis copy only. Segment WAVs retain the original unfiltered sound.

Strategy 4 estimates the per-channel 10th percentile over 30-second blocks,
clamps the floor to -75 through -30 dBFS, limits changes to 3 dB per block, and
interpolates block centers. Quiet onset is floor + 6 dB, clamped to -45 through
-32 dBFS; release is 3 dB above onset. These are experimental thresholds, not
validated speech detection. Offline interpolation can use future samples.

All new variants use 20 ms RMS frames, >=3-second exclusion runs, 300 ms padding,
and merge retained intervals separated by <=400 ms. The matched control uses
the same rules but unfiltered audio and a fixed -45/-42 dBFS onset/release.
This control is not production's FFmpeg sample-based silence detector.
The combined case estimates its floor from the filtered analysis audio.

## Results

Audio duration: 4752.4053125 s. Reference: 426 translated SRT cues, union 1145.76 s.

| Variant | Retained seconds | <=60s segments | Cue overlap loss | Affected cues |
| --- | ---: | ---: | ---: | ---: |
| Existing saved 5s filter | 4528.87 | 80 | 0 s | 0 |
| Previous rule experiment | 4521.34 | 81 | 0 s | 0 |
| Matched control | 4517.46 | 81 | 0 s | 0 |
| Strategy 3 only | 4505.18 | 81 | 0 s | 0 |
| Strategy 4 only | 4406.86 | 91 | 0.532 s | 1 |
| Strategies 3 + 4 | 4338.18 | 96 | 0.552 s | 1 |

Against the production saved result, combined removal saves 190.689625 s
(3:10.690), or 4.21% of previously retained audio. Against the previous rule
experiment it saves 183.16 s. Strategy 3 alone saves 23.689625 s versus production,
but only 12.28 s versus the matched control. Strategy 4 saves 110.60 s versus
the matched control; combining both saves 179.28 s versus that control.

Combined filtering creates 29 retained intervals and 96 requests under the
current independent-span splitting policy, versus 6 intervals and 80 requests
originally. No calls were actually submitted. Lower audio length does not prove
lower total cost or faster completion, given increased request overhead.

Cue #7, original time 00:20:45.048 through 00:20:47.848, is partially clipped.
Combined loss is 0.552 s (0.04818% of all reference cue time); no cue is fully
removed. This is timeline overlap loss, not a measured loss of spoken words.
No listening assessment or new recognition was performed. Existing subtitle
omissions are invisible to this test. Noise and music cannot be identified
semantically by these rules. Do not assume the clipped portion is disposable.

The >50% source-removal warning did not trigger for any variant. Raw experimental
results are reported without a fallback so risks are visible.

## Artifacts and Verification

Local ignored directory: `data/rule-based-audio-analysis/1001-strategies34-v1/`.
Contains report.json (including affected cues), intervals.json, features.npz,
analysis-bandpass.wav, segments.json, and 96 actual unfiltered segment WAVs in
combined-segments. The manifest stores each segment's original start/end and
concatenated processed start; duration determines its processed end.
cue7-original-context.wav is a nine-second original-audio excerpt starting at
00:20:42 for manual listening. Original media and subtitle files were not edited.

SHA-256 checks confirm the reused analysis WAV, reference SRT and prior interval
file are unchanged. All emitted segment frame counts were checked against their
manifest ranges; segment count matches the calculated 96. Eight synthetic tests
pass, including both-channel retention, threshold limits and padding.

```powershell
python scripts/compare-audio-strategies.py --prior data/rule-based-audio-analysis/1001-v2 --srt data/user-preview/jobs/aa9c3aec0d5f4372b175aef4a8ca1472/output/translated.srt --out data/rule-based-audio-analysis/1001-strategies34-new --ffmpeg .tools/ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe
python scripts/test-rule-audio.py
```

Requires NumPy and FFmpeg only. Choose a new output directory on each run.
Production settings remain unchanged pending evaluation of clipped audio.
