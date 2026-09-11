"""Offline image smoke test. Uses only generated media, never Gemini or job storage."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import tempfile


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-3000:]}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true", help="Also require real HEVC NVENC encoding")
    args = parser.parse_args()
    for module in ("app.main", "media_worker.worker", "video_service.models", "google.genai"):
        importlib.import_module(module)
    encoders = run("ffmpeg", "-hide_banner", "-encoders")
    for name in ("libx264", "libx265", "aac"):
        if name not in encoders:
            raise RuntimeError(f"Missing encoder: {name}")
    if "subtitles" not in run("ffmpeg", "-hide_banner", "-filters"):
        raise RuntimeError("FFmpeg requires the libass subtitles filter")
    if "Noto Sans CJK" not in run("fc-match", "Noto Sans CJK KR", "--format=%{family}"):
        raise RuntimeError("Missing CJK subtitle font")
    with tempfile.TemporaryDirectory(prefix="container-smoke-") as folder:
        work = Path(folder)
        (work / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n\ud55c\uae00 Subtitle\n", encoding="utf-8")
        codec = "hevc_nvenc" if args.gpu else "libx265"
        extra = [] if args.gpu else ["-x265-params", "pools=1:frame-threads=1:log-level=error"]
        run("ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=size=320x180:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-vf", "subtitles=captions.srt",
            "-c:v", codec, "-threads", "1", *extra, "-c:a", "aac", "output.mp4", cwd=work)
        metadata = json.loads(run("ffprobe", "-v", "error", "-show_streams", "-of", "json", "output.mp4", cwd=work))
        if {stream["codec_name"] for stream in metadata["streams"]} != {"hevc", "aac"}:
            raise RuntimeError("Unexpected output codecs")
        run("ffmpeg", "-v", "error", "-i", "output.mp4", "-f", "null", "-", cwd=work)
    print(f"Runtime OK: Python imports, FFmpeg/FFprobe, CJK fonts, subtitles, {codec}, AAC and decode")


if __name__ == "__main__":
    main()
