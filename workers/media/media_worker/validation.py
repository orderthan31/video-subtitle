import math


def validate_decodable(target, work, check):
    from .media import executable
    from .process import run_process

    run_process([executable("ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-i", target,
        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        cwd=work, log_name="validate-decode.log", check=check)


def validate_cues(cues, duration):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid subtitle media duration")
    previous_end = 0
    if not cues:
        raise ValueError("No speech subtitles detected")
    for cue in cues:
        if not (math.isfinite(cue.start) and math.isfinite(cue.end)
                and 0 <= cue.start < cue.end <= duration + 0.05):
            raise ValueError("Subtitle outside media timeline")
        if cue.start < previous_end - 0.001:
            raise ValueError("Subtitle cues overlap")
        if not cue.text.strip() or len(cue.text.splitlines()) > 2:
            raise ValueError("Invalid subtitle text layout")
        previous_end = cue.end


def display_dimensions(stream):
    width, height = int(stream["width"]), int(stream["height"])
    rotation = stream.get("tags", {}).get("rotate", 0)
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = side_data["rotation"]
            break
    rotation = float(rotation) % 360
    if not math.isfinite(rotation) or width <= 0 or height <= 0:
        raise ValueError("Invalid source display geometry")
    if math.isclose(rotation % 180, 90, abs_tol=0.01):
        return height, width
    if math.isclose(rotation % 180, 0, abs_tol=0.01):
        return width, height
    # Arbitrary-angle rotation can change the bounding box; do not guess it.
    return None


def validate_output(source, output, size, video_codec="hevc", subtitle_mode="burn"):
    if video_codec not in {"hevc", "h264"}:
        raise ValueError("Unsupported video codec")
    if subtitle_mode not in {"burn", "soft"}:
        raise ValueError("Unsupported subtitle mode")
    subtitles = [s for s in output["streams"] if s["codec_type"] == "subtitle"]
    if subtitle_mode == "soft":
        if len(subtitles) != 1 or subtitles[0].get("codec_name") != "mov_text":
            raise ValueError("Output must contain one mov_text subtitle track")
        if subtitles[0].get("disposition", {}).get("default") != 1:
            raise ValueError("Subtitle track must be enabled by default")
    elif subtitles:
        raise ValueError("Burned output must not contain subtitle tracks")
    videos = [s for s in output["streams"] if s["codec_type"] == "video"]
    audios = [s for s in output["streams"] if s["codec_type"] == "audio"]
    if size <= 0 or len(videos) != 1 or len(audios) != 1:
        raise ValueError("Invalid output streams")
    video, audio = videos[0], audios[0]
    tag = "hvc1" if video_codec == "hevc" else "avc1"
    if video.get("codec_name") != video_codec or video.get("codec_tag_string") != tag:
        raise ValueError(f"Output must be {video_codec} with {tag} tag")
    if video.get("pix_fmt") != "yuv420p" or audio.get("codec_name") != "aac":
        raise ValueError("Output must use yuv420p video and AAC audio")
    original_video = next(s for s in source["streams"] if s["codec_type"] == "video")
    expected = display_dimensions(original_video)
    actual = (int(video.get("width", 0)), int(video.get("height", 0)))
    if min(actual) <= 0 or (expected is not None and actual != expected):
        raise ValueError("Output resolution mismatch")
    for metadata in (source, output):
        if not math.isfinite(metadata["duration"]) or metadata["duration"] <= 0:
            raise ValueError("Invalid output duration")
    if abs(output["duration"] - source["duration"]) > max(1, source["duration"] * 0.01):
        raise ValueError("Output duration mismatch")
