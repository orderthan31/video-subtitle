from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.checkpoints import Checkpoints
from media_worker.llm_trace import capture_calls, cached_result


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)

    def test_success_reused_after_restart_but_changed_identity_recomputed(self):
        operation = Mock(return_value=[{"text": "speech"}])
        Checkpoints(self.root, "source-a", None).run("stt", operation)
        Checkpoints(self.root, "source-a", None).run("stt", operation)
        self.assertEqual(operation.call_count, 1)
        Checkpoints(self.root, "source-b", None).run("stt", operation)
        self.assertEqual(operation.call_count, 2)

    def test_failed_operation_never_becomes_checkpoint(self):
        operation = Mock(side_effect=[ValueError("failed"), "valid"])
        checkpoint = Checkpoints(self.root, "source", None)
        with self.assertRaises(ValueError):
            checkpoint.run("stt", operation)
        self.assertEqual(checkpoint.run("stt", operation), "valid")
        self.assertEqual(checkpoint.run("stt", operation), "valid")
        self.assertEqual(operation.call_count, 2)

    def test_missing_or_changed_media_invalidates_receipt(self):
        path = self.root / "audio.wav"
        def create():
            path.write_bytes(b"audio")
            return path.name
        operation = Mock(side_effect=create)
        checkpoint = Checkpoints(self.root, "source", None)
        checkpoint.run("extract", operation, files=(path.name,))
        path.write_bytes(b"changed")
        checkpoint.run("extract", operation, files=(path.name,))
        path.unlink()
        checkpoint.run("extract", operation, files=(path.name,))
        self.assertEqual(operation.call_count, 3)

    def test_llm_cache_is_per_job_and_keeps_success_only(self):
        operation = Mock(side_effect=[ValueError("bad"), ["valid"]])
        with capture_calls(self.root, self.root / "llm"):
            with self.assertRaises(ValueError):
                cached_result(["chunk", "hash"], operation)
            self.assertEqual(cached_result(["chunk", "hash"], operation), ["valid"])
            self.assertEqual(cached_result(["chunk", "hash"], operation), ["valid"])
        self.assertEqual(operation.call_count, 2)
