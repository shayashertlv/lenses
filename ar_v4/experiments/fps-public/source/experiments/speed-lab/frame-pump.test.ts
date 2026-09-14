import assert from 'node:assert/strict';
import {setImmediate} from 'node:timers/promises';
import {test} from 'node:test';
import {FramePump} from './frame-pump.ts';
import type {FramePumpOptions} from './frame-pump.ts';

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
interface Frame {sequence: number; capturedAtMs: number; pixels: number[]; closed: number;}
interface Inference {sequence: number; pixels: number[]; closed: number;}
interface Prepared extends Inference {}

function harness(mode: 'fresh' | 'overlap' = 'fresh') {
  const frames: Frame[] = [], inferences: Inference[] = [], prepared: Prepared[] = [];
  const inferenceGates = new Map<number, ReturnType<typeof deferred<Inference>>>();
  const prepareGates = new Map<number, ReturnType<typeof deferred<void>>>();
  const signals = new Map<number, AbortSignal>();
  const trace: string[] = [], published: number[] = [], errors: unknown[] = [];
  let concurrentInference = 0, owned = 0, maxOwned = 0, clock = 0;
  const options: FramePumpOptions<Frame, Inference, Prepared> = {
    mode, now: () => clock,
    identity: frame => ({sequence: frame.sequence, capturedAtMs: frame.capturedAtMs}),
    infer: (frame, signal) => {
      assert.equal(frame.closed, 0); assert.equal(++concurrentInference, 1);
      trace.push(`infer:${frame.sequence}`); signals.set(frame.sequence, signal);
      return inferenceGates.get(frame.sequence)!.promise.finally(() => {concurrentInference--;});
    },
    prepare: async (frame, inference, signal) => {
      assert.equal(frame.closed, 0); assert.equal(inference.closed, 0);
      assert.equal(inference.sequence, frame.sequence); assert.deepEqual(inference.pixels, frame.pixels);
      assert.equal(signal.aborted, false); trace.push(`prepare:${frame.sequence}`);
      await prepareGates.get(frame.sequence)?.promise;
      const output = {sequence: frame.sequence, pixels: [...frame.pixels], closed: 0};
      prepared.push(output); return output;
    },
    publish: (frame, inference, output) => {
      assert.equal(frame.closed, 0); assert.equal(inference.closed, 0); assert.equal(output.closed, 0);
      assert.equal(inference.sequence, frame.sequence); assert.equal(output.sequence, frame.sequence);
      assert.deepEqual(output.pixels, frame.pixels); trace.push(`publish:${frame.sequence}`);
      published.push(frame.sequence);
    },
    disposeFrame: frame => {assert.equal(frame.closed++, 0); owned--; trace.push(`close:${frame.sequence}`);},
    disposeInference: inference => {assert.equal(inference.closed++, 0);},
    disposePrepared: output => {assert.equal(output.closed++, 0);},
    onPublished: frame => {assert.equal(frame.closed, 0); trace.push(`completed:${frame.sequence}`);},
    onError: error => {errors.push(error);},
  };
  const pump = new FramePump(options);
  const capture = (sequence: number, capturedAtMs = sequence * 10): Frame => {
    const frame = {sequence, capturedAtMs, pixels: [sequence, 17, 255], closed: 0};
    frames.push(frame); inferenceGates.set(sequence, deferred<Inference>());
    owned++; maxOwned = Math.max(maxOwned, owned); trace.push(`capture:${sequence}`); return frame;
  };
  const offer = (sequence: number, capturedAtMs = sequence * 10) => pump.offer(() => capture(sequence, capturedAtMs));
  const resolveInference = (sequence: number) => {
    const frame = frames.find(value => value.sequence === sequence)!;
    const inference = {sequence, pixels: [...frame.pixels], closed: 0};
    inferences.push(inference); inferenceGates.get(sequence)!.resolve(inference);
  };
  return {pump, options, frames, inferences, prepared, inferenceGates, prepareGates, signals, trace, published,
    errors, capture, offer, resolveInference, maxOwned: () => maxOwned, owned: () => owned,
    clock: (value: number) => {clock = value;}};
}

test('fresh mode replaces pending snapshots, keeps exact pairs and never transiently owns a third frame', async () => {
  const h = harness();
  assert.equal(h.offer(1), true); assert.equal(h.offer(2), true); assert.equal(h.offer(3), true);
  assert.equal(h.frames[1]!.closed, 1);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:1']);
  assert.equal(h.maxOwned(), 2); assert.equal(h.pump.stats.maxOwnedFrames, 2);
  h.clock(12); h.resolveInference(1); await setImmediate();
  assert.deepEqual(h.published, [1]);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:1', 'infer:3']);
  h.clock(20); h.resolveInference(3); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1, 3]);
  assert.equal(h.owned(), 0); assert.ok(h.frames.every(frame => frame.closed === 1));
  assert.ok(h.inferences.every(result => result.closed === 1));
  assert.ok(h.prepared.every(result => result.closed === 1));
  assert.equal(h.pump.stats.replaced, 1); assert.equal(h.pump.stats.inferenceTotalMs, 20);
  assert.equal(h.pump.stats.lastInferenceMs, 8); assert.equal(h.pump.stats.maxInFlightInference, 1);
  assert.ok(h.trace.indexOf('completed:1') < h.trace.indexOf('close:1'));
  assert.deepEqual(h.errors, []);
});

test('fresh mode consumes the newest pair offered while private preparation waits', async () => {
  const h = harness(); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.resolveInference(1); await setImmediate();
  h.offer(2); h.offer(3); h.offer(4);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:1']);
  assert.deepEqual(h.published, []);
  h.prepareGates.get(1)!.resolve(); await setImmediate();
  assert.deepEqual(h.published, [1]);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:1', 'infer:4']);
  h.resolveInference(4); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1, 4]); assert.equal(h.maxOwned(), 2);
});

test('overlap starts the next inference before private prepare and locks its slot without a queue', async () => {
  const h = harness('overlap'); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.offer(2); h.offer(3);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:1']);
  h.resolveInference(1); await setImmediate();
  assert.ok(h.trace.indexOf('infer:3') < h.trace.indexOf('prepare:1'));
  assert.equal(h.pump.stats.inFlightInference, 1);
  let unwantedCapture = 0;
  assert.equal(h.pump.offer(() => {unwantedCapture++; return h.capture(4);}), false);
  h.resolveInference(3); await setImmediate();
  assert.equal(h.pump.stats.inFlightInference, 0);
  assert.equal(h.pump.offer(() => {unwantedCapture++; return h.capture(5);}), false);
  assert.equal(unwantedCapture, 0); assert.equal(h.pump.stats.lockedDrops, 2);
  assert.deepEqual(h.published, []);
  h.prepareGates.get(1)!.resolve(); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1, 3]); assert.equal(h.maxOwned(), 2);
  assert.equal(h.pump.stats.maxInFlightInference, 1); assert.equal(h.owned(), 0);
});

test('overlap can start a new offer during preparation and waits for its unfinished inference before publishing it', async () => {
  const h = harness('overlap'); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.resolveInference(1); await setImmediate(); h.offer(2);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:1', 'infer:2']);
  h.prepareGates.get(1)!.resolve(); await setImmediate();
  assert.deepEqual(h.published, [1]); assert.equal(h.pump.stats.processingFrames, 1);
  assert.equal(h.pump.stats.inFlightInference, 1);
  h.offer(3); assert.equal(h.pump.stats.ownedFrames, 2);
  h.resolveInference(2); await setImmediate();
  assert.deepEqual(h.published, [1, 2]);
  h.resolveInference(3); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1, 2, 3]); assert.equal(h.maxOwned(), 2);
});

test('stop revokes pending inference without closing worker-owned input before it settles', async () => {
  const h = harness(); h.offer(1); h.offer(2);
  h.pump.stop(); h.pump.stop();
  assert.equal(h.signals.get(1)!.aborted, true);
  assert.equal(h.frames[0]!.closed, 0); assert.equal(h.frames[1]!.closed, 1);
  let idle = false; const drained = h.pump.whenIdle().then(() => {idle = true;});
  await setImmediate(); assert.equal(idle, false);
  let captures = 0; assert.equal(h.pump.offer(() => {captures++; return h.capture(3);}), false);
  assert.equal(captures, 0);
  h.resolveInference(1); await drained;
  assert.deepEqual(h.published, []); assert.equal(h.owned(), 0);
  assert.equal(h.inferences[0]!.closed, 1); assert.deepEqual(h.errors, []);
});

test('stop during preparation and prefetched inference blocks both late publications and disposes both results', async () => {
  const h = harness('overlap'); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.offer(2); h.resolveInference(1); await setImmediate();
  h.pump.stop();
  assert.equal(h.signals.get(1)!.aborted, true); assert.equal(h.signals.get(2)!.aborted, true);
  assert.equal(h.frames[0]!.closed, 0); assert.equal(h.frames[1]!.closed, 0);
  h.prepareGates.get(1)!.resolve(); await setImmediate();
  assert.equal(h.frames[0]!.closed, 1); assert.equal(h.frames[1]!.closed, 0);
  h.resolveInference(2); await h.pump.whenIdle();
  assert.deepEqual(h.published, []); assert.equal(h.owned(), 0);
  assert.ok(h.inferences.every(value => value.closed === 1));
  assert.ok(h.prepared.every(value => value.closed === 1));
});

test('graceful Hold publishes only the active pair and drains an already prefetched next pair', async () => {
  const h = harness('overlap'); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.offer(2); h.resolveInference(1); await setImmediate();
  let idle = false; const held = h.pump.finishCurrent().then(() => {idle = true;});
  assert.equal(h.signals.get(1)!.aborted, false); assert.equal(h.signals.get(2)!.aborted, true);
  assert.equal(h.offer(3), false);
  h.prepareGates.get(1)!.resolve(); await setImmediate();
  assert.deepEqual(h.published, [1]); assert.equal(idle, false);
  h.resolveInference(2); await held;
  assert.deepEqual(h.published, [1]); assert.equal(h.owned(), 0);
  assert.equal(h.pump.stats.accepting, false); assert.deepEqual(h.errors, []);
});

test('an inference failure fails closed once and cancels the other owned pair', async () => {
  const h = harness('overlap'); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.offer(2); h.resolveInference(1); await setImmediate();
  const error = new Error('worker failed'); h.inferenceGates.get(2)!.reject(error);
  await setImmediate(); assert.equal(h.pump.stats.failed, true); assert.equal(h.pump.error, error);
  h.prepareGates.get(1)!.resolve(); await h.pump.whenIdle();
  assert.deepEqual(h.errors, [error]); assert.deepEqual(h.published, []); assert.equal(h.owned(), 0);
});

test('private preparation failure cancels next inference and cannot publish a partial pair', async () => {
  const h = harness('overlap'); h.prepareGates.set(1, deferred<void>());
  h.offer(1); h.offer(2); h.resolveInference(1); await setImmediate();
  const error = new Error('prepare failed'); h.prepareGates.get(1)!.reject(error);
  await setImmediate(); assert.equal(h.signals.get(2)!.aborted, true);
  h.resolveInference(2); await h.pump.whenIdle();
  assert.deepEqual(h.errors, [error]); assert.deepEqual(h.published, []); assert.equal(h.owned(), 0);
});

test('a stopped old pump cannot publish after a restarted pump even when its old worker completes last', async () => {
  const old = harness(), fresh = harness(); old.offer(1); old.pump.stop();
  fresh.offer(1); fresh.resolveInference(1); await fresh.pump.whenIdle();
  old.resolveInference(1); await old.pump.whenIdle();
  assert.deepEqual(old.published, []); assert.deepEqual(fresh.published, [1]);
  assert.equal(old.owned(), 0); assert.equal(fresh.owned(), 0);
});

test('duplicate or nonmonotonic metadata is disposed and never reaches a worker', async () => {
  const h = harness(); h.offer(2, 20);
  assert.equal(h.offer(1, 10), false); assert.equal(h.offer(3, 20), false); assert.equal(h.offer(4, NaN), false);
  assert.equal(h.pump.stats.invalidDrops, 3);
  assert.deepEqual(h.trace.filter(item => item.startsWith('infer:')), ['infer:2']);
  h.resolveInference(2); await h.pump.whenIdle();
  assert.deepEqual(h.published, [2]); assert.equal(h.owned(), 0); assert.deepEqual(h.errors, []);
});

test('capture runs synchronously, a stopped capture is disposed, and capture failure cancels the active pair', async () => {
  const first = harness(); let inside = false;
  assert.equal(first.pump.offer(() => {inside = true; const frame = first.capture(1); first.pump.stop(); return frame;}), false);
  assert.equal(inside, true); await first.pump.whenIdle(); assert.equal(first.owned(), 0);
  assert.deepEqual(first.trace.filter(item => item.startsWith('infer:')), []);
  const h = harness(); h.offer(1); const error = new Error('drawImage failed');
  assert.equal(h.pump.offer(() => {throw error;}), false);
  assert.equal(h.signals.get(1)!.aborted, true); h.resolveInference(1); await h.pump.whenIdle();
  assert.deepEqual(h.errors, [error]); assert.deepEqual(h.published, []); assert.equal(h.owned(), 0);
});

test('reentrant stop during synchronous publication leaves callback resources alive until it returns', async () => {
  const h = harness();
  h.options.publish = frame => {
    h.pump.stop(); assert.equal(frame.closed, 0); h.published.push(frame.sequence);
  };
  h.offer(1); h.resolveInference(1); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1]); assert.equal(h.owned(), 0);
  assert.equal(h.pump.stats.published, 1); assert.ok(!h.trace.includes('completed:1'));
});

test('cleanup failure still releases every resource, fails closed and reports once', async () => {
  const h = harness(); const error = new Error('prepared disposal failed');
  h.options.disposePrepared = output => {output.closed++; throw error;};
  h.offer(1); h.offer(2); h.resolveInference(1); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1]); assert.deepEqual(h.errors, [error]);
  assert.equal(h.owned(), 0); assert.equal(h.inferences[0]!.closed, 1);
  assert.ok(h.frames.every(value => value.closed === 1));
});

test('synchronous prefetched-worker failure revokes the active source before preparation can touch it', async () => {
  const h = harness('overlap'); const original = h.options.infer, error = new Error('postMessage failed');
  h.options.infer = (frame, signal) => {
    if (frame.sequence === 2) throw error;
    return original(frame, signal);
  };
  h.offer(1); h.offer(2); h.resolveInference(1); await h.pump.whenIdle();
  assert.deepEqual(h.trace.filter(value => value.startsWith('prepare:')), []);
  assert.deepEqual(h.published, []); assert.deepEqual(h.errors, [error]); assert.equal(h.owned(), 0);
});

test('publication failure stops the pump and releases both active and pending resources', async () => {
  const h = harness(); const error = new Error('display submission failed');
  h.options.publish = () => {throw error;};
  h.offer(1); h.offer(2); h.resolveInference(1); await h.pump.whenIdle();
  assert.deepEqual(h.errors, [error]); assert.equal(h.pump.stats.published, 0);
  assert.equal(h.owned(), 0); assert.ok(h.prepared.every(value => value.closed === 1));
});

test('a missing snapshot starts no worker and a later valid snapshot can still publish', async () => {
  const h = harness(); assert.equal(h.pump.offer(() => null), false);
  assert.equal(h.pump.stats.captureMisses, 1); assert.equal(h.pump.stats.inferenceCalls, 0);
  h.offer(1); h.resolveInference(1); await h.pump.whenIdle();
  assert.deepEqual(h.published, [1]); assert.deepEqual(h.errors, []);
});
