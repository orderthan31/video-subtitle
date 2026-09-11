"""CPU-only Silero ONNX experiment; no transcription or translation imports."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import time
import wave

import numpy as np
import onnxruntime as ort
import psutil

spec = importlib.util.spec_from_file_location('comparison', Path(__file__).with_name('compare-audio-strategies.py'))
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)
base = comparison.base


def prepare_chunk(raw, source_channels, mono, gain_db):
    chunk = np.frombuffer(raw, dtype='<i2').reshape(-1, source_channels).T.astype(np.float32)/32768
    if mono:
        chunk = chunk.mean(axis=0, keepdims=True)
    chunk = chunk * np.float32(10**(gain_db/20))
    clipped = int(np.count_nonzero(np.abs(chunk)>1))
    return np.clip(chunk,-1,1), clipped


def retained(probabilities, duration, threshold):
    state = np.zeros(probabilities.shape[1], dtype=bool)
    quiet = []
    for probability in probabilities:
        state = np.where(state, probability >= max(.01, threshold-.15), probability >= threshold)
        quiet.append(not bool(state.any()))
    return base.merge(base.complement(base.runs(quiet, .032, duration, minimum=3, padding=.3), duration), gap=.4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--prior', type=Path, required=True)
    parser.add_argument('--srt', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mono', action='store_true')
    parser.add_argument('--gain-db', type=float, default=0)
    args = parser.parse_args()
    if not np.isfinite(args.gain_db) or not -24 <= args.gain_db <= 24:
        parser.error('--gain-db must be finite and between -24 and 24')
    start = time.perf_counter()
    args.out.mkdir(parents=True, exist_ok=False)
    source = args.prior/'analysis-stereo.wav'
    inputs = [source, args.srt, args.prior/'intervals.json']
    hashes = {str(p):base.digest(p) for p in inputs}
    process = psutil.Process()
    baseline_rss = process.memory_info().rss
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    load_start = time.perf_counter()
    session = ort.InferenceSession(str(args.model), sess_options=options, providers=['CPUExecutionProvider'])
    assert session.get_providers() == ['CPUExecutionProvider']
    load_seconds = time.perf_counter()-load_start
    inference_start = time.perf_counter()
    with wave.open(str(source), 'rb') as audio:
        rate, source_channels, frames = audio.getframerate(), audio.getnchannels(), audio.getnframes()
        channels = 1 if args.mono else source_channels
        assert rate == 16000 and audio.getsampwidth() == 2
        duration = frames/rate
        probabilities = np.empty(((frames+511)//512, channels), dtype=np.float32)
        state = np.zeros((2, channels, 128), dtype=np.float32)
        context = np.zeros((channels, 64), dtype=np.float32)
        sample_rate = np.array(rate, dtype=np.int64)
        clipped_samples, sample_count, square_sum, peak = 0, 0, 0., 0.
        # Official ONNX input protocol: 512 samples plus 64-sample context,
        # with independent recurrent state for each channel in the batch.
        for i in range(len(probabilities)):
            raw = audio.readframes(512)
            chunk, clipped = prepare_chunk(raw, source_channels, args.mono, args.gain_db)
            clipped_samples += clipped
            sample_count += chunk.size
            square_sum += float(np.sum(chunk.astype(np.float64)**2))
            peak = max(peak,float(np.abs(chunk).max()))
            chunk = np.pad(chunk, ((0,0),(0,512-chunk.shape[1])))
            model_input = np.concatenate((context,chunk),axis=1)
            output,state = session.run(None, {'input':model_input,'state':state,'sr':sample_rate})
            probabilities[i] = output.reshape(channels)
            context = model_input[:,-64:]
            if i and i % 30000 == 0:
                print(f'Analyzed {min(i*512/rate,duration):.0f}/{duration:.0f} seconds',flush=True)
    inference_seconds = time.perf_counter()-inference_start
    assert np.isfinite(probabilities).all() and (probabilities >= 0).all() and (probabilities <= 1).all()
    analysis_memory = process.memory_info()
    cues = base.cues_from_srt(args.srt)
    prior = json.loads((args.prior/'intervals.json').read_text())
    variants = {'existing_5s':prior['existing_5s'],'previous_rules':prior['rule_candidate']}
    # Predeclared settings; do not select the best threshold using reference cues.
    for threshold in [.3,.5,.7]:
        variants[f'silero_{threshold}'] = retained(probabilities,duration,threshold)
    results = {name:base.metrics(intervals,cues,duration) for name,intervals in variants.items()}
    export_start = time.perf_counter()
    manifest = comparison.write_segments(source,args.out/'segments',variants['silero_0.5'])
    export_seconds = time.perf_counter()-export_start
    assert len(manifest) == results['silero_0.5']['segments_60s']
    np.save(args.out/'probabilities.npy', probabilities)
    unchanged = all(base.digest(p)==hashes[str(p)] for p in inputs)
    memory = process.memory_info()
    report = dict(duration_seconds=duration,channels=channels,variants=results,
                  input_processing=dict(source_channels=source_channels,mono=args.mono,gain_db=args.gain_db,
                                        clipped_samples=clipped_samples,clipped_percent=100*clipped_samples/sample_count,
                                        rms=float(np.sqrt(square_sum/sample_count)),peak=peak),
                  model_sha256=base.digest(args.model),model_bytes=args.model.stat().st_size,
                  upstream_commit='867c2aa692646a1f1de3e94a15c9dd9f614c0acb',
                  python=platform.python_version(),onnxruntime=ort.__version__,numpy=np.__version__,
                  providers=session.get_providers(),threads=1,
                  model_load_seconds=load_seconds,inference_with_wav_read_seconds=inference_seconds,
                  audio_seconds_per_wall_second=duration/inference_seconds,
                  baseline_rss_bytes=baseline_rss,analysis_peak_rss_bytes=getattr(analysis_memory,'peak_wset',analysis_memory.rss),
                  whole_run_peak_rss_bytes=getattr(memory,'peak_wset',memory.rss),
                  export_seconds=export_seconds,total_seconds=time.perf_counter()-start,
                  parameters=dict(thresholds=[.3,.5,.7],hysteresis=.15,min_non_speech_seconds=3,
                                  padding_seconds=.3,merge_gap_seconds=.4,short_speech_discard=False),
                  inputs_unchanged=unchanged,input_sha256=hashes)
    for name,value in [('report',report),('intervals',variants),('segments',manifest)]:
        (args.out/f'{name}.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    for name,value in results.items():
        print(name,json.dumps({k:v for k,v in value.items() if k!='affected'}))
    print(json.dumps({k:v for k,v in report.items() if k not in ['variants','input_sha256']}))


if __name__ == '__main__':
    main()
