import hashlib
import json
import logging

from video_service.storage import read_json, write_json_atomic


class Checkpoints:
    def __init__(self, work, identity, before_write):
        self.work = work
        self.identity = identity
        self.before_write = before_write

    def run(self, name, operation, files=()):
        key = hashlib.sha256(json.dumps([self.identity, name], sort_keys=True).encode()).hexdigest()
        path = self.work / "checkpoints" / (key + ".json")
        try:
            saved = read_json(path)
            if all((self.work / name).is_file() and
                    [(self.work / name).stat().st_size, (self.work / name).stat().st_mtime_ns] == stamp
                    for name, stamp in saved["files"].items()) and set(saved["files"]) == set(files):
                logging.info("Resuming completed checkpoint: %s", name)
                return saved["result"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result = operation()
        stamps = {name: [(self.work / name).stat().st_size, (self.work / name).stat().st_mtime_ns]
            for name in files}
        write_json_atomic(path, {"result": result, "files": stamps}, before_write=self.before_write)
        return result
