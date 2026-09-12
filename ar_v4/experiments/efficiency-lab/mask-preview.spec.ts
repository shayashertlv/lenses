import {test, expect} from '@playwright/test';
import type {Download, Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {RecordedRunFrame} from './continuous-run.ts';
import type {RecorderMetadata} from './continuous-recorder.ts';
import type {HairDeliveryExport} from './hair-delivery.ts';
import type {FrameSample} from './frame-profiler.ts';
import {PIPELINES} from './profiles.ts';

const URL_PATH = '/ar_testing/';
const CHOICES = ['g', 'mask-bytes'] as const;
const ORDER = [...CHOICES, ...[...CHOICES].reverse()];
type Pipeline = typeof CHOICES[number];
interface CameraObservation {streams: MediaStream[]; recordings: MediaStream[]; workers: {terminated: boolean}[];}

interface HashGate {reached: boolean; release(): void; restore(): void;}
declare global {interface Window {maskPreviewCamera: CameraObservation; maskPreviewHashGate?: HashGate;}}
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
  await page.route('**/ar_testing/mask-preview-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const state: CameraObservation = {streams: [], recordings: [], workers: []}; window.maskPreviewCamera = state;
    const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/mask-preview-camera.jpg';
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
async function open(page: Page, eyewear = 'amber-horizon', hair = 'hair-only'): Promise<void> {
  await installCamera(page); await page.goto(URL_PATH);
  await expect(page).toHaveURL(/\/ar_testing\/experiments\/efficiency-lab\/live\.html\?study=mask-preview$/);
  await expect(page.locator('#pipeline-select')).toHaveValue('g');
  expect(await page.evaluate(() => window.maskPreviewCamera.streams.length)).toBe(0);
  expect(await page.locator('#pipeline-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value)))
    .toEqual(CHOICES);
  await expect(page.locator('#continuous-video')).not.toBeChecked();
  await expect(page.locator('#continuous-start')).toContainText('Measure only');
  await expect(page.locator('.study-links a')).toHaveCount(1);
  await expect(page.locator('#mask-preview-study')).toHaveAttribute('href', '?study=mask-preview');
  await expect(page.locator('#mask-preview-study')).toHaveAttribute('aria-current', 'page');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
  await page.click('#start'); await expect(page.locator('#continuous-start')).toBeEnabled();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.maskPreviewCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.maskPreviewCamera.recordings.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.maskPreviewCamera.workers.every(worker => worker.terminated));
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

function assertReport(report: Report, complete: boolean): void {
  expect(report.schema).toBe('ar-continuous-comparison-v1'); expect(report.completed).toBe(complete); expect(report.partial).toBe(!complete);
  expect(report.protocol.studyOptions).toBe('mask-preview'); expect(report.protocol.order).toEqual(ORDER);
  expect(report.protocol.warmupMs).toBe(5000); expect(report.protocol.measureMs).toBe(30000);
  expect(report.protocol.minimumTrackedMaskedWarmupFrames).toBe(3); expect(report.windows).toHaveLength(4);
  expect(report.workload.sourceWidth).toBe(720); expect(report.workload.sourceHeight).toBe(1280);
  expect(report.hairDeliveryDrain.state).toBe('drained'); expect(report.hairDeliveryDrain.reason).toBeNull();
  expect(report.hairDelivery.sessionId).toBe(report.sessionId); expect(report.hairDelivery.rejected).toBe(0);
  expect(report.hairDelivery.truncated).toBe(false); expect(report.retention.truncated).toBe(false);
  expect(report.retention.rejectedRows).toBe(0); expect(report.retention.frameRows).toBe(report.rows.length);
  expect(privacyViolations(report)).toEqual([]);
  const requests = new Map<number, typeof report.hairDelivery.requests[number]>();
  for (const request of report.hairDelivery.requests) {
    expect(request.sessionId).toBe(report.sessionId); expect(CHOICES).toContain(request.pipeline);
    expect(request.capturedAtMs).toBeGreaterThanOrEqual(report.startedAtMs); expect(requests.has(request.sequence)).toBe(false);
    requests.set(request.sequence, request); expect(request.timing).not.toBeNull();
    const timing = request.timing!;
    expect(timing.releaseWorkerEarly).toBe(false); expect(timing.categoryExtractionMode).toBe(request.pipeline === 'mask-bytes' ? 'rgba8' : 'sdk');
    if (timing.submittedAtMs !== null) expect(timing.outcome).not.toBe('pending');
    if (request.usedAtPublication) {
      expect(timing.outcome).toBe('completed'); expect(timing.completedAtMs!).toBeLessThanOrEqual(request.publicationAtMs!);
    }
    if (request.publicationAtMs !== null && timing.completedAtMs !== null && timing.completedAtMs > request.publicationAtMs)
      expect(request.usedAtPublication).toBe(false);
  }
  for (const [index, row] of report.rows.entries()) {
    expect(row.serial).toBe(index + 1); expect(row.invalidFields).toEqual([]);
    expect(row.fields.sessionId).toBe(report.sessionId); expect(row.fields.sourceWidth).toBe(720); expect(row.fields.sourceHeight).toBe(1280);
    expect(row.fields.eyewearId).toBe(report.workload.eyewearId); expect(row.fields.hairModelId).toBe(report.workload.hairModelId);
    expect(row.native?.['admission.rateHz']).toBeNull(); expect(row.native?.['pump.mode']).toBe('overlap');
    expect(row.native?.['pump.deferPrefetch']).toBe(false); expect(row.native?.['hairDelivery.releaseWorkerEarly']).toBe(false);
    expect(Number(row.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
    expect(Number(row.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
    if (row.phase !== 'measured') continue;
    const window = report.windows[row.windowIndex!]!;
    expect(row.exclusion).toBeNull(); expect(row.fields.pipeline).toBe(window.pipeline);
    expect(Number(row.fields.capturedAtMs)).toBeGreaterThanOrEqual(window.measureStartedAtMs!);
    expect(Number(row.fields.publishedAtMs)).toBeLessThan(window.endedAtMs!);
    if (row.fields.hasMask) {
      const request = requests.get(Number(row.fields.sequence)); expect(request).toBeDefined();
      expect(request!.usedAtPublication).toBe(true); expect(request!.pipeline).toBe(row.fields.pipeline);
    }
  }
  if (complete) expect(report.endedAtMs - report.startedAtMs).toBeGreaterThanOrEqual(140000);
  for (const [index, window] of report.windows.entries()) {
    expect(window.index).toBe(index); expect(window.token).toBe(index + 1); expect(window.pipeline).toBe(ORDER[index]);
    expect(window.round).toBe(index < 2 ? 1 : 2); if (complete) expect(window.completed).toBe(true);
    if (!window.completed) continue;
    expect(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
    expect(window.measureStartedAtMs! - window.switchedAtMs!).toBeGreaterThanOrEqual(5000);
    expect(window.measureStartedAtMs!).toBeGreaterThanOrEqual(window.thirdWarmupAtMs!);
    expect(window.endedAtMs! - window.measureStartedAtMs!).toBe(30000); expect(window.summary.durationMs).toBe(30000);
    const measured = report.rows.filter(row => row.phase === 'measured' && row.windowIndex === index);
    expect(measured.length).toBeGreaterThan(0); expect(window.summary.frames).toBe(measured.length);
    expect(window.summary.completedArFps).toBeCloseTo(measured.length / 30, 9);
  }
}

function assertActualPath(sample: Pick<FrameSample, 'pipeline' | 'hasMask' | 'faceDelegate' | 'hairDelegate' | 'native' | 'sourceWidth' | 'sourceHeight'>): void {
  expect(sample.hasMask).toBe(true); expect(sample.faceDelegate).toBe('GPU'); expect(sample.hairDelegate).toBe('GPU');
  const native = sample.native!;
  expect(native['nativePipeline.native.speedLab.pbo.completed']).toBe(true);
  if (sample.pipeline === 'mask-bytes') {
    expect(native['hairCategory.requestedMode']).toBe('rgba8'); expect(native['hairCategory.path']).toBe('rgba8-readback');
    expect(Number(native['hairCategory.rgba8ReadbackBytes'])).toBe(sample.sourceWidth * sample.sourceHeight * 4); expect(native['hairCategory.rgba8FallbackReason']).toBeNull();
  } else expect(native['hairCategory.requestedMode']).toBe('sdk');

}

async function livePath(page: Page, pipeline: Pipeline): Promise<FrameSample> {
  const serial = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0);
  await page.selectOption('#pipeline-select', pipeline);
  await expect.poll(() => page.evaluate(({serial, pipeline}) => window.arPerformanceProfiler.samplesAfter(serial)
    .filter(sample => sample.pipeline === pipeline && sample.hasMask).length, {serial, pipeline})).toBeGreaterThanOrEqual(3);
  const sample = await page.evaluate(({serial, pipeline}) => window.arPerformanceProfiler.samplesAfter(serial)
    .filter(sample => sample.pipeline === pipeline && sample.hasMask).at(-1)!, {serial, pipeline});
  assertActualPath(sample); await expect(page.locator('#continuous-start')).toBeEnabled(); return sample;
}

async function heldAndRestart(page: Page): Promise<void> {
  const oldSession = await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId);
  await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
  await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
  const diagnostic = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as Record<string, unknown>;
  expect(diagnostic.schema).toBe('ar-efficiency-comparison-v1'); expect(diagnostic.candidateAccepted).toBe(false);
  expect(diagnostic.ownerSelectedG).toBe(true); expect(diagnostic.comparedPipelines).toEqual(CHOICES);
  expect(diagnostic.inputPolicy).toContain('does not independently test V live mask extraction');
  expect(PIPELINES.filter(id => Object.hasOwn(diagnostic, id))).toEqual(CHOICES);
  const outputs = diagnostic as Record<string, HeldOutput>;
  await expect(page.locator('#hold-hint')).toContainText('G and V share this exact image and diagnostic mask');
  await expect(page.locator('#hold-hint')).not.toContainText('Rate options');
  await page.screenshot({path: test.info().outputPath('mask-preview-held.png'), fullPage: true});
  const g = outputs.g!;
  // V deliberately shares G's SDK full diagnostic mask on Hold. The independent
  // same-MPMask extraction study, not this held alias, proves V category equality.
  for (const pipeline of CHOICES) {
    const output = outputs[pipeline]!; expect(output.stats.hasMask).toBe(true);
    for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
      expect(output[key], `${pipeline}/${key}`).toEqual(g[key]);
    for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
      expect(output.captureSnapshot[key], `${pipeline}/${key}`).toEqual(g.captureSnapshot[key]);
    for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
      expect(output.stats[key].changedPixels).toBe(0);
    await page.selectOption('#pipeline-select', pipeline);
    for (const variant of ['accepted', 'hair'] as const) {
      await page.selectOption('#variant-select', variant);
      expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png')))
        .toBe(output[variant === 'hair' ? 'hairPngDataUrl' : 'acceptedPngDataUrl']);
    }
  }
  await expect.poll(() => closed(page)).toBe(true);
  await page.selectOption('#pipeline-select', 'g'); await page.click('#resume-live');
  await expect(page.locator('#continuous-start')).toBeEnabled(); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  expect(await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId)).not.toBe(oldSession);
}

test('mask preview: default and candidate links stay focused while explicit historical studies remain accessible', async ({page}) => {
  const network = observeNetwork(page); await installCamera(page); await page.goto(URL_PATH);
  await expect(page).toHaveURL(/\?study=mask-preview$/);
  await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await expect(page.locator('.study-links a')).toHaveCount(1);
  await page.goto('/ar_testing/experiments/efficiency-lab/live.html?study=mask-preview&pipeline=mask-bytes');
  await expect(page.locator('#pipeline-select')).toHaveValue('mask-bytes');
  await expect(page.locator('#continuous-video')).not.toBeChecked();
  await page.click('.brand'); await expect(page).toHaveURL(/\?study=mask-preview$/);
  await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await page.goto('/ar_testing/experiments/efficiency-lab/live.html?study=review&legacy=1');
  expect(await page.locator('#pipeline-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value)))
    .toEqual(['g', 'publish', 'region', 'lens', 'ui']);
  await page.click('#mask-preview-study');
  await expect(page).toHaveURL(/\?study=mask-preview$/);
  expect(await page.locator('#pipeline-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value)))
    .toEqual(CHOICES);
  expect(await page.evaluate(() => window.maskPreviewCamera.streams.length)).toBe(0);
  expect(await page.evaluate(() => window.maskPreviewCamera.workers.length)).toBe(0);
  expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
});

test('mask preview: full four-window measurement-only run preserves source cadence, all hair outcomes and production boundaries', async ({page}) => {
  test.setTimeout(300_000); const network = observeNetwork(page); await open(page);
  try {
    const downloaded = page.waitForEvent('download', {timeout: 210_000}); await page.click('#continuous-start');
    for (const selector of ['#pipeline-select', '#variant-select', '#continuous-video', '#hold-frame', '#toggle-pipeline', '#stage-toggle-pipeline'])
      await expect(page.locator(selector)).toBeDisabled();
    await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.state)).toBe('measuring');
    for (const cue of ['Face forward · check nose/front', 'Slowly look down', 'Slowly look up', 'Slowly turn left', 'Slowly turn right'])
      await expect(page.locator('#run-movement')).toHaveText(cue, {timeout: 8000});
    const {report, files} = await downloadReport(await downloaded, 'g-v-full'); assertReport(report, true);
    expect([...files.keys()]).toEqual(['telemetry.json']); expect(report.recording.requested).toBe(false);
    expect(report.recording.status).toBe('disabled'); expect(report.recording.bytes).toBe(0);
    expect(report.hairDelivery.requests.length).toBeGreaterThan(0);
    for (const pipeline of CHOICES) {
      expect(report.hairDelivery.requests.some(request => request.pipeline === pipeline)).toBe(true);
      const row = report.rows.find(row => row.phase === 'measured' && row.fields.pipeline === pipeline && row.fields.hasMask);
      expect(row).toBeDefined(); assertActualPath({...row!.fields, native: row!.native} as unknown as FrameSample);
    }
    const releaseResponse = await page.request.get('/ar_testing/release.json'); expect(releaseResponse.ok()).toBe(true);
    const release = await releaseResponse.json() as {sourceFingerprint: string; createdAt: string; baseCommit: string};
    expect(report.metadata.build).toEqual({id: release.sourceFingerprint, createdAt: release.createdAt});
    expect(report.baseCommit).toBe(release.baseCommit); expect(report.metadata.device.crossOriginIsolated).toBe(true);
    expect(report.metadata.device.secureContext).toBe(true); await expect(page.locator('#continuous-start')).toBeEnabled();
    expect(await page.evaluate(() => window.maskPreviewCamera.recordings.length)).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: test.info().outputPath('mask-preview-after-full-run.png'), fullPage: true});
    expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
  } finally {await cleanup(page);}
});

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass']) {
  test(`mask preview: real G/V paths, exact held portrait and restart for ${eyewear}/${hair}`, async ({page}) => {
    test.setTimeout(180_000); const network = observeNetwork(page); await open(page, eyewear, hair);
    try {
      const samples: FrameSample[] = [];
      for (const pipeline of [...CHOICES, 'g'] as const) samples.push(await livePath(page, pipeline));
      expect(samples[0]!.gpuRenderer).toMatch(/Direct3D11|D3D11/i); expect(samples[0]!.gpuRenderer).not.toMatch(/SwiftShader|software/i);
      await writeFile(test.info().outputPath('mask-preview-live-paths.json'), JSON.stringify(samples, null, 2));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: test.info().outputPath('mask-preview-mobile-layout.png'), fullPage: true});
      await heldAndRestart(page); expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
    } finally {await cleanup(page);}
  });
}

test('mask preview: optional partial video and Stop during switch preserve evidence and resume once', async ({page}) => {
  test.setTimeout(120_000); const network = observeNetwork(page); await open(page);
  try {
    await page.check('#continuous-video'); const downloaded = page.waitForEvent('download'); await page.click('#continuous-start');
    await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.phaseElapsedMs ?? 0)).toBeGreaterThan(2000);
    await page.click('#continuous-stop'); const video = await downloadReport(await downloaded, 'g-v-video-partial'); assertReport(video.report, false);
    expect(video.report.recording.requested).toBe(true); expect(video.report.recording.audio).toBe(false);
    expect(video.report.recording.bytes).toBeGreaterThan(0); expect(video.report.recording.status).toBe('stopped');
    expect(video.report.recording.width).toBe(720); expect(video.report.recording.height).toBe(1280);
    const videoName = [...video.files.keys()].find(name => /^ar-mirror\.(webm|mp4)$/.test(name));
    expect(videoName).toBeDefined(); const videoBytes = video.files.get(videoName!)!;
    expect(videoBytes.length).toBe(video.report.recording.bytes);
    await page.route('**/ar_testing/mask-preview-video', route => route.fulfill({body: videoBytes,
      contentType: video.report.recording.mimeType!}));
    const decoded = await page.evaluate(async () => {
      const element = document.createElement('video'); element.muted = true; element.playsInline = true;
      const loaded = new Promise<void>((resolve, reject) => {
        element.onloadeddata = () => resolve(); element.onerror = () => reject(new Error(element.error?.message));
      });
      element.src = '/ar_testing/mask-preview-video'; document.body.append(element); await loaded;
      await element.play(); await new Promise<void>(resolve => element.requestVideoFrameCallback(() => resolve()));
      const dimensions = {width: element.videoWidth, height: element.videoHeight}; element.pause(); element.remove(); return dimensions;
    });
    expect(decoded).toEqual({width: 720, height: 1280});
    await expect(page.locator('#continuous-start')).toBeEnabled(); await page.uncheck('#continuous-video');
    const stoppedDownload = page.waitForEvent('download');
    const stoppedIn = await page.evaluate(() => {
      document.getElementById('continuous-start')!.click(); const state = window.arContinuousComparison.status()?.state;
      document.getElementById('continuous-stop')!.click(); return state;
    });
    expect(stoppedIn).toBe('switching'); const stopped = await downloadReport(await stoppedDownload, 'g-v-stop-switch'); assertReport(stopped.report, false);
    expect(stopped.report.rows).toHaveLength(0); expect(stopped.report.recording.requested).toBe(false);
    await expect(page.locator('#continuous-start')).toBeEnabled(); await livePath(page, 'g');
    expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
  } finally {await cleanup(page);}
});

test('mask preview: a real late mask hash keeps its original publication disposition in the drained archive', async ({page}) => {
  test.setTimeout(120_000); const network = observeNetwork(page); await open(page);
  try {
    await page.click('#continuous-start'); await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.state)).toBe('measuring');
    // Deliberately delay one completed WebCrypto mask hash in this lifecycle case
    // only. The full timing run above changes no production clocks or callbacks.
    await page.evaluate(() => {
      const original = crypto.subtle.digest.bind(crypto.subtle); let release!: () => void, armed = true;
      const waiting = new Promise<void>(resolve => {release = resolve;});
      const gate: HashGate = {reached: false, release, restore: () => {crypto.subtle.digest = original; release();}};
      window.maskPreviewHashGate = gate;
      crypto.subtle.digest = async (algorithm: AlgorithmIdentifier, data: BufferSource): Promise<ArrayBuffer> => {
        const result = await original(algorithm, data);
        if (armed && data.byteLength === 720 * 1280) {armed = false; gate.reached = true; await waiting;}
        return result;
      };
    });
    const serial = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)!.serial);
    await expect.poll(() => page.evaluate(() => window.maskPreviewHashGate!.reached)).toBe(true);
    await expect.poll(() => page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial).some(sample => sample.hasFace && !sample.hasMask), serial)).toBe(true);
    await page.evaluate(() => window.maskPreviewHashGate!.release());
    const downloaded = page.waitForEvent('download'); await page.click('#continuous-stop');
    const {report} = await downloadReport(await downloaded, 'g-v-late-mask'); assertReport(report, false);
    const late = report.hairDelivery.requests.filter(request => request.publicationAtMs !== null && request.timing?.outcome === 'completed'
      && request.timing.completedAtMs! > request.publicationAtMs);
    expect(late.length).toBeGreaterThan(0); for (const request of late) expect(request.usedAtPublication).toBe(false);
    await expect(page.locator('#continuous-start')).toBeEnabled(); expect(network).toEqual({errors: [], badResponses: [], unscoped: []});
  } finally {await page.evaluate(() => window.maskPreviewHashGate?.restore()); await cleanup(page);}
});
