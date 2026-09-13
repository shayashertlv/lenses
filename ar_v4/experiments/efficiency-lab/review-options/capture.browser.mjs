import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {after,before,test} from 'node:test';
import {chromium} from 'playwright';

// No dev server, port, personal recording, or production fixture is required.
// Browser gets the actual implementation with only TypeScript syntax removed.
const source=stripTypeScriptTypes(await readFile(new URL('./video-frame-capture.ts',import.meta.url),'utf8'),{mode:'strip'});
const script=source.replace(/^export /gm,'')+'\nglobalThis.ReviewCapture = ExactVideoFrameCapture;';
let browser;
before(async()=>{browser=await chromium.launch({headless:true,args:['--enable-gpu','--use-angle=d3d11']});});
after(async()=>{await browser?.close();});
async function pageForTest(t){
  const page=await browser.newPage();t.after(()=>page.close());
  await page.addScriptTag({content:script});
  await page.evaluate(async()=>{
    const canvas=document.createElement('canvas');canvas.width=720;canvas.height=1280;
    const ctx=canvas.getContext('2d',{alpha:false,colorSpace:'srgb'});
    ctx.fillStyle='rgb(224,32,16)';ctx.fillRect(0,0,360,640);
    ctx.fillStyle='rgb(16,192,64)';ctx.fillRect(360,0,360,640);
    ctx.fillStyle='rgb(48,80,208)';ctx.fillRect(0,640,360,640);
    ctx.fillStyle='rgb(176,160,144)';ctx.fillRect(360,640,360,640);
    const video=document.createElement('video');video.muted=true;video.playsInline=true;
    video.srcObject=canvas.captureStream(30);document.body.append(video);await video.play();
    setInterval(()=>ctx.drawImage(canvas,0,0),33);
    await new Promise(resolve=>video.requestVideoFrameCallback(resolve));
    globalThis.captureFixture={video,source:canvas,sourceContext:ctx};
  });return page;
}
test('real Chromium portrait VideoFrame.copyTo RGBA produces exact downstream canvas bytes',{timeout:30_000},async t=>{
  const page=await pageForTest(t);
  const result=await page.evaluate(async()=>{
    const {video}=globalThis.captureFixture;
    const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;
    const ctx=canvas.getContext('2d',{alpha:false,willReadFrequently:true,colorSpace:'srgb'});
    const capture=new globalThis.ReviewCapture();
    const result=capture.capture(video,canvas,ctx,performance.now(),()=>true);
    const telemetry=await result.ready,readback=ctx.getImageData(0,0,canvas.width,canvas.height);
    let unequal=0;for(let index=0;index<result.rgba.data.length;index++)if(result.rgba.data[index]!==readback.data[index])unequal++;
    const bytes=result.rgba.data,sample=(x,y)=>Array.from(bytes.slice((y*canvas.width+x)*4,(y*canvas.width+x)*4+4));
    return{telemetry,unequal,stats:capture.stats,samples:[sample(100,100),sample(600,100),sample(100,900),sample(600,900)]};
  });
  assert.equal(result.telemetry.actualPath,'video-frame-copy',JSON.stringify(result.telemetry));
  assert.equal(result.telemetry.fallbackReason,null);assert.equal(result.telemetry.width,720);assert.equal(result.telemetry.height,1280);
  assert.equal(result.telemetry.copiedBytes,720*1280*4);assert.equal(result.unequal,0);
  assert.equal(result.stats.framesClosed,1);assert.equal(result.stats.copied,1);
  assert.ok(result.samples[0][0]>result.samples[0][1]);assert.ok(result.samples[1][1]>result.samples[1][0]);
  assert.ok(result.samples[2][2]>result.samples[2][0]);assert.equal(result.samples[3][3],255);
});
test('real frozen VideoFrame fallback retains old image after synthetic camera changes',{timeout:30_000},async t=>{
  const page=await pageForTest(t);
  const result=await page.evaluate(async()=>{
    const {video,sourceContext}=globalThis.captureFixture;
    const canvas=document.createElement('canvas');canvas.width=720;canvas.height=1280;
    const ctx=canvas.getContext('2d',{alpha:false,willReadFrequently:true,colorSpace:'srgb'});
    let rejectCopy,closeCount=0;
    const capture=new globalThis.ReviewCapture({createFrame:(source,timestamp)=>{
      const frame=new VideoFrame(source,{timestamp,alpha:'discard'}),originalClose=frame.close.bind(frame);
      Object.defineProperty(frame,'copyTo',{value:()=>new Promise((_,reject)=>{rejectCopy=reject;})});
      Object.defineProperty(frame,'close',{value:()=>{closeCount++;originalClose();}});return frame;
    }});
    const captured=capture.capture(video,canvas,ctx,performance.now(),()=>true);
    sourceContext.fillStyle='rgb(0,0,0)';sourceContext.fillRect(0,0,720,1280);
    await new Promise(resolve=>video.requestVideoFrameCallback(resolve));
    rejectCopy(new TypeError('RGBA copy unsupported simulation'));
    const telemetry=await captured.ready,readback=ctx.getImageData(0,0,720,1280).data;
    let unequal=0;for(let i=0;i<readback.length;i++)if(readback[i]!==captured.rgba.data[i])unequal++;
    return{telemetry,closeCount,unequal,red:captured.rgba.data[(100*720+100)*4]};
  });
  assert.equal(result.telemetry.actualPath,'canvas-video-frame-fallback');assert.equal(result.telemetry.fallbackReason,'rgba-copy-failed');
  assert.equal(result.closeCount,1);assert.equal(result.unequal,0);assert.ok(result.red>180);
});
test('real browser API absence reports fallback; revoked copy closes before any publication write',{timeout:30_000},async t=>{
  const page=await pageForTest(t);
  const result=await page.evaluate(async()=>{
    const {video}=globalThis.captureFixture;
    const canvas=document.createElement('canvas');canvas.width=720;canvas.height=1280;
    const ctx=canvas.getContext('2d',{alpha:false,willReadFrequently:true,colorSpace:'srgb'});
    const unavailable=new globalThis.ReviewCapture({createFrame:null});
    const fallback=await unavailable.capture(video,canvas,ctx,performance.now(),()=>true).ready;
    let release,closeCount=0;
    const gate=new Promise(resolve=>{release=resolve;});
    const candidate=new globalThis.ReviewCapture({createFrame:(source,timestamp)=>{
      const frame=new VideoFrame(source,{timestamp,alpha:'discard'}),copy=frame.copyTo.bind(frame),close=frame.close.bind(frame);
      Object.defineProperty(frame,'copyTo',{value:async(destination,options)=>{await gate;return copy(destination,options);}});
      Object.defineProperty(frame,'close',{value:()=>{closeCount++;close();}});return frame;
    }});
    const captured=candidate.capture(video,canvas,ctx,performance.now(),()=>true);
    const extra=candidate.capture(video,canvas,ctx,performance.now(),()=>true);
    candidate.stop();ctx.fillStyle='black';ctx.fillRect(0,0,720,1280);release();
    let failure=null;try{await captured.ready;}catch(error){failure=error.name;}
    await candidate.whenIdle();
    return{fallback,failure,closeCount,extra,stats:candidate.stats,pixel:Array.from(ctx.getImageData(100,100,1,1).data)};
  });
  assert.equal(result.fallback.actualPath,'canvas-video-fallback');assert.equal(result.fallback.fallbackReason,'video-frame-unavailable');
  assert.equal(result.failure,'AbortError');assert.equal(result.closeCount,1);assert.equal(result.extra,null);
  assert.equal(result.stats.revoked,1);assert.equal(result.stats.busy,false);assert.deepEqual(result.pixel,[0,0,0,255]);
});
