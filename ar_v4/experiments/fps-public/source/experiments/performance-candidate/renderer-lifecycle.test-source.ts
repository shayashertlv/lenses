// Run with `node experiments/performance-candidate/renderer-lifecycle-test.mjs`.
import test from 'node:test';
import assert from 'node:assert/strict';
import {LiveHairRenderer} from './renderer.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';

/** Canvas/accepted stubs exercise ownership and publication, not native visual equivalence. */
class TestImageData {
  readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
  constructor(data: Uint8ClampedArray, width: number, height: number) { this.data = data; this.width = width; this.height = height; }
}
class TestContext {
  pixels = new Uint8ClampedArray(16).fill(37); publishes = 0; clears = 0;
  drawImage(source: TestCanvas): void { this.pixels = source.context.pixels.slice(); }
  getImageData(_x: number, _y: number, width: number, height: number): TestImageData { return new TestImageData(this.pixels.slice(), width, height); }
  putImageData(value: TestImageData): void { this.pixels = value.data.slice(); this.publishes++; }
  clearRect(): void { this.pixels.fill(0); this.clears++; }
}
class TestCanvas {
  width = 2; height = 2; context = new TestContext();
  getContext(): TestContext { return this.context; }
  toDataURL(): string { return 'data:image/png;base64,owned-test'; }
}
function fixture() {
  const previousDocument = globalThis.document, previousImageData = globalThis.ImageData;
  globalThis.document = {createElement: () => new TestCanvas()} as unknown as Document;
  globalThis.ImageData = TestImageData as unknown as typeof ImageData;
  const restore = (): void => { globalThis.document = previousDocument; globalThis.ImageData = previousImageData; };
  const display = new TestCanvas(), acceptedCanvas = new TestCanvas(), source = new TestCanvas();
  acceptedCanvas.context.getImageData = () => { throw new Error('Hidden accepted pixel readback must be bypassed.'); };
  const accepted = {calls: 0, disposals: 0, fail: false, noOwnedPixels: false, nativeSamples: 4,
    owned: null as TestImageData | null,
    get ownedPixels(): TestImageData | null { return this.noOwnedPixels ? null : this.owned; },
    eyewear: {id: 'amber-horizon'},
    present(frame: TestCanvas, _detection: Detection): boolean {
      this.calls++; if (this.fail) throw new Error('Injected accepted render failure.');
      this.owned = new TestImageData(frame.context.pixels.slice(), frame.width, frame.height); return true;
    },
    captureSnapshot: {eyewearMatrix: Array(16).fill(0), surfacePositions: new Float32Array([1, 2, 3])},
    diagnostics: {method: 'accepted-stub'}, dispose(): void { this.disposals++; },
  };
  const Constructor = LiveHairRenderer as unknown as new (display: HTMLCanvasElement, canvas: HTMLCanvasElement, accepted: unknown) => LiveHairRenderer;
  const renderer = new Constructor(display as unknown as HTMLCanvasElement, acceptedCanvas as unknown as HTMLCanvasElement, accepted);
  const detection: Detection = {matrix: Array.from({length: 16}, (_, index) => Number(index % 5 === 0)), inferenceMs: 0,
    landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0}))};
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: 'amber-horizon'};
  const model = {id: 'hair-only', sha256: '3'.repeat(64), labels: ['background', 'hair'], hairIndex: 1};
  return {renderer, display, source, accepted, detection, pair, model, restore};
}

test('prepare keeps output/metadata private; finish publishes exactly once from its owned source and detection', () => {
  const value = fixture();
  try {
    const {renderer, display, source, accepted, detection, pair, model} = value;
    assert.equal(renderer.prepare(source as unknown as HTMLCanvasElement, detection, pair, model), true);
    assert.equal(accepted.calls, 1); assert.equal(display.context.publishes, 0);
    assert.equal(renderer.stats, null); assert.equal(renderer.captureSnapshot, null);
    assert.equal(renderer.copyHeldInput(), null); assert.equal(renderer.exportDiagnostic(), null);
    renderer.selectVariant('accepted'); assert.equal(display.context.publishes, 0);
    source.context.pixels.fill(99); detection.landmarks[0]!.x = .9; pair.sourceSHA256 = '9'.repeat(64); model.labels[1] = 'changed';
    assert.equal(renderer.finish(null), true);
    assert.equal(accepted.calls, 1); assert.equal(display.context.publishes, 1); assert.equal(display.context.pixels[0], 37);
    assert.equal(renderer.stats!.timings.acceptedReadbackMs, 0);
    assert.equal(renderer.stats!.candidatePerformance.acceptedPixelReadbacksAvoided, 1);
    assert.equal(renderer.stats!.candidatePerformance.acceptedPixelBytesBorrowed, 16);
    const held = renderer.copyHeldInput()!;
    assert.equal(held.detection.landmarks[0]!.x, .5); assert.equal(held.pair.sourceSHA256, '1'.repeat(64));
    assert.equal(held.expectedModel.labels[1], 'hair');
    assert.equal((held.source as unknown as TestCanvas).context.pixels[0], 37);
    held.detection.landmarks[0]!.x = .1; (held.source as unknown as TestCanvas).context.pixels.fill(7);
    assert.equal(renderer.copyHeldInput()!.detection.landmarks[0]!.x, .5);
    assert.equal((renderer.copyHeldInput()!.source as unknown as TestCanvas).context.pixels[0], 37);
    const timings = renderer.stats!.timings;
    for (const duration of Object.values(timings)) assert.ok(Number.isFinite(duration) && duration >= 0);
    assert.equal(timings.workMs, timings.prepareMs + timings.finishMs);
  } finally { value.renderer.dispose(); value.restore(); }
});

test('one pending frame, one final publish, cancellation and synchronous present wrapper preserve lifecycle', () => {
  const value = fixture();
  try {
    const {renderer, source, detection, pair, model, accepted, display} = value;
    const canvas = source as unknown as HTMLCanvasElement;
    renderer.prepare(canvas, detection, pair, model);
    assert.throws(() => renderer.prepare(canvas, detection, pair, model), /preceding prepared/);
    renderer.finish(null); assert.equal(display.context.publishes, 1);
    assert.throws(() => renderer.finish(null), /no owned prepared/); assert.equal(display.context.publishes, 1);
    renderer.present(canvas, detection, null, pair, model);
    assert.equal(accepted.calls, 2); assert.equal(display.context.publishes, 2);
    renderer.prepare(canvas, detection, pair, model); renderer.dispose(); renderer.dispose();
    assert.equal(renderer.finish(null), false); assert.equal(renderer.stats, null); assert.equal(renderer.copyHeldInput(), null);
    assert.equal(display.context.publishes, 2); assert.equal(accepted.disposals, 1);
  } finally { value.renderer.dispose(); value.restore(); }
});

test('failed preparation clears owned pending state and never publishes an unmatched accepted image', () => {
  const value = fixture();
  try {
    const {renderer, source, detection, pair, model, accepted, display} = value;
    accepted.fail = true;
    assert.throws(() => renderer.prepare(source as unknown as HTMLCanvasElement, detection, pair, model), /Injected/);
    assert.equal(renderer.stats, null); assert.equal(renderer.exportDiagnostic(), null); assert.equal(display.context.publishes, 0);
    assert.throws(() => renderer.finish(null), /no owned prepared/);
    accepted.fail = false;
    renderer.present(source as unknown as HTMLCanvasElement, detection, null, pair, model);
    assert.equal(display.context.publishes, 1);
  } finally { value.renderer.dispose(); value.restore(); }
});

test('missing owned native pixels fail closed without reading the hidden display or publishing an old pair', () => {
  const value = fixture();
  try {
    const {renderer, source, detection, pair, model, accepted, display} = value;
    renderer.present(source as unknown as HTMLCanvasElement, detection, null, pair, model);
    accepted.noOwnedPixels = true;
    assert.throws(() => renderer.prepare(source as unknown as HTMLCanvasElement, detection, pair, model), /no owned pixels/);
    assert.equal(renderer.stats, null); assert.equal(renderer.copyHeldInput(), null);
    assert.equal(display.context.publishes, 1);
    accepted.noOwnedPixels = false;
    renderer.present(source as unknown as HTMLCanvasElement, detection, null, pair, model);
    assert.equal(display.context.publishes, 2);
  } finally { value.renderer.dispose(); value.restore(); }
});

test('candidate exports identify the experimental pipeline and retain independently owned held input across frames', () => {
  const value = fixture();
  try {
    const {renderer, source, detection, pair, model} = value;
    renderer.present(source as unknown as HTMLCanvasElement, detection, null, pair, model);
    const held = renderer.copyHeldInput()!;
    const receipt = renderer.exportDiagnostic()!;
    assert.equal(receipt.hairAccepted, false);
    assert.match(String(receipt.candidateRevisionLabel), /Performance candidate/);
    source.context.pixels.fill(99);
    renderer.present(source as unknown as HTMLCanvasElement, detection, null, pair, model);
    assert.equal((held.source as unknown as TestCanvas).context.pixels[0], 37);
    assert.equal((renderer.copyHeldInput()!.source as unknown as TestCanvas).context.pixels[0], 99);
  } finally { value.renderer.dispose(); value.restore(); }
});
