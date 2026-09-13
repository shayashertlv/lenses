import {test, expect} from '@playwright/test';
import type {Download, Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {RecordedRunFrame} from './continuous-run.ts';
import type {RecorderMetadata} from './continuous-recorder.ts';
import type {HairDeliveryExport} from './hair-delivery.ts';
import type {FrameSample} from './frame-profiler.ts';

const CHOICES = ['g', 'face-cpu', 'render-worker', 'frame-copy', 'reuse-compose'] as const;
type Pipeline = typeof CHOICES[number];
interface CameraObservation {streams: MediaStream[]; recordings: MediaStream[]; workers: {terminated: boolean}[];}

interface HashGate {reached: boolean; release(): void; restore(): void;}
declare global {interface Window {fpsReviewCamera: CameraObservation; fpsReviewHashGate?: HashGate;}}
interface Report {
  schema: string; baseCommit: string; sessionId: string; completed: boolean; partial: boolean; startedAtMs: number; endedAtMs: number;
  metadata: {build: {id: string; createdAt: string}; device: {crossOriginIsolated: boolean; secureContext: boolean}};
  protocol: {studyOptions: string; warmupMs: number; measureMs: number; minimumTrackedMaskedWarmupFrames: number; order: string[]};
  workload: {eyewearId: string; hairModelId: string; variant: string; sourceWidth: number; sourceHeight: number};
  recording: RecorderMetadata; rows: RecordedRunFrame[];
  hairDelivery: HairDeliveryExport;
  hairDeliveryDrain: {state: string; startedAtMs: number; endedAtMs: number; reason: string | null};
  windows: {index: number; token: number; pipeline: string; completed: boolean; validWarmupFrames: number; switchedAtMs: number | null; thirdWarmupAtMs: number | null; round: number;
    measureStartedAtMs: number | null; endedAtMs: number | null; summary: {frames: number; durationMs: number; completedArFps: number | null}}[];
  retention: {truncated: boolean; rejectedRows: number; frameRows: number};
}
interface HeldOutput {
  pair: unknown; detection: unknown; mask: unknown; sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  captureSnapshot: {surfacePositions: unknown; eyewearMatrix: unknown; protection: unknown};
  stats: {hasMask: boolean; protectedCheck: {changedPixels: number}; noseCheck: {changedPixels: number};
    outsideEditableCheck: {changedPixels: number}; backgroundPreservationCheck: {changedPixels: number}};
}

/** Static synthetic portrait pixels and an advancing MediaStream getter only.
 * Production clocks, 5/30-second windows, workers, rendering and recording remain real.
 * This checks lifecycle and matching pixels, not physical-phone or wearer motion quality. */
async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/fps-review-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const state: CameraObservation = {streams: [], recordings: [], workers: []}; window.fpsReviewCamera = state;
    const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/fps-review-camera.jpg';
    const draw = (): void => {if (image.complete && image.naturalWidth) {
      context.fillStyle = '#808080'; context.fillRect(0, 0, 720, 1280); context.drawImage(image, 0, 400, 720, 480);
    }};
    setInterval(draw, 33);
    const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!; let reads = 0;
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {...descriptor,
      get(this: HTMLMediaElement) {return this.srcObject instanceof MediaStream
        ? performance.now() / 1000 + ++reads / 1e6 : descriptor.get!.call(this) as number;}});
    const capture = HTMLCanvasElement.prototype.captureStream;
    HTMLCanvasElement.prototype.captureStream = function (fps?: number): MediaStream {
      const stream = capture.call(this, fps); if (this.id === 'mirror') state.recordings.push(stream); return stream;
    };
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly record: {terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {super(url, options);
        this.record = {terminated: false}; state.workers.push(this.record);}
      override terminate(): void {this.record.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  });
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.fpsReviewCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.fpsReviewCamera.recordings.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.fpsReviewCamera.workers.every(worker => worker.terminated));
async function cleanup(page: Page): Promise<void> {
  if (await page.locator('#stop').isVisible()) await page.click('#stop');
  await expect.poll(() => closed(page)).toBe(true);
}
function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);}
  return (crc ^ 0xffffffff) >>> 0;
}
/** Independent ZIP32 reader: validates stored entries, both headers, offsets,
 * sizes and CRC. It does not call the production archive implementation. */
function readArchive(bytes: Buffer): Map<string, Buffer> {
  const files = new Map<string, Buffer>(), local = new Map<string, {offset: number; crc: number; size: number}>();
  let offset = 0;
  while (bytes.readUInt32LE(offset) === 0x04034b50) {
    const start = offset, flags = bytes.readUInt16LE(offset + 6), method = bytes.readUInt16LE(offset + 8);
    const crc = bytes.readUInt32LE(offset + 14), compressed = bytes.readUInt32LE(offset + 18), size = bytes.readUInt32LE(offset + 22);
    const nameLength = bytes.readUInt16LE(offset + 26), extraLength = bytes.readUInt16LE(offset + 28);
    expect(flags).toBe(0x0800); expect(method).toBe(0); expect(compressed).toBe(size); expect(extraLength).toBe(0);
    const name = bytes.toString('utf8', offset + 30, offset + 30 + nameLength);
    expect(name).toMatch(/^(telemetry\.json|ar-mirror\.(webm|mp4))$/); expect(files.has(name)).toBe(false);
    offset += 30 + nameLength + extraLength; const content = bytes.subarray(offset, offset + size);
    expect(content.length).toBe(size); expect(crc32(content)).toBe(crc);
    files.set(name, content); local.set(name, {offset: start, crc, size}); offset += size;
  }
  const centralStart = offset; let centralCount = 0;
  while (bytes.readUInt32LE(offset) === 0x02014b50) {
    const nameLength = bytes.readUInt16LE(offset + 28), extraLength = bytes.readUInt16LE(offset + 30), commentLength = bytes.readUInt16LE(offset + 32);
    const name = bytes.toString('utf8', offset + 46, offset + 46 + nameLength), entry = local.get(name);
    expect(entry).toBeDefined(); expect(bytes.readUInt16LE(offset + 8)).toBe(0x0800); expect(bytes.readUInt16LE(offset + 10)).toBe(0);
    expect(bytes.readUInt32LE(offset + 16)).toBe(entry!.crc); expect(bytes.readUInt32LE(offset + 20)).toBe(entry!.size);
    expect(bytes.readUInt32LE(offset + 24)).toBe(entry!.size); expect(bytes.readUInt32LE(offset + 42)).toBe(entry!.offset);
    expect(extraLength).toBe(0); expect(commentLength).toBe(0); centralCount++; offset += 46 + nameLength;
  }
  expect(bytes.readUInt32LE(offset)).toBe(0x06054b50); expect(bytes.readUInt16LE(offset + 4)).toBe(0);
  expect(bytes.readUInt16LE(offset + 6)).toBe(0); expect(bytes.readUInt16LE(offset + 8)).toBe(files.size);
  expect(bytes.readUInt16LE(offset + 10)).toBe(files.size); expect(centralCount).toBe(files.size);
  expect(bytes.readUInt32LE(offset + 12)).toBe(offset - centralStart); expect(bytes.readUInt32LE(offset + 16)).toBe(centralStart);
  expect(bytes.readUInt16LE(offset + 20)).toBe(0); expect(offset + 22).toBe(bytes.length); return files;
}
async function downloadReport(download: Download, name: string): Promise<{report: Report; files: Map<string, Buffer>}> {
  expect(download.suggestedFilename()).toMatch(/^ar-mobile-comparison-.*\.zip$/);
  const filename = test.info().outputPath(name + '.zip'); await download.saveAs(filename);
  const files = readArchive(await readFile(filename)); expect(files.has('telemetry.json')).toBe(true);
  const data = files.get('telemetry.json')!; await writeFile(test.info().outputPath(name + '-telemetry.json'), data);
  return {files, report: JSON.parse(data.toString('utf8')) as Report};
}
function privacyViolations(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((item, index) => privacyViolations(item, `${path}[${index}]`));
  if (!value || typeof value !== 'object') return typeof value === 'string' && /^data:|;base64,/i.test(value) ? [path] : [];
  return Object.entries(value).flatMap(([key, item]) => /SHA256|PngDataUrl|landmarks|categoryBase64|imageData|maskBytes|sourceImage|sourceIdentity|deviceId|groupId/i.test(key)
    ? [path + '.' + key] : privacyViolations(item, path + '.' + key));
}

function observeNetwork(page: Page): {errors: string[]; badResponses: string[]; unscoped: string[]} {
  const observed = {errors: [] as string[], badResponses: [] as string[], unscoped: [] as string[]};
  page.on('pageerror', error => observed.errors.push(error.message));
  page.context().on('request', request => {
    const url = new URL(request.url()); if (url.protocol === 'blob:' || url.protocol === 'data:') return;
    if (url.origin !== 'http://127.0.0.1:8104' || !url.pathname.startsWith('/ar_testing/')) observed.unscoped.push(request.url());
  });
  page.context().on('response', response => {if (response.status() >= 400) observed.badResponses.push(`${response.status()} ${response.url()}`);});
  return observed;
}


const CANDIDATES = ['face-cpu','render-worker','frame-copy','reuse-compose'] as const;
const path = (candidate: string) => '/ar_testing/experiments/efficiency-lab/live.html?study=fps-review&candidate='+candidate;
async function published(page: Page, pipeline: Pipeline, after=0): Promise<FrameSample> {
  await expect.poll(()=>page.evaluate(({pipeline,after})=>window.arPerformanceProfiler.samplesAfter(after)
    .filter(row=>row.sessionId===window.hairLivePreview.diagnostics().sessionId&&row.pipeline===pipeline&&row.hasFace&&row.hasMask).length,{pipeline,after})).toBeGreaterThanOrEqual(6);
  return await page.evaluate(({pipeline,after})=>window.arPerformanceProfiler.samplesAfter(after)
    .filter(row=>row.sessionId===window.hairLivePreview.diagnostics().sessionId&&row.pipeline===pipeline&&row.hasFace&&row.hasMask).at(-1)!,{pipeline,after});
}
function mechanism(row: FrameSample): void {
  expect(row.sourceWidth).toBe(720);expect(row.sourceHeight).toBe(1280);
  expect(Number(row.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
  expect(Number(row.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
  expect(row.native?.['pump.deferPrefetch']).toBe(false);
  expect(row.native?.['hairDelivery.releaseWorkerEarly']).toBe(false);
  if(row.pipeline==='face-cpu')expect(row.faceDelegate).toBe('CPU');
  if(row.pipeline==='render-worker') {
    expect(row.native?.['renderWorker.backend']).toBe('offscreen-worker');
    expect(Number(row.native?.['renderWorker.sourceCopyBytes'])).toBe(720*1280*4);
    expect(Number(row.native?.['review.renderWorkerCompletionMs'])).toBeGreaterThan(0);
  }
  if(row.pipeline==='frame-copy') {
    expect(row.native?.['capture.actualPath']).toBe('video-frame-copy');
    expect(row.native?.['capture.fallbackReason']).toBeNull();
    expect(Number(row.native?.['capture.maxOwnedImages'])).toBeLessThanOrEqual(2);
  }
  if(row.pipeline==='reuse-compose')expect(row.native?.['reviewCompose.used']).toBe(true);
}
async function heldPixels(page:Page,candidate:string):Promise<void> {
  await page.click('#hold-frame');await expect(page.locator('.stage')).toHaveAttribute('data-state','held');
  await expect.poll(()=>page.evaluate(()=>window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
  const report=await page.evaluate(()=>window.hairLivePreview.exportDiagnostic()) as Record<string,unknown>;
  expect(report.comparedPipelines).toEqual(['g',candidate]);expect(report.candidateAccepted).toBe(false);
  const outputs=report as Record<string,HeldOutput>, g=outputs.g!, next=outputs[candidate]!;
  for(const key of ['pair','detection','mask','sourcePngDataUrl'] as const)expect(next[key]).toEqual(g[key]);
  for(const key of ['surfacePositions','eyewearMatrix','protection'] as const)expect(next.captureSnapshot[key]).toEqual(g.captureSnapshot[key]);
  for(const output of [g,next])for(const key of ['protectedCheck','noseCheck','outsideEditableCheck','backgroundPreservationCheck'] as const)
    expect(output.stats[key].changedPixels).toBe(0);
  const hashes=await page.evaluate(async candidate=>{
    const diagnostic=window.hairLivePreview.exportDiagnostic()! as Record<string,{acceptedPngDataUrl:string;hairPngDataUrl:string}>;
    const result:Record<string,string>={};
    for(const id of ['g',candidate])for(const variant of ['accepted','hair'] as const) {
      const image=new Image();image.src=diagnostic[id]![variant==='hair'?'hairPngDataUrl':'acceptedPngDataUrl'];await image.decode();
      const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
      const ctx=canvas.getContext('2d')!;ctx.drawImage(image,0,0);
      const bytes=ctx.getImageData(0,0,canvas.width,canvas.height).data;
      result[id+'.'+variant]=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).join(',');
    }
    return result;
  },candidate);
  for(const variant of ['accepted','hair'])expect(hashes[candidate+'.'+variant]).toBe(hashes['g.'+variant]);
  if(candidate==='face-cpu'||candidate==='frame-copy')await expect(page.locator('#held-result')).toContainText('does not test CPU tracking');
  for(const pipeline of ['g',candidate])for(const variant of ['accepted','hair']) {
    await page.selectOption('#pipeline-select',pipeline);await page.selectOption('#variant-select',variant);
    await expect(page.locator('#mirror')).toBeVisible();
  }
}

for(const eyewear of ['amber-horizon','tom-ford-clear'])for(const hair of ['hair-only','selfie-multiclass'])
  test(`FPS review live and held: ${eyewear} / ${hair}`,async({page})=>{
    const network=observeNetwork(page);await installCamera(page);
    for(const candidate of CANDIDATES) {
      await page.goto(path(candidate));await expect(page.locator('#pipeline-select')).toHaveValue('g');
      expect(await page.locator('#pipeline-select option').evaluateAll(nodes=>nodes.map(n=>(n as HTMLOptionElement).value))).toEqual(['g',candidate]);
      await expect(page.locator('#continuous-video')).not.toBeChecked();
      await page.selectOption('#eyewear-select',eyewear);await page.selectOption('#hair-model-select',hair);
      await page.click('#start');const g=await published(page,'g');expect(g.faceDelegate).toBe('GPU');
      await expect(page.locator('#review-candidate')).toBeDisabled();
      await page.selectOption('#pipeline-select',candidate);const row=await published(page,candidate,g.serial);mechanism(row);
      await heldPixels(page,candidate);
      await page.selectOption('#pipeline-select','g');await page.click('#resume-live');const resumed=await published(page,'g');
      expect(resumed.sessionId).not.toBe(row.sessionId);await cleanup(page);
    }
    await page.screenshot({path:test.info().outputPath('review-mobile.png'),fullPage:true});
    expect(network).toEqual({errors:[],badResponses:[],unscoped:[]});
  });

for(const candidate of ['face-cpu','render-worker'] as const)
  test(`FPS review complete real windows and ZIP: ${candidate}`,async({page})=>{
    const network=observeNetwork(page);await installCamera(page);await page.goto(path(candidate));
    await page.selectOption('#power-context','battery');await page.click('#start');await published(page,'g');
    const file=page.waitForEvent('download',{timeout:330000});await page.click('#continuous-start');
    const {report,files}=await downloadReport(await file,'fps-'+candidate);
    expect(files.size).toBe(1);expect(report.completed).toBe(true);expect(report.partial).toBe(false);
    expect(report.protocol.order).toEqual(['g',candidate,candidate,'g']);expect(report.protocol.studyOptions).toBe('fps-review');
    expect(report.protocol.measureMs).toBe(30000);expect(report.protocol.warmupMs).toBe(5000);
    expect(report.hairDeliveryDrain.state).toBe('drained');expect(report.retention.rejectedRows).toBe(0);
    expect((report.metadata.device as unknown as {userReportedPower:string}).userReportedPower).toBe('battery');
    expect(privacyViolations(report)).toEqual([]);
    for(const window of report.windows) {
      expect(window.completed).toBe(true);expect(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
      const measured=report.rows.filter(row=>row.phase==='measured'&&row.windowIndex===window.index);
      expect(measured.length).toBeGreaterThan(10);expect(window.summary.durationMs).toBe(30000);
      expect(window.summary.completedArFps).toBeCloseTo(measured.length/30,10);
      for(const row of measured) {
        expect(row.fields.pipeline).toBe(window.pipeline);expect(row.fields.capturedAtMs).toBeGreaterThanOrEqual(window.measureStartedAtMs!);
        expect(row.fields.publishedAtMs).toBeLessThan(window.endedAtMs!);
        if(window.pipeline==='face-cpu')expect(row.fields.faceDelegate).toBe('CPU');
        if(window.pipeline==='render-worker')expect(row.native?.['renderWorker.backend']).toBe('offscreen-worker');
      }
    }
    await cleanup(page);expect(network).toEqual({errors:[],badResponses:[],unscoped:[]});
  });

test('FPS review capture fallback stays visible and unavailable worker cannot produce a mislabeled measurement',async({page})=>{
  await installCamera(page);await page.addInitScript(()=>{Object.defineProperty(window,'VideoFrame',{value:undefined,configurable:true});});
  await page.goto(path('frame-copy'));await page.selectOption('#pipeline-select','frame-copy');await page.click('#start');
  const row=await published(page,'frame-copy');expect(row.native?.['capture.actualPath']).toBe('canvas-video-fallback');
  expect(row.native?.['capture.fallbackReason']).toBe('video-frame-unavailable');await expect(page.locator('#actual-path')).toContainText('Fallback');
  await cleanup(page);
  await page.addInitScript(()=>{Object.defineProperty(window,'OffscreenCanvas',{value:undefined,configurable:true});});
  await page.goto(path('render-worker'));await page.selectOption('#pipeline-select','render-worker');await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state','error');
  await expect(page.locator('#guidance')).toContainText('does not support');await expect(page.locator('#continuous-start')).toBeDisabled();
  expect(await page.evaluate(()=>window.arPerformanceProfiler.samplesAfter(0).length)).toBe(0);await expect.poll(()=>closed(page)).toBe(true);
});
