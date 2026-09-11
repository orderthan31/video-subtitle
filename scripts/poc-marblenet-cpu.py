"""Official NVIDIA NeMo MarbleNet, CPU-only, local VAD evaluation."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time
import wave

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['WANDB_MODE'] = 'disabled'
os.environ['CUDA_VISIBLE_DEVICES'] = ''

import numpy as np
import psutil
import torch
from nemo.collections.asr.models import EncDecClassificationModel
from nemo.utils import logging

spec = importlib.util.spec_from_file_location('comparison',Path(__file__).with_name('compare-audio-strategies.py'))
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)
base = comparison.base


def window_batch(audio, positions, window=10080):
    """Centered zero-padded mono windows, matching NeMo VAD collate semantics."""
    half = window//2
    left = max(0,int(positions[0])-half)
    right = min(audio.getnframes(),int(positions[-1])-half+window)
    audio.setpos(left)
    samples = np.frombuffer(audio.readframes(right-left),dtype='<i2')
    samples = samples.reshape(-1,audio.getnchannels()).astype(np.float32).mean(axis=1)/32768
    batch = np.zeros((len(positions),window),dtype=np.float32)
    for i,position in enumerate(positions):
        begin = int(position)-half
        a,b = max(begin,left), min(begin+window,right)
        batch[i,a-begin:b-begin] = samples[a-left:b-left]
    return batch


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--audio',type=Path,required=True)
    p.add_argument('--srt',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--limit-seconds',type=float,default=0)
    args = p.parse_args()
    if args.limit_seconds < 0:
        p.error('limit must be nonnegative')
    args.out.mkdir(parents=True,exist_ok=False)
    start = time.perf_counter()
    hashes = {str(x):base.digest(x) for x in [args.audio,args.srt,args.model]}
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(0)
    logging.setLevel(logging.ERROR)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    t = time.perf_counter()
    model = EncDecClassificationModel.restore_from(str(args.model.resolve()),map_location=torch.device('cpu'))
    model.eval()
    model.preprocessor.featurizer.dither = 0.0
    assert all(x.device.type=='cpu' for x in model.parameters())
    labels = list(model.cfg.labels)
    assert labels == ['background','speech']
    load_seconds = time.perf_counter()-t
    with wave.open(str(args.audio),'rb') as audio:
        assert audio.getframerate()==16000 and audio.getsampwidth()==2
        frames = audio.getnframes()
        if args.limit_seconds:
            frames = min(frames,round(args.limit_seconds*16000))
        duration = frames/16000
        positions = np.arange(0,frames,1280)
        probabilities = np.empty(len(positions),dtype=np.float32)
        t = time.perf_counter()
        with torch.inference_mode():
            for first in range(0,len(positions),64):
                batch = torch.from_numpy(window_batch(audio,positions[first:first+64]))
                logits = model(input_signal=batch,input_signal_length=torch.full((len(batch),),10080,dtype=torch.long))
                probabilities[first:first+len(batch)] = logits.softmax(dim=-1)[:,1].numpy()
                if first and first % 6400 == 0:
                    print(f'Analyzed {first*.08:.0f}/{duration:.0f} seconds',flush=True)
        inference_seconds = time.perf_counter()-t
    assert np.isfinite(probabilities).all()
    memory = process.memory_info()
    cues = [[a,min(b,duration)] for a,b in base.cues_from_srt(args.srt) if a<duration]
    results, intervals = {}, {}
    for onset,offset in [(.3,.15),(.5,.3),(.7,.5)]:
        speech, quiet = False, []
        for value in probabilities:
            speech = bool(value >= (offset if speech else onset))
            quiet.append(not speech)
        kept = base.merge(base.complement(base.runs(quiet,.08,duration,minimum=3,padding=.3),duration),gap=.4)
        name = str(onset)
        intervals[name] = kept
        results[name] = base.metrics(kept,cues,duration)
    report = dict(duration_seconds=duration,labels=labels,model_parameters=sum(x.numel() for x in model.parameters()),
                  model_bytes=args.model.stat().st_size,torch=torch.__version__,cuda=torch.version.cuda,
                  device='cpu',threads=1,window_seconds=.63,hop_seconds=.08,batch_size=64,
                  preprocessing='mean of stereo, no gain, official mel frontend, dither disabled',
                  postprocessing='non-speech >=3s; padding .3s; merge gap .4s; no short speech discard',
                  model_load_seconds=load_seconds,inference_seconds=inference_seconds,
                  baseline_rss_bytes=rss_before,analysis_peak_rss_bytes=getattr(memory,'peak_wset',memory.rss),
                  variants=results,input_sha256=hashes,
                  inputs_unchanged=all(base.digest(x)==hashes[str(x)] for x in [args.audio,args.srt,args.model]))
    if not args.limit_seconds:
        manifest = comparison.write_segments(args.audio,args.out/'segments',intervals['0.5'])
        assert len(manifest)==results['0.5']['segments_60s']
        (args.out/'segments.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    report['script_seconds_excluding_imports'] = time.perf_counter()-start
    np.save(args.out/'probabilities.npy',probabilities)
    for name,data in [('report',report),('intervals',intervals)]:
        (args.out/f'{name}.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['variants','input_sha256']}))
    for name,data in results.items():
        print(name,json.dumps({k:v for k,v in data.items() if k!='affected'}))


if __name__=='__main__':
    main()
