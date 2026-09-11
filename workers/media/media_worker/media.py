from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import wave

from video_service.timeline import build_timeline_from_kept_intervals
from video_service.storage import write_json_atomic
from .process import run_process


def executable(name):
    return os.getenv(name.upper() + "_PATH", name)


def probe(source, work, check=lambda: None):
    log = run_process([executable("ffprobe"), "-v", "error", "-show_format", "-show_streams",
        "-of", "json", source], cwd=work, log_name="probe.json", check=check, timeout=120)
    data = json.loads(log.read_text(encoding="utf-8"))
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid media duration")
    data["duration"] = duration
    return data


def extract_audio(source, work, check=lambda: None):
    output = work / "audio.wav"
    run_process([executable("ffmpeg"), "-nostdin", "-y", "-i", source, "-map", "0:a:0",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", output],
        cwd=work, log_name="extract.log", check=check)
    return output


def kept_intervals(lines, duration, minimum=10.0, padding=0.2):
    """Retain short silence and padding at each speech boundary."""
    removed = []
    start = None
    for line in lines:
        match = re.search(r"silence_(start|end):\s*(-?[\d.]+)", line)
        if not match:
            continue
        value = min(duration, max(0.0, float(match[2])))
        if match[1] == "start":
            start = value
        elif start is not None:
            if value - start >= minimum:
                removed.append((start + padding, value - padding))
            start = None
    if start is not None and duration - start >= minimum:
        removed.append((start + padding, duration - padding))
    cursor = 0.0
    result = []
    for start, end in sorted(removed):
        if start > cursor:
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        result.append((cursor, duration))
    return result


def copy_audio_intervals(source, destination, intervals, check=lambda: None):
    actual = []
    with wave.open(str(source), "rb") as audio, wave.open(str(destination), "wb") as output:
        output.setparams(audio.getparams())
        rate = audio.getframerate()
        for start, end in intervals:
            first = max(0, round(start * rate))
            last = min(audio.getnframes(), round(end * rate))
            if last <= first:
                continue
            audio.setpos(first)
            remaining = last - first
            while remaining:
                check()
                frames = min(remaining, rate)
                output.writeframesraw(audio.readframes(frames))
                remaining -= frames
            actual.append((first / rate, last / rate))
    return build_timeline_from_kept_intervals(actual)


def preprocess_audio(source, work, check=lambda: None):
    with wave.open(str(source), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    log = run_process([executable("ffmpeg"), "-nostdin", "-i", source,
        "-af", "silencedetect=noise=-45dB:d=10", "-f", "null", "-"],
        cwd=work, log_name="silence.log", check=check)
    with log.open(encoding="utf-8", errors="replace") as lines:
        intervals = kept_intervals(lines, duration)
    output = work / "processed-audio.wav"
    spans = copy_audio_intervals(source, output, intervals, check)
    write_json_atomic(work / "timeline-map.json", [span.to_dict() for span in spans])
    return output, spans


def encoding_args(source, destination, video_stream, quality="balanced", software=False):
    fps = float(Fraction(video_stream.get("avg_frame_rate", "0/1")))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Invalid frame rate")
    cq = {"balanced": 24, "high": 20, "compact": 29}[quality]
    args = [executable("ffmpeg"), "-nostdin", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "subtitles=translated.srt", "-c:v", "libx265" if software else "hevc_nvenc"]
    args += ["-preset", "medium", "-crf", str(cq)] if software else ["-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    return args + ["-g", str(max(1, round(fps * 2))), "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", destination]
