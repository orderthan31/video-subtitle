import {Job, request} from './api';

export type VideoAsset = {asset_id:string; original_filename:string; size_bytes:number; created_at:string;
  media?:{duration:number;width:number;height:number;has_audio:boolean};
  provenance:{kind:string;job_id?:string;parent_asset_id?:string}};
export type VideoUpload = {upload_id:string;filename:string;expected_size:number;uploaded_bytes:number;status:string;asset_id?:string;created_at:string};
export type SubtitleInput = {artifact_id:string;filename:string;language:string;cue_count:number;created_at:string;provenance:{kind:string;revision?:number}};
export type AudioInput = {job_id:string;created_at:string;size_bytes:number};
export type VideoDetail = {video:VideoAsset;jobs:Job[]};
export const templates:Record<string,string> = {full:'전체 처리',extract_audio:'음성 추출',transcribe:'전사만',transcribe_translate:'전사 + 번역',translate:'번역만',encode:'인코딩만'};
export const stageLabels:Record<string,string> = {analyze:'영상 확인',extract_audio:'음성 추출',preprocess_audio:'음성 전처리',transcribe:'전사',translate:'번역',generate_subtitle:'자막 저장',encode:'인코딩',validate_video:'결과 검증'};
export const statusLabels:Record<string,string> = {UPLOADING:'업로드 중',READY:'실행 대기',QUEUED:'처리 대기',ANALYZING:'영상 분석',EXTRACTING_AUDIO:'음성 추출',PREPROCESSING_AUDIO:'음성 전처리',TRANSCRIBING:'전사 중',FILTERING_TRANSCRIPT:'전사 정리',TRANSLATING:'번역 중',GENERATING_SUBTITLE:'자막 저장',AWAITING_REVIEW:'자막 검토',ENCODING:'인코딩 중',VALIDATING:'결과 검증',COMPLETED:'완료',FAILED:'실패',CANCELLED:'중단됨'};
export const sizeLabel=(n:number)=>n>=1024**3?`${(n/1024**3).toFixed(2)} GB`:`${(n/1024**2).toFixed(1)} MB`;
export const durationLabel=(n?:number)=>n===undefined?'':`${Math.floor(n/60)}분 ${Math.floor(n%60)}초`;
export const jsonPost=<T,>(path:string,body:unknown)=>request<T>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
