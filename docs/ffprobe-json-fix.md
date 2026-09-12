# FFprobe diagnostic separation

2026-09-12: 1093.mp4 and 1094.mp4 failed at ANALYZING with
`Expecting value: line 1 column 2 (char 1)`.

FFprobe returned usable JSON but emitted `Referenced QT chapter track not found`
to stderr. The shared process runner merged stderr into stdout, so `probe.json`
started with a diagnostic rather than JSON. No model request was involved.

- The process runner now optionally drains stderr into a separate bounded file.
  Existing FFmpeg callers keep merged logs. Each separated stream has its own
  existing size limit; cancellation, timeouts, output failures and exit checking
  remain enabled. Both pipes are drained concurrently to avoid blocking.
- Probe uses `probe.json` for stdout and `probe.stderr.log` for diagnostics.
  Invalid JSON now reports the relevant logs instead of a bare parser error.
- 321 backend tests passed, including warning/JSON separation, stderr overflow,
  separate-stream cancellation/timeout, exit diagnostics and probe integration.
- Both actual source files passed the new probe in the rebuilt Docker worker
  image with network disabled and user storage mounted read-only. Durations:
  1093 = 7146.623 seconds; 1094 = 7210.666 seconds. Both expose video/audio tracks;
  both chapter warnings were preserved separately.
- Only the GPU worker is replaced for this fix. Failed job records and original
  logs are retained. Full paid workflows are not retried while PAID_LLM_ENABLED
  remains false. No original, audio or subtitle file was removed.
