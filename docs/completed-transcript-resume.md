# Completed Transcription Resume

Retrying the same job preserves a successfully completed transcription even if
the configured Gemini models change. New jobs still use the configured model.
To intentionally transcribe again with a new model, create a new workflow.

After restoration to original video timestamps and transcript filtering, the
worker atomically writes `work/completed-transcript.json`. It contains the
completed cues, their digest, the producing model, and source/options identity.
On retry, a valid matching receipt skips extraction, preprocessing and STT.
The original-clock cues are not mapped through a timeline a second time.
Partial transcription results never become a completed receipt.

This policy does not migrate partially completed translation batches between
models. Translation queue caches remain model-specific, so switching the
translation model starts a new translation queue. Existing artifacts are kept.

## Legacy Recovery

An old `transcript.json` alone is not proof of completion. The worker refuses
paid re-transcription when this file exists without a verified receipt.
For a failed/cancelled legacy job where all three models previously matched:

```sh
python /app/scripts/recover-completed-transcript.py JOB_ID --legacy-model OLD_MODEL
python /app/scripts/recover-completed-transcript.py JOB_ID --legacy-model OLD_MODEL --apply
```

The dry run verifies the exact source/options/model-bound completed checkpoint
and its preprocessing timeline against the retained original-clock transcript.
It does not rely on a newly overwritten audio timeline. Mismatches and existing
receipts fail closed. Apply only adds the receipt, under job/execution locks;
it does not edit or delete the original media, logs or checkpoints and never
calls an LLM. Jobs using differing legacy model names require separate recovery.

## Verification

`tests/test_completed_transcript.py` covers model switches after translation
failure, timestamp preservation, changed source/options, corrupted and unverified
receipts, empty completed transcripts, and verified legacy migration. Tests use
fake providers with paid calls disabled.
