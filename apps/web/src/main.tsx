import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Captions, Upload, FileVideo, Download, Trash2, X, Pause, Play, RefreshCw, Check, Clock, Pencil} from 'lucide-react';
import {base, Job, request, resumeUpload} from './api';
import './styles.css';
import {StageProgress} from './StageProgress';
import {SubtitleEditor} from './SubtitleEditor';

const labels: Record<string, string> = {UPLOADING:'업로드 중', QUEUED:'처리 대기', ANALYZING:'영상 분석', EXTRACTING_AUDIO:'오디오 추출', PREPROCESSING_AUDIO:'음성 전처리', TRANSCRIBING:'음성 전사', FILTERING_TRANSCRIPT:'전사 정리', TRANSLATING:'번역', GENERATING_SUBTITLE:'자막 생성', ENCODING:'영상 출력', VALIDATING:'결과 검증', CLEANING:'파일 정리', COMPLETED:'완료', FAILED:'실패', CANCELLED:'취소됨'};
const terminal = (job: Job) => ['COMPLETED','FAILED','CANCELLED'].includes(job.status);
labels.AWAITING_REVIEW = '자막 검토 대기';
const bytes = (n: number) => n >= 1024**3 ? `${(n/1024**3).toFixed(2)} GB` : `${(n/1024**2).toFixed(1)} MB`;
const languageName = (code: string) => ({ko:'한국어',en:'영어',ja:'일본어',zh:'중국어',es:'스페인어'}[code] || code);

function App() {
  const [jobs,setJobs] = useState<Job[]>([]), [error,setError] = useState(''), [online,setOnline] = useState(false);
  const [file,setFile] = useState<File|null>(null), [preview,setPreview] = useState('');
  const [source,setSource] = useState('auto'), [target,setTarget] = useState('ko'), [quality,setQuality] = useState('balanced');
  const [codec,setCodec] = useState('hevc');
  const [subtitleMode,setSubtitleMode] = useState('burn');
  const [resolution,setResolution] = useState('original');
  const [additionalLanguages,setAdditionalLanguages] = useState<string[]>([]);
  const [audioFilter,setAudioFilter] = useState('conservative');
  const [reviewSubtitles,setReviewSubtitles] = useState(false), [reviewJob,setReviewJob] = useState<Job|null>(null);
  const [filter,setFilter] = useState('all'), [uploadId,setUploadId] = useState(''), [busy,setBusy] = useState(false);
  const [progress,setProgress] = useState(0), [confirm,setConfirm] = useState<Job|null>(null), [pending,setPending] = useState('');
  const [resumeId,setResumeId] = useState('');
  const creationRequest = useRef<{signature: string; key: string} | null>(null);
  const controller = useRef<AbortController|null>(null), active = useRef(false), input = useRef<HTMLInputElement>(null);
  async function refresh() {
    try { const data = await request<{jobs: Job[]}>('/jobs'); setJobs(data.jobs); setOnline(true); }
    catch { setOnline(false); }
  }
  useEffect(() => { void refresh(); const timer = setInterval(refresh, 2500); return () => {clearInterval(timer); controller.current?.abort();}; }, []);
  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const url = URL.createObjectURL(file); setPreview(url); return () => URL.revokeObjectURL(url);
  }, [file]);
  function choose(next: File | undefined) {
    if (!next || active.current) return;
    creationRequest.current = null;
    if (resumeId) {
      const job = jobs.find(item => item.job_id === resumeId);
      if (!job || next.name !== job.original_filename || next.size !== job.expected_size) {setError('원래 업로드한 파일과 이름·크기가 일치해야 합니다.'); return;}
      setUploadId(resumeId);
    } else { setUploadId(''); }
    setFile(next); setProgress(0); setError('');
  }
  async function start() {
    if (!file || active.current) return;
    active.current = true; setBusy(true); setError('');
    const abort = new AbortController(); controller.current = abort;
    try {
      let id = uploadId;
      if (!id) {
        const payload = {filename:file.name,size:file.size,source_language:source,target_language:target,quality_profile:quality,video_codec:codec,subtitle_mode:subtitleMode,resolution,additional_languages:additionalLanguages,audio_filter:audioFilter,review_subtitles:reviewSubtitles};
        const signature = JSON.stringify(payload);
        if (creationRequest.current?.signature !== signature) {
          creationRequest.current = {signature, key:crypto.randomUUID()};
        }
        const created = await request<{job_id: string}>('/uploads', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...payload,request_id:creationRequest.current.key})});
        id = created.job_id; setUploadId(id);
      }
      await resumeUpload(file,id,abort.signal,setProgress);
      if (!abort.signal.aborted) {setFile(null);setUploadId('');setResumeId('');}
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : '업로드에 실패했습니다.'); }
    finally {active.current=false;setBusy(false);controller.current=null;}
  }
  async function action(job: Job, operation: 'cancel'|'delete') {
    setPending(job.job_id);setError('');
    try {
      await request(`/jobs/${job.job_id}${operation === 'cancel' ? '/cancel' : ''}`, {method:operation === 'cancel' ? 'POST' : 'DELETE'});
      if (job.job_id === uploadId) {setUploadId('');setResumeId('');setFile(null);}
      setConfirm(null);await refresh();
    } catch(e) {setError(e instanceof Error ? e.message : '요청에 실패했습니다.');}
    finally {setPending('');}
  }
  const visible = jobs.filter(job => filter === 'all' || (filter === 'active' ? !terminal(job)
    : filter === 'history' ? terminal(job) : job.status === 'COMPLETED'));
  return <>
    <header><a className="brand" href="/"><Captions size={27}/><span>영상 자막 작업실</span></a><span className={'connection '+(online?'online':'')}>{online?'서버 연결됨':'서버 연결 끊김'}</span></header>
    <main>
      <div className="page-title"><div><p className="eyebrow">WORKSPACE</p><h1>영상 번역</h1></div><span>{jobs.filter(job=>!terminal(job)).length}개 진행 중</span></div>
      {error && <div className="alert" role="alert"><span>{error}</span><button className="icon" title="알림 닫기" onClick={()=>setError('')}><X size={18}/></button></div>}
      <section className="upload-area" aria-label="새 영상 작업">
        <div className="file-area" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();choose(e.dataTransfer.files[0]);}}>
          <input ref={input} type="file" accept="video/*,.mkv,.mov,.avi" aria-label="영상 파일 선택" disabled={busy} onChange={e=>{choose(e.target.files?.[0]);e.target.value='';}}/>
          {file ? <><video src={preview} controls preload="metadata"/><div className="file-name"><FileVideo size={20}/><strong>{file.name}</strong><span>{bytes(file.size)}</span></div></> : <button className="file-select" disabled={busy} onClick={()=>input.current?.click()}><Upload size={30}/><strong>{resumeId?'원본 영상 다시 선택':'영상 선택'}</strong><span>MP4 · MOV · MKV</span></button>}
          {file && <button className="text-button" disabled={busy} onClick={()=>input.current?.click()}>파일 변경</button>}
        </div>
        <div className="options"><h2>{resumeId?'업로드 재개':'새 작업'}</h2><div className="language-grid"><label>원본 언어<select value={source} disabled={busy||!!uploadId} onChange={e=>setSource(e.target.value)}><option value="auto">자동 감지</option>{['en','ja','ko','zh','es'].map(code=><option key={code} value={code}>{languageName(code)}</option>)}</select></label><label>번역 언어<select value={target} disabled={busy||!!uploadId} onChange={e=>{setTarget(e.target.value);setAdditionalLanguages(items=>items.filter(code=>code!==e.target.value));}}>{['ko','en','ja','zh','es'].map(code=><option key={code} value={code}>{languageName(code)}</option>)}</select></label></div>
          <fieldset className="additional-languages" disabled={busy||!!uploadId}><legend>추가 번역 언어</legend>{['ko','en','ja','zh','es'].filter(code=>code!==target).map(code=><label key={code}><input type="checkbox" checked={additionalLanguages.includes(code)} onChange={e=>setAdditionalLanguages(items=>e.target.checked?[...items,code]:items.filter(item=>item!==code))}/>{languageName(code)}</label>)}</fieldset>
          <label>영상 품질<select value={quality} disabled={busy||!!uploadId} onChange={e=>setQuality(e.target.value)}><option value="balanced">균형</option><option value="high">고화질</option><option value="compact">용량 우선</option></select></label>
          <label>영상 코덱<select value={codec} disabled={busy||!!uploadId} onChange={e=>setCodec(e.target.value)}><option value="hevc">H.265 (HEVC)</option><option value="h264">H.264</option></select></label>
          <label>자막 방식<select value={subtitleMode} disabled={busy||!!uploadId} onChange={e=>setSubtitleMode(e.target.value)}><option value="burn">번인</option><option value="soft">소프트 자막</option></select></label>
          <label>해상도<select value={resolution} disabled={busy||!!uploadId} onChange={e=>setResolution(e.target.value)}><option value="original">원본 유지</option><option value="1080p">최대 1080p</option><option value="720p">최대 720p</option></select></label>
          <label>오디오 필터<select value={audioFilter} disabled={busy||!!uploadId} onChange={e=>setAudioFilter(e.target.value)}><option value="off">끄기</option><option value="conservative">보수적 · 10초 이상 무음</option><option value="strong">강하게 · 5초 이상 무음</option></select></label>
          <div className="output-format"><span>결과 파일</span><strong>MP4 + SRT</strong></div>
          <label className="review-toggle"><input type="checkbox" checked={reviewSubtitles} disabled={busy||!!uploadId} onChange={e=>setReviewSubtitles(e.target.checked)}/>자막 검토 후 출력</label>
          {busy ? <button className="primary" onClick={()=>controller.current?.abort()}><Pause size={18}/>업로드 일시정지</button> : <button className="primary" disabled={!file||!online} onClick={()=>void start()}>{uploadId?<Play size={18}/>:<Upload size={18}/>} {uploadId?'업로드 재개':'번역 시작'}</button>}
          {file && <div className="upload-progress"><progress value={progress} max={file.size}/><span>{bytes(progress)} / {bytes(file.size)}</span></div>}
        </div>
      </section>
      <section className="jobs" aria-label="작업 목록"><div className="section-title"><h2>작업 목록 <span>{jobs.length}</span></h2><button className="icon" title="목록 새로고침" onClick={()=>void refresh()}><RefreshCw size={18}/></button></div>
        <div className="tabs" role="tablist" aria-label="작업 필터">{[['all','전체'],['active','진행 중'],['done','완료'],['history','작업 이력']].map(([value,label])=><button role="tab" aria-selected={filter===value} key={value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
        {!visible.length ? <div className="empty"><FileVideo size={36}/><h3>{online?'아직 작업이 없습니다':'서버에 연결할 수 없습니다'}</h3></div> : <div className="job-list">{visible.map(job=><article key={job.job_id} className="job-row"><div className="video-icon">{job.status==='COMPLETED'?<Check/>:<FileVideo/>}</div><div className="job-description"><h3>{job.original_filename}</h3><p>{bytes(job.expected_size)} <span>·</span> {languageName(job.target_language)} <span>·</span> {new Date(job.created_at).toLocaleString('ko-KR')}</p>{job.error&&<p className="job-error">{job.error}</p>}{job.status==='UPLOADING'&&<progress aria-label={`${job.original_filename} 업로드 진행률`} max={job.expected_size} value={job.job_id===uploadId?Math.max(job.uploaded_bytes,progress):job.uploaded_bytes}/>}</div><span className={'status '+job.status.toLowerCase()}><Clock size={14}/>{job.metadata.cancel_requested&&!terminal(job)?'취소 중':labels[job.status]||job.status}</span><div className="actions">
          {job.status==='COMPLETED'&&job.metadata.results_expired_at&&<span className="status" title={new Date(job.metadata.results_expired_at).toLocaleString('ko-KR')}>결과 만료</span>}
          {job.metadata.upload_expired_at&&<span className="status">업로드 만료</span>}
          {job.status==='COMPLETED'&&!job.metadata.results_expired_at&&<><a className="download" href={`${base}/jobs/${job.job_id}/results/final.mp4`}><Download size={16}/>MP4</a><a className="download" href={`${base}/jobs/${job.job_id}/results/translated.srt`}><Download size={16}/>번역 SRT</a>{job.metadata.result_files?.includes('original.srt')&&<a className="download" href={`${base}/jobs/${job.job_id}/results/original.srt`}><Download size={16}/>원문 SRT</a>}</>}
          {job.status==='COMPLETED'&&!job.metadata.results_expired_at&&job.metadata.result_files?.includes('translated.smi')&&<a className="download" href={`${base}/jobs/${job.job_id}/results/translated.smi`}><Download size={16}/>SMI</a>}
          {job.status==='COMPLETED'&&!job.metadata.results_expired_at&&!!job.additional_languages?.length&&<details className="extra-downloads"><summary role="button" aria-label="추가 자막"><Download size={16}/>추가 자막</summary><div>{job.additional_languages.map(language=><React.Fragment key={language}>{['srt','smi'].map(format=>{const filename=`translated.${language}.${format}`;return job.metadata.result_files?.includes(filename)&&<a key={format} className="download" href={`${base}/jobs/${job.job_id}/results/${filename}`}><Download size={16}/>{languageName(language)} {format.toUpperCase()}</a>;})}</React.Fragment>)}</div></details>}
          {job.status==='AWAITING_REVIEW'&&<button className="icon" title="자막 수정" onClick={()=>setReviewJob(job)}><Pencil size={18}/></button>}
          {job.status==='UPLOADING'&&<button className="icon" disabled={busy} title="업로드 재개" onClick={()=>{setResumeId(job.job_id);setUploadId(job.job_id);setFile(null);setProgress(job.uploaded_bytes);setSource(job.source_language);setTarget(job.target_language);setQuality(job.quality_profile);setCodec(job.video_codec||'hevc');setSubtitleMode(job.subtitle_mode||'burn');setResolution(job.resolution||'original');setAdditionalLanguages(job.additional_languages||[]);setAudioFilter(job.audio_filter||'conservative');setReviewSubtitles(job.review_subtitles||false);}}><Play size={18}/></button>}
          {terminal(job)?<button className="icon danger" title="작업 삭제" disabled={pending===job.job_id} onClick={()=>setConfirm(job)}><Trash2 size={18}/></button>:<button className="icon danger" title="작업 취소" disabled={busy||pending===job.job_id||!!job.metadata.cancel_requested} onClick={()=>void action(job,'cancel')}><X size={18}/></button>}
        </div><StageProgress job={job}/></article>)}</div>}
      </section>
    </main>
    {reviewJob&&<SubtitleEditor job={reviewJob} onClose={()=>setReviewJob(null)} onRendered={()=>void refresh()}/>}
    {confirm&&<div className="overlay"><div role="dialog" aria-modal="true" aria-labelledby="delete-title" className="dialog"><h2 id="delete-title">작업을 삭제할까요?</h2><p>{confirm.original_filename}</p><p>작업 기록과 남아 있는 영상·자막 파일이 함께 삭제됩니다.</p><div><button autoFocus onClick={()=>setConfirm(null)}>돌아가기</button><button className="destructive" disabled={!!pending} onClick={()=>void action(confirm,'delete')}>삭제</button></div></div></div>}
  </>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
