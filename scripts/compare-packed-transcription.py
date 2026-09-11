"""Explicit paid STT-only POC using unchanged filtered audio, packed to 60s."""
import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import unicodedata
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'workers/media'),str(ROOT/'packages/shared')]
from media_worker.providers import GeminiProvider
from media_worker.llm_trace import capture_calls
from video_service.storage import read_json, write_json_atomic
from video_service.subtitles import segments_to_srt, segment_subtitles
from video_service.transcript import TranscriptSegment, filter_transcript_segments


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def project(start,end,spans):
    pieces=[]
    for span in spans:
        a,b=max(start,span['processed_start']),min(end,span['processed_end'])
        if b>a:
            pieces.append([span['original_start']+a-span['processed_start'],
                           span['original_start']+b-span['processed_start']])
    return pieces


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text)
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def compare(before,after):
    a,b=[normalized(s.text) for s in before],[normalized(s.text) for s in after]
    # Repeated short replies must not align with unrelated scenes far away.
    pairs=[]
    last_after=-1
    for i,s in enumerate(before):
        candidates=[j for j,t in enumerate(after) if j>last_after and a[i]==b[j]
                    and t.start<s.end+2 and t.end>s.start-2]
        if candidates:
            j=min(candidates,key=lambda j:abs(after[j].start-s.start)+abs(after[j].end-s.end))
            pairs.append((i,j))
            last_after=j
    timing=[{'before_index':i+1,'after_index':j+1,
             'start_delta':after[j].start-before[i].start,'end_delta':after[j].end-before[i].end}
            for i,j in pairs]
    differences=[]
    for i,s in enumerate(before):
        candidates=[(j,t) for j,t in enumerate(after) if t.start<s.end+2 and t.end>s.start-2]
        scored=[(SequenceMatcher(None,a[i],b[j],autojunk=False).ratio(),j,t) for j,t in candidates]
        best=max(scored,key=lambda x:x[0]) if scored else None
        differences.append(dict(before_index=i+1,start=s.start,end=s.end,before=s.text,
                                best_nearby_after_index=best[1]+1 if best else None,
                                similarity=best[0] if best else 0,after=best[2].text if best else None))
    return dict(before_sentences=len(before),after_sentences=len(after),
                exact_normalized_sentences_in_order=len(pairs),
                before_without_nearby_candidate=sum(x['best_nearby_after_index'] is None for x in differences),
                nearby_similarity_ge_08=sum(x['similarity']>=.8 for x in differences),
                matched_timing=timing,differences=differences)


def usage(path):
    total=Counter()
    for f in path.glob('*/response.json'):
        r=read_json(f)
        if 'body' not in r:
            total['transport_failures']+=1
            continue
        body=json.loads(r['body'])
        q=read_json(f.parent/'request.json')
        if q.get('window') is None:
            continue
        total['calls']+=1
        for key,value in body.get('usageMetadata',{}).items():
            if key in ['promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount']:
                total[key]+=value
    return dict(total)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-live',action='store_true',required=True)
    p.add_argument('--analyze-only',action='store_true',help='Reuse saved transcript; never issue API requests')
    p.add_argument('--job',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    job=read_json(args.job/'job.json')
    assert job['status']=='COMPLETED'
    work=args.output.resolve()/'work'
    work.mkdir(parents=True,exist_ok=True)
    snapshots={'audio.wav':args.job/'work/processed-audio.wav',
               'asis.json':args.job/'work/transcript.json','asis.srt':args.job/'output/original.srt',
               'timeline-map.json':args.job/'work/timeline-map.json'}
    hashes={str(path):digest(path) for path in snapshots.values()}
    for name,source in snapshots.items():
        target=work/name
        if not target.exists():
            shutil.copy2(source,target)
        elif digest(target)!=hashes[str(source)]:
            raise ValueError('Snapshot differs; use fresh output directory')
    spans=read_json(work/'timeline-map.json')
    with wave.open(str(work/'audio.wav'),'rb') as audio:
        rate,frames=audio.getframerate(),audio.getnframes()
    duration=frames/rate
    manifest=[dict(index=i+1,processed_start=a/rate,processed_end=min(a+60*rate,frames)/rate,
                   original_pieces=project(a/rate,min(a+60*rate,frames)/rate,spans))
              for i,a in enumerate(range(0,frames,60*rate))]
    write_json_atomic(work/'requests.json',manifest)
    provider=GeminiProvider()
    if provider.transcription_model!='gemini-3.8-flash':
        raise ValueError('Expected the same Gemini model as baseline')
    last=[None]
    def progress(value):
        state=(value['completed'],value['failed'],value['retrying'])
        if state!=last[0]:
            print(f"STT {value['completed']}/{value['total']} active={value['in_flight']} failed={value['failed']} retrying={value['retrying']}",flush=True)
            last[0]=state
    t=time.perf_counter()
    if args.analyze_only:
        packed=[TranscriptSegment.from_dict(s) for s in read_json(work/'packed-clock.json')]
        elapsed=read_json(work/'report.json')['elapsed_seconds']
    else:
        with capture_calls(args.output.resolve(),work/'llm'):
            # Identity spans allow the existing scheduler to pack across original gaps.
            packed=provider.transcribe(work/'audio.wav',job['options']['source_language'],lambda:None,
                                       work=work,progress=progress)
        elapsed=time.perf_counter()-t
    write_json_atomic(work/'packed-clock.json',[s.to_dict() for s in packed])
    mapped,crossings=[],[]
    for i,s in enumerate(packed):
        pieces=project(s.start,s.end,spans)
        if abs(sum(b-a for a,b in pieces)-(s.end-s.start))>1e-6:
            raise ValueError('Incomplete timestamp mapping')
        if len(pieces)>1:
            crossings.append(dict(index=i+1,processed_start=s.start,processed_end=s.end,
                                  original_pieces=pieces,text=s.text))
        # Repeated text on split spans is explicitly flagged, never silently bridged.
        mapped.extend(s.with_times(a,b) for a,b in pieces)
    before=[TranscriptSegment.from_dict(x) for x in read_json(work/'asis.json')]
    write_json_atomic(work/'mapped-raw.json',[s.to_dict() for s in mapped])
    mapped=filter_transcript_segments(mapped,job['options']['audio_filter'])
    write_json_atomic(work/'mapped.json',[s.to_dict() for s in mapped])
    write_json_atomic(work/'cross-boundary.json',crossings)
    cues=segment_subtitles(mapped,line_width=24 if job['options']['source_language'] in {'auto','ko','ja','zh'} else 42)
    (work/'packed-mapped.srt').write_text(segments_to_srt(cues),encoding='utf-8')
    comparison=compare(before,mapped)
    late=compare([s for s in before if s.start>=1200],[s for s in mapped if s.start>=1200])
    write_json_atomic(work/'differences.json',comparison)
    def compact(value):
        timing=value['matched_timing']
        return {**{k:v for k,v in value.items() if k not in ['matched_timing','differences']},
                'mean_absolute_start_delta':sum(abs(x['start_delta']) for x in timing)/len(timing) if timing else None,
                'mean_absolute_end_delta':sum(abs(x['end_delta']) for x in timing)/len(timing) if timing else None}
    report=dict(model=provider.transcription_model,audio_seconds=duration,
                asis_requests=sum(math.ceil((s['processed_end']-s['processed_start'])/60) for s in spans),
                packed_requests=len(manifest),requests_crossing_original_spans=sum(len(x['original_pieces'])>1 for x in manifest),
                cross_boundary_sentences=len(crossings),packed_sentences=len(packed),
                elapsed_seconds=elapsed,comparison=compact(comparison),after_20_minutes=compact(late),
                asis_usage=usage(args.job/'work/llm'),packed_usage=usage(work/'llm'),
                originals_unchanged=all(digest(Path(path))==h for path,h in hashes.items()),input_sha256=hashes,
                note='No VAD, no translation. Existing silence filtering unchanged. Similarity is not semantic accuracy.')
    write_json_atomic(work/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='input_sha256'},ensure_ascii=False))


if __name__=='__main__':
    main()
