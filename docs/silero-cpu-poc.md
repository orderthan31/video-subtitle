# Silero CPU POC: 1001.mp4

2026-09-12. Isolated offline experiment. Production filtering is unchanged.
No Gemini calls, transcription, translation, GPU inference or original-file edits.

## Configuration

- Official Silero ONNX model, upstream commit
  `867c2aa692646a1f1de3e94a15c9dd9f614c0acb`.
- Model SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
- Model file: 2,327,524 bytes. ONNX Runtime 1.30.0, NumPy 2.5.3,
  Python 3.12.10, psutil 7.2.2, isolated `.tools/vad-poc` environment.
- CPUExecutionProvider only; intra/inter-op threads both one.
- Original-time 16 kHz stereo PCM, no prior noise filtering. Each channel has
  independent recurrent state; either channel can preserve an interval.
- Official inference protocol: 512 samples plus 64-sample context, state
  shape [2, channels, 128]. No PyTorch or generative model dependencies.
- Predeclared thresholds 0.3/0.5/0.7, release threshold 0.15 lower.
  Preserve short speech detections; remove only >=3 seconds of continuous
  non-speech, with 300 ms padding and 400 ms retained-gap merge.
- This is project-specific conservative postprocessing, not a claim that
  Silero's default get_speech_timestamps was used.

## Speed and Memory

Source audio: 4752.405 seconds, about 79 minutes.

- Model load: 0.031 seconds.
- Inference including WAV streaming: 17.960 seconds (264.6x real time).
- Total measured script body: 19.439 seconds, including input hashes,
  postprocessing and 11 default-threshold segment exports.
- Baseline process RSS after imports: 51.98 MiB.
- Process peak working set through inference: 72.17 MiB.
- Whole experiment peak working set: 78.35 MiB.
- CPU provider only; no GPU allocation requested. VRAM was not externally sampled.

One local run, not a repeated benchmark. Reused previously decoded WAV; original
MP4 decode, installation, download, interpreter startup and imports are excluded.
Peak working set is the process-wide Windows high-water mark, not model weights.

## Accuracy Against Existing SRT

Reference: 426 cues, 1145.76 seconds of union timeline coverage.

| Variant | Retained seconds | Segments | Lost reference seconds | Fully lost cues |
| --- | ---: | ---: | ---: | ---: |
| Existing saved filter | 4528.87 | 80 | 0 | 0 |
| Silero threshold 0.3 | 42.664 | 12 | 1108.140 (96.72%) | 415 |
| Silero threshold 0.5 | 34.000 | 11 | 1115.360 (97.35%) | 416 |
| Silero threshold 0.7 | 22.808 | 10 | 1124.872 (98.18%) | 418 |

This is a failed retention result, not a cost optimization. Do not deploy these
settings. An existing >50% removal safety guard would reject every candidate;
raw results are deliberately retained for diagnosis rather than hidden by fallback.

## Diagnostic Checks

The unexpectedly low detection triggered additional checks:

- On the original 70-90 second excerpt, batch inference matches separate-channel
  inference exactly (maximum probability discrepancy zero on both channels).
- Excerpt RMS is 0.03440/0.02798 and peaks 0.19684/0.20096, ruling out a simple
  all-zero input or integer scaling collapse for this excerpt.
- Maximum speech probability is 0.06245/0.04650 on that excerpt; averaging to
  mono only reaches 0.10270. Mono was checked on this excerpt, not the full video.
- The public whisper.cpp JFK reference WAV (16 kHz mono, 11 seconds) reaches
  0.99996 maximum probability, 0.68853 mean and 67.93% of complete 512-sample
  windows above 0.5. This positive control supports a functioning inference path,
  but is not evidence of accuracy on this video's language/audio conditions.
- Five synthetic postprocessing tests pass, covering channel retention,
  short pauses, partial final frames, short speech and hysteresis.
- Input WAV, SRT and prior interval hashes match before/after. Exported segment
  frame counts match their manifest.

Root cause is not established. Do not infer that the existing subtitles are
hallucinated, that all this audio is non-speech, or that Silero fails generally.
No manual listening or independent transcription was performed. Next investigation
should audit representative original audio/subtitle alignment and compare a second
lightweight detector before changing thresholds or considering GPU acceleration.

## Reproduction

```powershell
.tools/vad-poc/Scripts/python.exe scripts/poc-silero-cpu.py --model .tools/vad-poc/silero_vad.onnx --prior data/rule-based-audio-analysis/1001-v2 --srt data/user-preview/jobs/aa9c3aec0d5f4372b175aef4a8ca1472/output/translated.srt --out data/rule-based-audio-analysis/1001-silero-cpu-new
.tools/vad-poc/Scripts/python.exe scripts/test-silero-poc.py
```

Successful run artifacts: `data/rule-based-audio-analysis/1001-silero-cpu-v1/`.
Includes report.json, raw per-channel probabilities.npy, all candidate intervals,
and 11 actual default-threshold WAV segments with original-time mappings.
Private artifacts and downloaded dependencies are ignored by Git.

References:
- https://github.com/snakers4/silero-vad
- https://github.com/snakers4/silero-vad/blob/867c2aa692646a1f1de3e94a15c9dd9f614c0acb/src/silero_vad/utils_vad.py
- https://github.com/ggerganov/whisper.cpp/blob/master/samples/jfk.wav
