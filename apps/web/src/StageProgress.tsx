import type { Job } from './api';
import './progress.css';
export const StageProgress = ({ job }: { job: Job }) => {
  const translation = job.metadata.translation_progress;
  if (
    translation &&
    (job.status === 'TRANSLATING' ||
      (['FAILED', 'CANCELLED'].includes(job.status) && job.metadata.failed_stage === 'TRANSLATING'))
  ) {
    const completed = Math.max(0, Math.min(translation.total, translation.completed));
    const percent = translation.total > 0 ? Math.round((completed / translation.total) * 100) : 100;
    return (
      <div className="stage-progress" aria-live="polite">
        <p>
          번역 ({translation.language}) {completed} / {translation.total} 묶음 · {percent}%
          {job.status === 'TRANSLATING' && (
            <>
              {' '}
              · 요청 중 {translation.in_flight}
              {translation.retrying > 0 && ` · 재시도 대기 ${translation.retrying}`}
              {translation.draining && ' · 잔여 요청 회수 중'}
            </>
          )}
          {translation.failed > 0 && ` · 실패 ${translation.failed}`}
        </p>
        <progress
          aria-label="번역 진행률"
          aria-valuetext={`${completed} / ${translation.total} 묶음 완료, ${percent}%`}
          max={Math.max(1, translation.total)}
          value={translation.total ? completed : 1}
        />
      </div>
    );
  }
  const stt = job.metadata.transcription_progress;
  if (stt && ['TRANSCRIBING', 'FAILED', 'CANCELLED'].includes(job.status)) {
    const completed = Math.max(0, Math.min(stt.total, stt.completed));
    return (
      <div className="stage-progress" aria-live="polite">
        <p>
          전사 {completed} / {stt.total} 구간
          {job.status === 'TRANSCRIBING' && (
            <>
              {' '}
              · 요청 중 {stt.in_flight}
              {stt.retrying > 0 && ` · 재시도 대기 ${stt.retrying}`}
              {stt.draining && ' · 잔여 요청 회수 중'}
            </>
          )}
          {stt.failed > 0 && ` · 실패 ${stt.failed}`}
        </p>
        <progress
          aria-label="전사 진행률"
          max={Math.max(1, stt.total)}
          value={stt.total ? completed : 1}
        />
      </div>
    );
  }
  if (job.status !== 'ENCODING') {
    return null;
  }
  const fraction = job.metadata.stage_progress;
  const valid = typeof fraction === 'number' && Number.isFinite(fraction);
  return (
    <div className="stage-progress" aria-live="polite">
      <p>
        {job.status_message || '인코딩'}
        {valid ? ` · ${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%` : ''}
      </p>
      {valid && <progress aria-label="인코딩 진행률" max={1} value={fraction} />}
    </div>
  );
};
