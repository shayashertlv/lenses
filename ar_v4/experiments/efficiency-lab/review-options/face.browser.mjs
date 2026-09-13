import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {after,before,test} from 'node:test';
import {chromium} from 'playwright';
import {createServer} from 'vite';

// Dedicated ephemeral localhost port; the real module worker, pinned model and
// installed SDK are served from this checkout. No public deployment is changed.
let browser,server,base;
before(async()=>{
  server=await createServer({configFile:false,root:fileURLToPath(new URL('../../../',import.meta.url)),
    cacheDir:'node_modules/.vite-review-face-test',appType:'mpa',
    server:{host:'127.0.0.1',port:0,strictPort:false},worker:{format:'es'},optimizeDeps:{include:['@mediapipe/tasks-vision']}});
  await server.listen();base=`http://127.0.0.1:${server.httpServer.address().port}`;
  browser=await chromium.launch({headless:true,args:['--enable-gpu','--use-angle=d3d11']});
});
after(async()=>{await browser?.close();await server?.close();});
test('real CPU worker detects the exact downsample, reports CPU timing and restarts with fresh ownership',{timeout:120_000},async t=>{
  const page=await browser.newPage();t.after(()=>page.close());await page.goto(base+'/tests/fixtures/face-a.jpg');
  const result=await page.evaluate(async()=>{
    const {ForcedDelegateDetectorClient}=await import('/experiments/efficiency-lab/review-options/face-detector.ts');
    const image=new Image();image.src='/tests/fixtures/face-a.jpg';await image.decode();
    const source=document.createElement('canvas');source.width=720;source.height=1280;
    const ctx=source.getContext('2d',{alpha:false,willReadFrequently:true,colorSpace:'srgb'});
    ctx.fillStyle='#b0aaa0';ctx.fillRect(0,0,720,1280);
    const scale=720/image.naturalWidth,h=Math.round(image.naturalHeight*scale);ctx.drawImage(image,0,Math.round((1280-h)/2),720,h);
    const detectorCanvas=document.createElement('canvas');detectorCanvas.width=360;detectorCanvas.height=640;
    detectorCanvas.getContext('2d',{alpha:false,colorSpace:'srgb'}).drawImage(source,0,0,360,640);
    const hash=async()=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',ctx.getImageData(0,0,720,1280).data)),n=>n.toString(16).padStart(2,'0')).join('');
    const before=await hash(),rows=[];
    for(let pass=0;pass<2;pass++){
      const detector=new ForcedDelegateDetectorClient('CPU',{sessionNonce:'cpu-browser-session-'+pass});
      try{
        await detector.initialize(new AbortController().signal);
        const bitmap=await createImageBitmap(detectorCanvas),detection=await detector.detect(bitmap,100+pass);
        rows.push({delegate:detector.delegate,timing:detector.lastTiming,landmarks:detection.landmarks.length,
          matrixLength:detection.matrix?.length??0,closedWidth:bitmap.width});
        const lateBitmap=await createImageBitmap(detectorCanvas),pending=detector.detect(lateBitmap,200+pass);
        detector.close();let closedError=null;try{await pending;}catch(error){closedError=error.message;}
        rows[pass].closedError=closedError;rows[pass].closedTiming=detector.lastTiming;rows[pass].lateBitmapWidth=lateBitmap.width;
      }finally{detector.close();}
    }
    const abort=new AbortController(),cancelled=new ForcedDelegateDetectorClient('CPU',{sessionNonce:'cpu-browser-cancelled'});
    const starting=cancelled.initialize(abort.signal);abort.abort();let abortName=null;
    try{await starting;}catch(error){abortName=error.name;}finally{cancelled.close();}
    return{before,after:await hash(),rows,abortName};
  });
  assert.equal(result.before,result.after);assert.equal(result.abortName,'AbortError');assert.equal(result.rows.length,2);
  for(const row of result.rows){
    assert.equal(row.delegate,'CPU');assert.equal(row.timing.delegate,'CPU');assert.equal(row.timing.workerTimingStatus,'valid');
    assert.equal(row.timing.width,360);assert.equal(row.timing.height,640);
    assert.equal(row.landmarks,478);assert.equal(row.matrixLength,16);assert.equal(row.closedWidth,0);
    assert.match(row.closedError,/closed/);assert.equal(row.closedTiming,null);assert.equal(row.lateBitmapWidth,0);
  }
  assert.notEqual(result.rows[0].timing.sessionNonce,result.rows[1].timing.sessionNonce);
});
