import {test} from 'node:test';
import assert from 'node:assert/strict';
import {FrameProfiler, summarize, distribution, workCoverage} from './frame-profiler.ts';
import type {FrameInput, Pipeline} from './frame-profiler.ts';

function frame(at: number, pipeline:Pipeline='current', sequence=1): FrameInput {
  return {sessionId:'session',sequence,pipeline,variant:'hair',capturedAtMs:at-20,publishedAtMs:at,
    videoPresentedFrames:sequence*3,videoMediaTime:at/1000,videoPresentationTimeMs:null,cameraSettingFps:30,
    sourceWidth:640,sourceHeight:427,sourceDrawMs:1,detectorDrawMs:1,sourceReadbackMs:1,sourceHashMs:2,
    faceBitmapMs:1,faceRequestWallMs:8,faceInferenceMs:6,faceWorkerMs:7,faceExtractionMs:0.5,
    faceWorkerValidationMs:0.5,faceClientValidationMs:0.1,faceTransportSchedulingMs:1,prerequisitesWaitMs:8,
    detectionHashMs:1,prepareMs:5,finishMs:3,renderMs:8,totalMs:20,schedulerWaitMs:10,hairWaitMs:0,
    hairInferenceMs:9,hairExtractionMs:1,hasFace:true,hasMask:true,maskMode:'category-only',fallback:null,changedPixels:3,
    faceDelegate:'GPU',hairDelegate:'GPU',gpuRenderer:'test',cleanCameraMs:1,composeMs:1,continuityMs:0.5,
    finalChecksMs:0.5,publishMs:1,native:{readbacks:1}};
}

test('cadence uses completed publications, video delivery uses its own frame counter, overlapping stages stay distinct',()=>{
  const p=new FrameProfiler();p.add(frame(100,'current',1));p.add(frame(200,'current',2));p.add(frame(300,'current',3));
  const summary=summarize(p.samplesAfter(0));
  assert.equal(summary.processedFps,10);assert.equal(summary.videoDeliveryFps,30);
  assert.equal(summary.processing?.median,20);assert.equal(summary.stages.faceInferenceMs?.median,6);
  assert.equal(summary.hairCoverage,1);assert.equal(summary.changedHairFrames,3);
  assert.deepEqual(distribution([1,2,3,100]),{median:2.5,p95:100,max:100});
});

test('recent samples never cross pipeline or session changes and exports do not lend internal storage',()=>{
  const p=new FrameProfiler(3);p.add(frame(100));p.add(frame(200,'test'));p.add(frame(300));
  const rows=p.samplesAfter(0);rows[0]!.native!.readbacks=99;rows[0]!.pipeline='test2';
  assert.equal(p.samplesAfter(0)[0]!.native!.readbacks,1);
  assert.equal(p.recent('session','current','hair').length,1);
  p.add({...frame(400),sessionId:'new'});assert.equal(p.samplesAfter(0)[0]!.serial,2);
  assert.equal(p.recent('new','current','hair').length,1);assert.equal(p.recent('session','current','hair').length,0);
});

test('GPU coverage includes CPU fallback and missing-mask tracked frames, and is absent for other pipelines or hair off',()=>{
  const p=new FrameProfiler();
  p.add({...frame(100,'test3'),native:{gpuCompositorUsed:true}});
  p.add({...frame(200,'test3'),native:{gpuCompositorUsed:false}});
  p.add({...frame(300,'test3'),hasMask:false,native:{gpuCompositorUsed:false}});
  p.add({...frame(400,'test3'),hasFace:false,native:{gpuCompositorUsed:false}});
  assert.equal(summarize(p.samplesAfter(0)).gpuHairFraction,1/3);
  const older=new FrameProfiler();older.add(frame(100,'test2'));
  assert.equal(summarize(older.samplesAfter(0)).gpuHairFraction,null);
  const off=new FrameProfiler();off.add({...frame(100,'test3'),variant:'accepted'});
  assert.equal(summarize(off.samplesAfter(0)).gpuHairFraction,null);
});

test('benchmark requires warmup and thirty seconds per pipeline, drains switches once and retains complete numeric samples',()=>{
  const p=new FrameProfiler();assert.equal(p.begin('session','hair',0),'current');
  let at=100;
  for(const pipeline of ['current','test','test2','test3'] as const) {
    p.add(frame(at,pipeline));p.add(frame(at+4999,pipeline));
    p.add(frame(at+5000,pipeline));p.add(frame(at+34999,pipeline));
    assert.equal(p.takeNextPipeline(),null);
    p.add(frame(at+35000,pipeline));
    assert.equal(p.takeNextPipeline(),pipeline==='current'?'test':pipeline==='test'?'test2':pipeline==='test2'?'test3':null);
    assert.equal(p.takeNextPipeline(),null);at+=36000;
  }
  const run=p.snapshot().benchmark as {completed:boolean;segments:{samples:unknown[];summary:{durationMs:number}}[]};
  assert.equal(run.completed,true);assert.equal(p.running,false);
  assert.deepEqual(run.segments.map(s=>s.samples.length),[3,3,3,3]);
  assert.deepEqual(run.segments.map(s=>s.summary.durationMs),[30000,30000,30000,30000]);
  assert.equal(JSON.stringify(run).includes('sourcePng'),false);
});

test('cancelled and changed-session measurements cannot silently resume or become complete',()=>{
  const p=new FrameProfiler();p.begin('session','hair',0);p.add(frame(100));p.add(frame(1000));p.add(frame(5100));p.cancel('Held');p.add(frame(40000));
  assert.equal(p.running,false);assert.equal(p.takeNextPipeline(),null);
  const first=p.snapshot().benchmark as {completed:boolean;cancelledReason:string;segments:{samples:unknown[]}[]};
  assert.equal(first.completed,false);assert.equal(first.cancelledReason,'Held');assert.equal(first.segments[0]!.samples.length,1);
  p.begin('session','hair',0);p.add({...frame(50000),sessionId:'new'});
  assert.equal((p.snapshot().benchmark as {cancelledReason:string}).cancelledReason,'Session or hair setting changed.');
});

test('a measurement cannot claim warmup success without tracked hair frames',()=>{
  const p=new FrameProfiler();p.begin('session','hair',0);
  for(const at of [100,1000,5100,16000]) p.add({...frame(at),hasMask:false});
  const run=p.snapshot().benchmark as {completed:boolean;cancelledReason:string;segments:{samples:unknown[]}[]};
  assert.equal(run.completed,false);assert.equal(p.running,false);
  assert.match(run.cancelledReason,/unavailable during warmup/);assert.equal(run.segments[0]!.samples.length,0);
});

function completeSegment(p: FrameProfiler, pipeline: Pipeline, at: number,
  measured: (input: FrameInput, index: number) => FrameInput, variant: 'hair' | 'accepted' = 'hair'): void {
  for (const offset of [0,1000,2000]) p.add({...frame(at+offset,pipeline),variant,hasMask:variant==='hair'});
  for (const [index,offset] of [5000,20000,35000].entries()) p.add(measured({...frame(at+offset,pipeline),variant},index));
}

test('completed segments disclose lost tracking and missing masks without filtering faster fallback work',()=>{
  const p=new FrameProfiler();p.begin('session','hair',0);
  completeSegment(p,'current',100,input=>({...input,hasFace:false,hasMask:false,totalMs:2}));
  completeSegment(p,'test',36100,input=>({...input,hasMask:false,totalMs:5}));
  completeSegment(p,'test2',72100,input=>input);
  completeSegment(p,'test3',108100,input=>input);
  const completed=p.completedSegments;
  assert.deepEqual(completed.map(segment=>segment.coverage.status),['no-tracking','partial','full','full']);
  assert.deepEqual(completed.map(segment=>segment.summary.frames),[3,3,3,3]);
  assert.deepEqual(completed.map(segment=>segment.summary.processing?.median),[2,5,20,20]);
  assert.equal(completed[0]!.coverage.trackedFraction,0);assert.equal(completed[0]!.coverage.maskedTrackedFraction,null);
  assert.equal(completed[1]!.coverage.maskedTrackedFraction,0);assert.equal(completed[1]!.coverage.trackedFramesWithoutMask,3);
  assert.match(p.progress(120000),/complete with missing tracking or hair masks/);
  const report=JSON.parse(p.exportJSON()) as {coveragePolicy:string;benchmark:{completed:boolean;segments:{coverage:{status:string};samples:unknown[]}[]}};
  assert.equal(report.benchmark.completed,true);assert.match(report.coveragePolicy,/100%/);
  assert.deepEqual(report.benchmark.segments.map(segment=>segment.coverage.status),['no-tracking','partial','full','full']);
  assert.deepEqual(report.benchmark.segments.map(segment=>segment.samples.length),[3,3,3,3]);
});

test('coverage uses all measured frames for tracking and only tracked hair frames for mask coverage',()=>{
  const rows=[frame(100),{...frame(200),hasFace:false,hasMask:false},{...frame(300),hasMask:false}];
  const coverage=workCoverage(summarize(rows.map((row,index)=>({...row,serial:index+1}))),'hair');
  assert.equal(coverage.status,'partial');assert.equal(coverage.trackedFraction,2/3);
  assert.equal(coverage.maskedTrackedFraction,.5);assert.equal(coverage.untrackedFrames,1);assert.equal(coverage.trackedFramesWithoutMask,1);
  assert.equal(workCoverage(summarize([]),'hair').status,'no-frames');
});

test('hair-off measurements require tracking but do not report deliberately skipped masks as missing',()=>{
  const p=new FrameProfiler();p.begin('session','accepted',0);
  let at=100;
  for(const pipeline of ['current','test','test2','test3'] as const) {
    completeSegment(p,pipeline,at,input=>({...input,hasMask:false}),'accepted');at+=36000;
  }
  for(const segment of p.completedSegments) {
    assert.equal(segment.coverage.status,'full');assert.equal(segment.coverage.trackedFraction,1);
    assert.equal(segment.coverage.maskedTrackedFraction,null);assert.equal(segment.coverage.trackedFramesWithoutMask,null);
  }
  assert.match(p.progress(120000),/complete with full tracking and requested mask coverage/);
});

test('completed comparison summaries remain cached and owned while live frames continue, and restart clears them',()=>{
  const p=new FrameProfiler();p.begin('session','hair',0);const revision=p.comparisonRevision;
  p.add(frame(100));assert.equal(p.comparisonRevision,revision);assert.equal(p.completedSegments.length,0);
  completeSegment(p,'current',200,input=>input);
  const completedRevision=p.comparisonRevision, original=p.completedSegments[0]!;
  assert.equal(completedRevision,revision+1);
  const mutated=p.completedSegments[0]!;mutated.summary.processing!.median=900;mutated.summary.stages.renderMs!.p95=900;
  mutated.coverage.status='partial';mutated.summary.source!.width=1;
  p.add(frame(100000,'test'));
  assert.equal(p.comparisonRevision,completedRevision);assert.deepEqual(p.completedSegments[0],original);
  const snapshot=p.snapshot() as {benchmark:{segments:{summary:{processing:{median:number}}}[]}};
  snapshot.benchmark.segments[0]!.summary.processing.median=999;
  assert.deepEqual(p.completedSegments[0],original);
  const exported=JSON.parse(p.exportJSON()) as {samples:Record<string,unknown>[];privacy:string};
  assert.equal(exported.samples.length,8);assert.match(exported.privacy,/no camera images/);
  for(const row of exported.samples) for(const field of ['sourcePngDataUrl','hairPngDataUrl','detection','mask','sourceSHA256','detectionSHA256'])
    assert.equal(field in row,false);
  p.begin('session','hair',100001);assert.equal(p.completedSegments.length,0);assert.equal(p.comparisonRevision,completedRevision+1);
});
