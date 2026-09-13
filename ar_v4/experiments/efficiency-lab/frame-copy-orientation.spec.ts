import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import type {FrameSample} from './frame-profiler.ts';
import type {} from './live-main.ts';

type OrientationCase = 'missing-metadata' | 'rotated-storage';
type Choice = 'g' | 'frame-copy' | 'mask-bytes';
interface OrientationObservation {
  streams: MediaStream[]; workers: {terminated: boolean}[];
  frameConstructions: number; frameCloses: number; copyCalls: number;
}
declare global {interface Window {frameOrientationObservation: OrientationObservation;}}
interface HeldOutput {
  pair: unknown; detection: unknown; mask: unknown;
  sourcePngDataUrl: string; acceptedPngDataUrl: string; hairPngDataUrl: string;
  captureSnapshot: {surfacePositions: unknown; eyewearMatrix: unknown; protection: unknown};
  stats: {hasMask: boolean; protectedCheck: {changedPixels: number}; noseCheck: {changedPixels: number};
    outsideEditableCheck: {changedPixels: number}; backgroundPreservationCheck: {changedPixels: number}};
}
const entry = '/ar_testing/experiments/efficiency-lab/live.html?study=fps-review';

/** Native camera transport, workers and renderer with one controlled browser
 * API boundary: VideoFrame orientation is unknown or explicitly rotated. Its
 * poisoned copy would rotate the portrait 90 degrees if the guard let it run.
 * Static fixture evidence cannot establish iPhone throughput or wearer motion. */
async function installCamera(page: Page, scenario: OrientationCase): Promise<void> {
  const fixture = await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/ar_testing/orientation-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(({scenario}) => {
    const state: OrientationObservation = {streams: [], workers: [], frameConstructions: 0, frameCloses: 0, copyCalls: 0};
    window.frameOrientationObservation = state;
    const camera = document.createElement('canvas'); camera.width = 720; camera.height = 1280;
    const context = camera.getContext('2d')!, image = new Image();
    const ready = new Promise<void>((resolve, reject) => {image.onload = () => resolve(); image.onerror = reject;});
    image.src = '/ar_testing/orientation-camera.jpg';
    const draw = (): void => {
      if (!image.complete || !image.naturalWidth) return;
      context.fillStyle = '#808080'; context.fillRect(0, 0, 720, 1280); context.drawImage(image, 0, 400, 720, 480);
      for (const [x, y, color] of [[0, 0, '#e02010'], [640, 0, '#10c040'], [0, 1180, '#3050d0'], [640, 1180, '#e0c020']] as const) {
        context.fillStyle = color; context.fillRect(x, y, 80, 100);
      }
    };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      await ready; draw(); const stream = camera.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeFrame = window.VideoFrame;
    const poison = document.createElement('canvas'); poison.width = 720; poison.height = 1280;
    const poisonContext = poison.getContext('2d', {willReadFrequently: true})!;
    window.VideoFrame = new Proxy(NativeFrame, {construct(target, args) {
      const frame = Reflect.construct(target, args) as VideoFrame, close = frame.close.bind(frame);
      state.frameConstructions++;
      Object.defineProperties(frame, {
        rotation: {value: scenario === 'missing-metadata' ? undefined : 90},
        flip: {value: scenario === 'missing-metadata' ? undefined : false},
        ...(scenario === 'rotated-storage' ? {
          codedWidth: {value: 1280}, codedHeight: {value: 720},
          visibleRect: {value: new DOMRectReadOnly(0, 0, 1280, 720)},
          displayWidth: {value: 720}, displayHeight: {value: 1280},
        } : {}),
        close: {value: () => {state.frameCloses++; close();}},
        copyTo: {value: async (destination: Uint8ClampedArray): Promise<PlaneLayout[]> => {
          state.copyCalls++;
          poisonContext.save(); poisonContext.setTransform(720 / 1280, 0, 0, 1280 / 720, 0, 0);
          poisonContext.translate(1280, 0); poisonContext.rotate(Math.PI / 2);
          poisonContext.drawImage(args[0] as HTMLVideoElement, 0, 0, 720, 1280); poisonContext.restore();
          destination.set(poisonContext.getImageData(0, 0, 720, 1280).data);
          return [{offset: 0, stride: 720 * 4}];
        }},
      });
      return frame;
    }});
    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly record: {terminated: boolean};
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options); this.record = {terminated: false}; state.workers.push(this.record);
      }
      override terminate(): void {this.record.terminated = true; super.terminate();}
    }
    window.Worker = ObservedWorker;
  }, {scenario});
}

async function published(page: Page, pipeline: Choice, after = 0): Promise<FrameSample> {
  await expect.poll(() => page.evaluate(({pipeline, after}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId
      && row.pipeline === pipeline && row.hasFace && row.hasMask).length, {pipeline, after})).toBeGreaterThanOrEqual(4);
  return await page.evaluate(({pipeline, after}) => window.arPerformanceProfiler.samplesAfter(after)
    .filter(row => row.sessionId === window.hairLivePreview.diagnostics().sessionId
      && row.pipeline === pipeline && row.hasFace && row.hasMask).at(-1)!, {pipeline, after});
}
function upright(samples: number[][]): void {
  expect(samples).toHaveLength(4);
  const [red, green, blue, yellow] = samples as [number[], number[], number[], number[]];
  expect(red[0]!).toBeGreaterThan(red[1]! + 100);
  expect(green[1]!).toBeGreaterThan(green[0]! + 100);
  expect(blue[2]!).toBeGreaterThan(blue[0]! + 100);
  expect(yellow[0]!).toBeGreaterThan(yellow[2]! + 100);
  expect(yellow[1]!).toBeGreaterThan(yellow[2]! + 100);
  for (const pixel of samples) expect(pixel[3]).toBe(255);
}
async function displayPixels(page: Page): Promise<number[][]> {
  return await page.locator('#mirror').evaluate(element => {
    const canvas = element as HTMLCanvasElement, context = canvas.getContext('2d')!;
    if (canvas.width !== 720 || canvas.height !== 1280) throw new Error(`Unexpected mirror dimensions ${canvas.width} x ${canvas.height}`);
    return [[40, 50], [680, 50], [40, 1230], [680, 1230]]
      .map(([x, y]) => Array.from(context.getImageData(x!, y!, 1, 1).data));
  });
}
async function cleanup(page: Page): Promise<void> {
  if (await page.locator('#stop').isVisible()) await page.click('#stop');
  await expect.poll(() => page.evaluate(() => {
    const state = window.frameOrientationObservation;
    return state.workers.every(worker => worker.terminated)
      && state.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
      && state.frameCloses === state.frameConstructions;
  })).toBe(true);
}
async function heldPair(page: Page): Promise<unknown> {
  await page.click('#hold-frame'); await expect(page.locator('.stage')).toHaveAttribute('data-state', 'held');
  await expect.poll(() => page.evaluate(() => window.hairLivePreview.diagnostics().heldBusy)).toBe(false);
  const report = await page.evaluate(() => window.hairLivePreview.exportDiagnostic()) as Record<string, unknown>;
  expect(report.candidateAccepted).toBe(false);
  const outputs = report as Record<string, HeldOutput>, base = outputs.g!;
  for (const pipeline of ['g', 'frame-copy', 'mask-bytes']) {
    const output = outputs[pipeline]!; expect(output.stats.hasMask).toBe(true);
    for (const key of ['pair', 'detection', 'mask', 'sourcePngDataUrl', 'acceptedPngDataUrl', 'hairPngDataUrl'] as const)
      expect(output[key]).toEqual(base[key]);
    for (const key of ['surfacePositions', 'eyewearMatrix', 'protection'] as const)
      expect(output.captureSnapshot[key]).toEqual(base.captureSnapshot[key]);
    for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'] as const)
      expect(output.stats[key].changedPixels).toBe(0);
  }
  const rendered = await page.evaluate(async () => {
    const report = window.hairLivePreview.exportDiagnostic()! as Record<string, HeldOutput>, output = report.g!;
    const images = await Promise.all([output.sourcePngDataUrl, output.acceptedPngDataUrl].map(async url => {
      const image = new Image(); image.src = url; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
      return context.getImageData(0, 0, canvas.width, canvas.height);
    }));
    const source = images[0]!, accepted = images[1]!; let changedPixels = 0;
    for (let i = 0; i < source.data.length; i += 4)
      if (source.data[i] !== accepted.data[i] || source.data[i + 1] !== accepted.data[i + 1]
        || source.data[i + 2] !== accepted.data[i + 2]) changedPixels++;
    const samples = [[40, 50], [680, 50], [40, 1230], [680, 1230]].map(([x, y]) => {
      const offset = (y! * source.width + x!) * 4; return Array.from(source.data.slice(offset, offset + 4));
    });
    return {width: source.width, height: source.height, changedPixels, samples};
  });
  expect(rendered.width).toBe(720); expect(rendered.height).toBe(1280);
  expect(rendered.changedPixels).toBeGreaterThan(100); upright(rendered.samples);
  return {comparedPipelines: report.comparedPipelines, candidateAccepted: report.candidateAccepted,
    matchingInputAndSafeguards: true, rendered};
}

const cases: {eyewear: string; hair: string; scenario: OrientationCase}[] =
  ['amber-horizon', 'tom-ford-clear'].flatMap(eyewear => ['hair-only', 'selfie-multiclass']
    .map(hair => ({eyewear, hair, scenario: 'missing-metadata' as const})));
cases.push({eyewear: 'amber-horizon', hair: 'hair-only', scenario: 'rotated-storage'});
for (const {eyewear, hair, scenario} of cases)
  test(`VideoFrame orientation guard: ${scenario} / ${eyewear} / ${hair}`, async ({page}) => {
    test.setTimeout(240_000);
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await installCamera(page, scenario); await page.goto(entry);
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.click('#start');
    const receipts: {pipeline: Choice; sample: FrameSample; orientationPixels: number[][]}[] = [];
    let held: unknown = null;
    try {
      let lastSerial = 0, session: string | null = null, generation = 0;
      for (const pipeline of ['g', 'frame-copy', 'mask-bytes', 'g'] as const) {
        if (lastSerial) await page.selectOption('#pipeline-select', pipeline);
        const sample = await published(page, pipeline, lastSerial); lastSerial = sample.serial;
        session ??= sample.sessionId; expect(sample.sessionId).toBe(session);
        expect(sample.sourceWidth).toBe(720); expect(sample.sourceHeight).toBe(1280);
        expect(sample.hasFace).toBe(true); expect(sample.hasMask).toBe(true);
        expect(sample.faceDelegate).toBe('GPU');
        expect(Number(sample.native?.['pump.maxOwnedFrames'])).toBeLessThanOrEqual(2);
        expect(Number(sample.native?.['pump.maxInFlightInference'])).toBeLessThanOrEqual(1);
        expect(Number(sample.native?.['runtime.generation'])).toBeGreaterThan(generation);
        generation = Number(sample.native?.['runtime.generation']);
        if (pipeline === 'frame-copy') {
          expect(sample.native?.['capture.actualPath']).toBe('canvas-video-fallback');
          expect(sample.native?.['capture.fallbackReason']).toBe(scenario === 'missing-metadata'
            ? 'orientation-metadata-unavailable' : 'dimensions-or-transform');
          expect(sample.native?.['capture.copiedBytes']).toBe(0);
          expect(sample.native?.['capture.videoWidth']).toBe(720);
          expect(sample.native?.['capture.videoHeight']).toBe(1280);
          expect(sample.native?.['capture.frameDisplayWidth']).toBe(720);
          expect(sample.native?.['capture.frameDisplayHeight']).toBe(1280);
          expect(sample.native?.['capture.orientationMetadataAvailable']).toBe(scenario !== 'missing-metadata');
          expect(sample.native?.['capture.frameRotation']).toBe(scenario === 'missing-metadata' ? null : 90);
          expect(sample.native?.['capture.frameFlip']).toBe(scenario === 'missing-metadata' ? null : false);
          expect(sample.native?.['capture.frameVisibleWidth']).toBe(scenario === 'missing-metadata' ? 720 : 1280);
          expect(sample.native?.['capture.frameVisibleHeight']).toBe(scenario === 'missing-metadata' ? 1280 : 720);
          expect(Number(sample.native?.['capture.maxOwnedImages'])).toBeLessThanOrEqual(2);
        }
        if (pipeline === 'mask-bytes') {
          expect(sample.native?.['hairCategory.path']).toBe('rgba8-readback');
          expect(sample.native?.['hairCategory.rgba8FallbackReason']).toBeNull();
          expect(Number(sample.native?.['hairCategory.rgba8ReadbackBytes'])).toBe(720 * 1280 * 4);
        }
        const orientationPixels = await displayPixels(page); upright(orientationPixels);
        receipts.push({pipeline, sample, orientationPixels});
        expect(await page.evaluate(() => window.frameOrientationObservation.copyCalls)).toBe(0);
        expect(await page.evaluate(() => window.frameOrientationObservation.streams.map(stream =>
          stream.getTracks().map(track => track.readyState)))).toEqual([['live']]);
      }
      expect(await page.evaluate(() => window.frameOrientationObservation.frameConstructions)).toBeGreaterThanOrEqual(4);
      held = await heldPair(page);
      await page.screenshot({path: test.info().outputPath('upright-glasses.png'), fullPage: true});
    } finally {
      await cleanup(page);
      const resources = await page.evaluate(() => {
        const state = window.frameOrientationObservation;
        return {copyCalls: state.copyCalls, frameConstructions: state.frameConstructions, frameCloses: state.frameCloses,
          workers: state.workers.map(worker => ({...worker})),
          streams: state.streams.map(stream => stream.getTracks().map(track => track.readyState))};
      });
      await writeFile(test.info().outputPath('orientation-regression.json'), JSON.stringify({eyewear, hair, scenario,
        receipts, held, resources, errors, evidence: 'Static synthetic portrait; real workers and GPU; no iPhone speed or wearer-motion claim.'}, null, 2));
    }
    expect(errors).toEqual([]);
  });
