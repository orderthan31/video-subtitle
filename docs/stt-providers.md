# STT Provider Research

Checked against official documentation on 2026-09-22. This is an API capability
inventory, not a paid quality benchmark. This change implements translation only;
production STT remains Gemini. Consumer-app voice features do not establish public
audio-file API support, and text transcription does not imply subtitle timestamps.

| Provider | Public STT/audio input | Subtitle timing | Integration note |
| --- | --- | --- | --- |
| Google Gemini | Audio understanding and transcription | Prompted structured segment timestamps | Current integration; continue validating returned times and restoring packed-audio offsets. Google Cloud Speech-to-Text is a separate dedicated service. |
| OpenAI | Audio Transcriptions API; current guide uses `gpt-transcribe`, and documents existing GPT-4o transcription models and Whisper | `whisper-1` supports word/segment `timestamp_granularities`; `gpt-4o-transcribe-diarize` returns segment start/end in `diarized_json` | Do not assume every transcription model returns the same timing format. Files up to 25 MB; diarization over 30 seconds requires a chunking strategy. |
| xAI | Dedicated `/v1/stt`; `grok-voice-transcribe-2.0` and `1.0` | Word-level start/end; optional speaker labels | A real STT candidate, not merely Grok text chat. The documented language list includes Japanese and Korean; maximum upload is 500 MB. |
| Anthropic | Current public Claude model overview lists text/image input and text output; no direct audio-file STT API established by these docs | No verified STT timestamp contract | Include for translation, not selectable STT yet. This is not a claim about future or private features. |
| OpenRouter | Dedicated `/api/v1/audio/transcriptions` plus audio-input chat models | `verbose_json` exposes segments and optional words via `timestamp_granularities`, subject to provider support | Gateway, not a fifth model manufacturer. Docs show a timestamped `microsoft/mai-transcribe-2` example. Verify each routed model's supported parameters. |

## Implication

Among Google/OpenAI/xAI/Anthropic, the first three have documented STT paths.
The limiting factor for this app is reliable timestamps and source-language coverage,
not whether the provider has a conversational voice feature. Next STT POCs should
compare OpenAI's timestamp-capable endpoint and xAI STT against Gemini on the same
short, intact source sample, preserving original clock mapping. Corrupted source
files are not suitable quality benchmarks.

## Sources

- [Google audio understanding](https://ai.google.dev/gemini-api/docs/audio)
- [OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [xAI speech to text](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text)
- [Anthropic model capabilities](https://platform.claude.com/docs/en/models/overview)
- [OpenRouter speech to text](https://openrouter.ai/docs/guides/overview/multimodal/stt)

## Translation Adapter References

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
