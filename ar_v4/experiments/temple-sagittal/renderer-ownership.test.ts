import assert from 'node:assert/strict';
import {test} from 'node:test';
import {TryOnRenderer} from './renderer.ts';

class TestImageData {
  readonly data: Uint8ClampedArray;
  readonly width: number;
  readonly height: number;
  constructor(data: Uint8ClampedArray, width: number, height: number) { this.data = data; this.width = width; this.height = height; }
}

/** Pixel-only fake: exercises ownership/fallback, never claims WebGL appearance. */
class TestCanvas {
  private w = 4;
  private h = 3;
  pixels = new Uint8ClampedArray(4 * 3 * 4);
  get width() { return this.w; }
  set width(value: number) { this.w = value; this.pixels = new Uint8ClampedArray(this.w * this.h * 4); }
  get height() { return this.h; }
  set height(value: number) { this.h = value; this.pixels = new Uint8ClampedArray(this.w * this.h * 4); }
  getContext(kind: string) {
    if (kind === '2d') return {
      drawImage: (source: TestCanvas) => { this.pixels.set(source.pixels); },
      getImageData: () => new TestImageData(this.pixels.slice(), this.w, this.h),
      putImageData: (image: TestImageData) => { this.pixels.set(image.data); },
      clearRect: () => { this.pixels.fill(0); },
    };
    if (kind === 'webgl2') return {
      SAMPLES: 1, RGBA: 2, UNSIGNED_BYTE: 3, getParameter: () => 4, isContextLost: () => false,
      readPixels: (_x: number, _y: number, width: number, height: number, _format: number, _type: number, out: Uint8Array) => {
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
    present: () => { branchCalls++; if (failBranch) throw new Error('only the branch failed'); return false; },
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
