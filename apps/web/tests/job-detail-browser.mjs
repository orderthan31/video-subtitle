import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const media = await readFile(process.env.TEST_VIDEO || 'data/detail-test.mp4');
const browser = await chromium.launch({channel:process.env.BROWSER_CHANNEL || 'msedge',headless:true});
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const id='b'.repeat(32);
  const job={job_id:id,status:'COMPLETED',original_filename:'sample-video.mp4',expected_size:123456,uploaded_bytes:123456,target_language:'ko',source_language:'en',quality_profile:'balanced',created_at:'2026-09-12T00:00:00Z',error:null,metadata:{}};
  let preview={video_available:true,expired:false,tracks:{original:{available:true,partial:false,cues:[{start:1,end:2,text:'First sentence'},{start:3,end:4,text:'Second sentence'}]},translated:{available:true,partial:false,cues:[{start:1,end:2,text:'첫 번째 문장'},{start:3,end:4,text:'두 번째 문장'}]}}};
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/auth/session'))return route.fulfill({json:{enabled:false,user:null}});
    if(path.endsWith('/jobs'))return route.fulfill({json:{jobs:[job]}});
    if(path.endsWith('/'+id))return route.fulfill({json:job});
    if(path.endsWith('/preview'))return route.fulfill({json:preview});
    if(path.endsWith('/stream')) {
      const range=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/);
      if(range){const start=Number(range[1]),end=range[2]?Math.min(Number(range[2]),media.length-1):media.length-1;
        return route.fulfill({status:206,body:media.subarray(start,end+1),contentType:'video/mp4',headers:{'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${media.length}`}});}
      return route.fulfill({body:media,contentType:'video/mp4',headers:{'Accept-Ranges':'bytes'}});
    }
    return route.fulfill({status:503,json:{detail:'Writes disabled'}});
  });
  const url=process.env.WEB_URL||'http://127.0.0.1:5177';
  await page.goto(url+'/#/jobs/'+id);
  await page.locator('.job-detail h1').filter({hasText:job.original_filename}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.player-surface video')?.readyState>=1);
  for(const width of [320,390,768,1440]) {
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.getByRole('tab',{name:'번역 자막',exact:true}).click();
    await page.getByLabel('자막 검색').fill('두 번째');
    assert.equal(await page.locator('.preview-cue').count(),1);
    await page.locator('.preview-cue').click();
    try {await page.waitForFunction(()=>Math.abs(document.querySelector('.player-surface video').currentTime-3)<0.1);} catch(e){console.log(await page.locator('video').evaluate(v=>({time:v.currentTime,duration:v.duration,ready:v.readyState,error:v.error?.message,seekable:Array.from({length:v.seekable.length},(_,i)=>[v.seekable.start(i),v.seekable.end(i)])})));throw e;}
    await page.getByTitle('검색 지우기').click();
    await page.screenshot({path:`data/detail-${width}.png`,fullPage:true});
    await page.getByRole('tab',{name:'전사록',exact:true}).click();
  }
  await page.locator('video').evaluate(v=>{v.muted=true;return v.play();});
  await page.waitForFunction(()=>document.querySelector('video').currentTime>3.1);
  await page.locator('video').evaluate(v=>v.pause());
  await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
  await page.getByLabel('영상 설명 (선택)').fill('preserved draft');
  await page.keyboard.press('Escape');
  await page.getByTitle('작업 목록으로').click();
  await page.locator('.job-title').click();
  await page.goBack();
  await page.getByRole('heading',{name:'전체 작업',exact:true}).waitFor();
  await page.getByRole('button',{name:'영상 추가',exact:true}).filter({visible:true}).click();
  assert.equal(await page.getByLabel('영상 설명 (선택)').inputValue(),'preserved draft');
  await page.keyboard.press('Escape');
  job.status='TRANSLATING';
  preview={...preview,video_available:false,tracks:{...preview.tracks,translated:{available:false,partial:false,cues:[]}}};
  await page.goto(url+'/#/jobs/'+id);
  await page.getByRole('tab',{name:'번역 자막',exact:true}).click();
  await page.getByText('번역 결과를 기다리고 있습니다.').waitFor();
  assert.equal(await page.locator('video').count(),0);
  await page.getByRole('tab',{name:'전사록',exact:true}).click();
  await page.getByText('First sentence',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Detail page: responsive layout, video playback/seek, tabs/search, deep link/back, draft preservation and pending results passed.');
} finally {await browser.close();}
