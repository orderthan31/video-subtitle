# MVP 2 deployment checkpoint

## Verified before replacement

- 2026-09-12: all 311 backend tests passed, including actual synthetic FFmpeg workflows with a local fake LLM provider.
- Frontend: 16 unit tests, production build and both desktop/mobile browser suites passed.
- API, web and GPU VAD worker images built successfully. The worker retained the cached NeMo/CUDA installation.
- An isolated container with no network or user-data mount passed the GPU runtime check with `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`: FFmpeg/FFprobe, CJK fonts, subtitles, HEVC NVENC, AAC and decode.
- `docker-compose.preview.yml` forces `PAID_LLM_ENABLED=false`; the preview launcher always applies this overlay last.

## Backup and migration

Backup: `data/migration-backups/pre-mvp2-20260912-135258`.
All 593 pre-migration files (14,454,144,118 bytes) matched the source backup by SHA-256.

Migration applied successfully to the three completed user jobs:

| Video | Job | Asset |
| --- | --- | --- |
| 818.mp4 | 3a884b7af6414e469e6d0b06f32a6070 | 611bf6420813524ba64c33773a2ab3b9 |
| 1091.mp4 | 5a028ef701904b30be04cd3a408d5220 | 0a3e98df39ea5243b59ca384e3b590f5 |
| 1001.mp4 | aa9c3aec0d5f4372b175aef4a8ca1472 | e003ba72ebb051128a7dfbd9dca214cf |

After migration, all 590 non-job-manifest files still matched the backup. Each copied asset source matched its retained original by SHA-256. All three jobs remained COMPLETED. Only asset-link metadata and updated timestamps were intentionally added to the job manifests.

## Approved replacement

The execution safety reviewer initially rejected replacement without explicit approval and a rollback plan. Work paused at that gate. The user subsequently explicitly approved the restart, and API/web/GPU worker were successfully replaced on 2026-09-12.

Approved replacement should keep host storage `data/user-preview/jobs`, web port 5177, the existing API port and Tailscale origin. Apply base, VAD, GPU, host-data, VAD-GPU and preview overlays, with the preview overlay last. Wait for health checks and inspect only the paid flag, GPU configuration and mounts, never dump credentials.

## Rollback plan

Old container image IDs, retained at the time of the gate:

- API: `sha256:37b05ad89338d3f9c950b94e0a3c0a12f87671ebe87b78555796cb993414aaed`
- Web: `sha256:cf217229f0abde8332f4b600fc5f6a9e6e9bf580036ba629f45adb7b9533b5f5`
- Worker: `sha256:59e3392d995212026831ac23af067e62a97d08517460429dbd68a5cef83a34ca`

On a failed approved rollout, stop the new worker, restore API/web image references to the old IDs and recreate only those services with the same mounts/ports. Keep the old worker stopped: it does not contain the paid-call guard. Do not remove new assets or overwrite storage with the backup. Retained original job media makes the original MVP1 jobs readable; new MVP2 workflows require the MVP2 API/worker and must not be executed by the old worker.

Inspect the storage and job state before any rollback, particularly if new jobs have been created since rollout. The backup is recovery evidence, not permission to overwrite newer user work.

## Completed release checks

- API/web healthy, worker running; original storage mount and ports retained.
- Worker environment inspected without exposing secrets: `PAID_LLM_ENABLED=false`, `NVIDIA_VAD_DEVICE=cuda`, NVIDIA GPU reservation and `compute,video,utility` driver capabilities.
- Tailscale HTTPS homepage returned 200. All four currently registered assets (three migrated originals plus a promoted 1091 result already present during verification) returned 206 for bytes 0-31. All three legacy translated SRT downloads returned 200 (11,872 / 36,002 / 28,542 bytes).
- Real deployed library rendered at desktop 1440px and mobile 390px with no page errors or horizontal overflow. Verification was read-only and did not create jobs or invoke an LLM.
- Final requirements audit is recorded in `mvp2-tasks.md`; master remains unchanged unless separately approved. Paid LLM calls remain disabled, so actual transcription/translation requests cannot run until explicitly re-enabled.
