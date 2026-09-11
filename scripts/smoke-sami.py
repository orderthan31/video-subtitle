"""Check SAMI timing, clearing and Unicode through FFmpeg's independent demuxer."""
import json
import html
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from video_service.sami import segments_to_sami
from video_service.subtitles import segments_to_srt
from video_service.transcript import TranscriptSegment
from media_worker.media import executable
from media_worker.process import run_process


def main():
    load_environment()
    work = ROOT / "data/sami-validation" / uuid4().hex
    work.mkdir(parents=True)
    cues = [TranscriptSegment(0.25, 1.5, "Hello & welcome"),
        TranscriptSegment(2, 3, "\ud55c\uae00 \uc790\ub9c9\nSecond line"),
        TranscriptSegment(3, 4.125, "Adjacent cue")]
    source, target = work / "translated.smi", work / "roundtrip.srt"
    source.write_text(segments_to_sami(cues, "ko"), encoding="utf-8")
    run_process([executable("ffmpeg"), "-nostdin", "-v", "error", "-i", source,
        "-f", "srt", target], cwd=work, log_name="convert.log")
    actual = target.read_text(encoding="utf-8-sig")
    expected = segments_to_srt(cues)
    report = {"matches": html.unescape(actual).strip() == expected.strip(), "expected": expected, "actual": actual}
    (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(work / "report.json"), "matches": report["matches"]}))
    raise SystemExit(0 if report["matches"] else 1)


if __name__ == "__main__":
    main()
