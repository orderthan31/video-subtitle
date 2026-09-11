# Silero input gain and mono comparison

2026-09-12. CPU-only offline inference on the same preserved 1001.mp4 analysis
WAV. No training, Gemini calls, transcription, translation or production changes.
Original audio and SRT are unchanged; input hashes verified on every run.

## Method

Reuse the pinned ONNX model and settings documented in silero-cpu-poc.md.
Stereo channels retain independent recurrent states; either can preserve audio.
Mono is the arithmetic mean of left/right channels before gain. Fixed gain is
applied to float samples, then clipped to [-1,1]; clipping is counted before
padding. This is not loudness normalization, AGC, compression or denoising.
All exported segments use the original stereo audio, not the modified input.

Initially compare unchanged stereo (prior run), unchanged mono, stereo +12 dB
and mono +12 dB. After observing clipping in amplified inputs, add mono -12 dB
as an exploratory attenuation control. All newly tested inputs run the complete
4752.405-second file, with thresholds 0.3/0.5/0.7. No selection using subtitle
metrics is promoted to production.

## Results at Threshold 0.5

| Input | Retained seconds | Segments | Subtitle overlap loss | New clipped samples | Inference seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original stereo, prior run | 34.000 | 11 | 97.35% | Not measured | 17.96 |
| Mono, unchanged level | 33.904 | 11 | 97.32% | 0% | 17.32 |
| Stereo +12 dB | 5.592 | 6 | 99.57% | 6.377% | 19.78 |
| Mono +12 dB | 29.680 | 7 | 97.68% | 6.068% | 17.62 |
| Mono -12 dB | 42.464 | 17 | 97.31% | 0% | 17.31 |

At threshold 0.3, retained time / subtitle loss are respectively:
42.664 s / 96.72%, 40.936 s / 96.81%, 13.216 s / 98.99%,
33.664 s / 97.34%, and 85.432 s / 95.11%.
Even the best observed case still loses over 95% of reference subtitle time.
Reference is the existing 426-cue SRT with 1145.76 seconds of union coverage,
not independently verified speech annotations.

Mono unamplified RMS is 0.12228 (approximately -18.25 dBFS); peak is 1.0.
The file is not uniformly a very quiet input. This whole-file aggregate does
not establish the volume of individual dialogue segments or absence of existing
source distortion. Counting newly clipped samples does not measure distortion
already present in the source.

## Interpretation

Simple channel averaging and fixed input gain do not resolve this failure.
Positive gain introduces substantial hard clipping, so it is not evidence that
all possible loudness preprocessing fails. Attenuation avoids new clipping but
still leaves unusable retention. No root cause has been established, and these
results do not show that the subtitle content is wrong.

No deployment is appropriate. A second detector and manual checks of representative
audio/subtitle alignment are more informative next steps than further blind gain
changes. Adaptive normalization and channel selection remain untested.

## Runtime and Artifacts

CPUExecutionProvider only, one intra/inter-op thread. New runs take 17.31-19.78
seconds for inference plus WAV streaming and input statistics; model loading
about 0.032 seconds. Process peak working set through inference is 72.3-72.7 MiB.
End-to-end script bodies take 18.80-21.24 seconds, excluding interpreter startup,
imports and the earlier MP4 decode. The old baseline lacks the new statistics
overhead; these are single-run observations, not precise speed rankings.

Ignored artifacts under data/rule-based-audio-analysis:
- 1001-input-mono0
- 1001-input-stereo12
- 1001-input-mono12
- 1001-input-mono-minus12

Each contains report.json, probabilities.npy, intervals.json and actual original
audio segment WAVs with timestamp manifests. Segment frame counts verified.
Eight tests pass, including new PCM identity, channel averaging and clipping tests.

```powershell
.tools/vad-poc/Scripts/python.exe scripts/poc-silero-cpu.py --model .tools/vad-poc/silero_vad.onnx --prior data/rule-based-audio-analysis/1001-v2 --srt data/user-preview/jobs/aa9c3aec0d5f4372b175aef4a8ca1472/output/translated.srt --out data/rule-based-audio-analysis/1001-input-new --mono --gain-db 12
.tools/vad-poc/Scripts/python.exe scripts/test-silero-poc.py
```

Omit --mono to retain stereo. --gain-db defaults to zero; negative values attenuate.
Choose a fresh output directory for each run. Production dependencies unchanged.
