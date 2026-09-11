"""Opt-in live Gemini check for speech crossing a 60-second request boundary."""
import argparse
import json
from pathlib import Path
import re
import sys
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--expected-text", required=True)
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required; this check calls Gemini")
    work = ROOT / "data/transcription-boundary" / uuid4().hex
    work.mkdir(parents=True)
    source = work / "boundary.wav"
    with wave.open(str(args.audio), "rb") as original, wave.open(str(source), "wb") as output:
        if (original.getnchannels(), original.getsampwidth()) != (1, 2):
            parser.error("--audio must be mono 16-bit PCM WAV")
        output.setparams(original.getparams())
        silence = bytes(original.getframerate() * 2)
        for _ in range(58):
            output.writeframesraw(silence)
        while frames := original.readframes(original.getframerate()):
            output.writeframesraw(frames)
        output.writeframesraw(silence)
    # Deliberately bypass silence preprocessing to exercise the request boundary.
    segments = GeminiProvider().transcribe(source, "en", lambda: None)
    actual = " ".join(segment.text for segment in segments)
    tokens = lambda text: re.findall(r"\w+", text.casefold())
    report = {"expected": args.expected_text, "actual": actual,
        "words_match": tokens(actual) == tokens(args.expected_text),
        "segments": [segment.to_dict() for segment in segments]}
    path = work / "report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), **report}, indent=2))
    raise SystemExit(0 if report["words_match"] else 1)


if __name__ == "__main__":
    main()
