import {expect,test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
import type {FrameSample} from '../frame-profiler.ts';
import type {} from '../live-main.ts';

interface ObservedFaceWorker {terminated:boolean;delegate:'GPU'|'CPU'|null;detects:number;
  lastReplyTiming:{schema:string;delegate:string;width:number;height:number}|null;}
interface SwitchCamera {
  streams:MediaStream[];workers:ObservedFaceWorker[];blocked:boolean;released:number;release():void;
}
declare global {interface Window {startupSwitchCamera:SwitchCamera;}}
const entry='/ar_testing/experiments/efficiency-lab/live.html?study=fps-review&candidate=face-cpu';

/** Gate only delivery of one real face-worker ready reply. The model, worker
 * computation, detector responses, camera cadence and production deadlines
 * remain real. Releasing after stop exercises the old client's stale callback.
 */
async function installCamera(page:Page,delegate:'GPU'|'CPU'):Promise<void> {
  const fixture=await readFile(new URL('../../../tests/fixtures/face-a.jpg',import.meta.url));
  await page.route('**/ar_testing/startup-switch-camera.jpg',route=>route.fulfill({contentType:'image/jpeg',body:fixture}));
  await page.addInitScript(({gateDelegate})=>{
    const state:SwitchCamera={streams:[],workers:[],blocked:false,released:0,release(){}};window.startupSwitchCamera=state;
    const canvas=document.createElement('canvas');canvas.width=720;canvas.height=1280;
    const ctx=canvas.getContext('2d')!,image=new Image();
    const ready=new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=reject;});
    image.src='/ar_testing/startup-switch-camera.jpg';
    const draw=()=>{if(image.complete&&image.naturalWidth){ctx.fillStyle='#808080';ctx.fillRect(0,0,720,1280);ctx.drawImage(image,0,400,720,480);}};
    setInterval(draw,33);
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:async()=>{
      await ready;draw();const stream=canvas.captureStream(30);state.streams.push(stream);return stream;
    }});
    const Native=window.Worker;let armed=true;
    class ObservedWorker extends Native {
      private readonly observation:ObservedFaceWorker;
      constructor(url:string|URL,options?:WorkerOptions){
        super(url,options);this.observation={terminated:false,delegate:null,detects:0,lastReplyTiming:null};state.workers.push(this.observation);
        this.addEventListener('message',(event:MessageEvent<Record<string,unknown>>)=>{
          if(event.data.type==='result'&&this.observation.delegate&&event.data.timing){
            const timing=event.data.timing as {schema:string;delegate:string;width:number;height:number};
            this.observation.lastReplyTiming={schema:timing.schema,delegate:timing.delegate,width:timing.width,height:timing.height};
          }
          if(!armed||this.observation.delegate!==gateDelegate||event.data.type!=='ready')return;
          armed=false;state.blocked=true;event.stopImmediatePropagation();const reply=event.data;
          state.release=()=>{if(!state.blocked)return;state.blocked=false;state.released++;
            this.dispatchEvent(new MessageEvent('message',{data:reply}));};
        });
      }
      override postMessage(message:unknown,transfer:Transferable[]|StructuredSerializeOptions=[]):void {
        const value=message as {type?:string;modelUrl?:string;delegate?:'GPU'|'CPU'};
        if(value.type==='initialize'&&value.modelUrl?.includes('face_landmarker.task'))this.observation.delegate=value.delegate??null;
        if(value.type==='detect')this.observation.detects++;
        if(Array.isArray(transfer))super.postMessage(message,transfer);else super.postMessage(message,transfer);
      }
      override terminate():void {this.observation.terminated=true;super.terminate();}
    }
    window.Worker=ObservedWorker;
  },{gateDelegate:delegate});
}
const closed=(page:Page)=>page.evaluate(()=>window.startupSwitchCamera.streams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))
  &&window.startupSwitchCamera.workers.every(worker=>worker.terminated));
const samples=(page:Page,after=0):Promise<FrameSample[]>=>page.evaluate(serial=>window.arPerformanceProfiler.samplesAfter(serial),after);
async function cpuPublished(page:Page,after=0):Promise<FrameSample[]> {
  await expect.poll(()=>page.evaluate(serial=>window.arPerformanceProfiler.samplesAfter(serial)
    .filter(row=>row.sessionId===window.hairLivePreview.diagnostics().sessionId&&row.pipeline==='face-cpu'&&row.hasFace).length,after)).toBeGreaterThanOrEqual(3);
  const rows=await samples(page,after);
  for(const row of rows.filter(row=>row.pipeline==='face-cpu')){
    expect(row.faceDelegate).toBe('CPU');expect(row.native?.['review.faceDelegate']).toBe('CPU');
    expect(row.sourceWidth).toBe(720);expect(row.sourceHeight).toBe(1280);
    expect(row.faceWorkerMs).not.toBeNull();expect(row.faceExtractionMs).not.toBeNull();expect(row.faceWorkerValidationMs).not.toBeNull();
  }
  // lastTiming intentionally becomes null while the next inference is pending.
  // Retain the native reply and validate its published timing fields instead.
  const timings=await page.evaluate(()=>window.startupSwitchCamera.workers.filter(worker=>worker.delegate==='CPU'&&worker.lastReplyTiming)
    .map(worker=>worker.lastReplyTiming));
  expect(timings.length).toBeGreaterThan(0);
  for(const timing of timings)expect(timing).toEqual({schema:'face-worker-timing-v1',delegate:'CPU',width:360,height:640});
  return rows;
}
async function cleanup(page:Page):Promise<void> {
  await page.evaluate(()=>window.startupSwitchCamera.release());
  if(await page.locator('#stop').isVisible())await page.click('#stop');
  await expect.poll(()=>closed(page)).toBe(true);
}

test('startup switch: G readiness held during CPU selection publishes CPU from the first frame',async({page})=>{
  test.setTimeout(120_000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await installCamera(page,'GPU');await page.goto(entry);await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await page.click('#start');
  try {
    await expect.poll(()=>page.evaluate(()=>window.startupSwitchCamera.blocked)).toBe(true);
    expect(await samples(page)).toHaveLength(0);await page.selectOption('#pipeline-select','face-cpu');
    expect(await page.evaluate(()=>window.hairLivePreview.diagnostics().requestedPipeline)).toBe('face-cpu');
    await page.evaluate(()=>window.startupSwitchCamera.release());const rows=await cpuPublished(page);
    expect(rows[0]!.pipeline).toBe('face-cpu');expect(rows.every(row=>row.pipeline==='face-cpu'&&row.faceDelegate==='CPU')).toBe(true);
    const workers=await page.evaluate(()=>window.startupSwitchCamera.workers);
    expect(workers.some(worker=>worker.delegate==='GPU')).toBe(true);expect(workers.some(worker=>worker.delegate==='CPU'&&worker.detects>0)).toBe(true);
    expect(workers.filter(worker=>worker.delegate==='GPU').every(worker=>worker.detects===0)).toBe(true);
    await writeFile(test.info().outputPath('startup-selection-receipt.json'),JSON.stringify({first:rows[0],rows,workers,errors},null,2));
  }finally{await cleanup(page);}
  expect(errors).toEqual([]);
});

test('startup switch: stop revokes a withheld ready reply and CPU restart owns every publication',async({page})=>{
  test.setTimeout(120_000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await installCamera(page,'GPU');await page.goto(entry);await page.click('#start');
  try {
    await expect.poll(()=>page.evaluate(()=>window.startupSwitchCamera.blocked)).toBe(true);
    const stoppedSession=await page.evaluate(()=>window.hairLivePreview.diagnostics().sessionId);
    await page.selectOption('#pipeline-select','face-cpu');await page.click('#stop');
    await expect(page.locator('.stage')).toHaveAttribute('data-state','idle');await expect.poll(()=>closed(page)).toBe(true);
    expect(await samples(page)).toHaveLength(0);
    const oldWorkerCount=await page.evaluate(()=>window.startupSwitchCamera.workers.length);
    await page.click('#start');await page.evaluate(()=>window.startupSwitchCamera.release());
    const rows=await cpuPublished(page);
    expect(rows.every(row=>row.sessionId!==stoppedSession&&row.pipeline==='face-cpu'&&row.faceDelegate==='CPU')).toBe(true);
    expect(await page.evaluate(count=>window.startupSwitchCamera.workers.slice(0,count).every(worker=>worker.terminated&&worker.detects===0),oldWorkerCount)).toBe(true);
    await writeFile(test.info().outputPath('startup-stop-restart-receipt.json'),JSON.stringify({stoppedSession,rows,oldWorkerCount,errors},null,2));
  }finally{await cleanup(page);}
  expect(errors).toEqual([]);
});

/** Inspect the stored telemetry entry in the actual downloaded ZIP; archive
 * CRC/central-directory behavior has its independent production archive suite.
 */
function telemetryFromZip(bytes:Buffer):Record<string,unknown> {
  let offset=0;
  while(offset+30<=bytes.length&&bytes.readUInt32LE(offset)===0x04034b50){
    expect(bytes.readUInt16LE(offset+8)).toBe(0);
    const size=bytes.readUInt32LE(offset+18),nameLength=bytes.readUInt16LE(offset+26),extra=bytes.readUInt16LE(offset+28);
    const name=bytes.toString('utf8',offset+30,offset+30+nameLength),start=offset+30+nameLength+extra;
    expect(start+size).toBeLessThanOrEqual(bytes.length);
    if(name==='telemetry.json')return JSON.parse(bytes.toString('utf8',start,start+size)) as Record<string,unknown>;
    offset=start+size;
  }
  throw new Error('The retained partial ZIP is missing telemetry.json.');
}

test('startup switch: stopping a continuous run during CPU initialization retains ZIP before correct manual resume',async({page})=>{
  test.setTimeout(180_000);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await installCamera(page,'CPU');await page.goto(entry);await page.click('#start');
  try {
    await expect.poll(()=>page.evaluate(()=>window.arPerformanceProfiler.samplesAfter(0).filter(row=>row.hasFace&&row.hasMask).length)).toBeGreaterThanOrEqual(3);
    await expect(page.locator('#continuous-start')).toBeEnabled();await expect(page.locator('#continuous-video')).not.toBeChecked();
    const downloadPromise=page.waitForEvent('download',{timeout:150_000});let downloaded=false;void downloadPromise.then(()=>{downloaded=true;});
    await page.click('#continuous-start');
    // This reaches the real second window after the unchanged G warmup and
    // 30-second measurement. No production clock or timer is shortened.
    await expect.poll(()=>page.evaluate(()=>window.startupSwitchCamera.blocked),{timeout:90_000}).toBe(true);
    expect(await page.evaluate(()=>window.arContinuousComparison.status()?.pipeline)).toBe('face-cpu');
    const before=(await samples(page)).at(-1)?.serial??0;
    await page.click('#continuous-stop');
    await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
    expect(downloaded,'Finalization must wait for the owned CPU initialization before resuming the mirror.').toBe(false);
    expect((await samples(page,before)).filter(row=>row.pipeline==='face-cpu')).toHaveLength(0);
    await page.evaluate(()=>window.startupSwitchCamera.release());
    const download=await downloadPromise;expect(download.suggestedFilename()).toMatch(/^ar-mobile-comparison-.*\.zip$/);
    const filename=test.info().outputPath('stop-during-cpu-startup.zip');await download.saveAs(filename);
    const report=telemetryFromZip(await readFile(filename));expect(report.partial).toBe(true);expect(report.completed).toBe(false);
    expect(report.protocol).toMatchObject({order:['g','face-cpu','face-cpu','g'],warmupMs:5000,measureMs:30000});
    const rows=await cpuPublished(page,before);expect(rows.every(row=>row.pipeline==='face-cpu'&&row.faceDelegate==='CPU')).toBe(true);
    expect(await page.evaluate(()=>window.arContinuousComparison.report())).toMatchObject({partial:true,completed:false});
    await writeFile(test.info().outputPath('continuous-stop-cpu-receipt.json'),JSON.stringify({report,rows,errors},null,2));
  }finally{await cleanup(page);}
  expect(errors).toEqual([]);
});
