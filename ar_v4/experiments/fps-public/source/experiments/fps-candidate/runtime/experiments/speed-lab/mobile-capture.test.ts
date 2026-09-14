import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import {runExperimentPump} from './live-pump.ts';
import type {PumpContext} from './live-pump.ts';
import type {Pipeline} from './profiles.ts';
import type {FrameInput} from './frame-profiler.ts';
import type {FramePumpStats} from './frame-pump.ts';
import type {OwnedSourceFrame} from './speed-options.ts';
import type {HairMask} from './comparison-renderer.ts';

// The proven mobile capture regressions, repeated against the isolated G and
// FPS loops. Real rendering remains covered by the production browser suites.
function browserFixture() {
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const replace = (key: string, value: unknown): void => {
    if (!saved.has(key)) saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, {value, configurable: true, writable: true});
  };
  const canvases: HTMLCanvasElement[] = [];
  const canvasPixels = new WeakMap<HTMLCanvasElement, number>();
  let videoPixel = 0;
  let videoDraws = 0, readbacks = 0, onVideoDraw = (): void => {};
  class Pixels {
    data: Uint8ClampedArray<ArrayBuffer>; width: number; height: number; colorSpace = 'srgb';
    constructor(data: Uint8ClampedArray<ArrayBuffer>, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }
  replace('ImageData', Pixels); replace('MediaStream', class {});
  const pixels = (canvas: HTMLCanvasElement): Uint8ClampedArray<ArrayBuffer> => {
    const data = new Uint8ClampedArray(canvas.width * canvas.height * 4);
    for (let i = 0; i < data.length; i += 4) {data[i] = canvasPixels.get(canvas) ?? 0; data[i + 3] = 255;}
    return data;
  };
  replace('document', {createElement: () => {
    const canvas = {width: 300, height: 150, getContext: () => ({
      drawImage(source: unknown) {
        canvasPixels.set(canvas, source === video ? videoPixel : canvasPixels.get(source as HTMLCanvasElement) ?? 0);
        if(source === video) {videoDraws++; onVideoDraw();}
      }, clearRect() {}, getImageData: () => {
        readbacks++;
        return new Pixels(pixels(canvas), canvas.width, canvas.height);
      },
    })} as unknown as HTMLCanvasElement;
    canvases.push(canvas); return canvas;
  }});
  let callback: VideoFrameRequestCallback | undefined, nextId = 0;
  const video = {srcObject: null, readyState: 2, currentTime: 0, videoWidth: 80, videoHeight: 40,
    requestVideoFrameCallback: (value: VideoFrameRequestCallback) => {callback = value; return ++nextId;},
    cancelVideoFrameCallback: () => {callback = undefined;}} as unknown as HTMLVideoElement;
  const offer = (time: number, presentedFrames = time, presentationTime = performance.now()): void => {
    video.currentTime = time; const current = callback; callback = undefined;
    assert.ok(current); current(performance.now(), {mediaTime: time, presentedFrames, presentationTime} as VideoFrameCallbackMetadata);
  };
  const restore = (): void => {for (const [key, descriptor] of saved) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key);
  }};
  return {replace, canvases, video, offer, restore, pixels, paintVideo: (value: number) => {videoPixel = value;},
    pendingCallback: () => callback, counts: () => ({videoDraws, readbacks}),
    onVideoDraw: (callback: () => void) => {onVideoDraw = callback;}};
}

function completion() {
  let resolve!: () => void, reject!: (error: unknown) => void;
  const promise = new Promise<void>((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

/** A playback clock can advance while the same presented image remains current. */
function advancingPlaybackClock(video: HTMLVideoElement): () => number {
  let time = 0, reads = 0;
  Object.defineProperty(video, 'currentTime', {configurable: true,
    get() {reads++; time += 0.0007; return time;}, set(value: number) {time = value;}});
  return () => reads;
}

function pumpFixture(f: ReturnType<typeof browserFixture>, pipeline: Pipeline,
  controls: {detect?: () => Promise<unknown>; prepare?: () => Promise<boolean>; variant?: () => 'accepted'|'hair'} = {}) {
  f.replace('createImageBitmap', async () => ({close() {}}));
  const rows: FrameInput[] = [], errors: unknown[] = [], publications: (() => void)[] = [];
  let sequence = 0, detectorCalls = 0;
  const variantCalls: ('accepted'|'hair')[] = [];
  const renderer = {variant: 'accepted' as 'accepted'|'hair', pipeline: 'combined' as Pipeline, stats: {hasMask: false, fallbackReason: 'no-face', changedPixels: 0,
    timings: {cleanCameraMs: 0, composeMs: 0, continuityMs: 0, finalChecksMs: 0, publishMs: 0}},
    selectPipeline() {}, selectVariant(value: 'accepted'|'hair') {variantCalls.push(value); renderer.variant=value;},
    async prepare() {renderer.pipeline=pipeline;return controls.prepare?.() ?? false;}, finish() {return false;}};
  const pump = runExperimentPump({id: 'rate-test', video: f.video, pipeline, mode: 'overlap', owns: () => true,
    nextSequence: () => ++sequence, hairReady: () => false, variant: controls.variant ?? (() => 'accepted'), onBusy() {},
    onError: (error: unknown) => {errors.push(error);}, onHairError: (error: unknown) => {errors.push(error);},
    renderer, detector: {detect: () => {detectorCalls++; return controls.detect?.() ?? Promise.resolve({landmarks: []});},
      lastTiming: null, delegate: 'CPU'}, backend: () => ({active: null, renderer: null}),
    onPublished: (row: FrameInput) => {rows.push(row); for (const check of [...publications]) check();}, hairId: 'hair-only',
  } as unknown as PumpContext);
  return {pump, rows, errors, renderer, variantCalls, detectorCalls: () => detectorCalls,
    stats: () => pump.stats() as FramePumpStats,
    published: (count: number) => new Promise<void>(resolve => {
      const check = (): void => {if (rows.length >= count) {const index = publications.indexOf(check); if(index >= 0) publications.splice(index, 1); resolve();}};
      publications.push(check); check();
    })};
}

test('a playback clock advance during a synchronous snapshot preserves capture admission', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const h = pumpFixture(f, 'combined');
  try {
    f.onVideoDraw(() => {f.video.currentTime += 0.01;}); f.offer(1);
    assert.equal(h.stats().captured, 1, 'An advancing media clock does not invalidate pixels already copied synchronously.');
    await h.published(1);
    assert.equal(f.counts().readbacks, 1); f.onVideoDraw(() => {});
    clock += 125; f.offer(2); await h.published(2);
    assert.equal(h.rows[0]!.capturedAtMs, 1000); assert.equal(h.stats().captured, 2);
    assert.deepEqual(h.errors, []);
  } finally {h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('advancing playback getters preserve exact face, hair, source and publication pairing for stable rVFC identities', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  Object.assign(f.video, {videoWidth: 720, videoHeight: 1280});
  const clockReads = advancingPlaybackClock(f.video);
  type Snapshot = ImageBitmap & {pixels: Uint8ClampedArray<ArrayBuffer>; closed: boolean};
  const bitmaps: Snapshot[] = [], sources: OwnedSourceFrame[] = [], masks: HairMask[] = [], errors: unknown[] = [];
  const rows: FrameInput[] = [], facePixels: number[] = [], hairPixels: number[] = [];
  const pairs: {sourceIdentity: string; detectionIdentity: string}[] = [];
  let sequence = 0, publication = completion();
  f.replace('createImageBitmap', async (canvas: HTMLCanvasElement) => {
    const bitmap = {width: canvas.width, height: canvas.height, pixels: f.pixels(canvas), closed: false,
      close() {bitmap.closed = true;}} as Snapshot;
    bitmaps.push(bitmap); return bitmap;
  });
  const digest = (bytes: Uint8ClampedArray | string): string => createHash('sha256').update(bytes).digest('hex');
  const renderer = {variant: 'hair', pipeline: 'combined',
    stats: {hasMask: true, fallbackReason: null, changedPixels: 1,
      timings: {cleanCameraMs: 0, composeMs: 0, continuityMs: 0, finalChecksMs: 0, publishMs: 0}},
    selectPipeline() {}, selectVariant() {},
    async prepare(canvas: HTMLCanvasElement, detection: {landmarks: number[]}, pair: {sourceIdentity: string; detectionIdentity: string},
      _model: unknown, hair: boolean, source: OwnedSourceFrame) {
      assert.equal(hair, true); assert.equal(source.canvas, canvas); assert.equal(source.sessionId, 'mobile-clock-pair');
      assert.equal(source.generation, sources.length + 1); assert.equal(source.isCurrent(), true);
      assert.equal(source.rgba.width, 720); assert.equal(source.rgba.height, 1280);
      assert.deepEqual(source.rgba.data, f.pixels(canvas));
      assert.equal(detection.landmarks[0], source.rgba.data[0]);
      assert.equal(pair.sourceIdentity, digest(source.rgba.data));
      assert.equal(source.sourceIdentity, pair.sourceIdentity);
      assert.equal(pair.detectionIdentity, digest(JSON.stringify(detection)));
      sources.push(source); pairs.push({sourceIdentity: pair.sourceIdentity, detectionIdentity: pair.detectionIdentity}); return true;
    },
    finish(mask: HairMask | null) {
      assert.ok(mask); const source = sources.at(-1)!;
      assert.ok('sequence' in mask);
      assert.equal(source.isCurrent(), true); assert.equal(mask.sequence, source.generation);
      assert.equal(mask.sourceIdentity, source.sourceIdentity); assert.equal(mask.detectionIdentity, pairs.at(-1)!.detectionIdentity);
      masks.push(mask); return true;
    },
  };
  const pump = runExperimentPump({id: 'mobile-clock-pair', video: f.video, pipeline: 'combined', mode: 'overlap', owns: () => true,
    nextSequence: () => ++sequence, hairReady: () => true, variant: () => 'hair', onBusy() {},
    onError: (error: unknown) => {errors.push(error); publication.reject(error);},
    onHairError: (error: unknown) => {errors.push(error); publication.reject(error);}, renderer,
    detector: {async detect(bitmap: Snapshot, capturedAtMs: number) {
      assert.equal(bitmap.width, 360); assert.equal(bitmap.height, 640); assert.equal(bitmap.closed, false);
      assert.equal(capturedAtMs, clock); facePixels.push(bitmap.pixels[0]!); bitmap.close();
      return {landmarks: [bitmap.pixels[0]!]};
    }, lastTiming: null, delegate: 'CPU'},
    hair: () => ({async segment(bitmap: Snapshot, sourceIdentity: string, sequence: number) {
      assert.equal(bitmap.width, 720); assert.equal(bitmap.height, 1280); assert.equal(bitmap.closed, false);
      assert.equal(sourceIdentity, digest(bitmap.pixels)); hairPixels.push(bitmap.pixels[0]!); bitmap.close();
      return {sequence, sourceIdentity, inferenceMs: 0, extractionMs: 0};
    }}), hairId: 'hair-only', eyewearId: 'amber-horizon', backend: () => ({active: 'CPU', renderer: null}),
    onPublished: (row: FrameInput, pair: {sourceIdentity: string; detectionIdentity: string}) => {
      assert.deepEqual(pair, pairs.at(-1)); rows.push(row); publication.resolve();
    },
  } as unknown as PumpContext);
  try {
    // Advancing the live video after draw must never change the captured pixels
    // used by either worker, the renderer, hashes, or final hair-mask pairing.
    f.onVideoDraw(() => {f.paintVideo(203);}); f.paintVideo(37); f.offer(0, 7);
    assert.equal((pump.stats() as FramePumpStats).captured, 1, 'The first image must survive an advancing playback getter.');
    await publication.promise; assert.equal(sources[0]!.isCurrent(), false);
    const preserved = sources[0]!.rgba.data.slice(), beforeDuplicate = f.counts();
    clock++; f.paintVideo(89); f.offer(0, 7);
    assert.deepEqual(f.counts(), beforeDuplicate); assert.equal(rows.length, 1); assert.equal(bitmaps.length, 2);
    publication = completion(); clock++; f.paintVideo(91); f.offer(0, 8); await publication.promise;
    assert.equal(clockReads(), 0, 'A valid rVFC identity never consults the advancing playback clock.');
    assert.deepEqual(facePixels, [37, 91]); assert.deepEqual(hairPixels, [37, 91]);
    assert.equal((pump.stats() as FramePumpStats).maxOwnedFrames, 1);
    assert.equal(masks.length, 2); assert.ok(bitmaps.every(bitmap => bitmap.closed));
    assert.deepEqual(rows.map(row => [row.sessionId, row.sequence, row.videoMediaTime, row.videoPresentedFrames,
      row.videoPresentationTimeMs, row.sourceWidth, row.sourceHeight]), [
      ['mobile-clock-pair', 1, 0, 7, 1000, 720, 1280], ['mobile-clock-pair', 2, 0, 8, 1002, 720, 1280],
    ]);
    assert.notEqual(sources[0]!.rgba.data.buffer, sources[1]!.rgba.data.buffer);
    assert.notEqual(pairs[0]!.sourceIdentity, pairs[1]!.sourceIdentity);
    assert.deepEqual(sources[0]!.rgba.data, preserved); assert.ok(sources.every(source => !source.isCurrent()));
    assert.deepEqual(errors, []);
  } finally {pump.stop(); await pump.finishCurrent(); f.restore();}
});

test('valid rVFC frame counters stay authoritative when optional media and presentation timestamps are invalid', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const clockReads = advancingPlaybackClock(f.video), h = pumpFixture(f, 'combined');
  try {
    f.offer(NaN, 7, NaN);
    assert.equal(h.stats().captured, 1); await h.published(1);
    assert.equal(h.rows[0]!.videoPresentedFrames, 7);
    assert.equal(h.rows[0]!.videoMediaTime, null); assert.equal(h.rows[0]!.videoPresentationTimeMs, null);
    const beforeDuplicate = f.counts(); clock++; f.offer(NaN, 7, NaN);
    assert.deepEqual(f.counts(), beforeDuplicate); assert.equal(h.detectorCalls(), 1); assert.equal(h.rows.length, 1);
    assert.equal(clockReads(), 0, 'Invalid optional timestamps must not replace an available frame identity with the playback clock.');
    assert.deepEqual(h.errors, []);
  } finally {h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('RAF capture samples an advancing playback clock once and still admits its synchronous snapshot', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const clockReads = advancingPlaybackClock(f.video);
  Object.defineProperty(f.video, 'requestVideoFrameCallback', {value: undefined});
  let callback: FrameRequestCallback | undefined;
  f.replace('requestAnimationFrame', (next: FrameRequestCallback) => {callback = next; return 1;});
  f.replace('cancelAnimationFrame', () => {callback = undefined;});
  const h = pumpFixture(f, 'combined');
  try {
    assert.ok(callback); callback(clock);
    assert.equal(h.stats().captured, 1); assert.equal(clockReads(), 1); await h.published(1);
    assert.equal(h.rows[0]!.videoMediaTime, 0.0007); assert.equal(h.rows[0]!.videoPresentedFrames, null);
    assert.equal(h.rows[0]!.videoPresentationTimeMs, null);
    clock++; assert.ok(callback); callback(clock); await h.published(2);
    assert.equal(clockReads(), 2); assert.equal(h.rows[1]!.videoMediaTime, 0.0014); assert.deepEqual(h.errors, []);
  } finally {h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('Stop revokes pending work and a restarted session admits its first image immediately', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  advancingPlaybackClock(f.video);
  const detected = completion(), release = completion();
  const old = pumpFixture(f, 'combined', {detect: async () => {detected.resolve(); await release.promise; return {landmarks: []};}});
  let restarted: ReturnType<typeof pumpFixture> | undefined;
  try {
    f.offer(1); assert.equal(old.stats().captured, 1); await detected.promise;
    const lateCallback = f.pendingCallback(); assert.ok(lateCallback); old.pump.stop(); clock = 1001;
    restarted = pumpFixture(f, 'combined'); f.offer(2); await restarted.published(1);
    const beforeLate = f.counts(), restartedCallback = f.pendingCallback();
    lateCallback(clock, {mediaTime: 3, presentedFrames: 3, presentationTime: clock} as VideoFrameCallbackMetadata);
    assert.deepEqual(f.counts(), beforeLate); assert.equal(f.pendingCallback(), restartedCallback);
    release.resolve(); await old.pump.finishCurrent();
    assert.equal(old.rows.length, 0); assert.equal(old.stats().ownedFrames, 0);
    assert.equal(restarted.rows[0]!.capturedAtMs, 1001); assert.equal(restarted.stats().captured, 1);
    assert.deepEqual(old.errors, []); assert.deepEqual(restarted.errors, []);
  } finally {release.resolve(); old.pump.stop(); await old.pump.finishCurrent(); restarted?.pump.stop(); await restarted?.pump.finishCurrent(); f.restore();}
});

