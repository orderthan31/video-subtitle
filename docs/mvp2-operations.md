# MVP 2 operations

## Safety boundary

Development is on `feat/mvp2-video-workspace`; do not merge into master as part of preview deployment.
Keep `PAID_LLM_ENABLED=false`. The preview PowerShell launcher forces this value even if `.env` enables it.
Do not restart an older worker image lacking the guard. Mock providers in tests do not send real requests.

## Legacy source registration

Back up the storage directory before the first migration. Stop the worker and avoid concurrent uploads/edits during the migration window. Preserve the configured storage mount; never switch an existing installation to an empty named volume.

After building the MVP2 API image, report candidates:

```sh
docker compose exec -T api python scripts/migrate-video-assets.py --storage-root /data/video-jobs
```

Inspect the report and available disk space. Applying copies complete original video files into `.assets`; allow approximately the sum of candidate sizes in additional space. Existing job files/results are retained.

```sh
docker compose exec -T api python scripts/migrate-video-assets.py --storage-root /data/video-jobs --apply
```

The report separates registered, already-linked, pending, skipped and failed jobs. Failed entries produce a nonzero exit status. Re-run after resolving missing files, busy jobs or insufficient space. Uploading and active jobs are skipped; migrate them after they stop or finish. Corrupt manifests require inspection, not deletion.

Migration does not enqueue work, invoke Gemini, extract audio, alter subtitle versions or remove files. Do not treat a completed dry run as an applied migration.

## Storage and removal

- `.uploads` holds resumable upload sessions outside the worker queue.
- `.assets/<id>` owns each registered source and attached subtitle versions.
- Workflow jobs reference their source asset and copy selected subtitle/audio inputs into their own input directory.
- Deleting a producing job does not delete promoted source videos or other jobs' copied inputs.
- Referencing jobs and active video readers block source deletion. Remove dependent jobs explicitly first.
- Deleting an unreferenced uploaded asset also removes its staging upload. No automatic deletion is introduced.

## Subtitle interoperability

Imported SRT files preserve their timestamps, multiline content and overlapping intervals. The optional SMI export uses the existing layout normalization because its event representation does not support overlapping cues directly. SRT remains the authoritative editable subtitle format.
