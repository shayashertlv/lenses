import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4} from 'three';
import {TryOnRenderer} from './renderer.ts';
import {DEFAULT_SPEED_OPTIONS} from '../speed-options.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import {eyewearById} from '../../../references/perfect-temples/src/render/eyewear.ts';
import {REAR_DROP_METHOD} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

test('hidden nonzero prewarm completes once, accounts discarded read and clears its output before zero-drop publication', () => {
  let nativeReads = 0, published = 0;
  const gl = {SAMPLES: 1, NO_ERROR: 0, RGBA: 2, UNSIGNED_BYTE: 3, getParameter: () => 4,
    readPixels: (_x: number, _y: number, _w: number, _h: number, _format: number, _type: number, data: Uint8Array) => {nativeReads++; data.fill(44);},
    getError: () => 0, isContextLost: () => false};
  const ctx = {drawImage: () => {}, putImageData: () => {published++;}, clearRect: () => {}};
  const canvas = (): HTMLCanvasElement => ({width: 4, height: 3, getContext: (name: string) => name === 'webgl2' ? gl : ctx}) as unknown as HTMLCanvasElement;
  const priorDocument = globalThis.document, priorImage = globalThis.ImageData;
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: canvas}});
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
    readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
    constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }});
  try {
    const matrix = new Matrix4().makeTranslation(0, 0, -60).toArray();
    const beauty = {width: 4, height: 3, data: new Uint8ClampedArray(48).fill(255)} as ImageData;
    const snapshot = {eyewearModelId: 'tom-ford-clear', rawMatrix: matrix, correctedMatrix: matrix, eyewearMatrix: matrix,
      surfacePositions: new Float32Array(468 * 3), yawDegrees: 0, templeClip: null,
      templeVisibility: {method: 'temple-head-visibility-v3', coverage: 'alpha-to-coverage', negativeXWeight: 0, positiveXWeight: 0, frontalOcclusionWeight: 0}};
    const calls: unknown[][] = [], clears: number[] = [];
    const native = {eyewear: eyewearById('tom-ford-clear'), setFrameOptions: () => {}, present: () => true,
      nativeImagePair: {beauty, camera: null}, captureSnapshot: snapshot, stageTimings: null};
    const branch = {present: (...args: unknown[]) => {calls.push(args); return true;}, captureSnapshot: snapshot,
      clearOwnedFrame: () => {clears.push(calls.length);}, rearDropBounds: null};
    const display = canvas(), renderer = Reflect.construct(TryOnRenderer, [display, canvas(), canvas(), native, branch, {publish: false}]) as TryOnRenderer;
    const source = canvas(), detection: Detection = {matrix, landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})), inferenceMs: 0};
    const run = () => {
      renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, prewarmTemples: true});
      renderer.present(source, detection, undefined, undefined, undefined, {method: REAR_DROP_METHOD, dropM: 0});
    };
    run(); assert.equal(calls.length, 1); assert.equal(nativeReads, 1); assert.equal(published, 0);
    assert.deepEqual(calls[0]![5], {method: REAR_DROP_METHOD, dropM: .02});
    assert.equal((calls[0]![4] as {negativeXWeight: number}).negativeXWeight, 1);
    assert.equal(renderer.stageTimings.speedLab.prewarmCompleted, true);
    assert.equal(renderer.stageTimings.speedLab.prewarmReadbackBytes, 48);
    assert.equal(renderer.ownedPixels, beauty); assert.ok(clears.length >= 2, 'temporary branch and actual zero path clear ownership');
    run(); assert.equal(calls.length, 1); assert.equal(nativeReads, 1);
    assert.equal(renderer.stageTimings.speedLab.prewarmAttempted, false);
    assert.equal(renderer.stageTimings.speedLab.prewarmCompleted, true); assert.equal(renderer.stageTimings.speedLab.prewarmMs, 0);
  } finally {
    Object.defineProperty(globalThis, 'document', {configurable: true, value: priorDocument});
    Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: priorImage});
  }
});
