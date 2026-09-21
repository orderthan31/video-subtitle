import { test } from 'node:test';
import assert from 'node:assert/strict';
import { failureMessage, isContentBlocked } from '../src/job-failure.ts';

test('content block identifies provider, stage and one-based position', () => {
  const job = {
    status: 'FAILED',
    error: 'generic error',
    metadata: {
      failure: {
        kind: 'content_blocked',
        provider: 'Gemini',
        stage: 'TRANSLATING',
        blocks: [{ segment: 4, reason: 'PROHIBITED_CONTENT' }],
      },
    },
  };
  assert.equal(isContentBlocked(job), true);
  assert.match(failureMessage(job), /번역 4번 구간/);
  assert.match(failureMessage(job), /PROHIBITED_CONTENT/);
  assert.match(failureMessage(job), /자동 재시도는 중단/);
  job.metadata.failure.stage = 'TRANSCRIBING';
  assert.match(failureMessage(job), /전사 4번 구간/);
  job.status = 'TRANSLATING';
  assert.equal(isContentBlocked(job), false);
});

test('ordinary failures retain their error message', () => {
  const job = { status: 'FAILED', error: 'Network error', metadata: {} };
  assert.equal(isContentBlocked(job), false);
  assert.equal(failureMessage(job), 'Network error');
});

test('placeholder review explains that encoding was not run', () => {
  const job = {
    status: 'AWAITING_REVIEW',
    error: null,
    metadata: {
      content_block_review: true,
      translation_blocks: { ko: [{ segment: 4, reason: 'PROHIBITED_CONTENT' }] },
    },
  };
  assert.match(failureMessage(job), /번역 4번/);
  assert.match(failureMessage(job), /차단된 영역입니다/);
  assert.match(failureMessage(job), /인코딩은 실행하지 않았습니다/);
  job.status = 'TRANSLATING';
  assert.doesNotMatch(failureMessage(job), /검토해 주세요/);
});
