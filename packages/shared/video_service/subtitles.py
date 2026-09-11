from __future__ import annotations

from typing import Iterable

from .transcript import TranscriptSegment


def format_srt_timestamp(seconds: float) -> str:
    total_millis = max(0, int(round(seconds * 1000)))
    millis = total_millis % 1000
    total_seconds = total_millis // 1000
    seconds_part = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02}:{minutes:02}:{seconds_part:02},{millis:03}"


def segments_to_srt(segments: Iterable[TranscriptSegment]) -> str:
    blocks: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        start = format_srt_timestamp(segment.start)
        if segment.end <= segment.start:
            raise ValueError("Subtitle end must follow its start")
        end = format_srt_timestamp(segment.end)
        index = len(blocks) + 1
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")
