# POC Artifact Cleanup

2026-09-12, explicitly requested by the user after successful GPU VAD validation.
Removed 31 verified workspace paths containing 107,835 files, approximately
7.814 GiB of file contents. Resolved absolute paths were checked against the
workspace and protected directories; no reparse points were present.

Removed:
- Offline rule-based/VAD experiments, segmented audio, probability arrays,
  packed-transcription and parallel-translation comparisons under `data`.
- `data/production-validation`, including CPU/GPU benchmark raw outputs,
  duplicate model download, GPU memory samples and image-build log.
- Historical smoke/validation/fixture directories (audio, upload, browser/auth,
  subtitles, encoding, progress, transcript boundaries) and `data/test-runs`.
- `data/stt-smoke.wav`.
- `.tools/alignment-env`, `.tools/Qwen3-ForcedAligner-0.6B`, `.tools/nemo-poc`.
- Unused `.tools/ffmpeg-9.0.1-essentials_build` and downloaded FFmpeg ZIP archives.

Preserved:
- `data/user-preview`, including all real uploaded videos, audio, subtitles,
  job metadata, LLM traces and outputs.
- `data/video-jobs` and `data/migration-backups`.
- The running Docker containers/images and their embedded NVIDIA VAD model.
- The CPU rollback image and `.tools/ffmpeg-8.0.1-essentials_build`.
- Source code, test/POC scripts and Git-tracked summary reports.
- Runtime configuration and API keys; no application code was changed.

Historical POC reports describe the observations made before cleanup. Their
ignored artifact paths are no longer available; rerunning an experiment requires
regenerating its inputs/environment. Native NeMo fallback on this development
host now requires a fresh explicit runtime installation. Docker GPU VAD does not
depend on those deleted host environments.
