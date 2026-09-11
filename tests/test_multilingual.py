from copy import deepcopy
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared"), str(ROOT / "apps/api")]
from fastapi.testclient import TestClient
from app.api import routes
from app.main import app
from media_worker.media import encoding_args
from media_worker.validation import validate_output
from video_service.repository import FilesystemJobRepository
from video_service.models import JobStatus, QualityProfile


class MultilingualTests(unittest.TestCase):
    def test_soft_track_mapping_and_single_primary_default(self):
        args = encoding_args("source", "output", {"avg_frame_rate": "30/1"},
            subtitle_mode="soft", additional_languages=["ja", "es"])
        self.assertEqual(args.count("-i"), 4)
        self.assertIn("translated.ja.srt", args)
        self.assertIn("translated.es.srt", args)
        self.assertIn("3:s:0", args)
        self.assertEqual(args[args.index("-disposition:s:0") + 1], "default")
        self.assertEqual(args[args.index("-disposition:s:1") + 1], "0")
        self.assertIn("language=jpn", args)
        self.assertIn("language=spa", args)
        self.assertNotIn("-vf", args)
        burn = encoding_args("source", "output", {"avg_frame_rate": "30/1"}, additional_languages=["ja"])
        self.assertEqual(burn.count("-i"), 1)
        self.assertEqual(burn[burn.index("-vf") + 1], "subtitles=translated.srt")

    def test_multilingual_output_validation_rejects_missing_or_extra_default_tracks(self):
        source = {"duration": 5, "streams": [{"codec_type": "video", "width": 640, "height": 360}]}
        output = {"duration": 5, "streams": [
            {"codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1",
             "width": 640, "height": 360, "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"},
            *[{"codec_type": "subtitle", "codec_name": "mov_text", "disposition": {"default": int(i == 0)}} for i in range(3)]]}
        validate_output(source, output, 10, subtitle_mode="soft", subtitle_count=3)
        with self.assertRaises(ValueError):
            validate_output(source, output, 10, subtitle_mode="soft", subtitle_count=2)
        wrong = deepcopy(output)
        wrong["streams"][-1]["disposition"]["default"] = 1
        with self.assertRaises(ValueError):
            validate_output(source, wrong, 10, subtitle_mode="soft", subtitle_count=3)

    def test_additional_downloads_must_be_declared_and_expire_with_job(self):
        root = ROOT / "data/test-runs" / uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        repo = FilesystemJobRepository(root)
        record = repo.create_job(original_filename="clip.mp4", expected_size=1, source_language="en",
            target_language="ko", quality_profile=QualityProfile.BALANCED, additional_languages=["ja"])
        output = repo.job_dir(record.job_id) / "output"
        (output / "translated.ja.srt").write_text("Japanese subtitles", encoding="utf-8")
        (output / "translated.es.srt").write_text("Undeclared", encoding="utf-8")
        record = repo.update_status(record.job_id, JobStatus.COMPLETED, metadata={"result_files": ["translated.ja.srt"]})
        with patch.object(routes, "repository", repo):
            client = TestClient(app)
            url = f"/api/jobs/{record.job_id}/results/"
            self.assertEqual(client.get(url + "translated.ja.srt").status_code, 200)
            self.assertEqual(client.get(url + "translated.es.srt").status_code, 404)
            self.assertEqual(client.get(url + "translated.ja.json").status_code, 404)
            record.metadata["results_expired_at"] = "2026-01-01T00:00:00+00:00"
            repo.save(record)
            self.assertEqual(client.get(url + "translated.ja.srt").status_code, 410)
