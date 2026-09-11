import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import {DetectorClient} from './face-detector.ts';
import type {FaceWorkerPort} from './face-detector.ts';
import type {Detection} from '../../references/perfect-temples/src/runtime/protocol.ts';
import type {TimedDetectorRequest, TimedDetectorResponse, WorkerFaceTiming} from './face-timing.ts';

const hash = (value: unknown): string => createHash('sha256').update(JSON.stringify(value)).digest('hex');
class Bitmap {
  readonly width = 640; readonly height = 427; closes = 0;
  close(): void {this.closes++;}
}
class WorkerStub implements FaceWorkerPort {
  onmessage: FaceWorkerPort['onmessage'] = null;
  onerror: FaceWorkerPort['onerror'] = null;
  onmessageerror: FaceWorkerPort['onmessageerror'] = null;
  terminated = false;
  requests: TimedDetectorRequest[] = [];
  transfers: Transferable[][] = [];
  postHook: (() => void) | null = null;
  postMessage(message: TimedDetectorRequest, transfer: Transferable[] = []): void {
    this.requests.push(message); this.transfers.push(transfer); this.postHook?.();
  }
  terminate(): void {this.terminated = true;}
  emit(message: TimedDetectorResponse): void {this.onmessage?.(new MessageEvent('message', {data: message}));}
  get latest(): TimedDetectorRequest {return this.requests.at(-1)!;}
  ready(): void {this.emit({type: 'ready', id: this.latest.id, sessionNonce: this.latest.sessionNonce});}
}
function detection(face = true): Detection {
  return {landmarks: face ? Array.from({length: 478}, (_, index) => ({x: index / 478, y: .3, z: -.01})) : [],
    matrix: face ? Array.from({length: 16}, (_, index) => Number(index % 5 === 0)) : null, inferenceMs: 5};
}
function result(worker: WorkerStub, value: Detection, timing: unknown = undefined): TimedDetectorResponse {
  return {type: 'result', id: worker.latest.id, sessionNonce: worker.latest.sessionNonce, detection: value,
    ...(timing === undefined ? {} : {timing: timing as WorkerFaceTiming})};
}
function workerTiming(worker: WorkerStub, overrides: Partial<WorkerFaceTiming> = {}): WorkerFaceTiming {
  const request = worker.latest; assert.equal(request.type, 'detect');
  return {schema: 'face-worker-timing-v1', requestId: request.id, timestampMs: request.timestampMs,
    delegate: 'GPU', width: 640, height: 427, requestChecksMs: 2, inferenceMs: 5,
    extractionMs: 4, validationMs: 9, elapsedMs: 20, ...overrides};
}
function fixture(t: {after(fn: () => void): void}, detectTimeoutMs = 15_000) {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', {configurable: true, value: {location: {href: 'http://127.0.0.1:8069/'}}});
  const workers: WorkerStub[] = [], clock = {value: 0, reads: [] as number[]};
  const client = new DetectorClient({sessionNonce: 'face-session-tests', detectTimeoutMs,
    now: () => clock.reads.shift() ?? clock.value,
    createWorker: () => {const worker = new WorkerStub(); worker.postHook = () => {clock.value += 2;}; workers.push(worker); return worker;}});
  t.after(() => {client.close(); if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow); else Reflect.deleteProperty(globalThis, 'window');});
  return {client, workers, clock};
}
async function initialize(f: ReturnType<typeof fixture>): Promise<WorkerStub> {
  const initialized = f.client.initialize(new AbortController().signal); const worker = f.workers[0]!;
  assert.equal(worker.latest.type, 'initialize');
  if (worker.latest.type === 'initialize') {
    assert.equal(worker.latest.delegate, 'GPU'); assert.equal(worker.latest.modelUrl, 'http://127.0.0.1:8069/models/face_landmarker.task');
    assert.equal(worker.latest.wasmRoot, 'http://127.0.0.1:8069/mediapipe/');
  }
  worker.ready(); await initialized; return worker;
}

test('face timings split same-clock worker/client stages without changing Detection bytes or image ownership', async t => {
  const f = fixture(t), worker = await initialize(f), image = new Bitmap(), input = detection();
  f.clock.value = 100;
  const pending = f.client.detect(image as unknown as ImageBitmap, 17);
  assert.equal(f.client.lastTiming, null); assert.deepEqual(worker.transfers.at(-1), [image]);
  f.clock.reads = [140, 141, 144, 146];
  worker.emit(result(worker, input, workerTiming(worker)));
  const output = await pending, timing = f.client.lastTiming!;
  assert.equal(hash(output), hash(input)); assert.deepEqual(Object.keys(output), ['landmarks', 'matrix', 'inferenceMs']);
  assert.notEqual(output.landmarks, input.landmarks); assert.notEqual(output.matrix, input.matrix);
  assert.equal(image.closes, 1); assert.equal(f.client.delegate, 'GPU');
  assert.equal(timing.inferenceMs, 5); assert.equal(timing.workerExtractionMs, 4); assert.equal(timing.workerValidationMs, 9);
  assert.equal(timing.workerRequestChecksMs, 2); assert.equal(timing.workerElapsedMs, 20);
  assert.equal(timing.postMessageMs, 2); assert.equal(timing.roundTripMs, 40); assert.equal(timing.clientValidationMs, 3);
  assert.equal(timing.clientTotalMs, 44); assert.equal(timing.detectCallMs, 46); assert.equal(timing.transportAndSchedulingMs, 20);
  assert.equal(timing.timestampMs, 17); assert.equal(timing.workerTimingStatus, 'valid');
  timing.inferenceMs = 1000; assert.equal(f.client.lastTiming!.inferenceMs, 5, 'timing reads cannot mutate retained state');
});

test('missing or malformed timing remains unavailable and never poisons a valid face or no-face Detection', async t => {
  const f = fixture(t), worker = await initialize(f);
  const variants: ((valid: WorkerFaceTiming) => unknown)[] = [() => undefined, () => null,
    value => ({...value, inferenceMs: 99}), value => ({...value, validationMs: NaN}),
    value => ({...value, requestId: 999}), value => ({...value, timestampMs: 999}),
    value => ({...value, elapsedMs: 100}), value => ({...value, width: 1}), value => ({...value, delegate: 'CPU'}),
    () => new Proxy({}, {get() {throw new Error('malformed optional diagnostics');}})];
  for (let index = 0; index < variants.length; index++) {
    const image = new Bitmap(), input = detection(index % 2 === 0); f.clock.value = 100 * (index + 1);
    const pending = f.client.detect(image as unknown as ImageBitmap, index + 1);
    f.clock.value += 50; worker.emit(result(worker, input, variants[index]!(workerTiming(worker))));
    const output = await pending, timing = f.client.lastTiming!;
    assert.equal(hash(output), hash(input)); assert.equal(image.closes, 1);
    assert.equal(timing.workerTimingStatus, index === 0 ? 'missing' : 'invalid');
    assert.equal(timing.workerElapsedMs, null); assert.equal(timing.workerExtractionMs, null);
    assert.equal(timing.workerValidationMs, null); assert.equal(timing.transportAndSchedulingMs, null);
    assert.equal(timing.inferenceMs, input.inferenceMs); assert.ok(timing.clientTotalMs >= timing.roundTripMs);
  }
});

test('negative residual from precision/inconsistent clocks is unavailable rather than fabricated as zero', async t => {
  const f = fixture(t), worker = await initialize(f), image = new Bitmap();
  const pending = f.client.detect(image as unknown as ImageBitmap, 1);
  worker.emit(result(worker, detection(), workerTiming(worker)));
  await pending;
  assert.equal(f.client.lastTiming!.workerTimingStatus, 'valid'); assert.equal(f.client.lastTiming!.transportAndSchedulingMs, null);
});

test('one pending image and exact session/request identity prevent late callbacks from publishing detections or timings', async t => {
  const f = fixture(t), worker = await initialize(f), image = new Bitmap();
  const pending = f.client.detect(image as unknown as ImageBitmap, 10), extra = new Bitmap();
  await assert.rejects(f.client.detect(extra as unknown as ImageBitmap, 11), /already in progress/); assert.equal(extra.closes, 1);
  worker.emit({...result(worker, detection(), workerTiming(worker)), id: 999});
  worker.emit({...result(worker, detection(), workerTiming(worker)), sessionNonce: 'another-session'});
  assert.equal(f.client.lastTiming, null);
  const callback = worker.onmessage!; f.clock.value += 50;
  worker.emit(result(worker, detection(), workerTiming(worker))); await pending;
  const late = result(worker, detection(), workerTiming(worker)); f.client.close();
  callback(new MessageEvent('message', {data: late}));
  assert.equal(f.client.lastTiming, null); assert.equal(f.client.delegate, null); assert.equal(worker.terminated, true);
  const stoppedImage = new Bitmap(); await assert.rejects(f.client.detect(stoppedImage as unknown as ImageBitmap, 12), /not ready/);
  assert.equal(stoppedImage.closes, 1);
});

test('only explicit GPU initialization errors retry in a fresh CPU worker; close releases pending detection', async t => {
  const f = fixture(t), abort = new AbortController();
  const initializing = f.client.initialize(abort.signal), gpu = f.workers[0]!;
  const stale = gpu.onmessage!;
  gpu.emit({type: 'error', id: gpu.latest.id, sessionNonce: gpu.latest.sessionNonce, message: 'GPU initialization unavailable'});
  await Promise.resolve(); const cpu = f.workers[1]!;
  assert.ok(cpu); assert.equal(gpu.terminated, true);
  assert.equal(cpu.latest.type, 'initialize'); if (cpu.latest.type === 'initialize') assert.equal(cpu.latest.delegate, 'CPU');
  stale(new MessageEvent('message', {data: {type: 'ready', id: cpu.latest.id, sessionNonce: cpu.latest.sessionNonce}}));
  assert.equal(f.client.delegate, null);
  cpu.ready(); await initializing; assert.equal(f.client.delegate, 'CPU');
  const image = new Bitmap(), detecting = f.client.detect(image as unknown as ImageBitmap, 1);
  f.client.close(); await assert.rejects(detecting, /closed/); assert.equal(image.closes, 1);
});

test('aborted startup closes its worker without attempting CPU fallback', async t => {
  const abortedFixture = fixture(t), abort = new AbortController();
  const starting = abortedFixture.client.initialize(abort.signal); abort.abort();
  await assert.rejects(starting, {name: 'AbortError'}); assert.equal(abortedFixture.workers[0]!.terminated, true);
  assert.equal(abortedFixture.workers.length, 1);
});

test('malformed Detection and missing reply preserve cleanup and never expose stale timing', async t => {
  const f = fixture(t, 10), worker = await initialize(f), malformed = new Bitmap();
  const invalid = f.client.detect(malformed as unknown as ImageBitmap, 1);
  worker.emit(result(worker, {...detection(), matrix: [1, 2]}));
  await assert.rejects(invalid, /invalid face transformation/); assert.equal(f.client.lastTiming, null); assert.equal(malformed.closes, 1);
  const missing = new Bitmap(), timeout = f.client.detect(missing as unknown as ImageBitmap, 2);
  await assert.rejects(timeout, /timed out/); assert.equal(missing.closes, 1); assert.equal(worker.terminated, true);
});

test('failed image transfer closes the image and worker without exposing timing', async t => {
  const transferred = fixture(t), transferWorker = await initialize(transferred), image = new Bitmap();
  transferWorker.postHook = () => {throw new Error('injected transfer failure');};
  await assert.rejects(transferred.client.detect(image as unknown as ImageBitmap, 1), /transfer failure/);
  assert.equal(image.closes, 1); assert.equal(transferWorker.terminated, true); assert.equal(transferred.client.lastTiming, null);
});
