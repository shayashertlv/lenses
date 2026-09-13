import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {FrameSample} from './frame-profiler.ts';
import type {} from './live-main.ts';

const choices = ['g', 'face-cpu', 'render-worker', 'frame-copy', 'reuse-compose', 'mask-bytes'] as const;
type Choice = typeof choices[number];
interface WorkerObservation {terminated: boolean; kind: 'face' | 'hair' | 'render' | 'unknown'; delegate: string | null;}
interface RuntimeObservation {
  streams: MediaStream[]; workers: WorkerObservation[]; contexts: WebGLRenderingContext[];
  gateNextHair: boolean; gateNextCpu: boolean; blocked: boolean; released: number; release(): void;
}
declare global {interface Window {freshRuntimeObservation: RuntimeObservation;}}
const entry = '/ar_testing/experiments/efficiency-lab/live.html?study=fps-review';

/** Observe genuine Worker termination and WebGL context loss without changing
 * scheduling, inference, production startup limits or the owned source pixels.
 * The portrait is a synthetic camera; this is resource/lifecycle evidence, not
 * a physical-phone throughput or wearer-motion quality benchmark. */
async function installObservation(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/fresh-runtime-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const state: RuntimeObservation = {streams: [], workers: [], contexts: [], gateNextHair: false, gateNextCpu: false,
      blocked: false, released: 0, release() {}};
    window.freshRuntimeObservation = state;
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, ...args: Parameters<typeof getContext>): ReturnType<typeof getContext> {
      const result = getContext.apply(this, args);
      if ((args[0] === 'webgl' || args[0] === 'webgl2') && result) {
        const gl = result as WebGLRenderingContext;
        if (!state.contexts.includes(gl)) state.contexts.push(gl);
      }
      return result;
    } as typeof getContext;
    const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/fresh-runtime-camera.jpg';
    const draw = (): void => {if (image.complete && image.naturalWidth) {
      context.fillStyle = '#808080'; context.fillRect(0, 0, 720, 1280); context.drawImage(image, 0, 400, 720, 480);
    }};
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly observation: WorkerObservation;
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.observation = {terminated: false, kind: 'unknown', delegate: null};
        state.workers.push(this.observation);
        this.addEventListener('message', (event: MessageEvent<Record<string, unknown>>) => {
          if (event.data.type !== 'ready') return;
          const hairGate = state.gateNextHair && this.observation.kind === 'hair';
          const cpuGate = state.gateNextCpu && this.observation.kind === 'face' && this.observation.delegate === 'CPU';
          if (!hairGate && !cpuGate) return;
          if (hairGate) state.gateNextHair = false;
          if (cpuGate) state.gateNextCpu = false;
          state.blocked = true; event.stopImmediatePropagation(); const reply = event.data;
          state.release = () => {if (!state.blocked) return; state.blocked = false; state.released++;
            this.dispatchEvent(new MessageEvent('message', {data: reply}));};
        });
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const value = message as {type?: string; modelUrl?: string; modelId?: string; delegate?: string};
        if (value.type === 'initialize' && value.modelUrl?.includes('face_landmarker.task')) this.observation.kind = 'face';
        else if (value.type === 'initialize' && value.modelId) this.observation.kind = 'hair';
        else if (value.type === 'init') this.observation.kind = 'render';
        if (value.type === 'initialize') this.observation.delegate = value.delegate ?? null;
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void {this.observation.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  });
}

async function published(page: Page, pipeline: Choice, after = 0, newerThanGeneration = 0): Promise<FrameSample> {
  await expect.poll(() => page.evaluate(({pipeline, after, newerThanGeneration}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId
      && row.pipeline === pipeline && row.hasFace && row.hasMask
      && Number(row.native?.['runtime.generation']) > newerThanGeneration).length,
  {pipeline, after, newerThanGeneration})).toBeGreaterThanOrEqual(4);
  return await page.evaluate(({pipeline, after, newerThanGeneration}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId
      && row.pipeline === pipeline && row.hasFace && row.hasMask
      && Number(row.native?.['runtime.generation']) > newerThanGeneration).at(-1)!, {pipeline, after, newerThanGeneration});
}
async function resourceSnapshot(page: Page) {
  return await page.evaluate(() => {
    const state = window.freshRuntimeObservation;
    return {workers: state.workers.map(value => ({...value})),
      contexts: state.contexts.map(context => ({lost: context.isContextLost()})),
      streams: state.streams.map(stream => stream.getTracks().map(track => track.readyState)),
      diagnostics: window.hairLivePreview.diagnostics()};
  });
}
async function cleanup(page: Page): Promise<void> {
  if (await page.locator('#stop').isVisible()) await page.click('#stop');
  await expect.poll(() => page.evaluate(() => {
    const state = window.freshRuntimeObservation;
    return state.workers.every(worker => worker.terminated) && state.contexts.every(context => context.isContextLost())
      && state.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'));
  })).toBe(true);
}

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass'])
  test(`fresh runtime releases each switched experiment: ${eyewear} / ${hair}`, async ({page}) => {
    test.setTimeout(300_000);
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await installObservation(page); await page.goto(entry);
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start');
    const receipts: {pipeline: Choice; sample: FrameSample; resources: Awaited<ReturnType<typeof resourceSnapshot>>}[] = [];
    try {
      let sample = await published(page, 'g'), resources = await resourceSnapshot(page);
      const session = sample.sessionId, initialWorkers = resources.workers.filter(worker => !worker.terminated).length;
      const initialContexts = resources.contexts.filter(context => !context.lost).length;
      expect(initialWorkers).toBe(2); expect(initialContexts).toBe(4);
      receipts.push({pipeline: 'g', sample, resources});
      // Include G again after every candidate has actually run. The selected
      // runtime must match fresh-page resource shape, not accumulate workers.
      for (const pipeline of [...choices.slice(1), 'g'] as Choice[]) {
        const priorWorkers = resources.workers.length, priorContexts = resources.contexts.length;
        const priorGeneration = Number(sample.native?.['runtime.generation']);
        await page.selectOption('#pipeline-select', pipeline); sample = await published(page, pipeline, sample.serial);
        resources = await resourceSnapshot(page);
        expect(sample.sessionId).toBe(session);
        expect(sample.native?.['runtime.isolation']).toBe('fresh-runtime');
        expect(Number(sample.native?.['runtime.generation'])).toBeGreaterThan(priorGeneration);
        expect(resources.workers.slice(0, priorWorkers).every(worker => worker.terminated)).toBe(true);
        expect(resources.contexts.slice(0, priorContexts).every(context => context.lost)).toBe(true);
        expect(resources.workers.filter(worker => !worker.terminated)).toHaveLength(pipeline === 'render-worker' ? 3 : initialWorkers);
        expect(resources.contexts.filter(context => !context.lost)).toHaveLength(initialContexts);
        expect(resources.streams).toEqual([['live']]);
        expect(sample.sourceWidth).toBe(720); expect(sample.sourceHeight).toBe(1280);
        expect(sample.faceDelegate).toBe(pipeline === 'face-cpu' ? 'CPU' : 'GPU');
        expect(Number(sample.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
        expect(Number(sample.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
        expect(sample.native?.['hairDelivery.releaseWorkerEarly']).toBe(false);
        if (pipeline === 'render-worker') expect(sample.native?.['renderWorker.backend']).toBe('offscreen-worker');
        if (pipeline === 'frame-copy') expect(sample.native?.['capture.actualPath']).toBe('video-frame-copy');
        if (pipeline === 'reuse-compose') expect(sample.native?.['reviewCompose.used']).toBe(true);
        if (pipeline === 'mask-bytes') expect(sample.native?.['hairCategory.path']).toBe('rgba8-readback');
        receipts.push({pipeline, sample, resources});
      }
    } finally {
      await cleanup(page);
      await writeFile(test.info().outputPath('fresh-runtime-resources.json'), JSON.stringify({eyewear, hair, receipts, errors}, null, 2));
    }
    expect(errors).toEqual([]);
  });

test('fresh runtime stop revokes delayed hair readiness before a CPU restart', async ({page}) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installObservation(page); await page.goto(entry); await page.click('#start');
  try {
    const initial = await published(page, 'g');
    await page.evaluate(() => {window.freshRuntimeObservation.gateNextHair = true;});
    await page.selectOption('#pipeline-select', 'render-worker');
    await expect.poll(() => page.evaluate(() => window.freshRuntimeObservation.blocked)).toBe(true);
    const beforeStop = await resourceSnapshot(page);
    await page.selectOption('#pipeline-select', 'face-cpu'); await cleanup(page);
    const stopped = await resourceSnapshot(page);
    await page.click('#start'); await page.evaluate(() => window.freshRuntimeObservation.release());
    const resumed = await published(page, 'face-cpu', initial.serial), resources = await resourceSnapshot(page);
    expect(resumed.sessionId).not.toBe(initial.sessionId);
    expect(resumed.faceDelegate).toBe('CPU'); expect(resumed.native?.['runtime.isolation']).toBe('fresh-runtime');
    expect(resources.workers.slice(0, stopped.workers.length).every(worker => worker.terminated)).toBe(true);
    expect(resources.contexts.slice(0, stopped.contexts.length).every(context => context.lost)).toBe(true);
    expect(resources.workers.filter(worker => !worker.terminated)).toHaveLength(2);
    expect(resources.contexts.filter(context => !context.lost)).toHaveLength(4);
    const rows = await page.evaluate(({serial, session}) => window.arPerformanceProfiler.samplesAfter(serial)
      .filter(row => row.sessionId === session), {serial: initial.serial, session: resumed.sessionId});
    expect(rows.every(row => row.pipeline === 'face-cpu' && row.faceDelegate === 'CPU')).toBe(true);
    await writeFile(test.info().outputPath('fresh-runtime-late-ready.json'), JSON.stringify({initial, beforeStop, stopped, resumed, resources, rows, errors}, null, 2));
  } finally {await cleanup(page);}
  expect(errors).toEqual([]);
});

interface AutomaticReport {
  partial: boolean; completed: boolean; sessionId: string;
  protocol: {order: string[]; warmupMs: number; measureMs: number; maxSwitchMs: number; warmupTimeoutStartsAt: string};
  windows: {index: number; pipeline: string; completed: boolean; switchedAtMs: number; switchWaitMs: number;
    validWarmupFrames: number; measureStartedAtMs: number; endedAtMs: number;
    summary: {durationMs: number; frames: number; completedArFps: number}}[];
  rows: {windowIndex: number | null; phase: string; native: Record<string, unknown>;
    fields: Record<string, unknown>}[];
  hairDeliveryDrain: {state: string}; retention: {rejectedRows: number};
}
function telemetryFromZip(bytes: Buffer): AutomaticReport {
  let offset = 0;
  while (offset + 30 <= bytes.length && bytes.readUInt32LE(offset) === 0x04034b50) {
    expect(bytes.readUInt16LE(offset + 8)).toBe(0);
    const size = bytes.readUInt32LE(offset + 18), nameLength = bytes.readUInt16LE(offset + 26), extra = bytes.readUInt16LE(offset + 28);
    const name = bytes.toString('utf8', offset + 30, offset + 30 + nameLength), start = offset + 30 + nameLength + extra;
    expect(start + size).toBeLessThanOrEqual(bytes.length);
    if (name === 'telemetry.json') return JSON.parse(bytes.toString('utf8', start, start + size)) as AutomaticReport;
    offset = start + size;
  }
  throw new Error('The automatic comparison ZIP has no telemetry.json.');
}

test('fresh runtime automatic windows rebuild G and adjacent CPU with setup outside full measurement', async ({page}) => {
  test.setTimeout(210_000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installObservation(page); await page.goto(entry + '&candidate=face-cpu'); await page.click('#start');
  try {
    const initial = await published(page, 'g'), initialResources = await resourceSnapshot(page);
    await page.evaluate(() => {window.freshRuntimeObservation.gateNextCpu = true;});
    await page.click('#continuous-start');
    const first = await published(page, 'g', initial.serial, Number(initial.native?.['runtime.generation']));
    expect(Number(first.native?.['runtime.generation'])).toBeGreaterThan(Number(initial.native?.['runtime.generation']));
    expect(first.sessionId).toBe(initial.sessionId);
    const firstResources = await resourceSnapshot(page);
    expect(firstResources.workers.slice(0, initialResources.workers.length).every(worker => worker.terminated)).toBe(true);
    await expect.poll(() => page.evaluate(() => window.freshRuntimeObservation.blocked), {timeout: 90_000}).toBe(true);
    expect(await page.evaluate(() => window.arContinuousComparison.status())).toMatchObject({windowIndex: 1, state: 'switching', running: true});
    // Real elapsed setup deliberately exceeds the former switch-inclusive
    // 15-second limit. Production clocks and warmup durations are unchanged.
    await page.waitForTimeout(16_000);
    expect(await page.evaluate(() => window.arContinuousComparison.status())).toMatchObject({windowIndex: 1, state: 'switching', running: true});
    await page.evaluate(() => window.freshRuntimeObservation.release());
    const cpu = await published(page, 'face-cpu', first.serial), cpuResources = await resourceSnapshot(page);
    expect(Number(cpu.native?.['runtime.generation'])).toBeGreaterThan(Number(first.native?.['runtime.generation']));
    await expect.poll(() => page.evaluate(() => {
      const status = window.arContinuousComparison.status();
      return status?.windowIndex === 2 && status.state === 'warmup';
    }), {timeout: 65_000}).toBe(true);
    await expect.poll(() => page.evaluate(generation => window.arPerformanceProfiler.samplesAfter(0)
      .filter(row => row.pipeline === 'face-cpu' && row.hasFace && row.hasMask
        && Number(row.native?.['runtime.generation']) > generation).length,
    Number(cpu.native?.['runtime.generation']))).toBeGreaterThanOrEqual(3);
    const repeatedResources = await resourceSnapshot(page);
    expect(repeatedResources.workers.slice(0, cpuResources.workers.length).every(worker => worker.terminated)).toBe(true);
    expect(repeatedResources.contexts.slice(0, cpuResources.contexts.length).every(context => context.lost)).toBe(true);
    expect(repeatedResources.workers.filter(worker => !worker.terminated)).toHaveLength(2);
    expect(repeatedResources.contexts.filter(context => !context.lost)).toHaveLength(4);
    expect(repeatedResources.streams).toEqual([['live']]);
    const file = page.waitForEvent('download', {timeout: 45_000}); await page.click('#continuous-stop');
    const download = await file, filename = test.info().outputPath('fresh-runtime-auto-partial.zip'); await download.saveAs(filename);
    const report = telemetryFromZip(await readFile(filename));
    expect(report.partial).toBe(true); expect(report.completed).toBe(false); expect(report.sessionId).toBe(initial.sessionId);
    expect(report.protocol).toMatchObject({order: ['g', 'face-cpu', 'face-cpu', 'g'], warmupMs: 5000,
      measureMs: 30000, maxSwitchMs: 90000, warmupTimeoutStartsAt: 'switch-ready'});
    expect(report.hairDeliveryDrain.state).toBe('drained'); expect(report.retention.rejectedRows).toBe(0);
    expect(report.windows[1]!.switchWaitMs).toBeGreaterThanOrEqual(16000);
    const generations: number[] = [];
    for (const window of report.windows.slice(0, 2)) {
      expect(window.completed).toBe(true); expect(window.validWarmupFrames).toBeGreaterThanOrEqual(3);
      expect(window.measureStartedAtMs - window.switchedAtMs).toBeGreaterThanOrEqual(5000);
      expect(window.endedAtMs - window.measureStartedAtMs).toBe(30000); expect(window.summary.durationMs).toBe(30000);
      const measured = report.rows.filter(row => row.windowIndex === window.index && row.phase === 'measured');
      expect(measured.length).toBeGreaterThan(10); expect(window.summary.frames).toBe(measured.length);
      expect(window.summary.completedArFps).toBeCloseTo(measured.length / 30, 10);
      expect(measured.every(row => row.fields.sessionId === initial.sessionId && row.fields.pipeline === window.pipeline
        && Number(row.fields.capturedAtMs) >= window.measureStartedAtMs && Number(row.fields.publishedAtMs) < window.endedAtMs
        && row.native['runtime.isolation'] === 'fresh-runtime')).toBe(true);
      const epochs = [...new Set(measured.map(row => Number(row.native['runtime.generation'])))];
      expect(epochs).toHaveLength(1); generations.push(epochs[0]!);
    }
    const third = report.rows.filter(row => row.windowIndex === 2 && row.phase === 'warmup');
    expect(third.some(row => row.fields.hasFace && row.fields.hasMask)).toBe(true);
    const thirdEpochs = [...new Set(third.map(row => Number(row.native['runtime.generation'])))];
    expect(thirdEpochs).toHaveLength(1); generations.push(thirdEpochs[0]!);
    expect(generations[1]).toBeGreaterThan(generations[0]!); expect(generations[2]).toBeGreaterThan(generations[1]!);
    await writeFile(test.info().outputPath('fresh-runtime-auto-partial.json'), JSON.stringify(report, null, 2));
    await writeFile(test.info().outputPath('fresh-runtime-auto-resources.json'), JSON.stringify({initialResources, firstResources,
      cpuResources, repeatedResources, generations, errors}, null, 2));
  } finally {await page.evaluate(() => window.freshRuntimeObservation.release()); await cleanup(page);}
  expect(errors).toEqual([]);
});

test('fresh runtime FPS summaries follow hair toggles before and after a CPU switch', async ({page}) => {
  test.setTimeout(120_000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installObservation(page); await page.goto(entry); await page.click('#start');
  const receipts: {pipeline: Choice; variant: string; fps: string | null; coverage: string | null}[] = [];
  try {
    let priorSerial = 0;
    for (const pipeline of ['g', 'face-cpu'] as const) {
      if (pipeline !== 'g') await page.selectOption('#pipeline-select', pipeline);
      const hair = await published(page, pipeline, priorSerial); priorSerial = hair.serial;
      await expect(page.locator('#fps')).toHaveText(/^\d+(?:\.\d+)? fps$/);
      await expect(page.locator('#profile-fps')).toHaveText(/^\d+(?:\.\d+)? fps$/);
      await page.selectOption('#variant-select', 'accepted');
      await expect.poll(() => page.evaluate(({pipeline, serial}) => window.arPerformanceProfiler.samplesAfter(serial)
        .filter(row => row.pipeline === pipeline && row.variant === 'accepted' && row.hasFace).length,
      {pipeline, serial: priorSerial})).toBeGreaterThanOrEqual(4);
      await expect(page.locator('#fps')).toHaveText(/^\d+(?:\.\d+)? fps$/);
      await expect(page.locator('#profile-fps')).toHaveText(/^\d+(?:\.\d+)? fps$/);
      await expect(page.locator('#profile-coverage')).toHaveText('—');
      receipts.push({pipeline, variant: 'accepted', fps: await page.locator('#profile-fps').textContent(),
        coverage: await page.locator('#profile-coverage').textContent()});
      priorSerial = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)!.serial);
      await page.selectOption('#variant-select', 'hair'); const resumed = await published(page, pipeline, priorSerial);
      priorSerial = resumed.serial;
      await expect(page.locator('#fps')).toHaveText(/^\d+(?:\.\d+)? fps$/);
      await expect(page.locator('#profile-fps')).toHaveText(/^\d+(?:\.\d+)? fps$/);
      await expect(page.locator('#profile-coverage')).toHaveText(/^\d+%$/);
      receipts.push({pipeline, variant: 'hair', fps: await page.locator('#profile-fps').textContent(),
        coverage: await page.locator('#profile-coverage').textContent()});
    }
  } finally {
    await cleanup(page);
    await writeFile(test.info().outputPath('fresh-runtime-variant-summaries.json'), JSON.stringify({receipts, errors}, null, 2));
  }
  expect(errors).toEqual([]);
});
