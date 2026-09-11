"""Small real media/AI integration check; requires explicit --run-live."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from video_service.models import JobStatus, QualityProfile
from video_service.repository import FilesystemJobRepository
from media_worker.media import executable
from media_worker.process import run_process
from media_worker.providers import GeminiProvider
from media_worker.worker import Worker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--video-codec", choices=["hevc", "h264"], default="hevc")
    parser.add_argument("--subtitle-mode", choices=["burn", "soft"], default="burn")
    parser.add_argument("--resolution", choices=["original", "1080p", "720p"], default="original")
    parser.add_argument("--frame-size", choices=["640x360", "1920x1080", "2560x1440"], default="640x360")
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required; this test calls Gemini")
    load_environment()
    work = ROOT / "data" / "live-smoke" / uuid4().hex
    work.mkdir(parents=True)
    source = work / "test.mp4"
    run_process([executable("ffmpeg"), "-nostdin", "-y", "-f", "lavfi", "-i", f"testsrc2=size={args.frame_size}:rate=30",
        "-i", args.audio.resolve(), "-shortest", "-c:v", "libx264", "-c:a", "aac", source],
        cwd=work, log_name="fixture.log", timeout=60)
    repo = FilesystemJobRepository(work / "jobs")
    record = repo.create_job(original_filename="test.mp4", expected_size=source.stat().st_size,
        source_language="en", target_language="ko", quality_profile=QualityProfile.BALANCED,
        video_codec=args.video_codec, subtitle_mode=args.subtitle_mode, resolution=args.resolution)
    shutil.copyfile(source, repo.source_path(record))
    repo.update_upload_progress(record.job_id, record.expected_size)
    repo.update_status(record.job_id, JobStatus.QUEUED)
    Worker(repo, GeminiProvider()).process(record.job_id)
    record = repo.read(record.job_id)
    report = {"job_id": record.job_id, "status": record.status.value, "error": record.error,
        "output": str(repo.job_dir(record.job_id) / "output"), "metadata": record.metadata}
    (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if record.status == JobStatus.COMPLETED else 1)


if __name__ == "__main__":
    main()
