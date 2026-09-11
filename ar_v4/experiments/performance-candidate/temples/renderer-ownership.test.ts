import assert from 'node:assert/strict';
import {test} from 'node:test';
import {TryOnRenderer} from './renderer.ts';
import {Box3, Matrix4, Vector3} from 'three';
import {eyewearById} from '../../../references/perfect-temples/src/render/eyewear.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import type {CaptureGeometry} from '../native/renderer.ts';

class TestImageData {
  readonly data: Uint8ClampedArray;
  readonly width: number;
  readonly height: number;
  constructor(data: Uint8ClampedArray, width: number, height: number) { this.data = data; this.width = width; this.height = height; }
}

/** Pixel-only fake: exercises ownership/fallback, never claims WebGL appearance. */
class TestCanvas {
  private w = 64;
  private h = 48;
  pixels = new Uint8ClampedArray(64 * 48 * 4);
  get width() { return this.w; }
  set width(value: number) { this.w = value; this.pixels = new Uint8ClampedArray(this.w * this.h * 4); }
  get height() { return this.h; }
  set height(value: number) { this.h = value; this.pixels = new Uint8ClampedArray(this.w * this.h * 4); }
  nativeReads = 0;
  publishes = 0;
  getContext(kind: string) {
    if (kind === '2d') return {
      drawImage: (source: TestCanvas) => { this.pixels.set(source.pixels); },
      getImageData: () => new TestImageData(this.pixels.slice(), this.w, this.h),
      putImageData: (image: TestImageData) => { this.publishes++; this.pixels.set(image.data); },
      clearRect: () => { this.pixels.fill(0); },
    };
    if (kind === 'webgl2') return {
      SAMPLES: 1, RGBA: 2, UNSIGNED_BYTE: 3, getParameter: () => 4, isContextLost: () => false,
      readPixels: (_x: number, _y: number, width: number, height: number, _format: number, _type: number, out: Uint8Array) => {
        this.nativeReads++;
        for (let y = 0; y < height; y++) out.set(this.pixels.subarray(y * width * 4, (y + 1) * width * 4), (height - y - 1) * width * 4);
      },
    };
    return null;
  }
}

test('a no-face branch exception preserves the current authoritative pixels, clears candidate metadata, and permits restart/disposal', t => {
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const priorImageData = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  const ownedCanvases: TestCanvas[] = [];
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: () => {
    const canvas = new TestCanvas(); ownedCanvases.push(canvas); return canvas;
  }}});
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: TestImageData});
  t.after(() => {
    if (priorDocument) Object.defineProperty(globalThis, 'document', priorDocument); else Reflect.deleteProperty(globalThis, 'document');
    if (priorImageData) Object.defineProperty(globalThis, 'ImageData', priorImageData); else Reflect.deleteProperty(globalThis, 'ImageData');
  });
  const display = new TestCanvas(), baselineCanvas = new TestCanvas(), branchCanvas = new TestCanvas(), source = new TestCanvas();
  let failBranch = true, branchCalls = 0, baselineDisposals = 0, branchDisposals = 0;
  const baseline = {
    present: (frame: TestCanvas) => { baselineCanvas.pixels.set(frame.pixels); return false; },
    captureSnapshot: null,
    dispose: () => { baselineDisposals++; },
  };
  const branch = {
    clearOwnedFrame: () => { branchCalls++; if (failBranch) throw new Error('only the branch failed'); },
    dispose: () => { branchDisposals++; },
  };
  const renderer = Reflect.construct(TryOnRenderer, [display, baselineCanvas, branchCanvas, baseline, branch]) as TryOnRenderer;
  t.after(() => renderer.dispose());
  const detection = {landmarks: [], matrix: null, inferenceMs: 0};
  const putSource = (seed: number) => { source.pixels.set(source.pixels.map((_, index) => index % 4 === 3 ? 255 : (seed + index * 7) % 256)); };
  putSource(19);
  assert.equal(renderer.present(source as unknown as HTMLCanvasElement, detection), false);
  assert.deepEqual(display.pixels, source.pixels, 'valid B survives a failing no-face branch clear');
  assert.equal(renderer.captureSnapshot, null);
  assert.equal(renderer.diagnostics?.branchClearFailure, 'only the branch failed');
  assert.equal(renderer.diagnostics?.appliedDropM, 0);
  const ownedFirst = display.pixels.slice();
  source.pixels.fill(9);
  renderer.selectVariant('perfecto'); assert.deepEqual(display.pixels, ownedFirst);
  renderer.selectVariant('candidate'); assert.deepEqual(display.pixels, ownedFirst, 'toggle never rereads caller-owned pixels');
  failBranch = false; putSource(177);
  assert.equal(renderer.present(source as unknown as HTMLCanvasElement, detection), false);
  assert.deepEqual(display.pixels, source.pixels, 'next presentation replaces both fallback buffers');
  assert.notDeepEqual(display.pixels, ownedFirst);
  assert.equal(renderer.diagnostics?.branchClearFailure, null);
  assert.equal(renderer.diagnostics?.sequence, 2);
  renderer.dispose(); renderer.dispose();
  assert.equal(baselineDisposals, 1); assert.equal(branchDisposals, 1);
  assert.equal(renderer.captureSnapshot, null); assert.equal(renderer.diagnostics, null);
  assert.ok(ownedCanvases.every(canvas => canvas.width === 0 && canvas.height === 0));
  assert.equal(renderer.present(source as unknown as HTMLCanvasElement, detection), false);
  assert.equal(branchCalls, 2, 'disposed sessions cannot present again');
});

test('zero-drop pairs keep fresh protection/ownership while skipping branch rendering and private publication', t => {
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const priorImageData = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: () => new TestCanvas()}});
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: TestImageData});
  t.after(() => {
    if (priorDocument) Object.defineProperty(globalThis, 'document', priorDocument); else Reflect.deleteProperty(globalThis, 'document');
    if (priorImageData) Object.defineProperty(globalThis, 'ImageData', priorImageData); else Reflect.deleteProperty(globalThis, 'ImageData');
  });
  const display = new TestCanvas(), baselineCanvas = new TestCanvas(), branchCanvas = new TestCanvas(), source = new TestCanvas();
  const detectionFor = (degrees: number): Detection => {
    const matrix = new Matrix4().makeRotationX(degrees * Math.PI / 180); matrix.setPosition(0, 0, -60);
    return {landmarks: Array.from({length: 478}, (_, i) => ({x: .45 + (i % 2) * .1, y: .43 + (i % 3) * .05, z: 0})), matrix: matrix.toArray(), inferenceMs: 0};
  };
  let baselineSnapshot: CaptureGeometry | null = null, branchSnapshot: CaptureGeometry | null = null;
  let branchPresents = 0, branchClears = 0, currentDrop = 0, failBranch = false, failBaseline = false;
  const eyewear = eyewearById('amber-horizon');
  const baseline = {eyewear,
    present: (frame: TestCanvas, detection: Detection) => {
      if (failBaseline) throw new Error('baseline failed');
      baselineCanvas.pixels.set(frame.pixels);
      baselineSnapshot = detection.matrix ? {eyewearModelId: eyewear.id, rawMatrix: detection.matrix.slice(),
        correctedMatrix: detection.matrix.slice(), eyewearMatrix: detection.matrix.slice(), surfacePositions: new Float32Array(468 * 3),
        yawDegrees: 0, templeClip: null, templeVisibility: null} : null;
      return baselineSnapshot !== null;
    },
    get captureSnapshot() {return baselineSnapshot;}, dispose: () => {},
  };
  const optical = new Box3(new Vector3(-.07, -.025, -.01), new Vector3(.07, .025, .005));
  const originalArms = [new Box3(new Vector3(-.07, -.025, -.11), new Vector3(-.05, .025, -.025)),
    new Box3(new Vector3(.05, -.025, -.11), new Vector3(.07, .025, -.025))];
  const branch = {
    present: () => {
      branchPresents++; if (failBranch) throw new Error('branch failed');
      branchSnapshot = baselineSnapshot ? structuredClone(baselineSnapshot) : null;
      currentDrop = .02; branchCanvas.pixels.set(baselineCanvas.pixels); branchCanvas.pixels[0] = 255;
      return true;
    },
    clearOwnedFrame: () => {branchClears++; branchSnapshot = null; currentDrop = 0;},
    get rearDropBounds() {return {optical, originalArms, candidateArms: originalArms.map(box => box.clone().translate(new Vector3(0, -currentDrop, 0)))};},
    get rearDropDiagnostics() {return {dropM: currentDrop};},
    get captureSnapshot() {return branchSnapshot;}, dispose: () => {},
  };
  const renderer = Reflect.construct(TryOnRenderer, [display, baselineCanvas, branchCanvas, baseline, branch, {publish: false}]) as TryOnRenderer;
  t.after(() => renderer.dispose());
  const present = (degrees: number, seed: number): void => {
    source.pixels.set(source.pixels.map((_, index) => index % 4 === 3 ? 255 : (seed + index * 7) % 251));
    assert.equal(renderer.present(source as unknown as HTMLCanvasElement, detectionFor(degrees)), true);
  };
  present(0, 19);
  assert.equal(branchPresents, 0); assert.equal(branchCanvas.nativeReads, 0);
  assert.equal(renderer.diagnostics?.zeroDropBranchSkipped, true);
  assert.ok(renderer.captureSnapshot?.protection, 'hair still receives validated current-pair protection');
  assert.equal(renderer.captureSnapshot?.rearDrop?.dropM, 0);
  assert.deepEqual(renderer.ownedPixels?.data, source.pixels);
  assert.equal(display.publishes, 0, 'hair borrowing does not publish then read a hidden 2D canvas');
  const initial = renderer.ownedPixels!, initialBytes = initial.data.slice();
  present(30, 35);
  assert.equal(branchPresents, 1); assert.equal(branchCanvas.nativeReads, 1);
  assert.equal(renderer.diagnostics?.zeroDropBranchSkipped, false);
  assert.ok((renderer.captureSnapshot?.rearDrop?.dropM ?? 0) > 0);
  assert.deepEqual(initial.data, initialBytes, 'later presentations never mutate previously borrowed bytes');
  present(0, 97);
  assert.equal(branchPresents, 1); assert.equal(branchCanvas.nativeReads, 1);
  assert.equal(currentDrop, 0); assert.equal(branchSnapshot, null);
  assert.deepEqual(renderer.ownedPixels?.data, source.pixels);
  assert.equal(renderer.captureSnapshot?.rearDrop?.dropM, 0);
  assert.ok(renderer.captureSnapshot?.protection);
  failBranch = true; present(30, 151);
  assert.deepEqual(renderer.ownedPixels?.data, source.pixels, 'branch failure publishes current authoritative bytes');
  assert.equal(renderer.captureSnapshot?.protection, null); assert.equal(renderer.diagnostics?.fallback, 'branch failed');
  failBranch = false; present(0, 41);
  assert.ok(renderer.captureSnapshot?.protection, 'valid zero pair recovers after branch failure');
  assert.equal(renderer.present(source as unknown as HTMLCanvasElement, {landmarks: [], matrix: null, inferenceMs: 0}), false);
  assert.equal(renderer.captureSnapshot, null); assert.equal(branchSnapshot, null);
  failBaseline = true;
  assert.throws(() => present(0, 66), /baseline failed/);
  assert.equal(renderer.ownedPixels, null); assert.equal(renderer.captureSnapshot, null);
  assert.ok(branchClears >= 5);
  renderer.dispose(); assert.equal(renderer.ownedPixels, null);
});
