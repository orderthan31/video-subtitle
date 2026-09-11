"""Evaluate cached probabilities only: no inference, training or network."""
import argparse
import importlib.util
import itertools
import json
from pathlib import Path
import time

import numpy as np

spec = importlib.util.spec_from_file_location('base', Path(__file__).with_name('analyze-rule-audio.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def quiet_mask(probabilities, onset, offset):
    indices = np.arange(len(probabilities))[:, None]
    # Last decisive event implements the same per-channel hysteresis as a loop.
    last_on = np.maximum.accumulate(np.where(probabilities >= onset, indices, -1), axis=0)
    last_off = np.maximum.accumulate(np.where(probabilities < offset, indices, -1), axis=0)
    return ~np.any(last_on > last_off, axis=1)


def clipped(intervals, left, right):
    return [[max(a,left),min(b,right)] for a,b in intervals if b>left and a<right]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poc', type=Path, required=True)
    parser.add_argument('--srt', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    protected = [args.poc/'probabilities.npy',args.poc/'report.json',args.srt]
    hashes = {str(p):base.digest(p) for p in protected}
    probabilities = np.load(protected[0])
    duration = json.loads(protected[1].read_text())['duration_seconds']
    cues = base.cues_from_srt(args.srt)
    midpoint = duration/2
    centers = (np.arange(len(probabilities))+.5)*.032
    inside = np.zeros(len(centers),dtype=bool)
    for a,b in cues:
        inside |= (centers>=a)&(centers<b)
    values = probabilities.max(axis=1)
    quantiles = [0,.1,.25,.5,.75,.9,.99,1]
    distributions = {name:dict(zip(map(str,quantiles),map(float,np.quantile(values[mask],quantiles))))
                     for name,mask in [('subtitle',inside),('outside_subtitle',~inside)]}
    rows, intervals = [], {}
    for onset,ratio in itertools.product([.000001,.000003,.00001,.00003,.0001,.0003,.001,.003,.005,.01,.02,.03,.05,.1,.2,.3,.5],[.25,.5,1.]):
        quiet = quiet_mask(probabilities,onset,onset*ratio)
        for minimum,padding in itertools.product([3,5],[.3,.6,1.]):
            kept = base.merge(base.complement(base.runs(quiet,.032,duration,minimum,padding),duration),gap=.4)
            key = f'{onset}_{ratio}_{minimum}_{padding}'
            row = dict(id=key,onset=onset,offset=onset*ratio,minimum=minimum,padding=padding)
            for name,left,right in [('full',0,duration),('selection_half',0,midpoint),('heldout_half',midpoint,duration)]:
                region = clipped(kept,left,right)
                ref = clipped(cues,left,right)
                result = base.metrics(region,ref,right-left)
                row[name] = {k:v for k,v in result.items() if k!='affected'}
            rows.append(row)
            intervals[key] = kept
    # Select solely on the first half. Report second half without retuning.
    selected = {}
    for tolerance in [0,.1,1]:
        eligible = [r for r in rows if r['selection_half']['subtitle_loss_seconds']<=tolerance+1e-8]
        selected[str(tolerance)] = min(eligible,key=lambda r:(r['selection_half']['kept_seconds'],r['id'])) if eligible else None
    full_zero = [r for r in rows if r['full']['subtitle_loss_seconds']<=1e-8]
    oracle = min(full_zero,key=lambda r:r['full']['kept_seconds']) if full_zero else None
    chosen_ids = {r['id'] for r in selected.values() if r is not None}
    if oracle:
        chosen_ids.add(oracle['id'])
    report = dict(combinations=len(rows),duration_seconds=duration,distributions=distributions,
                  selected_on_first_half=selected,exploratory_full_zero_loss=oracle,
                  input_sha256=hashes,inputs_unchanged=all(base.digest(p)==hashes[str(p)] for p in protected),
                  elapsed_seconds=time.perf_counter()-start)
    for name,value in [('report',report),('grid',rows),('selected-intervals',{k:intervals[k] for k in chosen_ids})]:
        (args.out/f'{name}.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
