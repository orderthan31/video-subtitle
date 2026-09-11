# Manual Queue and Mobile Background Review

Updated: 2026-09-12

## Implemented

- Upload completion now produces READY, not QUEUED. Multiple videos can be
  uploaded one after another and kept on the server without starting Gemini.
- POST /api/jobs/{job_id}/start admits a complete READY upload to QUEUED.
  Repeating start while queued is idempotent and does not change its position.
- The list's Play button starts processing. The main form only uploads.
- Queue order uses persisted queued_at, with creation time for legacy jobs.
  Retry and subtitle-review rendering enter at the end of the queue.
- A storage-wide execution lock serializes worker polling and processing across
  local worker processes sharing that storage. This is not a distributed queue
  for independent storage roots. The direct process() helper is for tests/tools;
  the deployed worker must run through run()/tick().
- READY files are retained until explicitly deleted, subject to disk admission
  limits. Cancellation also preserves artifacts; only user deletion or an
  explicit expiry collection removes them.
- Downloading final.mp4 returns Content-Disposition naming it
  subtitle_<original stem>.mp4. Unicode and spaces are supported; unsafe path and
  control characters are removed. MOV/MKV inputs still produce MP4. Internal
  artifact paths and existing completed results are unchanged.

## Browser Upload Queue

The upload button immediately snapshots the file and its options into a local
queue, clears the selection and video description, and leaves the form available
for the next file. The job list shows pending, uploading, paused, error and complete
states, byte counts and progress. Only one file is transferred at a time; the next
waiting file starts when it finishes. An error or explicit pause lets other waiting
files proceed. Retry uses the same upload creation request ID and existing server
job, preserving resumable prefix verification. Every file retains its own settings.
Upload completion remains READY; it never starts paid processing automatically.
Local and server rows share a stable enqueue-time ordering during this session.

File references are in browser memory, not copied into IndexedDB. Hidden pages
suspend the queue, and returning resumes it if the page survives. Reload/close
prompts warn when possible; browser/OS process eviction can still discard pending
files. Server-created uploads remain resumable after selecting the original file;
files not yet sent to the server must be selected again. Background upload and
push notifications are not implemented.

The upload form now selects only one target language. Existing multi-language
records and their downloads remain supported for backwards compatibility.
New uploads default to `silence3`: silence detection at -45 dB for at least 3 seconds,
with existing speech-edge padding and original-timeline mapping retained. 5-second,
10-second and off options remain. Existing saved jobs keep their previous filter.

All Gemini SDK requests explicitly set the four supported adjustable categories
(harassment, hate speech, sexually explicit, dangerous content) to OFF. The current
Gemini safety guide lists no separate political category. Mandatory model safety
protections remain, and OFF does not guarantee a successful response. The applied
settings are included in retained request traces. See Google's official guide:
https://ai.google.dev/gemini-api/docs/safety-settings

## Mobile Background Limitations

Service workers do not guarantee uninterrupted multi-GB uploads after switching
apps, going home, locking the phone or losing the browser process. Background
Sync is intended for bounded deferred work; browsers may terminate long-running
work. Background Fetch is experimental and not consistently available across
browsers. It should not become a required cross-platform upload transport.

The current page explicitly pauses on visibility loss. This release retains
that behavior. A later improvement can retain chunk offsets and automatically
resume when visible if the original File is still available. After a page is
discarded, users may need to reselect their file. Persisting a multi-GB copy in
IndexedDB/OPFS creates quota, eviction and duplicate-storage problems and does
not remove OS execution limits.

Web Push can notify about SERVER processing independently of the upload page.
It requires permission, service-worker/manifest setup, push subscriptions and a
server-side sender (typically VAPID). iOS/iPadOS support requires a Home Screen
web app on supported versions (introduced in 16.4), and permission requested
from a user action. This is not an always-running percentage progress bar:
prefer stage changes, completion and failure, with coalescing/rate limiting.
Delivery may be delayed, so the job API remains authoritative.

The private Tailscale service need not be made public for outbound push delivery,
but the server needs access to push providers and opening the private result
still requires Tailscale. Keep payloads minimal: no dialogue, filenames or signed
download links on lock screens by default. Bind subscriptions to authorized
users/devices and remove expired subscriptions. Current authentication-disabled
preview requires an explicit device-scoping design before enabling push.

For reliable OS-managed background transfers, evaluate a native mobile upload
client separately; merely wrapping this web page is insufficient. No PWA,
notification permission, push subscription or background transfer was enabled
by this change.

## Sources

- [MDN: Background Fetch API](https://developer.mozilla.org/en-US/docs/Web/API/Background_Fetch_API)
- [MDN: Background Synchronization API](https://developer.mozilla.org/en-US/docs/Web/API/Background_Synchronization_API)
- [WebKit: Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)
- [Apple: Sending web push notifications](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers)

## Verification

Offline unit tests cover explicit admission, repeated starts, source completeness,
capacity failure, READY retention, FIFO after repository reload/heartbeat, shared
worker exclusion and safe Unicode download filenames. They do not call Gemini.
Physical-device background transfer and notification delivery are not tested.
