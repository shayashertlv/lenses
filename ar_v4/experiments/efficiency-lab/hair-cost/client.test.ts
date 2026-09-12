import test from 'node:test';
import assert from 'node:assert/strict';
import {HairClient} from './client.ts';
import type {HairWorkerPort, HairClientOptions, HairRequestTiming} from './client.ts';
import {HAIR_MODELS} from '../../hair-live-preview/models.ts';
import type {HairModelId} from '../../hair-live-preview/models.ts';
import type {HairOutputMode} from '../../hair-live-preview/hair-protocol.ts';
import {hashHairMasks} from '../../hair-live-preview/hair-protocol.ts';
import type {HairRawOutput} from '../../hair-live-preview/hair-protocol.ts';
import {CATEGORY_EXTRACTION_PROTOCOL} from './protocol.ts';
import type {HairWorkerRequest, CategoryExtractionMode, CategoryExtractionMetrics} from './protocol.ts';

function metrics(mode: CategoryExtractionMode = 'sdk'): CategoryExtractionMetrics {
  return {mode, path: mode === 'sdk' ? 'sdk-copy' : 'direct-byte-copy', hasUint8: true, hasFloat32: false,
    hasWebGLTexture: false, retrievalMs: .01, copyMs: .02, conversionMs: 0, totalMs: .04,
    retrievedArrayBytes: 4, categoryBytesCopied: 4, categoryBytesConverted: 0,
    maskPixels: 4, ownedCategoryBytesAllocated: 4, explicitFloatTemporaryBytesAvoided: 0, explicitCategoryCopyBytesAvoided: 0};
}

class FakeWorker implements HairWorkerPort {
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null = null;
  messages: HairWorkerRequest[] = [];
  transfers: Transferable[][] = [];
  terminated = 0;
  throwOnPost = false;
  postMessage(message: HairWorkerRequest, transfer: Transferable[] = []): void {
    if (this.throwOnPost) throw new Error('Synthetic transfer failure.');
    this.messages.push(message); this.transfers.push(transfer);
  }
  terminate(): void { this.terminated++; }
  emit(data: unknown): void { this.onmessage?.({data} as MessageEvent<unknown>); }
  ready(patch: Record<string, unknown> = {}): void {
    const message = this.messages.at(-1)!; assert.equal(message.type, 'initialize');
    const model = HAIR_MODELS[message.modelId];
    this.emit({type: 'ready', sessionNonce: message.sessionNonce, requestId: message.requestId, model: model.id,
      modelSHA256: model.sha256, labels: [...model.labels], hairIndex: 1, runningMode: 'IMAGE', delegate: 'CPU',
      outputMode: message.outputMode ?? 'full', initializationMs: 2, categoryExtractionProtocol: CATEGORY_EXTRACTION_PROTOCOL, ...patch});
  }
  result(patch: Record<string, unknown> = {}, envelope: Record<string, unknown> = {}): void {
    const message = this.messages.at(-1)!; assert.equal(message.type, 'segment');
    const initialize = this.messages.find(value => value.type === 'initialize')!; assert.equal(initialize.type, 'initialize');
    const model = HAIR_MODELS[initialize.modelId];
    this.emit({type: 'result', sessionNonce: message.sessionNonce, requestId: message.requestId, output: {
      sourceSHA256: message.sourceSHA256, sequence: message.sequence, model: model.id, modelSHA256: model.sha256,
      labels: [...model.labels], hairIndex: 1, width: 2, height: 2, category: new Uint8Array([0, 1, 1, 0]),
      ...(initialize.outputMode === 'category-only' ? {outputMode: 'category-only'} : {outputMode: 'full', confidence: new Float32Array([0, .5, 1, .25])}),
      inferenceMs: 5, extractionMs: .1, categoryExtraction: metrics(message.categoryExtractionMode), ...patch}, ...envelope});
  }
}
function bitmap(width = 2, height = 2): {image: ImageBitmap; closed: () => number} {
  let closed = 0;
  return {image: {width, height, close: () => { closed++; }} as ImageBitmap, closed: () => closed};
}
async function ready(model: HairModelId = 'hair-only', outputMode: HairOutputMode = 'full', options: HairClientOptions = {}) {
  const worker = new FakeWorker(), controller = new AbortController();
  const client = new HairClient(model, {...options, createWorker: () => worker, outputMode});
  const startup = client.initialize(controller.signal); worker.ready(); await startup;
  return {worker, controller, client};
}
const source = 'a'.repeat(64);

test('initialization cancellation terminates worker and ignores a late ready reply', async () => {
  const worker = new FakeWorker(), controller = new AbortController(), client = new HairClient('hair-only', {createWorker: () => worker});
  const startup = client.initialize(controller.signal), rejected = assert.rejects(startup, {name: 'AbortError'});
  const late = worker.onmessage; controller.abort(); await rejected;
  assert.equal(worker.terminated, 1); worker.ready(); late?.({data: {type: 'ready'}} as MessageEvent<unknown>);
  const frame = bitmap(); await assert.rejects(client.segment(frame.image, source, 0), /not ready/); assert.equal(frame.closed(), 1);
  client.close(); assert.equal(worker.terminated, 1);
});

test('wrong pinned readiness closes the worker before a frame can run', async () => {
  const worker = new FakeWorker(), client = new HairClient('hair-only', {createWorker: () => worker});
  const startup = client.initialize(new AbortController().signal), rejected = assert.rejects(startup, /pinned/);
  worker.ready({modelSHA256: '0'.repeat(64)}); await rejected; assert.equal(worker.terminated, 1);
});

test('one owned in-flight frame; every rejected image closes without corrupting the pending frame', async () => {
  const {worker, client} = await ready(), first = bitmap(), second = bitmap();
  const pending = client.segment(first.image, source, 0);
  await assert.rejects(client.segment(second.image, source, 1), /already owns/); assert.equal(second.closed(), 1);
  assert.deepEqual(worker.transfers.at(-1), [first.image]); worker.result(); const result = await pending;
  assert.equal(first.closed(), 1); assert.equal(result.sequence, 0); assert.equal(result.sourceSHA256, source);
  assert.ok(result.outputMode !== 'category-only');
  assert.match(result.categorySHA256, /^[a-f0-9]{64}$/); assert.match(result.confidenceSHA256, /^[a-f0-9]{64}$/);
  client.close();
});

test('wrong session and late request replies cannot replace the current pair', async () => {
  const {worker, client} = await ready(), first = bitmap(), pending = client.segment(first.image, source, 9);
  worker.result({sourceSHA256: 'b'.repeat(64)}, {sessionNonce: 'another-session'});
  worker.result({sequence: 1}, {requestId: 1});
  worker.result(); const result = await pending; assert.equal(result.sequence, 9); assert.equal(result.sourceSHA256, source); client.close();
});

test('abort after readiness cancels inference and a late result stays unpublished', async () => {
  const {worker, client, controller} = await ready(), image = bitmap();
  const pending = client.segment(image.image, source, 0), rejected = assert.rejects(pending, {name: 'AbortError'});
  const handler = worker.onmessage; controller.abort(); await rejected;
  worker.result(); handler?.({data: {type: 'result'}} as MessageEvent<unknown>);
  assert.equal(image.closed(), 1); assert.equal(worker.terminated, 1); client.close();
});

test('close during asynchronous raw-mask hashing rejects rather than publishing a stale result', async () => {
  const {worker, client} = await ready(), image = bitmap(), pending = client.segment(image.image, source, 0);
  const rejected = assert.rejects(pending, /closed/); worker.result(); client.close(); await rejected;
  assert.equal(image.closed(), 1); assert.equal(worker.terminated, 1);
});

test('invalid worker categories close the client and preserve caller fallback responsibility', async () => {
  const {worker, client} = await ready(), image = bitmap(), pending = client.segment(image.image, source, 0);
  const rejected = assert.rejects(pending, /invalid category/); worker.result({category: new Uint8Array([0, 4, 1, 0])}); await rejected;
  assert.equal(image.closed(), 1); assert.equal(worker.terminated, 1);
});

test('preflight rejects empty images, invalid identities and repeated sequences, closing each bitmap', async () => {
  const {worker, client} = await ready();
  for (const [width, identity, sequence] of [[0, source, 0], [2, 'bad-hash', 0], [2, source, -.5]] as const) {
    const image = bitmap(width); await assert.rejects(client.segment(image.image, identity, sequence)); assert.equal(image.closed(), 1);
  }
  const first = bitmap(), pending = client.segment(first.image, source, 0); worker.result(); await pending;
  const repeat = bitmap(); await assert.rejects(client.segment(repeat.image, source, 0), /must increase/); assert.equal(repeat.closed(), 1);
  client.close();
});

test('transfer failure closes the owned bitmap and terminal worker', async () => {
  const {worker, client} = await ready(), image = bitmap(); worker.throwOnPost = true;
  await assert.rejects(client.segment(image.image, source, 0), /transfer failure/);
  assert.equal(image.closed(), 1); assert.equal(worker.terminated, 1);
});

test('startup and segmentation have bounded deadlines and can restart only with a fresh client', async () => {
  const slowWorker = new FakeWorker(), slow = new HairClient('hair-only', {createWorker: () => slowWorker, initializeTimeoutMs: 5});
  await assert.rejects(slow.initialize(new AbortController().signal), /startup timed out/); assert.equal(slowWorker.terminated, 1);
  const worker = new FakeWorker(), client = new HairClient('selfie-multiclass', {createWorker: () => worker, segmentTimeoutMs: 5});
  const startup = client.initialize(new AbortController().signal); worker.ready(); await startup;
  const image = bitmap(); await assert.rejects(client.segment(image.image, source, 0), /segmentation timed out/);
  assert.equal(image.closed(), 1); assert.equal(worker.terminated, 1);
  await assert.rejects(client.initialize(new AbortController().signal), /closed/);
  const fresh = await ready('selfie-multiclass'), freshImage = bitmap(), result = fresh.client.segment(freshImage.image, source, 0);
  fresh.worker.result(); assert.equal((await result).model, 'selfie-multiclass'); fresh.client.close();
});

test('unreadable worker messages fail the owned request and close once', async () => {
  const {worker, client} = await ready(), image = bitmap(), pending = client.segment(image.image, source, 0);
  const rejected = assert.rejects(pending, /unreadable/); worker.onmessageerror?.({} as MessageEvent<unknown>); await rejected;
  assert.equal(image.closed(), 1); client.close(); assert.equal(worker.terminated, 1);
});

test('an explicitly selected GPU must acknowledge and produce GPU output', async () => {
  const worker = new FakeWorker(), client = new HairClient('hair-only', {createWorker: () => worker, delegate: 'GPU'});
  const startup = client.initialize(new AbortController().signal);
  assert.equal(worker.messages[0]!.type, 'initialize');
  assert.equal((worker.messages[0] as {delegate: string}).delegate, 'GPU');
  worker.ready({delegate: 'GPU'}); await startup;
  const image = bitmap(), pending = client.segment(image.image, source, 0);
  worker.result({delegate: 'GPU'});
  assert.equal((await pending).delegate, 'GPU'); client.close();
});

test('a GPU session rejects a silently substituted CPU mask', async () => {
  const worker = new FakeWorker(), client = new HairClient('hair-only', {createWorker: () => worker, delegate: 'GPU'});
  const startup = client.initialize(new AbortController().signal); worker.ready({delegate: 'GPU'}); await startup;
  const image = bitmap(), pending = client.segment(image.image, source, 0);
  const rejected = assert.rejects(pending, /another inference delegate/);
  worker.result({delegate: 'CPU'}); await rejected;
  assert.equal(worker.terminated, 1); assert.equal(image.closed(), 1);
});

test('category-only mode initializes explicitly and returns owned category bytes without confidence or its hash', async () => {
  for (const model of ['hair-only', 'selfie-multiclass'] as const) {
    const {worker, client} = await ready(model, 'category-only'), image = bitmap();
    const initialize = worker.messages[0]!; assert.equal(initialize.type, 'initialize');
    assert.equal(initialize.outputMode, 'category-only'); assert.equal(client.outputMode, 'category-only');
    const category = new Uint8Array([0, 1, 1, 0]), pending = client.segment(image.image, source, 0);
    worker.result({category}); const result = await pending; category.fill(0);
    assert.equal(result.outputMode, 'category-only'); assert.deepEqual([...result.category], [0, 1, 1, 0]);
    assert.match(result.categorySHA256, /^[a-f0-9]{64}$/);
    assert.equal('confidence' in result, false); assert.equal('confidenceSHA256' in result, false);
    assert.equal(image.closed(), 1); client.close();
  }
});

test('category-only readiness cannot silently fall back to full mode or omit its acknowledgement', async () => {
  for (const outputMode of ['full', undefined]) {
    const worker = new FakeWorker(), client = new HairClient('hair-only', {createWorker: () => worker, outputMode: 'category-only'});
    const startup = client.initialize(new AbortController().signal), rejected = assert.rejects(startup, /output mode/);
    worker.ready({outputMode}); await rejected; assert.equal(worker.terminated, 1);
  }
});

test('output mode swaps and fabricated confidence fail closed without publishing a category-only result', async () => {
  for (const patch of [{outputMode: 'full'}, {outputMode: undefined}, {confidence: new Float32Array(4)}, {confidenceSHA256: 'b'.repeat(64)}]) {
    const {worker, client} = await ready('hair-only', 'category-only'), image = bitmap();
    const pending = client.segment(image.image, source, 0), rejected = assert.rejects(pending, /output mode|confidence/);
    worker.result(patch); await rejected; assert.equal(worker.terminated, 1); assert.equal(image.closed(), 1);
  }
  const {worker, client} = await ready(), image = bitmap(), pending = client.segment(image.image, source, 0);
  const rejected = assert.rejects(pending, /output mode/); worker.result({outputMode: 'category-only'}); await rejected;
  assert.equal(worker.terminated, 1);
});

test('category-only hashing still honors cancellation and cannot leak a late result into a fresh session', async () => {
  const {worker, client} = await ready('hair-only', 'category-only'), image = bitmap();
  const pending = client.segment(image.image, source, 0), rejected = assert.rejects(pending, /closed/);
  worker.result(); client.close(); await rejected; assert.equal(image.closed(), 1);
  const fresh = await ready('hair-only', 'category-only'), second = bitmap(), next = fresh.client.segment(second.image, source, 0);
  worker.result({sequence: 999}); fresh.worker.result(); assert.equal((await next).sequence, 0); fresh.client.close();
});

test('default full mode accepts existing full fixtures that omit the mode field', async () => {
  const worker = new FakeWorker(), client = new HairClient('hair-only', {createWorker: () => worker});
  assert.equal(client.outputMode, 'full');
  const startup = client.initialize(new AbortController().signal); worker.ready({outputMode: undefined}); await startup;
  const image = bitmap(), pending = client.segment(image.image, source, 0); worker.result({outputMode: undefined});
  const result = await pending; assert.ok(result.outputMode !== 'category-only');
  assert.deepEqual([...result.confidence], [0, .5, 1, .25]); assert.match(result.confidenceSHA256, /^[a-f0-9]{64}$/); client.close();
});

test('instrumented ready must acknowledge its extraction protocol before owning any image', async () => {
  for (const categoryExtractionProtocol of [undefined, 'different-protocol']) {
    const worker = new FakeWorker(), client = new HairClient('hair-only', {createWorker: () => worker});
    const startup = client.initialize(new AbortController().signal), rejected = assert.rejects(startup, /extraction protocol/);
    worker.ready({categoryExtractionProtocol}); await rejected; assert.equal(worker.terminated, 1);
  }
});

test('SDK default and explicit direct requests freeze mode independently and keep the same mask hashes', async () => {
  const {worker, client} = await ready('hair-only', 'category-only');
  const first = bitmap(), baseline = client.segment(first.image, source, 0);
  const sdkRequest = worker.messages.at(-1)!; assert.equal(sdkRequest.type, 'segment'); assert.equal(sdkRequest.categoryExtractionMode, 'sdk');
  worker.result(); const sdk = await baseline;
  const second = bitmap(), candidate = client.segment(second.image, source, 1, 'direct');
  const directRequest = worker.messages.at(-1)!; assert.equal(directRequest.type, 'segment'); assert.equal(directRequest.categoryExtractionMode, 'direct');
  const rejectedImage = bitmap(); await assert.rejects(client.segment(rejectedImage.image, source, 2, 'sdk'), /already owns/);
  assert.equal(rejectedImage.closed(), 1); assert.equal(worker.messages.at(-1), directRequest);
  const telemetry = metrics('direct'); worker.result({categoryExtraction: telemetry}); telemetry.totalMs = 999;
  const direct = await candidate;
  assert.equal(sdk.categorySHA256, direct.categorySHA256); assert.deepEqual(sdk.category, direct.category);
  assert.equal(direct.categoryExtraction.mode, 'direct'); assert.equal(direct.categoryExtraction.totalMs, .04);
  assert.equal(direct.categoryExtraction.explicitCategoryCopyBytesAvoided, 0); assert.equal(first.closed(), 1); assert.equal(second.closed(), 1);
  client.close();
});

test('invalid extraction selection closes only its rejected image without consuming the next sequence', async () => {
  const {worker, client} = await ready(), bad = bitmap();
  await assert.rejects(client.segment(bad.image, source, 0, 'fast' as CategoryExtractionMode), /extraction mode/);
  assert.equal(bad.closed(), 1); assert.equal(worker.messages.length, 1);
  const next = bitmap(), pending = client.segment(next.image, source, 0); worker.result(); await pending; client.close();
});

test('a direct request rejects another mode, missing telemetry and dishonest path/allocation/timing claims', async () => {
  const valid = metrics('direct');
  for (const categoryExtraction of [undefined, metrics('sdk'), {...valid, path: 'sdk-copy'}, {...valid, hasUint8: 'true'},
    {...valid, maskPixels: 3}, {...valid, ownedCategoryBytesAllocated: 0}, {...valid, explicitCategoryCopyBytesAvoided: 4},
    {...valid, retrievedArrayBytes: 16}, {...valid, categoryBytesCopied: 0}, {...valid, categoryBytesConverted: 4},
    {...valid, retrievalMs: -1}, {...valid, copyMs: Infinity}, {...valid, conversionMs: .001},
    {...valid, totalMs: .01}, {...valid, totalMs: .2}]) {
    const {worker, client} = await ready('hair-only', 'category-only'), image = bitmap();
    const pending = client.segment(image.image, source, 0, 'direct'), rejected = assert.rejects(pending, /extraction/);
    worker.result({categoryExtraction}); await rejected; assert.equal(worker.terminated, 1); assert.equal(image.closed(), 1);
  }
});

test('direct conversion telemetry remains tied to original pair/model/shape/delegate validation', async () => {
  const floatMetrics: CategoryExtractionMetrics = {...metrics('direct'), path: 'direct-float-conversion', hasUint8: false,
    hasFloat32: true, retrievalMs: .01, conversionMs: .02, copyMs: 0,
    retrievedArrayBytes: 16, categoryBytesCopied: 0, categoryBytesConverted: 4,
    explicitFloatTemporaryBytesAvoided: 16, explicitCategoryCopyBytesAvoided: 4};
  for (const patch of [{sourceSHA256: 'b'.repeat(64)}, {sequence: 7}, {width: 1}, {model: 'selfie-multiclass'},
    {modelSHA256: '0'.repeat(64)}, {labels: ['wrong', 'hair']}, {delegate: 'GPU'}, {extractionMs: NaN}]) {
    const {worker, client} = await ready('hair-only', 'category-only'), image = bitmap();
    const pending = client.segment(image.image, source, 0, 'direct'), rejected = assert.rejects(pending);
    worker.result({categoryExtraction: floatMetrics, ...patch}); await rejected; assert.equal(worker.terminated, 1); assert.equal(image.closed(), 1);
  }
  const {worker, client} = await ready('hair-only', 'category-only'), image = bitmap();
  const pending = client.segment(image.image, source, 0, 'direct'); worker.result({categoryExtraction: floatMetrics});
  const result = await pending; assert.equal(result.categoryExtraction.explicitFloatTemporaryBytesAvoided, 16);
  assert.deepEqual([...result.category], [0, 1, 1, 0]); client.close();
});

test('a cancelled direct response cannot publish after hashing or replace a fresh SDK session', async () => {
  const {worker, client} = await ready('hair-only', 'category-only'), image = bitmap();
  const pending = client.segment(image.image, source, 0, 'direct'), rejected = assert.rejects(pending, /closed/);
  const late = worker.onmessage; worker.result(); client.close(); await rejected;
  const fresh = await ready('hair-only', 'category-only'), second = bitmap(), next = fresh.client.segment(second.image, source, 0);
  late?.({data: {type: 'result', categoryExtractionMode: 'direct'}} as MessageEvent<unknown>);
  fresh.worker.result(); assert.equal((await next).categoryExtraction.mode, 'sdk');
  assert.equal(image.closed(), 1); assert.equal(second.closed(), 1); fresh.client.close();
});

function controlledHashes() {
  const pending: {output: HairRawOutput; resolve(): void; reject(error: Error): void}[] = [];
  const hashMasks = ((output: HairRawOutput) => {
    let resolve!: () => void, reject!: (error: Error) => void;
    const gate = new Promise<void>((yes, no) => {resolve = yes; reject = no;});
    pending.push({output, resolve, reject});
    return gate.then(() => hashHairMasks(output));
  }) as typeof hashHairMasks;
  return {pending, hashMasks, releaseAll: () => {for (const item of pending) item.resolve();}};
}

test('explicit early release admits the next worker job during real mask hashing, with two owned results at most', async () => {
  const hashes = controlledHashes(), {worker, client} = await ready('hair-only', 'category-only', hashes);
  const traces: Readonly<HairRequestTiming>[] = [], images = [bitmap(), bitmap(), bitmap(), bitmap()];
  const first = client.beginSegment(images[0]!.image, source, 10, 'sdk', true, trace => {traces.push(trace);});
  let firstCompleted = false; void first.result.then(() => {firstCompleted = true;});
  try {
    worker.result({workerTiming: {inputValidationMs: .2, totalMs: 6}}); await first.workerReleased;
    assert.equal(firstCompleted, false); assert.equal(hashes.pending.length, 1);
    const second = client.beginSegment(images[1]!.image, 'b'.repeat(64), 11, 'direct', true);
    assert.equal(worker.messages.length, 3, 'The second worker request posts before the first hash completes.');
    assert.equal(second.timing().pendingAtSubmission, 2);
    assert.deepEqual(worker.transfers.at(-1), [images[1]!.image]);
    const busy = client.beginSegment(images[2]!.image, source, 12, 'sdk', true);
    await assert.rejects(busy.result, /already owns/); await assert.rejects(busy.workerReleased, /already owns/);
    assert.equal(images[2]!.closed(), 1);
    worker.result(); await second.workerReleased;
    assert.equal(hashes.pending.length, 2); assert.equal(firstCompleted, false);
    const bothHashing = client.beginSegment(images[3]!.image, source, 12, 'sdk', true);
    await assert.rejects(bothHashing.result, /already owns/);
    assert.equal(worker.messages.length, 3, 'Two validating masks still consume both ownership slots.');
    hashes.pending[1]!.resolve(); const secondResult = await second.result;
    assert.equal(secondResult.sequence, 11); assert.equal(secondResult.sourceSHA256, 'b'.repeat(64));
    assert.equal(firstCompleted, false, 'Valid hash completions can arrive in either order without mixing identities.');
    hashes.pending[0]!.resolve(); const firstResult = await first.result;
    assert.equal(firstResult.sequence, 10); assert.equal(firstResult.sourceSHA256, source);
    assert.equal(firstResult.categorySHA256, secondResult.categorySHA256); assert.deepEqual(firstResult.category, secondResult.category);
    assert.ok(images.every(image => image.closed() === 1));
    const t = first.timing();
    assert.equal(t.outcome, 'completed'); assert.equal(t.pendingAtSubmission, 1);
    assert.equal(t.workerValidationMs, .2); assert.equal(t.workerInferenceMs, 5);
    assert.equal(t.workerExtractionMs, .1); assert.equal(t.workerElapsedMs, 6);
    assert.ok(t.submittedAtMs! <= t.receivedAtMs! && t.receivedAtMs! <= t.workerReleasedAtMs!);
    assert.ok(t.workerReleasedAtMs! <= t.validatedAtMs! && t.validatedAtMs! <= t.hashStartedAtMs!);
    assert.ok(t.hashStartedAtMs! <= t.completedAtMs!); assert.ok(t.validationMs! >= 0 && t.hashMs! >= 0);
    assert.equal(traces.filter(trace => trace.outcome !== 'pending').length, 1);
    assert.ok(traces.every(Object.isFrozen)); assert.equal(Object.isFrozen(t), true);
    assert.ok(Object.values(t).every(value => value === null || ['string', 'number', 'boolean'].includes(typeof value)));
    assert.equal(JSON.stringify(traces).includes(source), false); assert.equal('sessionNonce' in t, false);
    assert.equal('category' in t, false); assert.equal('image' in t, false);
  } finally {hashes.releaseAll(); client.close(); await first.result.catch(() => {});}
});

test('default G release waits for full validation and hash completion and cannot be bypassed by an early request', async () => {
  const hashes = controlledHashes(), {worker, client} = await ready('hair-only', 'category-only', hashes);
  const firstImage = bitmap(), first = client.beginSegment(firstImage.image, source, 1);
  let released = false; void first.workerReleased.then(() => {released = true;});
  try {
    worker.result(); await Promise.resolve(); assert.equal(hashes.pending.length, 1); assert.equal(released, false);
    const secondImage = bitmap(), second = client.beginSegment(secondImage.image, source, 2, 'sdk', true);
    await assert.rejects(second.result, /already owns/); assert.equal(secondImage.closed(), 1);
    assert.equal(worker.messages.length, 2);
    hashes.pending[0]!.resolve(); await first.workerReleased; const result = await first.result;
    assert.equal(result.sequence, 1); assert.equal(firstImage.closed(), 1);
    assert.ok(first.timing().workerReleasedAtMs! >= first.timing().hashStartedAtMs!);
    assert.ok(first.timing().workerReleasedAtMs! <= first.timing().completedAtMs!);
    const thirdImage = bitmap(), third = client.beginSegment(thirdImage.image, source, 2);
    worker.result(); hashes.pending[1]!.resolve(); await third.result;
    assert.equal(thirdImage.closed(), 1);
  } finally {hashes.releaseAll(); client.close(); await first.result.catch(() => {});}
});

test('duplicate and stale first replies cannot free the second image computation slot', async () => {
  const hashes = controlledHashes(), {worker, client} = await ready('hair-only', 'category-only', hashes);
  const first = client.beginSegment(bitmap().image, source, 1, 'sdk', true);
  try {
    const firstRequest = worker.messages.at(-1)!; worker.result(); await first.workerReleased;
    const second = client.beginSegment(bitmap().image, source, 2, 'sdk', true);
    worker.result({}, {requestId: firstRequest.requestId});
    worker.result({}, {sessionNonce: 'different-session'});
    const rejectedImage = bitmap(); await assert.rejects(client.beginSegment(rejectedImage.image, source, 3, 'sdk', true).result, /already owns/);
    assert.equal(hashes.pending.length, 1); assert.equal(worker.messages.length, 3);
    hashes.pending[0]!.resolve(); await first.result;
    worker.result({}, {requestId: firstRequest.requestId});
    const stillBusy = bitmap(); await assert.rejects(client.beginSegment(stillBusy.image, source, 3, 'sdk', true).result, /already owns/);
    worker.result(); await second.workerReleased; hashes.pending[1]!.resolve(); await second.result;
    assert.equal(rejectedImage.closed(), 1); assert.equal(stillBusy.closed(), 1);
  } finally {hashes.releaseAll(); client.close(); await first.result.catch(() => {});}
});

test('a malformed second result fails both the previous validating mask and current request', async () => {
  for (const patch of [{sourceSHA256: 'b'.repeat(64)}, {category: new Uint8Array([0, 9, 1, 0])},
    {workerTiming: {inputValidationMs: 0, totalMs: 1}}, {workerTiming: {inputValidationMs: -1, totalMs: 9}}]) {
    const hashes = controlledHashes(), {worker, client} = await ready('hair-only', 'category-only', hashes);
    const firstImage = bitmap(), secondImage = bitmap();
    const first = client.beginSegment(firstImage.image, source, 1, 'sdk', true);
    worker.result(); await first.workerReleased;
    const second = client.beginSegment(secondImage.image, source, 2, 'sdk', true);
    worker.result(patch); await Promise.all([assert.rejects(first.result), assert.rejects(second.result)]);
    const terminalFirst = first.timing(), terminalSecond = second.timing();
    assert.equal(terminalFirst.outcome, 'failed'); assert.equal(terminalSecond.outcome, 'failed');
    assert.equal(worker.terminated, 1); assert.equal(hashes.pending.length, 1);
    hashes.releaseAll(); await Promise.resolve();
    assert.deepEqual(first.timing(), terminalFirst); assert.deepEqual(second.timing(), terminalSecond);
    assert.equal(firstImage.closed(), 1); assert.equal(secondImage.closed(), 1); client.close();
  }
});

test('close, session abort, unreadable replies and worker errors reject both early-release owners', async () => {
  for (const action of ['close', 'abort', 'unreadable', 'worker-error'] as const) {
    const hashes = controlledHashes(), {worker, client, controller} = await ready('hair-only', 'category-only', hashes);
    const traces: Readonly<HairRequestTiming>[] = [], firstImage = bitmap(), secondImage = bitmap();
    const first = client.beginSegment(firstImage.image, source, 1, 'sdk', true, trace => {traces.push(trace);});
    worker.result(); await first.workerReleased;
    const second = client.beginSegment(secondImage.image, source, 2, 'sdk', true, trace => {traces.push(trace);});
    const late = worker.onmessage;
    if (action === 'close') client.close();
    else if (action === 'abort') controller.abort();
    else if (action === 'unreadable') worker.emit(null);
    else worker.emit({type: 'error', sessionNonce: worker.messages.at(-1)!.sessionNonce,
      requestId: worker.messages.at(-1)!.requestId, message: 'Synthetic worker error'});
    await Promise.all([assert.rejects(first.result), assert.rejects(second.result), assert.rejects(second.workerReleased)]);
    const terminal = [first.timing(), second.timing()];
    assert.equal(terminal[0]!.outcome, action === 'close' || action === 'abort' ? 'cancelled' : 'failed');
    assert.equal(terminal[1]!.workerReleasedAtMs, null);
    assert.equal(traces.filter(trace => trace.outcome !== 'pending').length, 2);
    assert.equal(firstImage.closed(), 1); assert.equal(secondImage.closed(), 1); assert.equal(worker.terminated, 1);
    hashes.releaseAll(); late?.({data: {type: 'result'}} as MessageEvent<unknown>); await Promise.resolve();
    assert.deepEqual([first.timing(), second.timing()], terminal); client.close();
  }
});

test('each original deadline remains active during hashing and times out both owned requests', async () => {
  const hashes = controlledHashes(), {worker, client} = await ready('hair-only', 'category-only', {...hashes, segmentTimeoutMs: 15});
  const firstImage = bitmap(), secondImage = bitmap();
  const first = client.beginSegment(firstImage.image, source, 1, 'sdk', true);
  worker.result(); await first.workerReleased;
  const second = client.beginSegment(secondImage.image, source, 2, 'sdk', true);
  worker.result(); await second.workerReleased;
  await Promise.all([assert.rejects(first.result, /timed out/), assert.rejects(second.result, /timed out/)]);
  assert.equal(first.timing().outcome, 'timed-out'); assert.equal(second.timing().outcome, 'timed-out');
  assert.equal(first.timing().reason, 'deadline'); assert.equal(second.timing().reason, 'deadline');
  assert.equal(firstImage.closed(), 1); assert.equal(secondImage.closed(), 1); assert.equal(worker.terminated, 1);
  const terminal = first.timing(); hashes.releaseAll(); await Promise.resolve(); assert.deepEqual(first.timing(), terminal); client.close();
});

test('hash failure revokes both owners and preflight rejection reports a safe complete trace', async () => {
  const hashes = controlledHashes(), {worker, client} = await ready('hair-only', 'category-only', hashes);
  const rejectedImage = bitmap(), rejectedTraces: Readonly<HairRequestTiming>[] = [];
  const rejected = client.beginSegment(rejectedImage.image, 'invalid-source', 1, 'sdk', true, trace => {rejectedTraces.push(trace);});
  await assert.rejects(rejected.result); assert.equal(rejectedImage.closed(), 1);
  assert.equal(rejectedTraces.length, 1); assert.equal(rejected.timing().outcome, 'rejected');
  assert.equal(rejected.timing().requestId, null); assert.equal(rejected.timing().submittedAtMs, null);
  assert.equal(rejected.timing().reason, 'invalid-input'); assert.equal(typeof rejected.timing().completedAtMs, 'number');
  const firstImage = bitmap(), secondImage = bitmap();
  const first = client.beginSegment(firstImage.image, source, 1, 'sdk', true, () => {throw new Error('A diagnostic observer cannot break a valid request.');});
  worker.result(); await first.workerReleased;
  const second = client.beginSegment(secondImage.image, source, 2, 'sdk', true);
  hashes.pending[0]!.reject(new Error('Synthetic hash failure'));
  await Promise.all([assert.rejects(first.result, /hash failure/), assert.rejects(second.result, /hash failure/)]);
  assert.equal(first.timing().outcome, 'failed'); assert.ok(first.timing().hashMs! >= 0);
  assert.equal(firstImage.closed(), 1); assert.equal(secondImage.closed(), 1); assert.equal(worker.terminated, 1); client.close();
});
