import assert from 'node:assert/strict';
import {test} from 'node:test';
import {InputCanvasPool, hashInput, inputContext} from './input-resources.ts';

function factory() {
  const canvases: {canvas: HTMLCanvasElement; clears: number[][]; contexts: unknown[][]}[] = [];
  return {canvases, create: (): HTMLCanvasElement => {
    const clears: number[][] = [], contexts: unknown[][] = [];
    const canvas = {width: 300, height: 150, getContext: (...args: unknown[]) => {
      contexts.push(args); return {clearRect: (...rect: number[]) => {clears.push(rect);}};
    }} as unknown as HTMLCanvasElement;
    canvases.push({canvas, clears, contexts}); return canvas;
  }};
}

test('pooled capture storage never reuses an owned image and stays bounded at two across replacement and resize', () => {
  const f = factory(), pool = new InputCanvasPool(true, f.create);
  const first = pool.acquire(1280, 853), second = pool.acquire(1280, 853);
  assert.notEqual(first, second); assert.throws(() => pool.acquire(1280, 853), /two-image/);
  assert.equal(f.canvases.length, 2);
  pool.release(second); const resized = pool.acquire(960, 640);
  assert.equal(resized, second); assert.equal(first.width, 1280); assert.equal(first.height, 853);
  assert.equal(resized.width, 960); assert.equal(resized.height, 640);
  pool.release(resized); const sameSize = pool.acquire(960, 640);
  assert.equal(sameSize, second); assert.deepEqual(f.canvases[1]!.clears, [[0, 0, 960, 640]]);
  assert.deepEqual(f.canvases[1]!.contexts[0], ['2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'}]);
  assert.equal(pool.stats.created, 2); assert.equal(pool.stats.reused, 2); assert.equal(pool.stats.resized, 1);
  assert.equal(pool.stats.maxLeased, 2); assert.equal(pool.stats.liveCanvases, 2);
  pool.release(first); pool.release(second); pool.dispose();
  assert.equal(first.width, 0); assert.equal(second.width, 0); assert.equal(pool.stats.liveCanvases, 0);
});

test('shutdown frees released storage but retains bitmap reader leases until their promise settles', async () => {
  const f = factory(), pool = new InputCanvasPool(true, f.create);
  const reading = pool.acquire(640, 427), free = pool.acquire(1280, 853); pool.release(free);
  let finishRead!: () => void;
  const bitmapSnapshot = new Promise<void>(resolve => {finishRead = resolve;});
  const released = bitmapSnapshot.then(() => {assert.equal(reading.width, 640); pool.release(reading);});
  pool.dispose(); pool.dispose();
  assert.equal(free.width, 0); assert.equal(reading.width, 640);
  assert.throws(() => pool.acquire(640, 427), /closed/); assert.equal(pool.stats.leasedCanvases, 1);
  finishRead(); await released;
  assert.equal(reading.width, 0); assert.equal(pool.stats.liveCanvases, 0);
  assert.throws(() => pool.release(reading), /not owned/);
});

test('unpooled G controls allocate and destroy each lease without retaining canvas history', () => {
  const f = factory(), pool = new InputCanvasPool(false, f.create);
  const first = pool.acquire(1280, 853); inputContext(first); pool.release(first);
  assert.equal(first.width, 0); const next = pool.acquire(1280, 853);
  assert.notEqual(next, first); assert.equal(pool.stats.created, 2); assert.equal(pool.stats.reused, 0);
  assert.equal(pool.stats.liveCanvases, 1); assert.equal(pool.stats.freeCanvases, 0);
  assert.deepEqual(f.canvases[0]!.contexts[0], ['2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'}]);
  pool.release(next); pool.dispose();
});

test('input hashing preserves the exact byte range, buffer ownership and SHA with zero explicit lean copy bytes', async () => {
  const source = new Uint8ClampedArray([99, 97, 98, 99, 77]);
  const view = source.subarray(1, 4), before = source.slice();
  const full = await hashInput(view, false), lean = await hashInput(view, true);
  assert.equal(full.value, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(lean.value, full.value); assert.deepEqual(source, before);
  assert.equal(source.buffer.byteLength, 5); assert.equal(lean.explicitCopyBytes, 0); assert.equal(full.explicitCopyBytes, 3);
  assert.ok(lean.copyMs >= 0 && lean.submitMs >= 0 && lean.digestWallMs >= lean.submitMs);
  const moved = structuredClone(source, {transfer: [source.buffer]});
  assert.equal(source.buffer.byteLength, 0); assert.deepEqual(moved, before);
});

test('each lean digest snapshots bytes at submission even if its caller mutates after the promise is returned', async () => {
  const bytes = new Uint8Array([97, 98, 99]); const result = hashInput(bytes, true); bytes.fill(0);
  assert.equal((await result).value, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  // Production keeps the bytes immutable until completion; this verifies the
  // WebCrypto snapshot separately, not permission to alter paired source data.
  assert.equal(bytes.buffer.byteLength, 3);
});
