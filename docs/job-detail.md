# Job detail page

Open a job from the workspace to navigate to `/#/jobs/<job_id>`. Hash navigation
supports deep links and browser back without unmounting the upload queue or its
draft form. Desktop uses two panels; mobile stacks video above subtitle tabs.

- Left: completed MP4, native playback controls, download and job metadata.
- Right: original transcript / translated subtitles, text search, timestamps and
  current-cue highlighting. Clicking a cue seeks the video without autoplay.
- Available transcript/translation JSON appears before video completion. Published
  partial failure artifacts are explicitly labeled as partial; private LLM traces
  and incomplete per-request caches are never returned.
- Reviewed subtitle drafts take precedence over generated transcript JSON.
- The read-only preview polls every five seconds, with only one refresh at a time.
- `/api/jobs/<id>/preview` returns available published tracks and video availability.
- `/api/jobs/<id>/stream` serves completed MP4 inline with byte-range support and
  the same ownership, expiration and download-lease protections as downloads.
- Streaming does not transcode. HEVC support depends on the browser/device; playback
  failures show an error and the existing MP4 download remains available.

No worker, LLM, upload or retention behavior changes.

Checks:

```powershell
python -m unittest discover -s tests -p test_job_preview.py
python -m unittest discover -s tests -p test_downloads.py
```

Browser regression: `apps/web/tests/job-detail-browser.mjs` mocks every API call and
uses a five-second H.264 test-pattern MP4 (`TEST_VIDEO`, default
`data/detail-test.mp4`). It covers 320/390/768/1440px layouts, seeking/playback, tabs,
search, deep links/back, draft preservation and pending results. Set
`PLAYWRIGHT_MODULE` to a Playwright installation and optionally `WEB_URL` and
`BROWSER_CHANNEL` (defaults: localhost:5177 and msedge). The existing
`workspace-browser.mjs` also covers queue form reset and navigation.
