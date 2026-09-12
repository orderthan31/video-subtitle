# MVP 2 task tracker

- [x] Record approved product/system/UI scope and acceptance criteria.
- [x] Stop the current paid worker before development tests.
- [x] Add fail-closed paid LLM guard and tests; persist Docker preview lock.
- [x] Add isolated asset storage, idempotent source registration and result promotion primitives/API.
- [x] Separate video asset registry and upload-only backend lifecycle.
- [ ] Connect upload-only frontend and validate video metadata on registration.
- [x] Implement idempotent legacy migration and source/reference protection.
- [x] Validate real video registration and lease-protected source range streaming.
- [ ] Implement asset-scoped SRT attachments and artifact input snapshots.
- [x] Add SRT attachment/version import APIs, immutable artifact storage and snapshot primitives.
- [x] Define and test exact workflow stages and input compatibility.
- [ ] Implement independent workflow templates and checkpoint-safe execution.
- [x] Connect all six templates to job creation, fixed subtitle inputs and stage-selective worker execution.
- [x] Complete extracted-audio reuse and partial-workflow subtitle editor compatibility.
- [ ] Implement result-video promotion with provenance and independent storage.
- [ ] Implement video library, asset detail and workflow creation UI.
- [ ] Integrate existing job detail, editor and job queue with assets.
- [ ] Validate synthetic media E2E, API permissions, failure/retry and migration.
- [ ] Validate desktop/mobile UX; document operational and compatibility limits.
- [ ] Deploy with paid calls disabled, verify existing data and commit/push.

Real tests: no Gemini requests. Mark tasks only after verification, not on start.

## Foundation checkpoint

- Asset manifests and copied sources live in `.assets/<asset_id>/`, outside job enumeration.
- Registration preserves legacy job files/status and links the job to the new asset. It is explicit, not an automatic startup migration yet.
- Copy admission is checked under the existing global capacity lock. The manifest is published only after the copy completes. Retrying a failed job-link write reuses the published asset.
- Result promotion copies the completed MP4, records parent asset/job provenance, and does not inherit transcript/translation state.
- Asset deletion rejects referencing jobs. Parent job deletion does not remove asset-owned video bytes; derived assets remain independent.
- `run-docker-preview.ps1` forces paid calls off regardless of `.env`. The existing live worker remains stopped until guarded deployment.
- Verified: 11 storage tests, 4 API tests, 2 paid-call guard tests, 7 download regressions, 5 final-subtitle regressions.
- Pending: upload-only lifecycle, migration orchestration, shared locking for new workflow references and streaming, artifact snapshots, workflow execution/UI, and guarded deployment. No real user assets migrated in this checkpoint.

## Upload-only backend checkpoint

- `POST /api/video-uploads` accepts only filename, size and an idempotency request ID. Workflow settings are rejected.
- Resumable chunks/status/hash verification/completion use `/api/video-uploads/<upload_id>`. Sessions are stored in `.uploads/`, never in worker/job enumeration.
- Completion registers an asset without queueing any processing. Failed copy/admission can be retried without uploading the bytes again.
- Capacity admission includes pending upload reservations. Chunk writes continue to use the inexpensive free-space check, not directory scans.
- User-requested deletion removes incomplete uploads. Registered sessions cannot bypass asset deletion; deleting an unreferenced asset also removes its staging upload.
- Verified this checkpoint: 6 new upload-only tests, 11 asset tests, 10 capacity tests, 21 existing upload tests. Tests use isolated synthetic bytes and no paid calls.
- Frontend, metadata probing, migration and workflow integration remain pending; no live deployment or real data changes yet.

## Subtitle inputs and workflow policy checkpoint

- Added the pinned `srt==3.5.3` parser dependency instead of a custom SRT parser.
- `/api/videos/<asset_id>/subtitles` attaches/lists external SRT only under an existing owned video. Original SRT text, exact timestamps, language and a cue digest are retained.
- `/api/videos/<asset_id>/subtitle-inputs` imports a selected completed job track/revision from the same video. Stale revision selections return 409. The immutable artifact survives edits/deletion of the producing job.
- Workflow-local snapshot support copies the selected artifact content and records its digest; job creation integration is still pending.
- `/api/videos/<asset_id>/workflow-plan` validates selected inputs and previews explicit stages/reused inputs/paid stages. Six templates are defined: audio extraction, transcription, transcription+translation, translation, encoding and full workflow.
- Policy tests prove that translation requires subtitle input, subtitle-free encoding has no paid stages, and selected subtitle/audio reuse skips the corresponding generation stages.
- Verified: 3 workflow policy tests, 5 subtitle artifact tests, 6 asset API tests, 6 upload-only API tests.
- These are planning/input APIs, not a completed workflow executor. Worker branching, audio artifact reuse, job snapshots and UI remain required before release. No production deployment or paid requests.

## Workflow execution checkpoint

- `POST /api/videos/<asset_id>/jobs` creates an independent queued job with fixed options/source asset and copied subtitle input. Request IDs are idempotent; changed requests conflict. A runnable manifest is published only after its inputs exist.
- Asset references are created under the same registry lock as deletion. Jobs read the independent asset source instead of duplicating the original video for each run.
- Worker executes audio-only, transcription-only, transcription+translation, translation-only, encoding-only and full workflows. Existing legacy full jobs preserve their compatibility path.
- Translation-only skips audio/transcription. Encoding-only skips all LLM stages and can encode without subtitles; video-only media is supported. Audio-only publishes downloadable `audio.wav`.
- New non-encoding jobs do not initialize an encoder. Selected subtitle input is digest-checked and retains its original timing.
- Verified API-to-worker tests cover eight template/input combinations, result downloads, request idempotency, source deletion protection, subtitle snapshot independence, video-only encoding and translation-failure retry without repeating transcription.
- Entire backend test suite: **299 tests passed**, with `PAID_LLM_ENABLED=false`. Workflow media/provider operations are mocked here; actual synthetic FFmpeg E2E remains a release gate.
- Still pending: audio reuse, video registration validation/migration, editor compatibility for partial tracks, full frontend, synthetic media/browser E2E and paid-disabled deployment. Existing user files and live services were not changed.

## Audio reuse and partial subtitle editing checkpoint

- Workflow creation and preview accept a same-video completed `audio_job_id`. The original extracted WAV is copied into the new job input while the producing job is locked; its digest is verified before processing.
- Reused audio skips extraction, but runs the selected preprocessing/transcription stages. Deleting the producing job does not affect the snapshot. Audio from other videos/owners or expired outputs is rejected.
- `/api/videos/<asset_id>/audio-inputs` lists available completed extraction sources without exposing storage paths.
- Completed subtitle editing derives tracks from actual published results. Transcription-only jobs expose/edit/import the original track without requiring a nonexistent translation. The frontend selects an available track when the default is absent.
- Imported SRT timing, multiline text and overlapping intervals remain editable and preserved in SRT. The optional SMI output normalizes overlapping cues using the existing subtitle-layout function because SAMI requires a non-overlapping event timeline.
- Verified: **303 backend tests passed** with paid calls disabled; TypeScript/Vite production build passed. Added coverage for audio reuse after producer deletion, tamper detection, transcription-only edit/import, and multiline/overlapping external SRT edit round-trip.
- Remaining release work: video probing/migration, source streaming and library/workflow frontend, real synthetic media/browser verification, and guarded deployment. No user media or running services changed.

## Video registration and migration checkpoint

- Registration/promotion now invoke FFprobe before publishing an asset. Invalid media, audio-only files and attached cover art are not accepted as a source video. Basic duration/dimensions/codec/audio metadata is stored without audio extraction or model calls.
- `/api/videos/<asset_id>/stream` supports byte ranges and holds an asset reader lease through the response. Deletion is rejected while a reader is active.
- `scripts/migrate-video-assets.py` defaults to reporting candidates. `--apply` copies complete legacy originals and writes asset links without changing job status or removing original/result files. Repeated runs reuse links; incomplete/active jobs are skipped and corrupt manifests are reported.
- The migration script is included in Docker images. Operational instructions are in `mvp2-operations.md`.
- Verified: **309 backend tests passed** with paid calls disabled, including actual FFmpeg-generated MP4 upload, FFprobe metadata validation and HTTP range streaming. Migration repeatability/preservation, invalid-video rejection and reader/deletion conflicts are covered.
- Actual user-data migration and Docker deployment have not run yet. Frontend/library/workflow UI and full media/browser release verification remain pending.
