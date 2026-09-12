# MVP 2: Video-first workspace

Status: implemented and deployed with paid LLM calls disabled (2026-09-12). MVP 1 checkpoint: 1202f51.
Development branch: feat/mvp2-video-workspace. Do not merge into master without approval.

## Product contract

- Every workflow belongs to one registered video asset.
- Upload accepts video only and never automatically transcribes or translates.
- An asset may have many independent jobs, each with immutable inputs and settings.
- Templates: extract audio, transcribe, transcribe + translate, translate only,
  encode only (with or without subtitles), and full workflow.
- External SRT can only be attached to a selected video. No standalone SRT library.
- Retry resumes the same job; changed settings create a new job.
- Subtitle edits are revisions. Job creation freezes the chosen revision.
- Re-encoding normally uses the original video, not the previous encoded output.
- Processing an output video requires registering it as a new video asset. Preserve
  provenance, do not inherit transcription/translation completion automatically.
- Assets, jobs, artifacts and their physical files have distinct ownership.
- No automatic deletion is added. Referenced sources cannot be removed underneath
  jobs. Promoted assets must survive deletion of their parent job.

## UI

- Video library: upload queue, video names, search, upload/ready states.
- Asset detail: source playback, workflow creation, attachments, outputs and history.
- Workflow form: template, input artifact/revision, settings, explicit stage list.
- Job detail: existing progress, video/text double panel, final subtitle editing.
- All jobs: cross-asset execution queue and history.
- Keep the current neutral/blue palette and collapsible sidebar; stack on mobile.

## System design

Keep FastAPI, the filesystem repository and one media worker. Introduce a separate
asset manifest namespace without rewriting completed legacy artifacts. Migrate
legacy jobs idempotently to linked video assets. Reuse existing per-job checkpoints
and three-request Gemini queues; do not introduce a second execution framework.
Use explicit job template metadata, immutable copied artifact inputs and asset
source references. Validate ownership, file types, timelines, quotas and references
on the server. Versioned output and attachment records remain under their asset/job.

## Cost safety

Real Gemini network calls default to disabled unless PAID_LLM_ENABLED=true. Tests
must use injected fake providers and synthetic media in isolated storage, never
the real preview job directory. The deployed preview worker is guarded by the
paid-disabled Compose overlay. No paid calls are used as acceptance tests. Do not enable paid
mode or silently fall back to a real provider to finish tests.

## Acceptance

Upload once, execute independent transcribe/translate/encode jobs, attach an SRT
only under a video, reuse selected artifacts, promote an encoded result and keep
both histories. Restart/retry must preserve identity and checkpoints. Deletions
must not break independent promoted assets. Existing MVP 1 data stays readable.
Validate API security, migration idempotency, workflow stage selection, actual
FFmpeg extraction/encoding with synthetic clips, UI desktop/mobile and cost lock.

## Deferred

Cross-asset arbitrary artifact sharing, multi-video concatenation, standalone
subtitle workflows, cloud object storage migration and additional concurrent jobs.
