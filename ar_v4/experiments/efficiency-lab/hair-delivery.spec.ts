import {test, expect} from '@playwright/test';
import type {Download, Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {RecordedRunFrame} from './continuous-run.ts';
import type {RecorderMetadata} from './continuous-recorder.ts';
import type {HairDeliveryExport, HairDeliveryTrace} from './hair-delivery.ts';

const URL_PATH = '/ar_testing/experiments/efficiency-lab/live.html?study=hair-delivery';
const ORDER = ['g', 'hair-release', 'hair-release', 'g'];
interface CameraObservation {streams: MediaStream[]; recordings: MediaStream[]; workers: {terminated: boolean}[];}
interface BitmapGate {armed: boolean; reached: boolean; released: boolean; restored: boolean; release(): void; restore(): void;}
declare global {interface Window {hairDeliveryCamera: CameraObservation; hairDeliveryBitmapGate?: BitmapGate;}}
interface Report {
  schema: string; sessionId: string; completed: boolean; partial: boolean; startedAtMs: number; endedAtMs: number;
  protocol: {studyOptions: string; warmupMs: number; measureMs: number; minimumTrackedMaskedWarmupFrames: number; order: string[]};
  workload: {eyewearId: string; hairModelId: string; variant: string; sourceWidth: number; sourceHeight: number};
  recording: RecorderMetadata; rows: RecordedRunFrame[];
  hairDelivery: HairDeliveryExport;
  hairDeliveryDrain: {state: string; startedAtMs: number; endedAtMs: number; reason: string | null};
  windows: {index: number; token: number; pipeline: string; completed: boolean; validWarmupFrames: number;
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
  await page.route('**/ar_testing/hair-delivery-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const state: CameraObservation = {streams: [], recordings: [], workers: []}; window.hairDeliveryCamera = state;
    const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/hair-delivery-camera.jpg';
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
  expect(await page.locator('#pipeline-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value)))
    .toEqual(['g', 'hair-release']);
  await expect(page.locator('#continuous-video')).not.toBeChecked();
  await expect(page.locator('#continuous-start')).toContainText('Measure only');
  await expect(page.locator('#mask-preview-study')).toHaveAttribute('href', '?study=mask-preview');
  await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
  await page.click('#start'); await expect(page.locator('#continuous-start')).toBeEnabled();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.hairDeliveryCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.hairDeliveryCamera.recordings.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.hairDeliveryCamera.workers.every(worker => worker.terminated));
async function cleanup(page: Page): Promise<void> {
  if (await page.locator('#stop').isVisible()) await page.click('#stop');
  await expect.poll(() => closed(page)).toBe(true);
}
function crc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;}
  return (crc ^ 0xffffffff) >>> 0;
}
/** Independent stored-ZIP reader; the archive library is not imported into QA. */
async function downloadReport(download: Download, label: string): Promise<{report: Report; files: Map<string, Buffer>}> {
  expect(download.suggestedFilename()).toMatch(/^ar-mobile-comparison-.*\.zip$/);
  const filename = test.info().outputPath(label + '.zip'); await download.saveAs(filename);
  const bytes = await readFile(filename), files = new Map<string, Buffer>(); let offset = 0;
  while (bytes.readUInt32LE(offset) === 0x04034b50) {
    const size = bytes.readUInt32LE(offset + 22), nameLength = bytes.readUInt16LE(offset + 26), extraLength = bytes.readUInt16LE(offset + 28);
    expect(bytes.readUInt16LE(offset + 8)).toBe(0); expect(bytes.readUInt32LE(offset + 18)).toBe(size);
    const name = bytes.toString('utf8', offset + 30, offset + 30 + nameLength);
    expect(name).toMatch(/^(telemetry\.json|ar-mirror\.(webm|mp4))$/); expect(files.has(name)).toBe(false);
    const start = offset + 30 + nameLength + extraLength, data = bytes.subarray(start, start + size);
    expect(data.length).toBe(size); expect(crc32(data)).toBe(bytes.readUInt32LE(offset + 14));
    files.set(name, data); offset = start + size;
  }
  expect(bytes.readUInt32LE(offset)).toBe(0x02014b50);
  expect(files.has('telemetry.json')).toBe(true);
  const json = files.get('telemetry.json')!; await writeFile(test.info().outputPath(label + '-telemetry.json'), json);
  return {report: JSON.parse(json.toString('utf8')) as Report, files};
}
function privacyViolations(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((item, index) => privacyViolations(item, `${path}[${index}]`));
  if (!value || typeof value !== 'object') return typeof value === 'string' && /^data:|;base64,/i.test(value) ? [path] : [];
  return Object.entries(value).flatMap(([key, item]) => /SHA256|PngDataUrl|landmarks|categoryBase64|imageData|maskBytes|sourceImage|sourceIdentity|deviceId|groupId/i.test(key)
    ? [path + '.' + key] : privacyViolations(item, path + '.' + key));
}
function assertReport(report: Report, complete: boolean, drain: 'drained' | 'incomplete' = 'drained'): void {
  expect(report.schema).toBe('ar-continuous-comparison-v1'); expect(report.completed).toBe(complete); expect(report.partial).toBe(!complete);
  expect(report.protocol.studyOptions).toBe('hair-delivery'); expect(report.protocol.order).toEqual(ORDER);
  expect(report.protocol.warmupMs).toBe(5000); expect(report.protocol.measureMs).toBe(30000);
  expect(report.protocol.minimumTrackedMaskedWarmupFrames).toBe(3); expect(report.windows).toHaveLength(4);
  expect(report.workload.sourceWidth).toBe(720); expect(report.workload.sourceHeight).toBe(1280);
  expect(report.hairDeliveryDrain.state).toBe(drain);
  if (drain === 'drained') expect(report.hairDeliveryDrain.reason).toBeNull();
  else expect(report.hairDeliveryDrain.reason).toBeTruthy();
  expect(report.hairDelivery.sessionId).toBe(report.sessionId); expect(report.hairDelivery.rejected).toBe(0);
  expect(report.hairDelivery.truncated).toBe(false); expect(report.retention.truncated).toBe(false);
  expect(report.retention.rejectedRows).toBe(0); expect(report.retention.frameRows).toBe(report.rows.length);
  expect(privacyViolations(report)).toEqual([]);
  for (const request of report.hairDelivery.requests) {
    expect(request.sessionId).toBe(report.sessionId); expect(['g', 'hair-release']).toContain(request.pipeline);
    expect(request.capturedAtMs).toBeGreaterThanOrEqual(report.startedAtMs);
    expect(request.timing!.releaseWorkerEarly).toBe(request.pipeline === 'hair-release');
    if (drain === 'drained' && request.timing!.submittedAtMs !== null) expect(request.timing!.outcome).not.toBe('pending');
    if (request.usedAtPublication) {
      expect(request.timing!.outcome).toBe('completed');
      expect(request.timing!.completedAtMs!).toBeLessThanOrEqual(request.publicationAtMs!);
    }
  }
  for (const row of report.rows) if (row.fields.pipeline === 'hair-release') {
    expect(row.native?.['hairDelivery.releaseWorkerEarly']).toBe(true);
    expect(Number(row.native?.['hairDelivery.maxOwnedImages'])).toBeLessThanOrEqual(2);
    expect(Number(row.native?.['hairDelivery.maxPendingResults'])).toBeLessThanOrEqual(2);
  }
}
function overlapEvidence(requests: HairDeliveryTrace[]): Record<string, unknown> {
  const u = requests.filter(request => request.pipeline === 'hair-release' && request.timing?.outcome === 'completed');
  const releasedBeforeCompletion = u.filter(request => request.timing!.workerReleasedAtMs! < request.timing!.completedAtMs!);
  let nextSubmissionDuringPriorHash = 0;
  for (const earlier of u) if (earlier.timing!.hashStartedAtMs !== null) {
    if (u.some(later => later.generation === earlier.generation && later.sequence > earlier.sequence
      && later.timing!.submittedAtMs! >= earlier.timing!.hashStartedAtMs!
      && later.timing!.submittedAtMs! < earlier.timing!.completedAtMs!)) nextSubmissionDuringPriorHash++;
  }
  return {completedURequests: u.length, releasedBeforeCompletion: releasedBeforeCompletion.length, nextSubmissionDuringPriorHash,
    observation: nextSubmissionDuringPriorHash ? 'Overlapping submission and prior result hashing observed.' : 'No next-request overlap observed in this run.',
    limitation: 'Worker release precedes result completion; these scalar intervals do not establish simultaneous GPU execution or physical-camera smoothness.'};
}

test('hair delivery: full four-window measurement-only run preserves every request outcome and movement cue', async ({page}) => {
  test.setTimeout(480_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await open(page);
  try {
    const downloaded = page.waitForEvent('download', {timeout: 210_000}); await page.click('#continuous-start');
    for (const selector of ['#pipeline-select', '#variant-select', '#continuous-video', '#hold-frame']) await expect(page.locator(selector)).toBeDisabled();
    await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.state)).toBe('measuring');
    for (const cue of ['Face forward · check nose/front', 'Slowly look down', 'Slowly look up', 'Slowly turn left', 'Slowly turn right'])
      await expect(page.locator('#run-movement')).toHaveText(cue, {timeout: 8000});
    const {report, files} = await downloadReport(await downloaded, 'g-u-full'); assertReport(report, true);
    expect([...files.keys()]).toEqual(['telemetry.json']); expect(report.recording.requested).toBe(false);
    expect(report.recording.status).toBe('disabled'); expect(report.recording.bytes).toBe(0);
    expect(report.hairDelivery.requests.length).toBeGreaterThan(0);
    for (const pipeline of ['g', 'hair-release']) expect(report.hairDelivery.requests.some(request => request.pipeline === pipeline)).toBe(true);
    for (const [index, window] of report.windows.entries()) {
      expect(window.index).toBe(index); expect(window.token).toBe(index + 1); expect(window.pipeline).toBe(ORDER[index]);
      expect(window.completed).toBe(true); expect(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
      expect(window.summary.durationMs).toBe(30000);
      const measured = report.rows.filter(row => row.phase === 'measured' && row.windowIndex === index);
      expect(window.summary.frames).toBe(measured.length);
      expect(window.summary.completedArFps).toBeCloseTo(measured.length / 30, 9);
      for (const row of measured) {
        expect(row.fields.pipeline).toBe(window.pipeline);
        expect(Number(row.fields.capturedAtMs)).toBeGreaterThanOrEqual(window.measureStartedAtMs!);
        expect(Number(row.fields.publishedAtMs)).toBeLessThan(window.endedAtMs!);
      }
    }
    await writeFile(test.info().outputPath('g-u-overlap-evidence.json'), JSON.stringify(overlapEvidence(report.hairDelivery.requests), null, 2));
    await expect(page.locator('#continuous-start')).toBeEnabled();
    expect(await page.evaluate(() => window.hairDeliveryCamera.recordings.length)).toBe(0);
    await page.screenshot({path: test.info().outputPath('focused-preview-after-run.png'), fullPage: true});
    expect(errors).toEqual([]);
  } finally {await cleanup(page);}
});

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass']) {
  test(`hair delivery: exact held G/U portrait pair and restart for ${eyewear}/${hair}`, async ({page}) => {
    test.setTimeout(120_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await open(page, eyewear, hair);
    try {
      for (const pipeline of ['hair-release', 'g', 'hair-release']) {
        await page.selectOption('#pipeline-select', pipeline);
        await expect.poll(() => page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)?.pipeline)).toBe(pipeline);
        await expect(page.locator('#continuous-start')).toBeEnabled();
      }
      await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
      await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
      const outputs = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as Record<string, HeldOutput>;
      const g = outputs.g!, u = outputs['hair-release']!; expect(g.stats.hasMask).toBe(true); expect(u.stats.hasMask).toBe(true);
      for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const) expect(u[key], key).toEqual(g[key]);
      for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const) expect(u.captureSnapshot[key], key).toEqual(g.captureSnapshot[key]);
      for (const output of [g, u]) for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
        expect(output.stats[key].changedPixels).toBe(0);
      await expect.poll(() => closed(page)).toBe(true);
      if (eyewear === 'amber-horizon' && hair === 'hair-only') await page.screenshot({path: test.info().outputPath('focused-preview-held.png'), fullPage: true});
      const oldSession = await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId);
      await page.click('#resume-live'); await expect(page.locator('#continuous-start')).toBeEnabled();
      expect(await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId)).not.toBe(oldSession);
      expect(errors).toEqual([]);
    } finally {await cleanup(page);}
  });
}

test('hair delivery: optional partial video and stopping during the initial switch retain evidence and resume once', async ({page}) => {
  test.setTimeout(120_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await open(page);
  try {
    await page.check('#continuous-video'); await expect(page.locator('#continuous-start')).toContainText('Video + measurements');
    const videoDownload = page.waitForEvent('download'); await page.click('#continuous-start');
    await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.phaseElapsedMs ?? 0)).toBeGreaterThan(2000);
    await page.click('#continuous-stop');
    const videoRun = await downloadReport(await videoDownload, 'g-u-video-partial'); assertReport(videoRun.report, false);
    expect(videoRun.report.recording.requested).toBe(true); expect(videoRun.report.recording.audio).toBe(false);
    expect(videoRun.report.recording.bytes).toBeGreaterThan(0); expect(videoRun.report.recording.status).toBe('stopped');
    expect([...videoRun.files.keys()].some(name => /^ar-mirror\.(webm|mp4)$/.test(name))).toBe(true);
    await expect(page.locator('#continuous-start')).toBeEnabled(); await page.uncheck('#continuous-video');
    const stopDownload = page.waitForEvent('download');
    // Two DOM actions in one task guarantee Stop observes the async switch drain;
    // no production timer, worker reply, callback or controller is changed.
    const stoppedIn = await page.evaluate(() => {
      document.getElementById('continuous-start')!.click(); const state = window.arContinuousComparison.status()?.state;
      document.getElementById('continuous-stop')!.click(); return state;
    });
    expect(stoppedIn).toBe('switching');
    const stopped = await downloadReport(await stopDownload, 'g-u-stop-switch'); assertReport(stopped.report, false);
    expect(stopped.report.rows).toHaveLength(0); expect(stopped.report.recording.requested).toBe(false);
    await expect(page.locator('#continuous-start')).toBeEnabled();
    const serial = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)!.serial);
    await expect.poll(() => page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial).length, serial)).toBeGreaterThan(1);
    expect(errors).toEqual([]);
  } finally {await cleanup(page);}
});

test('hair delivery: real 20-second finalization watchdog saves incomplete diagnostics and rejects a late bitmap after reopen', async ({page}) => {
  test.setTimeout(120_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await open(page);
  try {
    await page.click('#continuous-start');
    await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.state)).toBe('warmup');
    const oldSession = await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId);
    await page.evaluate(() => {
      const original = window.createImageBitmap;
      let held: ImageBitmap | null = null, deliver: ((bitmap: ImageBitmap) => void) | null = null;
      const gate: BitmapGate = {armed: true, reached: false, released: false, restored: false,
        release() {
          gate.released = true;
          if (held && deliver) {const bitmap = held, resolve = deliver; held = null; deliver = null; resolve(bitmap);}
        },
        restore() {gate.restored = true; window.createImageBitmap = original; gate.release();},
      };
      window.hairDeliveryBitmapGate = gate;
      window.createImageBitmap = new Proxy(original, {apply(target, receiver, args) {
        const actual = Reflect.apply(target, receiver, args) as Promise<ImageBitmap>;
        if (!gate.armed) return actual;
        gate.armed = false;
        // Perform the real conversion and hold only its one resolved bitmap.
        // All other conversions, worker responses and timers continue normally.
        return actual.then(bitmap => {
          if (gate.released || gate.restored) return bitmap;
          held = bitmap; gate.reached = true;
          return new Promise<ImageBitmap>(resolve => {deliver = resolve;});
        });
      }});
    });
    await expect.poll(() => page.evaluate(() => window.hairDeliveryBitmapGate!.reached)).toBe(true);
    const downloaded = page.waitForEvent('download', {timeout: 60_000}); await page.click('#continuous-stop');
    const {report, files} = await downloadReport(await downloaded, 'g-u-finalization-timeout');
    assertReport(report, false, 'incomplete'); expect(report.sessionId).toBe(oldSession);
    expect([...files.keys()]).toEqual(['telemetry.json']);
    expect(report.hairDeliveryDrain.reason).toContain('20 seconds');
    expect(report.hairDeliveryDrain.endedAtMs - report.hairDeliveryDrain.startedAtMs).toBeGreaterThanOrEqual(19900);
    await expect.poll(() => closed(page)).toBe(true);
    await expect(page.locator('#start')).toBeEnabled(); await expect(page.locator('#continuous-status')).toContainText('could not finish');
    expect(await page.evaluate(() => window.hairDeliveryBitmapGate!.released)).toBe(false);
    const oldResources = await page.evaluate(() => ({streams: window.hairDeliveryCamera.streams.length, workers: window.hairDeliveryCamera.workers.length}));
    // A fresh camera session must already work while the old conversion is held.
    await page.click('#start'); await expect(page.locator('#continuous-start')).toBeEnabled();
    const fresh = await page.evaluate(() => ({session: window.hairLivePreview.diagnostics().sessionId,
      serial: window.arPerformanceProfiler.samplesAfter(0).at(-1)!.serial}));
    expect(fresh.session).not.toBe(oldSession);
    await page.evaluate(() => window.hairDeliveryBitmapGate!.release());
    await expect.poll(() => page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial).length, fresh.serial)).toBeGreaterThan(1);
    const rows = await page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial), fresh.serial);
    expect(rows.every(row => row.sessionId === fresh.session)).toBe(true);
    expect(await page.evaluate(counts =>
      window.hairDeliveryCamera.streams.slice(0, counts.streams).every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
      && window.hairDeliveryCamera.workers.slice(0, counts.workers).every(worker => worker.terminated), oldResources)).toBe(true);
    expect(await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId)).toBe(fresh.session);
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(() => window.hairDeliveryBitmapGate?.restore());
    await cleanup(page);
  }
});
