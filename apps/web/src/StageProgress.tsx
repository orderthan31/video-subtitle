import type {Job} from './api';
import './progress.css';

export function StageProgress({job}: {job: Job}) {
  if (job.status !== 'ENCODING') return null;
  const fraction = job.metadata.stage_progress;
  const valid = typeof fraction === 'number' && Number.isFinite(fraction);
  return <div className="stage-progress" aria-live="polite">
    <p>{job.status_message || '인코딩'}{valid ? ` · ${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%` : ''}</p>
    {valid && <progress aria-label="인코딩 진행률" max={1} value={fraction}/>}
  </div>;
}
