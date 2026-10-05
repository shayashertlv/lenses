import assert from 'node:assert/strict';
import {test} from 'node:test';
import type {TestContext} from 'node:test';
import {openCamera} from '../src/camera/camera.ts';

function replaceGlobal(t: TestContext, name: string, value: unknown): void {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, {configurable: true, writable: true, value});
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else Reflect.deleteProperty(globalThis, name);
  });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
async function microtasks(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}
function streamResource() {
  let stops = 0;
  return {
    stream: {getTracks: () => [{stop: () => { stops++; }}]} as unknown as MediaStream,
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
  replaceGlobal(t, 'navigator', {mediaDevices: {getUserMedia: (value: unknown) => {
    constraints = value;
    calls++;
    return request();
  }}});
  replaceGlobal(t, 'document', {createElement: () => video});
  return {video, get constraints() { return constraints; }, get calls() { return calls; }};
}

test('the camera cancels a pending permission and stops a stream that arrives later', async (t) => {
  const pending = deferred<MediaStream>();
  cameraEnvironment(t, () => pending.promise);
  const resource = streamResource();
  const abort = new AbortController();
  const opening = openCamera(abort.signal);
  abort.abort();
  await assert.rejects(opening, {name: 'AbortError'});
  pending.resolve(resource.stream);
  await microtasks();
  assert.equal(resource.stops, 1);
});

test('the camera asks for the user-facing 1280x720 stream, plays it muted and inline, and stop releases every track', async (t) => {
  const resource = streamResource();
  const environment = cameraEnvironment(t, async () => resource.stream);
  const session = await openCamera(new AbortController().signal);
  assert.deepEqual(environment.constraints, {audio: false, video: {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}}});
  assert.equal(environment.calls, 1);
  assert.ok(environment.video.muted && environment.video.playsInline && environment.video.autoplay);
  assert.equal(session.video, environment.video as unknown as HTMLVideoElement);
  session.stop(); session.stop();
  assert.equal(resource.stops, 1); assert.equal(environment.video.pauseCount, 1); assert.equal(environment.video.srcObject, null);
});
