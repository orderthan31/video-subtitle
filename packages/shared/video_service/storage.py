from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def resolve_under(root: Path, *parts: str | Path) -> Path:
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*map(Path, parts)).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"Path escapes storage root: {candidate}")
    return candidate


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json_atomic(path: Path, data: dict[str, Any] | list[Any]) -> None:
    ensure_dir(path.parent)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)


def remove_path_inside(root: Path, path: Path) -> None:
    target = resolve_under(root, path)
    if target == root.resolve():
        raise ValueError("Cannot remove the storage root")
    if not target.exists():
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 12:
        return ".mp4"
    allowed = "".join(ch for ch in suffix if ch.isalnum() or ch == ".")
    return allowed or ".mp4"
