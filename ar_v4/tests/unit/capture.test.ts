import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { CAPTURE_LIMITS, CaptureStore } from '../../src/capture/store.ts';
import type { CaptureCanvas, CaptureEncoder } from '../../src/capture/store.ts';
import type { Detection } from '../../src/runtime/protocol.ts';
import { CaptureController } from '../../src/capture/controller.ts';
import type { CaptureGeometry, TryOnRenderer } from '../../src/render/renderer.ts';
import type { TempleClipConfiguration } from '../../src/render/temple-clip.ts';

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

type PortableGeometry = Omit<CaptureGeometry, 'surfacePositions'> & { surfacePositions: number[] };
type TempleVisibilityConfiguration = NonNullable<CaptureGeometry['templeVisibility']>;

function geometry(model: string): PortableGeometry {
  return {
    eyewearModelId: model, rawMatrix: detection().matrix!, correctedMatrix: detection().matrix!,
    eyewearMatrix: detection().matrix!, surfacePositions: [0.1, 0.2, -40], yawDegrees: 0,
    occlusion: { method: 'historical-test-surface', selectedShapeId: null,
      appliedShapeId: null, status: 'recorded', rejectionReasons: [] },
  };
}

test('clipping provenance freezes with its paired surface and exports without relabeling historical frames', async t => {
  const pending = deferred<Blob>();
  let encodes = 0;
  const store = new CaptureStore<PortableGeometry>({
    now: () => 1_000, encode: () => ++encodes === 1 ? pending.promise : Promise.resolve(jpeg()),
  });
  t.after(() => store.reset());
  const clip = { method: 'temple-end-clip-v1', negativeXCutoffLocalZM: -0.071,
    positiveXCutoffLocalZM: -0.084 } satisfies TempleClipConfiguration;
  const current = { ...geometry('tom-ford-clear'), templeClip: clip };
  const expected = structuredClone(current);
  store.start();
  const capturing = store.capture(canvas(), detection(), { capturedAt: 1_000, metadata: current });
  clip.negativeXCutoffLocalZM = -0.1;
  clip.positiveXCutoffLocalZM = -0.11;
  current.surfacePositions[0] = 9;
  pending.resolve(jpeg('paired clipping image'));
  assert.equal(await capturing, true);
  const old = geometry('tom-ford-clear');
  assert.equal(await store.capture(canvas(), detection(), { capturedAt: 1_300, metadata: old }), true);
  await store.finish();
  const saved = store.snapshot.frames[0]!.metadata;
  assert.deepEqual(saved, expected);
  assert.equal(Object.isFrozen(saved.templeClip), true);
  assert.equal(Reflect.set(saved.templeClip!, 'negativeXCutoffLocalZM', -0.2), false);
  assert.equal(Reflect.set(saved.templeClip!, 'positiveXCutoffLocalZM', -0.2), false);
  const header = { templeClip: { method: 'temple-end-blend-v3', negativeXCutoffLocalZM: -0.110,
    positiveXCutoffLocalZM: -0.110, fadeLengthLocalM: 0.015 },
    templeVisibility: { method: 'temple-side-depth-v3', coverage: 'alpha-to-coverage', parameters: {} },
    occlusion: { method: 'raw-nasal-shape-v1' } };
  const expectedHeader = structuredClone(header);
  const exporting = store.exportJson(header);
  header.templeClip.negativeXCutoffLocalZM = -0.3;
  header.templeClip.positiveXCutoffLocalZM = -0.3;
  const document = JSON.parse(await exporting);
  assert.deepEqual(document.header.templeClip, expectedHeader.templeClip);
  assert.notDeepEqual(document.frames[0].metadata.templeClip, document.header.templeClip,
    'the stored frame retains its asymmetric historical cutoffs independently of the fixed model header');
  assert.deepEqual(document.frames[0].metadata, expected);
  assert.deepEqual(document.frames[1].metadata, old);
  assert.equal('templeClip' in document.frames[1].metadata, false,
    'a historical frame does not gain current clipping provenance');
  assert.equal('templeVisibility' in document.frames[0].metadata, false,
    'an asymmetric hard-v1 record does not gain current visibility provenance');
  assert.equal('templeVisibility' in document.frames[1].metadata, false,
    'a legacy surface does not gain current visibility provenance');
  assert.deepEqual(document.frames[1].metadata.occlusion, old.occlusion,
    'clipping provenance does not relabel a historical face surface');
});

test('camera dissolve and frontal visibility metadata freeze before encoding for both models', async t => {
  for (const [model, cutoff, coverage] of [
    ['amber-horizon', -0.105, 'alpha-to-coverage'],
    ['tom-ford-clear', -0.110, 'ordered-dither'],
  ] as const) {
    const pending = deferred<Blob>();
    const store = new CaptureStore<PortableGeometry>({ now: () => 1_000, encode: () => pending.promise });
    t.after(() => store.reset());
    const clip = { method: 'temple-end-blend-v3', negativeXCutoffLocalZM: cutoff,
      positiveXCutoffLocalZM: cutoff, fadeLengthLocalM: 0.015 } satisfies TempleClipConfiguration;
    const visibility = { method: 'temple-side-depth-v3', negativeXWeight: 0.17,
      positiveXWeight: 0.83, frontalOcclusionWeight: .64, coverage } satisfies TempleVisibilityConfiguration;
    const current = { ...geometry(model), templeClip: clip, templeVisibility: visibility };
    const expected = structuredClone(current);
    store.start();
    const capturing = store.capture(canvas(), detection(), { capturedAt: 1_000, metadata: current });
    clip.fadeLengthLocalM = 0.002;
    visibility.negativeXWeight = 0.99;
    visibility.positiveXWeight = 0.01;
    visibility.frontalOcclusionWeight = .02;
    current.surfacePositions[0] = 99;
    pending.resolve(jpeg('exact paired appearance'));
    assert.equal(await capturing, true);
    await store.finish();
    const saved = store.snapshot.frames[0]!.metadata;
    assert.deepEqual(saved, expected);
    assert.equal(Object.isFrozen(saved.templeClip), true);
    assert.equal(Object.isFrozen(saved.templeVisibility), true);
    assert.equal(Reflect.set(saved.templeVisibility!, 'negativeXWeight', 0), false);
    assert.equal(Reflect.set(saved.templeVisibility!, 'frontalOcclusionWeight', 0), false);
    assert.equal(Reflect.set(saved.templeClip!, 'fadeLengthLocalM', 0), false);
    const header = { templeClip: structuredClone(expected.templeClip),
      templeVisibility: { method: 'temple-side-depth-v3', coverage,
        parameters: { lateralCm: [3.5, 4.5] } } };
    const expectedHeader = structuredClone(header);
    const exporting = store.exportJson(header);
    header.templeVisibility.parameters.lateralCm[0] = 99;
    header.templeClip.fadeLengthLocalM = 0.01;
    const exported = JSON.parse(await exporting);
    assert.deepEqual(exported.header, expectedHeader);
    assert.deepEqual(exported.frames[0].metadata, expected);
    assert.equal(exported.frames[0].jpegDataUrl,
      `data:image/jpeg;base64,${btoa('exact paired appearance')}`);
  }
});

test('controller replays exact saved appearance and clears legacy and no-face state for both models', async t => {
  const elements = new Map<string, { textContent: string }>();
  const replacements: Record<string, unknown> = {
    document: { getElementById(id: string) {
      if (!elements.has(id)) elements.set(id, { textContent: '' });
      return elements.get(id);
    } },
    createImageBitmap: async () => ({ close() {} }),
  };
  for (const [key, value] of Object.entries(replacements)) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, value });
    t.after(() => {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    });
  }
  for (const model of ['amber-horizon', 'tom-ford-clear']) {
    const store = new CaptureStore<PortableGeometry | null>({ now: () => 1_000, encode: async () => jpeg() });
    t.after(() => store.reset());
    const clip: TempleClipConfiguration = { method: 'temple-end-clip-v1',
      negativeXCutoffLocalZM: -0.068, positiveXCutoffLocalZM: -0.087 };
    const fade: TempleClipConfiguration = { method: 'temple-end-fade-v2',
      negativeXCutoffLocalZM: -0.091, positiveXCutoffLocalZM: -0.099,
      fadeLengthLocalM: 0.003, coverage: 'ordered-dither' };
    const visibility: TempleVisibilityConfiguration = { method: 'temple-side-depth-v1',
      negativeXWeight: 0.25, positiveXWeight: 0.9, coverage: 'ordered-dither' };
    const fixedCutoff = model === 'amber-horizon' ? -0.105 : -0.110;
    const liveDefault: TempleClipConfiguration = { method: 'temple-end-blend-v3',
      negativeXCutoffLocalZM: fixedCutoff, positiveXCutoffLocalZM: fixedCutoff,
      fadeLengthLocalM: 0.015 };
    const dissolve: TempleClipConfiguration = { method: 'temple-end-blend-v3',
      negativeXCutoffLocalZM: -.101, positiveXCutoffLocalZM: -.108, fadeLengthLocalM: .012 };
    const viewVisibility: TempleVisibilityConfiguration = { method: 'temple-side-depth-v2',
      negativeXWeight: .18, positiveXWeight: .76, coverage: 'ordered-dither' };
    const frontalVisibility: TempleVisibilityConfiguration = { method: 'temple-side-depth-v3',
      negativeXWeight: .13, positiveXWeight: .28, frontalOcclusionWeight: .67, coverage: 'ordered-dither' };
    const frames: (PortableGeometry | null)[] = [
      { ...geometry(model), templeClip: clip },
      { ...geometry(model), templeClip: fade, templeVisibility: visibility }, geometry(model),
      { ...geometry(model), templeClip: null, templeVisibility: null }, null, null,
      { ...geometry(model), templeClip: dissolve, templeVisibility: frontalVisibility },
      { ...geometry(model), templeClip: dissolve, templeVisibility: viewVisibility },
      { ...geometry(model === 'amber-horizon' ? 'tom-ford-clear' : 'amber-horizon'),
        templeClip: fade, templeVisibility: visibility },
    ];
    store.start();
    for (const [index, metadata] of frames.entries()) {
      const paired = index === 4 ? { landmarks: [], matrix: null, inferenceMs: 1 } : detection();
      assert.equal(await store.capture(canvas(), paired,
        { capturedAt: 1_000 + index * 300, metadata }), true);
    }
    await store.finish();
    const calls: { detection: Detection; surface: readonly number[] | undefined;
      clipping: TempleClipConfiguration | null | undefined;
      visibility: TempleVisibilityConfiguration | null | undefined }[] = [];
    const renderer = { eyewear: { id: model }, templeClipConfiguration: liveDefault, present(_image: HTMLCanvasElement,
      paired: Detection, surface?: readonly number[], clipping?: TempleClipConfiguration | null,
      savedVisibility?: TempleVisibilityConfiguration | null) {
      calls.push({ detection: paired, surface, clipping, visibility: savedVisibility });
      return paired.matrix !== null;
    } } as unknown as TryOnRenderer;
    const slider = { value: '0' }, status = { textContent: '' };
    // Exercise the real replay method and real immutable store. Only UI/image
    // decoding and the renderer boundary are replaced; no GPU is needed here.
    const controller = Object.assign(Object.create(CaptureController.prototype) as CaptureController, {
      hooks: { renderer: () => renderer, replayPresented() {} }, store,
      panel: { dataset: { state: 'replay' } }, slider, status, renderGeneration: 0,
      cachedIndex: -1, image: { width: 1, height: 1, getContext: () => ({ drawImage() {} }) },
    });
    const replay = Reflect.get(controller, 'replay') as () => Promise<void>;
    for (let index = 0; index < frames.length; index++) {
      slider.value = String(index);
      await replay.call(controller);
    }
    assert.equal(calls.length, 8, 'cross-model metadata must not reach the renderer');
    assert.match(status.textContent, /different glasses session/);
    assert.deepEqual(calls.map(call => call.clipping), [clip, fade, null, null, null, null, dissolve, dissolve]);
    assert.deepEqual(calls.map(call => call.visibility), [null, visibility, null, null, null, null, frontalVisibility, viewVisibility]);
    assert.notDeepEqual(calls[0]!.clipping, liveDefault,
      'replay must retain both saved side cutoffs instead of substituting the fixed model endpoint');
    assert.equal(calls[0]!.clipping!.negativeXCutoffLocalZM, -0.068);
    assert.equal(calls[0]!.clipping!.positiveXCutoffLocalZM, -0.087);
    assert.notDeepEqual(calls[1]!.clipping, liveDefault,
      'saved fade width, coverage and asymmetric endpoints must not follow the new live defaults');
    assert.deepEqual(calls[6]!.clipping, dissolve, 'saved v3 width and asymmetric endpoints remain exact');
    assert.deepEqual(calls[6]!.visibility, frontalVisibility, 'saved nonzero frontal occlusion weight stays exact');
    assert.equal(calls[6]!.surface, store.snapshot.frames[6]!.metadata!.surfacePositions);
    assert.deepEqual(calls[7]!.visibility, viewVisibility, 'saved v2 weights and coverage do not gain v3 occlusion');
    for (let index = 0; index < 4; index++) {
      assert.equal(calls[index]!.surface, store.snapshot.frames[index]!.metadata!.surfacePositions,
        'replay forwards the exact immutable saved surface');
      assert.deepEqual(calls[index]!.detection, store.snapshot.frames[index]!.detection);
    }
    assert.equal(calls[4]!.surface, undefined);
    assert.deepEqual(calls[4]!.detection, { landmarks: [], matrix: null, inferenceMs: 1 });
    assert.equal(calls[5]!.surface, undefined);
    assert.deepEqual(calls[5]!.detection, detection(),
      'geometry-free historical face records still get explicit null appearance');
    assert.equal('templeClip' in store.snapshot.frames[2]!.metadata!, false,
      'replay must not annotate legacy source metadata');
    assert.equal('templeVisibility' in store.snapshot.frames[2]!.metadata!, false);
    for (const index of [1, 7, 6, 0]) {
      slider.value = String(index);
      await replay.call(controller);
    }
    assert.deepEqual(calls[8], calls[1], 'saved v2 fade and v1 visibility return exactly after v3 replay');
    assert.deepEqual(calls[9], calls[7], 'saved v2 visibility returns without acquiring frontal occlusion');
    assert.deepEqual(calls[10], calls[6], 'saved dissolve and v3 occlusion return after historical replay');
    assert.deepEqual(calls[11], calls[0], 'historical hard-v1 remains hard-v1 after current-style replay');
  }
});
