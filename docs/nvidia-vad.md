# Packed Transcription and Optional NVIDIA VAD

## Processing Contract

New transcription runs concatenate all retained PCM audio, then send consecutive
maximum-60-second requests. Short retained regions share a request. Only the last
request may be shorter. Concurrency remains three, with three total attempts per
request, immediate refill, stop-admission/drain on terminal failure, and cached
successful requests on retry. Translation scheduling is unchanged.

`timeline-map.json` preserves sample-aligned original positions. Returned cues are
projected onto every intersected retained interval; they never stretch across a
removed gap. A sentence crossing a join repeats its complete text on each piece,
because sentence timestamps cannot establish word ownership. These cases are
saved in `cross-boundary.json` (also partial-result variants). This is not a claim
of perfect linguistic alignment. The accepted 818 POC had zero such sentences.

Packed transcription and downstream translation have new checkpoint keys. Old
completed jobs and results are untouched. Retrying an older interrupted job can
recompute transcription/translation once under the new scheme; extraction and
preprocessing checkpoints with default VAD off remain compatible. New-scheme
retries reuse their successful request caches normally.

## Upload Option

`POST /api/uploads` accepts `vad_mode: "off" | "nvidia"`, default `off`.
The option is persisted, returned in job responses, included in upload idempotency
checks, and restored for upload resume. Existing jobs without this field use off.
The upload queue snapshots each file's option independently. Upload still does
not start media processing; the user starts it from the job list.

NVIDIA mode runs the official multilingual MarbleNet model locally against
the original extracted 16 kHz PCM16 audio. Onset 0.3, offset 0.15, window 0.63 s,
hop 0.08 s, batch 64, one CPU thread, no gain, mean-to-mono, dither disabled.
Only non-speech runs >=3 s are removed, padding 0.3 s, merge gap 0.4 s. Short
speech is not discarded. These settings are fixed for this version.

If a silence filter is also selected, its retained regions are intersected with
VAD retained regions in the original clock, then audio is copied once. Selecting
audio filter off disables only silence/transcript filtering, not selected VAD.
VAD can discard actual dialogue: on 818, the earlier SRT reference after minute
20 lost 23.71% of subtitle time. Leave it off when preserving dialogue matters.
It is not a lyrics/music classifier and does not guarantee music removal.

CPU is the default. `NVIDIA_VAD_DEVICE=cpu|cuda|auto` selects execution:
`cuda` fails explicitly if unavailable, while `auto` falls back to CPU only when
CUDA is unavailable at startup. CUDA execution/OOM errors do not silently retry
on CPU. `NVIDIA_VAD_BATCH_SIZE` accepts 1-256 (default 64); 16 is a smaller working
batch, not a guaranteed VRAM cap. CUDA context/framework memory can dominate this
small model. GPU results near thresholds can differ from CPU due to numerics.
Original source and preprocessing checkpoints are kept on retry, even if device
settings change. Previously completed preprocessing is not recomputed merely to
move it to another device; use a new job to compare execution settings.

No Gemini call is used for VAD. The subprocess is cancellation-aware and bounded
by the existing media-process timeout/log limit. Missing dependencies/model cause
an explicit preprocessing failure, not a silent fallback. Raw VAD probabilities,
model hash, settings and timings remain in `work/nvidia-vad.json`; process output
remains in `work/nvidia-vad.log`. Source/audio/subtitle retention is unchanged.

## Optional Runtime

Normal uploads with VAD off need no PyTorch or NeMo. For NVIDIA mode, configure
the worker environment (absolute paths):

```text
NVIDIA_VAD_PYTHON=/path/to/nemo-environment/bin/python
NVIDIA_VAD_MODEL=/path/to/vad_multilingual_marblenet.nemo
```

The development host reuses its existing `.tools/nemo-poc/Scripts/python.exe` and
`.tools/nemo-poc/vad_multilingual_marblenet.nemo` automatically when no override
is provided. Other hosts should create a separate Python 3.12 environment:

```sh
python -m venv .venv-nvidia
.venv-nvidia/bin/python -m pip install torch==2.14.0+cpu torchaudio==2.11.0+cpu --index-url https://download.pytorch.org/whl/cpu
.venv-nvidia/bin/python -m pip install 'nemo_toolkit[asr]==3.0.0'
```

On Windows use `.venv-nvidia/Scripts/python.exe`. These versions match the local
validated environment; installation on other platforms is not yet validated.
Use the NVIDIA NGC `vad_multilingual_marblenet` version 1.10.0 model from the
[POC model reference](marblenet-cpu-poc.md). Download the actual model archive,
not the NGC JSON response: that endpoint can return its download in a Location
header. The tested archive is 501,760 bytes. Do not load untrusted checkpoints.

Default Docker builds stay lightweight and do not include NeMo. The optional VAD
image includes NeMo and the official model, downloaded at build time and checked
against the SHA-256 of the tested archive. No host model download/mount is needed.
For CPU VAD:

```sh
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.vad.yml up --build -d
```

For NVIDIA CUDA VAD (independent of video encoding):

```sh
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.vad.yml -f docker-compose.vad-gpu.yml up --build -d
```

Set `NVIDIA_VAD_DEVICE=cuda` in `.env.docker` if it previously specified `cpu`.
GPU-enabled images can also run with `cpu` or `auto`; CPU-only images cannot gain
CUDA support just by changing a runtime variable. GPU wheel build arguments are
`VAD_TORCH_VERSION`, `VAD_TORCHAUDIO_VERSION`, `VAD_TORCH_INDEX_URL`. Defaults use
the officially published PyTorch 2.11.0 / TorchAudio 2.11.0 CUDA 12.8 combination.
The local CPU environment remains 2.14.0+cpu / 2.11.0+cpu, untouched. Example
runtime/build settings are in `.env.vad.example`. Changing build arguments
requires rebuilding; changing runtime environment requires recreating the worker.

No same GPU model or Windows version is required, but CUDA requires a compatible
NVIDIA GPU/driver and CUDA-enabled container PyTorch. The initial Docker target
is Linux x86-64 containers: Linux hosts need the NVIDIA driver and Container
Toolkit; Windows hosts need supported NVIDIA drivers and Docker Desktop's WSL2
backend. macOS Docker Desktop has no supported NVIDIA CUDA path here. ARM/Jetson,
AMD ROCm, Apple MPS and all older GPU architectures are not validated or promised.
CPU mode does not require an NVIDIA GPU. Do not install host CUDA just for these
prebuilt PyTorch containers; the host GPU driver/runtime integration is required.

References: [Docker GPU support](https://docs.docker.com/desktop/features/gpu/),
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html),
[PyTorch wheel combinations](https://pytorch.org/get-started/previous-versions/),
[TorchAudio compatibility](https://docs.pytorch.org/audio/main/installation.html).

The CPU VAD Docker image has now been built and validated on the development
host after its Windows restart. Full 818 CPU inference retained the same 266
intervals and 2,091.7573125 seconds as native Windows CPU. Production now uses
that container with NVIDIA video encoding (also smoke-tested). A subsequent
CUDA VAD image/full-audio test passed on RTX 5070 Ti, and the worker now uses CUDA
for VAD-selected jobs. See [GPU timing and output differences](818-gpu-vad-benchmark.md).
The original CPU environment remains installed for rollback. NeMo
adds substantial installation size and RAM use despite small weights.

## Verification

- 243 Python tests passed after device-selection support; the preceding frontend
  change passed 16 web tests and its production build (frontend unchanged here).
- Production VAD subprocess against unchanged 818 audio: 266 intervals,
  2,091.7573125 retained seconds, matching the original 0.3/0.15 POC.
- Artifacts: ignored `data/production-validation/818-vad`.
- No paid transcription/translation was run for this implementation validation.
- Updated CPU inference produced exactly identical 818 probabilities and intervals.
- Docker's pinned model downloader was exercised successfully outside Docker.
- CPU Docker build/inference and NVENC encoding are verified. Device selection,
  failure/fallback/batch bounds are unit-tested; RTX 5070 Ti CUDA VAD is now verified.
- Browser UI inspection unavailable in this session; no connected browser.
