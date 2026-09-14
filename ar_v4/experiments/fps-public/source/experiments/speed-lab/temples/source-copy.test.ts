import assert from 'node:assert/strict';
import {test} from 'node:test';
import {TryOnRenderer} from './renderer.ts';
import {DEFAULT_SPEED_OPTIONS} from '../speed-options.ts';
import type {OwnedSourceFrame} from '../speed-options.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';

for (const asynchronous of [false, true]) test(`temple ${asynchronous ? 'async' : 'sync'} copying rejects partial alpha, restores its backdrop and never reconstructs rejected raw pixels`, async () => {
  const priorDocument = globalThis.document;
  const operations: string[] = [];
  interface PixelCanvas {width: number; height: number; bytes: Uint8ClampedArray; getContext: (kind: string) => unknown;}
  const canvas = (bytes = [0, 0, 0, 255]): PixelCanvas => {
    const value: PixelCanvas = {width: 1, height: 1, bytes: new Uint8ClampedArray(bytes), getContext: () => null};
    const context = {
      putImageData: (image: ImageData) => {operations.push('restore'); value.bytes.set(image.data);},
      drawImage: (source: PixelCanvas) => {
        operations.push('copy'); const alpha = source.bytes[3]! / 255;
        for (let c = 0; c < 3; c++) value.bytes[c] = Math.round(source.bytes[c]! * alpha + value.bytes[c]! * (1 - alpha));
        value.bytes[3] = 255;
      }, clearRect: () => {value.bytes.set([0, 0, 0, 255]);},
    };
    value.getContext = kind => kind === 'webgl2' ? {SAMPLES: 1, getParameter: () => 4} : context;
    return value;
  };
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: () => canvas()}});
  try {
    const toHtml = (value: PixelCanvas): HTMLCanvasElement => value as unknown as HTMLCanvasElement;
    const opaque = canvas([20, 40, 60, 255]), transparent = canvas([200, 100, 40, 128]);
    const image = (value: PixelCanvas): ImageData => ({width: 1, height: 1, colorSpace: 'srgb', data: value.bytes.slice()}) as ImageData;
    let current = true;
    const owned = (value: PixelCanvas, generation: number): OwnedSourceFrame => ({canvas: toHtml(value), rgba: image(value),
      sourceSHA256: 'a'.repeat(64), generation, sessionId: 'temple-copy', colorSpace: 'srgb', isCurrent: () => current});
    const presented: PixelCanvas[] = [];
    const native = {setFrameOptions: () => {}, present: (frame: PixelCanvas) => {presented.push(frame); return false;},
      presentAsync: async (frame: PixelCanvas) => {presented.push(frame); return false;},
      nativeImagePair: {beauty: image(opaque), camera: null}, captureSnapshot: null, stageTimings: null};
    const renderer = Reflect.construct(TryOnRenderer, [toHtml(canvas()), toHtml(canvas()), toHtml(canvas()), native,
      {clearOwnedFrame: () => {}}, {publish: false}]) as TryOnRenderer;
    const detection: Detection = {matrix: null, landmarks: [], inferenceMs: 0};
    renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, fewerCopies: true}, owned(opaque, 1));
    if (asynchronous) await renderer.presentAsync(toHtml(opaque), detection); else renderer.present(toHtml(opaque), detection);
    assert.equal(presented[0], opaque); assert.equal(renderer.stageTimings.speedLab.sourceCopiesAvoided, 1);
    assert.deepEqual(operations, []);
    renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, fewerCopies: true}, owned(transparent, 2));
    if (asynchronous) await renderer.presentAsync(toHtml(transparent), detection); else renderer.present(toHtml(transparent), detection);
    assert.deepEqual(operations, ['restore', 'copy']); assert.notEqual(presented[1], transparent);
    assert.deepEqual(Array.from(presented[1]!.bytes), [110, 70, 50, 255]);
    assert.equal(renderer.stageTimings.speedLab.sourceCopiesAvoided, 0);
    assert.match(renderer.stageTimings.speedLab.sourceCopyFallback!, /opaque/);
    current = false; transparent.width = transparent.height = 0;
    const held = Reflect.get(renderer, 'ownedFrame') as PixelCanvas;
    assert.equal(held, presented[1]); assert.deepEqual(Array.from(held.bytes), [110, 70, 50, 255]);
    assert.deepEqual(operations, ['restore', 'copy'], 'expired rejected RGBA must not overwrite the opaque copy');
  } finally {Object.defineProperty(globalThis, 'document', {configurable: true, value: priorDocument});}
});
