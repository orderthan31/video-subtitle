"""Live speech-preservation check using caller-authorized speech and isolated breath audio."""
import argparse
import json
from pathlib import Path
import re
import sys
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "workers/media")]
from video_service.config import load_environment
from video_service.storage import write_json_atomic
from video_service.timeline import map_segment_to_original
from media_worker.media import executable, preprocess_audio
from media_worker.process import run_process
from media_worker.providers import GeminiProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--speech", type=Path, required=True)
    parser.add_argument("--breath", type=Path, required=True)
    parser.add_argument("--expected-text", required=True)
    args = parser.parse_args()
    if not args.run_live:
        parser.error("--run-live is required; this sends the test audio to Gemini")
    load_environment()
    work = ROOT / "data/vocalization-validation" / uuid4().hex
    work.mkdir(parents=True)
    pcm = []
    for name, source, gain in (("speech", args.speech, 1), ("breath", args.breath, 1), ("quiet", args.speech, 0.02)):
        target = work / f"{name}.wav"
        run_process([executable("ffmpeg"), "-nostdin", "-y", "-i", source.resolve(),
            "-af", f"volume={gain}", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", target],
            cwd=work, log_name=f"{name}.log", timeout=60)
        with wave.open(str(target), "rb") as audio:
            if audio.getnframes() > 16000 * 30:
                raise ValueError("Each fixture must be at most 30 seconds")
            pcm.append(audio.readframes(audio.getnframes()))
    normal, breath, quiet = pcm
    speech_end = len(normal) / 32000
    breath_start = speech_end + 1
    breath_end = breath_start + len(breath) / 32000
    quiet_start = breath_end + 12
    source = work / "mixed.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(normal + b"\x00" * 32000 + breath + b"\x00" * 32000 * 12 + quiet)
    provider = GeminiProvider()
    responses = []
    original_request = provider.request
    def observed_request(*call_args, **kwargs):
        result = original_request(*call_args, **kwargs)
        responses.append(result)
        write_json_atomic(work / "model-responses.json", responses)
        return result
    provider.request = observed_request
    analysis = provider.detect_vocalizations(source, "en", lambda: None)
    removals = analysis["removals"]
    cuts_inside_breath = all(breath_start <= item["start"] < item["end"] <= breath_end for item in removals)
    filtered, spans = preprocess_audio(source, work, vocalizations=removals, protected_audio=analysis["protected"])
    speech_exact = True
    with wave.open(str(source), "rb") as original, wave.open(str(filtered), "rb") as result:
        for span in spans:
            original.setpos(round(span.original_start * 16000))
            frames = round(span.original_end * 16000) - round(span.original_start * 16000)
            if original.readframes(frames) != result.readframes(frames):
                speech_exact = False
    speech_covered = all(sum(max(0, min(right, s.original_end) - max(left, s.original_start)) for s in spans)
        >= right-left-1/16000 for left, right in [(0, speech_end), (quiet_start, quiet_start+len(quiet)/32000)])
    segments = provider.transcribe(filtered, "en", lambda: None, spans=spans)
    restored = [s.with_times(*map_segment_to_original(s.start, s.end, spans)) for s in segments]
    words = lambda text: re.findall(r"[a-z]+", text.lower())
    recognized = words(" ".join(s.text for s in restored))
    expected = words(args.expected_text) * 2
    report = {"source": str(source), "model": provider.audio_filter_model, "removals": removals,
        "breath_slot": [breath_start, breath_end], "speech_slots": [[0, speech_end], [quiet_start, quiet_start+len(quiet)/32000]],
        "cuts_inside_breath": cuts_inside_breath, "speech_samples_retained": speech_covered,
        "retained_pcm_exact": speech_exact, "words_match": recognized == expected,
        "recognized": recognized, "expected": expected, "restored": [s.to_dict() for s in restored]}
    write_json_atomic(work / "report.json", report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not removals or not all((cuts_inside_breath, speech_covered, speech_exact, recognized == expected)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
