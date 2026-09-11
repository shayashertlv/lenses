import {test, expect} from '@playwright/test';
import type {Download, Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import type {RecordedRunFrame} from './continuous-run.ts';
import type {RecorderMetadata} from './continuous-recorder.ts';

const REVIEW = ['g', 'publish', 'region', 'lens', 'ui'] as const;
const ORDER = [...REVIEW, ...[...REVIEW].reverse()];
type Pipeline = typeof REVIEW[number];
interface MobileCamera {cameraStreams: MediaStream[]; recordingStreams: MediaStream[]; workers: {url: string; terminated: boolean}[];}
declare global {interface Window {mobileCamera: MobileCamera;}}
interface RunWindow {
  index: number; token: number; round: number; pipeline: Pipeline; completed: boolean;
  requestedAtMs: number | null; switchedAtMs: number | null; firstFrameAtMs: number | null;
  measureStartedAtMs: number | null; plannedEndAtMs: number | null; endedAtMs: number | null;
  validWarmupFrames: number; switchWaitMs: number | null; timerOvershootMs: number | null;
  summary: {frames: number; durationMs: number; completedArFps: number | null;
    initialNoCompletionMs: number | null; trailingNoCompletionMs: number | null;
    frameAgeMs: {count: number; median: number; p95: number; max: number} | null;
    completionGapMsIncludingEndpoints: {count: number; max: number} | null;
    camera: {deliveryFps: number | null; observationCount: number; observedSpanMs: number; source: string};
    stages: Record<string, unknown>; coverage: {status: string; trackedFrames: number; maskedTrackedFrames: number};
  };
}
interface RunReport {
  schema: string; baseCommit: string; sessionId: string; completed: boolean; partial: boolean;
  cancelledReason: string | null; startedAtMs: number; endedAtMs: number;
  workload: {eyewearId: string; hairModelId: string; variant: string; sourceWidth: number; sourceHeight: number};
  protocol: {warmupMs: number; measureMs: number; order: Pipeline[]; minimumTrackedMaskedWarmupFrames: number};
  metadata: {build: {id: string; createdAt: string}; device: {crossOriginIsolated: boolean; secureContext: boolean};
    sessionStartup: {openedAtMs: number; firstPublishedAtMs: number; firstMaskedAtMs: number}};
  recording: RecorderMetadata;
  rows: RecordedRunFrame[]; windows: RunWindow[]; events: {name: string; atMs: number}[];
  videoObservations: {atMs: number; presentedFrames: number; mediaTime: number | null}[];
  retention: {frameRows: number; videoRows: number; truncated: boolean; rejectedRows: number};
}
interface HeldOutput {
  pair: unknown; detection: unknown; mask: unknown; sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  captureSnapshot: {surfacePositions: unknown; eyewearMatrix: unknown; protection: unknown};
  stats: {hasMask: boolean; protectedCheck: {changedPixels: number}; noseCheck: {changedPixels: number};
    outsideEditableCheck: {changedPixels: number}; backgroundPreservationCheck: {changedPixels: number}};
}

/** Synthetic camera only. All frame scheduling, workers, renderer operations,
 * canvas recording, production timers and file generation remain real. */
async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/qa-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 427;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/qa-camera.jpg';
    const state: MobileCamera = {cameraStreams: [], recordingStreams: [], workers: []}; window.mobileCamera = state;
    const draw = (): void => {if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, 640, 427);};
    const nativeCapture = HTMLCanvasElement.prototype.captureStream;
    HTMLCanvasElement.prototype.captureStream = function(fps?: number): MediaStream {
      const stream = nativeCapture.call(this, fps); state.recordingStreams.push(stream); return stream;
    };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = nativeCapture.call(canvas, 30); state.cameraStreams.push(stream); return stream;
    }});
    const Native = window.Worker;
    class ObservedWorker extends Native {
      private readonly observation: {url: string; terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.observation = {url: String(url), terminated: false}; state.workers.push(this.observation);
      }
      override terminate(): void {this.observation.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  });
}

function observeNetwork(page: Page): {errors: string[]; badResponses: string[]; unscoped: string[]} {
  const observed = {errors: [] as string[], badResponses: [] as string[], unscoped: [] as string[]};
  page.on('pageerror', error => observed.errors.push(error.message));
  page.context().on('request', request => {
    const url = new URL(request.url());
    if (url.protocol === 'blob:' || url.protocol === 'data:') return;
    if (url.origin !== 'http://127.0.0.1:8104' || !url.pathname.startsWith('/ar_testing/')) observed.unscoped.push(request.url());
  });
  page.context().on('response', response => {
    if (response.status() >= 400) observed.badResponses.push(`${response.status()} ${response.url()}`);
  });
  return observed;
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  [...window.mobileCamera.cameraStreams, ...window.mobileCamera.recordingStreams]
    .every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.mobileCamera.workers.every(worker => worker.terminated));
async function open(page: Page, eyewear: string, hair: string): Promise<void> {
  await installCamera(page); await page.goto('/ar_testing/');
  await expect(page).toHaveURL(/\/ar_testing\/experiments\/efficiency-lab\/live\.html\?study=review$/);
  await expect(page.locator('#pipeline-select')).toHaveValue('g');
  expect(await page.locator('#pipeline-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value))).toEqual(REVIEW);
  expect(await page.evaluate(() => window.mobileCamera.cameraStreams.length)).toBe(0);
  expect(await page.evaluate(() => ({isolated: crossOriginIsolated, secure: isSecureContext}))).toEqual({isolated: true, secure: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
  await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('#continuous-start')).toBeEnabled();
  const first = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0)[0]!);
  expect(first.pipeline).toBe('g'); expect(first.sourceWidth).toBe(640); expect(first.sourceHeight).toBe(427);
  expect(first.gpuRenderer).toMatch(/Direct3D11|D3D11/i); expect(first.gpuRenderer).not.toMatch(/SwiftShader|software/i);
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
async function downloadReport(download: Download, name: string): Promise<{report: RunReport; files: Map<string, Buffer>}> {
  expect(download.suggestedFilename()).toMatch(/^ar-mobile-comparison-.*\.zip$/);
  const filename = test.info().outputPath(name + '.zip'); await download.saveAs(filename);
  const files = readArchive(await readFile(filename)); expect(files.has('telemetry.json')).toBe(true);
  const data = files.get('telemetry.json')!; await writeFile(test.info().outputPath(name + '-telemetry.json'), data);
  return {files, report: JSON.parse(data.toString('utf8')) as RunReport};
}
function privacyViolations(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((child, index) => privacyViolations(child, `${path}[${index}]`));
  if (!value || typeof value !== 'object') return typeof value === 'string' && /^data:|;base64,/i.test(value) ? [path] : [];
  return Object.entries(value).flatMap(([key, child]) =>
    /SHA256|PngDataUrl|landmarks|categoryBase64|imageData|maskBytes|sourceImage|sourceIdentity/i.test(key)
      ? [`${path}.${key}`] : privacyViolations(child, `${path}.${key}`));
}
async function assertReport(page: Page, report: RunReport, eyewear: string, hair: string, complete: boolean): Promise<void> {
  expect(report.schema).toBe('ar-continuous-comparison-v1'); expect(report.baseCommit).toBe('b9142b2a3b957445f378d8012eea7e27ca68fd0b');
  expect(report.completed).toBe(complete); expect(report.partial).toBe(!complete); expect(report.protocol.order).toEqual(ORDER);
  expect(report.protocol.warmupMs).toBe(5000); expect(report.protocol.measureMs).toBe(30000);
  expect(report.protocol.minimumTrackedMaskedWarmupFrames).toBe(3);
  expect(report.workload).toEqual({eyewearId: eyewear, hairModelId: hair, variant: 'hair', sourceWidth: 640, sourceHeight: 427});
  const releaseResponse = await page.request.get('/ar_testing/release.json'); expect(releaseResponse.ok()).toBe(true);
  const release = await releaseResponse.json() as {sourceFingerprint: string; createdAt: string; baseCommit: string};
  expect(report.metadata.build).toEqual({id: release.sourceFingerprint, createdAt: release.createdAt});
  expect(report.metadata.build.id).toMatch(/^[a-f0-9]{64}$/); expect(report.metadata.device.crossOriginIsolated).toBe(true);
  expect(report.metadata.device.secureContext).toBe(true); expect(release.baseCommit).toBe(report.baseCommit);
  const startup = report.metadata.sessionStartup;
  expect(startup.firstPublishedAtMs).toBeGreaterThanOrEqual(startup.openedAtMs);
  expect(startup.firstMaskedAtMs).toBeGreaterThanOrEqual(startup.firstPublishedAtMs);
  expect(privacyViolations(report)).toEqual([]); expect(report.retention.truncated).toBe(false); expect(report.retention.rejectedRows).toBe(0);
  expect(report.retention.frameRows).toBe(report.rows.length); expect(report.retention.videoRows).toBe(report.videoObservations.length);
  expect(report.rows.length).toBeGreaterThan(0); expect(report.videoObservations.length).toBeGreaterThan(0);
  const invalidRows = report.rows.flatMap((row, index) => {
    const fields = row.fields, native = row.native;
    const valid = row.serial === index + 1 && fields.sessionId === report.sessionId && fields.sourceWidth === 640 && fields.sourceHeight === 427
      && fields.eyewearId === eyewear && fields.hairModelId === hair && native?.['admission.rateHz'] === null
      && native['pump.mode'] === 'overlap' && Number(native['pump.maxOwnedFrames']) <= 2
      && Number(native['pump.maxInFlightInference']) <= 1 && row.invalidFields.length === 0;
    if (!valid) return [{serial: row.serial, reason: 'fixed workload, source ownership, telemetry or uncapped pump differs'}];
    if (row.phase !== 'measured') return [];
    const window = report.windows[row.windowIndex!]!;
    return row.exclusion === null && fields.pipeline === window.pipeline && Number(fields.capturedAtMs) >= window.measureStartedAtMs!
      && Number(fields.publishedAtMs) < window.endedAtMs! && Number(fields.publishedAtMs) >= Number(fields.capturedAtMs)
      ? [] : [{serial: row.serial, reason: 'measurement source/publication boundary differs'}];
  });
  expect(invalidRows).toEqual([]);
  expect(report.windows).toHaveLength(10);
  for (const [index, window] of report.windows.entries()) {
    expect(window.index).toBe(index); expect(window.token).toBe(index + 1); expect(window.pipeline).toBe(ORDER[index]);
    expect(window.round).toBe(index < 5 ? 1 : 2); if (!window.completed) continue;
    expect(window.measureStartedAtMs! - window.switchedAtMs!).toBeGreaterThanOrEqual(5000);
    expect(window.endedAtMs! - window.measureStartedAtMs!).toBe(30000); expect(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
    expect(window.summary.durationMs).toBe(30000); expect(window.summary.frames).toBeGreaterThan(0);
    const rows = report.rows.filter(row => row.windowIndex === index && row.phase === 'measured');
    expect(window.summary.frames).toBe(rows.length); expect(window.summary.completedArFps).toBeCloseTo(rows.length / 30, 10);
    expect(window.summary.frameAgeMs).not.toBeNull(); expect(window.summary.completionGapMsIncludingEndpoints).not.toBeNull();
    expect(window.summary.initialNoCompletionMs).toBeGreaterThanOrEqual(0); expect(window.summary.trailingNoCompletionMs).toBeGreaterThanOrEqual(0);
    expect(window.summary.camera.source).toBe('independent-video-frame-callback'); expect(window.summary.camera.observationCount).toBeGreaterThan(0);
    expect(window.summary.camera.deliveryFps).toBeGreaterThan(0); expect(window.summary.camera.observedSpanMs).toBeLessThan(30000);
    for (const stage of ['faceInferenceMs', 'prepareMs', 'finishMs', 'schedulerWaitMs']) expect(window.summary.stages[stage]).not.toBeNull();
  }
  if (complete) {
    expect(report.cancelledReason).toBeNull(); expect(report.windows.every(window => window.completed)).toBe(true);
    expect(report.endedAtMs - report.startedAtMs).toBeGreaterThanOrEqual(350000);
    for (const name of ['switch-requested', 'switch-ready', 'measurement-started', 'measurement-ended'])
      expect(report.events.filter(event => event.name === name)).toHaveLength(10);
    const q = report.rows.filter(row => row.phase === 'measured' && row.fields.pipeline === 'publish');
    expect(q.some(row => row.native?.['publication.prePrepareSkippedThisFrame'] === true)).toBe(true);
    const r = report.rows.filter(row => row.phase === 'measured' && row.fields.pipeline === 'region');
    expect(r.some(row => row.native?.['nativePipeline.efficiencyLab.branchRegion.used'] === true)).toBe(true);
    const s = report.rows.filter(row => row.phase === 'measured' && row.fields.pipeline === 'lens');
    expect(s.some(row => row.native?.['nativePipeline.efficiencyLab.branchLenses.used'] === true)).toBe(true);
    const t = report.rows.filter(row => row.phase === 'measured' && row.fields.pipeline === 'ui');
    expect(t.some(row => row.native?.['ui.summaryRefreshThisFrame'] === false)).toBe(true);
  }
}

async function heldAndRestart(page: Page): Promise<void> {
  const oldSession = await page.locator('.stage').getAttribute('data-session-id');
  await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
  await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
  await expect.poll(() => closed(page)).toBe(true);
  const outputs = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as Record<Pipeline, HeldOutput>;
  for (const pipeline of REVIEW) {
    const output = outputs[pipeline]; expect(output.stats.hasMask).toBe(true);
    for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
      expect(output[key], `${pipeline}/${key}`).toEqual(outputs.g[key]);
    for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
      expect(output.captureSnapshot[key], `${pipeline}/${key}`).toEqual(outputs.g.captureSnapshot[key]);
    for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
      expect(output.stats[key]?.changedPixels).toBe(0);
    await page.selectOption('#pipeline-select', pipeline);
    for (const variant of ['accepted', 'hair'] as const) {
      await page.selectOption('#variant-select', variant);
      expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png')))
        .toBe(output[variant === 'hair' ? 'hairPngDataUrl' : 'acceptedPngDataUrl']);
    }
  }
  await page.selectOption('#pipeline-select', 'g');
  await page.click('#resume-live'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('#continuous-start')).toBeEnabled();
  expect(await page.locator('.stage').getAttribute('data-session-id')).not.toBe(oldSession);
  expect(await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)!.pipeline)).toBe('g');
}

test('mobile network boundary: SDK guards and worker CSP keep requests local', async ({page}) => {
  let sentinelRequests = 0;
  const sentinel = createServer((_request, response) => {sentinelRequests++; response.end('unexpected');});
  await new Promise<void>(resolve => sentinel.listen(0, '127.0.0.1', resolve));
  const address = sentinel.address();
  if (!address || typeof address === 'string') throw new Error('Sentinel address unavailable.');
  const destination = `http://127.0.0.1:${address.port}/benign-policy-probe`;
  try {
    const network = observeNetwork(page); await open(page, 'amber-horizon', 'hair-only');
    const sdkWorkers = page.workers().filter(worker => !worker.url().startsWith('blob:'));
    expect(sdkWorkers.length).toBeGreaterThanOrEqual(2);
    for (const worker of sdkWorkers) {
      const header = await page.request.head(worker.url());
      expect(header.headers()['content-security-policy']).toContain("connect-src 'self' blob:");
      const message = await worker.evaluate(async url => {
        try {await fetch(url, {method: 'POST', body: 'benign-policy-probe'}); return 'unexpected success';}
        catch (error) {return error instanceof Error ? error.message : String(error);}
      }, destination);
      expect(message).toBe('AR testing blocks off-origin network requests.');
    }
    expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
    // A fresh blob worker has no SDK guard. Its inherited enforcing policy must
    // independently prevent a harmless request to a local different-origin server.
    const policy = await page.evaluate(async url => {
      const source = `onmessage = async event => {try {await fetch(event.data, {method:'POST', body:'benign-policy-probe'}); postMessage('unexpected success');} catch (_) {postMessage('blocked');}}`;
      const blob = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
      const worker = new Worker(blob);
      try {return await new Promise<string>((resolve, reject) => {
        worker.onmessage = event => resolve(String(event.data)); worker.onerror = event => reject(new Error(event.message));
        worker.postMessage(url);
      });} finally {worker.terminate(); URL.revokeObjectURL(blob);}
    }, destination);
    expect(policy).toBe('blocked'); expect(sentinelRequests).toBe(0);
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true);
  } finally {await new Promise<void>((resolve, reject) => sentinel.close(error => error ? reject(error) : resolve()));}
});

test('mobile Python mount: full real ten-window recording exports scalar telemetry and a playable continuous video', async ({page}) => {
  const network = observeNetwork(page); await open(page, 'amber-horizon', 'hair-only');
  await expect(page.locator('#continuous-video')).toBeChecked();
  const download = page.waitForEvent('download', {timeout: 405_000}); await page.click('#continuous-start');
  for (const selector of ['#pipeline-select', '#variant-select', '#toggle-pipeline', '#stage-toggle-pipeline', '#hold-frame', '#benchmark', '#continuous-video'])
    await expect(page.locator(selector)).toBeDisabled();
  const {report, files} = await downloadReport(await download, 'complete');
  await expect(page.locator('#continuous-start')).toBeEnabled(); await expect(page.locator('#continuous-save-actions')).toBeVisible();
  await assertReport(page, report, 'amber-horizon', 'hair-only', true);
  expect(report.recording.requested).toBe(true); expect(report.recording.status).toBe('stopped'); expect(report.recording.audio).toBe(false);
  expect(report.recording.source).toBe('displayed-ar-canvas'); expect(report.recording.width).toBe(640); expect(report.recording.height).toBe(427);
  expect(report.recording.bytes).toBeGreaterThan(1000); expect(report.recording.chunks).toBeGreaterThan(1);
  expect(report.recording.stoppedAtMs! - report.recording.startedAtMs!).toBeGreaterThanOrEqual(350000);
  const videoName = [...files.keys()].find(name => name !== 'telemetry.json')!; expect(files.size).toBe(2);
  const videoBytes = files.get(videoName)!; expect(videoBytes.length).toBe(report.recording.bytes);
  const videoPath = test.info().outputPath(videoName); await writeFile(videoPath, videoBytes);
  await heldAndRestart(page); await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true);
  // Decode the saved artifact in Chromium after timing is complete, including
  // a frame near the end. This never draws a replacement image into AR.
  await page.route('**/ar_testing/qa-output-video', route => route.fulfill({path: videoPath, contentType: report.recording.mimeType!}));
  const decoded = await page.evaluate(async expectedSeconds => {
    const video = document.createElement('video'); video.muted = true; video.playsInline = true; video.preload = 'auto';
    const loaded = new Promise<void>((resolve, reject) => {video.onloadeddata = () => resolve(); video.onerror = () => reject(new Error(video.error?.message));});
    video.src = '/ar_testing/qa-output-video'; document.body.append(video); await loaded;
    const first = {width: video.videoWidth, height: video.videoHeight, duration: Number.isFinite(video.duration) ? video.duration : null};
    await video.play();
    await new Promise<void>(resolve => video.requestVideoFrameCallback(() => resolve())); video.pause();
    const sought = new Promise<void>((resolve, reject) => {video.onseeked = () => resolve(); video.onerror = () => reject(new Error(video.error?.message));});
    video.currentTime = expectedSeconds - 2; await sought;
    const lastTime = video.currentTime; video.remove(); return {...first, lastTime};
  }, (report.recording.stoppedAtMs! - report.recording.startedAtMs!) / 1000);
  expect(decoded.width).toBe(640); expect(decoded.height).toBe(427); expect(decoded.lastTime).toBeGreaterThan(345);
  expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
  await writeFile(test.info().outputPath('mobile-complete-receipt.json'), JSON.stringify({decoded, network,
    realProductionWindows: true, realVideoEncoder: true, exactHeldSafeguards: true,
    scope: 'Desktop Chromium/D3D11 at mobile viewport with synthetic camera. No physical phone, sustained thermal or wearer-motion acceptance claim.'}, null, 2));
});

for (const [eyewear, hair] of [['amber-horizon', 'selfie-multiclass'], ['tom-ford-clear', 'hair-only'], ['tom-ford-clear', 'selfie-multiclass']] as const) {
  test(`mobile ${eyewear}/${hair}: real G→Q window, partial save, same-image safeguards and cancellation cleanup`, async ({page}) => {
    test.setTimeout(180_000); const network = observeNetwork(page); await open(page, eyewear, hair);
    await page.locator('#continuous-video').uncheck(); await page.click('#continuous-start');
    await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()), {timeout: 55_000})
      .toMatchObject({windowIndex: 1, pipeline: 'publish', state: 'measuring'});
    await expect.poll(() => page.evaluate(() => {
      const report = window.arContinuousComparison.report() as unknown as RunReport;
      return report.rows.filter(row => row.windowIndex === 1 && row.phase === 'measured').length;
    })).toBeGreaterThanOrEqual(3);
    const download = page.waitForEvent('download'); await page.click('#continuous-stop');
    const {report, files} = await downloadReport(await download, 'partial');
    await expect(page.locator('#continuous-start')).toBeEnabled(); await assertReport(page, report, eyewear, hair, false);
    expect(report.windows[0]!.completed).toBe(true); expect(report.windows[1]!.completed).toBe(false);
    expect(report.windows[1]!.summary.frames).toBeGreaterThanOrEqual(3); expect(report.recording.status).toBe('disabled');
    expect(files.size).toBe(1); await heldAndRestart(page);
    if (eyewear === 'tom-ford-clear' && hair === 'selfie-multiclass') {
      const backgroundDownload = page.waitForEvent('download'); await page.click('#continuous-start');
      // Explicit synthetic lifecycle input, not evidence of a physical mobile
      // OS background transition. All handler cleanup and export remain real.
      await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        document.dispatchEvent(new Event('visibilitychange'));});
      const background = await downloadReport(await backgroundDownload, 'background');
      expect(background.report.partial).toBe(true); expect(background.report.completed).toBe(false);
      await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle'); await expect.poll(() => closed(page)).toBe(true);
      await page.evaluate(() => {Reflect.deleteProperty(document, 'hidden');});
      await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
      await expect(page.locator('#continuous-start')).toBeEnabled();
    } else {
      const prior = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)!);
      const immediateDownload = page.waitForEvent('download');
      const state = await page.locator('#continuous-start').evaluate(button => {
        (button as HTMLButtonElement).click(); const status = window.arContinuousComparison.status();
        document.querySelector<HTMLButtonElement>('#continuous-stop')!.click(); return status;
      });
      expect(state?.state).toBe('switching');
      const immediate = await downloadReport(await immediateDownload, 'switch-cancel');
      expect(immediate.report.partial).toBe(true); expect(immediate.report.windows.every(window => !window.completed)).toBe(true);
      await expect(page.locator('#continuous-start')).toBeEnabled();
      await expect.poll(() => page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial).length, prior.serial)).toBeGreaterThan(2);
      const next = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)!);
      expect(next.sessionId).toBe(prior.sessionId); expect(next.sequence).toBeGreaterThan(prior.sequence);
    }
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true);
    expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
    await writeFile(test.info().outputPath('mobile-partial-receipt.json'), JSON.stringify({eyewear, hair, network,
      realFirstWindowAndSwitch: true, exactHeldSafeguards: true, cleanup: true,
      scope: 'Synthetic camera at mobile viewport. Background input is simulated; physical mobile camera, thermal and movement behavior remain unmeasured.'}, null, 2));
  });
}
