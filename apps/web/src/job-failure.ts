import type { Job } from './api';

export const isContentBlocked = (job: Job) =>
  job.status === 'FAILED' && job.metadata.failure?.kind === 'content_blocked';

export const failureMessage = (job: Job) => {
  const transcription = job.metadata.transcription_blocks || [];
  const translation = Object.values(job.metadata.translation_blocks || {}).flat();
  const providers = [
    ...new Set([...transcription, ...translation].map((block) => block.provider || 'Gemini')),
  ].join(', ');
  if (
    !isContentBlocked(job) &&
    (transcription.length || translation.length || job.metadata.content_block_review)
  ) {
    const locations = [
      transcription.length
        ? `전사 ${transcription.map((block) => block.segment).join(', ')}번`
        : '',
      translation.length
        ? `번역 ${[...new Set(translation.map((block) => block.segment))].join(', ')}번`
        : '',
    ]
      .filter(Boolean)
      .join(' · ');
    const notice = `${providers || 'AI 공급자'} 콘텐츠 차단${locations ? ` (${locations})` : ''}: 해당 구간을 “차단된 영역입니다”로 대체했습니다.`;
    const review =
      job.status === 'AWAITING_REVIEW'
        ? ' 자막 번인·인코딩은 실행하지 않았습니다. 자막을 검토해 주세요.'
        : '';
    return `${notice}${review}${job.error ? ` ${job.error}` : ''}`;
  }
  if (!isContentBlocked(job)) {
    return job.error;
  }
  const failure = job.metadata.failure!;
  const stage = failure.stage === 'TRANSLATING' ? '번역' : '전사';
  const positions = failure.blocks
    .filter((block) => block.segment !== undefined)
    .map((block) => block.segment)
    .join(', ');
  const reasons = [...new Set(failure.blocks.map((block) => block.reason))].join(', ');
  return `${failure.provider || 'Gemini'} 콘텐츠 정책으로 ${stage}${positions ? ` ${positions}번 구간` : ''}이 차단됐습니다 (${reasons}). 자동 재시도는 중단했으며 완료된 결과는 보존됩니다.`;
};
