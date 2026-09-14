import test from 'node:test';
import assert from 'node:assert/strict';
import {HairClient} from './hair-client.ts';
import type {HairWorkerPort} from './hair-client.ts';
import {HAIR_MODELS} from './models.ts';
import type {HairModelId} from './models.ts';
import type {HairOutputMode, HairWorkerRequest} from './hair-protocol.ts';

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
      outputMode: message.outputMode ?? 'full', initializationMs: 2, ...patch});
  }
  result(patch: Record<string, unknown> = {}, envelope: Record<string, unknown> = {}): void {
    const message = this.messages.at(-1)!; assert.equal(message.type, 'segment');
    const initialize = this.messages.find(value => value.type === 'initialize')!; assert.equal(initialize.type, 'initialize');
    const model = HAIR_MODELS[initialize.modelId];
    this.emit({type: 'result', sessionNonce: message.sessionNonce, requestId: message.requestId, output: {
      sourceSHA256: message.sourceSHA256, sequence: message.sequence, model: model.id, modelSHA256: model.sha256,
      labels: [...model.labels], hairIndex: 1, width: 2, height: 2, category: new Uint8Array([0, 1, 1, 0]),
      ...(initialize.outputMode === 'category-only' ? {outputMode: 'category-only'} : {outputMode: 'full', confidence: new Float32Array([0, .5, 1, .25])}),
      inferenceMs: 5, extractionMs: .1, ...patch}, ...envelope});
  }
}
function bitmap(width = 2, height = 2): {image: ImageBitmap; closed: () => number} {
  let closed = 0;
  return {image: {width, height, close: () => { closed++; }} as ImageBitmap, closed: () => closed};
}
async function ready(model: HairModelId = 'hair-only', outputMode: HairOutputMode = 'full') {
  const worker = new FakeWorker(), controller = new AbortController();
  const client = new HairClient(model, {createWorker: () => worker, outputMode});
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
