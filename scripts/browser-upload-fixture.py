"""Create isolated MP4 resume fixtures; does not call Gemini or start a worker."""
import argparse
import json
from pathlib import Path
import shutil
import struct
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8001/api")
    args = parser.parse_args()
    if not args.source.is_file() or not 0 < args.source.stat().st_size < 3 * 1024**2:
        parser.error("--source must be a nonempty MP4 smaller than 3 MiB")
    root = Path(__file__).resolve().parents[1] / "data/browser-fixtures" / uuid4().hex
    original = root / "original/resume-test.mp4"
    changed = root / "changed/resume-test.mp4"
    original.parent.mkdir(parents=True)
    changed.parent.mkdir(parents=True)
    shutil.copyfile(args.source, original)
    with original.open("ab") as stream:
        stream.write(struct.pack(">I4s", 64 * 1024**2 + 8, b"free"))
        for _ in range(64):
            stream.write(bytes(1024**2))
    shutil.copyfile(original, changed)
    with changed.open("r+b") as stream:
        stream.seek(args.source.stat().st_size + 8)
        stream.write(b"changed")
    with httpx.Client(timeout=60) as client:
        response = client.post(args.api + "/uploads", json={
            "filename": original.name, "size": original.stat().st_size})
        response.raise_for_status()
        job_id = response.json()["job_id"]
        with original.open("rb") as stream:
            response = client.put(f"{args.api}/uploads/{job_id}/chunks?offset=0",
                content=stream.read(4 * 1024**2))
        response.raise_for_status()
    report = {"original": str(original), "changed": str(changed), "job_id": job_id,
        "seeded_bytes": response.json()["uploaded_bytes"], "expected_size": original.stat().st_size}
    (root / "fixture.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
