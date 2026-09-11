import {test, expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {stripTypeScriptTypes} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import type {FrameSample} from '../frame-profiler.ts';
import type {HairModelId} from '../../hair-live-preview/models.ts';
import type {CategoryExtractionMetrics} from './extraction.ts';

const sha = (bytes: Uint8Array | string): string => createHash('sha256').update(bytes).digest('hex');
const workspace = fileURLToPath(new URL('../../../', import.meta.url));
const preview = '/experiments/efficiency-lab/live.html?pipeline=mask&study=mask';
// A module worker loaded by the isolated production page must retain its COEP
// boundary. Playwright's fulfilled responses do not inherit Vite's headers.
const isolationHeaders = {'Cross-Origin-Embedder-Policy': 'require-corp',
  'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Resource-Policy': 'same-origin'};
interface FrozenImage {id: string; width: number; height: number; capture: {path: string; sha256: string; bytes: number};
  attributes: {direction: string}; quality: {forwardElevationDegrees: number; headYawDegrees: number};}
interface MaskComparison {sourceId: string; width: number; height: number; imageWidth: number; imageHeight: number;
  confidenceAbsent: boolean; direct: CategoryExtractionMetrics; sdk: CategoryExtractionMetrics;
  categoryBytes: number; sdkCategoryBytes: number; independentBuffers: boolean; differences: number;
  firstDifferences: number[]; directSHA256: string; sdkSHA256: string; previousBefore: boolean; previousAfter: boolean;
  categoryValues: number[]; comparedAfterResultClose: boolean;}
interface ExtractionCamera {streams: MediaStream[]; workers: {terminated: boolean; readyProtocol: string | null}[];
  requests: {sequence: number; mode: string; sourceSHA256: string; sessionId: string | null; phase: string | null}[];
  armPendingStop: boolean; stopped: {sessionId: string; lastSerial: number} | null;}
declare global {interface Window {extractionCamera: ExtractionCamera;}}
interface HeldOutput {pair: unknown; detection: unknown; mask: unknown; sourcePngDataUrl: string;
  acceptedPngDataUrl: string; hairPngDataUrl: string; captureSnapshot: {surfacePositions: unknown; eyewearMatrix: unknown; protection: unknown};
  stats: {hasMask: boolean; protectedCheck: {changedPixels: number}; noseCheck: {changedPixels: number};
    outsideEditableCheck: {changedPixels: number}; backgroundPreservationCheck: {changedPixels: number}};}

/** No test endpoint is added to the build. Serve an in-memory module graph made
 * from exact local source and the installed SDK, using Node's type stripper. */
async function installDifferentialWorker(page: Page): Promise<Record<string, string>> {
  const definitions = [
    ['worker.mjs', new URL('./extraction-qa.worker.ts', import.meta.url)],
    ['extraction.mjs', new URL('./extraction.ts', import.meta.url)],
    ['models.mjs', new URL('../../hair-live-preview/models.ts', import.meta.url)],
    ['vision_bundle.mjs', new URL('../../../node_modules/@mediapipe/tasks-vision/vision_bundle.mjs', import.meta.url)],
  ] as const;
  const resources = new Map<string, string>(), hashes: Record<string, string> = {};
  for (const [name, url] of definitions) {
    const source = await readFile(url, 'utf8'); hashes[name] = sha(source);
    const body = name === 'vision_bundle.mjs' ? source : stripTypeScriptTypes(source, {mode: 'strip'})
      .replaceAll("'@mediapipe/tasks-vision'", "'./vision_bundle.mjs'")
      .replaceAll("'../../hair-live-preview/models.ts'", "'./models.mjs'")
      .replaceAll("'./extraction.ts'", "'./extraction.mjs'");
    resources.set(name, body);
  }
  await page.route('**/qa-hair-extraction/*', route => {
    const body = resources.get(new URL(route.request().url()).pathname.split('/').at(-1)!);
    return route.fulfill({status: body ? 200 : 404, headers: isolationHeaders,
      contentType: 'application/javascript', body: body ?? 'Missing QA module'});
  });
  return hashes;
}

test('optional frozen real masks: direct extraction exactly matches the installed SDK before cleanup', async ({page}) => {
  test.skip(process.env.AR_HAIR_EXTRACTION_FROZEN !== '1', 'Set AR_HAIR_EXTRACTION_FROZEN=1 to use preserved private angle inputs.');
  const archive = path.join(workspace, '.recovery/hair-angle-review-2026-09-09/runs/2026-09-09T08-18-33.789Z/report.json');
  const reportBytes = await readFile(archive), report = JSON.parse(reportBytes.toString()) as {complete: boolean; images: FrozenImage[]};
  expect(report.complete).toBe(true); expect(report.images).toHaveLength(8);
  const frozen = new Map<string, string>([[archive, sha(reportBytes)]]);
  const resources = new Map<string, Buffer>();
  for (const image of report.images) {
    const filename = path.resolve(workspace, image.capture.path), within = path.relative(path.join(workspace, '.recovery'), filename);
    expect(within.startsWith('..') || path.isAbsolute(within)).toBe(false);
    const bytes = await readFile(filename); expect(sha(bytes)).toBe(image.capture.sha256); expect(bytes.length).toBe(image.capture.bytes);
    frozen.set(filename, sha(bytes)); resources.set(image.id, bytes);
  }
  expect(Math.min(...report.images.map(image => image.quality.forwardElevationDegrees))).toBeLessThan(-20);
  expect(Math.max(...report.images.map(image => image.quality.forwardElevationDegrees))).toBeGreaterThan(15);
  expect(Math.min(...report.images.map(image => image.quality.headYawDegrees))).toBeLessThan(-25);
  expect(Math.max(...report.images.map(image => image.quality.headYawDegrees))).toBeGreaterThan(25);
  await page.route('**/qa-frozen-hair/*', route => {
    const bytes = resources.get(new URL(route.request().url()).pathname.split('/').at(-1)!);
    return route.fulfill({status: bytes ? 200 : 404, headers: isolationHeaders,
      contentType: 'image/png', body: bytes ?? 'Missing frozen source'});
  });
  const modules = await installDifferentialWorker(page);
  await page.goto(preview);
  const runs: {model: HairModelId; delegate: 'GPU' | 'CPU'; initialized: unknown; rows: MaskComparison[]; closed: unknown}[] = [];
  for (const delegate of ['GPU', 'CPU'] as const) for (const model of ['hair-only', 'selfie-multiclass'] as const) {
    const run = await page.evaluate(async ({delegate, model, images}) => {
      const worker = new Worker('/qa-hair-extraction/worker.mjs', {type: 'module'}); let id = 0;
      const send = (message: Record<string, unknown>, transfer: Transferable[] = []): Promise<Record<string, unknown>> =>
        new Promise((resolve, reject) => {
          const currentId = ++id;
          const receive = (event: MessageEvent<Record<string, unknown>>): void => {
            if (event.data.id !== currentId) return;
            worker.removeEventListener('message', receive); worker.removeEventListener('error', fail);
            if (event.data.ok) resolve(event.data); else reject(new Error(String(event.data.message)));
          };
          const fail = (event: ErrorEvent): void => {worker.removeEventListener('message', receive); reject(new Error(event.message));};
          worker.addEventListener('message', receive); worker.addEventListener('error', fail);
          worker.postMessage({...message, id: currentId}, transfer);
        });
      try {
        const initialized = await send({type: 'initialize', model, delegate}); const rows: MaskComparison[] = [];
        for (const image of images) {
          const bitmap = await createImageBitmap(await (await fetch('/qa-frozen-hair/' + image.id)).blob());
          rows.push(await send({type: 'compare', sourceId: image.id, image: bitmap}, [bitmap]) as unknown as MaskComparison);
        }
        const closed = await send({type: 'close'}); return {model, delegate, initialized, rows, closed};
      } finally {worker.terminate();}
    }, {delegate, model, images: report.images.map(({id}) => ({id}))});
    runs.push(run);
    // Preserve a partial receipt before assertions so a mechanism or byte failure
    // is inspectable and is never replaced by a later retry.
    await writeFile(test.info().outputPath(`mask-${delegate}-${model}.json`), JSON.stringify(run, null, 2));
    expect(run.rows).toHaveLength(8);
    for (const row of run.rows) {
      expect(row.width).toBe(1280); expect(row.height).toBe(853);
      expect(row.imageWidth).toBe(row.width); expect(row.imageHeight).toBe(row.height);
      expect(row.categoryBytes).toBe(row.width * row.height); expect(row.sdkCategoryBytes).toBe(row.categoryBytes);
      expect(row.differences).toBe(0); expect(row.firstDifferences).toEqual([]);
      expect(row.directSHA256).toBe(row.sdkSHA256); expect(row.independentBuffers).toBe(true);
      expect(row.previousBefore && row.previousAfter && row.comparedAfterResultClose && row.confidenceAbsent).toBe(true);
      expect(row.direct.mode).toBe('direct'); expect(row.sdk.mode).toBe('sdk'); expect(row.sdk.path).toBe('sdk-copy');
      expect(row.categoryValues.length).toBeGreaterThan(1);
      if (delegate === 'GPU') {
        expect(row.direct.hasWebGLTexture).toBe(true); expect(row.direct.hasUint8).toBe(false);
        expect(row.direct.path).toBe('direct-float-conversion');
        expect(row.direct.explicitFloatTemporaryBytesAvoided).toBe(row.categoryBytes * 4);
        expect(row.direct.explicitCategoryCopyBytesAvoided).toBe(row.categoryBytes);
      }
    }
    expect((run.closed as {lastOutputSurvivesSegmenterClose: boolean}).lastOutputSurvivesSegmenterClose).toBe(true);
  }
  for (const [filename, expected] of frozen) expect(sha(await readFile(filename)), filename).toBe(expected);
  await writeFile(test.info().outputPath('frozen-extraction-receipt.json'), JSON.stringify({modules, frozen: Object.fromEntries(frozen), runs,
    sourcePoses: report.images.map(({id, attributes, quality}) => ({id, attributes, quality})),
    exactSameRealMask: true, frozenInputsUnchanged: true,
    scope: '32 real-model mask comparisons: both pinned hair models, GPU and CPU, eight preserved generated images including down/up/both yaw. Direct is called before SDK on the same actual MPMask. SDK therefore sees cached float retrieval: these durations are not a fair speed comparison. This is extraction equality and ownership evidence, not physical-camera motion, anatomical accuracy or new renderer acceptance.'}, null, 2));
});

async function installCamera(page: Page): Promise<void> {
  const fixture = await readFile(new URL('../../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/extraction-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 427;
    const context = canvas.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/extraction-camera.jpg';
    const state: ExtractionCamera = {streams: [], workers: [], requests: [], armPendingStop: false, stopped: null};
    window.extractionCamera = state;
    const draw = (): void => {if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, canvas.width, canvas.height);};
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly observation: {terminated: boolean; readyProtocol: string | null};
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.observation = {terminated: false, readyProtocol: null}; state.workers.push(this.observation);
        this.addEventListener('message', (event: MessageEvent<Record<string, unknown>>) => {
          if (event.data.type === 'ready' && typeof event.data.categoryExtractionProtocol === 'string')
            this.observation.readyProtocol = event.data.categoryExtractionProtocol;
          const output = event.data.output as {outputMode?: string; categoryExtraction?: {mode?: string}} | undefined;
          if (!state.armPendingStop || event.data.type !== 'result' || output?.outputMode !== 'category-only'
            || output.categoryExtraction?.mode !== 'direct') return;
          const diagnostic = window.hairLivePreview.diagnostics();
          if (diagnostic.phase !== 'live' || diagnostic.pipeline !== 'mask' || !diagnostic.presented) return;
          state.armPendingStop = false; state.stopped = {sessionId: diagnostic.sessionId as string,
            lastSerial: window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0};
          event.stopImmediatePropagation(); document.querySelector<HTMLButtonElement>('#stop')!.click();
        });
      }
      override postMessage(message: unknown, transfer: Transferable[] | StructuredSerializeOptions = []): void {
        const value = message as {type?: string; sequence?: number; sourceSHA256?: string; categoryExtractionMode?: string};
        if (value.type === 'segment') {
          const diagnostic = window.hairLivePreview.diagnostics();
          state.requests.push({sequence: value.sequence!, mode: value.categoryExtractionMode!, sourceSHA256: value.sourceSHA256!,
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
  window.extractionCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
  && window.extractionCamera.workers.every(worker => worker.terminated));
async function collect(page: Page, pipeline: 'g' | 'mask'): Promise<FrameSample[]> {
  const after = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0);
  if (await page.locator('#pipeline-select').inputValue() !== pipeline) await page.selectOption('#pipeline-select', pipeline);
  await expect(page.locator('.stage')).toHaveAttribute('data-pipeline', pipeline);
  await expect.poll(() => page.evaluate(({after, pipeline}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.pipeline === pipeline && row.hasMask).length, {after, pipeline})).toBeGreaterThanOrEqual(8);
  return page.evaluate(({after, pipeline}) => window.arPerformanceProfiler.samplesAfter(after).filter(row => row.pipeline === pipeline), {after, pipeline});
}
function checkLive(rows: FrameSample[], pipeline: 'g' | 'mask'): void {
  expect(rows.length).toBeGreaterThanOrEqual(8);
  for (const row of rows) {
    const native = row.native; expect(native).not.toBeNull(); if (!native) throw new Error('Missing actual pipeline metrics.');
    expect(native['admission.rateHz']).toBeNull(); expect(native['pump.mode']).toBe('overlap');
    expect(native['pump.maxOwnedFrames']).toBeLessThanOrEqual(2); expect(native['pump.maxInFlightInference']).toBeLessThanOrEqual(1);
    expect(row.sourceWidth).toBe(640); expect(row.sourceHeight).toBe(427);
    if (!row.hasMask) continue;
    const mode = pipeline === 'mask' ? 'direct' : 'sdk';
    expect(native['hairCategory.requestedMode']).toBe(mode); expect(native['hairCategory.mode']).toBe(mode);
    expect(native['hairCategory.maskPixels']).toBe(640 * 427); expect(native['hairCategory.ownedCategoryBytesAllocated']).toBe(640 * 427);
    for (const key of ['retrievalMs', 'conversionMs', 'copyMs', 'totalMs']) expect(Number(native['hairCategory.' + key])).toBeGreaterThanOrEqual(0);
    expect(native['hairCategory.path']).toBe(mode === 'sdk' ? 'sdk-copy' : native['hairCategory.hasUint8'] ? 'direct-byte-copy' : 'direct-float-conversion');
  }
  if (pipeline === 'mask') expect(rows.some(row => row.native?.['hairCategory.path'] === 'direct-float-conversion')).toBe(true);
}

for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const hair of ['hair-only', 'selfie-multiclass']) {
  test(`${eyewear}/${hair}: P uses real direct extraction, uncapped G switching and exact held safeguards`, async ({page}) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await installCamera(page); await page.goto(preview);
    await expect(page.locator('#pipeline-select')).toHaveValue('mask'); await expect(page.locator('#pipeline-select option')).toHaveCount(2);
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    const sessionId = await page.locator('.stage').getAttribute('data-session-id');
    const first = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0)[0]!); expect(first.pipeline).toBe('mask');
    const collected = {mask: await collect(page, 'mask'), g: await collect(page, 'g'), maskAgain: await collect(page, 'mask')};
    checkLive(collected.mask, 'mask'); checkLive(collected.g, 'g'); checkLive(collected.maskAgain, 'mask');
    expect(await page.locator('.stage').getAttribute('data-session-id')).toBe(sessionId);
    const observations = await page.evaluate(() => window.extractionCamera);
    expect(observations.workers.some(worker => worker.readyProtocol === 'hair-category-extraction-v1')).toBe(true);
    for (const rows of Object.values(collected)) for (const row of rows.filter(row => row.hasMask)) {
      expect(row.eyewearId).toBe(eyewear); expect(row.hairModelId).toBe(hair);
      expect(observations.requests.some(request => request.sequence === row.sequence && request.sessionId === row.sessionId
        && request.mode === (row.pipeline === 'mask' ? 'direct' : 'sdk') && request.phase === 'live')).toBe(true);
    }
    await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
    await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
    await expect.poll(() => closed(page)).toBe(true);
    const comparison = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as unknown as
      {g: HeldOutput; mask: HeldOutput; currentBase: string; candidateAccepted: boolean};
    expect(comparison.currentBase).toBe('g'); expect(comparison.candidateAccepted).toBe(false);
    expect(comparison.mask.stats.hasMask).toBe(true);
    for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
      expect(comparison.mask[key], key).toEqual(comparison.g[key]);
    for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
      expect(comparison.mask.captureSnapshot[key], key).toEqual(comparison.g.captureSnapshot[key]);
    for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
      expect(comparison.mask.stats[key]?.changedPixels, key).toBe(0);
    for (const pipeline of ['g', 'mask'] as const) for (const variant of ['accepted', 'hair'] as const) {
      await page.selectOption('#pipeline-select', pipeline); await page.selectOption('#variant-select', variant);
      expect(await page.locator('#mirror').evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png')))
        .toBe(comparison[pipeline][variant === 'hair' ? 'hairPngDataUrl' : 'acceptedPngDataUrl']);
    }
    await page.click('#resume-live'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
    expect(await page.locator('.stage').getAttribute('data-session-id')).not.toBe(sessionId);
    checkLive(await collect(page, 'mask'), 'mask');
    await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(errors).toEqual([]);
    const download = page.waitForEvent('download'); await page.click('#download-metrics');
    const filename = test.info().outputPath('P-timings.json'); await (await download).saveAs(filename);
    const exported = await readFile(filename, 'utf8'); expect(exported).not.toMatch(/sourcePng|landmarks|categoryBase64|sourceSHA256/);
    expect(JSON.parse(exported).samples.some((row: FrameSample) => row.native?.['hairCategory.mode'] === 'direct')).toBe(true);
    await writeFile(test.info().outputPath('P-live-receipt.json'), JSON.stringify({eyewear, hair, first, collected,
      exactHeldPixelsMaskDetectionGeometry: true, safeguards: true, protocolAcknowledged: true, errors,
      scope: 'Production preview, real local face/hair workers and static 640x427 synthetic camera. Both extraction modes are observed on owned live requests. P uses unchanged G rendering; held cache equality and nose/front/outside-arm guards are verified. New moving-wearer visual acceptance and speed are unmeasured.'}, null, 2));
  });
}

test('P startup cancellation and pending direct result stop revoke ownership before a fresh G session', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await installCamera(page); await page.goto(preview);
  await page.click('#start'); await page.click('#stop');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle'); await expect.poll(() => closed(page)).toBe(true);
  await page.click('#start'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  checkLive(await collect(page, 'mask'), 'mask');
  await page.evaluate(() => {window.extractionCamera.armPendingStop = true;});
  await expect.poll(() => page.evaluate(() => window.extractionCamera.stopped)).not.toBeNull();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle'); await expect.poll(() => closed(page)).toBe(true);
  const stopped = await page.evaluate(() => window.extractionCamera.stopped!);
  expect(await page.evaluate(() => window.hairLivePreview.exportDiagnostic())).toBeNull();
  await page.selectOption('#pipeline-select', 'g'); await page.click('#start');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking'); checkLive(await collect(page, 'g'), 'g');
  const rows = await page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial), stopped.lastSerial);
  expect(rows.every(row => row.sessionId !== stopped.sessionId && row.pipeline === 'g')).toBe(true);
  await page.click('#stop'); await expect.poll(() => closed(page)).toBe(true); expect(errors).toEqual([]);
  await writeFile(test.info().outputPath('P-cancel-receipt.json'), JSON.stringify({stopped, rows, noLatePublication: true, errors}, null, 2));
});
