# Gemini Settings

Open Settings from the workspace sidebar (`#/settings`). Select or enter primary
and fallback model IDs separately for transcription and translation. Model-list
refresh calls the official Gemini Models API using the saved/environment key;
it does not generate content. Availability in that list does not guarantee a
model supports this application's audio or structured-output requests.

Fallback on request errors and on provider content blocks are independent,
opt-in settings. A fallback can incur additional API charges. A segment switches
to its configured fallback once; later automatic retries stay on that model.
The queue keeps its three-attempt budget (at most four model calls including the
initial switch), and authentication/storage failures do not trigger fallback.
If the fallback also blocks, the existing placeholder and pre-encoding review
policy applies. No prompt rewriting is performed.

Settings apply at the start of the next job/retry, not to an in-flight job.
Completed transcription receipts and completed queue results are preserved.
Changing a fallback does not automatically reprocess completed placeholder cues;
those are still review artifacts, not pending requests.

## Credentials And Access

- A newly entered API key is write-only. Responses report only whether a key
  exists and whether it came from registration or the environment.
- Registered keys take precedence over `GEMINI_API_KEY`. Removing registration
  returns to the environment key, if one exists. Empty input leaves the key alone.
- Settings are account-scoped when authentication is enabled. Without auth they
  are shared local-instance settings; only expose that mode to trusted users.
- Keys are Fernet-encrypted in `.llm-settings/<account>.json` in the persistent
  jobs volume. The encryption key is `.llm-settings/master.key`; Linux modes are
  0700 for the directory and 0600 for settings/key files. Preserve both on backup.
- This protects plaintext storage/accidental disclosure, not a compromised host
  or an attacker who can read the entire volume, including the encryption key.
  Restrict host/backup ACLs and use HTTPS (e.g. Tailscale Serve) for key entry.
- Credentials are never included in job snapshots, frontend storage or provider
  diagnostics. Invalid API payloads return sanitized errors without echoed input.
- Model-list requests have a fixed Google endpoint and never send keys in URLs.

No key is required just to start Docker and open Settings. A paid job still needs
a key and `PAID_LLM_ENABLED=true`; the settings page cannot disable the paid-call
safety lock. Model IDs and environment keys remain deployment defaults.
