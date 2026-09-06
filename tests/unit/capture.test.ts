import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { CAPTURE_LIMITS, CaptureStore } from '../../src/capture/store.ts';
import type { CaptureCanvas, CaptureEncoder } from '../../src/capture/store.ts';
import type { Detection } from '../../src/runtime/protocol.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function detection(): Detection {
  return {
    landmarks: Array.from({ length: 478 }, () => ({ x: 0.3, y: 0.4, z: -0.02 })),
    matrix: Array.from({ length: 16 }, (_, i) => i + 1),
    inferenceMs: 12,
  };
}

function jpeg(text = 'frame'): Blob { return new Blob([text], { type: 'image/jpeg' }); }
function canvas(): CaptureCanvas {
  return { width: 960, height: 720, toBlob: callback => callback(jpeg()) };
}

interface Metadata { pose: { matrix: number[] }; label: string }
function metadata(): Metadata { return { pose: { matrix: [1, 2, 3] }, label: 'matching frame' }; }

function environment(t: TestContext, encode?: CaptureEncoder) {
  let time = 1_000;
  const store = new CaptureStore<Metadata>({ encode, now: () => time });
  t.after(() => store.reset());
  return {
    store,
    setTime(value: number) { time = value; },
    capture(at = time) { return store.capture(canvas(), detection(), { capturedAt: at, metadata: metadata() }); },
  };
}

test('capture snapshots exact source, dimensions, detection and metadata before async JPEG completes', async t => {
  const pending = deferred<Blob>();
  const env = environment(t);
  const source = canvas();
  let pixels = 'original matching pixels';
  let capturedPixels = '';
  let encodedType: string | undefined;
  let quality: number | undefined;
  source.toBlob = (callback, type, value) => {
    capturedPixels = pixels;
    encodedType = type;
    quality = value;
    void pending.promise.then(callback);
  };
  const raw = detection();
  const info = metadata();
  env.store.start();
  const capturing = env.store.capture(source, raw, { capturedAt: 1_025, metadata: info, yawDegrees: -45 });
  pixels = 'new unrelated camera pixels';
  source.width = 640;
  raw.landmarks[0]!.x = 99;
  raw.matrix![0] = 99;
  info.pose.matrix[0] = 99;
  info.label = 'later frame';
  pending.resolve(jpeg(capturedPixels));
  assert.equal(await capturing, true);
  await env.store.finish();
  const frame = env.store.snapshot.frames[0]!;
  assert.equal(await frame.jpeg.text(), 'original matching pixels');
  assert.equal(encodedType, 'image/jpeg');
  assert.equal(quality, 0.94);
  assert.deepEqual([frame.width, frame.height, frame.capturedAt, frame.relativeMs, frame.yawDegrees], [960, 720, 1_025, 25, -45]);
  assert.equal(frame.detection.landmarks[0]!.x, 0.3);
  assert.equal(frame.detection.matrix![0], 1);
  assert.deepEqual(frame.metadata, metadata());
  assert.equal(Reflect.set(frame.metadata.pose.matrix, '0', 7), false);
  assert.equal(Object.isFrozen(frame.detection.landmarks[0]), true);
  assert.equal(Object.isFrozen(env.store.snapshot.frames), true);
});

test('finish closes admission, drains the accepted frame, and stays finished', async t => {
  const pending = deferred<Blob>();
  const env = environment(t, () => pending.promise);
  env.store.start();
  const capturing = env.capture();
  const finishing = env.store.finish();
  assert.equal(env.store.snapshot.state, 'finishing');
  assert.equal(await env.capture(1_300), false);
  pending.resolve(jpeg());
  await finishing;
  assert.equal(await capturing, true);
  assert.equal(env.store.snapshot.state, 'finished');
  assert.equal(env.store.snapshot.stopReason, 'manual');
  assert.equal(env.store.snapshot.frames.length, 1);
  await env.store.finish();
});

test('reset and restart discard late encoding without releasing its physical single-job lock early', async t => {
  const old = deferred<Blob>();
  let calls = 0;
  const env = environment(t, () => { calls++; return calls === 1 ? old.promise : Promise.resolve(jpeg('new')); });
  env.store.start();
  const previous = env.capture();
  const oldFinish = env.store.finish();
  env.store.reset();
  const discarded = env.store.snapshot;
  assert.deepEqual(discarded, { state: 'idle', frames: [], compressedBytes: 0, startedAt: null, stopReason: null });
  env.setTime(2_000);
  env.store.start();
  assert.equal(await env.capture(), false);
  assert.equal(calls, 1);
  old.resolve(jpeg('discarded'));
  assert.equal(await previous, false);
  await oldFinish;
  assert.equal(env.store.snapshot.state, 'recording');
  assert.equal(env.store.snapshot.frames.length, 0);
  assert.equal(await env.capture(), true);
  assert.equal(await env.store.snapshot.frames[0]!.jpeg.text(), 'new');
});

test('capture enforces bounded frame, byte, and wall-time admission', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const env = environment(t);
  env.store.start();
  for (let index = 0; index < CAPTURE_LIMITS.maxFrames; index++)
    assert.equal(await env.capture(1_000 + index * CAPTURE_LIMITS.sampleIntervalMs), true);
  assert.equal(env.store.snapshot.frames.length, 96);
  assert.equal(env.store.snapshot.stopReason, 'frame-limit');
  assert.equal(await env.capture(29_800), false);
  env.store.start();
  t.mock.timers.tick(CAPTURE_LIMITS.maxDurationMs);
  assert.equal(env.store.snapshot.state, 'finished');
  assert.equal(env.store.snapshot.stopReason, 'duration');
  const bytes = environment(t, async () => new Blob([new Uint8Array(CAPTURE_LIMITS.maxCompressedBytes + 1)], { type: 'image/jpeg' }));
  bytes.store.start();
  assert.equal(await bytes.capture(), false);
  assert.equal(bytes.store.snapshot.stopReason, 'byte-limit');
  assert.equal(bytes.store.snapshot.frames.length, 0);
});

test('explicit export carries exact bytes, immutable header, provenance, times and ordinary arrays', async t => {
  const env = environment(t, async () => jpeg('original JPEG bytes'));
  env.store.start();
  await assert.rejects(env.store.exportJson({}), /Finish/);
  await env.capture();
  await env.store.finish();
  const header = { session: 'local test', camera: { width: 960 }, provenance: { model: 'pinned' } };
  const exporting = env.store.exportJson(header);
  header.camera.width = 1;
  const document = JSON.parse(await exporting);
  assert.equal(document.schemaVersion, 1);
  assert.equal(document.projectId, 'ar_v4');
  assert.deepEqual(document.limits, CAPTURE_LIMITS);
  assert.equal(document.header.camera.width, 960);
  assert.equal(document.header.provenance.model, 'pinned');
  assert.equal(document.startedAt, 1_000);
  assert.equal(document.stopReason, 'manual');
  assert.equal(document.frames[0].jpegDataUrl, `data:image/jpeg;base64,${btoa('original JPEG bytes')}`);
  assert.equal('jpeg' in document.frames[0], false);
  assert.deepEqual(document.frames[0].detection, detection());
  assert.deepEqual(document.frames[0].metadata, metadata());
  assert.equal(document.frames[0].relativeMs, 0);
  const stale = env.store.exportJson({ session: 'discarded' });
  env.store.reset();
  await assert.rejects(stale, { name: 'AbortError' });
  assert.equal(env.store.snapshot.frames.length, 0);
});
