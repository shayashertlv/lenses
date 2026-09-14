import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {FrameSample} from './frame-profiler.ts';
import type {} from './live-main.ts';

interface StabilityObservation {
  documentId: string; streams: MediaStream[]; workers: {terminated: boolean}[];
  contexts: WebGLRenderingContext[]; gateCamera: boolean; cameraBlocked: boolean; releaseCamera(): void;
}
declare global {interface Window {stabilityObservation: StabilityObservation;}}
declare global {interface Window {stabilityTransactionGate: {blocked: boolean; release(): void};}}
const entry = '/ar_testing/experiments/efficiency-lab/live.html?study=g-stability';

/** Substitute only the camera with a portrait canvas. Timers, workers, GPU,
 * page navigations, IndexedDB and downloaded archives remain production code.
 * This cannot establish physical iPhone throughput or wearer motion quality. */
async function installCamera(page: Page, gateCamera = false): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/stability-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(({gateCamera}) => {
    const state: StabilityObservation = {documentId: crypto.randomUUID(), streams: [], workers: [], contexts: [],
      gateCamera, cameraBlocked: false, releaseCamera() {}};
    window.stabilityObservation = state;
    const nativeContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, ...args: Parameters<typeof nativeContext>): ReturnType<typeof nativeContext> {
      const result = nativeContext.apply(this, args);
      if ((args[0] === 'webgl' || args[0] === 'webgl2') && result) {
        const gl = result as WebGLRenderingContext;
        if (!state.contexts.includes(gl)) state.contexts.push(gl);
      }
      return result;
    } as typeof nativeContext;
    const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/stability-camera.jpg';
    const draw = (): void => {
      if (!image.complete || !image.naturalWidth) return;
      context.fillStyle = '#808080'; context.fillRect(0, 0, 720, 1280); context.drawImage(image, 0, 400, 720, 480);
      for (const [x, y, color] of [[0, 0, '#e02010'], [640, 0, '#10c040'], [0, 1180, '#3050d0'], [640, 1180, '#e0c020']] as const) {
        context.fillStyle = color; context.fillRect(x, y, 80, 100);
      }
    };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready;
      if (state.gateCamera) await new Promise<void>(resolve => {state.cameraBlocked = true; state.releaseCamera = () => {
        state.gateCamera = false; state.cameraBlocked = false; resolve();
      };});
      draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly observation: {terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.observation = {terminated: false}; state.workers.push(this.observation);
      }
      override terminate(): void {this.observation.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  }, {gateCamera});
}
async function resources(page: Page) {
  return await page.evaluate(() => ({documentId: window.stabilityObservation.documentId,
    historyLength: history.length, navigationType: (performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined)?.type ?? null,
    streams: window.stabilityObservation.streams.map(stream => stream.getTracks().map(track => track.readyState)),
    workers: window.stabilityObservation.workers.map(worker => ({...worker})),
    contexts: window.stabilityObservation.contexts.map(context => ({lost: context.isContextLost()})),
    diagnostics: window.hairLivePreview.diagnostics()}));
}
async function published(page: Page, after = 0): Promise<FrameSample> {
  await expect.poll(() => page.evaluate(after => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId && row.pipeline === 'g'
      && row.hasFace && row.hasMask).length, after)).toBeGreaterThanOrEqual(4);
  return await page.evaluate(after => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId && row.pipeline === 'g'
      && row.hasFace && row.hasMask).at(-1)!, after);
}
async function assertFrame(page: Page, sample: FrameSample): Promise<void> {
  expect(sample.sourceWidth).toBe(720); expect(sample.sourceHeight).toBe(1280);
  expect(sample.pipeline).toBe('g'); expect(sample.faceDelegate).toBe('GPU');
  expect(Number(sample.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
  expect(Number(sample.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
  expect(sample.native?.['hairDelivery.releaseWorkerEarly']).toBe(false);
  const rendered = await page.locator('#mirror').evaluate(element => {
    const canvas = element as HTMLCanvasElement, context = canvas.getContext('2d')!;
    return {width: canvas.width, height: canvas.height, corners: [[40, 50], [680, 50], [40, 1230], [680, 1230]]
      .map(([x, y]) => Array.from(context.getImageData(x!, y!, 1, 1).data))};
  });
  expect(rendered.width).toBe(720); expect(rendered.height).toBe(1280);
  const [red, green, blue, yellow] = rendered.corners as [number[], number[], number[], number[]];
  expect(red[0]!).toBeGreaterThan(red[1]! + 100); expect(green[1]!).toBeGreaterThan(green[0]! + 100);
  expect(blue[2]!).toBeGreaterThan(blue[0]! + 100); expect(yellow[0]!).toBeGreaterThan(yellow[2]! + 100);
  expect(yellow[1]!).toBeGreaterThan(yellow[2]! + 100);
}
function archiveEntries(bytes: Buffer): Map<string, Buffer> {
  const entries = new Map<string, Buffer>(); let offset = 0;
  while (offset + 30 <= bytes.length && bytes.readUInt32LE(offset) === 0x04034b50) {
    expect(bytes.readUInt16LE(offset + 8)).toBe(0);
    const size = bytes.readUInt32LE(offset + 18), nameLength = bytes.readUInt16LE(offset + 26), extra = bytes.readUInt16LE(offset + 28);
    const name = bytes.toString('utf8', offset + 30, offset + 30 + nameLength), start = offset + 30 + nameLength + extra;
    expect(start + size).toBeLessThanOrEqual(bytes.length); expect(entries.has(name)).toBe(false);
    entries.set(name, bytes.subarray(start, start + size)); offset = start + size;
  }
  expect(entries.size).toBeGreaterThan(0); return entries;
}

interface StabilityStatus {
  suiteId: string | null; documentId: string; state: string; condition: string | null;
  chunkIndex: number; completedChunks: number; planLength: number; active: unknown; pending: unknown;
}
interface StabilityApi {status(): StabilityStatus; report(): Promise<unknown>;}
async function status(page: Page): Promise<StabilityStatus> {
  const loading = {suiteId: null, documentId: 'loading', state: 'loading', condition: null,
    chunkIndex: -1, completedChunks: 0, planLength: 0, active: null, pending: null};
  try {return await page.evaluate(loading =>
    (window as unknown as {arGStability?: StabilityApi}).arGStability?.status() ?? loading, loading);}
  catch (error) {
    if (error instanceof Error && /Execution context was destroyed|because of a navigation/.test(error.message)) return loading;
    throw error;
  }
}
interface RawReport {
  schema: string; sessionId: string; completed: boolean; partial: boolean;
  metadata: {performanceTimeOriginMs: number; stability: {suiteId: string; documentId: string; chunkId: string; buildId: string; timeOrigin: number}};
  workload: {eyewearId: string; hairModelId: string; variant: string; sourceWidth: number; sourceHeight: number};
  windows: {index: number; completed: boolean; switchedAtMs: number; measureStartedAtMs: number;
    endedAtMs: number; validWarmupFrames: number; summary: {durationMs: number; frames: number; completedArFps: number}}[];
  rows: {serial: number; phase: string; windowIndex: number | null; native: Record<string, unknown>;
    fields: Record<string, unknown>}[];
  analysisBins?: {index: number; windowIndex: number; plannedStartAtMs: number; plannedEndAtMs: number;
    completed: boolean; summary: {durationMs: number; frames: number; completedArFps: number}}[];
  recording?: {requested: boolean; bytes: number};
  hairDeliveryDrain?: {state: string}; retention: {rejectedRows: number; truncated: boolean};
}
function rawReports(value: unknown): RawReport[] {
  if (!value || typeof value !== 'object') return [];
  if (!Array.isArray(value) && (value as Record<string, unknown>).schema === 'ar-continuous-comparison-v1') return [value as RawReport];
  return Object.values(value).flatMap(rawReports);
}
async function downloaded(page: Page, name: string): Promise<{payload: unknown; reports: RawReport[]}> {
  const pending = page.waitForEvent('download', {timeout: 60_000});
  await page.click('#stability-download'); const download = await pending;
  const path = test.info().outputPath(`${name}.zip`); await download.saveAs(path);
  const entries = archiveEntries(await readFile(path));
  expect([...entries.keys()].every(name => name.endsWith('.json'))).toBe(true);
  const payload = Object.fromEntries([...entries].map(([name, bytes]) => [name, JSON.parse(bytes.toString('utf8')) as unknown]));
  const reports = rawReports(payload);
  await writeFile(test.info().outputPath(`${name}.json`), JSON.stringify(payload, null, 2));
  return {payload, reports};
}
function auditReport(report: RawReport, eyewear: string, hair: string): void {
  expect(report.workload).toMatchObject({eyewearId: eyewear, hairModelId: hair, variant: 'hair', sourceWidth: 720, sourceHeight: 1280});
  expect(report.retention.rejectedRows).toBe(0); expect(report.retention.truncated).toBe(false);
  expect(report.hairDeliveryDrain?.state).toBe('drained');
  expect(report.recording?.requested ?? false).toBe(false); expect(report.recording?.bytes ?? 0).toBe(0);
  expect(new Set(report.rows.map(row => row.fields.sequence)).size).toBe(report.rows.length);
  expect(report.rows.every(row => row.fields.sessionId === report.sessionId && row.fields.pipeline === 'g'
    && row.fields.faceDelegate === 'GPU' && row.fields.sourceWidth === 720 && row.fields.sourceHeight === 1280)).toBe(true);
  expect(report.rows.every(row => Number(row.native['pump.maxOwnedFrames']) <= 2
    && Number(row.native['pump.maxInFlightInference']) <= 1)).toBe(true);
  for (const window of report.windows.filter(window => window.completed)) {
    expect(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
    expect(window.measureStartedAtMs - window.switchedAtMs).toBeGreaterThanOrEqual(5000);
    const rows = report.rows.filter(row => row.windowIndex === window.index && row.phase === 'measured');
    expect(rows.length).toBeGreaterThan(10); expect(rows.length).toBe(window.summary.frames);
    expect(window.endedAtMs - window.measureStartedAtMs).toBe(window.summary.durationMs);
    expect(window.summary.completedArFps).toBeCloseTo(rows.length * 1000 / window.summary.durationMs, 10);
    expect(rows.every(row => Number(row.fields.capturedAtMs) >= window.measureStartedAtMs
      && Number(row.fields.publishedAtMs) < window.endedAtMs)).toBe(true);
    expect(new Set(rows.map(row => row.native['runtime.generation'])).size).toBe(1);
  }
}
async function launch(page: Page, condition: string, eyewear: string, hair: string, direction = 'forward'): Promise<void> {
  await installCamera(page); await page.goto(entry);
  const initialHistoryLength = await page.evaluate(() => history.length);
  expect(await page.evaluate(() => window.stabilityObservation.streams.length)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
  await page.selectOption('#stability-condition', condition);
  await page.selectOption('#stability-order', direction);
  await page.click('#stability-start');
  await expect.poll(async () => {
    const value = await status(page);
    if (['save-error', 'blocked', 'waiting-camera', 'partial'].includes(value.state)) throw new Error(`Stability launch failed: ${JSON.stringify(value)}`);
    return value.state;
  }, {timeout: 120_000}).toBe('running');
  await expect(page.locator('#stability-condition')).toHaveValue(condition);
  await expect(page.locator('#stability-order')).toHaveValue(direction);
  expect((await resources(page)).navigationType).toBe('reload');
  expect((await resources(page)).historyLength).toBe(initialHistoryLength);
}
async function stop(page: Page): Promise<void> {
  await page.click('#stability-stop');
  await expect.poll(() => status(page).then(value => value.state), {timeout: 60_000}).toBe('partial');
  await expect.poll(() => resources(page).then(value => value.workers.every(worker => worker.terminated)
    && value.contexts.every(context => context.lost) && value.streams.every(stream => stream.every(state => state === 'ended')))).toBe(true);
}

test('G stability full production clock: all controls / amber-horizon / hair-only', async ({page}) => {
  test.setTimeout(960_000);
  const errors: string[] = [], receipts: unknown[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await launch(page, 'all', 'amber-horizon', 'hair-only');
  await assertFrame(page, await published(page));
  await page.screenshot({path: test.info().outputPath('stability-initial-mirror.png'), fullPage: true});
  const initialHistoryLength = (await resources(page)).historyLength;
  const startedAt = Date.now(); let lastKey = '';
  for (;;) {
    try {
      const current = await status(page);
      expect(['partial', 'save-error', 'blocked', 'waiting-camera']).not.toContain(current.state);
      const key = `${current.documentId}:${current.chunkIndex}:${current.state}`;
      if (key !== lastKey) {
        const snapshot = await resources(page);
        expect(snapshot.navigationType).toBe('reload'); expect(snapshot.historyLength).toBe(initialHistoryLength);
        lastKey = key; receipts.push({atMs: Date.now() - startedAt, status: current, resources: snapshot});
        await writeFile(test.info().outputPath('stability-progress.json'), JSON.stringify(receipts, null, 2));
      }
      if (current.state === 'complete') break;
    } catch (error) {
      if (!(error instanceof Error) || !/Execution context was destroyed|Cannot read properties of undefined|Target page.*closed|because of a navigation/.test(error.message)) throw error;
    }
    expect(Date.now() - startedAt).toBeLessThan(900_000);
    await page.waitForTimeout(2000);
  }
  expect(Date.now() - startedAt).toBeGreaterThanOrEqual(540_000);
  const result = await downloaded(page, 'g-stability-all-real-clock');
  expect(result.reports).toHaveLength(8);
  const durations = result.reports.map(report => report.windows.filter(window => window.completed).reduce((sum, window) => sum + window.summary.durationMs, 0));
  expect(durations).toEqual([180000, 180000, 30000, 30000, 30000, 30000, 30000, 30000]);
  expect(result.reports.map(report => report.windows.length)).toEqual([1, 6, 1, 1, 1, 1, 1, 1]);
  expect(new Set(result.reports.map(report => report.sessionId)).size).toBe(8);
  expect(new Set(result.reports.map(report => report.metadata.stability.documentId)).size).toBe(8);
  expect(new Set(result.reports.map(report => report.metadata.stability.timeOrigin)).size).toBe(8);
  expect(new Set(result.reports.map(report => report.metadata.stability.suiteId)).size).toBe(1);
  expect(new Set(result.reports.map(report => report.metadata.stability.buildId)).size).toBe(1);
  expect(result.reports.every(report => report.metadata.performanceTimeOriginMs === report.metadata.stability.timeOrigin)).toBe(true);
  for (const report of result.reports) {expect(report.completed).toBe(true); expect(report.partial).toBe(false); auditReport(report, 'amber-horizon', 'hair-only');}
  const continuous = result.reports[0]!, restarted = result.reports[1]!;
  expect(new Set(continuous.rows.map(row => row.native['runtime.generation'])).size).toBe(1);
  expect(continuous.analysisBins).toHaveLength(6);
  for (const bin of continuous.analysisBins!) {
    expect(bin.completed).toBe(true); expect(bin.plannedEndAtMs - bin.plannedStartAtMs).toBe(30000);
    const rows = continuous.rows.filter(row => row.phase === 'measured'
      && Number(row.fields.capturedAtMs) >= bin.plannedStartAtMs && Number(row.fields.publishedAtMs) < bin.plannedEndAtMs);
    expect(bin.summary.frames).toBe(rows.length); expect(bin.summary.completedArFps).toBeCloseTo(rows.length / 30, 10);
  }
  const epochs = restarted.windows.map(window => [...new Set(restarted.rows.filter(row => row.windowIndex === window.index && row.phase === 'measured')
    .map(row => Number(row.native['runtime.generation']))) ]);
  expect(epochs.every(value => value.length === 1)).toBe(true);
  expect(new Set(epochs.flat()).size).toBe(6);
  for (let i = 1; i < epochs.length; i++) expect(epochs[i]![0]).toBeGreaterThan(epochs[i - 1]![0]!);
  const complete = await status(page); expect(complete.completedChunks).toBe(8); expect(complete.planLength).toBe(8);
  const end = await resources(page);
  expect(end.workers.every(worker => worker.terminated)).toBe(true); expect(end.streams.every(stream => stream.every(state => state === 'ended'))).toBe(true);
  await writeFile(test.info().outputPath('stability-resource-audit.json'), JSON.stringify({receipts, complete, end, errors,
    evidence: 'Production clocks and native desktop GPU with a static portrait camera. No physical iPhone speed or wearer-motion claim.'}, null, 2));
  expect(errors).toEqual([]);
});

for (const {eyewear, hair, condition} of [
  {eyewear: 'amber-horizon', hair: 'selfie-multiclass', condition: 'restarted'},
  {eyewear: 'tom-ford-clear', hair: 'hair-only', condition: 'fresh-page'},
  {eyewear: 'tom-ford-clear', hair: 'selfie-multiclass', condition: 'continuous'},
]) test(`G stability bounded controls: ${condition} / ${eyewear} / ${hair}`, async ({page}) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const direction = condition === 'continuous' ? 'reverse' : 'forward';
  await launch(page, condition, eyewear, hair, direction); const initial = await published(page); await assertFrame(page, initial);
  const before = await resources(page), initialStatus = await status(page);
  if (condition === 'restarted') {
    await expect.poll(() => page.evaluate(generation => window.arPerformanceProfiler.samplesAfter(0)
      .some(row => row.hasFace && row.hasMask && Number(row.native?.['runtime.generation']) > generation),
    Number(initial.native?.['runtime.generation'])), {timeout: 90_000}).toBe(true);
    const next = await published(page, initial.serial); await assertFrame(page, next);
    const after = await resources(page);
    expect(after.documentId).toBe(before.documentId); expect(next.sessionId).toBe(initial.sessionId);
    expect(after.workers.slice(0, before.workers.length).every(worker => worker.terminated)).toBe(true);
    expect(after.contexts.slice(0, before.contexts.length).every(context => context.lost)).toBe(true);
    expect(after.streams).toEqual([['live']]);
  } else if (condition === 'fresh-page') {
    await expect.poll(async () => {const value = await status(page);
      return value.state === 'running' && value.chunkIndex === 1 && value.completedChunks === 1;}, {timeout: 90_000}).toBe(true);
    await expect.poll(async () => {try {return (await resources(page)).documentId !== before.documentId;} catch {return false;}}).toBe(true);
    const next = await published(page); await assertFrame(page, next); expect(next.sessionId).not.toBe(initial.sessionId);
    expect((await status(page)).suiteId).toBe(initialStatus.suiteId);
    expect((await resources(page)).navigationType).toBe('reload'); expect((await resources(page)).historyLength).toBe(before.historyLength);
    await expect(page.locator('#stability-condition')).toHaveValue(condition);
    await expect(page.locator('#stability-order')).toHaveValue(direction);
  } else {
    await page.waitForTimeout(35_000);
    const after = await resources(page), next = await published(page, initial.serial); await assertFrame(page, next);
    expect(after.documentId).toBe(before.documentId); expect(after.workers).toEqual(before.workers);
    expect(after.contexts).toEqual(before.contexts); expect(next.sessionId).toBe(initial.sessionId);
    expect(next.native?.['runtime.generation']).toBe(initial.native?.['runtime.generation']);
  }
  await page.screenshot({path: test.info().outputPath('stability-bounded-mirror.png'), fullPage: true});
  await page.locator('.stage').screenshot({path: test.info().outputPath('stability-bounded-stage.png')});
  await stop(page); const report = await downloaded(page, 'g-stability-bounded');
  expect(report.reports.length).toBeGreaterThan(0); for (const raw of report.reports) auditReport(raw, eyewear, hair);
  await writeFile(test.info().outputPath('g-stability-bounded-resources.json'), JSON.stringify({initial, before, initialStatus, final: await status(page), errors}, null, 2));
  expect(errors).toEqual([]);
});

test('G stability cancellation owns a late preflight camera', async ({page}) => {
  test.setTimeout(120_000);
  await installCamera(page, true); await page.goto(entry);
  await page.click('#stability-start');
  await expect.poll(() => page.evaluate(() => window.stabilityObservation.cameraBlocked)).toBe(true);
  await page.click('#stability-stop');
  await page.evaluate(() => window.stabilityObservation.releaseCamera());
  await expect.poll(() => resources(page).then(value => value.streams.length > 0
    && value.streams.every(stream => stream.every(state => state === 'ended')) && value.workers.every(worker => worker.terminated))).toBe(true);
  expect((await status(page)).state).not.toBe('running');
  await writeFile(test.info().outputPath('stability-preflight-cancel.json'), JSON.stringify({status: await status(page), resources: await resources(page)}, null, 2));
});

test('G stability refuses unavailable persistence without an unrecorded run', async ({page}) => {
  test.setTimeout(90_000);
  await installCamera(page);
  await page.addInitScript(() => {
    Object.defineProperty(indexedDB, 'open', {configurable: true, value: () => {throw new DOMException('QA storage access denied', 'SecurityError');}});
  });
  await page.goto(entry);
  await expect(page.locator('#stability-status')).toContainText(/storage|IndexedDB|persist|access denied/i);
  const state = await resources(page);
  expect(state.workers).toHaveLength(0); expect(state.streams.every(stream => stream.every(state => state === 'ended'))).toBe(true);
  expect((await status(page)).state).not.toBe('running');
  await writeFile(test.info().outputPath('stability-storage-denied.json'), JSON.stringify({status: await status(page), resources: state}, null, 2));
});

test('G stability unexpected refresh retains a completed page and stops the unfinished page', async ({page}) => {
  test.setTimeout(180_000);
  await launch(page, 'fresh-page', 'amber-horizon', 'hair-only');
  await expect.poll(async () => {const value = await status(page);
    return value.state === 'running' && value.chunkIndex === 1 && value.completedChunks === 1;}, {timeout: 100_000}).toBe(true);
  await published(page);
  const before = await status(page);
  await page.reload();
  await expect.poll(() => status(page).then(value => value.state)).toBe('partial');
  const after = await status(page); expect(after.suiteId).toBe(before.suiteId); expect(after.documentId).not.toBe(before.documentId);
  expect(after.completedChunks).toBe(1);
  expect((await resources(page)).streams).toHaveLength(0); expect((await resources(page)).workers).toHaveLength(0);
  const saved = await downloaded(page, 'stability-refresh-interrupted');
  const completed = saved.reports.filter(report => report.completed); expect(completed).toHaveLength(1);
  auditReport(completed[0]!, 'amber-horizon', 'hair-only');
  await writeFile(test.info().outputPath('stability-refresh-ownership.json'), JSON.stringify({before, after}, null, 2));
});

test('G stability copied active tab cannot claim the suite or open its camera', async ({page, context}) => {
  test.setTimeout(180_000);
  await launch(page, 'continuous', 'amber-horizon', 'hair-only'); const sample = await published(page);
  const before = await status(page);
  // A duplicate browser tab inherits its opener's sessionStorage. Reproduce
  // that browser state, including stale ownership data, without minting tokens.
  const copiedStorage = await page.evaluate(() => Object.fromEntries(Object.keys(sessionStorage).map(key => [key, sessionStorage.getItem(key)!])));
  const duplicate = await context.newPage(); await installCamera(duplicate);
  await duplicate.addInitScript(entries => {for (const [key, value] of Object.entries(entries)) sessionStorage.setItem(key, value);}, copiedStorage);
  try {
    await duplicate.goto(page.url());
    await expect(duplicate.locator('#stability-status')).toContainText(/another|already|owner|active|running|interrupted/i);
    const resume = duplicate.locator('#stability-resume');
    if (await resume.isVisible() && await resume.isEnabled()) await resume.click();
    expect((await resources(duplicate)).streams).toHaveLength(0); expect((await resources(duplicate)).workers).toHaveLength(0);
    const after = await status(page); expect(after.suiteId).toBe(before.suiteId); expect(after.state).toBe('running');
    expect((await published(page, sample.serial)).sessionId).toBe(sample.sessionId);
    await writeFile(test.info().outputPath('stability-duplicate-tab.json'), JSON.stringify({before, after, duplicate: await status(duplicate)}, null, 2));
  } finally {await duplicate.close(); await stop(page);}
});

test('G stability stop during initial storage commit cannot navigate afterwards', async ({page}) => {
  test.setTimeout(90_000);
  await installCamera(page);
  await page.addInitScript(() => {
    const state = {blocked: false, release() {}}; window.stabilityTransactionGate = state;
    const descriptor = Object.getOwnPropertyDescriptor(IDBTransaction.prototype, 'oncomplete')!; let used = false;
    Object.defineProperty(IDBTransaction.prototype, 'oncomplete', {...descriptor, set(this: IDBTransaction, handler: ((event: Event) => void) | null) {
      if (used || this.mode !== 'readwrite' || !this.objectStoreNames.contains('manifest') || !handler) {
        descriptor.set!.call(this, handler); return;
      }
      used = true;
      descriptor.set!.call(this, (event: Event) => {
        state.blocked = true; state.release = () => {state.blocked = false; handler.call(this, event);};
      });
    }});
  });
  await page.goto(entry); const before = await resources(page);
  await page.click('#stability-start');
  await expect.poll(() => page.evaluate(() => window.stabilityTransactionGate.blocked)).toBe(true);
  await page.click('#stability-stop'); await page.evaluate(() => window.stabilityTransactionGate.release());
  await expect.poll(() => status(page).then(value => value.state)).toBe('ready');
  expect((await resources(page)).documentId).toBe(before.documentId);
  expect((await resources(page)).workers).toHaveLength(0);
  expect((await resources(page)).streams.every(stream => stream.every(state => state === 'ended'))).toBe(true);
  await expect(page.locator('#stability-resume')).toBeVisible();
  await writeFile(test.info().outputPath('stability-stop-after-commit.json'), JSON.stringify({status: await status(page), resources: await resources(page)}, null, 2));
});

test('G stability failed handoff leaves a saved suite with actionable continuation', async ({page}) => {
  test.setTimeout(150_000);
  await installCamera(page);
  await page.addInitScript(() => {
    const nativeSet = Storage.prototype.setItem; let failed = false;
    Storage.prototype.setItem = function(key: string, value: string): void {
      if (this === sessionStorage && key === 'ar-g-stability-handoff-v1' && value !== 'probe' && !failed) {
        failed = true; throw new DOMException('QA handoff write failed once', 'QuotaExceededError');
      }
      nativeSet.call(this, key, value);
    };
  });
  await page.goto(entry); await page.selectOption('#stability-condition', 'continuous');
  await page.click('#stability-start');
  await expect(page.locator('#stability-status')).toContainText(/handoff write failed|handoff|continue/i);
  await expect.poll(() => status(page).then(value => value.state)).toBe('ready');
  const saved = await status(page); expect(saved.suiteId).not.toBeNull();
  await expect(page.locator('#stability-resume')).toBeVisible(); await page.click('#stability-resume');
  await expect.poll(() => status(page).then(value => value.state), {timeout: 120_000}).toBe('running');
  const resumed = await status(page); expect(resumed.suiteId).toBe(saved.suiteId); expect(resumed.documentId).not.toBe(saved.documentId);
  await assertFrame(page, await published(page)); await stop(page);
  await writeFile(test.info().outputPath('stability-handoff-retry.json'), JSON.stringify({saved, resumed, final: await status(page)}, null, 2));
});
