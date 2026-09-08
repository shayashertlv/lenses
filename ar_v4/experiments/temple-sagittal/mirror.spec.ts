import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { FaceSurface } from '../../src/render/face-surface.ts';
import { createNasalShape, NASAL_SHAPE_ID, NASAL_SHAPE_VERSION } from '../../src/render/nasal-shape.ts';
import type { Detection, DetectorRequest, DetectorResponse } from '../../src/runtime/protocol.ts';

test('neutral live policy round trip preserves its pair; recording blocks hold and replay locks selection', async ({page}) => {
  await installCamera(page);
  await page.goto('/experiments/temple-sagittal/live.html');
  await page.locator('#start').click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state','tracking');
  await page.evaluate(() => { (window as unknown as {cameraHarness:CameraHarness}).cameraHarness.holdNextResult=true; });
  await expect.poll(() => page.evaluate(() => (window as unknown as {cameraHarness:CameraHarness}).cameraHarness.held.length)).toBeGreaterThan(0);
  const result=await page.evaluate(() => {
    const select=document.querySelector<HTMLSelectElement>('#variant-select')!;
    const canvas=document.querySelector<HTMLCanvasElement>('#mirror')!;
    const show=(value:string) => {select.value=value;select.dispatchEvent(new Event('change'));return canvas.toDataURL();};
    const a=show('perfecto'),b=show('candidate'),again=show('perfecto');
    show('candidate');return {same:a===again,png:a.startsWith('data:image/png'),bytes:a.length,candidateBytes:b.length};
  });
  expect(result.same).toBe(true);expect(result.png).toBe(true);expect(result.bytes).toBeGreaterThan(10000);expect(result.candidateBytes).toBeGreaterThan(10000);
  await page.evaluate(() => (window as unknown as {cameraHarness:CameraHarness}).cameraHarness.releaseHeld());
  await page.locator('#record-turn').click();
  await expect(page.locator('#hold-frame')).toBeDisabled();
  await page.locator('#hold-frame').dispatchEvent('click');
  await expect(page.locator('#capture')).toHaveAttribute('data-state','recording');
  await expect(page.locator('.stage')).toHaveAttribute('data-state','tracking');
  await expect.poll(async()=>Number(await page.locator('#capture').getAttribute('data-frames'))).toBeGreaterThanOrEqual(2);
  await page.locator('#finish-recording').click();
  await expect(page.locator('#variant-select')).toBeDisabled();
  await expect(page.locator('#hold-frame')).toBeHidden();
  await page.locator('#discard-capture').click();
  await expect(page.locator('#variant-select')).toBeEnabled();
});

test('holding a controlled downward pair stops live work and exports exact matched diagnostic inputs', async ({page}) => {
  const privateFixtureUrl = new URL(
    '../../.recovery/nose-dropout-2026-09-07/trace-074054/capture.json', import.meta.url);
  test.skip(!existsSync(privateFixtureUrl), 'Requires the preserved local paired recording.');
  test.setTimeout(120_000);
  const errors: string[] = [];
  const downloads: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('download', download => downloads.push(download.suggestedFilename()));
  // This controls transport with a known historical down pair. It supplies the
  // original result directly; it does not run a detector on an overlaid image.
  // The camera's canvas/video conversion makes this a lifecycle fixture, not a
  // fresh recorded appearance evaluation. The separate native pair harness is
  // responsible for byte-identical historical source/render comparisons.
  const capture = JSON.parse(await readFile(privateFixtureUrl, 'utf8')) as {
      frames: {width: number; height: number; sourcePngDataUrl: string; detection: Detection}[];
    };
  const original = capture.frames[2]!;
  expect(original.detection.matrix![9]).toBeLessThan(-.3);
  await installCamera(page, 0, false, {width: original.width, height: original.height,
    image: Buffer.from(original.sourcePngDataUrl.split(',')[1]!, 'base64'), detection: original.detection});
  await page.goto('/experiments/temple-sagittal/live.html');
  // Observe the public present input synchronously, without changing the image,
  // detection, pose or renderer result. This is an independent export oracle.
  await page.evaluate(async () => {
    const modulePath = '/experiments/temple-sagittal/renderer.ts';
    const {TryOnRenderer}: typeof import('./renderer.ts') = await import(modulePath);
    const present = TryOnRenderer.prototype.present;
    const state: {presentations: number; latest: {
      sourcePng: string; detection: Detection; rawMatrix: number[]; correctedMatrix: number[];
      eyewearMatrix: number[]; surfacePositions: number[];
    } | null} = {presentations: 0, latest: null};
    Object.assign(window, {heldPairInputs: state});
    TryOnRenderer.prototype.present = function (this: Awaited<ReturnType<typeof TryOnRenderer.create>>, ...args: Parameters<typeof present>) {
      const sourcePng = args[0].toDataURL('image/png');
      const detection = structuredClone(args[1]);
      const visible = present.apply(this, args);
      const snapshot = this.captureSnapshot;
      state.presentations++;
      if (visible && snapshot) state.latest = {sourcePng, detection,
        rawMatrix: snapshot.rawMatrix, correctedMatrix: snapshot.correctedMatrix,
        eyewearMatrix: snapshot.eyewearMatrix, surfacePositions: Array.from(snapshot.surfacePositions)};
      return visible;
    };
  });
  await page.locator('#eyewear-select').selectOption('tom-ford-clear');
  await page.locator('#start').click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state','tracking');
  await expect(page.locator('#hold-frame')).toBeEnabled();
  await page.evaluate(() => { (window as unknown as {cameraHarness:CameraHarness}).cameraHarness.holdNextResult = true; });
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as {cameraHarness:CameraHarness}).cameraHarness.held.length)).toBe(1);
  const expected = await page.evaluate(() => (window as unknown as {heldPairInputs: {
    presentations: number; latest: {sourcePng: string; detection: Detection; rawMatrix: number[];
      correctedMatrix: number[]; eyewearMatrix: number[]; surfacePositions: number[]} | null;
  }}).heldPairInputs);
  expect(expected.latest).not.toBeNull();
  const presentedAt = Number(await page.locator('.stage').getAttribute('data-presented-at'));
  await page.locator('#hold-frame').click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state','held');
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  const stopped = await resources(page);
  await expect(page.locator('#variant-select')).toBeEnabled();
  await expect(page.locator('#eyewear-select')).toBeDisabled();
  await expect(page.locator('#record-turn')).toBeDisabled();
  await expect(page.locator('#download-diagnostic')).toBeEnabled();
  await expect(page.locator('#capture')).toHaveAttribute('data-frames','0');
  expect(downloads).toEqual([]);
  await page.evaluate(() => (window as unknown as {cameraHarness:CameraHarness}).cameraHarness.releaseHeld());
  const toggled = await page.evaluate(() => {
    const select = document.querySelector<HTMLSelectElement>('#variant-select')!;
    const canvas = document.querySelector<HTMLCanvasElement>('#mirror')!;
    const show = (value: string) => { select.value=value; select.dispatchEvent(new Event('change')); return canvas.toDataURL('image/png'); };
    const baseline=show('perfecto'), candidate=show('candidate'), restored=show('perfecto');
    show('candidate');
    return {baseline,candidate,restored,has2DContext:canvas.getContext('2d')!==null};
  });
  expect(toggled.has2DContext).toBe(true);
  expect(toggled.restored).toBe(toggled.baseline);
  expect(await resources(page)).toEqual(stopped);
  expect(await page.locator('.stage').getAttribute('data-presented-at')).toBe(String(presentedAt));
  expect(await page.evaluate(() =>
    (window as unknown as {heldPairInputs:{presentations:number}}).heldPairInputs.presentations)).toBe(expected.presentations);
  const downloadEvent = page.waitForEvent('download');
  await page.locator('#download-diagnostic').click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/^ar-v4-held-diagnostic-.+\.json$/);
  const diagnosticPath = test.info().outputPath('controlled-down-held-diagnostic.json');
  await download.saveAs(diagnosticPath);
  const diagnostic = JSON.parse(await readFile(diagnosticPath,'utf8')) as {
    schema:string; candidateAccepted:boolean; acceptedRevisionLabel:string; sourcePngDataUrl:string; detection:Detection;
    originalPerfectoPngDataUrl:string; candidatePngDataUrl:string; branchPngDataUrl:string;
    snapshot:{rawMatrix:number[];correctedMatrix:number[];eyewearMatrix:number[];surfacePositions:number[]};
    candidateRearDrop:import('./contracts.ts').RearDropConfiguration;
    protection:import('./contracts.ts').ProtectionConfiguration;
    heldFrame:{capturedAtMs:number;heldAt:string;performanceTimeOriginMs:number;selectedVariantAtDownload:string};
    nativeSamples:number;
    diagnostics:Record<string,unknown>;
  };
  await writeFile(test.info().outputPath('controlled-down-geometry-summary.json'), JSON.stringify({
    candidateRearDrop: diagnostic.candidateRearDrop, protection: diagnostic.protection,
    diagnostics: diagnostic.diagnostics, displayChanged: toggled.candidate !== toggled.baseline,
  }, null, 2));
  expect(diagnostic.schema).toBe('ar_v4_sagittal_diagnostic_v1');
  expect(diagnostic.candidateAccepted).toBe(true);
  expect(diagnostic.acceptedRevisionLabel).toBe('perfect_temples');
  expect(diagnostic.sourcePngDataUrl).toBe(expected.latest!.sourcePng);
  expect(diagnostic.detection).toEqual(original.detection);
  expect(diagnostic.detection).toEqual(expected.latest!.detection);
  for (const field of ['rawMatrix','correctedMatrix','eyewearMatrix','surfacePositions'] as const)
    expect(diagnostic.snapshot[field]).toEqual(expected.latest![field]);
  expect(diagnostic.originalPerfectoPngDataUrl).toBe(toggled.baseline);
  expect(diagnostic.candidatePngDataUrl).toBe(toggled.candidate);
  // Down454 requests a nonzero rear drop, but its protected optical region
  // currently contains all affected output pixels. Identical selected images
  // are legitimate here; the exact pair and actual applied policy are tested.
  expect(diagnostic.branchPngDataUrl).toMatch(/^data:image\/png;base64,/);
  expect(diagnostic.candidateRearDrop.method).toBe('temple-rear-drop-v1');
  expect(diagnostic.candidateRearDrop.dropM).toBeGreaterThan(0);
  expect(diagnostic.candidateRearDrop.dropM).toBeLessThanOrEqual(.02);
  expect(diagnostic.protection.method).toBe('temple-optics-copy-v1');
  expect(diagnostic.heldFrame.capturedAtMs).toBe(presentedAt);
  expect(Number.isFinite(Date.parse(diagnostic.heldFrame.heldAt))).toBe(true);
  expect(diagnostic.heldFrame.performanceTimeOriginMs).toBeGreaterThan(0);
  expect(diagnostic.heldFrame.selectedVariantAtDownload).toBe('candidate');
  expect(diagnostic.nativeSamples).toBe(Number(await page.locator('.stage').getAttribute('data-native-samples')));
  const pixelCheck = await page.evaluate(async ({baselineUrl,candidateUrl,protection}) => {
    async function pixels(url:string) { const image=new Image(); image.src=url; await image.decode();
      const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
      const context=canvas.getContext('2d')!;context.drawImage(image,0,0);
      return context.getImageData(0,0,canvas.width,canvas.height).data; }
    const baseline=await pixels(baselineUrl),candidate=await pixels(candidateUrl);
    let changed=0,protectedChanges=0,outsideEditableChanges=0;
    for(let y=0;y<protection.height;y++)for(let x=0;x<protection.width;x++) {
      const offset=(y*protection.width+x)*4;
      if(![0,1,2,3].some(channel=>baseline[offset+channel]!==candidate[offset+channel]))continue;
      changed++;
      const inside=(rect:{x0:number;y0:number;x1:number;y1:number})=>x>=rect.x0&&x<rect.x1&&y>=rect.y0&&y<rect.y1;
      if(protection.protectedRects.some(inside))protectedChanges++;
      if(!protection.editableRects.some(inside))outsideEditableChanges++;
    }
    return {changed,protectedChanges,outsideEditableChanges};
  },{baselineUrl:diagnostic.originalPerfectoPngDataUrl,candidateUrl:diagnostic.candidatePngDataUrl,protection:diagnostic.protection});
  expect(pixelCheck.changed).toBe(diagnostic.diagnostics.changedPixels);
  expect(toggled.candidate !== toggled.baseline).toBe(pixelCheck.changed > 0);
  expect(pixelCheck.protectedChanges).toBe(0);
  expect(pixelCheck.outsideEditableChanges).toBe(0);
  expect(downloads).toHaveLength(1);
  expect(await resources(page)).toEqual(stopped);
  await page.getByRole('button',{name:'Close held frame'}).click();
  await expect(page.locator('#eyewear-select')).toBeEnabled();
  await expect(page.locator('#download-diagnostic')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await page.locator('#start').click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state','tracking');
  await page.getByRole('button',{name:'Close camera'}).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});

interface CameraHarness {
  streams: MediaStream[];
  workers: { terminated: boolean; detections: number }[];
  results: { capturedAt: number; detection: Detection; marker: number[] }[];
  marker: 'red' | 'blue';
  blank: boolean;
  holdNextResult: boolean;
  delegates: string[];
  webglCanvases: HTMLCanvasElement[];
  held: (() => void)[];
  releaseHeld(): void;
}

type TempleCoverage = 'alpha-to-coverage' | 'ordered-dither';

interface ExportedFrame {
  index: number;
  capturedAt: number;
  relativeMs: number;
  width: number;
  height: number;
  jpegDataUrl: string;
  detection: Detection;
  metadata: {
    surfacePositions: number[];
    templeClip?: { method: string; negativeXCutoffLocalZM: number; positiveXCutoffLocalZM: number;
      fadeLengthLocalM?: number; coverage?: TempleCoverage } | null;
    templeVisibility?: { method: string; negativeXWeight: number; positiveXWeight: number;
      coverage: TempleCoverage; frontalOcclusionWeight?: number } | null;
    rearDrop?: import('./contracts.ts').RearDropConfiguration | null;
    protection?: import('./contracts.ts').ProtectionConfiguration | null;
    [key: string]: unknown;
  } | null;
  yawDegrees: number | null;
}

interface PairedCameraFixture { image: Buffer; width: number; height: number; detection: Detection; }
/** Default lifecycle fixture uses real MediaPipe. An explicit paired fixture is
 * a controlled camera/worker transport test, never new recorded visual evidence. */
async function installCamera(page: Page, permissionDelayMs = 0, failGpu = false,
  pairedFixture?: PairedCameraFixture): Promise<void> {
  const portrait = pairedFixture?.image ?? await readFile(new URL('../../tests/fixtures/face-a.jpg', import.meta.url));
  await page.route('**/test-fixtures/camera-face.jpg', route =>
    route.fulfill({ contentType: pairedFixture ? 'image/png' : 'image/jpeg', body: portrait }));
  await page.addInitScript(({ permissionDelayMs, failGpu, paired }) => {
    const fixture = document.createElement('canvas');
    fixture.width = paired?.width ?? 960;
    fixture.height = paired?.height ?? 720;
    const context = fixture.getContext('2d')!;
    const portrait = new Image();
    const ready = new Promise<void>((resolve, reject) => {
      portrait.onload = () => resolve();
      portrait.onerror = reject;
    });
    portrait.src = '/test-fixtures/camera-face.jpg';
    const state: CameraHarness = {
      streams: [], workers: [], results: [], marker: 'red', blank: false,
      holdNextResult: false, delegates: [], webglCanvases: [], held: [], releaseHeld: () => {},
    };
    const nativeGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, id: string, options?: unknown) {
      const result = nativeGetContext.call(this, id, options);
      if (id === 'webgl2' && result && !state.webglCanvases.includes(this)) state.webglCanvases.push(this);
      return result;
    } as typeof HTMLCanvasElement.prototype.getContext;
    state.releaseHeld = () => {
      for (const deliver of state.held.splice(0)) deliver();
    };
    Object.assign(window, { cameraHarness: state });
    const frameCallback = HTMLVideoElement.prototype.requestVideoFrameCallback;
    HTMLVideoElement.prototype.requestVideoFrameCallback = function (callback) {
      return frameCallback.call(this, (now, metadata) => callback(now, { ...metadata, mediaTime: 0 }));
    };
    setInterval(() => {
      context.fillStyle = '#778878';
      context.fillRect(0, 0, fixture.width, fixture.height);
      if (!state.blank && portrait.complete && portrait.naturalWidth) {
        if (paired) context.drawImage(portrait, 0, 0, fixture.width, fixture.height);
        else context.drawImage(portrait, 120, 0, 720, 720);
      }
      // A changing patch outside the face lets the export test independently
      // match compressed camera pixels to the exact worker submission.
      if (!paired) {
        context.fillStyle = state.marker === 'red' ? '#f02020' : '#2030f0';
        context.fillRect(0, 0, 80, 80);
      }
    }, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async () => {
        await ready;
        await new Promise(resolve => setTimeout(resolve, permissionDelayMs));
        const stream = fixture.captureStream(30);
        state.streams.push(stream);
        return stream;
      },
    });

    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly index: number;
      private readonly fixtureUrl: string | null;
      private readonly requests = new Map<number, { capturedAt: number; marker: number[] }>();
      private readonly sample = document.createElement('canvas');

      constructor(url: string | URL, options?: WorkerOptions) {
        // A blob worker inherits the isolated document's policy. Routing a
        // replacement worker response can omit its COEP headers and tests the
        // browser's network policy instead of the held-input ownership flow.
        const fixtureUrl = paired ? URL.createObjectURL(new Blob([
          'self.onmessage = ({data}) => { if(data.type === "initialize") self.postMessage({type:"ready",id:data.id}); else if(data.type === "detect") { data.image.close(); self.postMessage({type:"result",id:data.id,detection:'
            + JSON.stringify(paired.detection) + '}); } };',
        ], {type: 'text/javascript'})) : null;
        super(fixtureUrl ?? url, options);
        this.fixtureUrl = fixtureUrl;
        this.index = state.workers.length;
        state.workers.push({ terminated: false, detections: 0 });
        this.sample.width = this.sample.height = 1;
        this.addEventListener('message', (event: MessageEvent<DetectorResponse>) => {
          if (event.data.type !== 'result') return;
          const request = this.requests.get(event.data.id);
          if (request) {
            state.results.push({ ...request, detection: structuredClone(event.data.detection) });
            this.requests.delete(event.data.id);
          }
          if (state.holdNextResult) {
            state.holdNextResult = false;
            event.stopImmediatePropagation();
            // Retain an already queued callback even after terminate(), just
            // as the existing lifecycle regression does for late deliveries.
            const callback = this.onmessage;
            state.held.push(() => callback?.call(this,
              new MessageEvent('message', { data: event.data })));
          }
        });
      }

      override postMessage(message: unknown, transferOrOptions: Transferable[] | StructuredSerializeOptions = []): void {
        const request = message as DetectorRequest;
        if (request.type === 'initialize') {
          state.delegates.push(request.delegate);
          if (failGpu && this.index === 0 && request.delegate === 'GPU')
            message = { ...request, modelUrl: new URL('/test-fixtures/unavailable.task', location.href).href };
        }
        if (request.type === 'detect') {
          state.workers[this.index]!.detections++;
          const context = this.sample.getContext('2d', { willReadFrequently: true })!;
          context.drawImage(request.image, 10, 10, 1, 1, 0, 0, 1, 1);
          this.requests.set(request.id, {
            capturedAt: request.timestampMs,
            marker: Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3),
          });
        }
        if (Array.isArray(transferOrOptions)) super.postMessage(message, transferOrOptions);
        else super.postMessage(message, transferOrOptions);
      }

      override terminate(): void {
        state.workers[this.index]!.terminated = true;
        super.terminate();
        if (this.fixtureUrl) URL.revokeObjectURL(this.fixtureUrl);
      }
    }
    Object.defineProperty(window, 'Worker', { configurable: true, value: ObservedWorker });
  }, { permissionDelayMs, failGpu, paired: pairedFixture
    ? {width: pairedFixture.width, height: pairedFixture.height, detection: pairedFixture.detection} : null });
}

async function resources(page: Page): Promise<{ closed: boolean; workers: number; detections: number }> {
  return page.evaluate(() => {
    const harness = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    return {
      closed: harness.streams.length > 0
        && harness.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
        && harness.workers.every(worker => worker.terminated),
      workers: harness.workers.length,
      detections: harness.workers.reduce((total, worker) => total + worker.detections, 0),
    };
  });
}

async function storedFrames(page: Page): Promise<number> {
  return Number(await page.locator('#capture').getAttribute('data-frames'));
}

async function templeCoverage(page: Page): Promise<TempleCoverage> {
  return page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>('.stage')!;
    const samples = Number(stage.dataset.nativeSamples);
    if (!Number.isInteger(samples) || samples < 0) throw new Error('Native renderer sampling provenance is unavailable.');
    return samples > 0 ? 'alpha-to-coverage' : 'ordered-dither';
  });
}

async function expectTempleHeader(page: Page, header: Record<string, unknown>, cutoff: number): Promise<void> {
  const coverage = await templeCoverage(page);
  expect(header.comparison).toEqual(expect.objectContaining({candidateAccepted: true,
    acceptedRevisionLabel: 'perfect_temples', baselineCommit: '31df28eb8ca0c698467fd9bfb16f737f9a74e915'}));
  expect(header.templeClip).toEqual({ method: 'temple-end-blend-v3',
    negativeXCutoffLocalZM: cutoff, positiveXCutoffLocalZM: cutoff, fadeLengthLocalM: 0.015 });
  expect(header.templeClipPolicy).toMatch(/Fixed endpoints per model.*15 mm dissolve into the paired camera image/);
  expect(header.templeVisibility).toEqual(expect.objectContaining({
    method: 'temple-side-depth-v3', coverage, parameters: expect.any(Object),
  }));
  expect(header.templeVisibilityPolicy).toContain('Accepted perfecto side/frontal visibility');
  expect(header.rearDropPolicy).toMatch(/maximum 20 mm.*actual rear drop/);
  expect(header.nativeSamples).toBe(Number(await page.locator('.stage').getAttribute('data-native-samples')));
}

async function expectRawShapeCaptures(page: Page, frames: readonly ExportedFrame[]): Promise<void> {
  const coverage = await templeCoverage(page);
  const canonical = JSON.parse(await readFile(new URL('../../public/models/canonical-face.json', import.meta.url), 'utf8')) as {
    positions: number[]; indices: number[];
  };
  const reconstruction = new FaceSurface(canonical.positions);
  const shape = createNasalShape(canonical.positions, canonical.indices);
  let shapedFrames = 0;
  for (const frame of frames) {
    if (!frame.metadata) continue;
    expect(frame.detection.matrix).not.toBeNull();
    expect(reconstruction.reconstruct(frame.detection.landmarks, frame.detection.matrix!, frame.width / frame.height)).toBe(true);
    const raw = reconstruction.positions.slice();
    const expected = shape.apply({ surfacePositions: raw, rawMatrix: frame.detection.matrix! });
    expect(frame.metadata.surfacePositions).toEqual(Array.from(expected.surfacePositions));
    expect(frame.metadata.occlusion).toEqual({
      method: NASAL_SHAPE_VERSION, selectedShapeId: NASAL_SHAPE_ID,
      appliedShapeId: expected.accepted ? NASAL_SHAPE_ID : null,
      status: expected.accepted ? 'applied' : 'raw-fallback',
      rejectionReasons: expected.diagnostics.rejectionReasons,
    });
    expect(['amber-horizon', 'tom-ford-clear']).toContain(frame.metadata.eyewearModelId);
    const fixedCutoff = frame.metadata.eyewearModelId === 'amber-horizon' ? -0.105 : -0.110;
    expect(frame.metadata.rearDrop).toEqual({method:'temple-rear-drop-v1',dropM:expect.any(Number)});
    expect(frame.metadata.rearDrop?.dropM).toBeGreaterThanOrEqual(0);
    expect(frame.metadata.rearDrop?.dropM).toBeLessThanOrEqual(.02);
    if (frame.metadata.protection) {
      expect(frame.metadata.protection.method).toBe('temple-optics-copy-v1');
      expect(frame.metadata.protection.width).toBe(frame.width);
      expect(frame.metadata.protection.height).toBe(frame.height);
      expect(frame.metadata.protection.protectedRects.length).toBeGreaterThanOrEqual(2);
    }
    expect(frame.metadata.templeClip).toEqual({
      method: 'temple-end-blend-v3', fadeLengthLocalM: 0.015,
      negativeXCutoffLocalZM: fixedCutoff, positiveXCutoffLocalZM: fixedCutoff,
    });
    expect(frame.metadata.templeVisibility).toEqual({
      method: 'temple-side-depth-v3', coverage,
      negativeXWeight: expect.any(Number), positiveXWeight: expect.any(Number), frontalOcclusionWeight: expect.any(Number),
    });
    for (const weight of [frame.metadata.templeVisibility!.negativeXWeight,
      frame.metadata.templeVisibility!.positiveXWeight, frame.metadata.templeVisibility!.frontalOcclusionWeight!]) {
      expect(Number.isFinite(weight)).toBe(true);
      expect(weight).toBeGreaterThanOrEqual(0);
      expect(weight).toBeLessThanOrEqual(1);
    }
    if (expected.surfacePositions.some((value, index) => value !== raw[index])) shapedFrames++;
  }
  expect(shapedFrames).toBeGreaterThan(0);
}



async function startRecording(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('#record-turn')).toBeEnabled();
  await page.locator('#record-turn').click();
  await expect(page.locator('#capture')).toHaveAttribute('data-state', 'recording');
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(3);
}

async function finishRecording(page: Page): Promise<number> {
  await page.locator('#finish-recording').click();
  await expect(page.locator('#capture')).toHaveAttribute('data-state', 'replay');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'replay');
  await expect(page.locator('#replay-controls')).toBeVisible();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  return storedFrames(page);
}



test('recorded image and detection pairs replay locally without restarting inference', async ({ page }) => {
  const errors: string[] = [];
  const origins = new Set<string>();
  const downloads: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.url().startsWith('http')) origins.add(new URL(request.url()).origin);
  });
  page.on('download', download => downloads.push(download.suggestedFilename()));
  // Capture after the replay callback mutates its index, before the compositor
  // releases the non-preserved WebGL framebuffer. CSS screenshots also include
  // backdrop-filtered labels, whose pixels can vary with page scrolling.
  await page.addInitScript(() => {
    const state: {image: {index: string; png: string} | null} = {image: null};
    Object.assign(window, {nativeReplayImage: state});
    new MutationObserver(changes => {
      for (const change of changes) {
        const stage = change.target as HTMLElement;
        const canvas = stage.querySelector<HTMLCanvasElement>('#mirror');
        if (canvas && stage.dataset.replayFrame !== undefined)
          state.image = {index: stage.dataset.replayFrame, png: canvas.toDataURL()};
      }
    }).observe(document, {subtree: true, attributes: true, attributeFilter: ['data-replay-frame']});
  });
  const nativeReplayImage = () => page.evaluate(() => {
    const {image} = (window as unknown as {
      nativeReplayImage: {image: {index: string; png: string} | null};
    }).nativeReplayImage;
    if (!image) throw new Error('No rendered replay image was captured.');
    return image;
  });
  await installCamera(page);
  await page.goto('/experiments/temple-sagittal/live.html');
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#record-turn')).toBeDisabled();
  await startRecording(page);
  const redCount = await storedFrames(page);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.marker = 'blue';
  });
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(redCount + 3);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.blank = true;
  });
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'searching');
  const beforeBlank = await storedFrames(page);
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(beforeBlank + 2);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.blank = false;
  });
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  const afterBlank = await storedFrames(page);
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(afterBlank + 2);
  const count = await finishRecording(page);
  const stopped = await resources(page);
  expect(count).toBeGreaterThanOrEqual(6);
  expect(count).toBeLessThanOrEqual(96);
  await expect(page.locator('#replay-frame')).toHaveAttribute('min', '0');
  await expect(page.locator('#replay-frame')).toHaveAttribute('max', String(count - 1));
  await page.locator('#replay-frame').focus();
  await page.locator('#replay-frame').press('Home');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', '0');
  const firstNative = await nativeReplayImage();
  expect(firstNative.index).toBe('0');
  const firstImage = await page.locator('#mirror').screenshot();
  await writeFile(test.info().outputPath('replay-first-ui.png'), firstImage);
  await writeFile(test.info().outputPath('replay-first-native.png'), Buffer.from(firstNative.png.split(',')[1]!, 'base64'));
  await page.locator('#replay-frame').press('End');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', String(count - 1));
  await page.locator('#replay-frame').press('ArrowRight');
  await expect(page.locator('#replay-frame')).toHaveValue(String(count - 1));
  const lastNative = await nativeReplayImage();
  expect(lastNative.index).toBe(String(count - 1));
  expect(lastNative.png === firstNative.png).toBe(false);
  const lastImage = await page.locator('#mirror').screenshot();
  expect(lastImage.equals(firstImage)).toBe(false);
  await writeFile(test.info().outputPath('replay-baseline.png'), lastImage);

  expect(await resources(page)).toEqual(stopped);
  expect(downloads).toEqual([]);
  await page.screenshot({ path: test.info().outputPath('desktop-replay.png'), fullPage: true });

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-capture').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^ar-v4-.+\.json$/);
  const capturePath = test.info().outputPath('synthetic-capture.json');
  await download.saveAs(capturePath);
  const artifact = JSON.parse(await readFile(capturePath, 'utf8')) as {
    schemaVersion: number; projectId: string;
    limits: { maxFrames: number; maxCompressedBytes: number; maxDurationMs: number };
    header: Record<string, unknown>;
    frames: ExportedFrame[];
  };
  expect(artifact.schemaVersion).toBe(1);
  expect(artifact.projectId).toBe('ar_v4');
  expect(artifact.frames).toHaveLength(count);
  expect(artifact.frames.length).toBeLessThanOrEqual(artifact.limits.maxFrames);
  expect(artifact.header).toEqual(expect.objectContaining({
    camera: expect.any(Object), projection: expect.any(Object),
    assets: expect.any(Object),
  }));
  await expectTempleHeader(page, artifact.header, -0.105);
  const delivered = await page.evaluate(() =>
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.results);
  let compressedBytes = 0;
  for (const [index, frame] of artifact.frames.entries()) {
    expect(frame.index).toBe(index);
    expect(frame.relativeMs).toBeGreaterThanOrEqual(0);
    expect(frame.relativeMs).toBeLessThanOrEqual(artifact.limits.maxDurationMs);
    if (index > 0) expect(frame.capturedAt).toBeGreaterThan(artifact.frames[index - 1]!.capturedAt);
    expect(frame.width).toBeGreaterThan(0);
    expect(frame.width).toBeLessThanOrEqual(1280);
    expect(frame.width / frame.height).toBeCloseTo(4 / 3, 2);
    if (frame.metadata) {
      expect(frame.detection.landmarks).toHaveLength(478);
      expect(frame.detection.matrix).toHaveLength(16);
      for (const key of ['rawMatrix', 'correctedMatrix', 'eyewearMatrix']) {
        const matrix = frame.metadata[key];
        expect(matrix).toHaveLength(16);
        expect(Array.isArray(matrix) && matrix.every(Number.isFinite)).toBe(true);
      }
      expect(frame.metadata.rawMatrix).toEqual(frame.detection.matrix);
      expect(frame.metadata.surfacePositions).toHaveLength(1404);
      expect(Number.isFinite(frame.yawDegrees)).toBe(true);
    } else {
      expect(frame.detection.landmarks).toHaveLength(0);
      expect(frame.detection.matrix).toBeNull();
      expect(frame.yawDegrees).toBeNull();
    }
    expect(frame.jpegDataUrl).toMatch(/^data:image\/jpeg;base64,/);
    compressedBytes += Buffer.from(frame.jpegDataUrl.split(',')[1]!, 'base64').byteLength;
    const original = delivered.find(result => result.capturedAt === frame.capturedAt);
    expect(original).toBeDefined();
    expect(frame.detection).toEqual(original!.detection);
    const decoded = await page.evaluate(async url => {
      const image = new Image();
      image.src = url;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d')!;
      context.drawImage(image, 10, 10, 1, 1, 0, 0, 1, 1);
      return { width: image.naturalWidth, height: image.naturalHeight,
        marker: Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3) };
    }, frame.jpegDataUrl);
    expect(decoded.width).toBe(frame.width);
    expect(decoded.height).toBe(frame.height);
    for (let channel = 0; channel < 3; channel++)
      expect(Math.abs(decoded.marker[channel]! - original!.marker[channel]!)).toBeLessThanOrEqual(5);
  }
  expect(artifact.frames.filter(frame => frame.metadata !== null).length).toBeGreaterThanOrEqual(6);
  await expectRawShapeCaptures(page, artifact.frames);
  const blank = artifact.frames.find(frame => frame.metadata === null);
  expect(blank).toBeDefined();
  await page.locator('#replay-frame').focus();
  await page.locator('#replay-frame').press('Home');
  for (let step = 0; step < blank!.index; step++) await page.locator('#replay-frame').press('ArrowRight');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', String(blank!.index));
  await expect(page.locator('#replay-metrics')).toContainText('estimated yaw unknown');
  const blankImage = await page.locator('#mirror').screenshot();
  const blankPixel = await page.evaluate(async url => {
    const image = new Image();
    image.src = url;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d')!;
    context.drawImage(image, image.naturalWidth / 2, image.naturalHeight / 2, 1, 1, 0, 0, 1, 1);
    return Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3);
  }, `data:image/png;base64,${blankImage.toString('base64')}`);
  for (const [channel, expected] of [0x77, 0x88, 0x78].entries())
    expect(Math.abs(blankPixel[channel]! - expected)).toBeLessThanOrEqual(5);
  expect(compressedBytes).toBeLessThanOrEqual(artifact.limits.maxCompressedBytes);
  expect(downloads).toHaveLength(1);
  expect(await resources(page)).toEqual(stopped);
  expect([...origins]).toEqual([new URL(page.url()).origin]);
  await page.locator('#replay-frame').focus();
  await page.locator('#replay-frame').press('Home');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', '0');
  const returnedNative = await nativeReplayImage();
  expect(returnedNative.index).toBe('0');
  await writeFile(test.info().outputPath('replay-returned-native.png'), Buffer.from(returnedNative.png.split(',')[1]!, 'base64'));
  await writeFile(test.info().outputPath('replay-returned-ui.png'), await page.locator('#mirror').screenshot());
  expect(returnedNative.png === firstNative.png).toBe(true);
  await page.locator('#discard-capture').click();
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});

test('capture cancellation rejects late detection and replay context loss permits restart', async ({ page }) => {
  // Four real renderer/worker startups share this total budget; each assertion
  // keeps the configured 20-second limit.
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await installCamera(page);
  await page.goto('/experiments/temple-sagittal/live.html');
  await startRecording(page);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.holdNextResult = true;
  });
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.held.length)).toBe(1);
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.releaseHeld();
  });
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await startRecording(page);
  await finishRecording(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('mobile-replay.png'), fullPage: true });
  const oldCanvas = await page.locator('#mirror').elementHandle();
  expect(await page.evaluate(() => {
    const harness = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    const canvases = harness.webglCanvases.filter(canvas => !canvas.getContext('webgl2')?.isContextLost());
    const extension = canvases.at(-1)?.getContext('webgl2')?.getExtension('WEBGL_lose_context');
    extension?.loseContext();
    return Boolean(extension);
  })).toBe(true);
  await expect(page.locator('#guidance')).toContainText('graphics connection was interrupted');
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  expect(await oldCanvas!.evaluate(canvas => canvas.isConnected)).toBe(false);
  await oldCanvas!.dispose();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(page.locator('#mirror')).toBeHidden();
  await page.evaluate(() => { Reflect.deleteProperty(document, 'hidden'); });
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});


test('closing pending camera permission disposes the late stream and permits restart', async ({ page }) => {
  await installCamera(page, 1400);
  await page.goto('/experiments/temple-sagittal/live.html');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect((await resources(page)).workers).toBe(0);
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect(page.locator('#mirror')).toBeHidden();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
});

test('missing model closes resources, retry recovers, and a disconnected camera releases tracking', async ({ page }) => {
  await installCamera(page);
  await page.route('**/models/face_landmarker.task', route => route.fulfill({ status: 404, body: 'Missing model' }));
  await page.goto('/experiments/temple-sagittal/live.html');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'error');
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeEnabled();
  await page.unroute('**/models/face_landmarker.task');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.evaluate(() => {
    const state = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    state.streams.at(-1)!.getVideoTracks()[0]!.dispatchEvent(new Event('ended'));
  });
  await expect(page.locator('#guidance')).toContainText('disconnected');
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
});

test('GPU startup failure tracks through a fresh real CPU worker', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await installCamera(page, 0, true);
  await page.route('**/test-fixtures/unavailable.task', route => route.fulfill({ status: 404, body: 'Intentional GPU failure' }));
  await page.goto('/experiments/temple-sagittal/live.html');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  expect(await page.evaluate(() => {
    const state = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    return { delegates: state.delegates, terminated: state.workers.map(worker => worker.terminated) };
  })).toEqual({ delegates: ['GPU', 'CPU'], terminated: [true, false] });
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});

test('clear-lens selection belongs to its capture and unlocks only after discard', async ({ page }) => {
  const errors: string[] = [];
  const models: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith('.glb')) models.push(path);
  });
  await installCamera(page);
  await page.goto('/experiments/temple-sagittal/live.html');
  const selection = page.locator('#eyewear-select');
  await expect(selection).toBeEnabled();
  await expect(selection).toHaveValue('amber-horizon');
  await expect(selection.locator('option[value="tom-ford-clear"]')).toHaveText('Tom Ford · Clear lenses');
  await expect(page.locator('#eyewear-hint')).toContainText(/choose.*before/i);
  await selection.selectOption('tom-ford-clear');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('See yourself in Tom Ford.');
  await expect(page.locator('#frame-name')).toHaveText('Tom Ford');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(selection).toBeDisabled();
  await expect(page.locator('#eyewear-hint')).toContainText(/close.*camera/i);
  expect([...new Set(models)]).toEqual(['/models/tom-ford-clear.glb']);
  await page.locator('#record-turn').click();
  await expect(page.locator('#capture')).toHaveAttribute('data-state', 'recording');
  await expect(selection).toBeDisabled();
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(3);
  const count = await finishRecording(page);
  const stopped = await resources(page);
  await expect(selection).toBeDisabled();
  await expect(selection).toHaveValue('tom-ford-clear');
  await expect(page.locator('#eyewear-hint')).toContainText(/discard.*capture/i);
  await page.locator('#replay-frame').focus();
  await page.locator('#replay-frame').press('End');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', String(count - 1));
  await page.locator('#mirror').screenshot({ path: test.info().outputPath('tom-ford-clear-replay.png') });

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-capture').click();
  const download = await downloadPromise;
  const capturePath = test.info().outputPath('clear-lens-synthetic-capture.json');
  await download.saveAs(capturePath);
  const artifact = JSON.parse(await readFile(capturePath, 'utf8')) as {
    header: Record<string, unknown> & { eyewear: { id: string } }; frames: ExportedFrame[];
  };
  expect(artifact.header.eyewear.id).toBe('tom-ford-clear');
  await expectTempleHeader(page, artifact.header, -0.110);
  expect(artifact.frames).toHaveLength(count);
  await expectRawShapeCaptures(page, artifact.frames);
  const delivered = await page.evaluate(() =>
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.results);
  for (const frame of artifact.frames) {
    expect(frame.metadata).not.toBeNull();
    expect(frame.metadata!.eyewearModelId).toBe('tom-ford-clear');
    expect(frame.metadata!.rawMatrix).toEqual(frame.detection.matrix);
    expect(frame.metadata!.surfacePositions).toHaveLength(1404);
    expect(frame.detection).toEqual(delivered.find(result => result.capturedAt === frame.capturedAt)?.detection);
  }
  expect(await resources(page)).toEqual(stopped);
  expect([...new Set(models)]).toEqual(['/models/tom-ford-clear.glb']);
  await page.locator('#discard-capture').click();
  await expect(selection).toBeEnabled();
  await expect(page.locator('#eyewear-hint')).toContainText(/choose.*before/i);
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await selection.selectOption('amber-horizon');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('#frame-name')).toHaveText('Amber Horizon');
  await expect(selection).toBeDisabled();
  expect([...new Set(models)]).toEqual(['/models/tom-ford-clear.glb', '/models/amber-horizon.glb']);
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(selection).toBeEnabled();
  expect(errors).toEqual([]);
});

test('failed clear-lens asset releases selection and permits a successful retry', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await installCamera(page);
  await page.route('**/models/tom-ford-clear.glb', route => route.fulfill({ status: 404, body: 'Missing clear frame' }));
  await page.goto('/experiments/temple-sagittal/live.html');
  const selection = page.locator('#eyewear-select');
  await selection.selectOption('tom-ford-clear');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'error');
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect((await resources(page)).workers).toBe(0);
  await expect(selection).toBeEnabled();
  await expect(selection).toHaveValue('tom-ford-clear');
  await expect(page.locator('#mirror')).toBeHidden();
  await page.unroute('**/models/tom-ford-clear.glb');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(selection).toBeDisabled();
  await expect(page.locator('#frame-name')).toHaveText('Tom Ford');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(selection).toBeEnabled();
  expect(errors).toEqual([]);
});

test('cancelled clear-lens startup cannot publish into the next Amber session', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await installCamera(page);
  await page.addInitScript(() => {
    const state = { held: false, delivered: false, release: () => {} };
    const gate = new Promise<void>(resolve => { state.release = resolve; });
    Object.assign(window, { assetHarness: state });
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await nativeFetch(input, init);
      const url = input instanceof Request ? input.url : String(input);
      if (!new URL(url, location.href).pathname.endsWith('/tom-ford-clear.glb')) return response;
      // Buffer a real asset first, then simulate a dependency that resolves late
      // despite cancellation. The following session must retain its own model.
      const bytes = await response.arrayBuffer();
      state.held = true;
      await gate;
      state.delivered = true;
      return new Response(bytes, { status: response.status, headers: response.headers });
    };
  });
  await page.goto('/experiments/temple-sagittal/live.html');
  const selection = page.locator('#eyewear-select');
  await selection.selectOption('tom-ford-clear');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { assetHarness: { held: boolean } }).assetHarness.held)).toBe(true);
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'starting');
  await expect(selection).toBeDisabled();
  const oldCanvas = await page.locator('#mirror').elementHandle();
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(selection).toBeEnabled();
  await expect(page.locator('#mirror')).toBeHidden();
  await selection.selectOption('amber-horizon');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  const activeWorkers = (await resources(page)).workers;
  const beforeRelease = Number(await page.locator('.stage').getAttribute('data-presented-at'));
  await page.evaluate(() =>
    (window as unknown as { assetHarness: { release(): void } }).assetHarness.release());
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { assetHarness: { delivered: boolean } }).assetHarness.delivered)).toBe(true);
  await expect.poll(async () => Number(await page.locator('.stage').getAttribute('data-presented-at')))
    .toBeGreaterThan(beforeRelease);
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(selection).toHaveValue('amber-horizon');
  await expect(selection).toBeDisabled();
  await expect(page.locator('#frame-name')).toHaveText('Amber Horizon');
  await expect(page.locator('#mirror')).toBeVisible();
  expect((await resources(page)).workers).toBe(activeWorkers);
  expect(await oldCanvas!.evaluate(canvas => canvas.isConnected)).toBe(false);
  await oldCanvas!.dispose();
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(selection).toBeEnabled();
  expect(errors).toEqual([]);
});
