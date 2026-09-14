import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
import type {Detection} from '../../references/perfect-temples/src/runtime/detector.ts';
import type {HairMask, HairModelContract, PairIdentity, LiveHairRenderer as Renderer} from './renderer.ts';
import type {TempleStageTimings} from './temples/renderer.ts';

// Compile the actual top-level renderer's parameter properties for Node. Only
// the native owner is a fake; the public present/prepare/finish policy is real.
const rendererUrl = new URL('./renderer.ts', import.meta.url).href;
const hook = registerHooks({load(url, context, nextLoad) {
  if (url === rendererUrl) {
    const compiled = transformSync(fileURLToPath(url), readFileSync(new URL(url), 'utf8'));
    if (compiled.errors.length) throw new Error(JSON.stringify(compiled.errors));
    return {format: 'module', shortCircuit: true, source: compiled.code};
  }
  return nextLoad(url, context);
}});
const {LiveHairRenderer} = await import(rendererUrl) as typeof import('./renderer.ts');
hook.deregister();

class PixelData {
  readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
  constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
}
class Canvas {
  width = 2; height = 2; pixels = new Uint8ClampedArray(16).fill(37);
  getContext(kind: string) {
    assert.equal(kind, '2d');
    return {drawImage: (source: Canvas) => {this.pixels = source.pixels.slice();},
      putImageData: (image: PixelData) => {this.pixels = image.data.slice();}, clearRect: () => {this.pixels.fill(0);}};
  }
}

test('direct present with a supplied mask requests both variants even when accepted or explicit false was selected', t => {
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const priorImage = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: () => new Canvas()}});
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: PixelData});
  t.after(() => {
    if (priorDocument) Object.defineProperty(globalThis, 'document', priorDocument); else Reflect.deleteProperty(globalThis, 'document');
    if (priorImage) Object.defineProperty(globalThis, 'ImageData', priorImage); else Reflect.deleteProperty(globalThis, 'ImageData');
  });
  const requests: boolean[] = [];
  const stageTimings: TempleStageTimings = {sourceOwnershipMs: 0, nativePresentMs: 0, baselineReadbackMs: 0,
    branchPresentMs: 0, branchReadbackMs: 0, protectionMs: 0, compositionMs: 0, publishMs: 0, totalMs: 0,
    baselineReadbackCalls: 1, branchReadbackCalls: 0, baselineReadbackBytes: 16, branchReadbackBytes: 0,
    zeroDropBranchSkipped: false, native: null};
  const accepted = {setPreparationHairEnabled: (enabled: boolean) => requests.push(enabled),
    // No-face isolates preparation policy from image appearance/composition.
    present: () => false, ownedPixels: new PixelData(new Uint8ClampedArray(16).fill(37), 2, 2),
    ownedCameraPixels: null, stageTimings, dispose: () => {}};
  const renderer = Reflect.construct(LiveHairRenderer, [new Canvas(), new Canvas(), accepted]) as Renderer;
  t.after(() => renderer.dispose());
  const source = new Canvas() as unknown as HTMLCanvasElement;
  const detection: Detection = {landmarks: [], matrix: null, inferenceMs: 0};
  const pair = {} as PairIdentity, model = {} as HairModelContract, mask = {} as HairMask;
  renderer.selectVariant('accepted');
  assert.equal(renderer.present(source, detection, null, pair, model), false);
  assert.deepEqual(requests, [false]);
  renderer.present(source, detection, mask, pair, model);
  assert.deepEqual(requests, [false, true]);
  renderer.selectVariant('hair'); renderer.selectVariant('accepted');
  renderer.setPreparationHairEnabled(false);
  renderer.present(source, detection, mask, pair, model);
  assert.equal(requests.at(-1), true, 'the supplied mask overrides an incompatible one-shot skip');
  renderer.present(source, detection, null, pair, model);
  assert.equal(requests.at(-1), false, 'the mask request is consumed by exactly one preparation');
  renderer.setPreparationHairEnabled(true); renderer.present(source, detection, null, pair, model);
  assert.equal(requests.at(-1), true, 'an explicit request without a mask remains honored');
  renderer.prepare(source, detection, pair, model);
  const beforePending = requests.length;
  assert.throws(() => renderer.present(source, detection, mask, pair, model), /pending pair/);
  assert.equal(requests.length, beforePending, 'a second presentation cannot mutate a pending pair');
  renderer.finish(null); renderer.dispose();
  assert.equal(renderer.present(source, detection, mask, pair, model), false);
  assert.equal(requests.length, beforePending);
});
