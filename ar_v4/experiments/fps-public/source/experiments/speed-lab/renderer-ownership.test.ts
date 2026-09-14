import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {test} from 'node:test';
import type {TestContext} from 'node:test';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
import {createOwnedSourceFrame, DEFAULT_SPEED_OPTIONS} from './speed-options.ts';
import type {NativeSpeedOptions, OwnedSourceFrame} from './speed-options.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import type {HairMask} from './renderer.ts';

// Load both real outer pipelines. The native temple boundary alone is replaced;
// composition, continuity, guard checks, Hold and export run unchanged.
const rendererUrls = new Set([
  new URL('./renderer.ts', import.meta.url).href,
  new URL('../performance-stage2/renderer.ts', import.meta.url).href,
]);
const lowerUrls = new Set([
  new URL('./temples/renderer.ts', import.meta.url).href,
  new URL('../performance-stage2/temples/renderer.ts', import.meta.url).href,
]);
const hooks = registerHooks({load(url, context, nextLoad) {
  if (lowerUrls.has(url)) return {format: 'module', shortCircuit: true, source: 'export class TryOnRenderer {}'};
  if (rendererUrls.has(url)) {
    const compiled = transformSync(fileURLToPath(url), readFileSync(new URL(url), 'utf8'));
    if (compiled.errors.length) throw new Error(JSON.stringify(compiled.errors));
    return {format: 'module', shortCircuit: true, source: compiled.code};
  }
  return nextLoad(url, context);
}});
const {LiveHairRenderer} = await import('./renderer.ts');
const {LiveHairRenderer: Test2Renderer} = await import('../performance-stage2/renderer.ts');
hooks.deregister();

class TestImageData {
  readonly data: Uint8ClampedArray<ArrayBuffer>;
  readonly width: number; readonly height: number; readonly colorSpace: PredefinedColorSpace;
  constructor(data: Uint8ClampedArray<ArrayBuffer>, width: number, height: number, options: ImageDataSettings = {}) {
    this.data = data; this.width = width; this.height = height; this.colorSpace = options.colorSpace ?? 'srgb';
  }
}
class TestCanvas extends EventTarget {
  private w = 20; private h = 16;
  pixels = new Uint8ClampedArray(this.w * this.h * 4);
  private opaque = false;
  draws = 0; publishes = 0; clears = 0;
  get width() {return this.w;}
  set width(value: number) {this.w = value; this.resetPixels();}
  get height() {return this.h;}
  set height(value: number) {this.h = value; this.resetPixels();}
  private resetPixels() {
    this.pixels = new Uint8ClampedArray(this.w * this.h * 4);
    if (this.opaque) for (let offset = 3; offset < this.pixels.length; offset += 4) this.pixels[offset] = 255;
  }
  getContext(_kind?: string, options?: {alpha?: boolean}) {
    if (options?.alpha === false && !this.opaque) {
      this.opaque = true;
      for (let offset = 3; offset < this.pixels.length; offset += 4) this.pixels[offset] = 255;
    }
    return {
      drawImage: (source: TestCanvas) => {
        this.draws++;
        for (let offset = 0; offset < this.pixels.length; offset += 4) {
          const alpha = source.pixels[offset + 3]! / 255;
          for (let channel = 0; channel < 3; channel++) this.pixels[offset + channel] = Math.round(
            source.pixels[offset + channel]! * alpha + this.pixels[offset + channel]! * (1 - alpha));
          this.pixels[offset + 3] = this.opaque ? 255 : Math.round(source.pixels[offset + 3]! + this.pixels[offset + 3]! * (1 - alpha));
        }
      },
      getImageData: (_x: number, _y: number, width: number, height: number) => new ImageData(this.pixels.slice(), width, height, {colorSpace: 'srgb'}),
      putImageData: (image: ImageData) => {this.publishes++; this.pixels.set(image.data);},
      clearRect: () => {this.clears++; this.pixels.fill(0);},
    };
  }
  toDataURL() {return `data:image/png;base64,${Buffer.from(this.pixels).toString('base64')}`;}
}

class NativeBoundary {
  calls = 0; asyncCalls = 0; disposals = 0; fail = false; missingPixels = false;
  cameraRequested = true; requested: Readonly<NativeSpeedOptions> = DEFAULT_SPEED_OPTIONS;
  receivedSource: OwnedSourceFrame | undefined;
  receivedCanvas: TestCanvas | null = null;
  owned: ImageData | null = null; camera: ImageData | null = null;
  readonly nativeSamples = 4;
  readonly eyewear = {id: 'amber-horizon', offsetCm: [0, 0, 0]};
  wait: Promise<void> | null = null;
  visible = false;
  additionalReads = {prewarmReadbackCalls: 0, prewarmReadbackBytes: 0, retrievedCalls: 0, retrievedBytes: 0, queuedCalls: 0, queuedBytes: 0};
  setPreparationHairEnabled(value: boolean) {this.cameraRequested = value;}
  setFrameOptions(options: NativeSpeedOptions, source?: OwnedSourceFrame) {this.requested = {...options}; this.receivedSource = source;}
  present(frame: TestCanvas, detection: Detection) {
    this.calls++; if (this.fail) throw new Error('Injected native failure.');
    this.receivedCanvas = frame; this.visible = detection.matrix !== null;
    const before = frame.pixels.slice();
    if (this.visible) for (const [x, y] of [[1, 8], [10, 3], [10, 8], [18, 9]]) before[(y! * frame.width + x!) * 4] = 220;
    this.owned = new ImageData(before, frame.width, frame.height);
    this.camera = this.visible && this.cameraRequested ? new ImageData(frame.pixels.slice(), frame.width, frame.height) : null;
    return this.visible;
  }
  async presentAsync(frame: TestCanvas, detection: Detection) {
    this.asyncCalls++; if (this.wait) await this.wait;
    if (this.disposals) throw new DOMException('Native owner closed.', 'AbortError');
    return this.present(frame, detection);
  }
  get ownedPixels() {return this.missingPixels ? null : this.owned;}
  get ownedCameraPixels() {return this.camera;}
  get stageTimings() {return {baselineReadbackCalls: 0, branchReadbackCalls: 0, baselineReadbackBytes: 0, branchReadbackBytes: 0,
    speedLab: {prewarmReadbackCalls: this.additionalReads.prewarmReadbackCalls, prewarmReadbackBytes: this.additionalReads.prewarmReadbackBytes},
    native: {sharedCameraReady: this.camera !== null, sharedCameraFailure: null,
      speedLab: {pbo: {retrievedCalls: this.additionalReads.retrievedCalls, retrievedBytes: this.additionalReads.retrievedBytes,
        queuedCalls: this.additionalReads.queuedCalls, queuedBytes: this.additionalReads.queuedBytes}},
      sharedReadback: {readbackCalls: this.camera ? 2 : 0, readbackBytes: (this.owned?.data.byteLength ?? 0) * 2}}};}
  get captureSnapshot() {return this.visible && this.owned ? {
    eyewearMatrix: Array.from({length: 16}, (_, index) => Number(index % 5 === 0)),
    surfacePositions: new Float32Array(468 * 3), rearDrop: {method: 'temple-rear-drop-v1', dropM: 0},
    protection: {method: 'temple-optics-copy-v1', width: this.owned.width, height: this.owned.height, marginPx: 4,
      protectedRects: [{x0: 2, y0: 2, x1: 18, y1: 5}, {x0: 8, y0: 6, x1: 12, y1: 10}],
      editableRects: [{x0: 0, y0: 6, x1: 4, y1: 14}, {x0: 16, y0: 6, x1: 20, y1: 14}]},
  } : null;}
  get diagnostics() {return {method: 'bounded native substitute'};}
  dispose() {this.disposals++; this.owned = this.camera = null; this.visible = false;}
}

function fixture(t: TestContext) {
  const documentBefore = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const imageBefore = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: () => new TestCanvas()}});
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: TestImageData});
  t.after(() => {
    if (documentBefore) Object.defineProperty(globalThis, 'document', documentBefore); else Reflect.deleteProperty(globalThis, 'document');
    if (imageBefore) Object.defineProperty(globalThis, 'ImageData', imageBefore); else Reflect.deleteProperty(globalThis, 'ImageData');
  });
  const source = new TestCanvas(), display = new TestCanvas(), native = new NativeBoundary();
  for (let index = 0; index < source.pixels.length; index++) source.pixels[index] = index % 4 === 3 ? 255 : 37 + index % 31;
  const renderer = Reflect.construct(LiveHairRenderer, [display, new TestCanvas(), native]) as Awaited<ReturnType<typeof LiveHairRenderer.create>>;
  t.after(() => renderer.dispose());
  const detection: Detection = {landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})),
    matrix: Array.from({length: 16}, (_, index) => Number(index % 5 === 0)), inferenceMs: 1};
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: 'amber-horizon'};
  const model = {id: 'hair-only', sha256: '3'.repeat(64), labels: ['background', 'hair'], hairIndex: 1};
  const mask: HairMask = {...pair, outputMode: 'category-only', model: model.id, modelSHA256: model.sha256,
    categorySHA256: '4'.repeat(64), labels: model.labels.slice(), hairIndex: 1,
    width: source.width, height: source.height, category: new Uint8Array(source.width * source.height).fill(1)};
  const input = source as unknown as HTMLCanvasElement;
  const claim = (generation = 1, isCurrent = () => true) => createOwnedSourceFrame(input,
    new ImageData(source.pixels.slice(), source.width, source.height, {colorSpace: 'srgb'}),
    {sourceSHA256: pair.sourceSHA256, sessionId: 'session-A', generation, isCurrent});
  return {renderer, native, source, input, display, detection, pair, model, mask, claim, stats: () => renderer.stats};
}

test('all switches off preserve actual Test2 composition, continuity, checks, source snapshots and mask ownership', async t => {
  const f = fixture(t), referenceNative = new NativeBoundary(), referenceDisplay = new TestCanvas();
  const reference = Reflect.construct(Test2Renderer, [referenceDisplay, new TestCanvas(), referenceNative]) as Awaited<ReturnType<typeof Test2Renderer.create>>;
  t.after(() => reference.dispose());
  reference.present(f.input, f.detection, f.mask, f.pair, f.model);
  await f.renderer.present(f.input, f.detection, f.mask, f.pair, f.model);
  assert.equal(f.renderer.stats?.hasMask, true, f.renderer.stats?.fallbackReason ?? 'The paired mask must be accepted.');
  assert.ok(f.renderer.stats!.changedPixels > 0, 'fixture exercises actual eligible replacement');
  assert.deepEqual(f.display.pixels, referenceDisplay.pixels);
  for (const key of ['statistics', 'continuity', 'backgroundReferenceCheck', 'protectedCheck', 'noseCheck',
    'outsideEditableCheck', 'backgroundPreservationCheck'] as const) assert.deepEqual(f.renderer.stats![key], reference.stats![key]);
  assert.deepEqual(f.native.requested, DEFAULT_SPEED_OPTIONS);
  assert.equal(f.native.asyncCalls, 0);
  assert.notEqual(f.native.receivedCanvas, f.source);
  const saved = f.renderer.exportDiagnostic()!;
  f.mask.category.fill(0); f.source.pixels.fill(7); f.detection.landmarks[0]!.x = .1;
  const later = f.renderer.exportDiagnostic()!;
  assert.deepEqual(later.mask, saved.mask); assert.deepEqual(later.detection, saved.detection);
  assert.equal(later.sourcePngDataUrl, saved.sourcePngDataUrl);
  assert.equal(later.hairAccepted, false); assert.match(String(later.candidateRevisionLabel), /Speed lab/);
});

test('borrowed live source becomes an independent exact Hold/export after packet canvas release', async t => {
  const f = fixture(t), expected = f.source.pixels.slice(); let current = true;
  const source = f.claim(1, () => current), options = {fewerCopies: true, reuseSourcePixels: true};
  await f.renderer.prepare(f.input, f.detection, f.pair, f.model, {source, options});
  assert.equal(f.native.receivedCanvas, f.source);
  assert.equal(f.renderer.stats, null); assert.equal(f.renderer.copyHeldInput(), null); assert.equal(f.display.publishes, 0);
  options.fewerCopies = false;
  f.renderer.finish(f.mask); const displayed = f.display.pixels.slice();
  assert.equal(f.renderer.stats!.candidatePerformance.speedLab.options.fewerCopies, true);
  assert.equal(f.renderer.stats!.candidatePerformance.speedLab.sourceCopyBytesAvoided, expected.byteLength);
  current = false; f.source.width = f.source.height = 0;
  const held = f.renderer.copyHeldInput()!;
  assert.deepEqual((held.source as unknown as TestCanvas).pixels, expected);
  (held.source as unknown as TestCanvas).pixels.fill(17); held.detection.landmarks[0]!.x = .9;
  assert.deepEqual((f.renderer.copyHeldInput()!.source as unknown as TestCanvas).pixels, expected);
  assert.equal(f.renderer.copyHeldInput()!.detection.landmarks[0]!.x, .5);
  assert.equal(f.renderer.exportDiagnostic()!.sourcePngDataUrl, `data:image/png;base64,${Buffer.from(expected).toString('base64')}`);
  f.renderer.selectVariant('accepted'); f.renderer.selectVariant('hair');
  assert.deepEqual(f.display.pixels, displayed, 'released live source does not invalidate completed variants');
});

test('transparent borrowing fallback preserves Test2 source-over history, including an earlier failed opaque presentation', async t => {
  const f = fixture(t), referenceNative = new NativeBoundary(), referenceDisplay = new TestCanvas();
  const reference = Reflect.construct(Test2Renderer, [referenceDisplay, new TestCanvas(), referenceNative]) as Awaited<ReturnType<typeof Test2Renderer.create>>;
  t.after(() => reference.dispose());
  const fill = (rgba: readonly number[]): void => {
    for (let offset = 0; offset < f.source.pixels.length; offset += 4) f.source.pixels.set(rgba, offset);
  };
  const options = {fewerCopies: true, reuseSourcePixels: true};
  fill([255, 0, 0, 255]);
  reference.present(f.input, f.detection, null, f.pair, f.model);
  await f.renderer.present(f.input, f.detection, null, f.pair, f.model, {options, source: f.claim(1)});
  assert.equal(f.stats()!.candidatePerformance.speedLab.sourceCanvasBorrowed, true);
  // Do not request Hold/export here: doing so would materialize the deferred
  // source early and conceal a broken next-frame source-over history.
  fill([0, 0, 255, 128]);
  reference.present(f.input, f.detection, null, f.pair, f.model);
  await f.renderer.present(f.input, f.detection, null, f.pair, f.model, {options, source: f.claim(2)});
  assert.deepEqual(f.display.pixels, referenceDisplay.pixels);
  assert.equal(f.stats()!.candidatePerformance.speedLab.sourceCanvasBorrowed, false);
  assert.match(f.stats()!.candidatePerformance.speedLab.sourceFallbackReason!, /not fully opaque/);
  assert.equal(f.native.receivedCanvas!.pixels[3], 255, 'the native camera receives the normalized canvas');
  assert.equal(f.native.receivedSource!.rgba.data[3], 128, 'raw RGBA remains ineligible for native source reuse');
  assert.deepEqual([...f.native.receivedCanvas!.pixels.slice(0, 4)], [127, 0, 128, 255]);
  fill([0, 255, 0, 255]); f.native.fail = referenceNative.fail = true;
  assert.throws(() => reference.present(f.input, f.detection, null, f.pair, f.model), /Injected native failure/);
  await assert.rejects(f.renderer.present(f.input, f.detection, null, f.pair, f.model, {options, source: f.claim(3)}), /Injected native failure/);
  f.native.fail = referenceNative.fail = false; fill([0, 0, 255, 128]);
  reference.present(f.input, f.detection, null, f.pair, f.model);
  await f.renderer.present(f.input, f.detection, null, f.pair, f.model, {options, source: f.claim(4)});
  assert.deepEqual(f.display.pixels, referenceDisplay.pixels);
  assert.deepEqual([...f.native.receivedCanvas!.pixels.slice(0, 4)], [0, 127, 128, 255]);
  const diagnostic = f.renderer.exportDiagnostic()!, held = f.renderer.copyHeldInput()!;
  assert.equal(diagnostic.sourcePngDataUrl, reference.exportDiagnostic()!.sourcePngDataUrl);
  assert.deepEqual((held.source as unknown as TestCanvas).pixels, (reference.copyHeldInput()!.source as unknown as TestCanvas).pixels);
});

test('an opaque borrowed resize followed by transparent input resets the logical canvas instead of reviving older dimensions', async t => {
  const f = fixture(t), referenceNative = new NativeBoundary(), referenceDisplay = new TestCanvas();
  const reference = Reflect.construct(Test2Renderer, [referenceDisplay, new TestCanvas(), referenceNative]) as Awaited<ReturnType<typeof Test2Renderer.create>>;
  t.after(() => reference.dispose());
  const run = async (generation: number, width: number, height: number, rgba: readonly number[], borrow: boolean): Promise<void> => {
    f.source.width = width; f.source.height = height;
    for (let offset = 0; offset < f.source.pixels.length; offset += 4) f.source.pixels.set(rgba, offset);
    reference.present(f.input, f.detection, null, f.pair, f.model);
    await f.renderer.present(f.input, f.detection, null, f.pair, f.model,
      {options: {fewerCopies: borrow}, ...(borrow ? {source: f.claim(generation)} : {})});
    assert.deepEqual(f.display.pixels, referenceDisplay.pixels);
  };
  await run(1, 20, 16, [255, 0, 0, 255], false);
  await run(2, 24, 18, [0, 255, 0, 255], true);
  await run(3, 20, 16, [0, 0, 255, 128], true);
  assert.deepEqual([...f.native.receivedCanvas!.pixels.slice(0, 4)], [0, 0, 128, 255]);
});

test('async preparation stays private, snapshots its options, and rejects overlap or early finish', async t => {
  const f = fixture(t); let resolve!: () => void;
  f.native.wait = new Promise<void>(done => {resolve = done;});
  const options = {asyncReadback: true, fewerCopies: true};
  const pending = f.renderer.prepare(f.input, f.detection, f.pair, f.model, {options, source: f.claim()});
  options.asyncReadback = false;
  assert.equal(f.native.asyncCalls, 1); assert.equal(f.native.calls, 0);
  assert.equal(f.renderer.stats, null); assert.equal(f.renderer.captureSnapshot, null); assert.equal(f.renderer.exportDiagnostic(), null);
  f.renderer.selectVariant('accepted'); assert.equal(f.display.publishes, 0);
  assert.throws(() => f.renderer.finish(f.mask), /must settle/);
  assert.throws(() => f.renderer.setFrameOptions({}), /pending pair/);
  await assert.rejects(f.renderer.prepare(f.input, f.detection, f.pair, f.model), /preceding prepared/);
  resolve(); assert.equal(await pending, true);
  assert.equal(f.native.requested.asyncReadback, true);
  f.renderer.finish(f.mask); assert.equal(f.display.publishes, 1); assert.equal(f.native.calls, 1);
  assert.throws(() => f.renderer.finish(f.mask), /no owned prepared/);
});

test('a revoked source during asynchronous preparation or before finish cannot publish and permits a new generation', async t => {
  const f = fixture(t); let resolve!: () => void, active = true;
  f.native.wait = new Promise<void>(done => {resolve = done;});
  const source = f.claim(1, () => active);
  const pending = f.renderer.prepare(f.input, f.detection, f.pair, f.model,
    {options: {asyncReadback: true, fewerCopies: true}, source});
  active = false; resolve(); await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(f.display.publishes, 0); assert.equal(f.renderer.stats, null); assert.equal(f.renderer.copyHeldInput(), null);
  f.native.wait = null; active = true;
  await assert.rejects(f.renderer.prepare(f.input, f.detection, f.pair, f.model,
    {options: {fewerCopies: true}, source}), /generation was already consumed/);
  await f.renderer.prepare(f.input, f.detection, f.pair, f.model,
    {options: {fewerCopies: true}, source: f.claim(2, () => active)});
  active = false;
  assert.throws(() => f.renderer.finish(f.mask), {name: 'AbortError'});
  assert.equal(f.display.publishes, 0); assert.equal(f.renderer.stats, null);
  await f.renderer.present(f.input, f.detection, f.mask, f.pair, f.model,
    {options: {fewerCopies: true}, source: f.claim(3)});
  assert.equal(f.display.publishes, 1); assert.equal(f.stats()?.hasMask, true);
});

test('disposal during async readback prevents late publication and invalidates all public snapshots', async t => {
  const f = fixture(t); let resolve!: () => void;
  f.native.wait = new Promise<void>(done => {resolve = done;});
  const pending = f.renderer.prepare(f.input, f.detection, f.pair, f.model, {options: {asyncReadback: true}});
  f.renderer.dispose(); f.renderer.dispose(); resolve();
  await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(f.native.disposals, 1); assert.equal(f.display.publishes, 0);
  assert.equal(f.renderer.stats, null); assert.equal(f.renderer.captureSnapshot, null); assert.equal(f.renderer.copyHeldInput(), null);
  assert.equal(f.renderer.exportDiagnostic(), null); assert.equal(f.renderer.finish(f.mask), false);
});

test('async convenience presentation snapshots its mask and expected model before awaiting native bytes', async t => {
  const f = fixture(t); let resolve!: () => void;
  f.native.wait = new Promise<void>(done => {resolve = done;});
  const category = f.mask.category.slice(), model = structuredClone(f.model);
  const pending = f.renderer.present(f.input, f.detection, f.mask, f.pair, f.model, {options: {asyncReadback: true}});
  f.mask.category.fill(0); (f.mask.labels as string[])[1] = 'changed'; f.model.sha256 = 'f'.repeat(64);
  f.model.labels[1] = 'changed'; resolve();
  await pending;
  assert.equal(f.stats()?.hasMask, true);
  const diagnostic = f.renderer.exportDiagnostic()!;
  assert.deepEqual(diagnostic.expectedModel, model);
  const exportedMask = diagnostic.mask as {categoryBase64: string; labels: string[]};
  assert.deepEqual(new Uint8Array(Buffer.from(exportedMask.categoryBase64, 'base64')), category);
  assert.deepEqual(exportedMask.labels, model.labels);
});

test('CPU transfer totals include hidden prewarm and actual PBO retrieval, excluding queued-only reads', async t => {
  const f = fixture(t), byteLength = f.source.pixels.byteLength;
  // A failed first retrieval followed by synchronous fallback still costs one
  // CPU transfer; a queued second slot that never reached CPU must not be counted.
  f.native.additionalReads = {prewarmReadbackCalls: 1, prewarmReadbackBytes: byteLength,
    retrievedCalls: 1, retrievedBytes: byteLength, queuedCalls: 2, queuedBytes: byteLength * 2};
  await f.renderer.present(f.input, f.detection, f.mask, f.pair, f.model);
  const performance = f.stats()!.candidatePerformance;
  assert.equal(performance.cpuReadbackCalls, 4);
  assert.equal(performance.cpuReadbackBytes, byteLength * 4);
});

test('wrong source identity and native failures clear the pair; missing source uses explicit unchanged fallback', async t => {
  const f = fixture(t), source = f.claim();
  await assert.rejects(f.renderer.prepare(f.input, f.detection, {...f.pair, sourceSHA256: '9'.repeat(64)}, f.model,
    {options: {reuseSourcePixels: true}, source}), /another paired image/);
  assert.equal(f.native.calls, 0);
  f.native.fail = true;
  await assert.rejects(f.renderer.prepare(f.input, f.detection, f.pair, f.model), /Injected native failure/);
  assert.equal(f.renderer.stats, null); assert.equal(f.display.publishes, 0);
  f.native.fail = false; f.native.missingPixels = true;
  await assert.rejects(f.renderer.prepare(f.input, f.detection, f.pair, f.model), /no owned pixels/);
  f.native.missingPixels = false;
  await f.renderer.present(f.input, f.detection, f.mask, f.pair, f.model, {options: {reuseSourcePixels: true, fewerCopies: true}});
  assert.equal(f.stats()?.hasMask, true);
  assert.equal(f.stats()!.candidatePerformance.speedLab.sourceCanvasBorrowed, false);
  assert.match(f.stats()!.candidatePerformance.speedLab.sourceFallbackReason!, /No owned source/);
  await f.renderer.present(f.input, {matrix: null, landmarks: [], inferenceMs: 0}, null, f.pair, f.model);
  assert.equal(f.stats()!.hasFace, false); assert.equal(f.renderer.captureSnapshot, null); assert.equal(f.renderer.copyHeldInput(), null);
});
