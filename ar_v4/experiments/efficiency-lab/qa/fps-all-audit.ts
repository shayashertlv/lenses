import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import type {RecordedRunFrame} from '../continuous-run.ts';
import type {RecorderMetadata} from '../continuous-recorder.ts';
import type {HairDeliveryExport} from '../hair-delivery.ts';
import type {FrameSample} from '../frame-profiler.ts';
const CHOICES=['g','face-cpu','render-worker','frame-copy','reuse-compose','mask-bytes'] as const;
const ALL_ORDER=[...CHOICES,...[...CHOICES].reverse()];
type Pipeline=typeof CHOICES[number];
export interface Release {sourceFingerprint:string;createdAt:string;baseCommit:string;}
/** Node scalar assertions avoid a traced Playwright step for every field in
 * thousands of retained frames. Validation and production clocks are unchanged. */
const check=(actual:unknown)=>({
  toBe(expected:unknown):void {assert.strictEqual(actual,expected);},
  toEqual(expected:unknown):void {assert.deepStrictEqual(actual,expected);},
  toBeNull():void {assert.strictEqual(actual,null);},
  toBeDefined():void {assert.notStrictEqual(actual,undefined);},
  toHaveLength(expected:number):void {assert.ok(Array.isArray(actual));assert.equal(actual.length,expected);},
  toBeGreaterThan(expected:number):void {assert.ok(typeof actual==='number'&&actual>expected,`${String(actual)} > ${expected}`);},
  toBeGreaterThanOrEqual(expected:number):void {assert.ok(typeof actual==='number'&&actual>=expected,`${String(actual)} >= ${expected}`);},
  toBeLessThan(expected:number):void {assert.ok(typeof actual==='number'&&actual<expected,`${String(actual)} < ${expected}`);},
  toBeLessThanOrEqual(expected:number):void {assert.ok(typeof actual==='number'&&actual<=expected,`${String(actual)} <= ${expected}`);},
  toBeCloseTo(expected:number,digits:number):void {assert.ok(typeof actual==='number'&&Math.abs(actual-expected)<0.5*10**-digits,`${String(actual)} approximately ${expected}`);},
});
export interface Report {
  schema: string; baseCommit: string; sessionId: string; completed: boolean; partial: boolean; startedAtMs: number; endedAtMs: number;
  metadata: {build: {id: string; createdAt: string}; device: {crossOriginIsolated: boolean; secureContext: boolean}};
  protocol: {studyOptions: string; warmupMs: number; measureMs: number; minimumTrackedMaskedWarmupFrames: number; order: string[]};
  workload: {eyewearId: string; hairModelId: string; variant: string; sourceWidth: number; sourceHeight: number};
  recording: RecorderMetadata; rows: RecordedRunFrame[];
  hairDelivery: HairDeliveryExport;
  hairDeliveryDrain: {state: string; startedAtMs: number; endedAtMs: number; reason: string | null};
  windows: {index: number; token: number; pipeline: string; completed: boolean; validWarmupFrames: number; switchedAtMs: number | null; thirdWarmupAtMs: number | null; round: number;
    measureStartedAtMs: number | null; endedAtMs: number | null; summary: {frames: number; durationMs: number; completedArFps: number | null;
      frameAgeMs: {median: number; p95: number; max: number};
      coverage: {trackedFrames: number; maskedTrackedFrames: number};
      gapCountsIncludingEndpoints: {over100Ms: number; over200Ms: number; over500Ms: number}}}[];
  retention: {truncated: boolean; rejectedRows: number; frameRows: number};
}

function privacyViolations(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((item, index) => privacyViolations(item, `${path}[${index}]`));
  if (!value || typeof value !== 'object') return typeof value === 'string' && /^data:|;base64,/i.test(value) ? [path] : [];
  return Object.entries(value).flatMap(([key, item]) => /SHA256|PngDataUrl|landmarks|categoryBase64|imageData|maskBytes|sourceImage|sourceIdentity|deviceId|groupId/i.test(key)
    ? [path + '.' + key] : privacyViolations(item, path + '.' + key));
}

export function assertFpsMechanism(row: Pick<FrameSample,'pipeline'|'sourceWidth'|'sourceHeight'|'faceDelegate'|'native'>): void {
  check(row.sourceWidth).toBe(720);check(row.sourceHeight).toBe(1280);
  check(Number(row.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
  check(Number(row.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
  check(row.native?.['pump.deferPrefetch']).toBe(false);
  check(row.native?.['hairDelivery.releaseWorkerEarly']).toBe(false);
  if(row.pipeline==='face-cpu')check(row.faceDelegate).toBe('CPU');
  if(row.pipeline==='render-worker') {
    check(row.native?.['renderWorker.backend']).toBe('offscreen-worker');
    check(Number(row.native?.['renderWorker.sourceCopyBytes'])).toBe(720*1280*4);
    check(Number(row.native?.['review.renderWorkerCompletionMs'])).toBeGreaterThan(0);
  }
  if(row.pipeline==='frame-copy') {
    check(row.native?.['capture.actualPath']).toBe('video-frame-copy');
    check(row.native?.['capture.fallbackReason']).toBeNull();
    check(Number(row.native?.['capture.maxOwnedImages'])).toBeLessThanOrEqual(2);
  }
  if(row.pipeline==='reuse-compose')check(row.native?.['reviewCompose.used']).toBe(true);
  if(row.pipeline==='mask-bytes') {
    check(row.native?.['hairCategory.requestedMode']).toBe('rgba8');
    check(row.native?.['hairCategory.path']).toBe('rgba8-readback');
    check(row.native?.['hairCategory.rgba8FallbackReason']).toBeNull();
    check(Number(row.native?.['hairCategory.rgba8ReadbackBytes'])).toBe(720*1280*4);
  }
}

export function auditFpsAllReport(report:Report,release:Release) {
  check(report.completed).toBe(true);check(report.partial).toBe(false);
  check(report.protocol.studyOptions).toBe('fps-all');check(report.protocol.order).toEqual(ALL_ORDER);
  check(report.protocol.measureMs).toBe(30000);check(report.protocol.warmupMs).toBe(5000);
  check(report.protocol.minimumTrackedMaskedWarmupFrames).toBe(3);
  check(report.windows).toHaveLength(12);check(report.endedAtMs-report.startedAtMs).toBeGreaterThanOrEqual(420000);
  check(report.retention.truncated).toBe(false);check(report.retention.rejectedRows).toBe(0);
  check(report.retention.frameRows).toBe(report.rows.length);check(report.hairDeliveryDrain.state).toBe('drained');
  check(report.hairDelivery.truncated).toBe(false);check(report.hairDelivery.rejected).toBe(0);
  check((report.metadata.device as unknown as {userReportedPower:string}).userReportedPower).toBe('battery');
  check(privacyViolations(report)).toEqual([]);
  check(report.metadata.build).toEqual({id:release.sourceFingerprint,createdAt:release.createdAt});check(report.baseCommit).toBe(release.baseCommit);
  check(report.metadata.device.crossOriginIsolated).toBe(true);check(report.metadata.device.secureContext).toBe(true);
  const requests=new Map(report.hairDelivery.requests.map(request=>[request.sequence,request]));
  check(requests.size).toBe(report.hairDelivery.requests.length);
  for(const [index,row] of report.rows.entries()) {
    check(row.serial).toBe(index+1);check(row.invalidFields).toEqual([]);
    check(row.fields.sessionId).toBe(report.sessionId);check(row.fields.sourceWidth).toBe(720);check(row.fields.sourceHeight).toBe(1280);
    check(row.fields.eyewearId).toBe(report.workload.eyewearId);check(row.fields.hairModelId).toBe(report.workload.hairModelId);
    check(row.native?.['admission.rateHz']).toBeNull();check(row.native?.['pump.mode']).toBe('overlap');
    check(row.native?.['pump.deferPrefetch']).toBe(false);check(row.native?.['hairDelivery.releaseWorkerEarly']).toBe(false);
    check(Number(row.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
    check(Number(row.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
    if(row.phase!=='measured')continue;
    const window=report.windows[row.windowIndex!]!;check(row.exclusion).toBeNull();check(row.fields.pipeline).toBe(window.pipeline);
    check(Number(row.fields.capturedAtMs)).toBeGreaterThanOrEqual(window.measureStartedAtMs!);
    check(Number(row.fields.publishedAtMs)).toBeLessThan(window.endedAtMs!);
    if(row.fields.hasMask) {
      const request=requests.get(Number(row.fields.sequence));check(request).toBeDefined();
      check(request!.sessionId).toBe(report.sessionId);check(request!.pipeline).toBe(row.fields.pipeline);
      check(request!.usedAtPublication).toBe(true);check(request!.publicationAtMs).toBe(row.fields.publishedAtMs);
      check(request!.timing!.outcome).toBe('completed');
      check(request!.timing!.completedAtMs!).toBeLessThanOrEqual(Number(row.fields.publishedAtMs));
    }
  }
  for(const [index,window] of report.windows.entries()) {
    check(window.index).toBe(index);check(window.token).toBe(index+1);check(window.pipeline).toBe(ALL_ORDER[index]);
    check(window.round).toBe(index<6?1:2);check(window.completed).toBe(true);
    check(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
    check(window.measureStartedAtMs!-window.switchedAtMs!).toBeGreaterThanOrEqual(5000);
    check(window.measureStartedAtMs!).toBeGreaterThanOrEqual(window.thirdWarmupAtMs!);
    check(window.endedAtMs!-window.measureStartedAtMs!).toBe(30000);check(window.summary.durationMs).toBe(30000);
    const measured=report.rows.filter(row=>row.phase==='measured'&&row.windowIndex===index);
    check(measured.length).toBeGreaterThan(10);check(window.summary.frames).toBe(measured.length);
    check(window.summary.completedArFps).toBeCloseTo(measured.length/30,10);
    const tracked=measured.filter(row=>row.fields.hasFace===true),masked=tracked.filter(row=>row.fields.hasMask===true);
    check(masked.length).toBeGreaterThan(3);check(window.summary.coverage.trackedFrames).toBe(tracked.length);
    check(window.summary.coverage.maskedTrackedFrames).toBe(masked.length);
    for(const row of masked) {
      assertFpsMechanism({pipeline:window.pipeline as Pipeline,sourceWidth:Number(row.fields.sourceWidth),sourceHeight:Number(row.fields.sourceHeight),
        faceDelegate:String(row.fields.faceDelegate),native:row.native});
      check(row.fields.faceDelegate).toBe(window.pipeline==='face-cpu'?'CPU':'GPU');
    }
    // Reconstruct age and stalls directly from retained completed publications,
    // including the endpoints and every measured untracked or unmasked frame.
    const ages=measured.map(row=>Number(row.fields.publishedAtMs)-Number(row.fields.capturedAtMs)).sort((a,b)=>a-b);
    const half=Math.floor(ages.length/2);
    check(window.summary.frameAgeMs.median).toBeCloseTo(ages.length%2?ages[half]!:(ages[half-1]!+ages[half]!)/2,10);
    check(window.summary.frameAgeMs.p95).toBeCloseTo(ages[Math.ceil(ages.length*.95)-1]!,10);
    check(window.summary.frameAgeMs.max).toBeCloseTo(ages.at(-1)!,10);
    const points=[window.measureStartedAtMs!,...measured.map(row=>Number(row.fields.publishedAtMs)).sort((a,b)=>a-b),window.endedAtMs!];
    const gaps=points.slice(1).map((point,i)=>point-points[i]!);
    check(window.summary.gapCountsIncludingEndpoints).toEqual({over100Ms:gaps.filter(gap=>gap>100).length,
      over200Ms:gaps.filter(gap=>gap>200).length,over500Ms:gaps.filter(gap=>gap>500).length});
  }
  return {passed:true,windows:report.windows.length,frameRows:report.rows.length,
    measuredFrames:report.rows.filter(row=>row.phase==='measured').length,
    measuredMaskedFrames:report.rows.filter(row=>row.phase==='measured'&&row.fields.hasMask===true).length,
    hairRequests:report.hairDelivery.requests.length,elapsedMs:report.endedAtMs-report.startedAtMs,
    pipelines:[...CHOICES],sourceFingerprint:report.metadata.build.id};
}

/** Re-audit a completed real-clock receipt without repeating camera work. */
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  const [telemetryPath,releasePath,outputPath]=process.argv.slice(2);
  assert.ok(telemetryPath&&releasePath&&outputPath,'Usage: node fps-all-audit.ts telemetry.json release.json audit.json');
  const bytes=readFileSync(telemetryPath),report=JSON.parse(bytes.toString('utf8')) as Report;
  const release=JSON.parse(readFileSync(releasePath,'utf8')) as Release;
  const started=performance.now(),result=auditFpsAllReport(report,release),mutations:string[]=[];
  const cases:readonly [string,(copy:Report)=>void][]=[
    ['wrong-order',copy=>{copy.protocol.order[1]='g';}],
    ['wrong-full-window-denominator',copy=>{copy.windows[0]!.summary.durationMs=29000;}],
    ['cross-session-frame',copy=>{copy.rows[0]!.fields.sessionId='wrong-session';}],
    ['wrong-mask-publication-pair',copy=>{const row=copy.rows.find(row=>row.phase==='measured'&&row.fields.hasMask===true)!;
      copy.hairDelivery.requests.find(request=>request.sequence===row.fields.sequence)!.publicationAtMs=Number(row.fields.publishedAtMs)+1;}],
    ['wrong-actual-delegate',copy=>{copy.rows.find(row=>row.phase==='measured'&&row.fields.hasMask===true&&row.fields.pipeline==='face-cpu')!.fields.faceDelegate='GPU';}],
    ['wrong-worker-path',copy=>{copy.rows.find(row=>row.phase==='measured'&&row.fields.hasMask===true&&row.fields.pipeline==='render-worker')!.native!['renderWorker.backend']='main-thread';}],
    ['wrong-frame-age',copy=>{copy.windows[0]!.summary.frameAgeMs.p95+=1;}],
    ['wrong-mask-coverage',copy=>{copy.windows[0]!.summary.coverage.maskedTrackedFrames+=1;}],
  ];
  for(const [name,mutate] of cases) {const copy=structuredClone(report);mutate(copy);
    assert.throws(()=>auditFpsAllReport(copy,release),{name:'AssertionError'},name);mutations.push(name);}
  const receipt={schema:'fps-all-retained-audit-v1',...result,elapsedAuditMs:performance.now()-started,
    telemetrySHA256:createHash('sha256').update(bytes).digest('hex'),mutationsRejected:mutations};
  writeFileSync(outputPath,JSON.stringify(receipt,null,2)+'\n');process.stdout.write(JSON.stringify(receipt,null,2)+'\n');
}
