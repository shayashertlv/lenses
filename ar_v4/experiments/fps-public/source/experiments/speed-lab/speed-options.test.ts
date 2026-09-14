import assert from 'node:assert/strict';
import {test} from 'node:test';
import {DEFAULT_SPEED_OPTIONS, normalizeSpeedOptions, createOwnedSourceFrame, assertSourceFrameCurrent, sourcePixelsOpaque} from './speed-options.ts';

class TestImageData {
  readonly data: Uint8ClampedArray<ArrayBuffer>;
  readonly width: number;
  readonly height: number;
  readonly colorSpace: PredefinedColorSpace;
  constructor(data: Uint8ClampedArray<ArrayBuffer>, width: number, height: number, options: ImageDataSettings = {}) {
    this.data = data; this.width = width; this.height = height; this.colorSpace = options.colorSpace ?? 'srgb';
  }
}

test('options default off and freeze independently of later UI mutation', () => {
  assert.deepEqual(normalizeSpeedOptions(), DEFAULT_SPEED_OPTIONS);
  const requested = {fewerCopies: true}; const captured = normalizeSpeedOptions(requested);
  requested.fewerCopies = false;
  assert.equal(captured.fewerCopies, true); assert.equal(Object.isFrozen(captured), true);
  assert.throws(() => normalizeSpeedOptions({asyncReadback: 1} as never), /Invalid speed option/);
});

test('claiming the hashed RGBA snapshot detaches caller storage and preserves source identity', t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: TestImageData});
  t.after(() => {if (previous) Object.defineProperty(globalThis, 'ImageData', previous); else Reflect.deleteProperty(globalThis, 'ImageData');});
  const canvas = {width: 2, height: 1} as HTMLCanvasElement;
  const data = new Uint8ClampedArray([10, 20, 30, 255, 40, 50, 60, 255]), saved = data.slice();
  const rgba = new ImageData(data, 2, 1, {colorSpace: 'srgb'});
  let active = true;
  const identity = {sourceSHA256: 'a'.repeat(64), sessionId: 'camera-1', generation: 3, isCurrent: () => active};
  const source = createOwnedSourceFrame(canvas, rgba, identity);
  assert.equal(data.byteLength, 0, 'the original view cannot mutate adopted pixels');
  assert.deepEqual(source.rgba.data, saved);
  assert.equal(sourcePixelsOpaque(source.rgba), true);
  assert.equal(source.canvas, canvas);
  assertSourceFrameCurrent(source, canvas, identity.sourceSHA256);
  assert.throws(() => assertSourceFrameCurrent(source, canvas, 'b'.repeat(64)), /another paired image/);
  assert.throws(() => assertSourceFrameCurrent(source, {width: 2, height: 1} as HTMLCanvasElement), /does not own this canvas/);
  assert.throws(() => assertSourceFrameCurrent({...source, colorSpace: 'display-p3'} as never), /explicit sRGB/);
  active = false;
  assert.throws(() => assertSourceFrameCurrent(source), {name: 'AbortError'});
  assert.deepEqual(source.rgba.data, saved, 'released live canvas does not release the independent held snapshot');
});

test('invalid or stale source claims fail before detaching the caller pixels', t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: TestImageData});
  t.after(() => {if (previous) Object.defineProperty(globalThis, 'ImageData', previous); else Reflect.deleteProperty(globalThis, 'ImageData');});
  const canvas = {width: 2, height: 1} as HTMLCanvasElement;
  const rgba = new ImageData(new Uint8ClampedArray(8), 2, 1, {colorSpace: 'srgb'});
  const identity = {sourceSHA256: 'a'.repeat(64), sessionId: 1, generation: 1, isCurrent: () => true};
  for (const patch of [{sourceSHA256: 'bad'}, {generation: -1}, {sessionId: ''}])
    assert.throws(() => createOwnedSourceFrame(canvas, rgba, {...identity, ...patch}), /identity is invalid/);
  assert.throws(() => createOwnedSourceFrame(canvas, rgba, {...identity, isCurrent: () => false}), {name: 'AbortError'});
  assert.throws(() => createOwnedSourceFrame({width: 1, height: 1} as HTMLCanvasElement, rgba, identity), /dimensions differ/);
  const wide = new ImageData(new Uint8ClampedArray(8), 2, 1, {colorSpace: 'display-p3'});
  assert.equal(sourcePixelsOpaque(rgba), false);
  assert.throws(() => createOwnedSourceFrame(canvas, wide, identity), /explicit sRGB/);
  assert.equal(rgba.data.byteLength, 8);
});
