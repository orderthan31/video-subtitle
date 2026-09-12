# Upload capacity management

Uploads do not recursively scan storage while receiving chunks.

- Admission holds the shared admission lock, measures actual usage and remaining
  reservations, and checks quota and physical free space before creating a job.
- New jobs persist an initial reservation of four times the declared source size.
  Older jobs without this field keep the same implicit reservation for compatibility.
- The worker expands the reservation after probing duration to cover PCM audio and
  processing/output allowances. This is an estimate, not a filesystem allocation.
- Each upload batch checks physical free space only, in a thread pool. Declared file
  size enforcement, offset checks, disconnect rollback and disk-full handling remain.
- Successful writes update uploaded_bytes. Upload completion keeps the workspace
  reservation because READY jobs still need processing space.
- Terminal jobs release only unused reservations. All retained original, audio and
  result files still count as actual usage. Retrying reacquires capacity admission.
- The API audits actual usage, remaining reservations and disk free space every
  300 seconds, configured with STORAGE_AUDIT_INTERVAL_SECONDS (positive integer).
  It logs snapshots and warnings from a background task, without deleting files.
  Audit snapshots are advisory and can change during concurrent writes; admission
  still checks current storage rather than trusting a stale cached counter.

This change does not migrate storage or alter retention. Existing Windows bind
mounts remain supported. Whole-folder scans still occur at admission, processing
boundaries and audits, but no longer once per 4 MiB upload request. Physical free
space guards remain best-effort under unrelated concurrent disk writes, with OS
write errors as the final safeguard.

Validation (no Gemini requests):

```powershell
python -m unittest discover -s tests -p 'test_upload*.py'
python -m unittest discover -s tests -p 'test_capacity.py'
python -m unittest discover -s tests -p 'test_storage_monitor.py'
```
