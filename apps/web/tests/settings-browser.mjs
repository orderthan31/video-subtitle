import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const screenshots = process.env.TEST_SCREENSHOTS || 'data/multi-provider-preview';
await mkdir(screenshots, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const errors = [],
    saved = [];
  const credentials = Object.fromEntries(
    ['gemini', 'openai', 'xai', 'openrouter', 'anthropic'].map((provider) => [
      provider,
      {
        has_api_key: provider === 'gemini',
        key_source: provider === 'gemini' ? 'registered' : 'none',
      },
    ]),
  );
  let preferences = {
    revision: 0,
    transcription_model: 'test-stt',
    transcription_fallback_model: '',
    translation_model: 'test-translation',
    translation_fallback_model: '',
    translation_provider: 'gemini',
    translation_fallback_provider: 'gemini',
    fallback_on_error: false,
    fallback_on_block: false,
    has_api_key: true,
    key_source: 'registered',
    credentials,
  };
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/auth/session')) {
      return route.fulfill({ json: { enabled: false, user: null } });
    }
    if (url.pathname.endsWith('/settings/llm/models')) {
      assert.equal(url.searchParams.get('provider'), 'openrouter');
      return route.fulfill({ json: { models: ['vendor/test-model:free'] } });
    }
    if (url.pathname.endsWith('/settings/llm')) {
      if (route.request().method() === 'PUT') {
        const payload = route.request().postDataJSON();
        saved.push(payload);
        for (const [provider, action] of Object.entries(payload.credential_updates)) {
          credentials[provider] = {
            has_api_key: !action.remove_api_key,
            key_source: action.remove_api_key ? 'none' : 'registered',
          };
        }
        const { credential_updates: _actions, ...values } = payload;
        preferences = {
          ...preferences,
          ...values,
          revision: preferences.revision + 1,
          credentials,
        };
      }
      return route.fulfill({ json: preferences });
    }
    return route.fulfill({ json: { videos: [], jobs: [], assets: [], uploads: [] } });
  });
  await page.goto(`${process.env.TEST_WEB_URL || 'http://127.0.0.1:5178'}/#/settings`);
  await page.getByRole('heading', { name: 'AI 공급자 설정' }).waitFor();
  await page.getByLabel('공급자', { exact: true }).selectOption('openrouter');
  await page.getByLabel('새 API 키').fill('test-router-key-not-real-12345');
  await page.getByLabel('공급자', { exact: true }).selectOption('anthropic');
  await page.getByLabel('새 API 키').fill('test-anthropic-key-not-real-12345');
  await page.getByLabel('기본 공급자', { exact: true }).selectOption('openrouter');
  await page.getByLabel('번역 모델', { exact: true }).fill('vendor/test-model:free');
  await page.getByLabel('폴백 공급자', { exact: true }).selectOption('anthropic');
  await page.getByLabel('번역 폴백 모델', { exact: true }).fill('test-fallback');
  await page.getByLabel('요청 오류 시 폴백 사용').check();
  await page.getByRole('button', { name: '설정 저장' }).click();
  await page.getByRole('status').filter({ hasText: '저장했습니다' }).waitFor();
  assert.equal(saved.length, 1);
  assert.equal(saved[0].translation_provider, 'openrouter');
  assert.equal(saved[0].translation_fallback_provider, 'anthropic');
  assert.equal(saved[0].credential_updates.openrouter.api_key, 'test-router-key-not-real-12345');
  assert.equal(saved[0].credential_updates.anthropic.api_key, 'test-anthropic-key-not-real-12345');
  assert.ok(!('credentials' in saved[0]));
  assert.equal(await page.getByLabel('새 API 키').inputValue(), '');
  await page.getByLabel('공급자', { exact: true }).selectOption('openrouter');
  await page.getByRole('button', { name: '모델 목록 조회' }).click();
  await page.getByRole('status').filter({ hasText: '1개' }).waitFor();
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${screenshots}/settings-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${screenshots}/settings-mobile.png`, fullPage: true });
  await page.getByRole('button', { name: '등록 키 삭제' }).click();
  await page.getByRole('button', { name: '설정 저장' }).click();
  await page.getByRole('status').filter({ hasText: '저장했습니다' }).waitFor();
  assert.deepEqual(saved[1].credential_updates.openrouter, { remove_api_key: true });
  assert.deepEqual(errors, []);
  console.log(
    'Settings browser checks passed: provider selection, isolated keys, save/delete, model list, desktop/mobile.',
  );
} finally {
  await browser.close();
}
