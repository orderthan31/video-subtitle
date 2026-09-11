import errno
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/shared"))
from video_service.storage import read_json, write_json_atomic
from video_service.capacity import assert_capacity, StorageLimitError, used_bytes


class AtomicStorageTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.path = self.root / "draft.json"
        write_json_atomic(self.path, {"revision": 1})

    def test_capacity_callback_matches_exact_utf8_bytes(self):
        check = Mock()
        data = {"text": "한국어\n日本語", "revision": 2}
        write_json_atomic(self.path, data, before_write=check)
        check.assert_called_once_with(self.path.stat().st_size)
        self.assertEqual(read_json(self.path), data)
        self.assertTrue(self.path.read_bytes().endswith(b"\n"))
        self.assertNotIn(b"\r\n", self.path.read_bytes())

    def test_temporary_full_payload_is_reserved_not_only_growth(self):
        before = self.path.read_bytes()
        quota = used_bytes(self.root) + 1
        with self.assertRaises(StorageLimitError):
            write_json_atomic(self.path, {"revision": 2}, before_write=lambda size:
                assert_capacity(self.root, quota, 0, additional=size))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_partial_write_failure_removes_temp_and_preserves_original(self):
        original_write = Path.write_bytes
        def partial_write(path, payload):
            original_write(path, payload[:4])
            raise OSError(errno.ENOSPC, "full")
        with patch.object(Path, "write_bytes", partial_write), self.assertRaises(OSError):
            write_json_atomic(self.path, {"revision": 2})
        self.assertEqual(read_json(self.path), {"revision": 1})
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_replace_failure_preserves_original_and_removes_temp(self):
        with patch.object(Path, "replace", side_effect=PermissionError("locked")), self.assertRaises(PermissionError):
            write_json_atomic(self.path, {"revision": 2})
        self.assertEqual(read_json(self.path), {"revision": 1})
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())
