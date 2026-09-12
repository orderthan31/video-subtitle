# 818 GPU VAD Benchmark

2026-09-12. Latest completed job was 818.mp4 (job
`3a884b7af6414e469e6d0b06f32a6070`). Only VAD was run, once on the full saved
audio: 4,756.7573125 seconds (79m16.76s). Source mounted read-only. No CPU rerun,
transcription, translation, encoding, or paid Gemini requests.

## Environment and Results

- Docker Desktop / WSL2 Linux container, RTX 5070 Ti, driver 591.86.
- PyTorch 2.11.0+cu128, TorchAudio 2.11.0+cu128, NeMo 3.0.0.
- Same MarbleNet checkpoint and 0.3/0.15 thresholds; batch 64, hop 80ms,
  window 630ms, 3s minimum non-speech, 0.3s padding, 0.4s merge gap.
- Model loading/device setup: **0.256 s**.
- WAV streaming, window preparation, CUDA inference and result transfer: **7.309 s**.
- Processing body including model loading/postprocessing: **7.574 s**.
- Host wall clock including Docker startup, Python imports, VAD, report write
  and container removal: **16.170 s**. Image building/downloading excluded.
- PyTorch peak allocated: **42.45 MiB**; peak reserved: **66 MiB**.
- Whole GPU memory sampled every 200ms: initial **677 MiB**, peak **1,026 MiB**,
  observed increase **349 MiB**. This includes CUDA context and other apps; it is
  not an isolated process measurement or a universal memory bound.

Historical CPU processing-body time was 32.855 s, giving a reference speedup of
4.34x. CPU was not rerun, as requested. That run used PyTorch 2.14.0+cpu, so this
is not a controlled same-version CPU/GPU comparison. Single cold-process GPU run,
not a warmed-up repeated timing or a performance guarantee.

## Retained Audio

GPU kept 267 intervals / 2,087.1573125 s. Historical CPU kept 266 intervals /
2,091.7573125 s. GPU intervals are a subset: 4.60 s less retained audio, no added
audio. Do not interpret this as confirmed dialogue loss: no manual listening or
subtitle-quality assessment was performed. Backend/version/numerical differences
are possible causes; no cause was isolated and no threshold was adjusted.

## Saved Evidence and Runtime

Private artifacts, excluded from Git:

- `data/production-validation/818-vad/docker-gpu.json`: probabilities, intervals,
  model hash, device, versions, processing times and allocator memory.
- `docker-gpu-wall.json` in the same directory: host wall time / successful exit.
- `docker-gpu-memory.csv` in the same directory: GPU-wide memory samples.
- `data/production-validation/vad-gpu-build.log`: successful CUDA image build.

After success, only the idle worker was recreated with `NVIDIA_VAD_DEVICE=cuda`.
Web/API remained running. Upload VAD selection still defaults off; selected VAD
jobs now use the GPU. Existing completed jobs/results were not rerun or changed.
The prior CPU worker image remains tagged `video-subtitle-worker:cpu-checkpoint`.
Restart this GPU configuration with:

```powershell
./scripts/run-docker-preview.ps1 start -VadDevice cuda -WebOrigin https://YOUR-HOST.YOUR-TAILNET.ts.net
```

The launcher defaults to CPU unless `-VadDevice cuda` is provided. CUDA-enabled
images can run CPU mode; rebuilding CPU-only images is optional for rollback.
