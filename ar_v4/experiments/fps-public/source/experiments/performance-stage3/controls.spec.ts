import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';

type Pipeline = 'current' | 'test' | 'test2' | 'test3';
const PIPELINES = ['current', 'test', 'test2', 'test3'] as const;
const PIPELINE_LABELS = {current: 'Long-hair checkpoint', test: 'Test 1', test2: 'Current · Test 2', test3: 'Test 3'} as const;
interface HeldOutput {
  pair: {sourceSHA256: string; detectionSHA256: string; eyewearModel: string};
  detection: unknown;
  sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  mask: {outputMode: string; categorySHA256: string; categoryBase64: string;
    confidenceSHA256: string | null; confidenceBase64: string | null};
  stats: {hasMask: boolean; maskOutputMode: string};
}
interface HeldComparison {current: HeldOutput; test: HeldOutput; test2: HeldOutput; test3: HeldOutput; selectedPipeline: Pipeline; candidateAccepted: boolean; schema: string;}
interface PendingObservation {
  preparedPipeline: Pipeline; requestedPipeline: Pipeline; previousSequence: number;
  displayedBefore: string | undefined; displayedAfter: string | undefined;
  privateExportNull: boolean; holdRequested: boolean; heldVariant: string;
}
interface ControlState {
  streams: MediaStream[];
  workers: {terminated: boolean; requests: number; outputMode: string}[];
  armPendingSwitch: boolean; armCategoryHold: boolean; delayNextLiveHair: boolean;
  delayedResults: number; failedHeldSegments: number; failHeldSegment: boolean;
  pendingObservation: PendingObservation | null;
  beforeFailedUpgrade: HeldComparison | null;
  categoryHoldTriggered: boolean;
}
declare global {interface Window {performanceControls: ControlState;}}

async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/controls-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const source = document.createElement('canvas'); source.width = 640; source.height = 427;
    const context = source.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/controls-camera.jpg';
    const state: ControlState = {streams: [], workers: [], armPendingSwitch: false, armCategoryHold: false,
      delayNextLiveHair: false, delayedResults: 0, failedHeldSegments: 0, failHeldSegment: false,
      pendingObservation: null, beforeFailedUpgrade: null, categoryHoldTriggered: false};
    window.performanceControls = state;
    const draw = (): void => {
      context.fillStyle = '#163949'; context.fillRect(0, 0, source.width, source.height);
      if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, source.width, source.height);
    };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = source.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly index: number;
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.index = state.workers.length;
        const observed = {terminated: false, requests: 0, outputMode: 'unknown'}; state.workers.push(observed);
        this.addEventListener('message', (event: MessageEvent<Record<string, unknown>>) => {
          const output = event.data.output as {outputMode?: string} | undefined;
          if (event.data.type !== 'result' || output?.outputMode !== 'category-only' || !state.delayNextLiveHair) return;
          state.delayNextLiveHair = false; state.delayedResults++; event.stopImmediatePropagation();
          const callback = this.onmessage, data = event.data;
          // Make one existing hair wait observable. A stopped worker never delivers the delayed result.
          setTimeout(() => {if (!observed.terminated) callback?.call(this, new MessageEvent('message', {data}));}, 300);
        });
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const request = message as {type?: string; outputMode?: string; image?: ImageBitmap; sessionNonce?: string; requestId?: number};
        const observed = state.workers[this.index]!;
        if (request.type === 'initialize' && request.outputMode) observed.outputMode = request.outputMode;
        if (request.type === 'detect' || request.type === 'segment') observed.requests++;
        if (request.type === 'segment' && state.failHeldSegment && window.hairLivePreview.diagnostics().phase === 'held') {
          state.failHeldSegment = false; state.failedHeldSegments++;
          // The adapter's held export captures the valid category-only pair before the upgrade fails.
          state.beforeFailedUpgrade = window.hairLivePreview.exportDiagnostic() as unknown as HeldComparison;
          request.image?.close();
          queueMicrotask(() => {
            if (!observed.terminated) this.onmessage?.call(this, new MessageEvent('message', {data: {
              type: 'error', sessionNonce: request.sessionNonce, requestId: request.requestId,
              message: 'Deliberate full held-mask upgrade failure for controls QA.',
            }}));
          });
          return;
        }
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void {state.workers[this.index]!.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
    // Only active while armed. Inspect and click in one JS task so another frame
    // cannot replace the observed ownership state between the assertion and action.
    setInterval(() => {
      if ((!state.armPendingSwitch && !state.armCategoryHold) || !window.hairLivePreview) return;
      const value = window.hairLivePreview.diagnostics();
      const stats = value.stats as {hasMask?: boolean; maskOutputMode?: string} | null;
      const stage = document.querySelector<HTMLElement>('.stage')!;
      const hold = document.querySelector<HTMLButtonElement>('#hold-frame')!;
      if (value.phase !== 'live' || hold.disabled) return;
      if (state.armPendingSwitch && value.processing === true && stats === null && value.presented) {
        state.armPendingSwitch = false;
        const preparedPipeline = value.pipeline as Pipeline;
        const requestedPipeline = preparedPipeline === 'current' ? 'test' : preparedPipeline === 'test' ? 'test3' : 'current';
        const displayedBefore = stage.dataset.pipeline;
        const privateExportNull = window.hairLivePreview.exportDiagnostic() === null;
        const select = document.querySelector<HTMLSelectElement>('#pipeline-select')!;
        select.value = requestedPipeline; select.dispatchEvent(new Event('change'));
        const displayedAfter = stage.dataset.pipeline;
        // A held full-mask upgrade still needs hair preparation when the visible
        // variant is accepted. In particular Test3 must rebuild its skipped layers.
        const variant = document.querySelector<HTMLSelectElement>('#variant-select')!;
        variant.value = 'accepted'; variant.dispatchEvent(new Event('change'));
        hold.click();
        state.pendingObservation = {preparedPipeline, requestedPipeline, displayedBefore, displayedAfter, privateExportNull,
          previousSequence: (value.presented as {sequence: number}).sequence,
          heldVariant: variant.value,
          holdRequested: window.hairLivePreview.diagnostics().holdRequested === true};
      } else if (state.armCategoryHold && value.processing === false && stats?.hasMask && stats.maskOutputMode === 'category-only') {
        state.armCategoryHold = false; state.failHeldSegment = true; hold.click(); state.categoryHoldTriggered = true;
      }
    }, 1);
  });
}

async function resourcesClosed(page: Page): Promise<boolean> {
  return page.evaluate(() => window.performanceControls.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
    && window.performanceControls.workers.every(worker => worker.terminated));
}
async function waitCategory(page: Page, pipeline: Pipeline): Promise<void> {
  await expect.poll(() => page.evaluate(expectedPipeline => {
    const value = window.hairLivePreview.diagnostics(), stats = value.stats as {hasMask?: boolean; maskOutputMode?: string} | null;
    return value.phase === 'live' && value.pipeline === expectedPipeline && stats?.hasMask === true && stats.maskOutputMode === 'category-only';
  }, pipeline)).toBe(true);
}
async function completeHold(page: Page): Promise<void> {
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
  await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
  await expect.poll(() => resourcesClosed(page)).toBe(true);
  for (const id of ['pipeline-select', 'toggle-pipeline', 'stage-toggle-pipeline', 'download-diagnostic'])
    await expect(page.locator(`#${id}`)).toBeEnabled();
}
async function inspectHairToggles(page: Page, comparison: HeldComparison): Promise<void> {
  const resources = await page.evaluate(() => window.performanceControls.workers.map(worker => ({...worker})));
  for (const pipeline of PIPELINES) {
    await page.selectOption('#pipeline-select', pipeline);
    await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
    await expect(page.locator('#active-pipeline')).toHaveText(PIPELINE_LABELS[pipeline]);
    for (const variant of ['accepted', 'hair', 'accepted', 'hair'] as const) {
      await page.selectOption('#variant-select', variant);
      const pixels = await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png'));
      expect(pixels).toBe(comparison[pipeline][variant === 'accepted' ? 'acceptedPngDataUrl' : 'hairPngDataUrl']);
    }
  }
  expect(await page.evaluate(() => window.performanceControls.workers.map(worker => ({...worker})))).toEqual(resources);
}

test('pending Test3 frame queues algorithm switch and Hold, then all four held pipelines support exact hair toggles', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/performance-stage3/live.html');
  await page.selectOption('#eyewear-select', 'tom-ford-clear'); await page.click('#start');
  for (const pipeline of PIPELINES) {
    await page.selectOption('#pipeline-select', pipeline); await waitCategory(page, pipeline);
  }
  const session = await page.locator('.stage').getAttribute('data-session-id');
  await page.evaluate(() => {window.performanceControls.delayNextLiveHair = true; window.performanceControls.armPendingSwitch = true;});
  await expect.poll(() => page.evaluate(() => window.performanceControls.pendingObservation)).not.toBeNull();
  await completeHold(page);
  const observation = await page.evaluate(() => window.performanceControls.pendingObservation!);
  expect(observation.privateExportNull).toBe(true); expect(observation.holdRequested).toBe(true);
  expect(observation.preparedPipeline).toBe('test3'); expect(observation.requestedPipeline).toBe('current');
  expect(observation.heldVariant).toBe('accepted'); await expect(page.locator('#variant-select')).toHaveValue('accepted');
  expect(observation.displayedAfter).toBe(observation.displayedBefore);
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', observation.preparedPipeline);
  await expect(page.locator('#pipeline-select')).toHaveValue(observation.preparedPipeline);
  expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(session);
  expect(Number(await page.locator('.stage').getAttribute('data-frame-sequence'))).toBeGreaterThan(observation.previousSequence);
  const comparison = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as HeldComparison;
  expect(comparison.schema).toBe('ar-performance-stage3-comparison-v1'); expect(comparison.selectedPipeline).toBe('test3');
  expect(comparison.candidateAccepted).toBe(false);
  for (const pipeline of PIPELINES) {
    expect(comparison[pipeline].pair).toEqual(comparison.current.pair);
    expect(comparison[pipeline].detection).toEqual(comparison.current.detection);
    expect(comparison[pipeline].sourcePngDataUrl).toBe(comparison.current.sourcePngDataUrl);
    expect(comparison[pipeline].mask.outputMode).toBe('full');
    expect(comparison[pipeline].mask).toEqual(comparison.current.mask);
    expect(comparison[pipeline].stats.hasMask).toBe(true);
  }
  await inspectHairToggles(page, comparison);
  expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('pending-controls-receipt.json'), JSON.stringify({observation, pair: comparison.current.pair,
    exactHairToggles: true, liveCategoryObserved: [...PIPELINES], heldFullUpgradeWhileAccepted: true, resourcesClosed: true, errors,
    scope: 'Production bundles and real local workers on a synthetic camera; deliberate result delay, no speed or wearer claim.'}, null, 2));
  await page.click('#stop'); await expect.poll(() => resourcesClosed(page)).toBe(true);
});

test('failed held full-mask upgrade retains category pixels and unlocks exact pipeline toggles and download', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/performance-stage3/live.html');
  await page.selectOption('#pipeline-select', 'test3'); await page.selectOption('#eyewear-select', 'amber-horizon');
  await page.click('#start'); await waitCategory(page, 'test3');
  await page.evaluate(() => {window.performanceControls.armCategoryHold = true;});
  await expect.poll(() => page.evaluate(() => window.performanceControls.categoryHoldTriggered)).toBe(true);
  await completeHold(page);
  expect(await page.evaluate(() => window.performanceControls.failedHeldSegments)).toBe(1);
  const before = await page.evaluate(() => window.performanceControls.beforeFailedUpgrade!);
  const after = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as HeldComparison;
  expect(before).not.toBeNull(); expect(after).not.toBeNull();
  expect(before.selectedPipeline).toBe('test3'); expect(after.selectedPipeline).toBe('test3');
  for (const pipeline of PIPELINES) {
    expect(after[pipeline].stats.hasMask).toBe(true); expect(after[pipeline].mask.outputMode).toBe('category-only');
    for (const field of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
      expect(after[pipeline][field]).toEqual(before[pipeline][field]);
    expect(after[pipeline].pair).toEqual(after.current.pair);
    expect(after[pipeline].mask).toEqual(after.current.mask);
  }
  await expect(page.locator('#hair-status')).toContainText('Extra diagnostic data is unavailable');
  await inspectHairToggles(page, after);
  const downloadEvent = page.waitForEvent('download'); await page.click('#download-diagnostic');
  const download = await downloadEvent; const outputPath = test.info().outputPath('retained-category-comparison.json');
  await download.saveAs(outputPath);
  const saved = JSON.parse(await readFile(outputPath, 'utf8')) as HeldComparison;
  expect(saved.schema).toBe('ar-performance-stage3-comparison-v1');
  for (const pipeline of PIPELINES) {
    expect(saved[pipeline].pair).toEqual(after[pipeline].pair); expect(saved[pipeline].mask).toEqual(after[pipeline].mask);
    expect(saved[pipeline].sourcePngDataUrl).toBe(after[pipeline].sourcePngDataUrl);
    expect(saved[pipeline].acceptedPngDataUrl).toBe(after[pipeline].acceptedPngDataUrl);
    expect(saved[pipeline].hairPngDataUrl).toBe(after[pipeline].hairPngDataUrl);
  }
  await expect(page.locator('#download-diagnostic')).toBeEnabled();
  expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('failed-upgrade-controls-receipt.json'), JSON.stringify({pair: after.current.pair,
    failedUpgradePreservedCategoryPixels: true, liveCategoryObserved: ['test3'], exactHairToggles: [...PIPELINES], togglesAndDownloadEnabled: true, resourcesClosed: true, errors,
    scope: 'Production bundles and real local workers on a synthetic camera; one injected held segment error, no speed or wearer claim.'}, null, 2));
  await page.click('#stop'); await expect.poll(() => resourcesClosed(page)).toBe(true);
});
