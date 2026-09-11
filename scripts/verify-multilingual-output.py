"""Extract every MP4 subtitle track and compare against the job's SRT files; no AI."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from video_service.models import JobRecord
from media_worker.media import executable, probe
from media_worker.process import run_process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    load_environment()
    directory = args.job.resolve()
    job = JobRecord.from_dict(json.loads((directory / "job.json").read_text(encoding="utf-8")))
    if job.status != "COMPLETED" or job.options.subtitle_mode != "soft":
        raise ValueError("A completed soft-subtitle job is required")
    work = ROOT / "data/multilingual-validation" / uuid4().hex
    work.mkdir(parents=True)
    target = directory / "output/final.mp4"
    tracks = [s for s in probe(target, work)["streams"] if s["codec_type"] == "subtitle"]
    languages = [job.options.target_language, *job.options.additional_languages]
    if len(tracks) != len(languages):
        raise ValueError("Wrong subtitle track count")
    for index, language in enumerate(languages):
        stem = "translated" if index == 0 else f"translated.{language}"
        expected = work / f"expected-{index}.srt"
        actual = work / f"actual-{index}.srt"
        run_process([executable("ffmpeg"), "-nostdin", "-y", "-i", directory / f"output/{stem}.srt",
            "-c:s", "srt", expected], cwd=work, log_name=f"normalize-{index}.log")
        run_process([executable("ffmpeg"), "-nostdin", "-y", "-i", target, "-map", f"0:s:{index}",
            "-c:s", "srt", actual], cwd=work, log_name=f"extract-{index}.log")
        if actual.read_text(encoding="utf-8").strip() != expected.read_text(encoding="utf-8").strip():
            raise ValueError(f"Subtitle text/timing mismatch for {language}")
        if tracks[index].get("tags", {}).get("handler_name") != language:
            raise ValueError("Wrong subtitle track language label")
    report = {"job_id": job.job_id, "languages": languages, "roundtrip": "passed", "tracks": tracks}
    (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(work / "report.json"), "languages": languages, "roundtrip": "passed"}))


if __name__ == "__main__":
    main()
