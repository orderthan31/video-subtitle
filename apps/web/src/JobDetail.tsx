import {useEffect, useRef, useState} from 'react';
import {ArrowLeft, Download, FileText, Film, RefreshCw, Search, X} from 'lucide-react';
import {base, Job, request} from './api';
import {StageProgress} from './StageProgress';
import './job-detail.css';

type Cue = {start:number; end:number; text:string};
type Track = {available:boolean; partial:boolean; cues:Cue[]};
type Preview = {video_available:boolean; expired:boolean; tracks:Record<'original'|'translated',Track>};
const timestamp = (value:number) => {
  const seconds = Math.floor(value);
  return [Math.floor(seconds/3600), Math.floor(seconds/60)%60, seconds%60].map(n=>String(n).padStart(2,'0')).join(':');
};

export function JobDetail({id,labels}:{id:string;labels:Record<string,string>}) {
  const [job,setJob] = useState<Job|null>(null), [preview,setPreview] = useState<Preview|null>(null);
  const [error,setError] = useState(''), [videoError,setVideoError] = useState(false);
  const [tab,setTab] = useState<'original'|'translated'>('original'), [query,setQuery] = useState('');
  const [time,setTime] = useState(0), [reload,setReload] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(()=>{heading.current?.focus();},[]);
  useEffect(()=>{
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function refresh() {
      try {
        const [next,data] = await Promise.all([
          request<Job>(`/jobs/${id}`,{signal:controller.signal}),
          request<Preview>(`/jobs/${id}/preview`,{signal:controller.signal}),
        ]);
        if (!disposed) {setJob(next);setPreview(data);setError('');}
      } catch(e) {
        if (!disposed) setError(e instanceof Error?e.message:'작업을 불러오지 못했습니다.');
      } finally {
        if (!disposed) timer = setTimeout(refresh,5000);
      }
    }
    void refresh();
    return ()=>{disposed=true;controller.abort();clearTimeout(timer);};
  },[id,reload]);
  const track = preview?.tracks[tab];
  const cues = track?.cues.filter(cue=>cue.text.toLocaleLowerCase().includes(query.toLocaleLowerCase())) || [];
  function seek(cue:Cue) {
    if (!video.current || !preview?.video_available || videoError) return;
    video.current.currentTime = cue.start;
    setTime(cue.start);
  }
  return <main className="job-detail">
    <div className="detail-page-heading"><a href="#/" className="icon" title="작업 목록으로"><ArrowLeft size={22}/></a>
      <div><p className="eyebrow">작업 상세</p><h1 ref={heading} tabIndex={-1}>{job?.original_filename||'작업 불러오는 중'}</h1></div>
      <button className="icon" title="작업 새로고침" onClick={()=>setReload(n=>n+1)}><RefreshCw size={18}/></button>
    </div>
    {error&&<p role="alert" className="alert">{error}</p>}
    {job&&<div className="detail-stage"><span className={'status '+job.status.toLowerCase()}>{labels[job.status]||job.status}</span><StageProgress job={job}/></div>}
    {job?.error&&<p className="alert">{job.error}</p>}
    <div className="detail-panels">
      <section className="media-panel" aria-label="영상">
        <div className="panel-heading"><h2><Film size={18}/>영상</h2>{preview?.video_available&&<a className="download" href={`${base}/jobs/${id}/results/final.mp4`}><Download size={16}/>MP4</a>}</div>
        <div className="player-surface">
          {preview?.video_available?<video ref={video} controls playsInline preload="metadata" src={`${base}/jobs/${id}/stream`}
            onTimeUpdate={e=>setTime(e.currentTarget.currentTime)} onError={()=>setVideoError(true)} onLoadedMetadata={()=>setVideoError(false)}/>:
            <div className="media-empty"><Film size={36}/><p>{preview?.expired?'영상 보관 기간이 만료되었습니다.':job?.status==='COMPLETED'?'영상 파일이 없습니다.':'영상 처리 완료 후 재생할 수 있습니다.'}</p></div>}
        </div>
        {videoError&&<p className="alert" role="alert">영상을 재생할 수 없습니다. 브라우저의 코덱 지원이나 연결 상태를 확인하거나 MP4를 다운로드해 주세요.</p>}
        {job&&<dl className="media-info"><div><dt>등록 일시</dt><dd>{new Date(job.created_at).toLocaleString('ko-KR')}</dd></div><div><dt>영상 설명</dt><dd>{job.video_description||'없음'}</dd></div></dl>}
      </section>
      <section className="text-panel" aria-label="전사록과 번역 자막">
        <div className="tabs" role="tablist" aria-label="자막 종류">{([['original','전사록'],['translated','번역 자막']] as const).map(([value,label])=><button id={`tab-${value}`} key={value} role="tab" aria-selected={tab===value} aria-controls="cue-panel" tabIndex={tab===value?0:-1} onClick={()=>setTab(value)} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'original':e.key==='End'?'translated':tab==='original'?'translated':'original';setTab(next);document.getElementById(`tab-${next}`)?.focus();}}}>{label}</button>)}</div>
        <div className="cue-toolbar"><label className="cue-search"><Search size={16}/><input aria-label="자막 검색" value={query} onChange={e=>setQuery(e.target.value)} placeholder="검색"/>{query&&<button className="icon" title="검색 지우기" onClick={()=>setQuery('')}><X size={16}/></button>}</label><span>{cues.length}개{track?.partial?' · 일부 결과':''}</span></div>
        <div id="cue-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="cue-panel" tabIndex={0}>
          {track?.available?(cues.length?cues.map((cue,index)=><button className={'preview-cue '+(time>=cue.start&&time<cue.end?'current':'')} key={`${cue.start}-${index}`} disabled={!preview?.video_available||videoError} onClick={()=>seek(cue)} aria-label={`${timestamp(cue.start)} ${cue.text}`}><time>{timestamp(cue.start)}</time><span>{cue.text}</span></button>):<div className="text-empty"><FileText size={28}/><p>{query?'검색 결과가 없습니다.':'저장된 대사가 없습니다.'}</p></div>):<div className="text-empty"><FileText size={28}/><p>{preview?.expired?'자막 보관 기간이 만료되었습니다.':tab==='original'?'전사 결과를 기다리고 있습니다.':'번역 결과를 기다리고 있습니다.'}</p></div>}
        </div>
        {job?.status==='COMPLETED'&&!preview?.expired&&<div className="text-download"><a className="download" href={`${base}/jobs/${id}/results/${tab==='original'?'original':'translated'}.srt`}><Download size={16}/>{tab==='original'?'원문 SRT':'번역 SRT'}</a></div>}
      </section>
    </div>
  </main>;
}
