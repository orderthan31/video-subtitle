"""Opt-in paid check; retain a bounded audio excerpt and every request/result."""
import argparse
from pathlib import Path
import sys
import time
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    work = args.output.resolve() / "work"
    work.mkdir(parents=True, exist_ok=True)
    excerpt = work / "excerpt.wav"
    if not excerpt.exists():
        with wave.open(str(args.audio), "rb") as source, wave.open(str(excerpt), "wb") as target:
            target.setparams(source.getparams())
            target.writeframes(source.readframes(source.getframerate() * 360))
    provider = GeminiProvider()
    snapshots = []
    def progress(value):
        snapshots.append(value)
        print(f"completed={value['completed']}/{value['total']} active={value['in_flight']} retrying={value['retrying']}", flush=True)
    started = time.monotonic()
    with capture_calls(args.output.resolve(), work / "llm"):
        segments = provider.transcribe(excerpt, "auto", lambda: None, work=work, progress=progress)
    write_json_atomic(work / "result.json", {"elapsed_seconds": time.monotonic() - started,
        "progress": snapshots, "segments": [s.to_dict() for s in segments]})
    print(f"success; sentence_count={len(segments)}; elapsed={time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
