import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {WebGLRenderer} from 'three';
import {PboNativeReadback as Baseline} from '../../speed-lab/native/pbo-readback.ts';
import {PboNativeReadback as Diagnostic} from './native/pbo-readback.ts';

Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
  readonly data: Uint8ClampedArray; readonly width: number; readonly height: number; readonly colorSpace = 'srgb';
  constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
}});

type Reader = typeof Baseline | typeof Diagnostic;
function backend(ReaderType: Reader) {
  let lost = false, error = 0, failRead = false, failRetrieve = false, failFence = false, failBuffer = false;
  const originalRead = {id: 'read'}, originalPack = {id: 'pack'}, data = new Map<object, Uint8Array>();
  const state = new Map<number, unknown>([[0x8caa, originalRead], [0x88ed, originalPack], [0xd05, 8], [0xd02, 9], [0xd04, 2], [0xd03, 3]]);
  let pixels: Uint8Array = Uint8Array.from({length: 16}, (_, i) => i), serial = 0, clock = 0;
  let statuses = [0x911a];
  const trace: unknown[] = [];
  const gl = {READ_FRAMEBUFFER_BINDING: 0x8caa, READ_FRAMEBUFFER: 0x8ca8, PIXEL_PACK_BUFFER_BINDING: 0x88ed,
    PIXEL_PACK_BUFFER: 0x88eb, PACK_ALIGNMENT: 0xd05, PACK_ROW_LENGTH: 0xd02, PACK_SKIP_PIXELS: 0xd04, PACK_SKIP_ROWS: 0xd03,
    STREAM_READ: 0x88e1, RGBA: 0x1908, UNSIGNED_BYTE: 0x1401, NO_ERROR: 0, SYNC_GPU_COMMANDS_COMPLETE: 0x9117,
    ALREADY_SIGNALED: 0x911a, CONDITION_SATISFIED: 0x911c, WAIT_FAILED: 0x911d, TIMEOUT_EXPIRED: 0x911b,
    getParameter: (key: number) => state.get(key), isContextLost: () => lost,
    getError: () => {const current = error; error = 0; return current;},
    createBuffer: () => failBuffer ? null : ({id: ++serial}), deleteBuffer: (buffer: object) => data.delete(buffer),
    bindFramebuffer: (_: number, value: object | null) => {state.set(0x8caa, value);},
    bindBuffer: (_: number, value: object | null) => {state.set(0x88ed, value);},
    pixelStorei: (key: number, value: number) => {state.set(key, value);},
    bufferData: (_: number, bytes: number) => {data.set(state.get(0x88ed) as object, new Uint8Array(bytes));},
    readPixels: (...args: unknown[]) => {assert.equal(typeof args[6], 'number');
      if (failRead) error = 0x502; else data.get(state.get(0x88ed) as object)!.set(pixels);},
    getBufferSubData: (_: number, _offset: number, output: Uint8Array) => {
      if (failRetrieve) throw new Error('fixture retrieval failed');
      output.set(data.get(state.get(0x88ed) as object)!);
    },
    fenceSync: () => failFence ? null : ({id: 'sync'}), deleteSync: () => {}, flush: () => {},
    clientWaitSync: () => statuses.length > 1 ? statuses.shift()! : statuses[0]!};
  // Observe the original operation order, arguments and state. The diagnostic
  // is not allowed to acquire more GL state or alter polling/readback calls.
  for (const [name, method] of Object.entries(gl)) if (typeof method === 'function') {
    Object.defineProperty(gl, name, {value: (...args: unknown[]) => {
      trace.push([name, ...args.map(value => value instanceof Uint8Array ? {length: value.length} : value)]);
      clock += name === 'getBufferSubData' ? 7 : name === 'getParameter' ? 2 : 1;
      return (method as (...args: unknown[]) => unknown)(...args);
    }});
  }
  const renderer = {getContext: () => {trace.push(['getContext']); return gl;},
    getRenderTarget: () => {trace.push(['getRenderTarget']); return null;},
    state: {bindFramebuffer: (...args: [number, object | null]) => {trace.push(['renderer.bindFramebuffer', ...args]); gl.bindFramebuffer(...args);}}} as unknown as WebGLRenderer;
  return {reader: new ReaderType(renderer), gl, state, trace,
    now: () => clock, tick: (ms: number) => {clock += ms;},
    pixels: (value: Uint8Array) => {pixels = value;}, statuses: (value: number[]) => {statuses = value;},
    lost: () => {lost = true;}, failRead: () => {failRead = true;}, failRetrieve: () => {failRetrieve = true;},
    failFence: () => {failFence = true;}, failBuffer: () => {failBuffer = true;}};
}

const output = (pair: {beauty: ImageData; camera: ImageData | null}) => ({
  beauty: {width: pair.beauty.width, height: pair.beauty.height, data: [...pair.beauty.data]},
  camera: pair.camera ? {width: pair.camera.width, height: pair.camera.height, data: [...pair.camera.data]} : null,
});

type Scenario = 'beauty' | 'pair' | 'poll' | 'read-failure' | 'retrieve-failure' | 'context-loss' | 'fence-failure' |
  'buffer-failure' | 'wait-failure' | 'timeout' | 'revoked' | 'cancelled' | 'disposed';
async function exercise(ReaderType: Reader, scenario: Scenario) {
  const b = backend(ReaderType), results: unknown[] = [];
  b.reader.begin(2, 2);
  if (scenario === 'read-failure') b.failRead();
  if (scenario === 'buffer-failure') b.failBuffer();
  try {
    b.reader.capture(0);
    if (scenario === 'pair') {b.pixels(Uint8Array.from({length: 16}, (_, i) => 100 + i)); b.reader.capture(1);}
    if (scenario === 'context-loss') b.lost();
    if (scenario === 'retrieve-failure') b.failRetrieve();
    if (scenario === 'fence-failure') b.failFence();
    if (scenario === 'wait-failure') b.statuses([b.gl.WAIT_FAILED]);
    if (scenario === 'timeout' || scenario === 'cancelled' || scenario === 'disposed') b.statuses([b.gl.TIMEOUT_EXPIRED]);
    if (scenario === 'poll') b.statuses([b.gl.TIMEOUT_EXPIRED, b.gl.CONDITION_SATISFIED]);
    const pending = b.reader.finish(() => {b.trace.push(['isCurrent']); return scenario !== 'revoked';}, scenario === 'timeout' ? 0 : 500);
    if (scenario === 'cancelled') b.reader.cancel();
    if (scenario === 'disposed') b.reader.dispose();
    const firstPair = await pending; results.push(output(firstPair));
    // Later PBO reuse must not mutate a previously returned ImageData buffer.
    const retainedBeauty = new Uint8ClampedArray(firstPair.beauty.data);
    const retainedCamera = firstPair.camera ? new Uint8ClampedArray(firstPair.camera.data) : null;
    b.pixels(Uint8Array.from({length: 16}, (_, i) => 200 + i)); b.reader.begin(2, 2); b.reader.capture(0);
    const nextPair = await b.reader.finish(() => true); results.push(output(nextPair));
    assert.deepEqual(firstPair.beauty.data, retainedBeauty);
    assert.notEqual(firstPair.beauty.data, nextPair.beauty.data);
    assert.notEqual(firstPair.beauty.data.buffer, nextPair.beauty.data.buffer);
    if (firstPair.camera) assert.deepEqual(firstPair.camera.data, retainedCamera);
    b.pixels(Uint8Array.from({length: 8}, (_, i) => 50 + i)); b.reader.begin(1, 2); b.reader.capture(0);
    results.push(output(await b.reader.finish(() => true)));
  } catch (error) {results.push(error instanceof Error ? {name: error.name, message: error.message} : String(error));}
  const snapshot = b.reader.metrics;
  b.reader.dispose();
  return {trace: b.trace, state: [...b.state], results, completed: snapshot.completed, queuedCalls: snapshot.queuedCalls,
    retrievedCalls: snapshot.retrievedCalls, queuedBytes: snapshot.queuedBytes, retrievedBytes: snapshot.retrievedBytes,
    bufferBytesAllocated: snapshot.bufferBytesAllocated, polls: snapshot.polls};
}
for (const scenario of ['beauty', 'pair', 'poll', 'read-failure', 'retrieve-failure', 'context-loss', 'fence-failure',
  'buffer-failure', 'wait-failure', 'timeout', 'revoked', 'cancelled', 'disposed'] as const) {
  test(`diagnostic matches G GL trace, state, output and ownership for ${scenario}`, async () => {
    const base = await exercise(Baseline, scenario), diagnostic = await exercise(Diagnostic, scenario);
    assert.deepEqual(diagnostic, base);
    if (scenario === 'beauty' || scenario === 'pair') assert.deepEqual(
      (diagnostic.results[0] as ReturnType<typeof output>).beauty.data,
      [8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7]);
  });
}

test('phase timers separate observed state, GL retrieval and zero-timeout yield without extra GL calls', async t => {
  const b = backend(Diagnostic);
  t.mock.method(performance, 'now', b.now);
  t.mock.method(globalThis, 'setTimeout', ((callback: () => void, delay: number) => {
    assert.equal(delay, 0); queueMicrotask(() => {b.tick(41); callback();}); return 1;
  }) as unknown as typeof setTimeout);
  b.reader.begin(2, 2); b.reader.capture(0); b.statuses([b.gl.TIMEOUT_EXPIRED, b.gl.CONDITION_SATISFIED]);
  await b.reader.finish(() => true);
  const m = (b.reader as Diagnostic).metrics;
  assert.equal(m.diagnosticVersion, 1);
  assert.equal(m.submitStateQueryMs, 12); assert.equal(m.extractStateQueryMs, 12);
  assert.equal(m.submitStateSetupMs, 4); assert.equal(m.extractStateSetupMs, 4);
  assert.equal(m.submitStateRestoreMs, 7); assert.equal(m.extractStateRestoreMs, 7);
  assert.equal(m.submitBufferCreateMs, 1); assert.equal(m.submitBufferAllocateMs, 1); assert.equal(m.submitReadPixelsMs, 1);
  assert.equal(m.submitCheckMs, 4); assert.equal(m.waitCheckMs, 6); assert.equal(m.extractCheckMs, 2);
  assert.equal(m.fenceSyncMs, 1); assert.equal(m.fenceFlushMs, 1); assert.equal(m.clientWaitMs, 2);
  assert.equal(m.pollYields, 1); assert.equal(m.pollYieldMs, 41);
  assert.equal(m.extractGetBufferSubDataMs, 7); assert.equal(m.extractBufferBindMs, 1);
  assert.equal(m.extractAllocationMs, 0); assert.equal(m.extractRowFlipMs, 0); assert.equal(m.extractImageDataMs, 0);
  assert.ok(m.waitMs > m.pollYieldMs); assert.equal(m.waitObservationMs, m.waitMs);
  assert.equal(m.extractObservationMs, m.extractMs); assert.ok(m.extractMs >= 34);
  assert.ok(m.diagnosticClockReads > 40);
  for (const [key, value] of Object.entries(m)) if (typeof value === 'number') assert.ok(Number.isFinite(value) && value >= 0, key);
  const snapshot = {...m}; m.extractGetBufferSubDataMs = 99;
  assert.deepEqual((b.reader as Diagnostic).metrics, snapshot, 'caller mutation cannot mutate the reader metrics');
  b.reader.dispose();
});

test('cancelled observation cannot write into a new pair across the unchanged poll yield', async () => {
  const b = backend(Diagnostic), reader = b.reader as Diagnostic;
  b.reader.begin(2, 2); b.reader.capture(0); b.statuses([b.gl.TIMEOUT_EXPIRED]);
  const pending = b.reader.finish(() => true);
  reader.cancel(); reader.begin(2, 2); reader.capture(0);
  const currentMetrics = reader.metrics;
  await assert.rejects(pending, {name: 'AbortError'});
  assert.deepEqual(reader.metrics, currentMetrics);
  b.statuses([b.gl.ALREADY_SIGNALED]);
  const pair = await reader.finish(() => true);
  assert.equal(pair.camera, null); assert.equal(reader.metrics.completed, true);
  assert.equal(reader.metrics.fallbackReason, null); assert.equal(reader.metrics.pollYields, 0);
  reader.dispose();
});

test('partial extraction reports failed retrieval time but cannot claim a completed pair', async t => {
  const b = backend(Diagnostic), reader = b.reader as Diagnostic;
  t.mock.method(performance, 'now', b.now);
  reader.begin(2, 2); reader.capture(0); b.failRetrieve();
  await assert.rejects(reader.finish(() => true), /fixture retrieval failed/);
  const m = reader.metrics;
  assert.equal(m.completed, false); assert.equal(m.retrievedCalls, 0); assert.equal(m.extractMs, 0);
  assert.equal(m.extractGetBufferSubDataMs, 7); assert.ok(m.extractObservationMs > 7);
  assert.equal(m.extractStateRestoreMs, 7); assert.equal(m.extractImageDataMs, 0);
  reader.dispose();
});
