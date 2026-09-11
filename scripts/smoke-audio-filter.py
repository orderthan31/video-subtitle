"""Verify real FFmpeg silence profiles with guarded speech and known pauses; no AI."""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from media_worker.media import preprocess_audio, extract_audio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    args = parser.parse_args()
    load_environment()
    root = ROOT / "data/audio-filter-validation" / uuid4().hex
    root.mkdir(parents=True)
    normalized = extract_audio(args.audio.resolve(), root)
    with wave.open(str(normalized), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("Use mono PCM16 16kHz speech")
        speech = audio.readframes(audio.getnframes())
    rate = 16000
    guard = b"".join(struct.pack("<h", round(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(4800))
    block = guard + speech + guard
    pcm = block + b"\0\0" * (6 * rate) + block + b"\0\0" * (12 * rate) + block
    source = root / "fixture.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    reports = []
    for mode in ("off", "conservative", "strong"):
        work = root / mode
        work.mkdir()
        output, spans = preprocess_audio(source, work, audio_filter=mode)
        expected = bytearray()
        cursor = 0
        for span in spans:
            first, last = round(span.original_start * rate) * 2, round(span.original_end * rate) * 2
            if any(pcm[cursor:first]):
                raise ValueError(f"Nonzero signal removed by {mode}")
            expected.extend(pcm[first:last])
            cursor = last
        if any(pcm[cursor:]):
            raise ValueError("Nonzero trailing signal removed")
        with wave.open(str(output), "rb") as audio:
            actual = audio.readframes(audio.getnframes())
        if actual != expected or (mode == "off" and actual != pcm):
            raise ValueError("PCM or timeline mapping mismatch")
        expected_spans = {"off": 1, "conservative": 2, "strong": 3}[mode]
        if len(spans) != expected_spans:
            raise ValueError(f"Wrong silence removal count: {mode}")
        reports.append({"filter": mode, "duration": len(actual) / 2 / rate,
            "spans": [span.to_dict() for span in spans], "signal_preserved": True})
    report = root / "report.json"
    report.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report), "results": reports}, indent=2))


if __name__ == "__main__":
    main()
