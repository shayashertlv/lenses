import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {WebGLRenderer} from 'three';
import {PboNativeReadback} from './pbo-readback.ts';

Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
  readonly data: Uint8ClampedArray; readonly width: number; readonly height: number; readonly colorSpace = 'srgb';
  constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
}});

type Failure = 'create-buffer' | 'allocation' | 'read' | 'extraction' | 'create-fence' | 'fence-submission' | 'poll-context' | 'extraction-context';
function backend(reducedGlQueries = false, canonical = reducedGlQueries) {
  let lost = false, error = 0, status = 0x911a, failure: Failure | null = null;
  const originalRead = canonical ? null : {}, originalPack = canonical ? null : {}, data = new Map<object, Uint8Array>();
  const state = new Map<number, unknown>([[0x8caa, originalRead], [0x88ed, originalPack],
    [0xd05, canonical ? 4 : 8], [0xd02, canonical ? 0 : 9], [0xd04, canonical ? 0 : 2], [0xd03, canonical ? 0 : 3]]);
  const initialState = new Map(state);
  let pixels: Uint8Array = new Uint8Array(16).map((_, index) => index);
  let reads = 0, retrieves = 0, polls = 0, deleted = 0, parameterQueries = 0, errorChecks = 0, cachedRead: unknown = undefined;
  const gl = {READ_FRAMEBUFFER_BINDING: 0x8caa, READ_FRAMEBUFFER: 0x8ca8, PIXEL_PACK_BUFFER_BINDING: 0x88ed,
    PIXEL_PACK_BUFFER: 0x88eb, PACK_ALIGNMENT: 0xd05, PACK_ROW_LENGTH: 0xd02, PACK_SKIP_PIXELS: 0xd04, PACK_SKIP_ROWS: 0xd03,
    STREAM_READ: 0x88e1, RGBA: 0x1908, UNSIGNED_BYTE: 0x1401, NO_ERROR: 0, SYNC_GPU_COMMANDS_COMPLETE: 0x9117,
    ALREADY_SIGNALED: 0x911a, CONDITION_SATISFIED: 0x911c, WAIT_FAILED: 0x911d, TIMEOUT_EXPIRED: 0x911b,
    getParameter: (key: number) => {parameterQueries++; return state.get(key);}, isContextLost: () => lost,
    getError: () => {errorChecks++; const current = error; error = 0; return current;},
    createBuffer: () => failure === 'create-buffer' ? null : {}, deleteBuffer: (buffer: object) => data.delete(buffer),
    bindFramebuffer: (_: number, value: object | null) => {state.set(0x8caa, value);},
    bindBuffer: (_: number, value: object | null) => {state.set(0x88ed, value);},
    pixelStorei: (key: number, value: number) => {state.set(key, value);},
    bufferData: (_: number, bytes: number) => {
      if (failure === 'allocation') error = 0x505; else data.set(state.get(0x88ed) as object, new Uint8Array(bytes));
    },
    readPixels: (...args: unknown[]) => {
      assert.equal(typeof args[6], 'number', 'read is queued into PBO rather than CPU memory');
      assert.equal(state.get(0x8caa), null, 'read must use the current native default framebuffer');
      assert.equal(state.get(0xd05), 1);
      for (const key of [0xd02, 0xd04, 0xd03]) assert.equal(state.get(key), 0, 'foreign pack offsets must not change the image');
      reads++; if (failure === 'read') error = 0x502; else data.get(state.get(0x88ed) as object)!.set(pixels);
    },
    getBufferSubData: (_: number, _offset: number, output: Uint8Array) => {
      retrieves++;
      if (failure === 'extraction') error = 0x502;
      else if (failure === 'extraction-context') lost = true;
      else output.set(data.get(state.get(0x88ed) as object)!);
    },
    fenceSync: () => failure === 'create-fence' ? null : {}, deleteSync: () => {deleted++;},
    flush: () => {if (failure === 'fence-submission') error = 0x502;},
    clientWaitSync: () => {polls++; if (failure === 'poll-context') lost = true; return status;}};
  const renderer = {getContext: () => gl, getRenderTarget: () => null, state: {bindFramebuffer: (target: number, value: object | null) => {
    if (cachedRead !== value) {gl.bindFramebuffer(target, value); cachedRead = value; return true;} return false;
  }}} as unknown as WebGLRenderer;
  return {reader: new PboNativeReadback(renderer, {reducedGlQueries}), gl, state, initialState,
    setPixels: (value: Uint8Array) => {pixels = value;}, setStatus: (value: number) => {status = value;},
    lose: () => {lost = true;}, fail: (value: Failure | null) => {failure = value;},
    counts: () => ({reads, retrieves, polls, deleted, parameterQueries, errorChecks})};
}

for (const reduced of [false, true]) {
  const mode = reduced ? 'owned state' : 'conservative';
  test(`${mode}: exact pair, row flipping, pack isolation and buffer reuse`, async () => {
    const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0);
    assert.deepEqual(b.state, b.initialState);
    b.setPixels(Uint8Array.from({length: 16}, (_, i) => 100 + i)); b.reader.capture(1);
    assert.equal(b.counts().retrieves, 0); assert.deepEqual(b.state, b.initialState);
    const pair = await b.reader.finish(() => true);
    assert.deepEqual(Array.from(pair.beauty.data), [8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7]);
    assert.deepEqual(Array.from(pair.camera!.data), [108, 109, 110, 111, 112, 113, 114, 115, 100, 101, 102, 103, 104, 105, 106, 107]);
    assert.equal(b.reader.metrics.completed, true); assert.equal(b.reader.metrics.queuedBytes, 32);
    assert.deepEqual(b.state, b.initialState);
    assert.equal(b.reader.metrics.reducedGlQueriesUsed, reduced);
    assert.equal(b.reader.metrics.stateQueryCalls, reduced ? 6 : 18);
    b.reader.begin(2, 2); b.reader.capture(0); const beautyOnly = await b.reader.finish(() => true);
    assert.equal(beautyOnly.camera, null); assert.equal(b.reader.metrics.bufferBytesAllocated, 0);
    assert.equal(b.reader.metrics.stateQueryCalls, reduced ? 0 : 12);
    assert.equal(b.reader.metrics.errorCheckCalls, reduced ? 3 : 4);
    assert.deepEqual(b.state, b.initialState); b.reader.dispose();
  });

  test(`${mode}: fence polling yields, ownership cancellation rejects without stale retrieval`, async () => {
    const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
    const pending = b.reader.finish(() => true);
    assert.throws(() => b.reader.begin(2, 2), /already pending/);
    assert.equal(b.counts().polls, 1); b.reader.cancel();
    await assert.rejects(pending, {name: 'AbortError'});
    assert.equal(b.counts().retrieves, 0); assert.deepEqual(b.state, b.initialState); b.reader.dispose();
  });

  test(`${mode}: reused buffers cannot publish a previous successful image after failed submission`, async () => {
    const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0); await b.reader.finish(() => true);
    b.reader.begin(2, 2); b.fail('read'); assert.throws(() => b.reader.capture(0), /0x502/);
    await assert.rejects(b.reader.finish(() => true), /No submitted native pair/);
    assert.equal(b.counts().retrieves, 1); assert.equal(b.reader.metrics.completed, false);
    assert.deepEqual(b.state, b.initialState);
    b.fail(null); b.setPixels(new Uint8Array(16).fill(200)); b.reader.begin(2, 2); b.reader.capture(0);
    const resumed = await b.reader.finish(() => true); assert.ok(resumed.beauty.data.every(value => value === 200)); b.reader.dispose();
  });

  test(`${mode}: allocation and extraction failures retain pack isolation and reject`, async () => {
    for (const failure of ['create-buffer', 'allocation', 'extraction', 'extraction-context'] as const) {
      const b = backend(reduced); b.reader.begin(2, 2); b.fail(failure);
      if (failure === 'create-buffer' || failure === 'allocation') {
        assert.throws(() => b.reader.capture(0)); assert.equal(b.counts().retrieves, 0);
      } else {
        b.reader.capture(0); await assert.rejects(b.reader.finish(() => true), /CPU retrieval/);
      }
      assert.equal(b.reader.metrics.completed, false); assert.deepEqual(b.state, b.initialState); b.reader.dispose();
    }
  });

  test(`${mode}: lost context, fences, deadline and revocation cannot retrieve`, async () => {
    for (const failure of ['lost', 'wait', 'timeout', 'revoked', 'create-fence', 'fence-submission', 'poll-context'] as const) {
      const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0);
      if (failure === 'lost') b.lose();
      else if (failure === 'wait') b.setStatus(b.gl.WAIT_FAILED);
      else if (failure === 'timeout') b.setStatus(b.gl.TIMEOUT_EXPIRED);
      else if (failure !== 'revoked') b.fail(failure);
      await assert.rejects(b.reader.finish(() => failure !== 'revoked', 0));
      assert.equal(b.counts().retrieves, 0, failure); assert.equal(b.reader.metrics.completed, false);
      assert.deepEqual(b.state, b.initialState); b.reader.dispose();
    }
  });

  test(`${mode}: cancelled generation cannot affect a restarted pair or its metrics`, async () => {
    const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
    const pending = b.reader.finish(() => true); b.reader.cancel();
    b.reader.begin(2, 2); b.setPixels(new Uint8Array(16).fill(42)); b.reader.capture(0); b.setStatus(b.gl.ALREADY_SIGNALED);
    const pair = await b.reader.finish(() => true); const metrics = b.reader.metrics;
    await assert.rejects(pending, {name: 'AbortError'});
    assert.deepEqual(b.reader.metrics, metrics); assert.ok(pair.beauty.data.every(value => value === 42));
    assert.equal(metrics.completed, true); assert.equal(metrics.fallbackReason, null); assert.equal(b.counts().retrieves, 1); b.reader.dispose();
  });

  test(`${mode}: dispose during a fence wait cancels and prevents restart`, async () => {
    const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
    const pending = b.reader.finish(() => true); b.reader.dispose();
    await assert.rejects(pending, {name: 'AbortError'}); assert.throws(() => b.reader.begin(2, 2), /unavailable/);
    assert.equal(b.counts().retrieves, 0); assert.equal(b.counts().deleted, 1); b.reader.dispose();
  });
}

test('owned state: noncanonical first state explicitly keeps conservative queries and exact restoration', async () => {
  const b = backend(true, false); b.reader.begin(2, 2); b.reader.capture(0); await b.reader.finish(() => true);
  assert.equal(b.reader.metrics.reducedGlQueriesRequested, true); assert.equal(b.reader.metrics.reducedGlQueriesUsed, false);
  assert.match(b.reader.metrics.stateValidationFallback!, /canonical unbound pixel-pack state/);
  assert.equal(b.reader.metrics.stateQueryCalls, 12); assert.deepEqual(b.state, b.initialState);
  b.reader.begin(2, 2); b.reader.capture(0); await b.reader.finish(() => true);
  assert.equal(b.reader.metrics.reducedGlQueriesUsed, false); assert.equal(b.reader.metrics.stateQueryCalls, 12);
  assert.ok(b.reader.metrics.stateValidationFallback); assert.deepEqual(b.state, b.initialState); b.reader.dispose();
});

test('owned state: raw READ bind remains authoritative when the Three READ cache is stale', async () => {
  const b = backend(true); b.reader.begin(2, 2); b.reader.capture(0); await b.reader.finish(() => true);
  // Simulate the FRAMEBUFFER alias changing the actual READ binding without
  // changing Three's separate READ_FRAMEBUFFER cache, as internal passes can do.
  b.gl.bindFramebuffer(b.gl.READ_FRAMEBUFFER, {});
  b.reader.begin(2, 2); b.reader.capture(0); await b.reader.finish(() => true);
  assert.deepEqual(b.state, b.initialState); assert.equal(b.reader.metrics.stateQueryCalls, 0); b.reader.dispose();
});

test('owned state: skips redundant error reads during repeated polls but retains boundary checks', async () => {
  for (const reduced of [false, true]) {
    const b = backend(reduced); b.reader.begin(2, 2); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
    const pending = b.reader.finish(() => true); const afterFirstPoll = b.counts().errorChecks;
    b.setStatus(b.gl.CONDITION_SATISFIED); await pending;
    assert.equal(b.counts().polls, 2);
    assert.equal(b.counts().errorChecks - afterFirstPoll, reduced ? 1 : 2);
    assert.equal(b.reader.metrics.errorCheckCalls, b.counts().errorChecks);
    assert.equal(b.reader.metrics.stateQueryCalls, b.counts().parameterQueries); b.reader.dispose();
  }
});

test('planned final capture coalesces read and fence errors into checked extraction only in owned mode', async () => {
  for (const reduced of [false, true]) {
    const b = backend(reduced);
    for (let frame = 0; frame < 2; frame++) {
      b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0);
      const pair = await b.reader.finish(() => true);
      assert.deepEqual(Array.from(pair.beauty.data), [8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7]);
      assert.equal(b.reader.metrics.deferredSubmissionChecksUsed, reduced);
      assert.equal(b.reader.metrics.errorCheckCalls, (frame === 0 ? 1 : 0) + (reduced ? 1 : 4));
      assert.deepEqual(b.state, b.initialState);
    }
    b.reader.dispose();
  }
});

test('planned final read failure rejects stale reused pixels at extraction before any result is returned', async () => {
  const b = backend(true); b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0); await b.reader.finish(() => true);
  b.reader.begin(2, 2, {expectedCaptures: 1}); b.fail('read');
  assert.doesNotThrow(() => b.reader.capture(0), 'final read error remains pending until the checked retrieval');
  assert.equal(b.reader.metrics.errorCheckCalls, 0);
  await assert.rejects(b.reader.finish(() => true), /CPU retrieval failed \(GL 0x502\)/);
  assert.equal(b.reader.metrics.completed, false); assert.equal(b.reader.metrics.errorCheckCalls, 1);
  assert.deepEqual(b.state, b.initialState);
  b.fail(null); b.setPixels(new Uint8Array(16).fill(77)); b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0);
  const resumed = await b.reader.finish(() => true); assert.ok(resumed.beauty.data.every(value => value === 77)); b.reader.dispose();
});

test('planned final fence submission errors remain pending and reject before publication', async () => {
  const b = backend(true); b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0); b.fail('fence-submission');
  await assert.rejects(b.reader.finish(() => true), /CPU retrieval failed \(GL 0x502\)/);
  assert.equal(b.reader.metrics.completed, false); assert.equal(b.reader.metrics.deferredSubmissionChecksUsed, true);
  assert.deepEqual(b.state, b.initialState); b.reader.dispose();
});

test('two-capture plan validates beauty before the intervening Three render and defers only the final camera read', async () => {
  const failed = backend(true); failed.reader.begin(2, 2, {expectedCaptures: 2}); failed.fail('read');
  assert.throws(() => failed.reader.capture(0), /read submission failed \(GL 0x502\)/);
  assert.equal(failed.reader.metrics.deferredSubmissionChecksUsed, false); assert.equal(failed.counts().retrieves, 0); failed.reader.dispose();
  const b = backend(true); b.reader.begin(2, 2, {expectedCaptures: 2}); b.reader.capture(0);
  assert.equal(b.reader.metrics.deferredSubmissionChecksUsed, false);
  assert.equal(b.gl.getError(), b.gl.NO_ERROR, 'an intervening renderer cannot consume an unchecked beauty read error');
  b.fail('read'); b.reader.capture(1); assert.equal(b.reader.metrics.deferredSubmissionChecksUsed, true);
  await assert.rejects(b.reader.finish(() => true), /CPU retrieval failed \(GL 0x502\)/);
  assert.equal(b.reader.metrics.completed, false); assert.deepEqual(b.state, b.initialState); b.reader.dispose();
});

test('declared capture counts reject premature finish and captures after the planned final read', async () => {
  const b = backend(true); b.reader.begin(2, 2, {expectedCaptures: 2}); b.reader.capture(0);
  await assert.rejects(b.reader.finish(() => true), /capture plan is incomplete/);
  b.reader.cancel(); b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0);
  assert.throws(() => b.reader.capture(1), /capture plan is already complete/);
  const pair = await b.reader.finish(() => true); assert.equal(pair.camera, null); b.reader.dispose();
});

test('planned deferred checks preserve allocation, extraction, lost-context, deadline and failed-fence rejection', async () => {
  for (const failure of ['allocation', 'extraction', 'extraction-context', 'lost', 'poll-context', 'wait', 'timeout', 'revoked'] as const) {
    const b = backend(true); b.reader.begin(2, 2, {expectedCaptures: 1});
    if (failure === 'allocation') {
      b.fail(failure); assert.throws(() => b.reader.capture(0), /buffer allocation/);
    } else {
      b.reader.capture(0);
      if (failure === 'lost') b.lose();
      else if (failure === 'wait') b.setStatus(b.gl.WAIT_FAILED);
      else if (failure === 'timeout') b.setStatus(b.gl.TIMEOUT_EXPIRED);
      else if (failure !== 'revoked') b.fail(failure);
      await assert.rejects(b.reader.finish(() => failure !== 'revoked', 0));
    }
    assert.equal(b.reader.metrics.completed, false, failure); assert.deepEqual(b.state, b.initialState); b.reader.dispose();
  }
});

test('planned deferred cancellation cannot publish or mutate a newer completed pair', async () => {
  const b = backend(true); b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0); b.setStatus(b.gl.TIMEOUT_EXPIRED);
  const old = b.reader.finish(() => true); b.reader.cancel();
  b.reader.begin(2, 2, {expectedCaptures: 1}); b.reader.capture(0); b.setStatus(b.gl.ALREADY_SIGNALED);
  await b.reader.finish(() => true); const metrics = b.reader.metrics;
  await assert.rejects(old, {name: 'AbortError'}); assert.deepEqual(b.reader.metrics, metrics);
  assert.equal(b.counts().retrieves, 1); assert.equal(metrics.completed, true); b.reader.dispose();
});
