import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Captions, Upload, FileVideo, Download, Trash2, X, Pause, Play, RefreshCw, Check, Clock, Pencil, Plus, FolderOpen, ListVideo, CircleCheck, History, SlidersHorizontal} from 'lucide-react';
import {base, Job, request, resumeUpload, uploadRequest} from './api';
import {abortable} from './abortable';
import {UploadQueue, UploadItem} from './upload-queue';
import './styles.css';
import {StageProgress} from './StageProgress';
import {SubtitleEditor} from './SubtitleEditor';
import {AccountMenu, AuthGate} from './AuthGate';
import './workspace.css';

const labels: Record<string, string> = {UPLOADING:'업로드 중', QUEUED:'처리 대기', ANALYZING:'영상 분석', EXTRACTING_AUDIO:'오디오 추출', PREPROCESSING_AUDIO:'음성 전처리', TRANSCRIBING:'음성 전사', FILTERING_TRANSCRIPT:'전사 정리', TRANSLATING:'번역', GENERATING_SUBTITLE:'자막 생성', ENCODING:'영상 출력', VALIDATING:'결과 검증', CLEANING:'파일 정리', COMPLETED:'완료', FAILED:'실패', CANCELLED:'취소됨'};
const terminal = (job: Job) => ['COMPLETED','FAILED','CANCELLED'].includes(job.status);
labels.AWAITING_REVIEW = '자막 검토 대기';
labels.UPLOADING = '업로드 미완료';
labels.READY = '업로드 완료';
const bytes = (n: number) => n >= 1024**3 ? `${(n/1024**3).toFixed(2)} GB` : `${(n/1024**2).toFixed(1)} MB`;
const languageName = (code: string) => ({ko:'한국어',en:'영어',ja:'일본어',zh:'중국어',es:'스페인어'}[code] || code);

function App() {
  const [jobs,setJobs] = useState<Job[]>([]), [error,setError] = useState(''), [online,setOnline] = useState(false);
  const [file,setFile] = useState<File|null>(null), [preview,setPreview] = useState('');
  const [source,setSource] = useState('auto'), [target,setTarget] = useState('ko'), [quality,setQuality] = useState('balanced');
  const [codec,setCodec] = useState('hevc');
  const [subtitleMode,setSubtitleMode] = useState('burn');
  const [resolution,setResolution] = useState('original');
  const [audioFilter,setAudioFilter] = useState('silence3');
  const [vadMode,setVadMode] = useState<'off'|'nvidia'>('off');
  const [videoDescription,setVideoDescription] = useState('');
  const [reviewSubtitles,setReviewSubtitles] = useState(false), [reviewJob,setReviewJob] = useState<Job|null>(null);
  const [filter,setFilter] = useState('all');
  const [confirm,setConfirm] = useState<Job|null>(null), [pending,setPending] = useState('');
  const [resumeId,setResumeId] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const uploadDialog = useRef<HTMLDialogElement>(null);
  const [detailId,setDetailId] = useState('');
  const detailDialog = useRef<HTMLDialogElement>(null);
  const [uploads,setUploads] = useState<UploadItem[]>([]);
  const uploadOrder = useRef(new Map<string,string>());
  const [queue] = useState(() => new UploadQueue(async (item, signal, changed) => {
    if (!item.jobId) {
      const created = await uploadRequest<{job_id: string}>('/uploads', {method:'POST', signal,
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({...item.payload,request_id:item.key})});
      item.jobId = created.job_id; changed();
    }
    await abortable(inner => resumeUpload(item.file!, item.jobId!, inner, n => {
      if (!signal.aborted) {item.progress = n; changed();}
    }), signal, 0);
  }));
  async function refresh() {
    try { const data = await request<{jobs: Job[]}>('/jobs'); setJobs(data.jobs); setOnline(true);
      queue.forgetCompleted(data.jobs.filter(job => job.status !== 'UPLOADING').map(job => job.job_id)); }
    catch { setOnline(false); }
  }
  useEffect(() => {
    queue.onChange = () => {
      for (const item of queue.items) if (item.jobId) uploadOrder.current.set(item.jobId,item.createdAt);
      setUploads(queue.items.map(item => ({...item})));
    };
    queue.activate();
    void refresh(); const timer = setInterval(refresh, 2500);
    return () => {clearInterval(timer); queue.dispose();};
  }, [queue]);
  useEffect(() => {
    const pause = () => queue.suspend(true);
    const visibility = () => {
      if (document.visibilityState === 'hidden') pause();
      else {queue.suspend(false); void refresh();}
    };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', pause);
    const leaving = (event: BeforeUnloadEvent) => {
      if (queue.items.some(item => item.file)) {event.preventDefault(); event.returnValue = '';}
    };
    window.addEventListener('beforeunload', leaving);
    return () => {
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', pause);
      window.removeEventListener('beforeunload', leaving);
    };
  }, [queue]);
  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const url = URL.createObjectURL(file); setPreview(url); return () => URL.revokeObjectURL(url);
  }, [file]);
  function choose(next: File | undefined) {
    if (!next) return;
    if (resumeId) {
      const job = jobs.find(item => item.job_id === resumeId);
      if (!job || next.name !== job.original_filename || next.size !== job.expected_size) {setError('원래 업로드한 파일과 이름·크기가 일치해야 합니다.'); return;}
    }
    setFile(next); setError('');
  }
  function start() {
    if (!file) return;
    queue.add(file, {filename:file.name,size:file.size,source_language:source,target_language:target,
      quality_profile:quality,video_codec:codec,subtitle_mode:subtitleMode,resolution,
      audio_filter:audioFilter,vad_mode:vadMode,review_subtitles:reviewSubtitles,video_description:videoDescription.trim()}, resumeId || undefined);
    setFile(null); setResumeId(''); setVideoDescription(''); setFilter('all'); setError('');
    if (input.current) input.current.value = '';
    uploadDialog.current?.close();
  }
  async function action(job: Job, operation: 'cancel'|'delete') {
    setPending(job.job_id);setError('');
    try {
      await request(`/jobs/${job.job_id}${operation === 'cancel' ? '/cancel' : ''}`, {method:operation === 'cancel' ? 'POST' : 'DELETE'});
      const item = uploads.find(item => item.jobId === job.job_id);
      if (item) queue.remove(item.key);
      if (job.job_id === resumeId) {setResumeId('');setFile(null);}
      setConfirm(null);await refresh();
    } catch(e) {setError(e instanceof Error ? e.message : '요청에 실패했습니다.');}
    finally {setPending('');}
  }
  async function retry(job: Job, operation: 'retry'|'start' = 'retry') {
    setPending(job.job_id); setError('');
    try {
      await request(`/jobs/${job.job_id}/${operation}`, {method:'POST'});
      await refresh();
    } catch (e) {setError(e instanceof Error ? e.message : '재시도에 실패했습니다.');}
    finally {setPending('');}
  }
  const localUploads = uploads;
  const visible = jobs.filter(job => !localUploads.some(item => item.jobId === job.job_id)).filter(job => filter === 'all' || (filter === 'active' ? !terminal(job)
    : filter === 'history' ? terminal(job) : job.status === 'COMPLETED'));
  const rows: {key: string; createdAt: string; upload?: UploadItem; job?: Job}[] = [
    ...(['all','active'].includes(filter) ? localUploads.map(upload => ({key:upload.key,createdAt:upload.createdAt,upload})) : []),
    ...visible.map(job => ({key:job.job_id,createdAt:uploadOrder.current.get(job.job_id)||job.created_at,job})),
  ].sort((a,b) => b.createdAt.localeCompare(a.createdAt) || b.key.localeCompare(a.key));
  const total = jobs.length+localUploads.filter(item=>!jobs.some(job=>job.job_id===item.jobId)).length;
  const active = jobs.filter(job=>!terminal(job)).length+localUploads.filter(item=>!jobs.some(job=>job.job_id===item.jobId)).length;
  const detailJob = jobs.find(job=>job.job_id===detailId);
  const filters = [['all','전체 작업',FolderOpen],['active','진행 중',ListVideo],['done','완료',CircleCheck],['history','작업 이력',History]] as const;
  return <>
    <header><a className="brand" href="/"><Captions size={27}/><span>영상 자막 작업실</span></a><div className="header-session"><span className={'connection '+(online?'online':'')}>{online?'서버 연결됨':'서버 연결 끊김'}</span><AccountMenu/></div></header>
    <aside className="workspace-nav" aria-label="작업 탐색">
      <button className="primary" onClick={()=>uploadDialog.current?.showModal()}><Plus size={20}/>영상 추가</button>
      <p className="nav-heading">내 작업실</p>
      <nav>{filters.map(([value,label,Icon])=><button key={value} aria-current={filter===value?'page':undefined} onClick={()=>setFilter(value)}><Icon size={20}/><span>{label}</span>{value==='all'&&<small>{total}</small>}{value==='active'&&<small>{active}</small>}</button>)}</nav>
      <div className="nav-footer"><Captions size={18}/><span>영상 자막 작업실</span></div>
    </aside>
    <main>
      <div className="page-title"><div><p className="eyebrow">내 작업실</p><h1>{filters.find(([value])=>value===filter)?.[1]}</h1></div><span className="active-count"><span className="activity-dot"/>{active}개 진행 중</span></div>
      {error && <div className="alert" role="alert"><span>{error}</span><button className="icon" title="알림 닫기" onClick={()=>setError('')}><X size={18}/></button></div>}
      <dialog ref={uploadDialog} className="upload-dialog" aria-labelledby="upload-title">
      <div className="dialog-heading"><h2 id="upload-title">{resumeId?'업로드 재개':'영상 추가'}</h2><button className="icon" title="업로드 창 닫기" onClick={()=>uploadDialog.current?.close()}><X size={22}/></button></div>
      {error&&<div className="alert" role="alert">{error}</div>}
      <section className="upload-area" aria-label="새 영상 작업">
        <div className="file-area" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();choose(e.dataTransfer.files[0]);}}>
          <input ref={input} type="file" accept="video/*,.mkv,.mov,.avi" aria-label="영상 파일 선택" onChange={e=>{choose(e.target.files?.[0]);e.target.value='';}}/>
          {file ? <><video src={preview} controls preload="metadata"/><div className="file-name"><FileVideo size={20}/><strong>{file.name}</strong><span>{bytes(file.size)}</span></div></> : <button className="file-select" onClick={()=>input.current?.click()}><Upload size={30}/><strong>{resumeId?'원본 영상 다시 선택':'영상 선택'}</strong><span>MP4 · MOV · MKV</span></button>}
          {file && <button className="text-button" onClick={()=>input.current?.click()}>파일 변경</button>}
        </div>
        <div className="options"><h2>{resumeId?'업로드 재개':'새 작업'}</h2><div className="language-grid"><label>원본 언어<select value={source} disabled={!!resumeId} onChange={e=>setSource(e.target.value)}><option value="auto">자동 감지</option>{['en','ja','ko','zh','es'].map(code=><option key={code} value={code}>{languageName(code)}</option>)}</select></label><label>번역 언어<select value={target} disabled={!!resumeId} onChange={e=>setTarget(e.target.value)}>{['ko','en','ja','zh','es'].map(code=><option key={code} value={code}>{languageName(code)}</option>)}</select></label></div>
          <label>영상 설명 (선택)<textarea value={videoDescription} maxLength={2000} rows={3} disabled={!!resumeId} onChange={e=>setVideoDescription(e.target.value)}/></label>
          <details className="advanced-options"><summary><SlidersHorizontal size={16}/>상세 설정</summary>
          <label>영상 품질<select value={quality} disabled={!!resumeId} onChange={e=>setQuality(e.target.value)}><option value="balanced">균형</option><option value="high">고화질</option><option value="compact">용량 우선</option></select></label>
          <label>영상 코덱<select value={codec} disabled={!!resumeId} onChange={e=>setCodec(e.target.value)}><option value="hevc">H.265 (HEVC)</option><option value="h264">H.264</option></select></label>
          <label>자막 방식<select value={subtitleMode} disabled={!!resumeId} onChange={e=>setSubtitleMode(e.target.value)}><option value="burn">번인</option><option value="soft">소프트 자막</option></select></label>
          <label>해상도<select value={resolution} disabled={!!resumeId} onChange={e=>setResolution(e.target.value)}><option value="original">원본 유지</option><option value="1080p">최대 1080p</option><option value="720p">최대 720p</option></select></label>
          <label>오디오 필터<select value={audioFilter} disabled={!!resumeId} onChange={e=>setAudioFilter(e.target.value)}><option value="silence3">3초 이상 무음</option><option value="off">끄기</option><option value="conservative">보수적 · 10초 이상 무음</option><option value="strong">강하게 · 5초 이상 무음</option></select></label>
          <div className="output-format"><span>결과 파일</span><strong>MP4 + SRT</strong></div>
          <label className="review-toggle"><input type="checkbox" checked={vadMode==='nvidia'} disabled={!!resumeId} onChange={e=>setVadMode(e.target.checked?'nvidia':'off')}/>NVIDIA 음성 감지 (대사 누락 가능)</label>
          <label className="review-toggle"><input type="checkbox" checked={reviewSubtitles} disabled={!!resumeId} onChange={e=>setReviewSubtitles(e.target.checked)}/>자막 검토 후 출력</label>
          </details>
          <button className="primary" disabled={!file||!online} onClick={start}><Upload size={18}/>{resumeId?'업로드 재개':'업로드'}</button>
          {resumeId&&<button className="text-button" onClick={()=>{setResumeId('');setFile(null);setVideoDescription('');}}>새 파일로 돌아가기</button>}
        </div>
      </section>
      </dialog>
      <section className="jobs" aria-label="작업 목록"><div className="section-title"><h2>작업 목록 <span>{jobs.length+localUploads.filter(item=>!jobs.some(job=>job.job_id===item.jobId)).length}</span></h2><button className="icon" title="목록 새로고침" onClick={()=>void refresh()}><RefreshCw size={18}/></button></div>
        <div className="tabs" role="tablist" aria-label="작업 필터">{[['all','전체'],['active','진행 중'],['done','완료'],['history','작업 이력']].map(([value,label])=><button role="tab" aria-selected={filter===value} key={value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
        {!rows.length&&<div className="empty"><FileVideo size={36}/><h3>{online?'아직 작업이 없습니다':'서버에 연결할 수 없습니다'}</h3></div>}
        <div className="job-list">{rows.map(entry=>{const item=entry.upload;const job=entry.job;return item?<article className="job-row" key={entry.key}>
          <div className="video-icon"><FileVideo/></div><div className="job-description"><h3>{item.name}</h3>
            <p>{bytes(item.progress)} / {bytes(item.size)} · {Math.round(item.progress/item.size*100)}%</p>
            <progress aria-label={`${item.name} 업로드 진행률`} max={item.size} value={item.progress}/>
            {item.error&&<p className="job-error">{item.error}</p>}
          </div><span className="status">{{waiting:'업로드 대기',uploading:'업로드 중',paused:'일시정지',error:'업로드 오류',done:'업로드 완료'}[item.state]}</span>
          <div className="actions">{['waiting','uploading'].includes(item.state)?
            <button className="icon" title="업로드 일시정지" onClick={()=>queue.pause(item.key)}><Pause size={18}/></button>:
            item.state!=='done'&&<button className="icon" title="업로드 재개" onClick={()=>queue.resume(item.key)}><Play size={18}/></button>}
            {item.jobId&&item.state!=='done'&&<button className="icon danger" title="작업 취소" disabled={!!pending} onClick={()=>{queue.pause(item.key);void action({job_id:item.jobId!} as Job,'cancel');}}><X size={18}/></button>}
            {!item.started&&<button className="icon danger" title="대기 업로드 제거" onClick={()=>queue.remove(item.key)}><X size={18}/></button>}
          </div>
        </article>:job?<article key={entry.key} className="job-row"><div className={'video-icon '+(job.status==='COMPLETED'?'finished':'')}>{job.status==='COMPLETED'?<Check/>:<FileVideo/>}</div><div className="job-description"><h3><button className="job-title" onClick={()=>{setDetailId(job.job_id);detailDialog.current?.showModal();}}>{job.original_filename}</button></h3><p>{bytes(job.expected_size)} <span>·</span> {languageName(job.target_language)} <span>·</span> {new Date(job.created_at).toLocaleString('ko-KR')}</p>{job.error&&<p className="job-error">{job.error}</p>}{job.status==='UPLOADING'&&<progress aria-label={`${job.original_filename} 업로드 진행률`} max={job.expected_size} value={job.uploaded_bytes}/>}</div><span className={'status '+job.status.toLowerCase()}><Clock size={14}/>{job.metadata.cancel_requested&&!terminal(job)?'취소 중':labels[job.status]||job.status}</span><div className="actions">
          {job.status==='COMPLETED'&&job.metadata.results_expired_at&&<span className="status" title={new Date(job.metadata.results_expired_at).toLocaleString('ko-KR')}>결과 만료</span>}
          {job.metadata.upload_expired_at&&<span className="status">업로드 만료</span>}
          {job.status==='COMPLETED'&&!job.metadata.results_expired_at&&<><a className="download" href={`${base}/jobs/${job.job_id}/results/final.mp4`}><Download size={16}/>MP4</a><a className="download" href={`${base}/jobs/${job.job_id}/results/translated.srt`}><Download size={16}/>번역 SRT</a>{job.metadata.result_files?.includes('original.srt')&&<a className="download" href={`${base}/jobs/${job.job_id}/results/original.srt`}><Download size={16}/>원문 SRT</a>}</>}
          {job.status==='COMPLETED'&&!job.metadata.results_expired_at&&job.metadata.result_files?.includes('translated.smi')&&<a className="download" href={`${base}/jobs/${job.job_id}/results/translated.smi`}><Download size={16}/>SMI</a>}
          {job.status==='COMPLETED'&&!job.metadata.results_expired_at&&!!job.additional_languages?.length&&<details className="extra-downloads"><summary role="button" aria-label="추가 자막"><Download size={16}/>추가 자막</summary><div>{job.additional_languages.map(language=><React.Fragment key={language}>{['srt','smi'].map(format=>{const filename=`translated.${language}.${format}`;return job.metadata.result_files?.includes(filename)&&<a key={format} className="download" href={`${base}/jobs/${job.job_id}/results/${filename}`}><Download size={16}/>{languageName(language)} {format.toUpperCase()}</a>;})}</React.Fragment>)}</div></details>}
          {job.status==='AWAITING_REVIEW'&&<button className="icon" title="자막 수정" onClick={()=>setReviewJob(job)}><Pencil size={18}/></button>}
          {job.status==='READY'&&<button className="icon" title="처리 시작" disabled={!!pending} onClick={()=>void retry(job,'start')}><Play size={18}/></button>}
          {job.status==='UPLOADING'&&<button className="icon" title="업로드 재개" onClick={()=>{setResumeId(job.job_id);setFile(null);setSource(job.source_language);setTarget(job.target_language);setQuality(job.quality_profile);setCodec(job.video_codec||'hevc');setSubtitleMode(job.subtitle_mode||'burn');setResolution(job.resolution||'original');setAudioFilter(job.audio_filter||'conservative');setVadMode(job.vad_mode||'off');setReviewSubtitles(job.review_subtitles||false);setVideoDescription(job.video_description||'');uploadDialog.current?.showModal();}}><Play size={18}/></button>}
          {['FAILED','CANCELLED'].includes(job.status)&&<button className="icon" title="중단된 작업 재시도" disabled={pending===job.job_id} onClick={()=>void retry(job)}><RefreshCw size={18}/></button>}
          {terminal(job)?<button className="icon danger" title="작업 삭제" disabled={pending===job.job_id} onClick={()=>setConfirm(job)}><Trash2 size={18}/></button>:<button className="icon danger" title="작업 취소" disabled={pending===job.job_id||!!job.metadata.cancel_requested} onClick={()=>void action(job,'cancel')}><X size={18}/></button>}
        </div><StageProgress job={job}/></article>:null;})}</div>
      </section>
    </main>
    <button className="primary mobile-add" onClick={()=>uploadDialog.current?.showModal()}><Plus size={20}/>영상 추가</button>
    <dialog ref={detailDialog} className="detail-dialog" aria-labelledby="detail-title">
      <div className="dialog-heading"><h2 id="detail-title">작업 상세</h2><button className="icon" title="작업 상세 닫기" onClick={()=>detailDialog.current?.close()}><X size={22}/></button></div>
      {detailJob&&<><div className="detail-file"><div className="video-icon"><FileVideo size={28}/></div><div><h3>{detailJob.original_filename}</h3><p>{bytes(detailJob.expected_size)} · {languageName(detailJob.target_language)}</p></div></div>
        <div className={'status '+detailJob.status.toLowerCase()}>{labels[detailJob.status]||detailJob.status}</div>
        <StageProgress job={detailJob}/>
        {detailJob.error&&<p className="alert">{detailJob.error}</p>}
        <dl className="job-facts"><div><dt>등록 일시</dt><dd>{new Date(detailJob.created_at).toLocaleString('ko-KR')}</dd></div><div><dt>원본 언어</dt><dd>{detailJob.source_language==='auto'?'자동 감지':languageName(detailJob.source_language)}</dd></div><div><dt>영상 설명</dt><dd>{detailJob.video_description||'없음'}</dd></div><div><dt>음성 감지</dt><dd>{detailJob.vad_mode==='nvidia'?'NVIDIA VAD':'사용 안 함'}</dd></div></dl>
      </>}
    </dialog>
    {reviewJob&&<SubtitleEditor job={reviewJob} onClose={()=>setReviewJob(null)} onRendered={()=>void refresh()}/>}
    {confirm&&<div className="overlay"><div role="dialog" aria-modal="true" aria-labelledby="delete-title" className="dialog"><h2 id="delete-title">작업을 삭제할까요?</h2><p>{confirm.original_filename}</p><p>작업 기록과 남아 있는 영상·자막 파일이 함께 삭제됩니다.</p><div><button autoFocus onClick={()=>setConfirm(null)}>돌아가기</button><button className="destructive" disabled={!!pending} onClick={()=>void action(confirm,'delete')}>삭제</button></div></div></div>}
  </>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><AuthGate><App/></AuthGate></React.StrictMode>);
