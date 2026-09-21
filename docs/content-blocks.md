# Provider Content Blocks

Gemini prompt feedback blocks and policy finish reasons are explicit
`ContentBlockedError` failures, not transient network/JSON errors. They are
logged as `remote_content_blocked` with the provider reason, without payloads.
Rate limits, transport errors and incomplete non-policy output retain existing
retry behavior.

The transcription and translation queues handle policy blocks once per request:

- Transcription replaces the entire blocked audio request window with
  `차단된 영역입니다`, then maps it back through the original video timeline.
- Translation replaces every subtitle in the blocked batch with the same text,
  retaining each original subtitle interval. The provider does not identify the
  individual triggering sentence, so no attempt is made to guess one.
- Existing transcription placeholders are excluded from translation requests
  and preserved verbatim, regardless of target language.
- Placeholder results and one-based blocked positions are checkpointed. They
  count as processed, are reused on retry, and do not stop later queue entries.
- No rephrasing or repeated requests are used to bypass the provider block.

Jobs containing placeholders finish subtitles and save an editable review draft,
then enter `AWAITING_REVIEW` before burn-in/encoding, even when automatic review
was originally disabled. Encoding requires an explicit review/render action.
Workflows without encoding finish their requested subtitle outputs normally.

The list and detail views show provider content-block notices with positions.
The review state is labelled `차단 구간 검토`. Originals, partial results, provider
traces and subtitle artifacts remain preserved.
