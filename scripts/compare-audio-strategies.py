"""Offline frequency-filter/local-floor ablation; no provider imports or API calls."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import wave

import numpy as np

spec = importlib.util.spec_from_file_location('base', Path(__file__).with_name('analyze-rule-audio.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def measure(path):
    levels = []
    with wave.open(str(path), 'rb') as source:
        rate, channels, count = source.getframerate(), source.getnchannels(), source.getnframes()
        assert rate == 16000 and source.getsampwidth() == 2
        while raw := source.readframes(320*1000):
            values = np.frombuffer(raw, dtype='<i2').reshape(-1, channels).astype(float)/32768
            values = np.pad(values, ((0, (-len(values)) % 320), (0, 0)))
            energy = np.mean(values.reshape(-1, 320, channels)**2, axis=1)
            levels.append(10*np.log10(np.maximum(energy, 1e-12)))
    return np.concatenate(levels), count/rate


def thresholds(db, adaptive):
    if not adaptive:
        return np.full_like(db, -45.), np.full_like(db, -42.)
    centers, floors = [], []
    previous = None
    for start in range(0, len(db), 1500):
        stop = min(start+1500, len(db))
        level = np.clip(np.percentile(db[start:stop], 10, axis=0), -75, -30)
        if previous is not None:
            level = np.clip(level, previous-3, previous+3)
        floors.append(level)
        centers.append((start+stop-1)/2)
        previous = level
    floor = np.column_stack([np.interp(np.arange(len(db)), centers, np.array(floors)[:, c])
                             for c in range(db.shape[1])])
    # Raise the exclusion threshold above -45 only when the measured floor warrants it.
    enter = np.clip(floor+6, -45, -32)
    return enter, np.minimum(enter+3, -29)


def retained(db, duration, adaptive):
    enter, stay = thresholds(db, adaptive)
    state = np.zeros(db.shape[1], dtype=bool)
    quiet = []
    for i in range(len(db)):
        state = np.where(state, db[i] <= stay[i], db[i] <= enter[i])
        quiet.append(bool(state.all()))
    removed = base.runs(quiet, .02, duration, minimum=3, padding=.3)
    kept = base.merge(base.complement(removed, duration), gap=.4)
    return kept, enter


def write_segments(source_path, output, kept):
    output.mkdir()
    manifest, processed = [], 0.
    with wave.open(str(source_path), 'rb') as source:
        rate = source.getframerate()
        for a, b in kept:
            left, right = round(a*rate), round(b*rate)
            for first in range(left, right, 60*rate):
                last = min(first+60*rate, right)
                filename = f'{len(manifest)+1:04d}.wav'
                source.setpos(first)
                with wave.open(str(output/filename), 'wb') as target:
                    target.setparams(source.getparams())
                    target.writeframes(source.readframes(last-first))
                with wave.open(str(output/filename), 'rb') as check:
                    assert check.getnframes() == last-first
                manifest.append(dict(file=filename, original_start=first/rate,
                                     original_end=last/rate, processed_start=processed))
                processed += (last-first)/rate
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior', type=Path, required=True)
    parser.add_argument('--srt', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = args.prior/'analysis-stereo.wav'
    protected = [source, args.srt, args.prior/'intervals.json']
    before = {str(p):base.digest(p) for p in protected}
    filtered = args.out/'analysis-bandpass.wav'
    subprocess.run([args.ffmpeg, '-nostdin', '-v', 'error', '-i', str(source),
                    '-af', 'highpass=f=150:p=2,lowpass=f=4500:p=2',
                    '-c:a', 'pcm_s16le', str(filtered)], check=True)
    raw, duration = measure(source)
    band, filtered_duration = measure(filtered)
    assert raw.shape == band.shape and duration == filtered_duration
    prior = json.loads((args.prior/'intervals.json').read_text())
    variants = {'existing_5s':prior['existing_5s'], 'previous_rules':prior['rule_candidate']}
    features = {}
    for name, db, adaptive in [('matched_control',raw,False), ('strategy3',band,False),
                                ('strategy4',raw,True), ('strategy3_4',band,True)]:
        variants[name], features[name] = retained(db, duration, adaptive)
    cues = base.cues_from_srt(args.srt)
    report = dict(duration_seconds=duration, cues=len(cues),
                  parameters=dict(frame_ms=20, bandpass_hz=[150,4500], filter_poles=2,
                  floor_window_seconds=30, floor_percentile=10, floor_slew_db=3,
                  floor_bounds_db=[-75,-30], adaptive_enter_db=[-45,-32],
                  floor_margin_db=6, hysteresis_db=3, minimum_exclusion_seconds=3,
                  padding_seconds=.3, merge_gap_seconds=.4), variants={})
    for name, kept in variants.items():
        result = base.metrics(kept, cues, duration)
        result['over_50_percent_guard_would_trigger'] = result['removed_percent'] > 50
        if name in features:
            result['enter_db_range'] = [float(features[name].min()), float(features[name].max())]
        report['variants'][name] = result
    manifest = write_segments(source, args.out/'combined-segments', variants['strategy3_4'])
    assert len(manifest) == report['variants']['strategy3_4']['segments_60s']
    report['inputs_unchanged'] = all(base.digest(p)==before[str(p)] for p in protected)
    report['input_sha256'] = before
    for name, value in [('report',report), ('intervals',variants), ('segments',manifest)]:
        (args.out/f'{name}.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    np.savez_compressed(args.out/'features.npz', raw_db=raw, filtered_db=band, **features)
    for name, result in report['variants'].items():
        print(name, json.dumps({k:v for k,v in result.items() if k!='affected'}))
    print('inputs_unchanged:', report['inputs_unchanged'])


if __name__ == '__main__':
    main()
