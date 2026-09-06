import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { openCamera } from '../../src/runtime/camera.ts';
import { DetectorClient } from '../../src/runtime/detector.ts';
import { validateDetection } from '../../src/runtime/protocol.ts';
import type { Detection, DetectorRequest, DetectorResponse } from '../../src/runtime/protocol.ts';

function replaceGlobal(t: TestContext, name: string, value: unknown): void {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else Reflect.deleteProperty(globalThis, name);
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function microtasks(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

function streamResource() {
  let stops = 0;
  return {
    stream: { getTracks: () => [{ stop: () => { stops++; } }] } as unknown as MediaStream,
    get stops() { return stops; },
  };
}

class FakeVideo extends EventTarget {
  autoplay = false;
  muted = false;
  playsInline = false;
  readyState = 0;
  videoWidth = 0;
  videoHeight = 0;
  pauseCount = 0;
  autoMetadata = true;
  playResult = Promise.resolve();
  private source: unknown = null;
  get srcObject() { return this.source; }
  set srcObject(value: unknown) {
    this.source = value;
    if (value && this.autoMetadata) {
      this.readyState = 1;
      this.videoWidth = 1280;
      this.videoHeight = 720;
    }
  }
  play() { return this.playResult; }
  pause() { this.pauseCount++; }
  removeAttribute() {}
}

function cameraEnvironment(t: TestContext, request: () => Promise<MediaStream>, video = new FakeVideo()) {
  let constraints: unknown;
  let calls = 0;
  replaceGlobal(t, 'navigator', { mediaDevices: { getUserMedia: (value: unknown) => {
    constraints = value;
    calls++;
    return request();
  } } });
  replaceGlobal(t, 'document', { createElement: () => video });
  return { video, get constraints() { return constraints; }, get calls() { return calls; } };
}

class FakeWorker {
  static instances: FakeWorker[] = [];
  messages: DetectorRequest[] = [];
  transfers: unknown[][] = [];
  terminated = false;
  postError: Error | null = null;
  onmessage: ((event: { data: DetectorResponse }) => void) | null = null;
  onerror: ((event: { message: string; preventDefault(): void }) => void) | null = null;
  onmessageerror: (() => void) | null = null;
  url: URL;
  options: unknown;
  previousWorkersTerminated: boolean;
  constructor(url: URL, options: unknown) {
    this.url = url;
    this.options = options;
    this.previousWorkersTerminated = FakeWorker.instances.every(worker => worker.terminated);
    FakeWorker.instances.push(this);
  }
  postMessage(message: DetectorRequest, transfer: unknown[]) {
    if (this.postError) throw this.postError;
    this.messages.push(message);
    this.transfers.push(transfer);
  }
  terminate() { this.terminated = true; }
  emit(data: DetectorResponse) { this.onmessage?.({ data }); }
}

function workerEnvironment(t: TestContext) {
  FakeWorker.instances = [];
  replaceGlobal(t, 'Worker', FakeWorker);
  replaceGlobal(t, 'window', { location: { href: 'http://127.0.0.1:8040/try-on?test=1' } });
}

function imageResource() {
  let closes = 0;
  return {
    image: { width: 1280, height: 720, close: () => { closes++; } } as unknown as ImageBitmap,
    get closes() { return closes; },
  };
}

function detection(): Detection {
  return {
    landmarks: Array.from({ length: 478 }, () => ({ x: 0.3, y: 0.4, z: -0.02 })),
    matrix: Array.from({ length: 16 }, (_, i) => i + 1),
    inferenceMs: 12,
  };
}

async function readyClient(t: TestContext) {
  workerEnvironment(t);
  const client = new DetectorClient();
  t.after(() => client.close());
  const starting = client.initialize(new AbortController().signal);
  const worker = FakeWorker.instances[0]!;
  worker.emit({ type: 'ready', id: worker.messages[0]!.id });
  await starting;
  return { client, worker };
}

test('camera cancels pending permission and stops a stream that arrives later', async (t) => {
  const pending = deferred<MediaStream>();
  cameraEnvironment(t, () => pending.promise);
  const resource = streamResource();
  const abort = new AbortController();
  const opening = openCamera(abort.signal);
  abort.abort();
  await assert.rejects(opening, { name: 'AbortError' });
  pending.resolve(resource.stream);
  await microtasks();
  assert.equal(resource.stops, 1);
});

test('detector keeps one owned frame, validates replies, and preserves the paired raw matrix', async t => {
  const { client, worker } = await readyClient(t);
  const first = imageResource(), second = imageResource();
  const pending = client.detect(first.image, 100);
  await assert.rejects(client.detect(second.image, 101), /already in progress/);
  assert.equal(second.closes, 1);
  worker.emit({ type: 'result', id: -1, detection: detection() });
  assert.equal(first.closes, 0);
  const expected = detection();
  worker.emit({ type: 'result', id: worker.messages[1]!.id, detection: expected });
  assert.deepEqual(await pending, expected);
  assert.equal(first.closes, 1);
  assert.equal(worker.transfers[1]![0], first.image);
  const repeated = imageResource();
  await assert.rejects(client.detect(repeated.image, 100), /timestamps/);
  assert.equal(repeated.closes, 1);
  assert.throws(() => validateDetection({ ...expected, landmarks: new Array(478) }), /invalid landmark/);
  assert.throws(() => validateDetection({ ...expected, matrix: new Array(16) }), /invalid face transformation/);
  expected.landmarks[0]!.x = NaN;
  assert.throws(() => validateDetection(expected), /invalid landmark/);
});

test('detector close rejects pending detection and ignores an already queued reply', async (t) => {
  const { client, worker } = await readyClient(t);
  const frame = imageResource();
  const pending = client.detect(frame.image, 1);
  const lateCallback = worker.onmessage!;
  client.close();
  await assert.rejects(pending, /closed/);
  lateCallback({ data: { type: 'result', id: worker.messages[1]!.id, detection: detection() } });
  assert.equal(worker.terminated, true);
  assert.equal(frame.closes, 1);
  const after = imageResource();
  await assert.rejects(client.detect(after.image, 2), /not ready/);
  assert.equal(after.closes, 1);
});

test('explicit GPU startup failure retries CPU in a fresh worker and ignores the old worker', async (t) => {
  workerEnvironment(t);
  const client = new DetectorClient();
  t.after(() => client.close());
  const pending = client.initialize(new AbortController().signal);
  const gpu = FakeWorker.instances[0]!;
  const lateMessage = gpu.onmessage!;
  const lateError = gpu.onerror!;
  gpu.emit({ type: 'error', id: gpu.messages[0]!.id, message: 'GPU unavailable' });
  await microtasks();
  assert.equal(FakeWorker.instances.length, 2);
  const cpu = FakeWorker.instances[1]!;
  assert.equal(gpu.terminated, true);
  assert.equal(cpu.previousWorkersTerminated, true);
  const initialization = cpu.messages[0]!;
  assert.ok(initialization.type === 'initialize');
  assert.equal(initialization.delegate, 'CPU');
  assert.notEqual(cpu.messages[0]!.id, gpu.messages[0]!.id);
  lateMessage({ data: { type: 'ready', id: gpu.messages[0]!.id } });
  lateError({ message: 'Late GPU crash', preventDefault() {} });
  assert.equal(cpu.terminated, false);
  cpu.emit({ type: 'ready', id: cpu.messages[0]!.id });
  await pending;
  const frame = imageResource();
  const detecting = client.detect(frame.image, 1);
  cpu.emit({ type: 'result', id: cpu.messages[1]!.id, detection: detection() });
  assert.equal((await detecting).landmarks.length, 478);
  assert.equal(frame.closes, 1);
});

test('detector deadline terminates a hung worker and disposes the submitted frame', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { client, worker } = await readyClient(t);
  const frame = imageResource();
  const pending = client.detect(frame.image, 1);
  const rejected = assert.rejects(pending, /timed out/);
  t.mock.timers.tick(15_000);
  await rejected;
  assert.equal(worker.terminated, true);
  assert.equal(frame.closes, 1);
});
