import assert from 'node:assert/strict';
import {test} from 'node:test';
import {NativePixelReader} from './native-pixels.ts';

test('native fallback rejects unchanged scratch on GL error or context loss, recovers, and preserves prior owned bytes', t => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  class PixelData {
    readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
    constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: PixelData});
  t.after(() => {if (prior) Object.defineProperty(globalThis, 'ImageData', prior); else Reflect.deleteProperty(globalThis, 'ImageData');});
  let width = 3, height = 2, lost = false, fail = false, loseDuringRead = false, error = 0;
  let source = Uint8ClampedArray.from({length: 24}, (_, index) => index * 7);
  const gl = {RGBA: 1, UNSIGNED_BYTE: 2, NO_ERROR: 0, isContextLost: () => lost,
    getError: () => {const result = error; error = 0; return result;},
    readPixels: (_x: number, _y: number, w: number, h: number, _format: number, _type: number, out: Uint8Array) => {
      if (fail) {error = 0x502; return;}
      if (loseDuringRead) {lost = true; return;}
      for (let y = 0; y < h; y++) out.set(source.subarray(y * w * 4, (y + 1) * w * 4), (h - y - 1) * w * 4);
    }};
  const canvas = {get width() {return width;}, get height() {return height;}, getContext: (kind: string) => {
    assert.equal(kind, 'webgl2'); return gl;
  }} as unknown as HTMLCanvasElement;
  const reader = new NativePixelReader(), first = reader.read(canvas), saved = source.slice();
  assert.deepEqual(first.data, saved);
  fail = true; source.fill(77);
  assert.throws(() => reader.read(canvas), /GL 0x502/, 'failed read must not publish the preceding scratch contents');
  assert.deepEqual(first.data, saved);
  fail = false; assert.deepEqual(reader.read(canvas).data, source);
  loseDuringRead = true; assert.throws(() => reader.read(canvas), /context lost: true/);
  assert.throws(() => reader.read(canvas), /framebuffer is unavailable/);
  lost = loseDuringRead = false; width = 2; height = 1; source = new Uint8ClampedArray(8).fill(123);
  assert.deepEqual(reader.read(canvas).data, source); assert.deepEqual(first.data, saved);
  reader.dispose(); assert.deepEqual(first.data, saved);
});
