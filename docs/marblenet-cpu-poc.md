# NVIDIA MarbleNet CPU POC

2026-09-12. Official NVIDIA NeMo model and inference implementation. No training,
transcription, translation, paid API calls or production changes. All inference
runs locally on CPU, and no user audio is uploaded.

## Model and Environment

- Model: vad_multilingual_marblenet, NVIDIA NGC version 1.10.0.
- Official download: https://api.ngc.nvidia.com/v2/models/nvidia/nemo/vad_multilingual_marblenet/versions/1.10.0/files/vad_multilingual_marblenet.nemo
- 91,378 parameters, checkpoint archive 501,760 bytes. SHA-256 in local report.
- NeMo Toolkit 3.0.0, PyTorch 2.14.0+cpu, Python 3.12.10.
- Separate `.tools/nemo-poc` environment. No production dependency changes.
- CUDA-capable PyTorch is not installed: torch.version.cuda is null, model
  parameters are explicitly verified to be on CPU. NeMo's package dependencies
  include Python cuda-bindings/cuda-pathfinder; those are not a CUDA Toolkit or
  GPU inference runtime installation. No driver updates were made.
- NeMo restore needs temporary-directory extraction permissions on this host;
  execution required elevated sandbox permission. No inference telemetry exporters
  are configured. Hugging Face offline mode and disabled W&B mode are set.

The user-requested removal of the old model is complete: `.tools/vad-poc`, including
Silero ONNX weights and its dedicated environment, was removed after checking the
resolved directory. Earlier private audio, subtitle outputs, probabilities and
comparison reports remain preserved. Historical experiment source remains in Git;
it cannot run without reinstalling its removed model/runtime.

## Inference Method

The official EncDecClassificationModel restores the original NVIDIA checkpoint,
uses its mel frontend and network, and produces background/speech logits. Speech
probability is softmax index 1, verified from checkpoint labels. No ASR method is
called. Eval mode, one CPU thread, batch size 64, no audio gain, stereo arithmetic
mean to mono, and inference dither disabled for deterministic input features.

Window 0.63 seconds and hop 0.08 seconds match NVIDIA's published fast VAD
configuration. Centered zero padding matches its official VAD collate function;
a synthetic test compares all windows against that function exactly. The final
partial hop is included and clipped to the actual audio duration.

Our project-specific postprocessing preserves short speech: only continuous
non-speech >=3 seconds is removed, with 0.3 seconds boundary padding and merging
retained gaps <=0.4 seconds. Tested onset/offset pairs are 0.3/0.15, 0.5/0.3,
0.7/0.5. No subtitle-guided threshold tuning. This differs from NVIDIA's default
postprocessing and is intended for comparison with prior retention experiments.
Time decisions have 80 ms grid resolution, not sample-accurate speech boundaries.

## Results

1001.mp4 source audio: 4752.405 seconds. Reference SRT: 426 cues, union 1145.76 s.

| Variant | Retained seconds | <=60s segments | Lost subtitle time | Fully lost cues |
| --- | ---: | ---: | ---: | ---: |
| Existing saved filter | 4528.87 | 80 | 0 s | 0 |
| Prior Silero onset 0.5 | 34.00 | 11 | 1115.36 s (97.35%) | 416 |
| NVIDIA onset 0.3 | 2177.81 | 278 | 367.095 s (32.04%) | 77 |
| NVIDIA onset 0.5 | 1311.88 | 292 | 579.041 s (50.54%) | 154 |
| NVIDIA onset 0.7 | 804.56 | 259 | 762.194 s (66.52%) | 218 |

NVIDIA is substantially better than the prior Silero run on this reference, but
all tested settings still discard too much subtitle time for production use.
The similar total duration of retained audio and subtitle intervals at onset 0.5
does not imply correct detection: much of the retained audio is elsewhere.
Fragmentation increases hypothetical request count. No requests were submitted.
All variants would trigger the earlier >50% source-removal guard; raw POC results
are reported rather than obscured by a fallback. Production filtering is unchanged.

## Speed and Memory

- Full-file inference plus WAV streaming: 38.912 seconds, about 122x real time.
- Model restore: 0.079 seconds.
- Script body including hashes, analysis and 292 segment exports: 40.464 seconds.
- Baseline process RSS after imports: approximately 596.5 MiB.
- Process peak working set through inference: approximately 642.6 MiB.

This is one local run. Timing excludes Python startup/imports, installation,
download and the earlier MP4 decode. The framework's RAM footprint is much larger
than the model weights. No GPU allocation was requested; VRAM was not sampled.

## Validation and Limitations

First 180 seconds retained only 0.6 seconds and lost all four reference cues.
Because this was unexpectedly poor, the input path and positive control were
checked before extending the diagnostic run to the full video.

- A 70-90 second excerpt of the reused stereo analysis WAV, averaged to mono,
  correlates 0.999999918 with the preserved mono WAV used by the original workflow.
  This verifies that excerpt, not the entire transcript's temporal correctness.
- Public whisper.cpp JFK WAV: mean speech probability 0.7667, maximum 0.9982,
  78.99% of windows above 0.5. All 11 seconds retained after postprocessing.
  Its run uses no matching transcript, so subtitle metrics in that control report
  are empty and must not be interpreted as accuracy evidence.
- Synthetic input-window test matches NVIDIA's official collate function exactly.
- Source WAV, SRT and checkpoint hashes match before/after every run.
- Exported segment frame counts and count match the manifest (292 at onset 0.5).

These checks support a working inference path, but do not establish the cause
of missed dialogue. Existing subtitle intervals are not independently annotated
speech truth; no manual listening or new transcription was performed. Avoid
claiming either that the subtitles are wrong or that this model fails generally.

## Artifacts and Reproduction

Ignored output directories under data/rule-based-audio-analysis:
- 1001-nvidia-preview: first 180-second diagnostic.
- nvidia-public-control: public 11-second positive control.
- 1001-nvidia-full: full results, probabilities.npy, intervals.json, report.json,
  segments.json and 292 original-audio WAV segments with original-time mappings.

```powershell
.tools/nemo-poc/Scripts/python.exe scripts/poc-marblenet-cpu.py --model .tools/nemo-poc/vad_multilingual_marblenet.nemo --audio data/rule-based-audio-analysis/1001-v2/analysis-stereo.wav --srt data/user-preview/jobs/aa9c3aec0d5f4372b175aef4a8ca1472/output/translated.srt --out data/rule-based-audio-analysis/1001-nvidia-new
.tools/nemo-poc/Scripts/python.exe scripts/test-marblenet-poc.py
```

Use a fresh output directory. Optional --limit-seconds limits diagnostic duration.

Official references:
- https://catalog.ngc.nvidia.com/orgs/nvidia/nemo/models/vad_multilingual_marblenet
- https://github.com/NVIDIA-NeMo/Speech/blob/main/examples/asr/conf/vad/vad_inference_postprocessing.yaml
- https://github.com/NVIDIA-NeMo/Speech/blob/main/nemo/collections/asr/data/audio_to_label.py
