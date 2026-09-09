import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

interface CameraState {blank: boolean; streams: MediaStream[]; workers: {url: string; terminated: boolean; requests: number}[];}
interface Diagnostic {
  pair: {sourceSHA256: string; detectionSHA256: string; eyewearModel: string};
  sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  detection: unknown;
  mask: {outputMode: string; sourceSHA256: string; detectionSHA256: string; model: string;
    categorySHA256: string; categoryBase64: string; confidenceSHA256: string; confidenceBase64: string} | null;
  stats: {hasMask: boolean; maskOutputMode: string; maskStatus: string; changedPixels: number};
}
declare global {interface Window {hairIntegrationCamera: CameraState;}}
const sha = (value: Buffer): string => createHash('sha256').update(value).digest('hex');

async function installCamera(page: Page): Promise<void> {
  // Tracked generic fixture: production lifecycle evidence, never a private wearer scan.
  const image = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/integration-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: image}));
  await page.addInitScript(() => {
    const source = document.createElement('canvas'); source.width = 640; source.height = 427;
    const drawing = source.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = reject; });
    image.src = '/integration-camera.jpg';
    const state: CameraState = {blank: false, streams: [], workers: []}; window.hairIntegrationCamera = state;
    const draw = (): void => { drawing.fillStyle = '#163949'; drawing.fillRect(0, 0, source.width, source.height);
      if (!state.blank && image.complete && image.naturalWidth) drawing.drawImage(image, 0, 0, source.width, source.height); };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = source.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly index: number;
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.index = state.workers.length;
        state.workers.push({url: String(url), terminated: false, requests: 0});
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const value = message as {type?: string};
        if (value.type === 'detect' || value.type === 'segment') state.workers[this.index]!.requests++;
        if (Array.isArray(transfer)) super.postMessage(message, transfer); else super.postMessage(message, transfer);
      }
      override terminate(): void { state.workers[this.index]!.terminated = true; super.terminate(); }
    }
    window.Worker = ObservedWorker;
  });
}
async function closed(page: Page): Promise<boolean> {
  return page.evaluate(() => window.hairIntegrationCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
    && window.hairIntegrationCamera.workers.every(worker => worker.terminated));
}
async function ready(page: Page): Promise<void> {
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('.stage')).toHaveAttribute('data-hair-status', 'ready');
  await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().stats)).toMatchObject({hasMask: true, maskOutputMode: 'category-only'});
}

for (const selection of [{eyewear: 'tom-ford-clear', model: 'hair-only'}, {eyewear: 'amber-horizon', model: 'selfie-multiclass'}]) {
  test(`production default ${selection.model}: actual workers, owned hold, references and fresh restart`, async ({page, request}) => {
    const errors: string[] = [], missing: string[] = [], requested: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.context().on('response', response => { if (response.status() >= 400) missing.push(`${response.status()} ${response.url()}`); });
    page.context().on('request', event => requested.push(event.url()));
    await installCamera(page);
    const response = await page.goto('/');
    await expect(page).toHaveURL(/\/experiments\/hair-live-preview\/live\.html$/);
    expect(response?.status()).toBe(200); expect(await page.evaluate(() => crossOriginIsolated)).toBe(true);
    const reference = await request.get('/index.html'); expect(reference.status()).toBe(200);
    expect(reference.url()).toMatch(/\/index\.html$/);
    const temples = await request.get('/experiments/temple-sagittal/live.html'); expect(temples.status()).toBe(200);
    expect(await page.locator('#eyewear-select option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value).sort()))
      .toEqual(['amber-horizon', 'tom-ford-clear']);
    await page.selectOption('#eyewear-select', selection.eyewear); await page.selectOption('#hair-model-select', selection.model);
    await page.click('#start'); await ready(page);
    await expect(page.locator('#eyewear-select')).toBeDisabled(); await expect(page.locator('#hair-model-select')).toBeDisabled();
    await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
    await expect.poll(() => closed(page)).toBe(true);
    const owned = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as Diagnostic | null;
    expect(owned).not.toBeNull(); const diagnostic = owned!;
    expect(diagnostic.stats).toMatchObject({hasMask: true, maskStatus: 'ready', maskOutputMode: 'full'});
    expect(diagnostic.mask).not.toBeNull(); expect(diagnostic.mask!.outputMode).toBe('full'); expect(diagnostic.mask!.model).toBe(selection.model);
    expect(diagnostic.pair.eyewearModel).toBe(selection.eyewear);
    expect(diagnostic.pair.detectionSHA256).toBe(sha(Buffer.from(JSON.stringify(diagnostic.detection))));
    expect(diagnostic.mask!.sourceSHA256).toBe(diagnostic.pair.sourceSHA256);
    expect(diagnostic.mask!.detectionSHA256).toBe(diagnostic.pair.detectionSHA256);
    expect(sha(Buffer.from(diagnostic.mask!.categoryBase64, 'base64'))).toBe(diagnostic.mask!.categorySHA256);
    expect(sha(Buffer.from(diagnostic.mask!.confidenceBase64, 'base64'))).toBe(diagnostic.mask!.confidenceSHA256);
    const toggled = await page.evaluate(async source => {
      const image = new Image(); image.src = source; await image.decode(); const decoded = document.createElement('canvas');
      decoded.width = image.naturalWidth; decoded.height = image.naturalHeight; const drawing = decoded.getContext('2d', {willReadFrequently: true})!;
      drawing.drawImage(image, 0, 0); const data = drawing.getImageData(0, 0, decoded.width, decoded.height).data;
      const sourceHash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), byte => byte.toString(16).padStart(2, '0')).join('');
      const select = document.querySelector<HTMLSelectElement>('#variant-select')!, canvas = document.querySelector<HTMLCanvasElement>('#mirror')!;
      const show = (value: string): string => { select.value = value; select.dispatchEvent(new Event('change')); return canvas.toDataURL('image/png'); };
      const accepted = show('accepted'), hair = show('hair'), acceptedAgain = show('accepted'); show('hair');
      return {sourceHash, accepted, hair, acceptedAgain};
    }, diagnostic.sourcePngDataUrl);
    expect(toggled.sourceHash).toBe(diagnostic.pair.sourceSHA256);
    expect(toggled.accepted).toBe(diagnostic.acceptedPngDataUrl); expect(toggled.acceptedAgain).toBe(toggled.accepted);
    expect(toggled.hair).toBe(diagnostic.hairPngDataUrl);
    const downloadEvent = page.waitForEvent('download'); await page.click('#download-diagnostic'); const download = await downloadEvent;
    await download.saveAs(test.info().outputPath('owned-held-diagnostic.json'));
    await page.screenshot({path: test.info().outputPath('production-held.png'), fullPage: true});
    const session = await page.locator('.stage').getAttribute('data-session-id');
    await page.click('#resume-live'); await ready(page);
    expect(await page.locator('.stage').getAttribute('data-session-id')).not.toBe(session);
    await page.evaluate(() => { window.hairIntegrationCamera.blank = true; });
    await expect(page.locator('.stage')).toHaveAttribute('data-state', 'searching');
    expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
    await page.click('#stop'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
    await expect.poll(() => closed(page)).toBe(true);
    expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
    expect(errors).toEqual([]); expect(missing).toEqual([]);
    expect(requested.some(url => /\.tflite/.test(url))).toBe(true);
    expect(requested.some(url => /\.wasm/.test(url))).toBe(true);
    expect(requested.filter(url => /\/(?:\.recovery|@fs|node_modules|src)\//.test(new URL(url).pathname))).toEqual([]);
    await writeFile(test.info().outputPath('integration-receipt.json'), JSON.stringify({selection, exactHeldPair: diagnostic.pair,
      complete: true, realCategoryLiveAndFullHeld: true, acceptedHairToggleExact: true, resourcesClosed: true,
      defaultAndReferencesAvailable: true, errors, missing, requested,
      evidence: 'Production bundles and actual local workers, tracked synthetic camera fixture, fresh browser. Not recorded wearer/moving-hair or speed evidence.'}, null, 2));
  });
}
