import math


def encoding_progress(path, duration):
    """Read only the tail of FFmpeg's key/value progress stream."""
    if not math.isfinite(duration) or duration <= 0:
        return None
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 4096))
            text = stream.read().decode("ascii", errors="ignore")
    except FileNotFoundError:
        return None
    fraction = None
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "out_time_us":
            try:
                seconds = int(value) / 1000000
            except ValueError:
                continue
            fraction = max(0.0, min(1.0, seconds / duration))
    return fraction
