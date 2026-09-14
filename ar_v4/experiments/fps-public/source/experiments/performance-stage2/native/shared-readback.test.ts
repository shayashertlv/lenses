import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {WebGLRenderer} from 'three';
import {SharedNativeReadback} from './shared-readback.ts';

class TestImageData {
  readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
  constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
}

test('shared context reads beauty before clean and preserves native rows, alpha, ownership and failure recovery', t => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'ImageData');
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: TestImageData});
  t.after(() => {if (prior) Object.defineProperty(globalThis, 'ImageData', prior); else Reflect.deleteProperty(globalThis, 'ImageData');});
  let source: Uint8Array = new Uint8Array(0), error = 0, reads = 0, lost = false;
  const bindings: string[] = [];
  const gl = {READ_FRAMEBUFFER: 1, RGBA: 2, UNSIGNED_BYTE: 3, NO_ERROR: 0,
    texStorage2D: () => {}, isContextLost: () => lost,
    bindFramebuffer: (target: number, value: unknown) => {assert.equal(target, 1); assert.equal(value, null); bindings.push('native');},
    getError: () => {const result = error; error = 0; return result;},
    readPixels: (_x: number, _y: number, w: number, h: number, _format: number, _type: number, bytes: Uint8Array) => {
      assert.equal(bytes.length, w * h * 4); bytes.set(source); reads++;
      assert.deepEqual(bindings.splice(0), ['native', 'cache']);
    }};
  const renderer = {getContext: () => gl, getRenderTarget: () => null,
    state: {bindFramebuffer: (target: number, value: unknown) => {assert.equal(target, 1); assert.equal(value, null); bindings.push('cache');}}};
  const reader = new SharedNativeReadback(renderer as unknown as WebGLRenderer);
  const beauty = Uint8Array.from({length: 24}, (_, i) => i * 7), camera = Uint8Array.from({length: 24}, (_, i) => 255 - i * 3);
  reader.begin(3, 2); assert.throws(() => reader.read(), /Both current/); assert.throws(() => reader.capture(1), /beauty then camera/);
  source = beauty; reader.capture(0); assert.throws(() => reader.capture(0), /beauty then camera/);
  source = camera; reader.capture(1); const first = reader.read();
  assert.deepEqual(Array.from(first.beauty.data), [...beauty.slice(12), ...beauty.slice(0, 12)]);
  assert.deepEqual(Array.from(first.camera.data), [...camera.slice(12), ...camera.slice(0, 12)]);
  assert.equal(reads, 2); assert.equal(reader.metrics.readbackCalls, 2); assert.equal(reader.metrics.readbackBytes, 48);
  assert.equal(reader.metrics.mode, 'shared-context-two-readbacks'); assert.throws(() => reader.read(), /Both current/);
  const saved = first.beauty.data.slice();
  reader.begin(3, 2); assert.equal(reader.metrics.allocatedBytes, 0);
  source = new Uint8Array(24).fill(22); reader.capture(0); error = 0x502;
  assert.throws(() => reader.capture(1), /camera readback failed \(GL 0x502\)/); assert.throws(() => reader.read(), /Both current/);
  assert.deepEqual(first.beauty.data, saved);
  reader.begin(2, 1); source = new Uint8Array(8).fill(77); reader.capture(0); reader.capture(1);
  assert.equal(reader.read().camera.data[0], 77); assert.equal(reader.metrics.allocatedBytes, 24);
  lost = true; assert.throws(() => reader.begin(2, 1), /unavailable/); lost = false;
  reader.begin(2, 1); reader.capture(0); reader.dispose(); reader.dispose();
  assert.throws(() => reader.read(), /Both current/); assert.throws(() => reader.begin(2, 1), /unavailable/);
  assert.deepEqual(first.beauty.data, saved);
});
