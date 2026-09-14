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
type Comparison = Record<typeof PIPELINES[number],Output> & {selectedPipeline:string;candidateAccepted:boolean;
  currentBase:string;ownerSelectedG:boolean;previousBase:string;};

const PIPELINES=['base','source','fresh','warm','copies','async','overlap','combined'] as const;
async function openPreview(page:Page):Promise<void>{
  await page.goto(process.env.COMBINED_BASE_QA === '1' ? '/' : '/experiments/speed-lab/live.html');
  await expect(page.locator('#pipeline-select')).toHaveValue('combined');
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline','combined');
  await expect(page.locator('#active-pipeline')).toHaveText('Current · G Combined');
  await expect(page.locator('#pipeline-select option[value="base"]')).toHaveText('Previous base · Test 2');
  await expect(page.locator('#pipeline-select option')).toHaveCount(8);
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

async function collect(page: Page, pipeline: typeof PIPELINES[number]): Promise<Record<string, unknown>[]> {
  await page.selectOption('#pipeline-select', pipeline);
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
  const rows: Record<string, unknown>[] = [];
  let last = -1;
  for (let index = 0; index < 6; index++) {
    let row: Record<string, unknown> = {};
    await expect.poll(async () => {
      row = await page.evaluate(() => window.hairLivePreview.diagnostics());
      return row.stats && row.pipeline === pipeline ? (row.presented as {sequence: number}).sequence : -1;
    }).toBeGreaterThan(last);
    last = (row.presented as {sequence: number}).sequence;
    if (row.pipeline === pipeline && index >= 1) rows.push(row);
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
    const firstPublication = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0)[0]!);
    expect(firstPublication.pipeline).toBe('combined');
    expect(firstPublication.native?.['pump.mode']).toBe('overlap');
    await expect(page.locator('.stage')).toHaveAttribute('data-pipeline','combined');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().hairReady)).toBe(true);
    const sessionId = await page.locator('.stage').getAttribute('data-session-id');
    const collected:Record<string,Record<string,unknown>[]>= {};
    for(const profile of PIPELINES)collected[profile]=await collect(page,profile);
    expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(sessionId);
    for(const profile of PIPELINES)expect(collected[profile]!.some(row=>(row.stats as Output['stats'])?.hasMask),profile+' must have an actual live mask').toBe(true);
    await page.click('#hold-frame');
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
    await expect.poll(() => closed(page)).toBe(true);
    const diagnostic = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as Comparison;
    expect(diagnostic).not.toBeNull(); expect(diagnostic.candidateAccepted).toBe(false);
    expect(diagnostic.currentBase).toBe('combined'); expect(diagnostic.ownerSelectedG).toBe(true); expect(diagnostic.previousBase).toBe('base');
    for(const profile of PIPELINES){
      expect(diagnostic[profile].pair).toEqual(diagnostic.base.pair);
      expect(diagnostic[profile].sourcePngDataUrl).toBe(diagnostic.base.sourcePngDataUrl);
      expect(diagnostic[profile].mask).toEqual(diagnostic.base.mask);
      expect(diagnostic[profile].acceptedPngDataUrl).toBe(diagnostic.base.acceptedPngDataUrl);
      expect(diagnostic[profile].hairPngDataUrl).toBe(diagnostic.base.hairPngDataUrl);
      for(const key of ['surfacePositions','eyewearMatrix','protection'] as const)
        expect(diagnostic[profile].captureSnapshot[key]).toEqual(diagnostic.base.captureSnapshot[key]);
      await page.selectOption('#pipeline-select',profile);
      for(const variant of ['accepted','hair'] as const){
        await page.selectOption('#variant-select',variant);
        expect(await page.locator('#mirror').evaluate(canvas=>(canvas as HTMLCanvasElement).toDataURL('image/png')))
          .toBe(diagnostic[profile][variant==='hair'?'hairPngDataUrl':'acceptedPngDataUrl']);
      }
    }
    const event = page.waitForEvent('download'); await page.click('#download-diagnostic');
    await (await event).saveAs(test.info().outputPath('held-comparison.json'));
    await page.evaluate(()=>window.scrollTo(0,0));
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
    const value=(row:typeof metrics.samples[number],suffix:string)=>Object.entries(row.native??{}).find(([key])=>key===suffix||key.endsWith('.'+suffix))?.[1];
    for(const pipeline of ['source','combined'])expect(metrics.samples.some(row=>row.pipeline===pipeline&&value(row,'reuseSourcePixelsUsed')===true)).toBe(true);
    for(const pipeline of ['async','combined'])expect(metrics.samples.some(row=>row.pipeline===pipeline&&value(row,'asyncReadbackUsed')===true)).toBe(true);
    for(const pipeline of ['copies','combined'])expect(metrics.samples.some(row=>row.pipeline===pipeline&&value(row,'sourceCanvasBorrowed')===true)).toBe(true);
    expect(metrics.samples.some(row=>row.pipeline==='warm'&&value(row,'prewarmCompleted')===true)).toBe(true);
    for(const row of metrics.samples){const retrieved=value(row,'pbo.retrievedCalls');if(typeof retrieved==='number')expect(value(row,'cpuReadbackCalls')).toBeGreaterThanOrEqual(retrieved);}
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
    await writeFile(test.info().outputPath('receipt.json'), JSON.stringify({eyewear, hair, firstPublication, collected,metrics,
      exactHeldImageAndMask: true, pixelsEqual: true, geometryEqual: true, stoppedAndRestarted: true, errors, missing,
      scope: 'Production browser, synthetic camera, short warm samples; the actual renderer/delegate is recorded per sample. Not physical-camera or wearer acceptance.'}, null, 2));
  });
}

test('cancel startup and restart without stale workers or images', async ({page}) => {
  await installCamera(page); await openPreview(page);
  await page.click('#start'); await page.click('#stop');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect.poll(() => closed(page)).toBe(true);
  await page.selectOption('#pipeline-select', 'combined'); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', 'combined');
  await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true);
});
