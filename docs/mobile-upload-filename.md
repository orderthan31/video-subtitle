# Mobile file selection and filenames

Status: local fix only; not Docker deployed. No running services or user job data
were changed.

## Investigation

The upload queue copies `File.name` into its display name and creation request.
The API stores that value as `original_filename`; it does not substitute the
upload ID or a numeric counter. Current stored 1093/1094 upload names match their
asset names. No observed server record proves where the reported substitution
occurred.

The video-only `accept` attribute can select Android's media/photo picker rather
than its generic file chooser. A provider-supplied numeric name is therefore the
suspected upstream cause, not a confirmed on-device reproduction. A website
cannot recover an original filename that the picker does not expose.

## Change

- Remove the media-only accept hint from the source-video input so Chromium uses
  its generic file-selection path. Keep SRT attachment filtering unchanged.
- Validate video MIME type or a known video extension after selection and before
  upload admission. FFprobe on the server remains the actual content validation.
- Snapshot the FileList before resetting the picker, preserve exact File.name,
  and retain serial uploads, multi-file selection and resume behavior.
- Never guess replacement names or treat all-digit originals such as 1001.mp4 as
  invalid. No rename/migration is applied to existing assets.

## Verification and limits

18 frontend tests and TypeScript/Vite build passed. Browser regression covers the
generic input, rejecting a text file without uploading it, picker reset and exact
Korean/space/parenthesis/numeric filenames in serial upload creation requests.
Android's native picker itself cannot be reproduced by desktop Playwright; confirm
the actual mobile selection after a separately authorized deployment. If a chosen
provider still supplies a numeric display name, choose the source through Files
instead of the gallery; the web app cannot infer the missing original name.

References: [Chromium file picker selection logic](https://chromium.googlesource.com/chromium/src/+/70912634b85f207b0b9d7b1406cdb1d5fb146096/ui/android/java/src/org/chromium/ui/base/SelectFileDialog.java),
[Android document picker](https://developer.android.com/training/data-storage/shared/documents-files).
