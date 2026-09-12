import {useEffect,useRef,useState} from 'react';
import {Play,X} from 'lucide-react';
import {Job,request} from './api';
import {AudioInput,jsonPost,stageLabels,SubtitleInput,templates,VideoDetail} from './video-api';

type Plan={stages:string[];reused:string[];paid_stages:string[]};
export function WorkflowDialog({detail,onClose,onCreated}:{detail:VideoDetail;onClose:()=>void;onCreated:(job:Job)=>void}){
  const modal=useRef<HTMLDialogElement>(null);
  const [template,setTemplate]=useState('full'),[subtitles,setSubtitles]=useState<SubtitleInput[]>([]),[audios,setAudios]=useState<AudioInput[]>([]);
  const [subtitle,setSubtitle]=useState(''),[audio,setAudio]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[plan,setPlan]=useState<Plan|null>(null);
  const [options,setOptions]=useState({source_language:'auto',target_language:'ko',quality_profile:'balanced',video_codec:'hevc',subtitle_mode:'burn',resolution:'original',audio_filter:'silence3',vad_mode:'off',video_description:''});
  const key=useRef(crypto.randomUUID()), lastPayload=useRef('');
  const base=`/videos/${detail.video.asset_id}`;
  useEffect(()=>{modal.current?.showModal();return()=>modal.current?.close();},[]);
  useEffect(()=>{const controller=new AbortController();
    request<{subtitles:SubtitleInput[]}>(base+'/subtitles',{signal:controller.signal}).then(v=>setSubtitles(v.subtitles)).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    request<{audio_inputs:AudioInput[]}>(base+'/audio-inputs',{signal:controller.signal}).then(v=>setAudios(v.audio_inputs)).catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();},[base]);
  const acceptsSubtitle=['translate','encode','full'].includes(template), needsTranscription=['transcribe','transcribe_translate','full'].includes(template)&&!subtitle;
  const hasTranslation=['translate','transcribe_translate','full'].includes(template),hasEncoding=['encode','full'].includes(template);
  const validInput=!(template==='translate'&&!subtitle)&&!(template==='encode'&&options.subtitle_mode!=='none'&&!subtitle);
  useEffect(()=>{setPlan(null);if(!validInput)return;const controller=new AbortController();
    request<Plan>(base+'/workflow-plan',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({template,subtitle_artifact_id:subtitle||null,audio_job_id:audio||null,subtitle_mode:options.subtitle_mode})})
      .then(value=>{if(!controller.signal.aborted)setPlan(value);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();
  },[base,template,subtitle,audio,options.subtitle_mode,validInput]);
  function option(name:keyof typeof options,value:string){setOptions(o=>({...o,[name]:value}));setError('');}
  function changeTemplate(value:string){setTemplate(value);setSubtitle('');setAudio('');setPlan(null);setError('');}
  async function importTrack(value:string){
    if(!value.startsWith('job:')){setSubtitle(value);setAudio('');return;}
    setBusy(true);setError('');
    const [,id,track,revision]=value.split(':');
    try{const added=await jsonPost<SubtitleInput>(base+'/subtitle-inputs',{job_id:id,track,revision:Number(revision)});setSubtitles(v=>[added,...v]);setSubtitle(added.artifact_id);setAudio('');}
    catch(e){setError(e instanceof Error?e.message:'자막을 가져오지 못했습니다.');}finally{setBusy(false);}
  }
  async function submit(){
    if(!plan||busy)return;setBusy(true);setError('');
    const payload={template,subtitle_artifact_id:subtitle||null,audio_job_id:audio||null,options};
    const serialized=JSON.stringify(payload);if(lastPayload.current&&lastPayload.current!==serialized)key.current=crypto.randomUUID();lastPayload.current=serialized;
    try{onCreated(await jsonPost<Job>(base+'/jobs',{...payload,request_id:key.current}));}
    catch(e){setError(e instanceof Error?e.message:'작업 생성 실패');}finally{setBusy(false);}
  }
  return <dialog ref={modal} className="workflow-modal" onCancel={e=>{e.preventDefault();if(!busy)onClose();}} aria-labelledby="workflow-title">
    <form onSubmit={e=>{e.preventDefault();void submit();}}>
      <div className="dialog-heading"><div><h2 id="workflow-title">새 작업</h2><p>{detail.video.original_filename}</p></div><button type="button" className="icon" title="닫기" disabled={busy} onClick={onClose}><X size={20}/></button></div>
      {error&&<p role="alert" className="alert">{error}</p>}
      <fieldset disabled={busy}><label>작업 종류<select value={template} onChange={e=>changeTemplate(e.target.value)}>{Object.entries(templates).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
      {acceptsSubtitle&&<label>자막 입력<select value={subtitle} onChange={e=>void importTrack(e.target.value)}><option value="">{template==='full'?'새로 전사':'선택 안 함'}</option>{subtitles.map(s=><option key={s.artifact_id} value={s.artifact_id}>{s.filename} · {s.language} · {s.cue_count}개</option>)}{detail.jobs.filter(j=>j.status==='COMPLETED').flatMap(j=>(j.metadata.result_files||[]).filter(f=>f.endsWith('.srt')).map(f=><option key={j.job_id+f} value={`job:${j.job_id}:${f.replace('.srt','')}:${j.metadata.subtitle_revision||0}`}>{f} · {new Date(j.created_at).toLocaleString('ko-KR')} · v{j.metadata.subtitle_revision||0}</option>))}</select></label>}
      {needsTranscription&&<label>음성 입력<select value={audio} onChange={e=>setAudio(e.target.value)}><option value="">원본에서 추출</option>{audios.map(a=><option key={a.job_id} value={a.job_id}>추출 음성 · {new Date(a.created_at).toLocaleString('ko-KR')}</option>)}</select></label>}
      <div className="workflow-fields">
      {needsTranscription&&<><label>원본 언어<select value={options.source_language} onChange={e=>option('source_language',e.target.value)}>{Object.entries({auto:'자동',ko:'한국어',en:'영어',ja:'일본어',zh:'중국어',es:'스페인어'}).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label><label>무음 필터<select value={options.audio_filter} onChange={e=>option('audio_filter',e.target.value)}><option value="silence3">3초 이상 무음</option><option value="off">사용 안 함</option><option value="conservative">보수적</option><option value="strong">강하게</option></select></label><label className="check-field"><input type="checkbox" checked={options.vad_mode==='nvidia'} onChange={e=>option('vad_mode',e.target.checked?'nvidia':'off')}/>NVIDIA 음성 감지</label></>}
      {hasTranslation&&<label>번역 언어<select value={options.target_language} onChange={e=>option('target_language',e.target.value)}>{Object.entries({ko:'한국어',en:'영어',ja:'일본어',zh:'중국어',es:'스페인어'}).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>}
      {hasEncoding&&<><label>코덱<select value={options.video_codec} onChange={e=>option('video_codec',e.target.value)}><option value="hevc">HEVC</option><option value="h264">H.264</option></select></label><label>화질<select value={options.quality_profile} onChange={e=>option('quality_profile',e.target.value)}><option value="balanced">균형</option><option value="high">고화질</option><option value="compact">용량 우선</option></select></label><label>해상도<select value={options.resolution} onChange={e=>option('resolution',e.target.value)}><option value="original">원본</option><option value="1080p">1080p</option><option value="720p">720p</option></select></label><label>자막 출력<select value={options.subtitle_mode} onChange={e=>{option('subtitle_mode',e.target.value);if(template==='encode'&&e.target.value==='none')setSubtitle('');}}><option value="burn">영상에 삽입</option><option value="soft">선택형 자막</option><option value="none">자막 없음</option></select></label></>}
      </div>
      {hasTranslation&&<label>영상 설명<textarea rows={3} maxLength={2000} value={options.video_description} onChange={e=>option('video_description',e.target.value)}/></label>}
      </fieldset>
      <div className="workflow-plan" aria-live="polite"><h3>실행 단계</h3>{plan?<ol>{plan.stages.map(s=><li key={s}>{stageLabels[s]||s}</li>)}</ol>:<p>{validInput?'확인 중':'자막 입력을 선택하세요.'}</p>}{plan&&plan.reused.length>0&&<p>재사용: {plan.reused.map(s=>s==='audio'?'추출 음성':'선택한 자막').join(', ')}</p>}</div>
      <div className="workflow-footer"><button type="button" onClick={onClose} disabled={busy}>취소</button><button className="primary" disabled={busy||!plan}><Play size={17}/>{busy?'처리 중':'작업 시작'}</button></div>
    </form>
  </dialog>;
}
