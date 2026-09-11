"""Offline audio experiment. Requires numpy and ffmpeg; never imports providers."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import wave

import numpy as np


def merge(intervals, gap=0):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1] + gap:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def complement(intervals, duration):
    result, cursor = [], 0.0
    for start, end in merge(intervals):
        if start > cursor:
            result.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < duration:
        result.append([cursor, duration])
    return result


def runs(mask, step, duration, minimum=3, padding=.3):
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return [[float(a * step + padding), float(min(b * step, duration) - padding)]
            for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0])
            if min(b * step, duration) - a * step >= minimum]


def cues_from_srt(path):
    pattern = r'(\d+):(\d+):(\d+),(\d+)\s+-->\s+(\d+):(\d+):(\d+),(\d+)'
    def seconds(parts):
        h, m, s, ms = map(int, parts)
        return h * 3600 + m * 60 + s + ms / 1000
    return [[seconds(m[:4]), seconds(m[4:])]
            for m in re.findall(pattern, path.read_text(encoding='utf-8-sig'))]


def overlap(interval, kept):
    a, b = interval
    return sum(max(0, min(b, d) - max(a, c)) for c, d in kept)


def metrics(kept, cues, duration):
    kept = merge(kept)
    affected = []
    for i, cue in enumerate(cues, 1):
        length = cue[1] - cue[0]
        loss = max(0, length - overlap(cue, kept))
        if loss > .001:
            affected.append(dict(cue=i, start=cue[0], end=cue[1],
                                 lost_seconds=loss, fully_lost=loss >= length - .001))
    union = merge(cues)
    reference = sum(b-a for a, b in union)
    loss = sum(b-a-overlap([a, b], kept) for a, b in union)
    seconds = sum(b-a for a, b in kept)
    return dict(kept_seconds=seconds, removed_seconds=duration-seconds,
                removed_percent=100*(duration-seconds)/duration,
                intervals=len(kept), segments_60s=sum(math.ceil((b-a)/60) for a,b in kept),
                subtitle_union_seconds=reference, subtitle_loss_seconds=max(0,loss),
                subtitle_loss_percent=100*max(0,loss)/reference if reference else 0,
                affected_cues=len(affected), fully_lost_cues=sum(x['fully_lost'] for x in affected),
                affected=affected)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    original = args.job / 'input/source.mp4'
    srt = args.job / 'output/translated.srt'
    baseline = args.job / 'work/timeline-map.json'
    protected = [original, srt, baseline, args.job / 'work/audio.wav']
    hashes = {str(p): digest(p) for p in protected}
    analysis = args.out / 'analysis-stereo.wav'
    subprocess.run([args.ffmpeg, '-nostdin', '-v', 'error', '-i', str(original),
                    '-map', '0:a:0', '-vn', '-ar', '16000', '-c:a', 'pcm_s16le',
                    str(analysis)], check=True)
    with wave.open(str(analysis), 'rb') as audio:
        rate, channels, total = audio.getframerate(), audio.getnchannels(), audio.getnframes()
        assert audio.getsampwidth() == 2
        frame = 320  # 20 ms, with bounded memory for FFT batches.
        full, band = [], []
        frequencies = np.fft.rfftfreq(frame, 1/rate)
        bandmask = (frequencies >= 150) & (frequencies <= 4500)
        weights = np.ones(len(frequencies)); weights[1:-1] = 2
        while raw := audio.readframes(frame * 1000):
            values = np.frombuffer(raw, dtype='<i2').reshape(-1, channels).astype(float)/32768
            values = np.pad(values, ((0, (-len(values)) % frame), (0, 0)))
            values = values.reshape(-1, frame, channels)
            full.append(np.mean(values**2, axis=1))
            spectrum = np.abs(np.fft.rfft(values, axis=1))**2
            band.append(np.sum(spectrum[:,bandmask,:]*weights[bandmask,None], axis=1)/frame**2)
    energy, voice = np.concatenate(full), np.concatenate(band)
    db = 10*np.log10(np.maximum(energy, 1e-12))
    banddb = 10*np.log10(np.maximum(voice, 1e-12))
    ratio = voice/np.maximum(energy, 1e-12)
    step, duration = frame/rate, total/rate
    floor = np.empty_like(db)
    previous = None
    # Local 10th percentile over 30 seconds, capped to 3 dB change per window.
    for start in range(0,len(db),1500):
        level = np.percentile(db[start:start+1500],10,axis=0)
        level = np.clip(level,-75,-40)
        if previous is not None:
            level = np.clip(level,previous-3,previous+3)
        floor[start:start+1500] = level
        previous = level
    quiet_enter = (db <= -45) & (db <= floor+6)
    quiet_stay = (db <= -42) & (db <= floor+9)
    noise_enter = (banddb <= -50) & (ratio <= .1) & (db <= -30)
    noise_stay = (banddb <= -47) & (ratio <= .15) & (db <= -27)
    quiet, noise = [], []
    q, n = np.zeros(channels,dtype=bool), np.zeros(channels,dtype=bool)
    for i in range(len(db)):
        q = np.where(q,quiet_stay[i],quiet_enter[i])
        n = np.where(n,noise_stay[i],noise_enter[i])
        quiet.append(bool(q.all()))
        noise.append(bool(n.all()))
    removed_quiet = runs(quiet,step,duration)
    removed_noise = runs(noise,step,duration)
    raw_kept = merge(complement(merge(removed_quiet+removed_noise),duration),gap=.4)
    baseline_kept = [[s['original_start'],s['original_end']]
                     for s in json.loads(baseline.read_text())]
    # A deliberately conservative operational guard, not tuned on subtitle overlap.
    fallback = sum(b-a for a,b in raw_kept) < duration*.5
    kept = baseline_kept if fallback else raw_kept
    simple3 = complement(runs(np.all(db <= -45,axis=1),step,duration,padding=.2),duration)
    cues = cues_from_srt(srt)
    variants = {'existing_5s':baseline_kept,'fixed_rms_3s':simple3,
                'rule_candidate':raw_kept,'rule_guarded':kept}
    report = dict(duration_seconds=duration,channels=channels,cues=len(cues),
                  fallback=fallback,parameters=dict(frame_ms=20,noise_window_seconds=30,
                  noise_percentile=10,noise_floor_db=[-75,-40],floor_slew_db=3,
                  quiet_enter_db=-45,quiet_exit_db=-42,relative_enter_db=6,relative_exit_db=9,
                  band_hz=[150,4500],band_enter_db=-50,band_exit_db=-47,
                  band_ratio_enter=.1,band_ratio_exit=.15,noise_enter_db=-30,noise_exit_db=-27,
                  minimum_exclusion_seconds=3,padding_seconds=.3,merge_gap_seconds=.4,
                  fallback_removed_fraction=.5),
                  variants={name:metrics(intervals,cues,duration) for name,intervals in variants.items()})
    manifest, timeline, processed = [], [], 0.0
    segments = args.out / 'segments'; segments.mkdir()
    with wave.open(str(analysis),'rb') as source:
        params = source.getparams()
        for a,b in kept:
            left,right = round(a*rate),round(b*rate)
            timeline.append(dict(original_start=left/rate,original_end=right/rate,
                                 processed_start=processed,processed_end=processed+(right-left)/rate))
            for first in range(left,right,60*rate):
                last = min(first+60*rate,right)
                name = f'{len(manifest)+1:04d}.wav'
                source.setpos(first)
                with wave.open(str(segments/name),'wb') as target:
                    target.setparams(params)
                    target.writeframes(source.readframes(last-first))
                manifest.append(dict(file=name,original_start=first/rate,original_end=last/rate,
                                     processed_start=processed+(first-left)/rate))
            processed += (right-left)/rate
    report['inputs_unchanged'] = all(digest(p)==hashes[str(p)] for p in protected)
    report['input_sha256'] = hashes
    for name, value in [('report',report),('segments',manifest),('timeline-map',timeline),
                        ('intervals',variants),('removed-reasons',dict(quiet=removed_quiet,out_of_band=removed_noise))]:
        (args.out/f'{name}.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    np.savez_compressed(args.out/'frame-features.npz',db=db,band_db=banddb,band_ratio=ratio,noise_floor_db=floor)
    print(json.dumps({k:v for k,v in report.items() if k not in ('input_sha256','variants')}))
    for name,value in report['variants'].items():
        print(name,json.dumps({k:v for k,v in value.items() if k!='affected'}))


if __name__ == '__main__':
    main()
