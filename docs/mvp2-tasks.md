# MVP 2 task tracker

- [x] Record approved product/system/UI scope and acceptance criteria.
- [x] Stop the current paid worker before development tests.
- [x] Add fail-closed paid LLM guard and tests; persist Docker preview lock.
- [x] Add isolated asset storage, idempotent source registration and result promotion primitives/API.
- [ ] Separate video asset registry and upload-only lifecycle.
- [ ] Implement idempotent legacy migration and source/reference protection.
- [ ] Implement asset-scoped SRT attachments and artifact input snapshots.
- [ ] Implement independent workflow templates and checkpoint-safe execution.
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
