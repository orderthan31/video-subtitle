"""Local rotated-video encode and full-decode check; no AI requests."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from video_service.config import load_environment
from media_worker.media import executable, probe, encoding_args, select_encoder
from media_worker.process import run_process
from media_worker.validation import validate_output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--subtitle", required=True, type=Path)
    parser.add_argument("--video-codec", choices=["hevc", "h264"], default="hevc")
    args = parser.parse_args()
    load_environment()
    work = ROOT / "data/output-validation" / uuid4().hex
    work.mkdir(parents=True)
    source = work / "rotated.mp4"
    run_process([executable("ffmpeg"), "-nostdin", "-i", args.source.resolve(), "-c", "copy",
        "-metadata:s:v:0", "rotate=90", source], cwd=work, log_name="rotate.log")
    shutil.copyfile(args.subtitle, work / "translated.srt")
    source_info = probe(source, work)
    encoder = select_encoder(work, video_codec=args.video_codec)
    target = work / "final.mp4"
    video = next(s for s in source_info["streams"] if s["codec_type"] == "video")
    run_process(encoding_args(source, target, video, software=encoder in {"libx265", "libx264"}, video_codec=args.video_codec), cwd=work, log_name="encode.log")
    output_info = probe(target, work)
    validate_output(source_info, output_info, target.stat().st_size, args.video_codec)
    run_process([executable("ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-i", target,
        "-f", "null", "-"], cwd=work, log_name="decode.log")
    report = {"encoder": encoder, "source": source_info, "output": output_info, "full_decode": "passed"}
    (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(work / "report.json"), "encoder": encoder, "validation": "passed"}))


if __name__ == "__main__":
    main()
