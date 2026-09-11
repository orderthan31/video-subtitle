import {blockSize, verifyUploadedPrefix} from './upload-verification';

export const base = import.meta.env.VITE_API_BASE_URL || '/api';
export type Job = {
  job_id: string; status: string; original_filename: string; expected_size: number; uploaded_bytes: number;
  target_language: string; quality_profile: string; created_at: string; completed_at: string | null;
  source_language: string; video_codec?: string;
  error: string | null; status_message?: string | null;
  metadata: {cancel_requested?: boolean; duration?: number; stage_progress?: number | null; result_files?: string[]};
};
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof body.detail === 'string' ? body.detail : body.detail?.message || `요청 실패 (${response.status})`);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export async function resumeUpload(file: File, id: string, signal: AbortSignal, progress: (n: number) => void) {
  let retries = 0;
  while (!signal.aborted) {
    try {
      const state = await request<{uploaded_bytes: number; expected_size: number; resumable: boolean}>(`/uploads/${id}`, {signal});
      if (!state.resumable) return;
      if (state.expected_size !== file.size) throw new ApiError(400, '원본 파일과 크기가 일치해야 합니다.');
      let offset = state.uploaded_bytes;
      progress(offset);
      await verifyUploadedPrefix(file, offset, signal, block => request<void>(`/uploads/${id}/verify`, {
        method: 'POST', signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(block),
      }));
      while (offset < file.size) {
        const chunk = file.slice(offset, offset + blockSize);
        const next = await request<{uploaded_bytes: number}>(`/uploads/${id}/chunks?offset=${offset}`, {
          method: 'PUT', body: chunk, signal, headers: {'Content-Type': 'application/octet-stream'},
        });
        if (next.uploaded_bytes <= offset) throw new Error('업로드 진행 정보를 확인할 수 없습니다.');
        offset = next.uploaded_bytes;
        progress(offset);
        retries = 0;
      }
      await request(`/uploads/${id}/complete`, {method: 'POST', signal});
      return;
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof ApiError && error.status !== 409 && error.status < 500) throw error;
      if (!(error instanceof ApiError) && !(error instanceof TypeError)) throw error;
      if (++retries > 4) throw error;
      await new Promise<void>(resolve => {
        const timer = setTimeout(done, retries * 500);
        function done() { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); }
        signal.addEventListener('abort', done, {once: true});
      });
    }
  }
}
