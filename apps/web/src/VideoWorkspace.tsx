import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,Download,FileVideo,FolderOpen,ListVideo,Menu,Pause,Play,Plus,Search,Trash2,Upload,X} from 'lucide-react';
import {base,Job,request,resumeUpload,uploadRequest} from './api';
import {AccountMenu} from './AuthGate';
import {JobDetail} from './JobDetail';
import {StageProgress} from './StageProgress';
import {SubtitleEditor} from './SubtitleEditor';
import {UploadItem,UploadQueue} from './upload-queue';
import {WorkflowDialog} from './WorkflowDialog';
import {durationLabel,jsonPost,sizeLabel,statusLabels,SubtitleInput,templates,VideoAsset,VideoDetail,VideoUpload} from './video-api';
import './video-workspace.css';
import './workspace-polish.css';

export function VideoWorkspace(){
  const [route,setRoute]=useState(location.hash||'#/'),[videos,setVideos]=useState<VideoAsset[]>([]),[jobs,setJobs]=useState<Job[]>([]),[sessions,setSessions]=useState<VideoUpload[]>([]);
  const [uploads,setUploads]=useState<UploadItem[]>([]),[query,setQuery]=useState(''),[error,setError]=useState(''),[online,setOnline]=useState(false),[busy,setBusy]=useState('');
  const [sidebar,setSidebar]=useState(()=>{if(matchMedia('(max-width:720px)').matches)return false;try{return localStorage.getItem('sidebar-open')!=='false';}catch{return true;}});
  const [detail,setDetail]=useState<VideoDetail|null>(null),[subtitleInputs,setSubtitleInputs]=useState<SubtitleInput[]>([]),[workflow,setWorkflow]=useState(false),[review,setReview]=useState<Job|null>(null);
  const input=useRef<HTMLInputElement>(null),srtInput=useRef<HTMLInputElement>(null),resume=useRef<{id:string;legacy:boolean}|null>(null);
  const [confirm,setConfirm]=useState<{title:string;action:()=>Promise<void>}|null>(null);
  const [queue]=useState(()=>new UploadQueue(async(item,signal,changed)=>{
    const legacy=!!item.payload.legacy;
    if(!item.jobId){const created=await uploadRequest<{upload_id:string}>('/video-uploads',{method:'POST',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:item.name,size:item.size,request_id:item.key})});item.jobId=created.upload_id;changed();}
    await resumeUpload(item.file!,item.jobId!,signal,n=>{item.progress=n;changed();},legacy?'/uploads':'/video-uploads');
  }));
  const assetId=route.match(/^#\/videos\/([a-f0-9]{32})$/)?.[1],jobId=route.match(/^#\/jobs\/([a-f0-9]{32})$/)?.[1];
  const jobsView=route==='#/jobs';
  async function refresh(signal?:AbortSignal){
    const [v,j,u]=await Promise.all([request<{videos:VideoAsset[]}>('/videos',{signal}),request<{jobs:Job[]}>('/jobs',{signal}),request<{uploads:VideoUpload[]}>('/video-uploads',{signal})]);
    if(signal?.aborted)return;setVideos(v.videos);setJobs(j.jobs);setSessions(u.uploads);setOnline(true);
    queue.forgetCompleted([...u.uploads.filter(x=>x.asset_id).map(x=>x.upload_id),...j.jobs.filter(x=>x.status!=='UPLOADING').map(x=>x.job_id)]);
  }
  useEffect(()=>{const navigate=()=>{setRoute(location.hash||'#/');setWorkflow(false);setDetail(null);setSubtitleInputs([]);setQuery('');window.scrollTo(0,0);if(matchMedia('(max-width:720px)').matches)setSidebar(false);};window.addEventListener('hashchange',navigate);return()=>window.removeEventListener('hashchange',navigate);},[]);
  useEffect(()=>{if(!matchMedia('(max-width:720px)').matches){try{localStorage.setItem('sidebar-open',String(sidebar));}catch{}}},[sidebar]);
  useEffect(()=>{const media=matchMedia('(max-width:720px)');const change=()=>{if(media.matches)setSidebar(false);else{try{setSidebar(localStorage.getItem('sidebar-open')!=='false');}catch{setSidebar(true);}}};media.addEventListener('change',change);return()=>media.removeEventListener('change',change);},[]);
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape'&&matchMedia('(max-width:720px)').matches)setSidebar(false);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[]);
  useEffect(()=>{queue.onChange=()=>setUploads(queue.items.map(x=>({...x})));queue.activate();const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{await refresh(controller.signal);}catch{if(!controller.signal.aborted)setOnline(false);}finally{if(!controller.signal.aborted)timer=setTimeout(poll,2500);}};void poll();
    const visibility=()=>queue.suspend(document.visibilityState==='hidden');document.addEventListener('visibilitychange',visibility);
    const leave=(e:BeforeUnloadEvent)=>{if(queue.items.some(x=>x.file)){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',leave);
    return()=>{controller.abort();clearTimeout(timer);queue.dispose();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('beforeunload',leave);};},[queue]);
  useEffect(()=>{if(!assetId)return;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const d=await request<VideoDetail>(`/videos/${assetId}`,{signal:controller.signal});const s=await request<{subtitles:SubtitleInput[]}>(`/videos/${assetId}/subtitles`,{signal:controller.signal});if(!controller.signal.aborted){setDetail(d);setSubtitleInputs(s.subtitles);}}catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'영상 조회 실패');}finally{if(!controller.signal.aborted)timer=setTimeout(poll,3000);}};void poll();return()=>{controller.abort();clearTimeout(timer);};},[assetId]);
  async function action(key:string,operation:()=>Promise<unknown>){if(busy)return;setBusy(key);setError('');try{await operation();await refresh();setConfirm(null);}catch(e){setError(e instanceof Error?e.message:'요청 실패');}finally{setBusy('');}}
  function chooseFiles(files:FileList|null){if(!files)return;for(const file of Array.from(files)){if(resume.current){queue.add(file,{legacy:resume.current.legacy},resume.current.id);resume.current=null;break;}queue.add(file,{});}if(input.current)input.current.value='';}
  function chooseResume(id:string,legacy=false){resume.current={id,legacy};input.current?.click();}
  async function attachSrt(file:File|undefined){if(!file||!assetId)return;await action('srt',async()=>{if(file.size>4*1024**2)throw new Error('SRT는 4MB 이하만 첨부할 수 있습니다.');await jsonPost(`/videos/${assetId}/subtitles`,{filename:file.name,language:'auto',content:await file.text()});const s=await request<{subtitles:SubtitleInput[]}>(`/videos/${assetId}/subtitles`);setSubtitleInputs(s.subtitles);});if(srtInput.current)srtInput.current.value='';}
  const terminal=(j:Job)=>['COMPLETED','FAILED','CANCELLED'].includes(j.status);
  function jobRows(items:Job[]){return <div className="video-job-list">{items.map(job=><article key={job.job_id} className="video-job-row"><div><a className="job-title" href={`#/jobs/${job.job_id}`}>{job.original_filename}</a><p>{templates[job.metadata.workflow?.template||'full']} · {new Date(job.created_at).toLocaleString('ko-KR')}</p><span className={'status '+job.status.toLowerCase()}>{statusLabels[job.status]||job.status}</span>{job.error&&<p className="alert">{job.error}</p>}</div><div className="row-actions">
    {job.status==='UPLOADING'&&<button className="icon" title="업로드 재개" onClick={()=>chooseResume(job.job_id,true)}><Upload size={17}/></button>}
    {['READY','FAILED','CANCELLED'].includes(job.status)&&<button className="icon" title={job.status==='READY'?'작업 시작':'중단 단계부터 재시도'} disabled={!!busy} onClick={()=>void action(job.job_id,()=>request(`/jobs/${job.job_id}/${job.status==='READY'?'start':'retry'}`,{method:'POST'}))}><Play size={17}/></button>}
    {job.status==='AWAITING_REVIEW'&&<button onClick={()=>setReview(job)}>자막 검토</button>}
    {!terminal(job)&&!['READY','UPLOADING'].includes(job.status)&&<button className="icon" title="작업 중단" disabled={!!busy} onClick={()=>void action(job.job_id,()=>request(`/jobs/${job.job_id}/cancel`,{method:'POST'}))}><Pause size={17}/></button>}
    {!job.metadata.asset_id&&job.status!=='UPLOADING'&&<button disabled={!!busy} onClick={()=>void action(job.job_id,async()=>{const a=await request<VideoAsset>(`/jobs/${job.job_id}/register-source`,{method:'POST'});location.hash=`/videos/${a.asset_id}`;})}><FolderOpen size={15}/>원본 등록</button>}
    {terminal(job)&&<button className="icon danger" title="작업 삭제" onClick={()=>setConfirm({title:`${job.original_filename} 작업과 결과 파일을 삭제할까요?`,action:async()=>{await request(`/jobs/${job.job_id}`,{method:'DELETE'});}})}><Trash2 size={17}/></button>}
    </div><StageProgress job={job}/></article>)}{!items.length&&<div className="library-empty"><ListVideo size={30}/><p>작업이 없습니다.</p></div>}</div>;}
  const shownVideos=videos.filter(v=>v.original_filename.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const pendingSessions=sessions.filter(s=>!s.asset_id&&!uploads.some(u=>u.jobId===s.upload_id));
  return <div className={'app-shell video-workspace '+(sidebar?'sidebar-open':'sidebar-collapsed')}>
    <header><div className="brand-controls"><button className="icon sidebar-toggle" title={sidebar?'사이드바 닫기':'사이드바 열기'} aria-label={sidebar?'사이드바 닫기':'사이드바 열기'} aria-expanded={sidebar} onClick={()=>setSidebar(v=>!v)}><Menu className="sidebar-menu-icon" size={22}/><X className="sidebar-close-icon" size={22}/></button><a className="brand" href="#/" aria-label="장면 홈"><span className="brand-mark"><FileVideo size={21}/></span><strong>장면</strong><span className="brand-wordmark">JANGMYEON</span></a></div><div className="header-session"><span className={'connection '+(online?'online':'')}>{online?'연결됨':'연결 확인 중'}</span><AccountMenu/></div></header>
    {sidebar&&<><button className="sidebar-backdrop" aria-label="메뉴 닫기" onClick={()=>setSidebar(false)}/><aside className="workspace-nav"><button className="primary" onClick={()=>{resume.current=null;input.current?.click();}}><Plus size={19}/>영상 업로드</button><nav><a href="#/" aria-current={!jobsView&&!jobId?'page':undefined}><FolderOpen size={19}/>원본 보관함<span>{videos.length}</span></a><a href="#/jobs" aria-current={jobsView||jobId?'page':undefined}><ListVideo size={19}/>전체 작업<span>{jobs.length}</span></a></nav></aside></>}
    <input ref={input} type="file" accept="video/*,.mkv,.mov,.avi,.webm" multiple={!resume.current} hidden onChange={e=>chooseFiles(e.target.files)}/>
    <input ref={srtInput} type="file" accept=".srt" hidden onChange={e=>void attachSrt(e.target.files?.[0])}/>
    {jobId?<JobDetail id={jobId} key={jobId} labels={statusLabels} promotedAssetId={videos.find(v=>v.provenance.kind==='result'&&v.provenance.job_id===jobId)?.asset_id}/>:<main className="video-main">
      {error&&<p role="alert" className="alert">{error}<button className="icon" title="오류 닫기" onClick={()=>setError('')}><X size={16}/></button></p>}
      {assetId?<>{detail?<><div className="library-heading"><a className="icon" title="원본 보관함으로" href="#/"><ArrowLeft size={21}/></a><div><p className="eyebrow">원본 영상</p><h1>{detail.video.original_filename}</h1></div><button className="primary" onClick={()=>setWorkflow(true)}><Play size={17}/>새 작업</button></div>
        <section className="asset-overview"><video controls playsInline preload="none" poster={`${base}/videos/${assetId}/thumbnail`} src={`${base}/videos/${assetId}/stream`}/><div><h2>영상 정보</h2><dl><dt>길이</dt><dd>{durationLabel(detail.video.media?.duration)||'미확인'}</dd><dt>용량</dt><dd>{sizeLabel(detail.video.size_bytes)}</dd><dt>해상도</dt><dd>{detail.video.media?`${detail.video.media.width} × ${detail.video.media.height}`:'미확인'}</dd><dt>등록일</dt><dd>{new Date(detail.video.created_at).toLocaleString('ko-KR')}</dd><dt>등록 경로</dt><dd>{detail.video.provenance.kind==='result'?'결과 영상에서 등록':'파일 업로드'}{detail.video.provenance.parent_asset_id&&<a href={`#/videos/${detail.video.provenance.parent_asset_id}`}>이전 원본</a>}</dd></dl><a className="download" href={`${base}/videos/${assetId}/download`}><Download size={16}/>원본 다운로드</a><button className="danger" onClick={()=>setConfirm({title:'원본 영상을 삭제할까요? 연결된 작업이 있으면 삭제할 수 없습니다.',action:async()=>{await request(`/videos/${assetId}`,{method:'DELETE'});location.hash='/';}})}><Trash2 size={15}/>원본 삭제</button></div></section>
        <section className="asset-subtitles"><div className="section-heading"><h2>자막 입력 <small>{subtitleInputs.length}</small></h2><button disabled={!!busy} onClick={()=>srtInput.current?.click()}><Plus size={16}/>SRT 첨부</button></div>{subtitleInputs.length?<ul>{subtitleInputs.map(s=><li key={s.artifact_id}><span>{s.filename}</span><small>{s.language} · {s.cue_count}개 · {s.provenance.kind==='job_subtitle'?`작업 자막 v${s.provenance.revision}`:'외부 첨부'}</small></li>)}</ul>:<p className="muted">첨부된 자막이 없습니다.</p>}</section>
        <section><div className="section-heading"><h2>작업 기록 <small>{detail.jobs.length}</small></h2></div>{jobRows(detail.jobs)}</section>
      </>:<p role="status">영상 불러오는 중</p>}</>:<><div className="library-heading"><div><h1>{jobsView?'전체 작업':'원본 보관함'} <span className="heading-count">{jobsView?jobs.length:videos.length}</span></h1></div><button className="primary" onClick={()=>{resume.current=null;input.current?.click();}}><Upload size={17}/>영상 업로드</button></div>
      {!jobsView&&(uploads.length>0||pendingSessions.length>0)&&<section className="upload-shelf"><h2>업로드 <small>{uploads.length+pendingSessions.length}</small></h2>{uploads.map(u=><div className="upload-line" key={u.key}><div><strong>{u.name}</strong><small>{u.progress===u.size&&u.state==='uploading'?'영상 확인 및 등록 중':({waiting:'대기',uploading:'업로드 중',paused:'일시정지',error:'실패',done:'완료'}[u.state])} · {Math.floor(u.progress/u.size*100)}% · {sizeLabel(u.size)}</small>{u.error&&<p role="alert" className="alert">{u.error}</p>}<progress value={u.progress} max={u.size}/></div><button className="icon" title={['paused','error'].includes(u.state)?'업로드 재개':'업로드 일시정지'} onClick={()=>['paused','error'].includes(u.state)?queue.resume(u.key):queue.pause(u.key)}>{['paused','error'].includes(u.state)?<Play size={17}/>:<Pause size={17}/>}</button><button className="icon danger" title="업로드 제거" onClick={()=>queue.remove(u.key)}><X size={17}/></button></div>)}{pendingSessions.map(s=><div className="upload-line" key={s.upload_id}><div><strong>{s.filename}</strong><small>{Math.floor(s.uploaded_bytes/s.expected_size*100)}% · {s.status==='READY'?'등록 대기':'업로드 미완료'}</small><progress value={s.uploaded_bytes} max={s.expected_size}/></div><button className="icon" title={s.status==='READY'?'영상 등록 재시도':'파일 선택 후 업로드 재개'} onClick={()=>s.status==='READY'?void action(s.upload_id,()=>request(`/video-uploads/${s.upload_id}/complete`,{method:'POST'})):chooseResume(s.upload_id)}><Play size={17}/></button><button className="icon danger" title="업로드 삭제" onClick={()=>setConfirm({title:`${s.filename} 업로드를 삭제할까요?`,action:async()=>{await request(`/video-uploads/${s.upload_id}`,{method:'DELETE'});}})}><Trash2 size={17}/></button></div>)}</section>}
      <label className="library-search"><Search size={18}/><input aria-label={jobsView?'작업 검색':'영상 검색'} placeholder={jobsView?'작업 검색':'영상 검색'} value={query} onChange={e=>setQuery(e.target.value)}/></label>
      {jobsView?jobRows(jobs.filter(j=>j.original_filename.toLocaleLowerCase().includes(query.toLocaleLowerCase()))):<div className="asset-list">{shownVideos.map(v=><a key={v.asset_id} className="asset-row" href={`#/videos/${v.asset_id}`}><Thumbnail assetId={v.asset_id}/><div><h2>{v.original_filename}</h2><p>{durationLabel(v.media?.duration)} · {sizeLabel(v.size_bytes)}</p><small>{new Date(v.created_at).toLocaleString('ko-KR')}{v.provenance.kind==='result'?' · 결과에서 등록':''}</small></div><span className="asset-open">열기</span></a>)}{!shownVideos.length&&<div className="library-empty"><FolderOpen size={38}/><p>{query?'검색 결과가 없습니다.':'등록된 원본 영상이 없습니다.'}</p></div>}</div>}
      </>}
    </main>}
    {workflow&&detail&&<WorkflowDialog detail={detail} onClose={()=>setWorkflow(false)} onCreated={job=>{setWorkflow(false);location.hash=`/jobs/${job.job_id}`;void refresh();}}/>}
    {review&&<SubtitleEditor job={review} onClose={()=>setReview(null)} onRendered={()=>void refresh()}/>}
    {confirm&&<Confirm title={confirm.title} error={error} busy={!!busy} onClose={()=>setConfirm(null)} onConfirm={()=>void action('delete',confirm.action)}/>}
  </div>;
}

function Thumbnail({assetId}:{assetId:string}){
  const [failed,setFailed]=useState(false);
  return <div className="asset-thumbnail">{failed?<FileVideo size={24} aria-label="미리보기 없음"/>:<img src={`${base}/videos/${assetId}/thumbnail`} width={480} height={270} alt="" loading="lazy" decoding="async" onError={()=>setFailed(true)}/>}</div>;
}

function Confirm({title,error,busy,onClose,onConfirm}:{title:string;error:string;busy:boolean;onClose:()=>void;onConfirm:()=>void}){
  const ref=useRef<HTMLDialogElement>(null);useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close();},[]);
  return <dialog className="confirm-modal" ref={ref} onCancel={e=>{e.preventDefault();if(!busy)onClose();}}><h2>{title}</h2>{error&&<p role="alert" className="alert">{error}</p>}<div><button autoFocus disabled={busy} onClick={onClose}>취소</button><button className="destructive" disabled={busy} onClick={onConfirm}>삭제</button></div></dialog>;
}
