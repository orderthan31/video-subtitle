from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


NON_SPEECH_LABELS = {
    "숨소리",
    "호흡",
    "기합",
    "한숨",
    "신음",
    "breath",
    "breathing",
    "sigh",
    "grunt",
    "groan",
}

STANDALONE_VOCALIZATIONS = {
    "핫",
    "얍",
    "이얍",
    "헛",
    "하",
    "읏",
    "으앗",
    "후",
    "하아",
    "헉",
    "흐읍",
    "음",
    "어",
    "아",
    "으",
    "흠",
}


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranscriptSegment":
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            text=str(data.get("text", "")),
            speaker=data.get("speaker"),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        if self.speaker:
            data["speaker"] = self.speaker
        return data

    def with_times(self, start: float, end: float) -> "TranscriptSegment":
        return TranscriptSegment(start=start, end=end, text=self.text, speaker=self.speaker)

    def with_text(self, text: str) -> "TranscriptSegment":
        return TranscriptSegment(start=self.start, end=self.end, text=text, speaker=self.speaker)


def _strip_wrapping_label(text: str) -> str | None:
    stripped = text.strip()
    match = re.fullmatch(r"[\[\(（【]\s*([^\]\)）】]+?)\s*[\]\)）】]", stripped)
    if not match:
        return None
    return match.group(1).strip().lower()


def _compact(text: str) -> str:
    return re.sub(r"[\s\.\,\!\?~…\"'`]+", "", text.strip().lower())


def is_non_speech_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True

    label = _strip_wrapping_label(stripped)
    if label and label in NON_SPEECH_LABELS:
        return True

    # Plain words and interjections may carry meaning; only explicit labels are removed.
    return False


def filter_transcript_segments(segments: Iterable[TranscriptSegment], audio_filter="conservative") -> list[TranscriptSegment]:
    if audio_filter not in {"off", "conservative", "strong"}:
        raise ValueError("Unsupported audio filter")
    if audio_filter == "off":
        return [segment for segment in segments if segment.text.strip()]
    return [segment for segment in segments if not is_non_speech_text(segment.text)]


def transcript_segments_from_dicts(items: Iterable[dict[str, Any]]) -> list[TranscriptSegment]:
    return [TranscriptSegment.from_dict(item) for item in items]


def transcript_segments_to_dicts(items: Iterable[TranscriptSegment]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in items]
