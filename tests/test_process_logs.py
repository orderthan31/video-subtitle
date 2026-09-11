import json
from contextlib import contextmanager
import errno
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/media"))
from media_worker.process import Cancelled, ProcessLogLimitError, run_process


class ProcessLogTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)

    def run_child(self, code, **kwargs):
        return run_process([sys.executable, "-c", code], cwd=self.root, log_name="child.log", **kwargs)

    def test_structured_output_below_limit_is_complete(self):
        path = self.run_child("import json; print(json.dumps({'streams': [{'codec': 'hevc'}]}))", max_log_bytes=4096)
        self.assertEqual(json.loads(path.read_text()), {"streams": [{"codec": "hevc"}]})

    def test_overflow_cannot_return_a_truncated_success_even_after_process_exit(self):
        with self.assertRaises(ProcessLogLimitError):
            self.run_child("import sys; sys.stdout.buffer.write(b'x' * 262144)", max_log_bytes=4096)
        self.assertEqual((self.root / "child.log").stat().st_size, 4096)

    def test_error_includes_final_stderr(self):
        with self.assertRaisesRegex(RuntimeError, "FINAL_ERROR"):
            self.run_child("import sys; print('header', flush=True); print('FINAL_ERROR', file=sys.stderr); sys.exit(3)", max_log_bytes=4096)
        self.assertIn(b"FINAL_ERROR", (self.root / "child.log").read_bytes())

    def test_infinite_output_child_is_stopped_and_disk_usage_is_bounded(self):
        children = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        with patch("media_worker.process.subprocess.Popen", side_effect=capture), self.assertRaises(ProcessLogLimitError):
            self.run_child("import os\nwhile True: os.write(1, b'x' * 65536)", max_log_bytes=4096)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual((self.root / "child.log").stat().st_size, 4096)

    def test_idle_child_can_be_cancelled_while_reader_is_waiting(self):
        with self.assertRaises(Cancelled):
            self.run_child("import time; time.sleep(30)", check=lambda: (_ for _ in ()).throw(Cancelled()), max_log_bytes=4096)

    def test_invalid_limit_is_rejected_before_launch(self):
        for value in (0, 4095, 67108865, True, 8192.5):
            with patch("media_worker.process.subprocess.Popen") as launch, self.assertRaises(ValueError):
                self.run_child("pass", max_log_bytes=value)
            launch.assert_not_called()

    def test_reader_start_failure_terminates_the_started_child(self):
        children = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with patch("media_worker.process.subprocess.Popen", side_effect=capture), \
                patch("media_worker.process.Thread.start", side_effect=RuntimeError("thread unavailable")), \
                self.assertRaisesRegex(RuntimeError, "thread unavailable"):
            self.run_child("import time; time.sleep(30)")
        self.assertIsNotNone(children[0].poll())

    def test_log_write_failure_is_reported_instead_of_success(self):
        original = Path.open
        @contextmanager
        def failing_open(path, *args, **kwargs):
            with original(path, *args, **kwargs) as output:
                writer = Mock(wraps=output)
                writer.write.side_effect = OSError(errno.ENOSPC, "test disk full")
                yield writer
        with patch.object(Path, "open", failing_open), self.assertRaisesRegex(OSError, "Cannot save media process output") as caught:
            self.run_child("print('output')")
        self.assertEqual(caught.exception.__cause__.errno, errno.ENOSPC)


if __name__ == "__main__":
    unittest.main()
