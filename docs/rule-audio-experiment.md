# Offline rule-based audio experiment

Date: 2026-09-12. Source: retained 1001.mp4.

This is an isolated experiment, not a production filter change. No transcription,
translation, AI models, API calls, or service restarts were performed.
Media, subtitle text, hashes, and detailed timestamps stay in ignored local data.

## Method

- Decode original audio as 16 kHz stereo PCM. Analyze channels independently;
  exclusion requires both channels to qualify. Do not normalize loudness first.
- Measure RMS and FFT energy in 20 ms frames. The broad 150-4500 Hz band is a
  heuristic, not a speech classifier.
- Estimate a local noise floor using the 10th percentile in each 30-second block,
  clamped to -75 through -40 dBFS with at most 3 dB change between blocks.
- Quiet onset requires both <= -45 dBFS and <= noise floor + 6 dB.
  Quiet continuation uses -42 dBFS and floor + 9 dB hysteresis.
- Out-of-band noise onset requires band energy <= -50 dBFS, band/total energy
  <= 10%, and total energy <= -30 dBFS. Continuation thresholds are -47 dBFS,
  15%, and -27 dBFS. This cannot establish that sound contains no speech.
- Each exclusion type must persist for at least 3 seconds. Keep 300 ms padding
  at both ends of excluded runs. Merge retained intervals separated by <= 400 ms.
- Fall back to the original saved filter if removal exceeds 50%. This guard did
  not trigger. Noise-floor slew is limited; a separate instability fallback is
  not implemented in this initial experiment.
- Split each retained original-time interval independently into <= 60-second
  WAV files, matching production's span boundary policy. Do not join distant
  original intervals into one segment.
- Freeze thresholds before comparing to subtitles. Use the union of existing
  translated SRT cue intervals as the reference. No subtitle-based tuning.

## Results

Decoded audio: 4752.4053125 seconds (79:12.405), two channels.
Existing translated subtitles: 426 cues, union duration 1145.76 seconds.

| Variant | Retained seconds | Intervals | <=60s segments | Subtitle overlap lost | Affected cues |
| --- | ---: | ---: | ---: | ---: | ---: |
| Existing saved 5s filter | 4528.869625 | 6 | 80 | 0 s | 0 |
| Fixed RMS 3s control | 4524.32 | 6 | 80 | 0.028 s | 1 |
| New rules, before/after guard | 4521.34 | 8 | 81 | 0 s | 0 |

The 3s control uses frame RMS on stereo, not production FFmpeg silencedetect on
mono; it is not an exact reproduction of the current production 3s option.
Its affected cue is #1, with 28 ms clipped from the start, not an entirely lost cue.

New rules remove 231.0653125 seconds (4.8621% of source audio). Relative to the
existing filter they remove only 7.529625 additional seconds, a 0.1663% reduction
in previously retained audio. Request count increases by one due to fragmentation.
All 426 reference cues remain fully covered, with zero completely lost cues.

Seven quiet exclusions account for all removed time. The five out-of-band
exclusions (217.5053125 seconds) overlap those exclusions and add no unique removal.
Thus this experiment does not demonstrate useful additional music/noise rejection.
It provides no evidence for substantial cost or speed savings.

## Interpretation

Zero timeline loss is not proof of zero actual speech loss: existing Gemini
subtitles may omit speech, and subtitle time ranges may include non-speech.
No new recognition or manual listening evaluation was performed. Quiet dialogue
and dialogue mixed with music remain risks if thresholds are made more aggressive.
Rules alone do not establish whether a sound is music, dialogue, or an interjection.

These conservative parameters should not be promoted as a major optimization.
Production filtering remains unchanged.

## Reproduction and Artifacts

Requires Python with NumPy and FFmpeg. No model packages are required.
Use a new output directory; existing results are never overwritten.

```powershell
python scripts/analyze-rule-audio.py --job data/user-preview/jobs/aa9c3aec0d5f4372b175aef4a8ca1472 --out data/rule-based-audio-analysis/1001-new --ffmpeg .tools/ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe
python scripts/test-rule-audio.py
```

Successful local output: `data/rule-based-audio-analysis/1001-v2/`.
The v1 directory is an incomplete attempt retained after a JSON serialization error.

- `report.json`: parameters, metrics, affected cue numbers/times, SHA-256 hashes.
- `intervals.json`: kept intervals for all variants.
- `removed-reasons.json`: quiet and out-of-band exclusion intervals.
- `timeline-map.json`: processed-to-original timestamp mapping.
- `segments.json` and `segments/0001.wav` through `0081.wav`: actual new segments.
- `analysis-stereo.wav`: independently decoded analysis audio.
- `frame-features.npz`: measured per-channel values and estimated noise floors.

Verification: five synthetic interval tests pass; all 81 WAV durations match the
manifest (maximum floating-point discrepancy < 1e-12 seconds), total 4521.34 seconds,
maximum 60 seconds each. SHA-256 hashes of original MP4, original extracted WAV,
reference translated SRT, and original timeline map match before and after the run.
