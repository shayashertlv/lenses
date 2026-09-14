import assert from 'node:assert/strict';
import test from 'node:test';
import {FrameProfiler,summarize} from '../runtime/experiments/speed-lab/frame-profiler.ts';
import type {FrameInput,ProfileSummary} from '../runtime/experiments/speed-lab/frame-profiler.ts';

function frame(at:number,sequence=1,patch:Partial<FrameInput>={}):FrameInput {
  return {sessionId:'session',sequence,pipeline:'combined',variant:'hair',capturedAtMs:at-20,publishedAtMs:at,
    videoPresentedFrames:sequence*3,videoMediaTime:at/1000,videoPresentationTimeMs:null,cameraSettingFps:30,
    sourceWidth:640,sourceHeight:427,sourceDrawMs:1,detectorDrawMs:1,sourceReadbackMs:1,sourceHashMs:2,
    faceBitmapMs:1,faceRequestWallMs:8,faceInferenceMs:6,faceWorkerMs:7,faceExtractionMs:.5,
    faceWorkerValidationMs:.5,faceClientValidationMs:.1,faceTransportSchedulingMs:1,prerequisitesWaitMs:8,
    detectionHashMs:1,prepareMs:5,finishMs:3,renderMs:8,totalMs:20,schedulerWaitMs:10,hairWaitMs:0,
    hairInferenceMs:9,hairExtractionMs:1,hasFace:true,hasMask:true,maskMode:'category-only',fallback:null,changedPixels:3,
    faceDelegate:'GPU',hairDelegate:'GPU',gpuRenderer:'test',cleanCameraMs:1,composeMs:1,continuityMs:.5,
    finalChecksMs:.5,publishMs:1,native:{readbacks:1},...patch};
}
const live=(p:FrameProfiler,at:number)=>p.liveSummary('session','combined','hair',at);
function headline(s:ProfileSummary){const {processing:_processing,frameInterval:_frameInterval,stages:_stages,...result}=s;return result;}

test('incremental headlines equal full 10-second distributions across irregular cadence, eviction and incomplete work',()=>{
  const p=new FrameProfiler(95,true);let at=100;
  for(let sequence=1;sequence<=720;sequence++) {
    at+=sequence%9===0?1400:37+(sequence%17)*2;
    p.add(frame(at,sequence,{hasFace:sequence%5!==0,hasMask:sequence%7!==0,changedPixels:sequence%3,
      videoPresentedFrames:sequence%13===0?null:sequence*3,sourceWidth:sequence<400?640:720}));
    assert.deepEqual(headline(live(p,at)),headline(summarize(p.recent('session','combined','hair'))));
  }
  assert.equal(p.samplesAfter(0).length,95);
});

test('cached UI percentiles have a bounded age while FPS, mask coverage and every exported row remain current',()=>{
  const p=new FrameProfiler(4096,true);
  p.add(frame(100,1));assert.equal(live(p,100).processing!.p95,20);
  const second=p.add(frame(200,2,{hasFace:false,hasMask:false,totalMs:199,faceInferenceMs:null}));
  const cached=live(p,200);assert.equal(cached.frames,2);assert.equal(cached.processedFps,10);assert.equal(cached.trackedFrames,1);
  assert.equal(cached.processing!.p95,20);
  p.finishPublicationBookkeeping(second,1.25,1);
  assert.equal(second.native!['fps.bookkeeping.summaryBuilds'],1);assert.equal(second.native!['fps.bookkeeping.summaryReuses'],1);
  assert.equal(p.samplesAfter(0)[1]!.totalMs,199);assert.equal(summarize(p.samplesAfter(0)).processing!.p95,199);
  assert.equal(live(p,599).processing!.p95,20);assert.equal(live(p,600).processing!.p95,199);
  assert.deepEqual(JSON.parse(p.exportJSON()).samples.map((row:FrameInput)=>row.totalMs),[20,199]);
});

test('held/stop invalidation rejects stale accounting and fresh session or resumed frame starts a new readout',()=>{
  const p=new FrameProfiler(4096,true),first=p.add(frame(100));live(p,100);
  p.invalidateLiveSummary();assert.equal(live(p,110).frames,0);
  assert.throws(()=>p.finishPublicationBookkeeping(first,1,1),/ownership expired/);
  const second=p.add(frame(200,2));assert.equal(live(p,200).frames,1);p.finishPublicationBookkeeping(second,1,1);
  p.invalidateLiveSummary();p.add(frame(250,1,{sessionId:'fresh-session'}));
  assert.equal(live(p,250).frames,0);assert.equal(p.liveSummary('fresh-session','combined','hair',250).frames,1);
  assert.equal(p.samplesAfter(0).length,3);
});

test('pipeline and hair changes cannot mix old readout samples, even when changing back within 500ms',()=>{
  const p=new FrameProfiler(4096,true);p.add(frame(100));p.add(frame(200,2));assert.equal(live(p,200).frames,2);
  p.add(frame(250,3,{pipeline:'source'}));assert.equal(live(p,250).frames,0);
  assert.equal(p.liveSummary('session','source','hair',250).frames,1);
  p.add(frame(300,4));assert.equal(live(p,300).frames,1);
  p.add(frame(350,5,{variant:'accepted',hasMask:false}));assert.equal(live(p,350).frames,0);
  const hairOff=p.liveSummary('session','combined','accepted',350);assert.equal(hairOff.frames,1);assert.equal(hairOff.hairCoverage,null);
  p.add(frame(400,6));assert.equal(live(p,400).frames,1);
});

test('private cached summaries and raw telemetry resist mutation through every exposed getter',()=>{
  const p=new FrameProfiler(4096,true),input=frame(100);const returned=p.add(input),summary=live(p,100);
  input.native!.readbacks=90;returned.native!.readbacks=91;returned.totalMs=92;
  const recent=p.recent('session','combined','hair');recent[0]!.totalMs=93;recent[0]!.native!.readbacks=94;
  const after=p.samplesAfter(0);after[0]!.totalMs=95;after[0]!.native!.readbacks=96;
  assert.throws(()=>{summary.processing!.p95=1000;},TypeError);
  assert.throws(()=>{summary.stages.renderMs!.median=1000;},TypeError);
  assert.throws(()=>{summary.source!.width=1;},TypeError);
  assert.throws(()=>{summary.frames=1000;},TypeError);
  assert.equal(live(p,110).processing!.p95,20);assert.equal(p.samplesAfter(0)[0]!.totalMs,20);
  assert.equal(p.samplesAfter(0)[0]!.native!.readbacks,1);
});

test('publication accounting belongs to exactly the latest indexed frame and exports only allowed numeric diagnostics',()=>{
  const p=new FrameProfiler(4096,true),old=p.add(frame(100));live(p,100);
  const row=p.add(frame(200,2,{native:{readbacks:1,sourceIdentity:'live:source:session:2', 'x.detectionIdentity':'secret'}}));live(p,200);
  assert.throws(()=>p.finishPublicationBookkeeping(old,1,1),/ownership expired/);
  assert.throws(()=>p.finishPublicationBookkeeping({...row,sessionId:'different'},1,1),/ownership expired/);
  assert.throws(()=>p.finishPublicationBookkeeping(row,NaN,1),/Invalid/);
  assert.throws(()=>p.finishPublicationBookkeeping(row,1,-1),/Invalid/);
  p.finishPublicationBookkeeping(row,2.5,1);
  assert.equal(p.samplesAfter(0)[1]!.native!['fps.bookkeeping.publicationWorkMs'],2.5);
  assert.equal(p.samplesAfter(0)[1]!.native!['fps.bookkeeping.statsReadsAvoided'],1);
  row.native!['fps.bookkeeping.publicationWorkMs']=1000;
  assert.equal(p.samplesAfter(0)[1]!.native!['fps.bookkeeping.publicationWorkMs'],2.5);
  assert.doesNotMatch(p.exportJSON(),/live:source:|"sourceIdentity"|"x.detectionIdentity"|secret/);
});

test('completed benchmark summaries use all raw measured work and never the live percentile cache',()=>{
  const p=new FrameProfiler(4096,true);p.begin('session','hair',0,['combined']);
  for(const [sequence,at]of [100,1100,2100,5100,20100,35100].entries()) {
    const row=p.add(frame(at,sequence+1,{...(at===20100?{hasFace:false,hasMask:false,totalMs:2}:{}),...(at===35100?{hasMask:false,totalMs:999}: {})}));
    // Deliberately keep the UI clock fixed so its first percentile stays cached.
    live(p,100);p.finishPublicationBookkeeping(row,.5,1);
  }
  assert.equal(p.running,false);const complete=p.completedSegments[0]!;
  assert.equal(complete.summary.frames,3);assert.equal(complete.summary.processing!.p95,999);
  assert.equal(complete.summary.trackedFrames,2);assert.equal(complete.summary.hairCoverage,.5);assert.equal(complete.coverage.status,'partial');
  const exported=JSON.parse(p.exportJSON());assert.equal(exported.samples.length,6);
  assert.deepEqual(exported.benchmark.segments[0].samples.map((row:FrameInput)=>row.totalMs),[20,2,999]);
  assert.equal(exported.benchmark.segments[0].samples[2].native['fps.bookkeeping.publicationWorkMs'],.5);
});

test('the 10-second endpoint is inclusive and expired rows, capacity and clock rollback cannot inflate FPS',()=>{
  const p=new FrameProfiler(3,true);p.add(frame(100,1));p.add(frame(10100,2));assert.equal(live(p,10100).frames,2);
  p.add(frame(10101,3));assert.equal(live(p,10101).frames,2);p.add(frame(10200,4));p.add(frame(10300,5));
  assert.deepEqual(headline(live(p,10300)),headline(summarize(p.recent('session','combined','hair'))));
  p.add(frame(500,6));const reset=live(p,500);assert.equal(reset.frames,1);assert.equal(reset.processedFps,null);
});

test('previous modes keep immediate full summary behavior and gain no opt-in bookkeeping fields',()=>{
  const p=new FrameProfiler();p.add(frame(100));p.add(frame(200,2,{totalMs:199}));
  assert.equal(live(p,100).processing!.p95,199);p.invalidateLiveSummary();assert.equal(live(p,100).frames,2);
  assert.doesNotMatch(p.exportJSON(),/fps\.bookkeeping/);
});
