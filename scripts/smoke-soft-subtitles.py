"""Encode both MP4 codecs and verify embedded subtitle text/timing; no AI calls."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from media_worker.media import executable, encoding_args, probe, select_encoder
from media_worker.process import run_process
from media_worker.validation import validate_output, validate_decodable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--subtitle", type=Path, required=True)
    args = parser.parse_args()
    load_environment()
    work = ROOT / "data/soft-subtitle-validation" / uuid4().hex
    work.mkdir(parents=True)
    shutil.copyfile(args.subtitle, work / "translated.srt")
    source = args.source.resolve()
    metadata = probe(source, work)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    run_process([executable("ffmpeg"), "-nostdin", "-y", "-i", "translated.srt", "-c:s", "srt", "expected.srt"],
        cwd=work, log_name="normalize.log")
    expected = (work / "expected.srt").read_text(encoding="utf-8").strip()
    results = []
    for codec in ("hevc", "h264"):
        encoder = select_encoder(work, video_codec=codec)
        target = work / f"{codec}.mp4"
        run_process(encoding_args(source, target, video, software=encoder in {"libx264", "libx265"},
            video_codec=codec, subtitle_mode="soft"), cwd=work, log_name=f"encode-{codec}.log")
        output = probe(target, work)
        validate_output(metadata, output, target.stat().st_size, codec, "soft")
        validate_decodable(target, work, lambda: None)
        extracted = work / f"{codec}.srt"
        run_process([executable("ffmpeg"), "-nostdin", "-y", "-i", target,
            "-map", "0:s:0", "-c:s", "srt", extracted], cwd=work, log_name=f"extract-{codec}.log")
        if extracted.read_text(encoding="utf-8").strip() != expected:
            raise ValueError(f"Embedded subtitle text/timing mismatch: {codec}")
        track = next(s for s in output["streams"] if s["codec_type"] == "subtitle")
        if track.get("tags", {}).get("language") != "kor":
            raise ValueError("Subtitle language metadata mismatch")
        results.append({"codec": codec, "encoder": encoder, "output": str(target),
            "subtitle_roundtrip": "passed", "full_decode": "passed", "track": track})
    report = work / "report.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
