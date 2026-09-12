import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

// Mock all API traffic so layout checks never start a real media job.
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({channel:process.env.BROWSER_CHANNEL || 'msedge',headless:true});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error=>errors.push(error.message));
  const job = {job_id:'a'.repeat(32),status:'TRANSCRIBING',original_filename:'long_video_filename_for_mobile_layout_818.mp4',expected_size:123456,uploaded_bytes:123456,target_language:'ko',source_language:'auto',quality_profile:'balanced',created_at:'2026-09-12T00:00:00Z',completed_at:null,error:null,metadata:{transcription_progress:{total:80,completed:30,in_flight:3,retrying:0,failed:0,draining:false}}};
  await page.route('**/api/**', route=>{
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/session')) return route.fulfill({json:{enabled:false,user:null}});
    if (path.endsWith('/jobs')) return route.fulfill({json:{jobs:[job]}});
    if (path.endsWith('/'+job.job_id)) return route.fulfill({json:job});
    if (path.endsWith('/preview')) return route.fulfill({json:{video_available:false,expired:false,tracks:{original:{available:false,partial:false,cues:[]},translated:{available:false,partial:false,cues:[]}}}});
    return route.fulfill({status:503,json:{detail:'Browser test: writes disabled'}});
  });
  await page.goto(process.env.WEB_URL || 'http://127.0.0.1:5177');
  await page.getByRole('heading',{name:'전체 작업',exact:true}).waitFor();
  for (const width of [320,390,768,1440]) {
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Overflow at ${width}px`);
    assert.equal(await page.getByRole('progressbar',{name:'전사 진행률'}).getAttribute('value'),'30');
    await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
    assert.equal(await page.locator('dialog[open]').count(),1);
    await page.getByText('상세 설정',{exact:true}).click();
    assert.equal(await page.getByLabel('영상 코덱').isVisible(),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog[open]').count(),0);
    await page.locator('.job-title').click();
    await page.locator('.job-detail h1').filter({hasText:job.original_filename}).waitFor();
    assert.equal(await page.getByRole('tab',{name:'전사록',exact:true}).isVisible(),true);
    await page.getByTitle('작업 목록으로').click();
    await page.getByRole('tab',{name:'완료',exact:true}).click();
    assert.equal(await page.locator('.job-row').count(),0);
    await page.getByRole('tab',{name:'전체',exact:true}).click();
    await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
    await page.getByText('상세 설정',{exact:true}).click();
    await page.keyboard.press('Escape');
  }
  await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
  await page.getByLabel('영상 파일 선택').setInputFiles({name:'queued-design-check.mp4',mimeType:'video/mp4',buffer:Buffer.from('mock video')});
  await page.getByLabel('영상 설명 (선택)').fill('Draft context');
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
  assert.equal(await page.getByLabel('영상 설명 (선택)').inputValue(),'Draft context');
  await page.getByRole('button',{name:'업로드',exact:true}).click();
  assert.equal(await page.locator('dialog[open]').count(),0);
  await page.getByRole('heading',{name:'queued-design-check.mp4',exact:true}).waitFor();
  await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
  assert.equal(await page.getByLabel('영상 파일 선택').inputValue(),'');
  assert.equal(await page.getByLabel('영상 설명 (선택)').inputValue(),'');
  await page.keyboard.press('Escape');
  assert.deepEqual(errors,[]);
  console.log('Workspace browser checks passed: 320/390/768/1440px, filters, progress, upload and detail dialogs.');
} finally {
  await browser.close();
}
