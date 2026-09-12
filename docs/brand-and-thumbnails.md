# JANGMYEON / 장면

## Product and UI decisions

- Working product name: 장면 (JANGMYEON). A video-first identity, not a subtitle-only tool. Naming is provisional; trademark/domain availability has not been assessed.
- Keep the earlier CLOVA Note-inspired information architecture: quiet left navigation, scan-friendly lists, and a video/text split detail view. This is an adaptation, not a claim to implement NAVER's internal design system.
- Retain Toss-style blue (#3182f6) for primary actions, neutral surfaces and restrained typography. Remove the generic VIDEO WORKSPACE eyebrow.
- Lists are for navigation and job controls; no download or result-promotion actions in rows.
- Job detail owns the complete result download menu, including MP4, WAV, SRT and SMI. Subtitle tabs retain contextual SRT downloads/editing. Asset detail owns original video download.
- Result promotion sits directly below the result player. Confirm that it creates an independent stored copy. Already-promoted results link to the registered original. No result promotion on a source-only preview.

## Poster selection policy

There is no universal second that identifies the important scene. The initial heuristic samples 10%, 35% and 60% of the video's duration, not the zero-second opening frame. It does not infer semantic importance or guarantee that titles/credits will be excluded.

FFmpeg input seeking reads one frame from each candidate, with at most two codec threads. Frames are scaled/padded to 480x270 RGB. Sparse luminance statistics favor contrast and penalize predominantly near-black/white images. The best candidate is encoded once as `thumbnail-v1.jpg`. No AI, audio extraction, transcription or translation is involved.

- Three candidate decodes, each capped at 12 seconds, plus one JPEG encode capped at 5 seconds. This avoids a full-video scan; worst-case registration overhead is about 41 seconds, normally much less. It happens during the existing registration state, not in the upload byte transfer.
- Atomic publication; existing posters are reused. Generation failure logs a warning but does not invalidate a complete upload. A failed poster can be retried with the backfill command.
- New uploads and promoted assets generate posters before asset publication. Existing assets use `python scripts/backfill-thumbnails.py --storage-root /data/video-jobs`; only missing JPEGs are added and no media/job manifests are rewritten.
- Image GET never runs FFmpeg. It checks ownership, uses the same deletion/read leases as source streaming and preserves the API-wide `no-store` privacy policy. The server-side JPEG is the cache, not a publicly cached video preview.
- Library uses lazy-loaded images with fixed dimensions and a file-icon fallback. It never starts video streams. Asset detail uses the poster and loads video only when playback is requested.

## Verification (2026-09-12)

- Backend: 315 tests passed with paid calls disabled. Includes a real synthetic MP4 upload producing a valid JPEG, attachment download, missing/foreign-owner rejection, sampling boundaries, blank-frame scoring and poster reuse/failure handling.
- Frontend: 16 tests, production build and both desktop/mobile browser suites passed. Verified removal of list downloads, source playback on demand, detail result menu, promotion confirmation cancellation and subtitle editor regressions.
- Existing assets backfilled in the CPU API Docker image without network: 1091 original 0.72s / 14,829 bytes; 818 0.58s / 15,170 bytes; promoted 1091 1.20s / 14,697 bytes; 1001 2.49s / 13,327 bytes. These are poster creation times, not page-load benchmarks.
- Backfill only created four JPEGs. No original video, audio, subtitle or job status was modified. Paid LLM calls remain disabled.
- API/web deployed successfully without replacing the GPU worker. Live Tailscale browser checks at 1440px and 390px loaded all four 480px JPEGs with zero video stream requests, zero page errors and no horizontal overflow.

References: [CLOVA Note](https://clovanote.naver.com/), [NAVER DEVIEW 2023](https://deview.kr/data/deview/session/attach/%5B122%5D%2B%EB%88%88%EC%9C%BC%EB%A1%9C%2B%EB%B3%B4%EB%A9%B0%2B%EB%93%A3%EB%8A%94%2B%EC%9D%8C%EC%84%B1%2B%EA%B8%B0%EB%A1%9D%2C%2B%ED%81%B4%EB%A1%9C%EB%B0%94%EB%85%B8%ED%8A%B8%2B%EC%84%9C%EB%B9%84%EC%8A%A4%EC%9D%98%2B%EC%9B%B9%2B%EA%B8%B0%EC%88%A0%2B%ED%86%BA%EC%95%84%EB%B3%B4%EA%B8%B0.pdf), [FFmpeg thumbnail filter](https://ffmpeg.org/ffmpeg-filters.html#thumbnail). The implemented policy uses separately sought frames instead of the consecutive-frame thumbnail filter to avoid scanning long inputs.
