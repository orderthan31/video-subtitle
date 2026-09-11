from pathlib import Path
import shutil
import sys
import subprocess
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/shared"))
from video_service.locking import encoding_slot
from video_service.repository import FilesystemJobRepository


class EncodingSlotTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = FilesystemJobRepository(self.root)

    def test_second_worker_uses_second_slot(self):
        with encoding_slot(self.repo, 2) as first:
            with encoding_slot(FilesystemJobRepository(self.root), 2) as second:
                self.assertEqual((first, second), (0, 1))

    def test_waiting_worker_can_cancel(self):
        count = 0
        def cancel():
            nonlocal count
            count += 1
            if count == 2:
                raise InterruptedError("cancelled")
        with encoding_slot(self.repo, 1):
            with self.assertRaises(InterruptedError):
                with encoding_slot(self.repo, 1, cancel):
                    self.fail("A second worker must not obtain the occupied slot")

    def test_failure_releases_slot(self):
        with self.assertRaises(RuntimeError):
            with encoding_slot(self.repo, 1):
                raise RuntimeError("encoder failed")
        with encoding_slot(self.repo, 1) as slot:
            self.assertEqual(slot, 0)

    def test_separate_process_cannot_take_occupied_slot(self):
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from video_service.locking import encoding_slot
from video_service.repository import FilesystemJobRepository
checks = 0
def check():
    global checks
    checks += 1
    if checks > 1:
        raise SystemExit(7)
with encoding_slot(FilesystemJobRepository(sys.argv[2]), 1, check):
    raise SystemExit(3)
"""
        with encoding_slot(self.repo, 1):
            child = subprocess.run([sys.executable, "-c", code, str(ROOT / "packages/shared"), str(self.root)],
                capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 7, child.stderr.decode(errors="replace"))

    def test_invalid_limits_rejected(self):
        for limit in (0, -1, 65):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                with encoding_slot(self.repo, limit):
                    pass


if __name__ == '__main__':
    unittest.main()
