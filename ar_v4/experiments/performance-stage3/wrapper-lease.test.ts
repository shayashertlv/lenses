import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {test} from 'node:test';
import {GpuNativeFrame} from './native/gpu-frame.ts';
import type {NativeGpuFrames} from './native/gpu-frame.ts';

// Exercise the real wrapper. Only the two expensive renderer modules are
// substituted; no WebGL appearance or performance claim is made by this test.
const nativeUrl = new URL('./native/renderer.ts', import.meta.url).href;
const branchUrl = new URL('../performance-candidate/temples/branch-renderer.ts', import.meta.url).href;
const hooks = registerHooks({load(url, context, nextLoad) {
  if (url === nativeUrl || url === branchUrl)
    return {format: 'module', shortCircuit: true, source: 'export class TryOnRenderer {}'};
  return nextLoad(url, context);
}});
const {TryOnRenderer} = await import('./temples/renderer.ts');
hooks.deregister();

class TestCanvas extends EventTarget {
  width = 8;
  height = 6;
  clears = 0;
  getContext(kind: string) {
    if (kind === 'webgl2') return {SAMPLES: 1, getParameter: () => 4};
    if (kind === '2d') return {
      drawImage: () => {},
      clearRect: () => {this.clears++;},
      putImageData: () => {throw new Error('This GPU ownership test must not publish CPU pixels.');},
    };
    return null;
  }
}

test('a rejected wrapper presentation revokes the previously borrowed GPU frame and permits a fresh session frame', t => {
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  Object.defineProperty(globalThis, 'document', {configurable: true,
    value: {createElement: () => new TestCanvas()}});
  t.after(() => {
    if (previousDocument) Object.defineProperty(globalThis, 'document', previousDocument);
    else Reflect.deleteProperty(globalThis, 'document');
  });

  const display = new TestCanvas(), nativeCanvas = new TestCanvas(), branchCanvas = new TestCanvas();
  const source = new TestCanvas();
  let generation = 0, active = false, disposed = false, nativePresents = 0;
  let current: GpuNativeFrame | null = null;
  const diagnostic = {width: source.width, height: source.height,
    data: new Uint8ClampedArray(source.width * source.height * 4)};
  // The native boundary supplies a revocable lease. Use the actual public lease
  // class and assert its observable operations, rather than counting invalidation
  // method calls or inspecting the wrapper's private fields.
  const owner = {
    assertLease(value: number) {
      if (disposed || !active || value !== generation) throw new Error('The native GPU frame lease is stale or unavailable.');
    },
    presentToCanvas: () => nativeCanvas as unknown as HTMLCanvasElement,
    readDiagnostic: () => diagnostic as ImageData,
  };
  const native = {
    get gpuFrame() {return current;},
    nativeImagePair: null,
    captureSnapshot: null,
    stageTimings: {gpu: null},
    invalidateGpuFrame() {generation++; active = false; current = null;},
    present() {
      nativePresents++;
      this.invalidateGpuFrame();
      active = true;
      current = new GpuNativeFrame(owner as unknown as NativeGpuFrames,
        generation, source.width, source.height, false);
      return false; // No-face beauty still owns a current, publishable GPU image.
    },
    dispose() {disposed = true; this.invalidateGpuFrame();},
  };
  const branch = {clearOwnedFrame() {}, dispose() {}};
  const renderer = Reflect.construct(TryOnRenderer,
    [display, nativeCanvas, branchCanvas, native, branch, {publish: false}]) as Awaited<ReturnType<typeof TryOnRenderer.create>>;
  t.after(() => renderer.dispose());
  const detection = {landmarks: [], matrix: null, inferenceMs: 0};
  const present = (frame: TestCanvas) => renderer.present(frame as unknown as HTMLCanvasElement, detection);
  const getLease = (): GpuNativeFrame | null => renderer.gpuFrame;

  assert.equal(present(source), false);
  const retained = renderer.gpuFrame;
  assert.ok(retained);
  assert.equal(retained.presentToCanvas('beauty'), nativeCanvas);
  assert.equal(retained.readDiagnostic('beauty'), diagnostic);
  assert.equal(nativePresents, 1);

  const empty = new TestCanvas(); empty.width = 0;
  assert.throws(() => present(empty), /paired camera frame is empty/);
  assert.equal(nativePresents, 1, 'the rejected image never reaches native present');
  assert.equal(renderer.gpuFrame, null);
  assert.equal(renderer.ownedPixels, null);
  assert.equal(renderer.captureSnapshot, null);
  assert.equal(renderer.diagnostics, null);
  assert.throws(() => retained.presentToCanvas('beauty'), /lease is stale or unavailable/,
    'a consumer retaining the old lease cannot republish the rejected session image');
  assert.throws(() => retained.readDiagnostic('beauty'), /lease is stale or unavailable/,
    'a stale lease cannot export old pixels after the wrapper clears its ownership');
  assert.ok(display.clears > 0);

  assert.equal(present(source), false);
  const recovered = getLease();
  assert.ok(recovered);
  assert.notEqual(recovered, retained);
  assert.equal(recovered.presentToCanvas('beauty'), nativeCanvas);
  assert.throws(() => retained.readDiagnostic('beauty'), /lease is stale or unavailable/,
    'issuing a new lease never revives a retained old lease');
  renderer.dispose();
  assert.equal(renderer.gpuFrame, null);
  assert.throws(() => recovered.presentToCanvas('beauty'), /lease is stale or unavailable/);
});
