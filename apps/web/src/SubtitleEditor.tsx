import {useEffect, useRef, useState} from 'react';
import {ChevronLeft, ChevronRight, Plus, Save, Trash2, X, Play} from 'lucide-react';
import {Job, request} from './api';

type Cue = {start:number; end:number; text:string; speaker?:string};
type Draft = {revision:number; duration:number; languages:Record<string,string>; tracks:Record<string,Cue[]>; allow_imported_layout?:boolean};
const pageSize = 30;
export function SubtitleEditor({job,onClose,onRendered,initialTrack='translated'}:{job:Job;onClose:()=>void;onRendered:()=>void;initialTrack?:string}) {
  const completed = job.status==='COMPLETED';
  const [draft,setDraft] = useState<Draft|null>(null), [track,setTrack] = useState(initialTrack);
  const [page,setPage] = useState(0), [dirty,setDirty] = useState(false), [discard,setDiscard] = useState(false);
  const [saving,setSaving] = useState(false), [error,setError] = useState('');
  const [saved,setSaved] = useState(false);
  const modal=useRef<HTMLDialogElement>(null);
  useEffect(()=>{modal.current?.showModal();return()=>modal.current?.close();},[]);
  useEffect(()=>{
    if(!dirty)return;
    const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
    window.addEventListener('beforeunload',warn);
    return()=>window.removeEventListener('beforeunload',warn);
  },[dirty]);
  useEffect(()=>{let active=true;request<Draft>(`/jobs/${job.job_id}/subtitles`).then(value=>{if(active){setDraft(value);setTrack(current=>current in value.tracks?current:Object.keys(value.tracks)[0]);}}).catch(e=>{if(active)setError(String(e.message));});return()=>{active=false;};},[job.job_id]);
  function update(index:number, changes:Partial<Cue>) {
    setDraft(value=>value&&({...value,tracks:{...value.tracks,[track]:value.tracks[track].map((cue,i)=>i===index?{...cue,...changes}:cue)}}));
    setDirty(true);setDiscard(false);setSaved(false);
  }
  function remove(index:number) {
    if(!draft||(!completed&&draft.tracks[track].length<=1))return;
    const next=draft.tracks[track].filter((_,i)=>i!==index);
    setDraft({...draft,tracks:{...draft.tracks,[track]:next}});
    setPage(Math.max(0,Math.min(page,Math.floor((next.length-1)/pageSize))));setDirty(true);setSaved(false);
  }
  function add() {
    if(!draft)return;
    const cues=draft.tracks[track], start=cues[cues.length-1]?.end||0;
    if(!Number.isFinite(start)||start>=draft.duration||cues.length>=10000)return;
    setDraft({...draft,tracks:{...draft.tracks,[track]:[...cues,{start,end:Math.min(draft.duration,start+2),text:''}]}});
    setPage(Math.floor(cues.length/pageSize));setDirty(true);
  }
  async function save(action:'save'|'render') {
    if(!draft||saving)return;
    setSaving(true);setError('');
    try {
      for(const cues of Object.values(draft.tracks)) {
        let previous=0;
        for(const cue of cues) {
          if(!Number.isFinite(cue.start)||!Number.isFinite(cue.end)||cue.start<0||cue.end<=cue.start||cue.end>draft.duration+0.05||cue.start<previous-0.001)
            throw new Error('자막 시간이 겹치거나 영상 범위를 벗어났습니다.');
          if(!cue.text.trim()||(!draft.allow_imported_layout&&cue.text.split('\n').length>2))throw new Error('자막 문구는 비어 있지 않은 최대 두 줄이어야 합니다.');
          previous=draft.allow_imported_layout?cue.start:cue.end;
        }
      }
      const next=await request<Draft>(`/jobs/${job.job_id}/subtitles`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:draft.revision,tracks:draft.tracks,action})});
      setDraft(next);setDirty(false);setDiscard(false);
      setSaved(true);
      if(completed)onRendered();
      if(action==='render'){onRendered();onClose();}
    } catch(e){setError(e instanceof Error?e.message:'저장에 실패했습니다.');}
    finally{setSaving(false);}
  }
  const cues=draft?.tracks[track]||[], pages=Math.max(1,Math.ceil(cues.length/pageSize));
  return <dialog ref={modal} className="editor-modal" aria-labelledby="subtitle-editor-title" onCancel={e=>{e.preventDefault();if(!saving){dirty?setDiscard(true):onClose();}}}><section className="subtitle-editor" onKeyDown={event=>{
    if(event.key==='Escape'&&!saving){event.preventDefault();dirty?setDiscard(true):onClose();}
    if(event.key==='Tab'){
      const controls=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled)')).filter(node=>node.offsetParent!==null);
      const first=controls[0],last=controls[controls.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    }
  }}>
    <div className="editor-heading"><div><h2 id="subtitle-editor-title">자막 수정</h2><p>{job.original_filename}</p></div><button className="icon" title="편집 닫기" disabled={saving} onClick={()=>dirty?setDiscard(true):onClose()}><X size={20}/></button></div>
    {error&&<p className="alert" role="alert">{error}</p>}
    {completed&&<p className="edit-notice">SRT·SMI 자막 파일에 반영됩니다. 기존 MP4의 자막은 변경되지 않습니다.</p>}
    {saved&&<p role="status" className="edit-saved">저장되었습니다.</p>}
    {!draft?(!error&&<p>자막 불러오는 중</p>):<>
      <div className="editor-toolbar"><label>자막 트랙<select autoFocus value={track} disabled={saving} onChange={e=>{setTrack(e.target.value);setPage(0);}}>{Object.entries(draft.languages).map(([key,language])=><option key={key} value={key}>{key==='original'?'원문':language}</option>)}</select></label><span>{draft.duration.toFixed(2)}초 · {cues.length}개 · v{draft.revision}{dirty?' · 저장 전':''}</span><button className="icon" title="자막 추가" disabled={saving||cues.length>=10000||(cues[cues.length-1]?.end||0)>=draft.duration} onClick={add}><Plus size={18}/></button></div>
      <div className="editor-cues"><div className="cue-heading"><span>시작 (초)</span><span>종료 (초)</span><span>문구</span><span/></div>{cues.slice(page*pageSize,(page+1)*pageSize).map((cue,offset)=>{const index=page*pageSize+offset;return <div className="cue-row" key={`${track}-${index}`}>
        <input type="number" step="0.001" min="0" max={draft.duration} aria-label={`자막 ${index+1} 시작`} value={Number.isFinite(cue.start)?cue.start:''} disabled={saving} onChange={e=>update(index,{start:e.target.value===''?NaN:Number(e.target.value)})}/>
        <input type="number" step="0.001" min="0" max={draft.duration} aria-label={`자막 ${index+1} 종료`} value={Number.isFinite(cue.end)?cue.end:''} disabled={saving} onChange={e=>update(index,{end:e.target.value===''?NaN:Number(e.target.value)})}/>
        <textarea aria-label={`자막 ${index+1} 문구`} rows={2} maxLength={1000} value={cue.text} disabled={saving} onChange={e=>update(index,{text:e.target.value})}/>
        <button className="icon danger" title={`자막 ${index+1} 삭제`} disabled={saving||(!completed&&cues.length<=1)} onClick={()=>remove(index)}><Trash2 size={16}/></button>
      </div>;})}</div>
      <div className="editor-footer"><div><button className="icon" title="이전 자막 페이지" disabled={page===0} onClick={()=>setPage(page-1)}><ChevronLeft size={18}/></button><span>{page+1} / {pages}</span><button className="icon" title="다음 자막 페이지" disabled={page+1>=pages} onClick={()=>setPage(page+1)}><ChevronRight size={18}/></button></div><div><button className="icon" title={completed?'자막 저장':'초안 저장'} disabled={saving||!dirty} onClick={()=>void save('save')}><Save size={19}/></button>{!completed&&<button disabled={saving} onClick={()=>void save('render')}><Play size={16}/>최종 출력</button>}</div></div>
    </>}
    {discard&&<div className="editor-discard"><span>저장하지 않은 변경이 있습니다.</span><button onClick={()=>setDiscard(false)}>계속 편집</button><button className="destructive" onClick={onClose}>변경 버리고 닫기</button></div>}
  </section></dialog>;
}
