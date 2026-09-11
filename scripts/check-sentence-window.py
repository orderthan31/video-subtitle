"""Explicit live check of a retained audio window; never invoked by unit tests."""
import argparse
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider
from media_worker.llm_trace import capture_calls
from video_service.storage import write_json_atomic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    provider = GeminiProvider()
    args.output.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.audio), "rb") as audio, capture_calls(args.output, args.output / "llm"):
        rate = audio.getframerate()
        if not 0 <= args.start < args.end <= audio.getnframes() / rate:
            raise ValueError("Audio window is outside the source")
        result = provider.transcribe_window(audio, round(args.start * rate), round(args.end * rate), "auto", lambda: None)
    write_json_atomic(args.output / "sentences.json", {"model": provider.transcription_model,
        "processed_start": args.start, "processed_end": args.end, "sentences": result})
    print(f"model={provider.transcription_model}; sentences={len(result)}; saved={args.output / 'sentences.json'}")


if __name__ == "__main__":
    main()
