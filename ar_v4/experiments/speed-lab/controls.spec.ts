import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {PIPELINES, PIPELINE_LABELS} from './profiles.ts';
import type {Pipeline} from './profiles.ts';
import type {FrameSample, ProfileSummary, WorkCoverage} from './frame-profiler.ts';

interface HeldOutput {
  pair: {sourceSHA256: string; detectionSHA256: string; eyewearModel: string};
  detection: unknown; sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  mask: {outputMode: string; sourceSHA256: string; detectionSHA256: string; categorySHA256: string;
    categoryBase64: string; confidenceSHA256: string | null; confidenceBase64: string | null};
  stats: {hasMask: boolean; maskOutputMode: string};
}
type HeldComparison = Record<Pipeline, HeldOutput> & {selectedPipeline: Pipeline; candidateAccepted: boolean; schema: string};
interface PendingObservation {
  preparedPipeline: string; requestedPipeline: string; previousSequence: number;
  displayedBefore: string | undefined; displayedAfter: string | undefined;
  privateExportNull: boolean; privateStatsNull: boolean; holdRequested: boolean; heldVariant: string;
}
interface StopObservation {sessionId: string; pipeline: string; lastSerial: number; privateStatsNull: boolean;
  privateExportNull: boolean; processing: boolean; trigger: 'native-fence' | 'worker-result';}
interface CategoryBeforeHold {
  pipeline: string; sourceSHA256: string; detectionSHA256: string; sequence: number;
  categoryBase64: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
}
interface ControlState {
  streams: MediaStream[]; workers: {terminated: boolean; requests: number; outputMode: string}[];
  armPendingHold: boolean; armAsyncStop: boolean; armOverlapStop: boolean; armCategoryHold: boolean;
  armPrefetchToggle: boolean; prefetchToggle: {sequence: number; previousSequence: number; privateStatsNull: boolean} | null;
  failHeldSegment: boolean; failedHeldSegments: number; fenceInterceptions: number;
  pendingObservation: PendingObservation | null; stopObservation: StopObservation | null;
  beforeFailedUpgrade: CategoryBeforeHold | null; upgradeWasLocked: boolean; upgradeExportWasNull: boolean;
  categoryResults: {sequence: number; sourceSHA256: string; category: Uint8Array}[];
}
declare global {interface Window {speedLabControls: ControlState;}}

async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/speed-controls-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const source = document.createElement('canvas'); source.width = 640; source.height = 427;
    const context = source.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/speed-controls-camera.jpg';
    const state: ControlState = {streams: [], workers: [], armPendingHold: false, armAsyncStop: false,
      armOverlapStop: false, armCategoryHold: false, failHeldSegment: false, failedHeldSegments: 0,
      armPrefetchToggle: false, prefetchToggle: null,
      fenceInterceptions: 0, pendingObservation: null, stopObservation: null, beforeFailedUpgrade: null,
      upgradeWasLocked: false, upgradeExportWasNull: false, categoryResults: []};
    window.speedLabControls = state;
    const draw = (): void => {
      context.fillStyle = '#163949'; context.fillRect(0, 0, source.width, source.height);
      if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, source.width, source.height);
    };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = source.captureStream(30); state.streams.push(stream); return stream;
    }});
    const stopWhilePending = (trigger: StopObservation['trigger']): void => {
      const value = window.hairLivePreview.diagnostics();
      state.stopObservation = {sessionId: value.sessionId as string, pipeline: value.pipeline as string,
        lastSerial: window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0,
        privateStatsNull: value.stats === null, privateExportNull: window.hairLivePreview.exportDiagnostic() === null,
        processing: value.processing === true, trigger};
      document.querySelector<HTMLButtonElement>('#stop')!.click();
    };
    // Delay exactly one real native fence notification by one event-loop turn.
    // This exposes PBO ownership deterministically without replacing rendering,
    // pixels, shader results or the actual GL fence call. It is a lifecycle test.
    const wait = WebGL2RenderingContext.prototype.clientWaitSync;
    WebGL2RenderingContext.prototype.clientWaitSync = function(sync, flags, timeout): GLenum {
      const status = wait.call(this, sync, flags, timeout);
      if ((!state.armPendingHold && !state.armAsyncStop) || status === this.WAIT_FAILED || !window.hairLivePreview) return status;
      const value = window.hairLivePreview.diagnostics(), hold = document.querySelector<HTMLButtonElement>('#hold-frame')!;
      if (value.phase !== 'live' || value.pipeline !== 'async' || value.processing !== true || value.stats !== null
        || !value.presented || hold.disabled) return status;
      const stop = state.armAsyncStop; state.armAsyncStop = state.armPendingHold = false; state.fenceInterceptions++;
      setTimeout(() => {
        if (stop) {stopWhilePending('native-fence'); return;}
        const current = window.hairLivePreview.diagnostics(), stage = document.querySelector<HTMLElement>('.stage')!;
        const displayedBefore = stage.dataset.pipeline, privateExportNull = window.hairLivePreview.exportDiagnostic() === null;
        const select = document.querySelector<HTMLSelectElement>('#pipeline-select')!;
        select.value = 'base'; select.dispatchEvent(new Event('change'));
        const displayedAfter = stage.dataset.pipeline;
        const variant = document.querySelector<HTMLSelectElement>('#variant-select')!;
        variant.value = 'accepted'; variant.dispatchEvent(new Event('change'));
        hold.click();
        state.pendingObservation = {preparedPipeline: current.pipeline as string, requestedPipeline: select.value,
          previousSequence: (current.presented as {sequence: number}).sequence, displayedBefore, displayedAfter,
          privateExportNull, privateStatsNull: current.stats === null,
          holdRequested: window.hairLivePreview.diagnostics().holdRequested === true, heldVariant: variant.value};
      }, 0);
      return this.TIMEOUT_EXPIRED;
    };
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly index: number;
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.index = state.workers.length;
        const observed = {terminated: false, requests: 0, outputMode: 'unknown'}; state.workers.push(observed);
        this.addEventListener('message', (event: MessageEvent<Record<string, unknown>>) => {
          const output = event.data.output as {outputMode?: string; sequence: number; sourceSHA256: string; category: Uint8Array} | undefined;
          if (event.data.type !== 'result' || output?.outputMode !== 'category-only') return;
          if (state.armCategoryHold) {
            state.categoryResults.push({sequence: output.sequence, sourceSHA256: output.sourceSHA256, category: output.category.slice()});
            if (state.categoryResults.length > 4) state.categoryResults.shift();
          }
          if (state.armOverlapStop && window.hairLivePreview) {
            const value = window.hairLivePreview.diagnostics();
            if (value.phase === 'live' && value.pipeline === 'overlap' && value.processing === true && value.presented) {
              state.armOverlapStop = false;
              // Stop while this real worker result is still owned by its pending
              // request. Closing terminates it before the client consumes bytes.
              event.stopImmediatePropagation(); stopWhilePending('worker-result');
            }
          }
        });
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const request = message as {type?: string; outputMode?: string; image?: ImageBitmap; sessionNonce?: string; requestId?: number; sequence?: number};
        const observed = state.workers[this.index]!;
        if (request.type === 'initialize' && request.outputMode) observed.outputMode = request.outputMode;
        if (request.type === 'detect' || request.type === 'segment') observed.requests++;
        if (request.type === 'segment' && state.armPrefetchToggle && window.hairLivePreview) {
          const value = window.hairLivePreview.diagnostics();
          const previous = value.presented as {sequence: number} | null;
          // A new packet's segmentation request while the renderer owns another
          // private pair exercises actual prefetch, not a synthetic pump fixture.
          if (value.phase === 'live' && value.pipeline === 'overlap' && value.processing === true && value.stats === null
            && previous && request.sequence !== undefined && request.sequence > previous.sequence) {
            state.armPrefetchToggle = false;
            state.prefetchToggle = {sequence: request.sequence, previousSequence: previous.sequence, privateStatsNull: true};
            const variant = document.querySelector<HTMLSelectElement>('#variant-select')!;
            variant.value = 'accepted'; variant.dispatchEvent(new Event('change'));
          }
        }
        if (request.type === 'segment' && state.failHeldSegment && window.hairLivePreview.diagnostics().phase === 'held') {
          state.failHeldSegment = false; state.failedHeldSegments++;
          state.upgradeWasLocked = ['pipeline-select', 'toggle-pipeline', 'stage-toggle-pipeline', 'download-diagnostic']
            .every(id => (document.getElementById(id) as HTMLButtonElement | HTMLSelectElement).disabled);
          // Speed lab populates its eight-output cache after the upgrade attempt;
          // it must not expose a previous comparison while that work is pending.
          state.upgradeExportWasNull = window.hairLivePreview.exportDiagnostic() === null;
          request.image?.close();
          queueMicrotask(() => {
            if (!observed.terminated) this.onmessage?.call(this, new MessageEvent('message', {data: {
              type: 'error', sessionNonce: request.sessionNonce, requestId: request.requestId,
              message: 'Deliberate full held-mask upgrade failure for speed-lab controls QA.',
            }}));
          });
          return;
        }
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void {state.workers[this.index]!.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
    setInterval(() => {
      if (!state.armCategoryHold || !window.hairLivePreview) return;
      const value = window.hairLivePreview.diagnostics();
      const stats = value.stats as {hasMask?: boolean; maskOutputMode?: string} | null;
      const held = document.querySelector<HTMLButtonElement>('#hold-frame')!;
      if (value.phase !== 'live' || value.processing !== false || held.disabled || !stats?.hasMask || stats.maskOutputMode !== 'category-only') return;
      const pair = value.presented as {sequence: number; sourceSHA256: string; detectionSHA256: string};
      const category = state.categoryResults.find(item => item.sequence === pair.sequence && item.sourceSHA256 === pair.sourceSHA256);
      if (!category) return;
      state.armCategoryHold = false;
      const variant = document.querySelector<HTMLSelectElement>('#variant-select')!, canvas = document.querySelector<HTMLCanvasElement>('#mirror')!;
      const hairPngDataUrl = canvas.toDataURL('image/png');
      variant.value = 'accepted'; variant.dispatchEvent(new Event('change'));
      const acceptedPngDataUrl = canvas.toDataURL('image/png');
      variant.value = 'hair'; variant.dispatchEvent(new Event('change'));
      let binary = '';
      for (let offset = 0; offset < category.category.length; offset += 0x4000)
        binary += String.fromCharCode(...category.category.subarray(offset, offset + 0x4000));
      state.beforeFailedUpgrade = {...pair, pipeline: value.pipeline as string, categoryBase64: btoa(binary),
        acceptedPngDataUrl, hairPngDataUrl};
      state.categoryResults.length = 0; state.failHeldSegment = true; held.click();
    }, 1);
  });
}

const resourcesClosed = (page: Page): Promise<boolean> => page.evaluate(() =>
  window.speedLabControls.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.speedLabControls.workers.every(worker => worker.terminated));
async function waitCategory(page: Page, pipeline: Pipeline): Promise<void> {
  await expect.poll(() => page.evaluate(expected => {
    const value = window.hairLivePreview.diagnostics(), stats = value.stats as {hasMask?: boolean; maskOutputMode?: string} | null;
    return value.phase === 'live' && value.pipeline === expected && stats?.hasMask === true && stats.maskOutputMode === 'category-only';
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
  const workers = await page.evaluate(() => window.speedLabControls.workers.map(worker => ({...worker})));
  for (const pipeline of PIPELINES) {
    await page.selectOption('#pipeline-select', pipeline);
    await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
    await expect(page.locator('#active-pipeline')).toHaveText(PIPELINE_LABELS[pipeline]);
    for (const variant of ['accepted', 'hair'] as const) {
      await page.selectOption('#variant-select', variant);
      expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png')))
        .toBe(comparison[pipeline][variant === 'accepted' ? 'acceptedPngDataUrl' : 'hairPngDataUrl']);
    }
  }
  expect(await page.evaluate(() => window.speedLabControls.workers.map(worker => ({...worker})))).toEqual(workers);
}

test('pending async download preserves displayed ownership through switch request and Hold; all eight held outputs toggle exactly', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/speed-lab/live.html');
  await page.selectOption('#eyewear-select', 'tom-ford-clear'); await page.selectOption('#pipeline-select', 'async');
  await page.click('#start'); await waitCategory(page, 'async');
  const session = await page.locator('.stage').getAttribute('data-session-id');
  await page.evaluate(() => {window.speedLabControls.armPendingHold = true;});
  await expect.poll(() => page.evaluate(() => window.speedLabControls.pendingObservation)).not.toBeNull();
  await completeHold(page);
  const observation = await page.evaluate(() => window.speedLabControls.pendingObservation!);
  expect(observation.preparedPipeline).toBe('async'); expect(observation.requestedPipeline).toBe('base');
  expect(observation.privateStatsNull).toBe(true); expect(observation.privateExportNull).toBe(true);
  expect(observation.holdRequested).toBe(true); expect(observation.displayedAfter).toBe(observation.displayedBefore);
  expect(observation.heldVariant).toBe('accepted'); await expect(page.locator('#variant-select')).toHaveValue('accepted');
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', 'async');
  await expect(page.locator('#pipeline-select')).toHaveValue('async');
  expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(session);
  expect(Number(await page.locator('.stage').getAttribute('data-frame-sequence'))).toBeGreaterThan(observation.previousSequence);
  const comparison = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as HeldComparison;
  expect(comparison.schema).toBe('ar-speed-lab-comparison-v1'); expect(comparison.selectedPipeline).toBe('async');
  expect(comparison.candidateAccepted).toBe(false);
  for (const pipeline of PIPELINES) {
    expect(comparison[pipeline].pair).toEqual(comparison.base.pair);
    expect(comparison[pipeline].detection).toEqual(comparison.base.detection);
    expect(comparison[pipeline].sourcePngDataUrl).toBe(comparison.base.sourcePngDataUrl);
    expect(comparison[pipeline].mask).toEqual(comparison.base.mask);
    expect(comparison[pipeline].mask.outputMode).toBe('full'); expect(comparison[pipeline].stats.hasMask).toBe(true);
  }
  await inspectHairToggles(page, comparison); expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('pending-controls-receipt.json'), JSON.stringify({observation, pair: comparison.base.pair,
    exactHairToggles: [...PIPELINES], liveCategoryObserved: ['async'], heldFullUpgradeWhileAccepted: true, errors,
    scope: 'Production bundles, real local workers and GPU calls, synthetic camera; one fence notification delayed by one event-loop turn. Lifecycle coverage, not speed or wearer acceptance.'}, null, 2));
  await page.click('#stop'); await expect.poll(() => resourcesClosed(page)).toBe(true);
});

for (const pipeline of ['async', 'overlap'] as const) test(`stop during ${pipeline} work revokes the old session and allows a fresh masked restart`, async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/speed-lab/live.html');
  await page.selectOption('#pipeline-select', pipeline); await page.click('#start'); await waitCategory(page, pipeline);
  let prefetchToggle: ControlState['prefetchToggle'] = null;
  if (pipeline === 'overlap') {
    await page.evaluate(() => {window.speedLabControls.armPrefetchToggle = true;});
    await expect.poll(() => page.evaluate(() => window.speedLabControls.prefetchToggle)).not.toBeNull();
    prefetchToggle = await page.evaluate(() => window.speedLabControls.prefetchToggle!);
    const prefetchedSequence = prefetchToggle.sequence;
    await expect.poll(() => page.evaluate(sequence => window.arPerformanceProfiler.samplesAfter(0)
      .some(row => row.pipeline === 'overlap' && row.sequence === sequence), prefetchedSequence)).toBe(true);
    const packet = await page.evaluate(sequence => window.arPerformanceProfiler.samplesAfter(0)
      .find(row => row.pipeline === 'overlap' && row.sequence === sequence)!, prefetchedSequence);
    expect(packet.variant).toBe('accepted');
    await expect(page.locator('#variant-select')).toHaveValue('accepted');
    await expect(page.locator('.stage')).toHaveAttribute('data-variant', 'accepted');
    await page.selectOption('#variant-select', 'hair'); await waitCategory(page, pipeline);
  }
  await page.evaluate(mode => {
    window.speedLabControls.armAsyncStop = mode === 'async'; window.speedLabControls.armOverlapStop = mode === 'overlap';
  }, pipeline);
  await expect.poll(() => page.evaluate(() => window.speedLabControls.stopObservation)).not.toBeNull();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect.poll(() => resourcesClosed(page)).toBe(true);
  const observation = await page.evaluate(() => window.speedLabControls.stopObservation!);
  expect(observation.pipeline).toBe(pipeline); expect(observation.processing).toBe(true); expect(observation.privateExportNull).toBe(true);
  expect(observation.trigger).toBe(pipeline === 'async' ? 'native-fence' : 'worker-result');
  if (pipeline === 'async') expect(observation.privateStatsNull).toBe(true);
  expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
  const oldCounts = await page.evaluate(() => ({workers: window.speedLabControls.workers.length, streams: window.speedLabControls.streams.length}));
  await page.selectOption('#pipeline-select', 'combined'); await page.click('#start'); await waitCategory(page, 'combined');
  const freshSession = await page.locator('.stage').getAttribute('data-session-id');
  expect(freshSession).not.toBe(observation.sessionId);
  const newRows = await page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial), observation.lastSerial);
  expect(newRows.length).toBeGreaterThan(0); expect(newRows.every(row => row.sessionId === freshSession)).toBe(true);
  expect(await page.evaluate(counts => window.speedLabControls.workers.slice(0, counts.workers).every(worker => worker.terminated)
    && window.speedLabControls.streams.slice(0, counts.streams).every(stream => stream.getTracks().every(track => track.readyState === 'ended')), oldCounts)).toBe(true);
  await page.click('#stop'); await expect.poll(() => resourcesClosed(page)).toBe(true); expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('stop-restart-controls-receipt.json'), JSON.stringify({observation, prefetchToggle, freshSession,
    liveCategoryObserved: [pipeline, 'combined'], oldSessionsPublishedAfterStop: false, resourcesClosed: true, errors,
    scope: 'Production lifecycle with synthetic camera. Stop is triggered inside a real pending GPU fence or worker result; no speed or wearer claim.'}, null, 2));
});

test('reversed combined-versus-base measurement completes automatically and exports ordered summaries with factual coverage', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/speed-lab/live.html');
  await page.selectOption('#pipeline-select', 'combined'); await page.selectOption('#benchmark-order', 'experiment-first');
  await page.click('#start'); await waitCategory(page, 'combined');
  await expect(page.locator('#benchmark')).toBeEnabled(); await page.click('#benchmark');
  await expect(page.locator('#benchmark-status')).toContainText('Measurement complete', {timeout: 150_000});
  await expect(page.locator('#benchmark-results tr')).toHaveCount(2);
  await expect(page.locator('#benchmark-comparison')).toBeVisible();
  await expect(page.locator('#benchmark-results tr').nth(0)).toHaveAttribute('data-pipeline', 'combined');
  await expect(page.locator('#benchmark-results tr').nth(1)).toHaveAttribute('data-pipeline', 'base');
  await expect(page.locator('#pipeline-select')).toHaveValue('base');
  await expect(page.locator('#benchmark-coverage-policy')).toContainText('100%');
  // Completion persists after camera closure; stop before export to make the
  // fallback text and downloaded samples a stable, exactly comparable record.
  await page.click('#stop'); await expect.poll(() => resourcesClosed(page)).toBe(true);
  const event = page.waitForEvent('download'); await page.click('#download-metrics');
  const outputPath = test.info().outputPath('reversed-benchmark-timings.json'); await (await event).saveAs(outputPath);
  const json = await readFile(outputPath, 'utf8'); expect(await page.locator('#metrics-json').inputValue()).toBe(json);
  interface Segment {pipeline: Pipeline; warmupStartedAtMs: number; measureStartedAtMs: number; endedAtMs: number;
    validWarmupFrames: number; samples: FrameSample[]; summary: ProfileSummary; coverage: WorkCoverage;}
  const exported = JSON.parse(json) as {schema: string; coveragePolicy: string; benchmark: {completed: boolean;
    cancelledReason: string | null; order: Pipeline[]; warmupMs: number; measureMs: number; segments: Segment[]}};
  expect(exported.schema).toBe('ar-speed-lab-performance-v1'); expect(exported.benchmark.completed).toBe(true);
  expect(exported.benchmark.cancelledReason).toBeNull(); expect(exported.benchmark.order).toEqual(['combined', 'base']);
  expect(exported.benchmark.warmupMs).toBe(5000); expect(exported.benchmark.measureMs).toBe(30000);
  expect(exported.benchmark.segments).toHaveLength(2);
  expect(exported.coveragePolicy).toContain('100%'); expect(json).not.toMatch(/sourcePng|landmarks|categoryBase64|sourceSHA256|detectionSHA256/);
  for (const segment of exported.benchmark.segments) {
    expect(segment.validWarmupFrames).toBeGreaterThanOrEqual(3);
    expect(segment.measureStartedAtMs - segment.warmupStartedAtMs).toBeGreaterThanOrEqual(5000);
    expect(segment.endedAtMs - segment.measureStartedAtMs).toBeGreaterThanOrEqual(30000);
    expect(segment.samples.length).toBeGreaterThan(1);
    expect(segment.samples.every(row => row.pipeline === segment.pipeline && row.variant === 'hair')).toBe(true);
    const tracked = segment.samples.filter(row => row.hasFace).length;
    const maskedTracked = segment.samples.filter(row => row.hasFace && row.hasMask).length;
    const trackedFraction = tracked / segment.samples.length, maskFraction = tracked ? maskedTracked / tracked : null;
    expect(segment.summary.frames).toBe(segment.samples.length); expect(segment.summary.trackedFrames).toBe(tracked);
    expect(segment.coverage.trackedFraction).toBe(trackedFraction); expect(segment.coverage.maskedTrackedFraction).toBe(maskFraction);
    expect(segment.coverage.status).toBe(!tracked ? 'no-tracking' : trackedFraction === 1 && maskFraction === 1 ? 'full' : 'partial');
    const row = page.locator(`#benchmark-results tr[data-pipeline="${segment.pipeline}"]`);
    await expect(row).toHaveAttribute('data-coverage', segment.coverage.status);
    await expect(row.locator('[data-metric="fps"]')).toHaveText(segment.summary.processedFps!.toFixed(1));
    await expect(row.locator('[data-metric="intervalP95"]')).toHaveText(`${segment.summary.frameInterval!.p95.toFixed(1)} ms`);
    await expect(row.locator('[data-metric="tracked"]')).toHaveText(`${(trackedFraction * 100).toFixed(1)}%`);
    await expect(row.locator('[data-metric="maskedTracked"]')).toHaveText(maskFraction === null ? '—' : `${(maskFraction * 100).toFixed(1)}%`);
  }
  await expect(page.locator('#metrics-export')).toHaveAttribute('open', '');
  await expect(page.locator('#metrics-json')).toHaveAttribute('readonly', ''); expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('reversed-benchmark-controls-receipt.json'), JSON.stringify({order: exported.benchmark.order,
    completed: true, coverage: exported.benchmark.segments.map(segment => ({pipeline: segment.pipeline, coverage: segment.coverage})),
    orderedTableAndJSON: true, sameFallbackAndDownload: true, errors,
    scope: 'Actual 5-second warmup and 30-second measured segments on a synthetic camera. Validates UI, automatic transitions, coverage and export; not a wearer or speed comparison claim.'}, null, 2));
});

test('failed full held-mask upgrade preserves the actual live category pixels and unlocks all eight cached comparisons and download', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto('/experiments/speed-lab/live.html');
  await page.selectOption('#pipeline-select', 'copies'); await page.click('#start'); await waitCategory(page, 'copies');
  await page.evaluate(() => {window.speedLabControls.armCategoryHold = true;});
  await expect.poll(() => page.evaluate(() => window.speedLabControls.beforeFailedUpgrade)).not.toBeNull();
  await completeHold(page);
  const result = await page.evaluate(() => ({before: window.speedLabControls.beforeFailedUpgrade!,
    failures: window.speedLabControls.failedHeldSegments, locked: window.speedLabControls.upgradeWasLocked,
    privateCache: window.speedLabControls.upgradeExportWasNull, after: window.hairLivePreview.exportDiagnostic()}));
  const comparison = result.after as HeldComparison, before = result.before;
  expect(result.failures).toBe(1); expect(result.locked).toBe(true); expect(result.privateCache).toBe(true);
  expect(comparison.schema).toBe('ar-speed-lab-comparison-v1'); expect(comparison.selectedPipeline).toBe('copies');
  const categorySHA256 = createHash('sha256').update(Buffer.from(before.categoryBase64, 'base64')).digest('hex');
  for (const pipeline of PIPELINES) {
    const output = comparison[pipeline];
    expect(output.stats.hasMask).toBe(true); expect(output.mask.outputMode).toBe('category-only');
    expect(output.pair.sourceSHA256).toBe(before.sourceSHA256); expect(output.pair.detectionSHA256).toBe(before.detectionSHA256);
    expect(output.mask.categoryBase64).toBe(before.categoryBase64); expect(output.mask.categorySHA256).toBe(categorySHA256);
    expect(output.mask.confidenceBase64).toBeNull(); expect(output.mask.confidenceSHA256).toBeNull();
    expect(output.mask).toEqual(comparison.base.mask); expect(output.sourcePngDataUrl).toBe(comparison.base.sourcePngDataUrl);
    expect(output.acceptedPngDataUrl).toBe(before.acceptedPngDataUrl); expect(output.hairPngDataUrl).toBe(before.hairPngDataUrl);
  }
  await expect(page.locator('#hair-status')).toContainText('Extra diagnostic data is unavailable');
  await inspectHairToggles(page, comparison);
  const event = page.waitForEvent('download'); await page.click('#download-diagnostic');
  const outputPath = test.info().outputPath('retained-category-comparison.json'); await (await event).saveAs(outputPath);
  const saved = JSON.parse(await readFile(outputPath, 'utf8')) as HeldComparison;
  expect(saved.schema).toBe('ar-speed-lab-comparison-v1');
  for (const pipeline of PIPELINES) for (const field of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
    expect(saved[pipeline][field]).toEqual(comparison[pipeline][field]);
  await expect(page.locator('#download-diagnostic')).toBeEnabled(); expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('failed-upgrade-controls-receipt.json'), JSON.stringify({pair: comparison.base.pair,
    categorySHA256, retainedLivePixelsAndMask: true, liveCategoryObserved: ['copies'], exactHairToggles: [...PIPELINES],
    controlsLockedDuringUpgrade: true, togglesAndDownloadEnabled: true, resourcesClosed: true, errors,
    scope: 'Production bundles and real local workers on a synthetic camera; one injected full-mask segment error, no speed or wearer claim.'}, null, 2));
  await page.click('#stop'); await expect.poll(() => resourcesClosed(page)).toBe(true);
});
