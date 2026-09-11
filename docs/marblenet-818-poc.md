# 818.mp4: NVIDIA MarbleNet with fixed prior-best thresholds

2026-09-12. Reused the official model/environment from marblenet-cpu-poc.md.
CPU-only analysis, no Gemini requests, training or production filter changes.
818.mp4 was COMPLETED before this comparison began.

The prior-best setting means lowest reference loss among NVIDIA's tested pairs:
onset 0.3, offset 0.15. It does not mean a proven production-safe setting.
The same 0.63s window, 0.08s hop, batch 64, one CPU thread, >=3s non-speech
exclusion, 0.3s padding and 0.4s merge gap are retained. No threshold tuning on 818.

Input: preserved original-time 16 kHz mono work/audio.wav, not processed-audio.wav.
The channel-mean operation is identity for this mono input. No gain applied.
Reference: output/translated.srt, 501 cues, union duration 1373.75 seconds.
Audio duration: 4756.7573125 seconds. Video metadata duration is 4756.8 seconds.

| Variant | Retained audio seconds | <=60s segments | Lost subtitle seconds | Loss percent |
| --- | ---: | ---: | ---: | ---: |
| Existing saved filter | 4545.09275 | 80 | 0 | 0% |
| NVIDIA 0.3 / 0.15 | 2091.75731 | 267 | 400.577 | 29.1594% |

Relative to existing filtering, the candidate removes another 2453.33544 seconds
(40:53.335), or about 53.98% of previously retained audio. However, 230 of 501
reference cues are affected, including 81 whose entire time range is removed.
This is a failed retention result, not a deployable cost saving.
Fragmentation increases request count under current per-span splitting.
No Gemini calls were actually made by this POC.

For comparison, 1001.mp4 at the same 0.3/0.15 setting had 32.04% reference loss;
818.mp4 has 29.16%. This is similar behavior across the two recordings, not a
resolution of the failure. The script also calculates its existing 0.5 and 0.7
controls from the same probability pass (43.65% and 54.46% loss). Only the requested
0.3 setting is exported as audio segments.

Timing: 38.256 seconds inference including WAV streaming, 0.080 seconds model
restore, 39.575 seconds script body including hashes, metrics and exports.
Imports, environment setup and the earlier audio decode are excluded. One run.
Peak process working set through analysis: 672641024 bytes (641.5 MiB).
PyTorch 2.14.0+cpu; CUDA runtime null; all model parameters on CPU.

The source WAV, reference SRT and model hashes match before/after. The official
window-equivalence test passed, and all exported WAV frame counts/count match
their manifest. Original video, audio, subtitles and existing outputs are preserved.
Reference overlap loss is not measured spoken-word loss; subtitle timing can
include pauses or omit real speech. No manual listening assessment was performed.

Artifacts: data/rule-based-audio-analysis/818-nvidia-03 contains report.json,
probabilities.npy, intervals.json, segments.json and 267 original-audio WAV clips.
The report's generic preprocessing label was corrected after the run to explicitly
cover mono input; measured data and parameters were not changed.

```powershell
.tools/nemo-poc/Scripts/python.exe scripts/poc-marblenet-cpu.py --model .tools/nemo-poc/vad_multilingual_marblenet.nemo --audio data/user-preview/jobs/3a884b7af6414e469e6d0b06f32a6070/work/audio.wav --srt data/user-preview/jobs/3a884b7af6414e469e6d0b06f32a6070/output/translated.srt --out data/rule-based-audio-analysis/818-nvidia-new --export-onset 0.3
```
