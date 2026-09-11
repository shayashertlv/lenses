import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {WebGLRenderer} from 'three';
import {PboNativeReadback} from './pbo-readback.ts';

Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
  readonly data: Uint8ClampedArray; readonly width: number; readonly height: number; readonly colorSpace = 'srgb';
  constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
}});

function backend(canvasReader = false) {
  let lost = false, error = 0, status = 0x911a, failRead = false;
  const originalRead = {}, originalPack = {}, data = new Map<object, Uint8Array>();
  const state = new Map<number, unknown>([[0x8caa, originalRead], [0x88ed, originalPack], [0x8ca6, null], [0xd05, 8], [0xd02, 9], [0xd04, 2], [0xd03, 3]]);
  const targets: Uint8Array[] = [];
  let pixels: Uint8Array = new Uint8Array(16).map((_, index) => index), reads = 0, retrieves = 0, polls = 0, deleted = 0;
  const gl = {DRAW_FRAMEBUFFER_BINDING: 0x8ca6, READ_FRAMEBUFFER_BINDING: 0x8caa, READ_FRAMEBUFFER: 0x8ca8, PIXEL_PACK_BUFFER_BINDING: 0x88ed,
    PIXEL_PACK_BUFFER: 0x88eb, PACK_ALIGNMENT: 0xd05, PACK_ROW_LENGTH: 0xd02, PACK_SKIP_PIXELS: 0xd04, PACK_SKIP_ROWS: 0xd03,
    STREAM_READ: 0x88e1, RGBA: 0x1908, UNSIGNED_BYTE: 0x1401, NO_ERROR: 0, SYNC_GPU_COMMANDS_COMPLETE: 0x9117,
    ALREADY_SIGNALED: 0x911a, CONDITION_SATISFIED: 0x911c, WAIT_FAILED: 0x911d, TIMEOUT_EXPIRED: 0x911b,
    getParameter: (key: number) => state.get(key), isContextLost: () => lost,
    getError: () => {const current = error; error = 0; return current;},
    createBuffer: () => ({}), deleteBuffer: (buffer: object) => data.delete(buffer),
    bindFramebuffer: (_: number, value: object | null) => {state.set(0x8caa, value);},
    bindBuffer: (_: number, value: object | null) => {state.set(0x88ed, value);},
    pixelStorei: (key: number, value: number) => {state.set(key, value);},
    bufferData: (_: number, bytes: number) => {data.set(state.get(0x88ed) as object, new Uint8Array(bytes));},
    readPixels: (...args: unknown[]) => {assert.equal(typeof args[6], 'number', 'read is queued into PBO rather than CPU memory');
      reads++; if (failRead) error = 0x502; else data.get(state.get(0x88ed) as object)!.set(pixels);},
    getBufferSubData: (_: number, _offset: number, output: Uint8Array) => {retrieves++; targets.push(output); output.set(data.get(state.get(0x88ed) as object)!);},
    fenceSync: () => ({}), deleteSync: () => {deleted++;}, flush: () => {},
    clientWaitSync: () => {polls++; return status;}};
  const renderer = {getContext: () => gl, getRenderTarget: () => null, state: {bindFramebuffer: gl.bindFramebuffer}} as unknown as WebGLRenderer;
  return {reader: new PboNativeReadback(canvasReader ? {getContext: () => gl} as unknown as HTMLCanvasElement : renderer), gl, state, originalRead, originalPack, targets,
    setPixels: (value: Uint8Array) => {pixels = value;}, setStatus: (value: number) => {status = value;},
    lose: () => {lost = true;}, failRead: () => {failRead = true;}, counts: () => ({reads, retrieves, polls, deleted})};
}

test('PBO pair snapshots both native draws, retrieves only after a fence, flips exact rows and restores pack/read state', async () => {
  const b = backend(); b.reader.begin(2, 2); b.reader.capture(0);
  b.setPixels(Uint8Array.from({length: 16}, (_, i) => 100 + i)); b.reader.capture(1);
  assert.equal(b.counts().retrieves, 0); assert.equal(b.state.get(b.gl.READ_FRAMEBUFFER_BINDING), b.originalRead);
  assert.equal(b.state.get(b.gl.PIXEL_PACK_BUFFER_BINDING), b.originalPack);
  const pair = await b.reader.finish(() => true);
  assert.deepEqual(Array.from(pair.beauty.data), [8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(Array.from(pair.camera!.data), [108, 109, 110, 111, 112, 113, 114, 115, 100, 101, 102, 103, 104, 105, 106, 107]);
  assert.equal(b.reader.metrics.completed, true); assert.equal(b.reader.metrics.queuedBytes, 32);
  for (const [key, value] of [[b.gl.PACK_ALIGNMENT, 8], [b.gl.PACK_ROW_LENGTH, 9], [b.gl.PACK_SKIP_PIXELS, 2], [b.gl.PACK_SKIP_ROWS, 3]]) assert.equal(b.state.get(key!), value);
  b.reader.begin(2, 2); b.reader.capture(0); const beautyOnly = await b.reader.finish(() => true);
  assert.equal(beautyOnly.camera, null); assert.equal(b.reader.metrics.bufferBytesAllocated, 0); b.reader.dispose();
});

test('pooled bottom-up scratch is shared only privately and every held beauty/camera keeps independent exact output bytes', async () => {
  const b = backend(); b.reader.begin(2, 2, true); b.reader.capture(0);
  b.setPixels(new Uint8Array(16).fill(71)); b.reader.capture(1);
  const first = await b.reader.finish(() => true), held = first.beauty.data.slice();
  assert.equal(b.targets[0], b.targets[1]);
  assert.notEqual(first.beauty.data.buffer, b.targets[0]!.buffer);
  assert.notEqual(first.camera!.data.buffer, first.beauty.data.buffer);
  assert.equal(b.reader.metrics.scratchBytesAllocated, 16); assert.equal(b.reader.metrics.scratchBytesReused, 16);
  assert.equal(b.reader.metrics.outputBytesAllocated, 32);
  b.reader.begin(2, 2, true); b.setPixels(new Uint8Array(16).fill(23)); b.reader.capture(0);
  const next = await b.reader.finish(() => true);
  assert.equal(b.targets[2], b.targets[0]); assert.equal(b.reader.metrics.scratchBytesAllocated, 0);
  assert.equal(b.reader.metrics.scratchBytesReused, 16); assert.deepEqual(first.beauty.data, held);
  assert.ok(first.camera!.data.every(value => value === 71)); assert.ok(next.beauty.data.every(value => value === 23));
  b.reader.dispose(); assert.throws(() => b.reader.begin(2, 2, true), /unavailable/);
});

test('scratch resizing and disabling allocate exact storage; cancelled pairs cannot expose or reuse unfinished bytes', async () => {
  const b = backend(); b.reader.begin(2, 2, true); b.reader.capture(0); await b.reader.finish(() => true);
  b.reader.begin(1, 1, true); b.setPixels(new Uint8Array([9, 8, 7, 6])); b.reader.capture(0);
  const resized = await b.reader.finish(() => true);
  assert.equal(b.reader.metrics.scratchBytesAllocated, 4); assert.equal(b.targets[1]!.byteLength, 4);
  assert.notEqual(b.targets[0], b.targets[1]); assert.deepEqual(Array.from(resized.beauty.data), [9, 8, 7, 6]);
  b.reader.begin(1, 1, false); b.reader.capture(0); await b.reader.finish(() => true);
  assert.equal(b.reader.metrics.scratchBytesAllocated, 4); assert.equal(b.reader.metrics.scratchBytesReused, 0);
  b.reader.begin(1, 1, true); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
  const pending = b.reader.finish(() => true); b.reader.cancel(); await assert.rejects(pending, {name: 'AbortError'});
  const retrieved = b.counts().retrieves; b.reader.begin(1, 1, true); b.setStatus(b.gl.ALREADY_SIGNALED);
  b.setPixels(new Uint8Array([1, 2, 3, 4])); b.reader.capture(0);
  const restarted = await b.reader.finish(() => true);
  assert.equal(b.counts().retrieves, retrieved + 1); assert.deepEqual(Array.from(restarted.beauty.data), [1, 2, 3, 4]);
  assert.deepEqual(Array.from(resized.beauty.data), [9, 8, 7, 6]); b.reader.dispose();
});

test('canvas branch PBO queues default framebuffer with exact GL state restoration and rejects an offscreen draw target', async () => {
  const b = backend(true); b.reader.begin(2, 2, true); b.reader.capture(0);
  assert.equal(b.counts().retrieves, 0); assert.equal(b.state.get(b.gl.READ_FRAMEBUFFER_BINDING), b.originalRead);
  assert.equal(b.state.get(b.gl.PIXEL_PACK_BUFFER_BINDING), b.originalPack);
  const image = await b.reader.finish(() => true); assert.equal(image.camera, null);
  assert.deepEqual(Array.from(image.beauty.data.slice(0, 4)), [8, 9, 10, 11]);
  b.reader.begin(2, 2, true); b.state.set(b.gl.DRAW_FRAMEBUFFER_BINDING, {});
  assert.throws(() => b.reader.capture(0), /default framebuffer/); b.reader.dispose();
});

test('pending cancellation yields control and cannot retrieve stale bytes', async () => {
  const b = backend(); b.reader.begin(2, 2); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
  const pending = b.reader.finish(() => true);
  assert.throws(() => b.reader.begin(2, 2), /already pending/);
  b.reader.cancel(); await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(b.counts().retrieves, 0); b.reader.dispose();
});

test('read submission errors, context loss, deadline and failed fences never return old bytes', async () => {
  const failed = backend(); failed.reader.begin(2, 2); failed.failRead();
  assert.throws(() => failed.reader.capture(0), /0x502/); assert.equal(failed.counts().retrieves, 0); failed.reader.dispose();
  for (const mode of ['lost', 'wait', 'timeout', 'revoked'] as const) {
    const b = backend(); b.reader.begin(2, 2); b.reader.capture(0);
    if (mode === 'lost') b.lose();
    if (mode === 'wait') b.setStatus(b.gl.WAIT_FAILED);
    if (mode === 'timeout') b.setStatus(b.gl.TIMEOUT_EXPIRED);
    await assert.rejects(b.reader.finish(() => mode !== 'revoked', 0));
    assert.equal(b.counts().retrieves, 0); assert.equal(b.reader.metrics.completed, false); b.reader.dispose();
  }
});
