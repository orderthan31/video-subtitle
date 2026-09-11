"""Explicit paid translation-only comparison. Never overwrite published results."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workers/media"), str(ROOT / "packages/shared")]
from media_worker.providers import GeminiProvider
from media_worker.llm_trace import capture_calls
from media_worker.validation import validate_cues
from video_service.storage import read_json, write_json_atomic
from video_service.subtitles import segment_subtitles, segments_to_srt
from video_service.transcript import TranscriptSegment


def translation_calls(root):
    calls = []
    for folder in sorted(root.glob("*")):
        if not (folder / "request.json").is_file() or not (folder / "response.json").is_file():
            continue
        request = read_json(folder / "request.json")
        prompt = request["payload"]["contents"][0]["parts"][0].get("text", "")
        if request.get("window") is not None or not prompt.startswith("Translate each subtitle"):
            continue
        response = read_json(folder / "response.json")
        body = json.loads(response.get("body", "{}"))
        usage = body.get("usageMetadata", {})
        calls.append({"start": datetime.strptime(folder.name.split("-")[0], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp(),
            "end": datetime.fromisoformat(response["received_at"]).timestamp(),
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(), "status": response.get("status"),
            "input": usage.get("promptTokenCount", 0), "output": usage.get("candidatesTokenCount", 0),
            "thinking": usage.get("thoughtsTokenCount", 0), "model": request["model"]})
    return calls


def stats(calls):
    return {"calls": len(calls), "elapsed_seconds": max(c["end"] for c in calls) - min(c["start"] for c in calls),
        **{name: sum(c[name] for c in calls) for name in ("input", "output", "thinking")}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    job = read_json(args.job / "job.json")
    if job["status"] != "COMPLETED":
        raise ValueError("Compare only a completed job")
    work = args.output.resolve() / "work"
    work.mkdir(parents=True, exist_ok=True)
    snapshots = {"source-transcript.json": args.job / "work/transcript.json",
        "asis-translated.json": args.job / "work/translated.json", "asis.srt": args.job / "output/translated.srt"}
    for name, source in snapshots.items():
        target = work / name
        if not target.exists():
            shutil.copy2(source, target)
        elif target.read_bytes() != source.read_bytes():
            raise ValueError("Existing comparison snapshot differs; use a fresh output folder")
    originals = {str(source): hashlib.sha256(source.read_bytes()).hexdigest() for source in snapshots.values()}
    source = [TranscriptSegment.from_dict(item) for item in read_json(work / "source-transcript.json")]
    before = [TranscriptSegment.from_dict(item) for item in read_json(work / "asis-translated.json")]
    if len(source) != len(before):
        raise ValueError("Baseline does not align with source transcript")
    language = job["options"]["target_language"]
    provider = GeminiProvider()
    progress_log = []
    def progress(value):
        progress_log.append(value)
        print(f"translation={value['completed']}/{value['total']}; active={value['in_flight']}; retrying={value['retrying']}", flush=True)
    started = time.monotonic()
    with capture_calls(args.output.resolve(), work / "llm"):
        after = provider.translate(source, language, lambda: None, work=work, progress=progress)
    elapsed = time.monotonic() - started
    if len(after) != len(before) or any((a.start, a.end) != (b.start, b.end) for a, b in zip(before, after)):
        raise ValueError("Translation changed sentence count or timing")
    cues = segment_subtitles(after, line_width=24 if language.split("-")[0] in {"ko", "ja", "zh"} else 42)
    validate_cues(cues, job["metadata"]["source_duration"])
    srt = segments_to_srt(cues)
    (work / "parallel.srt").write_text(srt, encoding="utf-8")
    write_json_atomic(work / "parallel-translated.json", [item.to_dict() for item in after])
    differences = [{"index": i + 1, "start": a.start, "end": a.end, "source": original.text,
        "before": a.text, "after": b.text} for i, (original, a, b) in enumerate(zip(source, before, after)) if a.text != b.text]
    write_json_atomic(work / "differences.json", differences)
    old_calls = translation_calls(args.job / "work/llm")
    new_calls = translation_calls(work / "llm")
    for path, digest in originals.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError("Original artifact changed during comparison")
    report = {"job_id": job["job_id"], "source_duration": job["metadata"]["source_duration"],
        "sentence_count": len(after), "srt_cue_count": len(cues), "changed_sentences": len(differences),
        "unchanged_sentences": len(after) - len(differences), "sentence_timing_equal": True,
        "original_artifacts_unchanged": True, "elapsed_seconds": elapsed, "progress": progress_log,
        "asis": stats(old_calls), "parallel": stats(new_calls),
        "identical_prompt_set": {c["prompt_hash"] for c in old_calls} == {c["prompt_hash"] for c in new_calls},
        "identical_model_set": {c["model"] for c in old_calls} == {c["model"] for c in new_calls}}
    write_json_atomic(work / "report.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "progress"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
