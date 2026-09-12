import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const media=await readFile('data/detail-test.mp4');
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],created=[],uploads=[];let jobs=[],chunkActive=0,maxChunkActive=0;
  page.on('pageerror',e=>errors.push(e.message));
  const asset={asset_id:'a'.repeat(32),original_filename:'sample-video.mp4',size_bytes:media.length,created_at:'2026-09-12T00:00:00Z',media:{duration:5,width:640,height:360,has_audio:true},provenance:{kind:'upload'}};
  const subtitle={artifact_id:'c'.repeat(32),filename:'external.srt',language:'en',cue_count:2,created_at:asset.created_at,provenance:{kind:'attachment'}};
  await page.route('**/api/**',async route=>{
    const url=new URL(route.request().url()),path=url.pathname,method=route.request().method();
    const body=()=>route.request().postDataJSON();
    if(path.endsWith('/auth/session'))return route.fulfill({json:{enabled:false,user:null}});
    if(path==='/api/videos')return route.fulfill({json:{videos:[asset]}});
    if(path==='/api/jobs')return route.fulfill({json:{jobs}});
    if(path==='/api/video-uploads'){
      if(method==='POST'){const data=body();assert.deepEqual(Object.keys(data).sort(),['filename','request_id','size']);const upload={upload_id:String(uploads.length+1).repeat(32),filename:data.filename,expected_size:data.size,uploaded_bytes:0,status:'UPLOADING',created_at:asset.created_at};uploads.push(upload);return route.fulfill({status:201,json:upload});}
      return route.fulfill({json:{uploads}});
    }
    if(path.startsWith('/api/video-uploads/')){
      const id=path.split('/')[3],upload=uploads.find(u=>u.upload_id===id);assert.ok(upload);
      if(path.endsWith('/chunks')){chunkActive++;maxChunkActive=Math.max(maxChunkActive,chunkActive);await new Promise(r=>setTimeout(r,600));upload.uploaded_bytes+=route.request().postDataBuffer().length;chunkActive--;return route.fulfill({json:upload});}
      if(path.endsWith('/complete')){upload.status='READY';upload.asset_id=asset.asset_id;return route.fulfill({json:{status:'ready',video:asset}});}
      return route.fulfill({json:{...upload,resumable:upload.status==='UPLOADING'}});
    }
    if(path.endsWith('/workflow-plan')){const b=body();let stages=['analyze'];if(['full','transcribe','transcribe_translate'].includes(b.template))stages.push('extract_audio','preprocess_audio','transcribe');if(['full','translate','transcribe_translate'].includes(b.template))stages.push('translate');if(b.template!=='extract_audio')stages.push('generate_subtitle');if(['full','encode'].includes(b.template))stages.push('encode','validate_video');return route.fulfill({json:{stages,reused:[],paid_stages:stages.filter(s=>['transcribe','translate'].includes(s))}});}
    if(path.endsWith('/audio-inputs'))return route.fulfill({json:{audio_inputs:[]}});
    if(path===`/api/videos/${asset.asset_id}/subtitles`)return route.fulfill({json:{subtitles:[subtitle]}});
    if(path===`/api/videos/${asset.asset_id}/jobs`){created.push(body());const job={job_id:'b'.repeat(32),status:'COMPLETED',original_filename:asset.original_filename,source_language:'en',target_language:'ko',quality_profile:'balanced',expected_size:media.length,uploaded_bytes:media.length,created_at:asset.created_at,error:null,metadata:{asset_id:asset.asset_id,workflow:{template:body().template},result_files:['original.srt'],duration:5}};jobs=[job];return route.fulfill({status:201,json:job});}
    if(path===`/api/videos/${asset.asset_id}`)return route.fulfill({json:{video:asset,jobs}});
    if(path.endsWith('/preview'))return route.fulfill({json:{video_available:false,expired:false,tracks:{original:{available:true,partial:false,cues:[{start:1,end:2,text:'Original dialogue'}]},translated:{available:false,partial:false,cues:[]}}}});
    if(path===`/api/jobs/${'b'.repeat(32)}`)return route.fulfill({json:jobs[0]});
    if(path.endsWith('/stream')){const match=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/);const start=Number(match?.[1]||0),end=match?.[2]?Number(match[2]):media.length-1;return route.fulfill({status:match?206:200,body:media.subarray(start,end+1),contentType:'video/mp4',headers:{'Accept-Ranges':'bytes',...(match?{'Content-Range':`bytes ${start}-${end}/${media.length}`}:{})}});}
    return route.fulfill({status:503,json:{detail:'Mock API: request not enabled'}});
  });
  const url=process.env.WEB_URL||'http://127.0.0.1:5183';
  await page.goto(url);
  await page.getByRole('heading',{name:'원본 보관함',exact:true}).waitFor();
  await page.locator('.asset-row').waitFor();
  await page.screenshot({path:'data/mvp2-library-desktop.png',fullPage:true});
  await page.locator('.asset-row').click();
  await page.waitForFunction(()=>document.querySelector('.asset-overview video')?.readyState>=1);
  await page.screenshot({path:'data/mvp2-asset-desktop.png',fullPage:true});
  await page.getByRole('button',{name:'새 작업',exact:true}).click();
  await page.getByLabel('작업 종류').selectOption('transcribe');
  await page.getByRole('button',{name:'작업 시작',exact:true}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('.workflow-footer .primary')?.disabled);
  await page.screenshot({path:'data/mvp2-workflow-desktop.png',fullPage:true});
  await page.getByRole('button',{name:'작업 시작',exact:true}).click();
  await page.locator('.job-detail h1').waitFor();
  assert.equal(created[0].template,'transcribe');
  await page.waitForFunction(()=>document.querySelector('.player-surface video')?.readyState>=1);
  assert.match(await page.locator('.player-surface video').getAttribute('src'),/\/videos\//);
  await page.getByTitle('작업 목록으로').click();
  for(const width of [320,390,768,1440]){
    await page.setViewportSize({width,height:900});
    await page.getByRole('button',{name:'새 작업',exact:true}).click();
    await page.locator('.workflow-modal[open]').waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`page overflow at ${width}`);
    assert.ok(await page.locator('.workflow-modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`modal overflow at ${width}`);
    if(width===390)await page.screenshot({path:'data/mvp2-workflow-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'취소',exact:true}).click();
  }
  await page.goto(url);
  await page.locator('input[type=file]').first().setInputFiles([{name:'one.mp4',mimeType:'video/mp4',buffer:Buffer.alloc(100)},{name:'two.mp4',mimeType:'video/mp4',buffer:Buffer.alloc(100)}]);
  await page.waitForFunction(()=>document.querySelector('.upload-shelf')?.textContent.includes('one.mp4'));
  await page.locator('.asset-row').click();
  await page.waitForTimeout(1800);
  assert.equal(uploads.length,2);assert.ok(uploads.every(u=>u.asset_id));assert.equal(maxChunkActive,1);
  assert.equal(await page.locator('input[type=file]').first().inputValue(),'');
  assert.deepEqual(errors,[]);
  console.log('MVP2 browser passed: library/detail, workflow creation, original playback, 4 widths, serial uploads across navigation.');
}finally{await browser.close();}
