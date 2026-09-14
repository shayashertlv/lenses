import assert from 'node:assert/strict';
import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {FrameSample} from './frame-profiler.ts';
import type {} from './live-main.ts';

interface ReadbackObservation {
  documentId: string; streams: MediaStream[]; workers: {terminated: boolean}[];
  contexts: WebGLRenderingContext[]; gateCamera: boolean; cameraBlocked: boolean; releaseCamera(): void;
}
declare global {interface Window {readbackObservation: ReadbackObservation;}}
declare global {interface Window {stabilityTransactionGate: {blocked: boolean; release(): void};}}
const entry = '/ar_testing/experiments/efficiency-lab/live.html?study=readback-diagnostic';
type ReadbackPipeline = 'g' | 'g-readback';

/** Substitute only the camera with a portrait canvas. Timers, workers, GPU,
 * page navigations, IndexedDB and downloaded archives remain production code.
 * This cannot establish physical iPhone throughput or wearer motion quality. */
async function installCamera(page: Page, gateCamera = false): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/stability-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(({gateCamera}) => {
    const state: ReadbackObservation = {documentId: crypto.randomUUID(), streams: [], workers: [], contexts: [],
      gateCamera, cameraBlocked: false, releaseCamera() {}};
    window.readbackObservation = state;
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
  return await page.evaluate(() => ({documentId: window.readbackObservation.documentId,
    historyLength: history.length, navigationType: (performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined)?.type ?? null,
    streams: window.readbackObservation.streams.map(stream => stream.getTracks().map(track => track.readyState)),
    workers: window.readbackObservation.workers.map(worker => ({...worker})),
    contexts: window.readbackObservation.contexts.map(context => ({lost: context.isContextLost()})),
    diagnostics: window.hairLivePreview.diagnostics()}));
}
async function published(page: Page, pipeline: ReadbackPipeline, after = 0): Promise<FrameSample> {
  await expect.poll(() => page.evaluate(({after, pipeline}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId && row.pipeline === pipeline
      && row.hasFace && row.hasMask).length, {after, pipeline})).toBeGreaterThanOrEqual(4);
  return await page.evaluate(({after, pipeline}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId && row.pipeline === pipeline
      && row.hasFace && row.hasMask).at(-1)!, {after, pipeline});
}
async function assertFrame(page: Page, sample: FrameSample, pipeline: ReadbackPipeline): Promise<void> {
  expect(sample.sourceWidth).toBe(720); expect(sample.sourceHeight).toBe(1280);
  expect(sample.pipeline).toBe(pipeline); expect(sample.faceDelegate).toBe('GPU');
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

interface ReadbackStatus {
  suiteId: string | null; documentId: string; state: string; condition: string | null;
  chunkIndex: number; completedChunks: number; planLength: number; active: unknown; pending: unknown;
}
interface ReadbackApi {status(): ReadbackStatus; report(): Promise<unknown>;}
async function status(page: Page): Promise<ReadbackStatus> {
  const loading = {suiteId: null, documentId: 'loading', state: 'loading', condition: null,
    chunkIndex: -1, completedChunks: 0, planLength: 0, active: null, pending: null};
  try {return await page.evaluate(loading =>
    (window as unknown as {arGStability?: ReadbackApi}).arGStability?.status() ?? loading, loading);}
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
function auditReport(report: RawReport, eyewear: string, hair: string, pipeline: ReadbackPipeline): void {
  expect(report.workload).toMatchObject({eyewearId: eyewear, hairModelId: hair, variant: 'hair', sourceWidth: 720, sourceHeight: 1280});
  expect(report.retention.rejectedRows).toBe(0); expect(report.retention.truncated).toBe(false);
  expect(report.hairDeliveryDrain?.state).toBe('drained');
  expect(report.recording?.requested ?? false).toBe(false); expect(report.recording?.bytes ?? 0).toBe(0);
  expect(new Set(report.rows.map(row => row.fields.sequence)).size).toBe(report.rows.length);
  expect(report.rows.every(row => row.fields.sessionId === report.sessionId && row.fields.pipeline === pipeline
    && row.fields.faceDelegate === 'GPU' && row.fields.sourceWidth === 720 && row.fields.sourceHeight === 1280)).toBe(true);
  expect(report.rows.every(row => Number(row.native['pump.maxOwnedFrames']) <= 2
    && Number(row.native['pump.maxInFlightInference']) <= 1)).toBe(true);
  auditNative(report.rows.filter(row => row.phase === 'measured' && row.fields.hasFace === true), pipeline);
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
  expect(await page.evaluate(() => window.readbackObservation.streams.length)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
  await page.selectOption('#stability-condition', condition);
  await page.selectOption('#stability-order', direction);
  await page.click('#stability-start');
  await expect.poll(async () => {
    const value = await status(page);
    if (['save-error', 'blocked', 'waiting-camera', 'partial'].includes(value.state)) throw new Error(`Readback launch failed: ${JSON.stringify(value)}`);
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


// Timings are wall-clock observations of existing calls. They are not isolated
// GPU time and these synthetic-camera tests make no device throughput claim.
const nativePboFields = new WeakMap<Record<string, unknown>, Map<string, unknown>>();
function suffix(native: Record<string, unknown>, key: string): unknown {
  let fields = nativePboFields.get(native);
  if (!fields) {
    fields = new Map(Object.entries(native).filter(([name]) => name.includes('.pbo.'))
      .map(([name, value]) => [name.slice(name.lastIndexOf('.pbo.') + 5), value]));
    nativePboFields.set(native, fields);
  }
  return fields.get(key);
}
function auditNative(rows: RawReport['rows'], pipeline: ReadbackPipeline): void {
  assert.ok(rows.length > 0, `${pipeline}: no tracked rows`);
  assert.ok(rows.some(row => row.fields.hasMask === true), `${pipeline}: no paired mask`);
  for (const row of rows) {
    const context = `${pipeline} row ${row.serial}`;
    assert.equal(row.native['pump.mode'], 'overlap', context);
    assert.equal(row.native['admission.rateHz'], null, context);
    assert.equal(row.native['hairDelivery.releaseWorkerEarly'], false, context);
  }
  const completed = rows.filter(row => suffix(row.native, 'completed') === true);
  assert.ok(completed.length > 0, `${pipeline}: no completed native PBO read`);
  const fields = ['diagnosticClockReads', 'submitStateQueryMs', 'submitStateSetupMs', 'submitStateRestoreMs',
    'extractStateQueryMs', 'extractStateSetupMs', 'extractStateRestoreMs', 'submitBufferCreateMs',
    'submitBufferAllocateMs', 'submitReadPixelsMs', 'submitCheckMs', 'waitCheckMs', 'extractCheckMs',
    'currentCheckMs', 'fenceSyncMs', 'fenceFlushMs', 'clientWaitMs', 'pollYieldMs', 'pollYields',
    'waitObservationMs', 'extractObservationMs', 'extractBufferBindMs', 'extractGetBufferSubDataMs',
    'extractAllocationMs', 'extractRowFlipMs', 'extractImageDataMs'];
  for (const row of completed) {
    const context = `${pipeline} row ${row.serial}`;
    assert.equal(suffix(row.native, 'queuedCalls'), 1, context);
    assert.equal(suffix(row.native, 'retrievedCalls'), 1, context);
    assert.equal(suffix(row.native, 'retrievedBytes'), 720 * 1280 * 4, context);
    assert.equal(suffix(row.native, 'fallbackReason'), null, context);
    if (pipeline === 'g-readback') {
      assert.equal(suffix(row.native, 'diagnosticVersion'), 1, context);
      for (const field of fields) {
        const value = suffix(row.native, field), label = `${context} ${field}`;
        assert.equal(typeof value, 'number', label); assert.ok(Number.isFinite(value), label);
        assert.ok(Number(value) >= 0, label);
      }
      assert.ok(Number(suffix(row.native, 'diagnosticClockReads')) > 0, context);
      assert.equal(suffix(row.native, 'pollYields'), Number(suffix(row.native, 'polls')) - 1, context);
    } else for (const field of ['diagnosticVersion', ...fields]) assert.equal(suffix(row.native, field), undefined, `${context} ${field}`);
  }
  if (pipeline === 'g-readback') for (const field of ['extractStateQueryMs', 'extractGetBufferSubDataMs', 'extractRowFlipMs'])
    assert.ok(completed.some(row => Number(suffix(row.native, field)) > 0), `${pipeline}: ${field} not exercised`);
}

test('G readback full production clock: separate control and diagnostic documents', async ({page}) => {
  test.setTimeout(660_000);
  const errors: string[] = [], receipts: unknown[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await launch(page, 'all', 'amber-horizon', 'hair-only');
  await assertFrame(page, await published(page, 'g'), 'g');
  const first = await resources(page), startedAt = Date.now(); let lastKey = '';
  for (;;) {
    try {
      const current = await status(page);
      expect(['partial', 'save-error', 'blocked', 'waiting-camera']).not.toContain(current.state);
      const key = `${current.documentId}:${current.chunkIndex}:${current.state}`;
      if (key !== lastKey) {
        if (current.state === 'running') {
          const pipeline = current.condition === 'readback-diagnostic' ? 'g-readback' : 'g';
          await assertFrame(page, await published(page, pipeline), pipeline);
        }
        const snapshot = await resources(page);
        expect(snapshot.navigationType).toBe('reload'); expect(snapshot.historyLength).toBe(first.historyLength);
        if (current.state === 'running') expect(snapshot.contexts.length).toBe(first.contexts.length);
        lastKey = key; receipts.push({atMs: Date.now() - startedAt, status: current, resources: snapshot});
        await writeFile(test.info().outputPath('readback-progress.json'), JSON.stringify(receipts, null, 2));
      }
      if (current.state === 'complete') break;
    } catch (error) {
      if (!(error instanceof Error) || !/Execution context was destroyed|Cannot read properties of undefined|Target page.*closed|because of a navigation/.test(error.message)) throw error;
    }
    expect(Date.now() - startedAt).toBeLessThan(600_000);
    await page.waitForTimeout(2000);
  }
  expect(Date.now() - startedAt).toBeGreaterThanOrEqual(350_000);
  const result = await downloaded(page, 'g-readback-all-real-clock');
  expect(result.reports).toHaveLength(2);
  expect(result.reports.map(report => report.windows.map(window => window.summary.durationMs))).toEqual([[180000], [180000]]);
  expect(new Set(result.reports.map(report => report.sessionId)).size).toBe(2);
  expect(new Set(result.reports.map(report => report.metadata.stability.documentId)).size).toBe(2);
  expect(new Set(result.reports.map(report => report.metadata.stability.timeOrigin)).size).toBe(2);
  expect(new Set(result.reports.map(report => report.metadata.stability.suiteId)).size).toBe(1);
  expect(new Set(result.reports.map(report => report.metadata.stability.buildId)).size).toBe(1);
  expect(result.reports.every(report => report.metadata.performanceTimeOriginMs === report.metadata.stability.timeOrigin)).toBe(true);
  for (const [index, report] of result.reports.entries()) {
    expect(report.completed).toBe(true); expect(report.partial).toBe(false);
    auditReport(report, 'amber-horizon', 'hair-only', index === 0 ? 'g' : 'g-readback');
    expect(report.analysisBins).toHaveLength(6);
    expect(new Set(report.rows.map(row => row.native['runtime.generation'])).size).toBe(1);
    for (const bin of report.analysisBins!) {
      expect(bin.completed).toBe(true); expect(bin.plannedEndAtMs - bin.plannedStartAtMs).toBe(30000);
      const rows = report.rows.filter(row => row.phase === 'measured'
        && Number(row.fields.capturedAtMs) >= bin.plannedStartAtMs && Number(row.fields.publishedAtMs) < bin.plannedEndAtMs);
      expect(bin.summary.frames).toBe(rows.length); expect(bin.summary.completedArFps).toBeCloseTo(rows.length / 30, 10);
    }
  }
  const end = await resources(page), complete = await status(page);
  expect(complete.completedChunks).toBe(2); expect(complete.planLength).toBe(2);
  expect(end.workers.every(worker => worker.terminated)).toBe(true);
  expect(end.contexts.every(context => context.lost)).toBe(true);
  expect(end.streams.every(stream => stream.every(state => state === 'ended'))).toBe(true);
  await writeFile(test.info().outputPath('readback-resource-audit.json'), JSON.stringify({receipts, complete, end, errors,
    evidence: 'Production clocks and native desktop GPU with a static portrait camera. No physical iPhone speed or wearer-motion claim.'}, null, 2));
  expect(errors).toEqual([]);
});

for (const {eyewear, hair, action} of [
  {eyewear: 'amber-horizon', hair: 'selfie-multiclass', action: 'stop'},
  {eyewear: 'tom-ford-clear', hair: 'hair-only', action: 'refresh'},
  {eyewear: 'tom-ford-clear', hair: 'selfie-multiclass', action: 'stop'},
]) test(`G readback diagnostic bounded controls: ${action} / ${eyewear} / ${hair}`, async ({page}) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await launch(page, 'readback-diagnostic', eyewear, hair, 'reverse');
  const initial = await published(page, 'g-readback'); await assertFrame(page, initial, 'g-readback');
  const before = await resources(page), initialStatus = await status(page);
  await expect.poll(() => page.evaluate(() => window.arContinuousComparison.status()?.state), {timeout: 60_000}).toBe('measuring');
  await page.waitForTimeout(10_000);
  await page.screenshot({path: test.info().outputPath('readback-bounded-mirror.png'), fullPage: true});
  await page.locator('.stage').screenshot({path: test.info().outputPath('readback-bounded-stage.png')});
  if (action === 'refresh') {
    await page.reload();
    await expect.poll(() => status(page).then(value => value.state)).toBe('partial');
    const after = await resources(page);
    expect(after.documentId).not.toBe(before.documentId);
    expect((await status(page)).suiteId).toBe(initialStatus.suiteId);
    expect(after.streams).toHaveLength(0); expect(after.workers).toHaveLength(0);
    expect(after.contexts).toHaveLength(0);
  } else {
    await stop(page);
    const saved = await downloaded(page, 'g-readback-bounded');
    expect(saved.reports).toHaveLength(1);
    auditReport(saved.reports[0]!, eyewear, hair, 'g-readback');
    expect(saved.reports[0]!.partial).toBe(true);
  }
  await writeFile(test.info().outputPath('readback-bounded-resources.json'), JSON.stringify({initial, before, initialStatus,
    final: await status(page), errors}, null, 2));
  expect(errors).toEqual([]);
});
