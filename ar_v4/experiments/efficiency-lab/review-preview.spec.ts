import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {FrameSample} from './frame-profiler.ts';
import {PIPELINES, PIPELINE_LABELS} from './profiles.ts';

const REVIEW = ['g', 'publish', 'region', 'lens', 'ui'] as const;
type ReviewPipeline = typeof REVIEW[number];
interface ReviewCamera {
  streams: MediaStream[]; workers: {terminated: boolean}[];
  requests: {sequence: number; sessionId: string | null; phase: string | null}[];
  armStop: boolean; stopped: {sessionId: string; lastSerial: number; pipeline: string} | null;
}
declare global {interface Window {reviewCamera: ReviewCamera;}}
interface HeldOutput {pair: unknown; detection: unknown; mask: unknown; sourcePngDataUrl: string;
  acceptedPngDataUrl: string; hairPngDataUrl: string;
  captureSnapshot: {surfacePositions: unknown; eyewearMatrix: unknown; protection: unknown};
  stats: {hasMask: boolean; protectedCheck: {changedPixels: number}; noseCheck: {changedPixels: number};
    outsideEditableCheck: {changedPixels: number}; backgroundPreservationCheck: {changedPixels: number}};}

/** Real local workers and GPU rendering. Observation only; no result, geometry,
 * mask or renderer operation is replaced. The pending-stop case cancels one
 * actual worker response before the client consumes it. */
async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/review-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 427;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/review-camera.jpg';
    const state: ReviewCamera = {streams: [], workers: [], requests: [], armStop: false, stopped: null}; window.reviewCamera = state;
    const draw = (): void => {if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, canvas.width, canvas.height);};
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const Native = window.Worker;
    class ObservedWorker extends Native {
      private readonly observation: {terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.observation = {terminated: false}; state.workers.push(this.observation);
        this.addEventListener('message', (event: MessageEvent<Record<string, unknown>>) => {
          const output = event.data.output as {outputMode?: string} | undefined;
          if (!state.armStop || event.data.type !== 'result' || output?.outputMode !== 'category-only') return;
          const diagnostic = window.hairLivePreview.diagnostics();
          if (diagnostic.phase !== 'live' || diagnostic.pipeline !== 'lens' || !diagnostic.presented) return;
          state.armStop = false; state.stopped = {sessionId: diagnostic.sessionId as string, pipeline: 'lens',
            lastSerial: window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0};
          event.stopImmediatePropagation(); document.querySelector<HTMLButtonElement>('#stop')!.click();
        });
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const value = message as {type?: string; sequence?: number};
        if (value.type === 'segment') {const diagnostic = window.hairLivePreview.diagnostics();
          state.requests.push({sequence: value.sequence!, sessionId: diagnostic.sessionId as string | null, phase: diagnostic.phase as string | null});}
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void {this.observation.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  });
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.reviewCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.reviewCamera.workers.every(worker => worker.terminated));
async function collect(page: Page, pipeline: ReviewPipeline): Promise<FrameSample[]> {
  const after = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0);
  if (await page.locator('#pipeline-select').inputValue() !== pipeline) await page.selectOption('#pipeline-select', pipeline);
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
  await expect.poll(() => page.evaluate(({after, pipeline}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.pipeline === pipeline && row.hasMask).length, {after, pipeline})).toBeGreaterThanOrEqual(10);
  return page.evaluate(({after, pipeline}) => window.arPerformanceProfiler.samplesAfter(after).filter(row => row.pipeline === pipeline), {after, pipeline});
}
function checkMechanism(rows: FrameSample[], pipeline: ReviewPipeline): void {
  expect(rows.length).toBeGreaterThanOrEqual(10);
  for (const row of rows) {
    const native = row.native; expect(native).not.toBeNull(); if (!native) throw new Error('Actual runtime counters are missing.');
    expect(native['admission.rateHz']).toBeNull(); expect(native['pump.mode']).toBe('overlap');
    expect(native['pump.maxOwnedFrames']).toBeLessThanOrEqual(2); expect(native['pump.maxInFlightInference']).toBeLessThanOrEqual(1);
    expect(row.sourceWidth).toBe(640); expect(row.sourceHeight).toBe(427);
    expect(native['publication.suppressUnchangedRequested']).toBe(pipeline === 'publish');
    expect(native['ui.throttleSummariesRequested']).toBe(pipeline === 'ui');
    expect(native['ui.summaryIntervalMs']).toBe(pipeline === 'ui' ? 500 : 0);
    expect(Number(native['publication.prePrepareCalls']) + Number(native['publication.prePrepareSkipped']))
      .toBe(native['publication.prePrepareRequests']);
    if (pipeline !== 'publish') expect(native['publication.prePrepareSkippedThisFrame']).toBe(false);
  }
  if (pipeline === 'publish') expect(rows.some(row => row.native?.['publication.prePrepareSkippedThisFrame'] === true)).toBe(true);
  if (pipeline === 'ui') {
    expect(rows.some(row => row.native?.['ui.summaryRefreshThisFrame'] === false)).toBe(true);
    expect(rows.some(row => Number(row.native?.['ui.summarySkipped']) > 0)).toBe(true);
    expect(rows.some(row => row.native?.['ui.summaryRefreshThisFrame'] === true)).toBe(true);
  }
  if (pipeline === 'region') {
    const branches = rows.filter(row => row.native?.['nativePipeline.efficiencyLab.branchRegion.used'] === true);
    expect(branches.length).toBeGreaterThan(0);
    for (const row of branches) {
      const n = row.native!, prefix = 'nativePipeline.efficiencyLab.branchRegion.';
      expect(n[prefix + 'requested']).toBe(true); expect(n[prefix + 'fallback']).toBeNull();
      expect(n[prefix + 'fullBytes']).toBe(640 * 427 * 4);
      expect(n[prefix + 'readBytes']).toBe(n['nativePipeline.branchReadbackBytes']);
      expect(Number(n[prefix + 'readBytes'])).toBeLessThan(Number(n[prefix + 'fullBytes']));
      expect(n['nativePipeline.zeroDropBranchSkipped']).toBe(false);
      if (n[prefix + 'rectangle'] === null) {
        // This static frontal fixture can have every editable pixel protected.
        // Full frozen angle matrices separately require positive cropped reads.
        expect(n[prefix + 'readBytes']).toBe(0);
        expect(n['nativePipeline.branchReadbackCalls']).toBe(0);
        for (const key of ['x0', 'x1', 'y0', 'y1']) expect(n[prefix + 'rectangle.' + key]).toBeUndefined();
      } else {
        const y0 = Number(n[prefix + 'rectangle.y0']), y1 = Number(n[prefix + 'rectangle.y1']);
        expect(n[prefix + 'rectangle.x0']).toBe(0); expect(n[prefix + 'rectangle.x1']).toBe(640);
        expect(Number.isInteger(y0) && Number.isInteger(y1)).toBe(true);
        expect(y0).toBeGreaterThanOrEqual(0); expect(y1).toBeLessThanOrEqual(427); expect(y1).toBeGreaterThan(y0);
        expect(n[prefix + 'readBytes']).toBe(640 * (y1 - y0) * 4);
        expect(n['nativePipeline.branchReadbackCalls']).toBe(1);
      }
    }
  }
  if (pipeline === 'lens') {
    const branches = rows.filter(row => row.native?.['nativePipeline.efficiencyLab.branchLenses.used'] === true);
    expect(branches.length).toBeGreaterThan(0);
    for (const row of branches) {
      expect(row.native?.['nativePipeline.efficiencyLab.branchLenses.requested']).toBe(true);
      expect(Number(row.native?.['nativePipeline.efficiencyLab.branchLenses.omittedMaterials'])).toBeGreaterThan(0);
      expect(row.native?.['nativePipeline.efficiencyLab.branchLenses.fallback']).toBeNull();
    }
  }
}

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass']) {
  test(`${eyewear}/${hair}: review Q–T use their real paths and retain exact held G safeguards`, async ({page}) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await installCamera(page); await page.goto('/experiments/efficiency-lab/live.html?study=review');
    await expect(page.locator('#pipeline-select')).toHaveValue('g');
    expect(await page.locator('#pipeline-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value))).toEqual(REVIEW);
    expect(await page.evaluate(() => window.reviewCamera.streams.length)).toBe(0);
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    const sessionId = await page.locator('.stage').getAttribute('data-session-id');
    const first = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0)[0]!); expect(first.pipeline).toBe('g');
    const collected: Partial<Record<ReviewPipeline, FrameSample[]>> = {};
    for (const pipeline of REVIEW) {
      const rows = await collect(page, pipeline); collected[pipeline] = rows; checkMechanism(rows, pipeline);
      expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(sessionId);
      await expect(page.locator('#active-pipeline')).toHaveText(PIPELINE_LABELS[pipeline]);
    }
    checkMechanism(await collect(page, 'g'), 'g');
    const requests = await page.evaluate(() => window.reviewCamera.requests);
    for (const rows of Object.values(collected)) for (const row of rows.filter(row => row.hasMask)) {
      expect(row.eyewearId).toBe(eyewear); expect(row.hairModelId).toBe(hair);
      expect(requests.some(request => request.sequence === row.sequence && request.sessionId === row.sessionId && request.phase === 'live')).toBe(true);
    }
    await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
    await expect.poll(() => closed(page)).toBe(true);
    const comparison = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as
      Record<ReviewPipeline, HeldOutput> & {currentBase: string; candidateAccepted: boolean};
    expect(comparison.currentBase).toBe('g'); expect(comparison.candidateAccepted).toBe(false);
    for (const pipeline of [...REVIEW.slice(1), 'g'] as ReviewPipeline[]) {
      const output = comparison[pipeline]; expect(output.stats.hasMask).toBe(true);
      for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
        expect(output[key], `${pipeline}/${key}`).toEqual(comparison.g[key]);
      for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
        expect(output.captureSnapshot[key], `${pipeline}/${key}`).toEqual(comparison.g.captureSnapshot[key]);
      for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
        expect(output.stats[key]?.changedPixels, `${pipeline}/${key}`).toBe(0);
      for (const variant of ['accepted', 'hair'] as const) {
        await page.selectOption('#pipeline-select', pipeline); await page.selectOption('#variant-select', variant);
        expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png')))
          .toBe(output[variant === 'hair' ? 'hairPngDataUrl' : 'acceptedPngDataUrl']);
      }
    }
    await page.click('#resume-live'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    expect(await page.locator('.stage').getAttribute('data-session-id')).not.toBe(sessionId);
    checkMechanism(await collect(page, 'g'), 'g');
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(errors).toEqual([]);
    const download = page.waitForEvent('download'); await page.click('#download-metrics');
    const filename = test.info().outputPath('review-timings.json'); await (await download).saveAs(filename);
    const exported = await readFile(filename, 'utf8'); expect(exported).not.toMatch(/sourcePng|landmarks|categoryBase64|sourceSHA256/);
    const samples = (JSON.parse(exported) as {samples: FrameSample[]}).samples;
    expect(samples.every((row, index) => index === 0 || row.serial === samples[index - 1]!.serial + 1)).toBe(true);
    for (const mode of REVIEW) expect(samples.some(row => row.pipeline === mode)).toBe(true);
    await writeFile(test.info().outputPath('review-live-receipt.json'), JSON.stringify({eyewear, hair, first, collected,
      exactHeldPixelsMaskDetectionGeometry: true, safeguards: true, uncapped: true, errors,
      scope: 'Real local workers, synthetic static 640x427 camera. Q and T share G held output; their live counters prove operation suppression. R/S real branch paths and held guards are checked; R may legitimately use an empty protected region with zero reads here. Separate frozen matrices require positive cropped reads for every glasses/hair combination in each phase and cover angle rendering. No physical-camera speed, wearer-motion acceptance or thermal claim.'}, null, 2));
  });
}

test('review startup cancellation and pending lens-worker stop retain no stale publications after G restart', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/efficiency-lab/live.html?study=review&pipeline=region');
  await expect(page.locator('#pipeline-select')).toHaveValue('region');
  await page.click('#start'); await page.click('#stop'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect.poll(() => closed(page)).toBe(true);
  await page.selectOption('#pipeline-select', 'lens'); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking'); checkMechanism(await collect(page, 'lens'), 'lens');
  await page.evaluate(() => {window.reviewCamera.armStop = true;});
  await expect.poll(() => page.evaluate(() => window.reviewCamera.stopped)).not.toBeNull();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle'); await expect.poll(() => closed(page)).toBe(true);
  const stopped = await page.evaluate(() => window.reviewCamera.stopped!);
  expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
  await page.selectOption('#pipeline-select', 'g'); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking'); checkMechanism(await collect(page, 'g'), 'g');
  const rows = await page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial), stopped.lastSerial);
  expect(rows.every(row => row.sessionId !== stopped.sessionId && row.pipeline === 'g')).toBe(true);
  await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(errors).toEqual([]);
  await page.goto('/experiments/efficiency-lab/live.html');
  await expect(page.locator('#pipeline-select option')).toHaveCount(PIPELINES.length); await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await writeFile(test.info().outputPath('review-cancel-receipt.json'), JSON.stringify({stopped, rows, noLatePublication: true, oldOptionsRetained: true, errors}, null, 2));
});
