import {test, expect} from '@playwright/test';
import type {Page, Route} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {StartupReceipt} from './startup-watchdog.ts';
import type {StartupPumpDiagnostic} from './startup-pump-diagnostic.ts';

interface StartupCamera {
  streams: MediaStream[]; workers: {terminated: boolean}[];
  pendingTimers: Map<number, number>;
}
declare global {interface Window {startupCamera: StartupCamera;}}
interface StartupReport {
  schema: string; build: {id: string; createdAt: string}; startup: StartupReceipt;
  milestones: {name: string; atMs: number}[]; errors: {startup: string | null; hair: string | null};
  workload: {eyewearId: string; hairModelId: string; variant: string; pipeline: string};
}
const sensitiveMarker = 'startup-qa-private-marker-7d29';

/** Camera input and request latency are simulated. All setup, renderer/worker
 * deadlines, watchdog timers, cancellation and first-frame work remain real. */
async function installCamera(page: Page, portraitClock = false): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/startup-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(({marker, portraitClock}) => {
    const canvas = document.createElement('canvas'); canvas.width = portraitClock ? 720 : 640; canvas.height = portraitClock ? 1280 : 427;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/startup-camera.jpg';
    const state: StartupCamera = {streams: [], workers: [], pendingTimers: new Map()}; window.startupCamera = state;
    const draw = (): void => {if (image.complete && image.naturalWidth) {
      context.fillStyle = '#808080'; context.fillRect(0, 0, canvas.width, canvas.height);
      if (portraitClock) context.drawImage(image, 0, 400, 720, 480);
      else context.drawImage(image, 0, 0, 640, 427);
    }};
    if (portraitClock) {
      // Model WebKit's playing MediaStream clock while keeping real video
      // callbacks, pixels, workers and renderer. A getter delta is guaranteed
      // even when this test machine rounds performance.now().
      const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!;
      let reads = 0;
      Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {...descriptor,
        get(this: HTMLMediaElement) {return this.srcObject instanceof MediaStream
          ? performance.now() / 1000 + ++reads / 1e6 : descriptor.get!.call(this) as number;}});
    }
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream);
      for (const track of stream.getVideoTracks()) {
        const nativeSettings = track.getSettings.bind(track);
        track.getSettings = () => ({...nativeSettings(), deviceId: marker, groupId: marker});
        Object.defineProperty(track, 'label', {configurable: true, value: marker});
      }
      return stream;
    }});
    const Native = window.Worker;
    class ObservedWorker extends Native {
      private readonly observation: {terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {super(url, options);
        this.observation = {terminated: false}; state.workers.push(this.observation);}
      override terminate(): void {this.observation.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
    // Observe main-thread timer ownership without changing delays or advancing
    // the clock. Long setup timers must be cleared after completion/cancel.
    const nativeSetTimeout = window.setTimeout.bind(window), nativeClearTimeout = window.clearTimeout.bind(window);
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]): number => {
      let id = 0;
      if (typeof handler !== 'function') return nativeSetTimeout(handler, delay, ...args);
      id = nativeSetTimeout(() => {state.pendingTimers.delete(id); Reflect.apply(handler, window, args);}, delay);
      state.pendingTimers.set(id, delay ?? 0); return id;
    }) as typeof window.setTimeout;
    window.clearTimeout = (...args: Parameters<typeof window.clearTimeout>): void => {
      const [id] = args; if (typeof id === 'number') state.pendingTimers.delete(id); nativeClearTimeout(...args);
    };
  }, {marker: sensitiveMarker, portraitClock});
}
async function gateFirstResponse(page: Page, pattern: RegExp): Promise<{
  reached: Promise<void>; release(): void; settled: Promise<void>; requests(): number;
}> {
  let release!: () => void, reached!: () => void, settled!: () => void, count = 0;
  const gate = new Promise<void>(resolve => {release = resolve;});
  const pending = new Promise<void>(resolve => {reached = resolve;});
  const done = new Promise<void>(resolve => {settled = resolve;});
  await page.route(pattern, async (route: Route) => {
    count++;
    if (count !== 1) {await route.continue(); return;}
    const response = await route.fetch(); reached();
    try {
      await gate;
      // A timed-out GLB fetch is correctly aborted before this gate opens.
      // A module import cannot be aborted, so its real late response is sent.
      if (!route.request().failure()) await route.fulfill({response});
    } finally {settled();}
  });
  return {reached: pending, release, settled: done, requests: () => count};
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.startupCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.startupCamera.workers.every(worker => worker.terminated));
const longTimers = (page: Page): Promise<number[]> => page.evaluate(() =>
  [...window.startupCamera.pendingTimers.values()].filter(delay => delay >= 29000));
function privateFields(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) return value.flatMap((child, index) => privateFields(child, `${path}[${index}]`));
  if (!value || typeof value !== 'object') {
    return typeof value === 'string' && (/^data:|;base64,|https?:\/\/[^\s]+[?&#]/i.test(value) || value.includes(sensitiveMarker)) ? [path] : [];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    /SHA256|PngDataUrl|landmarks|categoryBase64|imageData|maskBytes|sourceImage|sourceIdentity|deviceId|groupId|requestBody|responseBody/i.test(key)
      ? [`${path}.${key}`] : privateFields(child, `${path}.${key}`));
}
async function saveReport(page: Page, label: string): Promise<StartupReport> {
  await expect(page.locator('#download-startup')).toBeEnabled();
  const download = page.waitForEvent('download'); await page.click('#download-startup');
  const item = await download; expect(item.suggestedFilename()).toMatch(/^ar-startup-.*\.json$/);
  const filename = test.info().outputPath(label + '-startup.json'); await item.saveAs(filename);
  const raw = await readFile(filename, 'utf8'), report = JSON.parse(raw) as StartupReport;
  expect(report.schema).toBe('ar-startup-diagnostic-v1'); expect(report.build.id).toMatch(/^[a-f0-9]{64}$/);
  expect(report.workload).toEqual({eyewearId: 'amber-horizon', hairModelId: 'hair-only', variant: 'hair', pipeline: 'g'});
  expect(privateFields(report)).toEqual([]); expect(raw).not.toContain(sensitiveMarker);
  expect(raw).not.toContain(fixturePrefix());
  expect(JSON.parse(await page.locator('#startup-json').inputValue())).toEqual(report);
  if (await page.locator('#startup-report-copy').getAttribute('open') === null)
    await page.locator('#startup-report-copy').locator('summary').click();
  await expect(page.locator('#select-startup-json')).toBeEnabled();
  return report;
}
const fixturePrefix = (): string => 'data:image/jpeg;base64,';

for (const eyewear of ['amber-horizon', 'tom-ford-clear'] as const) for (const hair of ['hair-only', 'selfie-multiclass'] as const) {
  test(`startup: advancing camera clock preserves portrait pairs for ${eyewear}/${hair}`, async ({page}) => {
    test.setTimeout(120_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await installCamera(page, true); await page.goto('/ar_testing/');
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start');
    try {
      await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
      await expect(page.locator('#continuous-start')).toBeEnabled();
      const firstProgress = await page.evaluate(() => window.arStartupDiagnostics.report()!.frameProgress) as StartupPumpDiagnostic;
      expect(firstProgress.settled).toBe(true); expect(firstProgress.publicationObserved).toBe(true);
      expect(firstProgress.pump!.captured).toBeGreaterThan(0); expect(firstProgress.pump!.captureMisses).toBe(0);
      expect(firstProgress.pump!.startup.inferred).toBeGreaterThan(0); expect(firstProgress.pump!.startup.prepared).toBeGreaterThan(0);
      expect(firstProgress.video!.width).toBe(720); expect(firstProgress.video!.height).toBe(1280);
      const rows = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0));
      expect(rows.some(row => row.hasFace && row.hasMask)).toBe(true);
      for (const row of rows) {
        expect(row.sourceWidth).toBe(720); expect(row.sourceHeight).toBe(1280);
        expect(row.videoPresentedFrames).not.toBeNull(); expect(row.pipeline).toBe('g');
        expect(Number(row.native!['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
      }
      await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
      await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
      const outputs = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as Record<string, {
        pair: unknown; detection: unknown; mask: unknown; sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
        captureSnapshot: {surfacePositions: unknown; eyewearMatrix: unknown; protection: unknown};
        stats: {hasMask: boolean; [key: string]: unknown};
      }>;
      for (const pipeline of ['g', 'publish', 'region', 'lens', 'ui']) {
        const output = outputs[pipeline]!; expect(output.stats.hasMask).toBe(true);
        for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
          expect(output[key], `${pipeline}/${key}`).toEqual(outputs.g![key]);
        for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
          expect(output.captureSnapshot[key]).toEqual(outputs.g!.captureSnapshot[key]);
        for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'])
          expect((output.stats[key] as {changedPixels: number}).changedPixels).toBe(0);
      }
      await expect.poll(() => closed(page)).toBe(true);
      expect(await page.evaluate(() => window.arStartupDiagnostics.report()!.frameProgress)).toEqual(firstProgress);
      await page.selectOption('#pipeline-select', 'publish'); await page.selectOption('#variant-select', 'accepted');
      expect(await page.evaluate(() => window.arStartupDiagnostics.report()!.workload))
        .toEqual({eyewearId: eyewear, hairModelId: hair, pipeline: 'g', variant: 'hair'});
      await page.selectOption('#pipeline-select', 'g'); await page.selectOption('#variant-select', 'hair');
      await page.click('#resume-live'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
      await expect(page.locator('#continuous-start')).toBeEnabled();
      expect(errors).toEqual([]);
    } finally {
      const report = await page.evaluate(() => window.arStartupDiagnostics.report());
      await writeFile(test.info().outputPath('advancing-clock-startup.json'), JSON.stringify(report, null, 2));
      if (await page.locator('#stop').isVisible() && await page.locator('#stop').isEnabled()) await page.click('#stop');
      await expect.poll(() => closed(page)).toBe(true);
    }
  });
}
async function retryToFirstFrame(page: Page, oldSession: string): Promise<StartupReceipt> {
  await expect(page.locator('#start')).toBeEnabled(); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('.stage')).toHaveAttribute('data-startup-state', 'complete');
  await expect(page.locator('#continuous-start')).toBeEnabled();
  const receipt = await page.evaluate(() => window.arStartupDiagnostics.status());
  expect(receipt).not.toBeNull(); expect(receipt!.state).toBe('complete'); expect(receipt!.stage).toBe('first-ar');
  expect(receipt!.sessionId).not.toBe(oldSession); expect(receipt!.remainingMs).toBe(0);
  expect(receipt!.stages.map(value => value.stage)).toEqual(['module', 'g-renderer', 'candidate-renderer', 'face', 'first-ar']);
  expect(receipt!.stages.every(value => value.endedAtMs !== null)).toBe(true);
  expect(await longTimers(page)).toEqual([]);
  const first = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0)[0]!);
  expect(first.sessionId).toBe(receipt!.sessionId); expect(first.pipeline).toBe('g');
  expect(first.sourceWidth).toBe(640); expect(first.sourceHeight).toBe(427); expect(first.gpuRenderer).toMatch(/Direct3D11|D3D11/i);
  await expect(page.locator('#startup-stage-progress')).toBeHidden(); await expect(page.locator('#startup-status')).toContainText('Mirror ready');
  return receipt!;
}

test('startup: delayed mirror module exposes live progress and a pre-frame report; cancel and late completion cannot resurrect the session', async ({page}) => {
  test.setTimeout(120_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page);
  const gate = await gateFirstResponse(page, /\/ar_testing\/assets\/comparison-renderer-[^/]+\.js(?:\?.*)?$/);
  try {
    await page.goto('/ar_testing/experiments/efficiency-lab/live.html?study=review&startup_qa=' + sensitiveMarker);
    await expect(page.locator('#start')).toBeEnabled(); await page.click('#start'); await gate.reached;
    await expect(page.locator('.stage')).toHaveAttribute('data-startup-stage', 'module');
    await expect(page.locator('.stage')).toHaveAttribute('data-startup-state', 'running');
    await expect(page.locator('#startup-stage-progress')).toBeVisible();
    await expect(page.locator('#startup-status')).toHaveText(/Loading mirror code · [1-9]\d* s elapsed/);
    expect(await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).length)).toBe(0);
    const pending = await saveReport(page, 'pending-module');
    expect(pending.startup.stage).toBe('module'); expect(pending.startup.state).toBe('running');
    expect(pending.startup.stageElapsedMs).toBeGreaterThanOrEqual(1000); expect(pending.startup.stages[0]!.timeoutMs).toBe(60000);
    expect(pending.errors).toEqual({startup: null, hair: null}); expect(pending.milestones.some(value => value.name === 'stage-first-ar')).toBe(false);
    await page.click('#stop'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
    await expect.poll(() => closed(page)).toBe(true); expect(await longTimers(page)).toEqual([]);
    gate.release(); await gate.settled; await page.waitForLoadState('networkidle');
    expect(await page.evaluate(() => window.hairLivePreview.diagnostics().sessionId)).toBeNull();
    expect(await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).length)).toBe(0);
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
    const cancelled = await saveReport(page, 'cancelled-module');
    expect(cancelled.startup.state).toBe('cancelled'); expect(cancelled.startup.sessionId).toBe(pending.startup.sessionId);
    const ready = await retryToFirstFrame(page, pending.startup.sessionId);
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(await longTimers(page)).toEqual([]);
    expect(errors).toEqual([]);
    await writeFile(test.info().outputPath('startup-module-receipt.json'), JSON.stringify({requests: gate.requests(), ready,
      preFrameDownload: true, copyFallback: true, cancelledNoResurrection: true, timersCleared: true, errors,
      scope: 'Synthetic camera and delayed real module response; production setup timers and real G/worker startup.'}, null, 2));
  } finally {gate.release();}
});

test('startup: a blocked glasses response reaches the real 60-second G deadline, saves its stage and retries cleanly', async ({page}) => {
  // Includes the actual 60-second production deadline plus real retry startup.
  test.setTimeout(180_000); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page);
  const gate = await gateFirstResponse(page, /\/ar_testing\/models\/amber-horizon\.glb(?:\?.*)?$/);
  try {
    await page.goto('/ar_testing/'); await page.click('#start'); await gate.reached;
    await expect(page.locator('.stage')).toHaveAttribute('data-startup-stage', 'g-renderer');
    await expect(page.locator('#startup-stage-progress')).toBeVisible();
    await expect(page.locator('#startup-status')).toHaveText(/Preparing G glasses · \d+ s elapsed/);
    const pending = await saveReport(page, 'pending-glasses');
    expect(pending.startup.stage).toBe('g-renderer'); expect(pending.startup.state).toBe('running');
    const deadline = pending.startup.stages.at(-1)!; expect(deadline.timeoutMs).toBe(60000);
    expect(deadline.deadlineAtMs - deadline.startedAtMs).toBe(60000);
    await expect(page.locator('.stage')).toHaveAttribute('data-startup-state', 'timed-out', {timeout: 75_000});
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'error');
    await expect(page.locator('#start')).toBeEnabled(); await expect(page.locator('#start')).toHaveText('Try again');
    await expect.poll(() => closed(page)).toBe(true); expect(await longTimers(page)).toEqual([]);
    const timedOut = await saveReport(page, 'timed-out-glasses');
    expect(timedOut.startup.state).toBe('timed-out'); expect(timedOut.startup.stage).toBe('g-renderer');
    expect(timedOut.startup.reason).toBe('g-renderer-timeout'); expect(timedOut.startup.timeoutScope).toBe('stage');
    expect(timedOut.startup.stageElapsedMs).toBeGreaterThanOrEqual(60000);
    expect(timedOut.startup.finishedAtMs!).toBeGreaterThanOrEqual(deadline.deadlineAtMs);
    expect(timedOut.errors.startup).toBeTruthy();
    expect(await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).length)).toBe(0);
    gate.release(); await gate.settled;
    const ready = await retryToFirstFrame(page, pending.startup.sessionId);
    expect(gate.requests()).toBeGreaterThanOrEqual(2);
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(await longTimers(page)).toEqual([]);
    expect(errors).toEqual([]);
    await writeFile(test.info().outputPath('startup-timeout-receipt.json'), JSON.stringify({deadline, timeout: timedOut.startup, ready,
      productionClockAndDeadlines: true, timedOutStreamsClosed: true, timersCleared: true, errors,
      scope: 'Synthetic camera and delayed real GLB response. No timer acceleration or renderer deadline changes.'}, null, 2));
  } finally {gate.release();}
});
