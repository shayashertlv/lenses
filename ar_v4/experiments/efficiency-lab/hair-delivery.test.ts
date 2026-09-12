import assert from 'node:assert/strict';
import {test} from 'node:test';
import {HairDeliveryLog} from './hair-delivery.ts';
import type {HairDeliveryTrace} from './hair-delivery.ts';
import type {HairRequestTiming} from './hair-cost/delivery.ts';
import {HairClient} from './hair-cost/client.ts';
import type {HairWorkerPort} from './hair-cost/client.ts';
import type {HairWorkerRequest} from './hair-cost/protocol.ts';
import {CATEGORY_EXTRACTION_PROTOCOL} from './hair-cost/protocol.ts';
import {extractCategoryMask} from './hair-cost/extraction.ts';
import {HAIR_MODELS} from '../hair-live-preview/models.ts';
import {hashHairMasks} from '../hair-live-preview/hair-protocol.ts';
import type {HairRawOutput} from '../hair-live-preview/hair-protocol.ts';

const pending = (extra: Partial<HairRequestTiming> = {}): HairRequestTiming => ({
  requestId: 2, sequence: 11, releaseWorkerEarly: false, categoryExtractionMode: 'sdk',
  submittedAtMs: 110, receivedAtMs: null, workerReleasedAtMs: null, validatedAtMs: null,
  hashStartedAtMs: null, completedAtMs: null, validationMs: null, hashMs: null,
  workerValidationMs: null, workerInferenceMs: null, workerExtractionMs: null, workerElapsedMs: null,
  pendingAtSubmission: 0, outcome: 'pending', reason: null, ...extra,
});
const completed = (): HairRequestTiming => pending({receivedAtMs: 130, validatedAtMs: 131,
  hashStartedAtMs: 132, workerReleasedAtMs: 149, completedAtMs: 150, validationMs: 1, hashMs: 17,
  workerValidationMs: 0.1, workerInferenceMs: 10, workerExtractionMs: 8, workerElapsedMs: 19, outcome: 'completed'});
const frame = (extra: Partial<HairDeliveryTrace> = {}): HairDeliveryTrace => ({
  sessionId: 'session', generation: 1, sequence: 11, pipeline: 'g', capturedAtMs: 100, observedAtMs: 120,
  publicationAtMs: null, usedAtPublication: null, disposedAtMs: null, timing: pending(), ...extra,
});

test('request completion after publication retains both the missed deadline and terminal timing', () => {
  const log = new HairDeliveryLog('session');
  log.record(frame());
  log.record(frame({observedAtMs: 135, publicationAtMs: 134, usedAtPublication: false,
    timing: pending({receivedAtMs: 130, validatedAtMs: 131, hashStartedAtMs: 132})}));
  log.record(frame({observedAtMs: 155, publicationAtMs: 134, usedAtPublication: false,
    disposedAtMs: 136, timing: completed()}));
  const result = log.export();
  assert.equal(result.requests.length, 1); assert.equal(result.rejected, 0);
  assert.equal(result.requests[0]!.publicationAtMs, 134); assert.equal(result.requests[0]!.usedAtPublication, false);
  assert.equal(result.requests[0]!.disposedAtMs, 136); assert.equal(result.requests[0]!.timing!.completedAtMs, 150);
  assert.equal(result.requests[0]!.timing!.outcome, 'completed');
});

test('late V and SDK outcomes retain exact scalar extraction accounting without private payloads', () => {
  for (const path of ['rgba8-readback', 'rgba8-sdk-fallback', 'sdk-copy'] as const) {
    const timing: HairRequestTiming = {...completed(), categoryExtractionMode: path === 'sdk-copy' ? 'sdk' : 'rgba8',
      categoryPath: path, categoryRetrievalMs: 5, categoryConversionMs: 1, categoryCopyMs: .2,
      categoryTotalMs: 7.8, categoryAttemptMs: path === 'sdk-copy' ? null : path === 'rgba8-readback' ? 7.5 : 1.5,
      categoryReadbackBytes: path === 'rgba8-readback' ? 3686400 : path === 'sdk-copy' ? null : 0,
      categoryFallbackReason: path === 'rgba8-sdk-fallback' ? 'not-gpu-only' : null};
    const log = new HairDeliveryLog('session');
    log.record(frame({pipeline: 'mask-bytes', timing: pending({categoryExtractionMode: timing.categoryExtractionMode})}));
    log.record(frame({pipeline: 'mask-bytes', observedAtMs: 160, publicationAtMs: 134, usedAtPublication: false, disposedAtMs: 135,
      timing: Object.assign(timing, {categoryPixels: new Uint8Array([1, 2, 3]), sourceSHA256: 'private'})}));
    const result = log.export(); assert.equal(result.rejected, 0); assert.equal(result.requests.length, 1);
    assert.equal(result.requests[0]!.usedAtPublication, false);
    const copy = result.requests[0]!.timing!;
    assert.equal(copy.categoryPath, path); assert.equal(copy.categoryRetrievalMs, 5);
    assert.equal(copy.categoryTotalMs, 7.8); assert.equal(copy.categoryAttemptMs, timing.categoryAttemptMs);
    assert.equal(copy.categoryReadbackBytes, timing.categoryReadbackBytes);
    assert.equal(copy.categoryFallbackReason, timing.categoryFallbackReason);
    assert.equal(JSON.stringify(result).includes('private'), false);
    assert.equal(JSON.stringify(result).includes('categoryPixels'), false);
  }
});

test('extraction trace rejects arbitrary failure text and invalid byte or timing counters', () => {
  const valid: HairRequestTiming = {...completed(), categoryExtractionMode: 'rgba8', categoryPath: 'rgba8-readback',
    categoryRetrievalMs: 1, categoryConversionMs: 0, categoryCopyMs: 0, categoryReadbackBytes: 16, categoryFallbackReason: null};
  for (const delta of [{categoryPath: 'unknown'}, {categoryFallbackReason: 'private stack trace'},
    {categoryReadbackBytes: -1}, {categoryReadbackBytes: 1.5}, {categoryRetrievalMs: NaN},
    {categoryTotalMs: 2}, {categoryAttemptMs: 1}, {categoryTotalMs: NaN, categoryAttemptMs: 0},
    {categoryTotalMs: 2, categoryAttemptMs: -1}, {categoryTotalMs: .5, categoryAttemptMs: 0},
    {categoryTotalMs: 9, categoryAttemptMs: 1}, {categoryTotalMs: 2, categoryAttemptMs: 3}]) {
    const log = new HairDeliveryLog('session');
    log.record(frame({observedAtMs: 160, timing: {...valid, ...delta}}));
    assert.equal(log.export().rejected, 1); assert.equal(log.export().requests.length, 0);
  }
});

test('real client forwards complete SDK and failed-V-attempt costs through delayed hashing into the late ledger', async () => {
  for (const mode of ['sdk', 'rgba8'] as const) {
    let last!: HairWorkerRequest;
    const worker: HairWorkerPort = {onmessage: null, onerror: null, onmessageerror: null,
      postMessage(message) {last = message;}, terminate() {}};
    let release!: () => void;
    const gate = new Promise<void>(resolve => {release = resolve;});
    const hashMasks = ((output: HairRawOutput) => gate.then(() => hashHairMasks(output))) as typeof hashHairMasks;
    const client = new HairClient('hair-only', {createWorker: () => worker, outputMode: 'category-only', delegate: 'GPU', hashMasks});
    const model = HAIR_MODELS['hair-only'];
    try {
      const initialized = client.initialize(new AbortController().signal);
      worker.onmessage!({data: {type: 'ready', requestId: last.requestId, sessionNonce: last.sessionNonce,
        model: model.id, modelSHA256: model.sha256, labels: [...model.labels], hairIndex: model.hairIndex,
        runningMode: 'IMAGE', delegate: 'GPU', outputMode: 'category-only', initializationMs: 1,
        categoryExtractionProtocol: CATEGORY_EXTRACTION_PROTOCOL}} as MessageEvent<unknown>);
      await initialized;
      let sdkReads = 0;
      const extracted = extractCategoryMask({width: 2, height: 2,
        hasUint8Array: () => mode === 'sdk', hasFloat32Array: () => false, hasWebGLTexture: () => mode === 'rgba8',
        canvas: {getContext: () => null} as unknown as OffscreenCanvas, getAsWebGLTexture: () => ({} as WebGLTexture),
        getAsUint8Array: () => {sdkReads++; return new Uint8Array([0, 1, 1, 0]);},
        getAsFloat32Array: () => {throw new Error('Unexpected float getter');}}, mode);
      assert.equal(sdkReads, 1); assert.equal(extracted.metrics.path, mode === 'sdk' ? 'sdk-copy' : 'rgba8-sdk-fallback');
      const log = new HairDeliveryLog('session'), capturedAtMs = performance.now();
      let publicationAtMs: number | null = null;
      const observe = (timing: Readonly<HairRequestTiming>): void => log.record({sessionId: 'session', generation: 1, sequence: 11,
        pipeline: mode === 'sdk' ? 'g' : 'mask-bytes', capturedAtMs, observedAtMs: performance.now(),
        publicationAtMs, usedAtPublication: publicationAtMs === null ? null : false, disposedAtMs: null, timing});
      const request = client.beginSegment({width: 2, height: 2, close() {}} as ImageBitmap, 'a'.repeat(64), 11, mode, false, observe);
      worker.onmessage!({data: {type: 'result', requestId: last.requestId, sessionNonce: last.sessionNonce, output: {
        sourceSHA256: 'a'.repeat(64), sequence: 11, model: model.id, modelSHA256: model.sha256, labels: [...model.labels], hairIndex: model.hairIndex,
        width: 2, height: 2, category: extracted.category, outputMode: 'category-only', delegate: 'GPU',
        inferenceMs: 1, extractionMs: extracted.metrics.totalMs + .5, categoryExtraction: extracted.metrics,
      }}} as MessageEvent<unknown>);
      assert.equal(request.timing().outcome, 'pending'); assert.equal(request.timing().categoryTotalMs, extracted.metrics.totalMs);
      publicationAtMs = performance.now(); observe(request.timing()); release(); await request.result;
      const exported = log.export(); assert.equal(exported.rejected, 0); assert.equal(exported.requests.length, 1);
      const trace = exported.requests[0]!, timing = trace.timing!;
      assert.equal(trace.usedAtPublication, false); assert.equal(timing.outcome, 'completed');
      assert.ok(timing.completedAtMs! >= publicationAtMs); assert.equal(timing.categoryTotalMs, extracted.metrics.totalMs);
      assert.equal(timing.categoryAttemptMs, mode === 'sdk' ? null : extracted.metrics.rgba8WorkMs);
      assert.equal(timing.categoryFallbackReason, mode === 'sdk' ? null : 'webgl2-unavailable');
      if (mode === 'rgba8') assert.ok(timing.categoryTotalMs! >= timing.categoryAttemptMs! + timing.categoryRetrievalMs! + timing.categoryCopyMs!);
    } finally {release(); client.close();}
  }
});

test('terminal timing survives a later publication snapshot carrying old pending fields', () => {
  const log = new HairDeliveryLog('session');
  log.record(frame({observedAtMs: 150, timing: completed()}));
  log.record(frame({observedAtMs: 170, publicationAtMs: 165, usedAtPublication: true, disposedAtMs: 166}));
  assert.deepEqual(log.export().requests[0]!.timing, completed());
  assert.equal(log.export().requests[0]!.usedAtPublication, true);
  assert.equal(log.export().requests[0]!.disposedAtMs, 166);
  log.record(frame({observedAtMs: 180, timing: completed()}));
  assert.equal(log.export().requests[0]!.publicationAtMs, 165, 'Null snapshots cannot erase known publication.');
  log.record(frame({observedAtMs: 130}));
  assert.equal(log.export().requests[0]!.observedAtMs, 180); assert.equal(log.export().rejected, 1);
});

test('failure, timeout, cancellation and rejection terminal outcomes cannot become success later', () => {
  for (const [outcome, reason] of [['failed', 'worker-error'], ['timed-out', 'deadline'],
    ['cancelled', 'session-aborted'], ['rejected', 'admission-rejected']] as const) {
    const log = new HairDeliveryLog('session');
    const timing = pending({outcome, reason, completedAtMs: 125});
    log.record(frame({observedAtMs: 125, timing}));
    log.record(frame({observedAtMs: 160, timing: completed(), disposedAtMs: 155}));
    assert.equal(log.export().requests[0]!.timing!.outcome, outcome);
    assert.equal(log.export().requests[0]!.timing!.completedAtMs, 125);
    assert.equal(log.export().requests[0]!.disposedAtMs, 155);
  }
});

test('generation and request IDs retain distinct requests even on the same sequence', () => {
  const log = new HairDeliveryLog('session');
  log.record(frame());
  log.record(frame({timing: pending({requestId: 3})}));
  log.record(frame({generation: 2}));
  log.record(frame({timing: pending({requestId: null, submittedAtMs: null, pendingAtSubmission: null,
    outcome: 'rejected', reason: 'not-ready', completedAtMs: 115})}));
  const entries = log.export().requests;
  assert.equal(entries.length, 4); assert.equal(log.export().rejected, 0);
  assert.deepEqual(entries.map(r => [r.generation, r.sequence, r.timing!.requestId]),
    [[1, 11, 2], [1, 11, 3], [2, 11, 2], [1, 11, null]]);
});

test('requestless frames do not consume retention and an invalid-input preflight can lack timing sequence', () => {
  const log = new HairDeliveryLog('session', 1);
  log.record(frame({timing: null})); assert.equal(log.export().requests.length, 0);
  log.record(frame({timing: pending({requestId: null, sequence: null, submittedAtMs: null,
    outcome: 'rejected', reason: 'invalid-input', completedAtMs: 119})}));
  assert.equal(log.export().requests.length, 1); assert.equal(log.export().truncated, false);
});

test('a full ledger refuses new identities explicitly but still records terminal updates', () => {
  const log = new HairDeliveryLog('session', 1);
  log.record(frame()); log.record(frame({generation: 2}));
  log.record(frame({observedAtMs: 160, timing: completed(), publicationAtMs: 155, usedAtPublication: true}));
  const result = log.export();
  assert.equal(result.requests.length, 1); assert.equal(result.truncated, true); assert.equal(result.rejected, 1);
  assert.equal(result.requests[0]!.timing!.outcome, 'completed'); assert.equal(result.requests[0]!.usedAtPublication, true);
});

test('strict identity, timestamp and enum validation rejects malformed observations without throwing', () => {
  const corruptions: ((trace: HairDeliveryTrace) => void)[] = [
    r => {r.sessionId = 'another-session';}, r => {r.generation = -1;}, r => {r.generation = 1.5;},
    r => {r.sequence = Number.MAX_SAFE_INTEGER + 1;}, r => {r.sequence = NaN;},
    r => {r.pipeline = 'unknown' as never;}, r => {r.observedAtMs = 99;},
    r => {r.publicationAtMs = 121; r.usedAtPublication = false;}, r => {r.usedAtPublication = true;},
    r => {r.disposedAtMs = 99;}, r => {r.publicationAtMs = 115; r.usedAtPublication = false; r.disposedAtMs = 114;},
    r => {r.timing!.sequence = 12;}, r => {r.timing!.sequence = null;}, r => {r.timing!.requestId = 2.1;},
    r => {r.timing!.submittedAtMs = 99;}, r => {r.timing!.receivedAtMs = 121;},
    r => {r.timing!.workerElapsedMs = Infinity;}, r => {r.timing!.hashMs = -0.1;},
    r => {r.timing!.pendingAtSubmission = -1;}, r => {r.timing!.reason = 'arbitrary private text' as never;},
    r => {r.timing!.outcome = 'completed';}, r => {r.timing!.completedAtMs = 115;},
    r => {r.timing!.receivedAtMs = 109;},
    r => {r.timing!.receivedAtMs = 113; r.timing!.validatedAtMs = 112;},
    r => {r.timing!.receivedAtMs = 113; r.timing!.validatedAtMs = 114; r.timing!.hashStartedAtMs = 113;},
  ];
  const log = new HairDeliveryLog('session');
  for (const change of corruptions) {const trace = frame(); change(trace); assert.doesNotThrow(() => log.record(trace));}
  assert.equal(log.export().requests.length, 0); assert.equal(log.export().rejected, corruptions.length);
});

test('owned-frame and request immutable facts cannot change on an upsert', () => {
  const changes: Partial<HairDeliveryTrace>[] = [{pipeline: 'hair-release'}, {capturedAtMs: 99},
    {timing: pending({submittedAtMs: 111})}, {timing: pending({releaseWorkerEarly: true})}];
  for (const change of changes) {
    const log = new HairDeliveryLog('session'); log.record(frame()); log.record(frame(change));
    assert.deepEqual(log.export().requests[0], frame()); assert.equal(log.export().rejected, 1);
  }
});

test('copying and freezing exclude private properties and prevent caller or export mutation', () => {
  const log = new HairDeliveryLog('session'), input = frame();
  Object.assign(input, {pixels: new Uint8Array([1]), mask: {private: true}, arbitrary: 'data:image/png;base64,private'});
  Object.assign(input.timing!, {sessionNonce: 'private-nonce', error: 'private error', category: new Uint8Array([2])});
  Object.defineProperty(input, 'sourceSHA256', {get() {throw new Error('Private property must not be inspected.');}});
  Object.defineProperty(input.timing!, 'landmarks', {get() {throw new Error('Private property must not be inspected.');}});
  log.record(input); const exported = log.export();
  input.capturedAtMs = 0; input.timing!.requestId = 800;
  assert.deepEqual(exported.requests[0], frame());
  assert.equal(log.export().requests[0]!.timing!.requestId, 2);
  assert.notEqual(exported.requests, log.export().requests);
  assert.notEqual(exported.requests[0]!.timing, log.export().requests[0]!.timing);
  assert.ok(Object.isFrozen(exported) && Object.isFrozen(exported.requests)
    && Object.isFrozen(exported.requests[0]) && Object.isFrozen(exported.requests[0]!.timing));
  assert.throws(() => {exported.requests[0]!.timing!.requestId = 12;}, TypeError);
  assert.throws(() => exported.requests.push(frame()), TypeError);
  assert.doesNotMatch(JSON.stringify(exported), /private|pixels|landmarks|category"|sourceSHA256|sessionNonce/);
});

test('fresh sessions retain no prior records and constructor bounds are enforced', () => {
  for (const value of ['', 'bad session', 'x'.repeat(129), undefined, null])
    assert.throws(() => new HairDeliveryLog(value as string));
  for (const limit of [0, -1, 1.5, Infinity, 100001]) assert.throws(() => new HairDeliveryLog('session', limit));
  const old = new HairDeliveryLog('session'); old.record(frame());
  const fresh = new HairDeliveryLog('next-session'); fresh.record(frame());
  assert.equal(fresh.export().requests.length, 0); assert.equal(fresh.export().rejected, 1);
  assert.equal(old.export().requests.length, 1);
});
