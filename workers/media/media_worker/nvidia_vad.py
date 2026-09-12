"""Optional MarbleNet subprocess; importing this module needs no ML packages."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import wave


def retained_intervals(probabilities, duration, hop=.08):
    if not math.isfinite(duration) or duration <= 0 or len(probabilities) != math.ceil(duration / hop - 1e-9):
        raise ValueError("Invalid VAD duration or frame count")
    speech, quiet_start, removed = False, None, []
    for index, value in enumerate([*probabilities, 1.0]):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid VAD probability")
        speech = value >= (.15 if speech else .3)
        now = min(index * hop, duration)
        if not speech and quiet_start is None:
            quiet_start = now
        elif speech and quiet_start is not None:
            if now - quiet_start >= 3:
                removed.append((quiet_start + .3, now - .3))
            quiet_start = None
    kept, cursor = [], 0.0
    for a, b in removed:
        if a > cursor:
            kept.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < duration:
        kept.append((cursor, duration))
    merged = []
    for a, b in kept:
        if merged and a <= merged[-1][1] + .4:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def runtime():
    root = Path(__file__).resolve().parents[3]
    local = root / ".tools/nemo-poc"
    python = Path(os.getenv("NVIDIA_VAD_PYTHON", str(local / "Scripts/python.exe" if
        (local / "Scripts/python.exe").is_file() else Path(sys.executable))))
    model = Path(os.getenv("NVIDIA_VAD_MODEL", str(local / "vad_multilingual_marblenet.nemo")))
    if not python.is_file() or not model.is_file():
        raise RuntimeError("NVIDIA VAD requires NVIDIA_VAD_PYTHON and NVIDIA_VAD_MODEL; see docs/nvidia-vad.md")
    return python.resolve(), model.resolve()


def detect_intervals(source, work, check):
    from .process import run_process
    python, model = runtime()
    check()
    run_process([python, Path(__file__).resolve(), "--audio", source.resolve(), "--model", model,
                 "--output", (work / "nvidia-vad.json").resolve()],
                cwd=work, log_name="nvidia-vad.log", check=check, timeout=7200)
    report = json.loads((work / "nvidia-vad.json").read_text(encoding="utf-8"))
    with wave.open(str(source), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    if report["duration_seconds"] != duration:
        raise ValueError("NVIDIA VAD duration does not match audio")
    return retained_intervals(report["probabilities"], duration)


def select_device(torch, requested):
    if requested not in {"cpu", "cuda", "auto"}:
        raise ValueError("NVIDIA_VAD_DEVICE must be cpu, cuda or auto")
    if requested == "cpu":
        return "cpu"
    available = torch.cuda.is_available()
    if requested == "cuda" and not available:
        raise RuntimeError("NVIDIA VAD CUDA unavailable: check CUDA PyTorch, driver and container GPU access")
    return "cuda" if available else "cpu"


def batch_size():
    value = int(os.getenv("NVIDIA_VAD_BATCH_SIZE", "64"))
    if not 1 <= value <= 256:
        raise ValueError("NVIDIA_VAD_BATCH_SIZE must be between 1 and 256")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    requested = os.getenv("NVIDIA_VAD_DEVICE", "cpu").strip().lower()
    batch_limit = batch_size()
    os.environ.update(HF_HUB_OFFLINE="1", WANDB_MODE="disabled")
    if requested == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        import numpy as np
        import torch
        from nemo.collections.asr.models import EncDecClassificationModel
        from nemo.utils import logging
    except ImportError as exc:
        raise RuntimeError("Install the optional NeMo environment: docs/nvidia-vad.md") from exc
    device = select_device(torch, requested)
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(0)
    logging.setLevel(logging.ERROR)
    started = time.monotonic()
    model = EncDecClassificationModel.restore_from(str(args.model), map_location=torch.device(device))
    model.to(device)
    model.eval()
    model.preprocessor.featurizer.dither = 0.0
    if list(model.cfg.labels) != ["background", "speech"]:
        raise ValueError("Expected MarbleNet background/speech labels")
    if device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    model_load_seconds = time.monotonic() - started
    inference_started = time.monotonic()
    probabilities = []
    with wave.open(str(args.audio), "rb") as audio:
        if audio.getframerate() != 16000 or audio.getsampwidth() != 2:
            raise ValueError("NVIDIA VAD requires 16 kHz signed PCM16 audio")
        frames, window, hop = audio.getnframes(), 10080, 1280
        duration = frames / 16000
        with torch.inference_mode():
            for first in range(0, frames, hop * batch_limit):
                positions = np.arange(first, min(frames, first + hop * batch_limit), hop)
                left = max(0, int(positions[0]) - window // 2)
                right = min(frames, int(positions[-1]) + window // 2)
                audio.setpos(left)
                samples = np.frombuffer(audio.readframes(right - left), dtype="<i2")
                samples = samples.reshape(-1, audio.getnchannels()).astype(np.float32).mean(axis=1) / 32768
                batch = np.zeros((len(positions), window), dtype=np.float32)
                for index, position in enumerate(positions):
                    begin = int(position) - window // 2
                    a, b = max(begin, left), min(begin + window, right)
                    batch[index, a - begin:b - begin] = samples[a - left:b - left]
                logits = model(input_signal=torch.from_numpy(batch).to(device),
                               input_signal_length=torch.full((len(batch),), window, dtype=torch.long, device=device))
                probabilities.extend(logits.softmax(dim=-1)[:, 1].cpu().tolist())
                if first % (hop * batch_limit * 100) == 0:
                    print(f"VAD ({device}) analyzed {min(frames, first + hop * batch_limit) / 16000:.1f}/{duration:.1f}s", flush=True)
    if device == "cuda":
        torch.cuda.synchronize()
    inference_seconds = time.monotonic() - inference_started
    intervals = retained_intervals(probabilities, duration)
    report = {"version": 2, "device": device, "requested_device": requested, "batch_size": batch_limit,
              "torch_version": torch.__version__, "cuda_version": torch.version.cuda,
              "model_load_seconds": model_load_seconds, "inference_seconds": inference_seconds,
              "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else 0,
              "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved() if device == "cuda" else 0,
              "onset": .3, "offset": .15,
              "minimum_non_speech": 3, "padding": .3, "merge_gap": .4, "hop": .08,
              "duration_seconds": duration, "retained_seconds": sum(b - a for a, b in intervals),
              "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
              "elapsed_seconds": time.monotonic() - started, "intervals": intervals,
              "probabilities": probabilities}
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report), encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
