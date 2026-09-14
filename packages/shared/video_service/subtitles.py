from __future__ import annotations

from typing import Iterable
import math
import textwrap

from .transcript import TranscriptSegment


def format_srt_timestamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Subtitle timestamp must be finite and non-negative")
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
        if start == end:
            raise ValueError("Subtitle duration is below SRT millisecond precision")
        index = len(blocks) + 1
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def segment_subtitles(segments: Iterable[TranscriptSegment], *, line_width=42, max_lines=2):
    """Wrap cues and share overlapping intervals without discarding dialogue."""
    if line_width < 1 or max_lines < 1:
        raise ValueError("Subtitle dimensions must be positive")
    normalized = []
    for segment in sorted(segments, key=lambda item: item.start):
        if not (math.isfinite(segment.start) and math.isfinite(segment.end)
                and 0 <= segment.start < segment.end):
            raise ValueError("Invalid subtitle time interval")
        text = "\n".join(" ".join(line.split()) for line in segment.text.splitlines() if line.strip())
        if not text:
            continue
        normalized.append(segment.with_text(text))
    # Sweep on the output millisecond clock. Never extend speech across another cue.
    events = {}
    for index, segment in enumerate(normalized):
        first, last = round(segment.start * 1000), round(segment.end * 1000)
        if last <= first:
            raise ValueError("Subtitle duration is below SRT millisecond precision")
        events.setdefault(first, []).append((index, True))
        events.setdefault(last, []).append((index, False))
    active = set()
    intervals = []
    previous = None
    for point in sorted(events):
        if previous is not None and point > previous and active:
            texts = [normalized[i].text for i in sorted(active)]
            speaker = normalized[next(iter(active))].speaker if len(active) == 1 else None
            intervals.append(TranscriptSegment(previous / 1000, point / 1000, "\n".join(texts), speaker))
        for index, starts in events[point]:
            if starts:
                active.add(index)
            else:
                active.discard(index)
        previous = point
    result = []
    for segment in intervals:
        lines = [line for utterance in segment.text.splitlines()
                 for line in textwrap.wrap(utterance, width=line_width, break_long_words=True, break_on_hyphens=False)]
        blocks = ["\n".join(lines[index:index+max_lines]) for index in range(0, len(lines), max_lines)]
        weights = [len(block.replace("\n", "")) for block in blocks]
        total = sum(weights)
        first, last = round(segment.start * 1000), round(segment.end * 1000)
        if last - first < len(blocks):
            raise ValueError("Insufficient time for subtitle text")
        consumed = 0
        cursor = first
        for index, (block, weight) in enumerate(zip(blocks, weights)):
            consumed += weight
            boundary = last if index == len(blocks)-1 else min(last-(len(blocks)-index-1),
                max(cursor+1, first+round((last-first)*consumed/total)))
            result.append(TranscriptSegment(cursor/1000, boundary/1000, block, segment.speaker))
            cursor = boundary
    return result
