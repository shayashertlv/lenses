import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';

interface CameraState {blank: boolean; streams: MediaStream[]; workers: {terminated: boolean; requests: number}[];}
declare global {interface Window {performanceCamera: CameraState;}}
interface Output {
  pair: {sourceSHA256: string; detectionSHA256: string; eyewearModel: string};
  sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  mask: {categorySHA256: string; confidenceSHA256: string; model: string; outputMode: string};
  stats: {hasMask: boolean; changedPixels: number};
  captureSnapshot: {surfacePositions: number[]; eyewearMatrix: number[]; protection: unknown};
}
interface Comparison {current: Output; test: Output; test2: Output; selectedPipeline: string; candidateAccepted: boolean;}

const speedBase = process.env.SPEED_BASE_QA === '1';
async function openPreview(page: Page): Promise<void> {
  const response = await page.goto(speedBase ? '/' : '/experiments/performance-stage2/live.html');
  if (!speedBase) return;
  await expect(page).toHaveURL(/\/experiments\/performance-stage2\/live\.html$/);
  expect(response?.request().redirectedFrom()?.url()).toBe(`${new URL(page.url()).origin}/`);
  expect(response?.status()).toBe(200);
  expect(response?.headers()['cross-origin-opener-policy']).toBe('same-origin');
  expect(response?.headers()['cross-origin-embedder-policy']).toBe('require-corp');
  await expect(page.locator('#pipeline-select')).toHaveValue('test2');
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', 'test2');
  await expect.poll(() => page.evaluate(() => window.hairLivePreview?.diagnostics())).toMatchObject({
    state: 'idle', pipeline: 'test2', requestedPipeline: 'test2', sessionId: null, presented: null,
  });
  expect(await page.evaluate(() => ({streams: window.performanceCamera.streams.length,
    workers: window.performanceCamera.workers.length}))).toEqual({streams: 0, workers: 0});
  for (const [path, title] of [
    ['/index.html', 'Lenses — AR v4'],
    ['/experiments/temple-sagittal/live.html', 'Lenses — Perfecto / Rear-temple candidate'],
    ['/experiments/hair-live-preview/live.html', 'Lenses · Long hair mirror'],
    ['/experiments/performance-candidate/live.html', 'Lenses · Performance comparison'],
    ['/experiments/performance-stage3/live.html', 'Lenses · Test 3 · GPU hair comparison'],
  ]) {
    const reference = await page.request.get(path!, {maxRedirects: 0});
    expect(reference.status(), `${path} must remain a real production entry.`).toBe(200);
    expect(reference.headers()['content-type']).toContain('text/html');
    expect(await reference.text()).toContain(`<title>${title}</title>`);
  }
}

async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/performance-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const source = document.createElement('canvas'); source.width = 640; source.height = 427;
    const context = source.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/performance-camera.jpg';
    const state: CameraState = {blank: false, streams: [], workers: []}; window.performanceCamera = state;
    const draw = (): void => {context.fillStyle = '#163949'; context.fillRect(0, 0, source.width, source.height);
      if (!state.blank && image.complete && image.naturalWidth) context.drawImage(image, 0, 0, source.width, source.height);};
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = source.captureStream(30); state.streams.push(stream); return stream;
    }});
    const Native = window.Worker;
    class ObservedWorker extends Native {
      private readonly index: number;
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.index = state.workers.length; state.workers.push({terminated: false, requests: 0});
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        if (['detect', 'segment'].includes((message as {type: string}).type)) state.workers[this.index]!.requests++;
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void {state.workers[this.index]!.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  });
}
const closed = (page: Page): Promise<boolean> => page.evaluate(() => window.performanceCamera.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))
  && window.performanceCamera.workers.every(w => w.terminated));

async function collect(page: Page, pipeline: 'current' | 'test' | 'test2'): Promise<Record<string, unknown>[]> {
  await page.selectOption('#pipeline-select', pipeline);
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
  const rows: Record<string, unknown>[] = [];
  let last = -1;
  for (let index = 0; index < 16; index++) {
    let row: Record<string, unknown> = {};
    await expect.poll(async () => {
      row = await page.evaluate(() => window.hairLivePreview.diagnostics());
      return row.stats && row.pipeline === pipeline ? (row.presented as {sequence: number}).sequence : -1;
    }).toBeGreaterThan(last);
    last = (row.presented as {sequence: number}).sequence;
    if (row.pipeline === pipeline && index >= 3) rows.push(row);
  }
  return rows;
}

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass']) {
  test(`${eyewear}/${hair}: live switch, matched hold, stop and fresh restart`, async ({page}) => {
    const errors: string[] = [], missing: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {if (response.status() >= 400) missing.push(`${response.status()} ${response.url()}`);});
    await installCamera(page);
    await openPreview(page);
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start');
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().hairReady)).toBe(true);
    if (speedBase) await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics())).toMatchObject({
      pipeline: 'test2', presented: {pipeline: 'test2'},
    });
    const sessionId = await page.locator('.stage').getAttribute('data-session-id');
    const first = await collect(page, 'current'), candidate = await collect(page, 'test'), stage2 = await collect(page, 'test2');
    expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(sessionId);
    expect(first.length).toBeGreaterThan(5); expect(candidate.length).toBeGreaterThan(5);
    for (const rows of [first, candidate, stage2]) expect(rows.some(row => {
      const stats = row.stats as Output['stats'] & {maskOutputMode: string};
      return stats.hasMask && stats.maskOutputMode === 'category-only';
    }), 'The live pipeline must apply a valid same-frame hair mask before Hold.').toBe(true);
    await page.click('#hold-frame');
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
    await expect.poll(() => closed(page)).toBe(true);
    const diagnostic = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as Comparison;
    expect(diagnostic).not.toBeNull(); expect(diagnostic.candidateAccepted).toBe(false);
    expect(diagnostic.current.pair).toEqual(diagnostic.test.pair);
    expect(diagnostic.current.sourcePngDataUrl).toBe(diagnostic.test.sourcePngDataUrl);
    expect(diagnostic.current.mask.categorySHA256).toBe(diagnostic.test.mask.categorySHA256);
    expect(diagnostic.current.mask.confidenceSHA256).toBe(diagnostic.test.mask.confidenceSHA256);
    expect(diagnostic.current.mask.outputMode).toBe('full');
    expect(diagnostic.current.stats.hasMask).toBe(true); expect(diagnostic.test.stats.hasMask).toBe(true);
    expect(diagnostic.current.acceptedPngDataUrl).toBe(diagnostic.test.acceptedPngDataUrl);
    expect(diagnostic.current.hairPngDataUrl).toBe(diagnostic.test.hairPngDataUrl);
    expect(diagnostic.current.pair).toEqual(diagnostic.test2.pair);
    expect(diagnostic.current.sourcePngDataUrl).toBe(diagnostic.test2.sourcePngDataUrl);
    expect(diagnostic.current.mask).toEqual(diagnostic.test2.mask);
    expect(diagnostic.current.acceptedPngDataUrl).toBe(diagnostic.test2.acceptedPngDataUrl);
    expect(diagnostic.current.hairPngDataUrl).toBe(diagnostic.test2.hairPngDataUrl);
    for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
      for(const pipeline of ['test','test2'] as const) expect(diagnostic.current.captureSnapshot[key]).toEqual(diagnostic[pipeline].captureSnapshot[key]);
    for (const pipeline of ['current', 'test', 'test2', 'current', 'test2'] as const) {
      await page.selectOption('#pipeline-select', pipeline);
      expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png'))).toBe(diagnostic[pipeline].hairPngDataUrl);
      expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(sessionId);
    }
    const event = page.waitForEvent('download'); await page.click('#download-diagnostic');
    await (await event).saveAs(test.info().outputPath('held-comparison.json'));
    await page.screenshot({path: test.info().outputPath('held.png'), fullPage: true});
    await page.click('#resume-live'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    expect(await page.locator('.stage').getAttribute('data-session-id')).not.toBe(sessionId);
    await page.evaluate(() => {window.performanceCamera.blank = true;});
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'searching');
    expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
    await page.click('#stop'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
    await expect.poll(() => closed(page)).toBe(true);
    expect(errors).toEqual([]); expect(missing).toEqual([]);
    const metrics = await page.evaluate(() => ({snapshot:window.arPerformanceProfiler.snapshot(), samples:window.arPerformanceProfiler.samplesAfter(0)}));
    expect(metrics.samples.length).toBeGreaterThan(30);
    expect(metrics.samples.every(row=>row.publishedAtMs>=row.capturedAtMs && row.totalMs>=0)).toBe(true);
    expect(metrics.samples.some(row=>row.faceInferenceMs!==null && row.faceInferenceMs>=0)).toBe(true);
    expect(JSON.stringify(metrics)).not.toMatch(/sourcePng|landmarks|categoryBase64/);
    const timingsEvent = page.waitForEvent('download'); await page.click('#download-metrics');
    const timingsPath = test.info().outputPath('timings-only.json');
    await (await timingsEvent).saveAs(timingsPath);
    const fallbackJSON = await page.locator('#metrics-json').inputValue();
    expect(await readFile(timingsPath, 'utf8')).toBe(fallbackJSON);
    expect(JSON.parse(fallbackJSON).samples).toEqual(metrics.samples);
    expect(fallbackJSON).not.toMatch(/sourcePng|landmarks|categoryBase64/);
    await expect(page.locator('#metrics-export')).toHaveAttribute('open', '');
    await expect(page.locator('#metrics-json')).toHaveAttribute('readonly', '');
    await writeFile(test.info().outputPath('receipt.json'), JSON.stringify({eyewear, hair, current: first, test: candidate, test2:stage2,metrics,
      exactHeldImageAndMask: true, pixelsEqual: true, geometryEqual: true, stoppedAndRestarted: true, errors, missing,
      scope: 'Production browser, synthetic camera, short warm samples; the actual renderer/delegate is recorded per sample. Not physical-camera or wearer acceptance.'}, null, 2));
  });
}

test('cancel startup and restart without stale workers or images', async ({page}) => {
  await installCamera(page); await openPreview(page);
  await page.click('#start'); await page.click('#stop');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect.poll(() => closed(page)).toBe(true);
  await page.selectOption('#pipeline-select', 'test2'); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', 'test2');
  await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true);
});
