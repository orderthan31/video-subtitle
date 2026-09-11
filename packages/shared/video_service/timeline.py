from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TimelineSpan:
    processed_start: float
    processed_end: float
    original_start: float
    original_end: float

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineSpan":
        return cls(
            processed_start=float(data["processed_start"]),
            processed_end=float(data["processed_end"]),
            original_start=float(data["original_start"]),
            original_end=float(data["original_end"]),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "processed_start": self.processed_start,
            "processed_end": self.processed_end,
            "original_start": self.original_start,
            "original_end": self.original_end,
        }

    @property
    def processed_duration(self) -> float:
        return max(0.0, self.processed_end - self.processed_start)

    @property
    def original_duration(self) -> float:
        return max(0.0, self.original_end - self.original_start)


def build_timeline_from_kept_intervals(intervals: Iterable[tuple[float, float]]) -> list[TimelineSpan]:
    spans: list[TimelineSpan] = []
    processed_cursor = 0.0
    for original_start, original_end in sorted(intervals):
        if original_end <= original_start:
            continue
        duration = original_end - original_start
        spans.append(
            TimelineSpan(
                processed_start=processed_cursor,
                processed_end=processed_cursor + duration,
                original_start=original_start,
                original_end=original_end,
            )
        )
        processed_cursor += duration
    return spans


def identity_timeline(duration: float) -> list[TimelineSpan]:
    safe_duration = max(0.0, float(duration))
    return [
        TimelineSpan(
            processed_start=0.0,
            processed_end=safe_duration,
            original_start=0.0,
            original_end=safe_duration,
        )
    ]


def map_processed_time_to_original(timestamp: float, spans: list[TimelineSpan], *, end_boundary: bool = False) -> float:
    if not spans:
        return max(0.0, timestamp)

    for span in (spans if end_boundary else reversed(spans)):
        if span.processed_start <= timestamp <= span.processed_end:
            offset = timestamp - span.processed_start
            return max(0.0, span.original_start + offset)

    if timestamp < spans[0].processed_start:
        return max(0.0, spans[0].original_start)

    last = spans[-1]
    offset = timestamp - last.processed_end
    return max(0.0, last.original_end + offset)


def map_segment_to_original(start: float, end: float, spans: list[TimelineSpan]) -> tuple[float, float]:
    mapped_start = map_processed_time_to_original(start, spans)
    mapped_end = map_processed_time_to_original(end, spans, end_boundary=True)
    if mapped_end < mapped_start:
        mapped_end = mapped_start
    return mapped_start, mapped_end


def project_segment_to_original(start: float, end: float, spans: list[TimelineSpan]) -> list[tuple[float, float]]:
    """Split a packed-clock cue at removed gaps instead of stretching through them."""
    result = []
    for span in spans:
        left, right = max(start, span.processed_start), min(end, span.processed_end)
        if right <= left:
            continue
        a = span.original_start + left - span.processed_start
        b = span.original_start + right - span.processed_start
        if result and abs(a - result[-1][1]) < 1e-8:
            result[-1] = (result[-1][0], b)
        else:
            result.append((a, b))
    return result
