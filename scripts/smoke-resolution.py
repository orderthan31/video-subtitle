"""Verify real resolution outputs, rotation, aspect ratio and no upscaling; no AI."""
import json
from fractions import Fraction
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from video_service.config import load_environment
from media_worker.media import executable, probe, encoding_args, select_encoder
from media_worker.process import run_process
from media_worker.validation import validate_output, validate_decodable


def main():
    load_environment()
    work = ROOT / "data/resolution-validation" / uuid4().hex
    work.mkdir(parents=True)
    (work / "translated.srt").write_text("1\n00:00:00,100 --> 00:00:01,500\nResolution test\n", encoding="utf-8")
    results = []
    for name, size, rotation, preset, codec, mode, expected in (
        ("landscape", "1920x1080", False, "720p", "hevc", "burn", (1280, 720)),
        ("portrait", "1920x1080", True, "720p", "h264", "soft", (720, 1280)),
        ("fullhd", "2560x1440", False, "1080p", "hevc", "soft", (1920, 1080)),
        ("small", "640x360", False, "1080p", "h264", "burn", (640, 360)),
        ("wide", "1920x800", False, "720p", "h264", "soft", (1280, 532)),
    ):
        case = work / name
        case.mkdir()
        shutil.copyfile(work / "translated.srt", case / "translated.srt")
        source = case / "source.mp4"
        run_process([executable("ffmpeg"), "-nostdin", "-y", "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", source],
            cwd=case, log_name="fixture.log")
        if rotation:
            rotated = case / "rotated.mp4"
            run_process([executable("ffmpeg"), "-nostdin", "-y", "-display_rotation:v:0", "90",
                "-i", source, "-c", "copy", rotated], cwd=case, log_name="rotate.log")
            source = rotated
        metadata = probe(source, case)
        video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        if rotation and not any(abs(float(s.get("rotation", 0))) == 90 for s in video.get("side_data_list", [])):
            raise ValueError("Fixture is missing rotation metadata")
        encoder = select_encoder(case, video_codec=codec)
        target = case / "final.mp4"
        run_process(encoding_args(source, target, video, software=encoder in {"libx264", "libx265"},
            video_codec=codec, subtitle_mode=mode, resolution=preset), cwd=case, log_name="encode.log")
        output = probe(target, case)
        validate_output(metadata, output, target.stat().st_size, codec, mode, preset)
        validate_decodable(target, case, lambda: None)
        final = next(s for s in output["streams"] if s["codec_type"] == "video")
        actual = (final["width"], final["height"])
        if actual != expected:
            raise ValueError(f"Wrong dimensions: {name}: {actual}")
        source_dar = Fraction(video["display_aspect_ratio"].replace(":", "/"))
        if rotation:
            source_dar = 1 / source_dar
        output_dar = Fraction(final["display_aspect_ratio"].replace(":", "/"))
        if abs(float(source_dar / output_dar) - 1) > 0.001:
            raise ValueError(f"Display aspect ratio changed: {name}")
        results.append({"case": name, "encoder": encoder, "resolution": preset,
            "dimensions": actual, "display_aspect_ratio": str(output_dar), "full_decode": "passed"})
    report = work / "report.json"
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report), "results": results}, indent=2))


if __name__ == "__main__":
    main()
