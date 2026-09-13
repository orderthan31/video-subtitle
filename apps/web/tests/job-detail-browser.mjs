import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const media = await readFile(process.env.TEST_VIDEO || 'data/detail-test.mp4');
const browser = await chromium.launch({
  channel: process.env.BROWSER_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const id = 'b'.repeat(32);
  const job = {
    job_id: id,
    status: 'COMPLETED',
    original_filename: 'sample-video.mp4',
    expected_size: 123456,
    uploaded_bytes: 123456,
    target_language: 'ko',
    source_language: 'en',
    quality_profile: 'balanced',
    created_at: '2026-09-12T00:00:00Z',
    error: null,
    metadata: {},
  };
  let preview = {
    video_available: true,
    expired: false,
    tracks: {
      original: {
        available: true,
        partial: false,
        cues: [
          { start: 1, end: 2, text: 'First sentence' },
          { start: 3, end: 4, text: 'Second sentence' },
        ],
      },
      translated: {
        available: true,
        partial: false,
        cues: [
          { start: 1, end: 2, text: '첫 번째 문장' },
          { start: 3, end: 4, text: '두 번째 문장' },
        ],
      },
    },
  };
  let draft = {
    revision: 0,
    duration: 5,
    languages: { original: 'en', translated: 'ko' },
    tracks: { original: preview.tracks.original.cues, translated: preview.tracks.translated.cues },
  };
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/session')) {
      return route.fulfill({ json: { enabled: false, user: null } });
    }
    if (path === '/api/videos') {
      return route.fulfill({ json: { videos: [] } });
    }
    if (path === '/api/video-uploads') {
      return route.fulfill({ json: { uploads: [] } });
    }
    if (path.endsWith('/jobs')) {
      return route.fulfill({ json: { jobs: [job] } });
    }
    if (path.endsWith('/' + id)) {
      return route.fulfill({ json: job });
    }
    if (path.endsWith('/preview')) {
      return route.fulfill({ json: preview });
    }
    if (path.endsWith('/subtitles')) {
      if (route.request().method() === 'PUT') {
        const body = route.request().postDataJSON();
        draft = { ...draft, tracks: body.tracks, revision: draft.revision + 1 };
        preview = {
          ...preview,
          tracks: {
            original: { available: true, partial: false, cues: draft.tracks.original },
            translated: { available: true, partial: false, cues: draft.tracks.translated },
          },
        };
      }
      return route.fulfill({ json: draft });
    }
    if (path.endsWith('/stream')) {
      const range = route
        .request()
        .headers()
        ['range']?.match(/bytes=(\d+)-(\d*)/);
      if (range) {
        const start = Number(range[1]),
          end = range[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1;
        return route.fulfill({
          status: 206,
          body: media.subarray(start, end + 1),
          contentType: 'video/mp4',
          headers: {
            'Accept-Ranges': 'bytes',
            'Content-Range': `bytes ${start}-${end}/${media.length}`,
          },
        });
      }
      return route.fulfill({
        body: media,
        contentType: 'video/mp4',
        headers: { 'Accept-Ranges': 'bytes' },
      });
    }
    return route.fulfill({ status: 503, json: { detail: 'Writes disabled' } });
  });
  const url = process.env.WEB_URL || 'http://127.0.0.1:5183';
  await page.goto(url + '/#/jobs/' + id);
  await page.locator('.job-detail h1').filter({ hasText: job.original_filename }).waitFor();
  await page.waitForFunction(
    () => document.querySelector('.player-surface video')?.readyState >= 1,
  );
  await page.locator('.result-downloads summary').click();
  assert.match(
    await page.getByRole('link', { name: '결과 영상 · MP4' }).getAttribute('href'),
    /final\.mp4$/,
  );
  await page.locator('.result-downloads summary').click();
  await page.getByRole('button', { name: '새 원본으로 등록', exact: true }).click();
  await page.locator('.promotion-modal[open]').waitFor();
  await page.locator('.promotion-modal').getByRole('button', { name: '취소', exact: true }).click();
  assert.equal(await page.locator('.promotion-modal[open]').count(), 0);
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page.getByRole('tab', { name: '번역 자막', exact: true }).click();
    await page.getByLabel('자막 검색').fill('두 번째');
    assert.equal(await page.locator('.preview-cue').count(), 1);
    await page.locator('.preview-cue').click();
    try {
      await page.waitForFunction(
        () => Math.abs(document.querySelector('.player-surface video').currentTime - 3) < 0.1,
      );
    } catch (e) {
      console.log(
        await page.locator('video').evaluate((v) => ({
          time: v.currentTime,
          duration: v.duration,
          ready: v.readyState,
          error: v.error?.message,
          seekable: Array.from({ length: v.seekable.length }, (_, i) => [
            v.seekable.start(i),
            v.seekable.end(i),
          ]),
        })),
      );
      throw e;
    }
    await page.getByTitle('검색 지우기').click();
    await page.screenshot({ path: `data/detail-${width}.png`, fullPage: true });
    await page.getByRole('tab', { name: '전사록', exact: true }).click();
  }
  await page.locator('video').evaluate((v) => {
    v.muted = true;
    return v.play();
  });
  await page.waitForFunction(() => document.querySelector('video').currentTime > 3.1);
  await page.locator('video').evaluate((v) => v.pause());
  await page.getByTitle('사이드바 닫기', { exact: true }).filter({ visible: true }).first().click();
  assert.equal(await page.locator('.workspace-nav').isVisible(), false);
  await page.getByTitle('사이드바 열기', { exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTitle('사이드바 열기', { exact: true }).click();
  assert.equal(await page.locator('.workspace-nav').isVisible(), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.workspace-nav').isVisible(), false);
  await page.getByRole('tab', { name: '번역 자막', exact: true }).click();
  await page.getByTitle('최종 자막 편집').click();
  await page.getByLabel('자막 1 시작', { exact: true }).fill('0.5');
  await page.getByLabel('자막 1 종료', { exact: true }).fill('2.5');
  await page.getByLabel('자막 1 문구', { exact: true }).fill('수정한 자막');
  await page.getByTitle('자막 2 삭제', { exact: true }).click();
  await page.screenshot({ path: 'data/final-editor-mobile.png', fullPage: true });
  await page.getByTitle('자막 저장', { exact: true }).click();
  await page.getByText('저장되었습니다.', { exact: true }).waitFor();
  assert.deepEqual(draft.tracks.translated, [{ start: 0.5, end: 2.5, text: '수정한 자막' }]);
  await page.getByTitle('편집 닫기', { exact: true }).click();
  await page.getByText('수정한 자막', { exact: true }).waitFor();
  await page.getByTitle('최종 자막 편집').click();
  await page.getByLabel('자막 1 문구', { exact: true }).fill('버릴 변경');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '변경 버리고 닫기' }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByTitle('작업 목록으로').click();
  await page.locator('.job-title').click();
  await page.goBack();
  await page.getByRole('heading', { name: /^전체 작업/ }).waitFor();
  job.status = 'TRANSLATING';
  preview = {
    ...preview,
    video_available: false,
    tracks: { ...preview.tracks, translated: { available: false, partial: false, cues: [] } },
  };
  await page.goto(url + '/#/jobs/' + id);
  await page.getByRole('tab', { name: '번역 자막', exact: true }).click();
  await page.getByText('번역 결과를 기다리고 있습니다.').waitFor();
  assert.equal(await page.locator('video').count(), 0);
  await page.getByRole('tab', { name: '전사록', exact: true }).click();
  await page.getByText('First sentence', { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log(
    'Detail page: responsive layout, video playback/seek, tabs/search, deep link/back, subtitle editing and pending results passed.',
  );
} finally {
  await browser.close();
}
