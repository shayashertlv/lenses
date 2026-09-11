import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {FrameSample} from './frame-profiler.ts';
import {PIPELINE_LABELS} from './profiles.ts';

const RATES = {rate12: 12, rate10: 10, rate8: 8} as const;
type RatePipeline = keyof typeof RATES;
interface RequestObservation {
  kind: 'detect' | 'segment'; sequence: number | null; capturedAtMs: number | null;
  width: number; height: number; sessionId: string | null; phase: string | null;
}
interface StopObservation {sessionId: string; lastSerial: number; processing: boolean;}
interface RateCameraState {
  streams: MediaStream[]; workers: {terminated: boolean}[]; requests: RequestObservation[];
  armPendingStop: boolean; stoppedPending: StopObservation | null;
}
declare global {interface Window {rateCamera: RateCameraState;}}
interface Output {
  pair: {sourceSHA256: string; detectionSHA256: string; eyewearModel: string};
  detection: unknown; sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  mask: {categorySHA256: string; outputMode: string};
  stats: {hasMask: boolean; protectedCheck: {changedPixels: number} | null;
    noseCheck: {changedPixels: number} | null; outsideEditableCheck: {changedPixels: number} | null;
    backgroundPreservationCheck: {changedPixels: number} | null};
  captureSnapshot: {surfacePositions: number[]; eyewearMatrix: number[]; protection: unknown};
}
type HeldComparison = Record<RatePipeline | 'g', Output> & {candidateAccepted: boolean; currentBase: string};

/** Uses the existing checked-in camera fixture and real local face/hair workers.
 * Observations do not replace inference, pixels, geometry or GPU operations. */
async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/rate-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 427;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/rate-camera.jpg';
    const state: RateCameraState = {streams: [], workers: [], requests: [], armPendingStop: false, stoppedPending: null};
    window.rateCamera = state;
    const draw = (): void => {
      context.fillStyle = '#163949'; context.fillRect(0, 0, canvas.width, canvas.height);
      if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, canvas.width, canvas.height);
    };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly observation: {terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.observation = {terminated: false}; state.workers.push(this.observation);
        this.addEventListener('message', (event: MessageEvent<Record<string, unknown>>) => {
          const output = event.data.output as {outputMode?: string} | undefined;
          if (!state.armPendingStop || event.data.type !== 'result' || output?.outputMode !== 'category-only') return;
          const value = window.hairLivePreview.diagnostics();
          if (value.phase !== 'live' || value.pipeline !== 'rate8' || value.processing !== true || !value.presented) return;
          state.armPendingStop = false;
          state.stoppedPending = {sessionId: value.sessionId as string,
            lastSerial: window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0, processing: true};
          // Cancel before the real pending hair result reaches its client. This
          // tests revocation of owned work; no fabricated worker result is used.
          event.stopImmediatePropagation(); document.querySelector<HTMLButtonElement>('#stop')!.click();
        });
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const value = message as {type?: string; sequence?: number; timestampMs?: number; image?: ImageBitmap};
        if (value.type === 'detect' || value.type === 'segment') {
          const diagnostic = window.hairLivePreview.diagnostics();
          state.requests.push({kind: value.type, sequence: value.sequence ?? null, capturedAtMs: value.timestampMs ?? null,
            width: value.image?.width ?? 0, height: value.image?.height ?? 0,
            sessionId: diagnostic.sessionId as string | null, phase: diagnostic.phase as string | null});
        }
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void {this.observation.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  });
}

const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.rateCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.rateCamera.workers.every(worker => worker.terminated));

async function collect(page: Page, pipeline: RatePipeline): Promise<FrameSample[]> {
  const after = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0);
  if (await page.locator('#pipeline-select').inputValue() !== pipeline) await page.selectOption('#pipeline-select', pipeline);
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
  await expect.poll(() => page.evaluate(({after, pipeline}) =>
    window.arPerformanceProfiler.samplesAfter(after).filter(row => row.pipeline === pipeline).length, {after, pipeline}))
    .toBeGreaterThanOrEqual(8);
  return page.evaluate(({after, pipeline}) =>
    window.arPerformanceProfiler.samplesAfter(after).filter(row => row.pipeline === pipeline), {after, pipeline});
}

function checkRate(rows: FrameSample[], pipeline: RatePipeline): void {
  expect(rows.length).toBeGreaterThanOrEqual(8);
  const cap = RATES[pipeline], gaps = rows.slice(1).map((row, index) => row.capturedAtMs - rows[index]!.capturedAtMs);
  // Only floating point arithmetic tolerance; there is no dropped-frame or FPS
  // tolerance. We test source admission times, not bursty completion times.
  for (const gap of gaps) expect(gap).toBeGreaterThanOrEqual(1000 / cap - 0.001);
  for (const row of rows) {
    expect(row.pipeline).toBe(pipeline); expect(row.native?.['admission.rateHz']).toBe(cap);
    expect(row.native?.['pump.maxOwnedFrames']).toBeLessThanOrEqual(2);
    expect(row.native?.['pump.maxInFlightInference']).toBeLessThanOrEqual(1);
    expect(row.sourceWidth).toBe(640); expect(row.sourceHeight).toBe(427);
    expect(row.publishedAtMs).toBeGreaterThanOrEqual(row.capturedAtMs);
  }
  expect(rows.some(row => row.hasFace && row.hasMask && row.maskMode === 'category-only')).toBe(true);
}

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass']) {
  test(`${eyewear}/${hair}: lower capture rates keep exact held G pairs and respect admission caps`, async ({page}) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await installCamera(page); await page.goto('/experiments/efficiency-lab/live.html?pipeline=rate12');
    await expect(page.locator('#pipeline-select')).toHaveValue('rate12');
    await expect(page.locator('#active-pipeline')).toHaveText(PIPELINE_LABELS.rate12);
    expect(await page.evaluate(() => window.rateCamera.streams.length)).toBe(0);
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    const sessionId = await page.locator('.stage').getAttribute('data-session-id');
    const first = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0)[0]!);
    expect(first.pipeline).toBe('rate12');
    const collected: Partial<Record<RatePipeline, FrameSample[]>> = {};
    for (const pipeline of Object.keys(RATES) as RatePipeline[]) {
      const rows = await collect(page, pipeline); checkRate(rows, pipeline); collected[pipeline] = rows;
      expect(rows.every(row => row.eyewearId === eyewear && row.hairModelId === hair)).toBe(true);
      expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(sessionId);
    }
    await expect.poll(() => page.evaluate(() => {
      const rows = window.arPerformanceProfiler.samplesAfter(0).filter(row => row.pipeline === 'rate8');
      return Math.max(0, ...rows.map(row => Number(row.native?.['admission.rateSkipped'] ?? 0)));
    })).toBeGreaterThan(0);
    await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
    await expect.poll(() => closed(page)).toBe(true);
    const comparison = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as HeldComparison;
    expect(comparison.currentBase).toBe('g'); expect(comparison.candidateAccepted).toBe(false);
    for (const pipeline of Object.keys(RATES) as RatePipeline[]) {
      const output = comparison[pipeline]; expect(output.stats.hasMask).toBe(true);
      for (const field of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
        expect(output[field], `${pipeline} ${field}`).toEqual(comparison.g[field]);
      for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
        expect(output.captureSnapshot[key]).toEqual(comparison.g.captureSnapshot[key]);
      for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
        expect(output.stats[key]?.changedPixels, `${pipeline} ${key}`).toBe(0);
      await page.selectOption('#pipeline-select', pipeline);
      for (const variant of ['accepted', 'hair'] as const) {
        await page.selectOption('#variant-select', variant);
        expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png')))
          .toBe(output[variant === 'hair' ? 'hairPngDataUrl' : 'acceptedPngDataUrl']);
      }
    }
    const requests = await page.evaluate(() => window.rateCamera.requests);
    for (const rows of Object.values(collected)) for (const row of rows) {
      expect(requests.some(request => request.kind === 'detect' && request.capturedAtMs === row.capturedAtMs
        && request.sessionId === row.sessionId && request.width === 640 && request.height === 427)).toBe(true);
      if (row.hasMask) expect(requests.some(request => request.kind === 'segment' && request.sequence === row.sequence
        && request.sessionId === row.sessionId && request.phase === 'live' && request.width === row.sourceWidth
        && request.height === row.sourceHeight)).toBe(true);
    }
    await page.click('#resume-live'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    expect(await page.locator('.stage').getAttribute('data-session-id')).not.toBe(sessionId);
    await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', 'rate8');
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(errors).toEqual([]);
    await writeFile(test.info().outputPath('rate-receipt.json'), JSON.stringify({eyewear, hair, collected,
      exactHeldPixelsDetectionMaskGeometry: true, protectedChecks: true, stoppedAndRestarted: true, errors,
      scope: 'Production browser, real local workers, static synthetic camera. Validates source-admission caps and held G cache mapping only; rate modes share the unchanged G renderer and held output. New down/up/yaw motion, physical-camera smoothness and wearer acceptance are unmeasured.'}, null, 2));
  });
}

test('rate limit startup cancellation, pending-worker stop and fresh G restart revoke old publication', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/efficiency-lab/live.html?pipeline=rate8');
  await page.click('#start'); await page.click('#stop');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle'); await expect.poll(() => closed(page)).toBe(true);
  await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await collect(page, 'rate8');
  await page.evaluate(() => {window.rateCamera.armPendingStop = true;});
  await expect.poll(() => page.evaluate(() => window.rateCamera.stoppedPending)).not.toBeNull();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle'); await expect.poll(() => closed(page)).toBe(true);
  const stopped = await page.evaluate(() => window.rateCamera.stoppedPending!);
  expect(stopped.processing).toBe(true);
  expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
  await page.selectOption('#pipeline-select', 'g'); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', 'g');
  const rows = await page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial), stopped.lastSerial);
  expect(rows.length).toBeGreaterThan(0); expect(rows.every(row => row.sessionId !== stopped.sessionId)).toBe(true);
  expect(rows.every(row => row.pipeline === 'g' && row.native?.['admission.rateHz'] === null)).toBe(true);
  await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('rate-cancellation-receipt.json'), JSON.stringify({stopped,
    newRows: rows, oldSessionPublishedAfterStop: false, errors,
    scope: 'One real pending worker result is cancelled before consumption; synthetic camera, no speed claim.'}, null, 2));
});
