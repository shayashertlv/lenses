import assert from 'node:assert/strict';
import {test} from 'node:test';
import {NativePixelReader} from './native-pixels.ts';

test('native output preserves top-down bytes without a 2D conversion and owns prior frames across reuse/resize', t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  class PixelData {
    readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
    constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: PixelData});
  t.after(() => { if (previous) Object.defineProperty(globalThis, 'ImageData', previous); else Reflect.deleteProperty(globalThis, 'ImageData'); });
  const reader = new NativePixelReader();
  let width = 3, height = 2, lost = false, source = Uint8ClampedArray.from({length: 24}, (_, index) => index * 7);
  const canvas = {get width() {return width;}, get height() {return height;},
    getContext: (kind: string) => {
      assert.equal(kind, 'webgl2', 'native reading never requests a canvas-converted image');
      return {RGBA: 1, UNSIGNED_BYTE: 2, isContextLost: () => lost,
        readPixels: (_x: number, _y: number, w: number, h: number, _format: number, _type: number, output: Uint8Array) => {
          for (let y = 0; y < h; y++) output.set(source.subarray(y * w * 4, (y + 1) * w * 4), (h - y - 1) * w * 4);
        }};
    }} as unknown as HTMLCanvasElement;
  const first = reader.read(canvas), firstBytes = source.slice();
  assert.deepEqual(first.data, source);
  source.fill(17); const second = reader.read(canvas);
  assert.deepEqual(second.data, source); assert.deepEqual(first.data, firstBytes);
  width = 2; height = 1; source = new Uint8ClampedArray(8).fill(123);
  assert.deepEqual(reader.read(canvas).data, source); assert.deepEqual(first.data, firstBytes);
  lost = true; assert.throws(() => reader.read(canvas), /framebuffer/);
  reader.dispose(); assert.deepEqual(first.data, firstBytes);
});
