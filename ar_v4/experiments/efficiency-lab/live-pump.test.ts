import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import {runExperimentPump} from './live-pump.ts';
import type {PumpContext} from './live-pump.ts';
import type {Pipeline} from './profiles.ts';
import type {FrameInput} from './frame-profiler.ts';
import type {CaptureAdmissionStats} from './capture-rate.ts';
import type {FramePumpStats} from './frame-pump.ts';
import type {OwnedSourceFrame} from './speed-options.ts';
import type {HairMask} from './comparison-renderer.ts';
import type {HairDeliveryTrace} from './hair-delivery.ts';
import type {HairRequestTiming} from './hair-cost/delivery.ts';

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

test('lean pump keeps revoked capture and face canvases alive until pending bitmap readers settle', async () => {
  const f = browserFixture(), snapshots: {source: HTMLCanvasElement; resolve: (bitmap: ImageBitmap) => void}[] = [];
  const closed: string[] = [];
  f.replace('createImageBitmap', (source: HTMLCanvasElement) => new Promise<ImageBitmap>(resolve => {snapshots.push({source, resolve});}));
  let sequence = 0;
  const pump = runExperimentPump({id: 'test', video: f.video, pipeline: 'lean', mode: 'overlap', owns: () => true,
    nextSequence: () => ++sequence, hairReady: () => true, variant: () => 'hair', onBusy() {}, onError() {}, onHairError() {},
    hair: () => {throw new Error('Cancelled bitmap must not reach hair worker.');},
    detector: {detect: () => {throw new Error('Cancelled bitmap must not reach face worker.');}},
  } as unknown as PumpContext);
  try {
    f.offer(1); assert.equal(snapshots.length, 2);
    const capture = snapshots[0]!.source, face = snapshots[1]!.source;
    assert.notEqual(capture, face); pump.stop();
    assert.equal(capture.width, 80); assert.equal(face.width, 80);
    const faceClosed = completion();
    snapshots[1]!.resolve({close: () => {closed.push('face'); faceClosed.resolve();}} as ImageBitmap); await faceClosed.promise;
    assert.equal(face.width, 0); assert.equal(capture.width, 80); assert.deepEqual(closed, ['face']);
    snapshots[0]!.resolve({close: () => {closed.push('hair');}} as ImageBitmap); await pump.finishCurrent();
    assert.equal(capture.width, 0); assert.deepEqual(closed, ['face', 'hair']);
  } finally {pump.stop(); f.restore();}
});

function rateFixture(f: ReturnType<typeof browserFixture>, pipeline: Pipeline,
  controls: {detect?: () => Promise<unknown>; prepare?: () => Promise<boolean>; variant?: () => 'accepted'|'hair'} = {}) {
  f.replace('createImageBitmap', async () => ({close() {}}));
  const rows: FrameInput[] = [], errors: unknown[] = [], publications: (() => void)[] = [];
  let sequence = 0, detectorCalls = 0;
  const variantCalls: ('accepted'|'hair')[] = [];
  const renderer = {variant: 'accepted' as 'accepted'|'hair', pipeline: 'g' as Pipeline, stats: {hasMask: false, fallbackReason: 'no-face', changedPixels: 0,
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
    stats: () => pump.stats() as FramePumpStats & {admission: CaptureAdmissionStats},
    published: (count: number) => new Promise<void>(resolve => {
      const check = (): void => {if (rows.length >= count) {const index = publications.indexOf(check); if(index >= 0) publications.splice(index, 1); resolve();}};
      publications.push(check); check();
    })};
}

test('Q suppresses only repeat pre-prepare presentations while G retains every call', async () => {
  for (const pipeline of ['g', 'publish'] as const) {
    const f = browserFixture(), h = rateFixture(f, pipeline);
    try {
      for (let i=1;i<=3;i++) {f.offer(i);await h.published(i);}
      assert.equal(h.rows.length, 3); assert.equal(h.detectorCalls(), 3);
      assert.equal(h.variantCalls.length, pipeline==='publish'?1:3);
      const first=h.rows[0]!.native!, last=h.rows[2]!.native!;
      assert.equal(first['publication.prePrepareSkippedThisFrame'], false, 'The first display always takes the original selection call.');
      assert.equal(last['publication.prePrepareRequests'], 3);
      assert.equal(last['publication.prePrepareSkipped'], pipeline==='publish'?2:0);
      assert.equal(last['publication.prePrepareCalls'], pipeline==='publish'?1:3);
      assert.equal(last['publication.suppressUnchangedRequested'], pipeline==='publish');
      assert.equal(h.stats().admission.rateHz, null); assert.deepEqual(h.errors, []);
    } finally {h.pump.stop();await h.pump.finishCurrent();f.restore();}
  }
});

test('Q preserves immediate variant toggles and reapplies a changed view or renderer before preparation', async () => {
  const f = browserFixture(); let variant: 'accepted'|'hair' = 'accepted';
  const h = rateFixture(f, 'publish', {variant: () => variant});
  try {
    f.offer(1);await h.published(1);
    variant='hair';h.renderer.selectVariant(variant);
    assert.equal(h.renderer.variant, 'hair', 'The direct toggle is not gated by a new image.');
    f.offer(2);await h.published(2);
    assert.equal(h.rows[1]!.native!['publication.prePrepareSkippedThisFrame'], true);
    assert.equal(h.rows[1]!.variant, 'hair');
    variant='accepted'; // A changed requested view not yet applied must be forwarded.
    f.offer(3);await h.published(3);
    assert.equal(h.rows[2]!.native!['publication.prePrepareSkippedThisFrame'], false);
    assert.equal(h.rows[2]!.variant, 'accepted');
    h.renderer.pipeline='scratch';
    f.offer(4);await h.published(4);
    assert.equal(h.rows[3]!.native!['publication.prePrepareSkippedThisFrame'], false);
    assert.equal(h.rows[3]!.native!['publication.prePrepareCalls'], 3);
    assert.deepEqual(h.errors, []);
  } finally {h.pump.stop();await h.pump.finishCurrent();f.restore();}
});

test('capped live pump skips work before capture and publishes full-size pairs with bounded start spacing', async () => {
  for (const pipeline of ['rate12', 'rate10', 'rate8'] as const) {
    const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
    const rate = Number(pipeline.slice(4)), interval = 1000 / rate, h = rateFixture(f, pipeline);
    try {
      f.offer(1); await h.published(1); assert.equal(h.detectorCalls(), 1);
      const initial = f.counts();
      clock += interval - 0.001; f.offer(2);
      assert.deepEqual(f.counts(), initial); assert.equal(h.detectorCalls(), 1);
      clock = 1000 + interval + 0.001; f.offer(3); await h.published(2);
      clock += interval + 1; f.offer(3); // Same video image stays ineligible even after the time gate opens.
      assert.equal(h.rows.length, 2); assert.equal(h.detectorCalls(), 2);
      assert.equal(h.rows[1]!.sourceWidth, 80); assert.equal(h.rows[1]!.sourceHeight, 40);
      assert.ok(h.rows[1]!.capturedAtMs - h.rows[0]!.capturedAtMs >= interval);
      assert.equal(h.rows[1]!.native!['admission.rateHz'], rate);
      assert.deepEqual(h.stats().admission, {rateHz: rate, candidateCallbacks: 3, duplicateCallbacks: 1,
        rateSkipped: 1, backpressureSkipped: 0, captures: 2});
      assert.equal(h.stats().maxInFlightInference, 1); assert.ok(h.stats().maxOwnedFrames <= 2);
      await h.pump.finishCurrent(); assert.deepEqual(h.errors, []);
    } finally {h.pump.stop(); f.restore();}
  }
});

test('G live admission remains uncapped on successively delivered camera images', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const h = rateFixture(f, 'g');
  try {
    f.offer(1); await h.published(1); clock++; f.offer(2); await h.published(2);
    assert.equal(h.rows[1]!.capturedAtMs - h.rows[0]!.capturedAtMs, 1);
    assert.equal(h.stats().admission.rateHz, null); assert.equal(h.stats().admission.rateSkipped, 0);
    assert.equal(h.stats().admission.captures, 2); assert.deepEqual(h.errors, []);
  } finally {h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('rate rejection preserves an already captured pending pair and Hold drains only the active image', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const detected = completion(), release = completion();
  const h = rateFixture(f, 'rate10', {detect: async () => {detected.resolve(); await release.promise; return {landmarks: []};}});
  try {
    f.offer(1); await detected.promise; clock = 1100; f.offer(2);
    assert.equal(h.stats().pendingFrames, 1); assert.equal(h.stats().captured, 2);
    const before = f.counts(); clock = 1130; f.offer(3);
    assert.deepEqual(f.counts(), before); assert.equal(h.stats().pendingFrames, 1); assert.equal(h.stats().replaced, 0);
    const drained = h.pump.finishCurrent(); release.resolve(); await drained;
    assert.equal(h.rows.length, 1); assert.equal(h.rows[0]!.sequence, 1);
    assert.equal(h.detectorCalls(), 1); assert.equal(h.stats().ownedFrames, 0);
    assert.equal(h.stats().admission.rateSkipped, 1); assert.deepEqual(h.errors, []);
  } finally {release.resolve(); h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('busy rate pump does no lazy capture or inference and does not consume future admission', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const prepared = completion(), release = completion(), secondDetected = completion(); let detections = 0;
  const h = rateFixture(f, 'rate10', {
    detect: async () => {if (++detections === 2) secondDetected.resolve(); return {landmarks: []};},
    prepare: async () => {prepared.resolve(); await release.promise; return false;},
  });
  try {
    f.offer(1); await prepared.promise; clock = 1100; f.offer(2); await secondDetected.promise;
    const before = f.counts(); clock = 1200; f.offer(3);
    assert.deepEqual(f.counts(), before); assert.equal(h.detectorCalls(), 2);
    assert.equal(h.stats().admission.backpressureSkipped, 1); assert.equal(h.stats().admission.captures, 2);
    release.resolve(); await h.published(2);
    clock = 1201; f.offer(4); await h.published(3);
    assert.equal(h.rows[2]!.capturedAtMs, 1201); assert.equal(h.stats().admission.captures, 3);
    assert.equal(h.stats().maxOwnedFrames, 2); assert.equal(h.stats().maxInFlightInference, 1);
    assert.deepEqual(h.errors, []);
  } finally {release.resolve(); h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('a playback clock advance during a synchronous snapshot preserves capture admission', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const h = rateFixture(f, 'rate8');
  try {
    f.onVideoDraw(() => {f.video.currentTime += 0.01;}); f.offer(1);
    assert.equal(h.stats().admission.captures, 1, 'An advancing media clock does not invalidate pixels already copied synchronously.');
    await h.published(1);
    assert.equal(f.counts().readbacks, 1); f.onVideoDraw(() => {});
    clock += 125; f.offer(2); await h.published(2);
    assert.equal(h.rows[0]!.capturedAtMs, 1000); assert.equal(h.stats().admission.captures, 2);
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
  const pairs: {sourceSHA256: string; detectionSHA256: string}[] = [];
  let sequence = 0, publication = completion();
  f.replace('createImageBitmap', async (canvas: HTMLCanvasElement) => {
    const bitmap = {width: canvas.width, height: canvas.height, pixels: f.pixels(canvas), closed: false,
      close() {bitmap.closed = true;}} as Snapshot;
    bitmaps.push(bitmap); return bitmap;
  });
  const digest = (bytes: Uint8ClampedArray | string): string => createHash('sha256').update(bytes).digest('hex');
  const renderer = {variant: 'hair', pipeline: 'g',
    stats: {hasMask: true, fallbackReason: null, changedPixels: 1,
      timings: {cleanCameraMs: 0, composeMs: 0, continuityMs: 0, finalChecksMs: 0, publishMs: 0}},
    selectPipeline() {}, selectVariant() {},
    async prepare(canvas: HTMLCanvasElement, detection: {landmarks: number[]}, pair: {sourceSHA256: string; detectionSHA256: string},
      _model: unknown, hair: boolean, source: OwnedSourceFrame) {
      assert.equal(hair, true); assert.equal(source.canvas, canvas); assert.equal(source.sessionId, 'mobile-clock-pair');
      assert.equal(source.generation, sources.length + 1); assert.equal(source.isCurrent(), true);
      assert.equal(source.rgba.width, 720); assert.equal(source.rgba.height, 1280);
      assert.deepEqual(source.rgba.data, f.pixels(canvas));
      assert.equal(detection.landmarks[0], source.rgba.data[0]);
      assert.equal(pair.sourceSHA256, digest(source.rgba.data));
      assert.equal(source.sourceSHA256, pair.sourceSHA256);
      assert.equal(pair.detectionSHA256, digest(JSON.stringify(detection)));
      sources.push(source); pairs.push({sourceSHA256: pair.sourceSHA256, detectionSHA256: pair.detectionSHA256}); return true;
    },
    finish(mask: HairMask | null) {
      assert.ok(mask); const source = sources.at(-1)!;
      assert.ok('sequence' in mask);
      assert.equal(source.isCurrent(), true); assert.equal(mask.sequence, source.generation);
      assert.equal(mask.sourceSHA256, source.sourceSHA256); assert.equal(mask.detectionSHA256, pairs.at(-1)!.detectionSHA256);
      masks.push(mask); return true;
    },
  };
  const pump = runExperimentPump({id: 'mobile-clock-pair', video: f.video, pipeline: 'g', mode: 'overlap', owns: () => true,
    nextSequence: () => ++sequence, hairReady: () => true, variant: () => 'hair', onBusy() {},
    onError: (error: unknown) => {errors.push(error); publication.reject(error);},
    onHairError: (error: unknown) => {errors.push(error); publication.reject(error);}, renderer,
    detector: {async detect(bitmap: Snapshot, capturedAtMs: number) {
      assert.equal(bitmap.width, 360); assert.equal(bitmap.height, 640); assert.equal(bitmap.closed, false);
      assert.equal(capturedAtMs, clock); facePixels.push(bitmap.pixels[0]!); bitmap.close();
      return {landmarks: [bitmap.pixels[0]!]};
    }, lastTiming: null, delegate: 'CPU'},
    hair: () => ({async segment(bitmap: Snapshot, sourceSHA256: string, sequence: number) {
      assert.equal(bitmap.width, 720); assert.equal(bitmap.height, 1280); assert.equal(bitmap.closed, false);
      assert.equal(sourceSHA256, digest(bitmap.pixels)); hairPixels.push(bitmap.pixels[0]!); bitmap.close();
      return {sequence, sourceSHA256, inferenceMs: 0, extractionMs: 0};
    }}), hairId: 'hair-only', eyewearId: 'amber', backend: () => ({active: 'CPU', renderer: null}),
    onPublished: (row: FrameInput, pair: {sourceSHA256: string; detectionSHA256: string}) => {
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
    assert.equal(masks.length, 2); assert.ok(bitmaps.every(bitmap => bitmap.closed));
    assert.deepEqual(rows.map(row => [row.sessionId, row.sequence, row.videoMediaTime, row.videoPresentedFrames,
      row.videoPresentationTimeMs, row.sourceWidth, row.sourceHeight]), [
      ['mobile-clock-pair', 1, 0, 7, 1000, 720, 1280], ['mobile-clock-pair', 2, 0, 8, 1002, 720, 1280],
    ]);
    assert.notEqual(sources[0]!.rgba.data.buffer, sources[1]!.rgba.data.buffer);
    assert.notEqual(pairs[0]!.sourceSHA256, pairs[1]!.sourceSHA256);
    assert.deepEqual(sources[0]!.rgba.data, preserved); assert.ok(sources.every(source => !source.isCurrent()));
    assert.equal((pump.stats() as {admission: CaptureAdmissionStats}).admission.duplicateCallbacks, 1);
    assert.deepEqual(errors, []);
  } finally {pump.stop(); await pump.finishCurrent(); f.restore();}
});

test('valid rVFC frame counters stay authoritative when optional media and presentation timestamps are invalid', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  const clockReads = advancingPlaybackClock(f.video), h = rateFixture(f, 'g');
  try {
    f.offer(NaN, 7, NaN);
    assert.equal(h.stats().captured, 1); await h.published(1);
    assert.equal(h.rows[0]!.videoPresentedFrames, 7);
    assert.equal(h.rows[0]!.videoMediaTime, null); assert.equal(h.rows[0]!.videoPresentationTimeMs, null);
    const beforeDuplicate = f.counts(); clock++; f.offer(NaN, 7, NaN);
    assert.deepEqual(f.counts(), beforeDuplicate); assert.equal(h.detectorCalls(), 1); assert.equal(h.rows.length, 1);
    assert.equal(h.stats().admission.duplicateCallbacks, 1);
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
  const h = rateFixture(f, 'g');
  try {
    assert.ok(callback); callback(clock);
    assert.equal(h.stats().captured, 1); assert.equal(clockReads(), 1); await h.published(1);
    assert.equal(h.rows[0]!.videoMediaTime, 0.0007); assert.equal(h.rows[0]!.videoPresentedFrames, null);
    assert.equal(h.rows[0]!.videoPresentationTimeMs, null);
    clock++; assert.ok(callback); callback(clock); await h.published(2);
    assert.equal(clockReads(), 2); assert.equal(h.rows[1]!.videoMediaTime, 0.0014); assert.deepEqual(h.errors, []);
  } finally {h.pump.stop(); await h.pump.finishCurrent(); f.restore();}
});

test('Stop revokes pending work and a restarted capped session admits its first image immediately', async () => {
  const f = browserFixture(); let clock = 1000; f.replace('performance', {now: () => clock});
  advancingPlaybackClock(f.video);
  const detected = completion(), release = completion();
  const old = rateFixture(f, 'rate8', {detect: async () => {detected.resolve(); await release.promise; return {landmarks: []};}});
  let restarted: ReturnType<typeof rateFixture> | undefined;
  try {
    f.offer(1); assert.equal(old.stats().captured, 1); await detected.promise;
    const lateCallback = f.pendingCallback(); assert.ok(lateCallback); old.pump.stop(); clock = 1001;
    restarted = rateFixture(f, 'rate8'); f.offer(2); await restarted.published(1);
    const beforeLate = f.counts(), restartedCallback = f.pendingCallback();
    lateCallback(clock, {mediaTime: 3, presentedFrames: 3, presentationTime: clock} as VideoFrameCallbackMetadata);
    assert.deepEqual(f.counts(), beforeLate); assert.equal(f.pendingCallback(), restartedCallback);
    release.resolve(); await old.pump.finishCurrent();
    assert.equal(old.rows.length, 0); assert.equal(old.stats().ownedFrames, 0);
    assert.equal(restarted.rows[0]!.capturedAtMs, 1001); assert.equal(restarted.stats().admission.captures, 1);
    assert.deepEqual(old.errors, []); assert.deepEqual(restarted.errors, []);
  } finally {release.resolve(); old.pump.stop(); await old.pump.finishCurrent(); restarted?.pump.stop(); await restarted?.pump.finishCurrent(); f.restore();}
});

test('lean live pump reuses released canvases while each published packet owns an independent paired snapshot', async () => {
  const f = browserFixture(); f.replace('createImageBitmap', async () => ({close() {}}));
  const pairs: {canvas: HTMLCanvasElement; rgba: ImageData; sourceSHA256: string; current: () => boolean}[] = [];
  const rows: unknown[] = [], errors: unknown[] = []; let sequence = 0;
  let publication = completion();
  const stats = {hasMask: false, fallbackReason: 'no-face', changedPixels: 0,
    timings: {cleanCameraMs: 0, composeMs: 0, continuityMs: 0, finalChecksMs: 0, publishMs: 0}};
  const renderer = {variant: 'accepted', stats, selectPipeline() {}, selectVariant() {},
    async prepare(_canvas: HTMLCanvasElement, _detection: unknown, pair: {sourceSHA256: string}, _model: unknown, _hair: boolean,
      source: {canvas: HTMLCanvasElement; rgba: ImageData; sourceSHA256: string; isCurrent: () => boolean}) {
      assert.equal(source.sourceSHA256, pair.sourceSHA256); assert.ok(source.isCurrent());
      pairs.push({canvas: source.canvas, rgba: source.rgba, sourceSHA256: source.sourceSHA256, current: source.isCurrent}); return false;
    }, finish() {return false;}};
  const pump = runExperimentPump({id: 'test', video: f.video, pipeline: 'lean', mode: 'overlap', owns: () => true,
    nextSequence: () => ++sequence, hairReady: () => false, variant: () => 'accepted', onBusy() {},
    onError: (error: unknown) => {errors.push(error); publication.reject(error);},
    onHairError: (error: unknown) => {errors.push(error); publication.reject(error);},
    renderer, detector: {detect: async () => ({landmarks: []}), lastTiming: null, delegate: 'CPU'},
    backend: () => ({active: null, renderer: null}), onPublished: (row: unknown) => {rows.push(row); publication.resolve();}, hairId: 'hair-only',
  } as unknown as PumpContext);
  try {
    // WebCrypto may finish after any number of event-loop ticks. Await the real
    // publication callback, which follows both SHA operations and preparation.
    f.offer(1); await publication.promise; assert.equal(rows.length, 1);
    const firstPixels = pairs[0]!.rgba.data.slice(); assert.equal(pairs[0]!.current(), false);
    publication = completion(); f.offer(2); await publication.promise; assert.equal(rows.length, 2);
    assert.equal(pairs[0]!.canvas, pairs[1]!.canvas); assert.notEqual(pairs[0]!.rgba.data.buffer, pairs[1]!.rgba.data.buffer);
    assert.deepEqual(pairs[0]!.rgba.data, firstPixels); assert.equal(pairs[0]!.sourceSHA256, pairs[1]!.sourceSHA256);
    const row = rows[1] as {native: Record<string, unknown>};
    assert.equal(row.native['input.hashExplicitCopyBytes'], 0); assert.equal(row.native['input.capturePool.reused'], 1);
    assert.equal(row.native['input.facePool.reused'], 1); assert.equal(row.native['pump.maxInFlightInference'], 1);
    assert.deepEqual(errors, []); await pump.finishCurrent();
    assert.ok(f.canvases.every(canvas => canvas.width === 0)); assert.deepEqual(pairs[0]!.rgba.data, firstPixels);
  } finally {pump.stop(); f.restore();}
});

function hairDeliveryFixture(f:ReturnType<typeof browserFixture>,pipeline:'g'|'hair-release') {
  type Snapshot=ImageBitmap&{pixels:Uint8ClampedArray<ArrayBuffer>};
  f.replace('createImageBitmap',async(canvas:HTMLCanvasElement)=>({width:canvas.width,height:canvas.height,pixels:f.pixels(canvas),close(){}}));
  const requests:{sequence:number;release:()=>void;complete:()=>void}[]=[],rows:FrameInput[]=[],traces:HairDeliveryTrace[]=[],errors:unknown[]=[];
  const sources:OwnedSourceFrame[]=[],callbacks:(()=>void)[]=[];let sequence=0,detects=0;
  const changed=()=>{for(const callback of [...callbacks])callback();};
  const wait=(predicate:()=>boolean)=>new Promise<void>(resolve=>{const check=()=>{if(predicate()){callbacks.splice(callbacks.indexOf(check),1);resolve();}};callbacks.push(check);check();});
  const renderer={pipeline,variant:'hair',stats:{hasMask:false,fallbackReason:null,changedPixels:0,
    timings:{cleanCameraMs:0,composeMs:0,continuityMs:0,finalChecksMs:0,publishMs:0}},selectPipeline(){},selectVariant(){},
    async prepare(canvas:HTMLCanvasElement,detection:{landmarks:number[]},pair:{sourceSHA256:string},_model:unknown,_hair:boolean,source:OwnedSourceFrame) {
      assert.equal(source.canvas,canvas);assert.equal(source.isCurrent(),true);assert.equal(source.rgba.data[0],detection.landmarks[0]);
      assert.equal(pair.sourceSHA256,createHash('sha256').update(source.rgba.data).digest('hex'));sources.push(source);return true;
    },finish(mask:HairMask|null) {if(mask){assert.equal(mask.sourceSHA256,sources.at(-1)!.sourceSHA256);assert.ok('sequence' in mask);assert.equal(mask.sequence,sources.at(-1)!.generation);}
      renderer.stats.hasMask=!!mask;return !!mask;}};
  const pump=runExperimentPump({id:'hair-delivery-test',generation:3,video:f.video,pipeline,mode:'overlap',owns:()=>true,
    nextSequence:()=>++sequence,hairReady:()=>true,variant:()=> 'hair',onBusy(){},
    onError:(error:unknown)=>{errors.push(error);changed();},onHairError:(error:unknown)=>{errors.push(error);changed();},renderer,
    detector:{async detect(bitmap:Snapshot){detects++;changed();return {landmarks:[bitmap.pixels[0]]};},lastTiming:null,delegate:'CPU'},
    hair:()=>({beginSegment(bitmap:Snapshot,sourceSHA256:string,seq:number,_mode:unknown,early:boolean,observer:(timing:HairRequestTiming)=>void) {
      assert.equal(early,pipeline==='hair-release');assert.equal(sourceSHA256,createHash('sha256').update(bitmap.pixels).digest('hex'));
      const resultGate=completion(),workerGate=completion();
      const timing:HairRequestTiming={requestId:seq,sequence:seq,releaseWorkerEarly:early,categoryExtractionMode:'sdk',
        submittedAtMs:performance.now(),receivedAtMs:null,workerReleasedAtMs:null,validatedAtMs:null,hashStartedAtMs:null,completedAtMs:null,
        validationMs:null,hashMs:null,workerValidationMs:null,workerInferenceMs:null,workerExtractionMs:null,workerElapsedMs:null,
        pendingAtSubmission:1,outcome:'pending',reason:null};
      const emit=()=>observer({...timing});emit();
      requests.push({sequence:seq,release(){timing.receivedAtMs=performance.now();if(early){timing.workerReleasedAtMs=performance.now();workerGate.resolve();}emit();},
        complete(){timing.completedAtMs=performance.now();timing.outcome='completed';timing.workerReleasedAtMs??=timing.completedAtMs;emit();workerGate.resolve();resultGate.resolve();}});changed();
      return {workerReleased:workerGate.promise,result:resultGate.promise.then(()=>({sequence:seq,sourceSHA256,inferenceMs:1,extractionMs:1}))};
    }}),hairId:'hair-only',eyewearId:'amber',backend:()=>({active:'CPU',renderer:null}),
    onPublished:(row:FrameInput)=>{rows.push(row);changed();},onHairTrace:(trace:HairDeliveryTrace)=>{traces.push(trace);},
  } as unknown as PumpContext);
  return {pump,requests,rows,traces,errors,sources,wait,detects:()=>detects,
    stats:()=>pump.stats() as FramePumpStats&{hairDelivery:{ownedImages:number|null;maxOwnedImages:number|null;pendingResults:number}}};
}

test('U reuses the worker before a late result completes without owning a third source image or reusing its mask',async()=>{
  const f=browserFixture(),h=hairDeliveryFixture(f,'hair-release');
  try {
    f.paintVideo(37);f.offer(1);await h.wait(()=>h.requests.length===1);h.requests[0]!.release();await h.wait(()=>h.rows.length===1);
    assert.equal(h.rows[0]!.hasMask,false);assert.equal(h.sources[0]!.isCurrent(),false);
    const firstPixels=h.sources[0]!.rgba.data.slice();
    f.paintVideo(91);f.offer(2);await h.wait(()=>h.requests.length===2);
    assert.equal(h.traces.filter(t=>t.timing?.outcome==='completed').length,0,'Second worker request starts before first result validation/hash completes.');
    h.requests[1]!.release();await h.wait(()=>h.rows.length===2);
    assert.equal(h.stats().ownedFrames,0);assert.equal(h.stats().hairDelivery.ownedImages,2,'Published but validating images remain owned.');
    const before=f.counts();f.paintVideo(203);f.offer(3);
    assert.deepEqual(f.counts(),before,'The third image is rejected before draw, readback, hashing and bitmap allocation.');
    assert.equal(h.requests.length,2);assert.deepEqual(h.sources[0]!.rgba.data,firstPixels);
    h.requests[0]!.complete();await new Promise<void>(resolve=>setImmediate(resolve));
    f.offer(4);await h.wait(()=>h.requests.length===3);h.requests[2]!.release();h.requests[2]!.complete();await h.wait(()=>h.rows.length===3);
    assert.equal(h.rows[2]!.hasMask,true);assert.equal(h.sources[2]!.rgba.data[0],203);
    assert.equal(h.stats().hairDelivery.maxOwnedImages,2);
    const late=h.traces.filter(t=>t.sequence===1).at(-1)!;
    assert.equal(late.timing!.outcome,'completed');assert.equal(late.usedAtPublication,false);assert.ok(late.disposedAtMs!==null);
    let drained=false;const drain=h.pump.finishCurrent().then(()=>{drained=true;});await new Promise<void>(resolve=>setImmediate(resolve));
    assert.equal(drained,false,'Graceful switching/export waits for every full result, not merely worker release.');
    h.requests[1]!.complete();await drain;
    assert.equal(h.stats().hairDelivery.ownedImages,0);assert.equal(h.stats().hairDelivery.pendingResults,0);assert.deepEqual(h.errors,[]);
  } finally {for(const request of h.requests)request.complete();h.pump.stop();await h.pump.finishCurrent();f.restore();}
});

test('instrumented G still waits for full hair validation before submitting its next request',async()=>{
  const f=browserFixture(),h=hairDeliveryFixture(f,'g');
  try {
    f.offer(1);await h.wait(()=>h.requests.length===1);h.requests[0]!.release();await h.wait(()=>h.rows.length===1);
    f.offer(2);await h.wait(()=>h.detects()===2);await new Promise<void>(resolve=>setImmediate(resolve));
    assert.equal(h.requests.length,1,'A worker message alone does not release the G gate.');
    h.requests[0]!.complete();await h.wait(()=>h.requests.length===2);h.requests[1]!.complete();await h.wait(()=>h.rows.length===2);
    await h.pump.finishCurrent();assert.deepEqual(h.errors,[]);
  } finally {for(const request of h.requests)request.complete();h.pump.stop();await h.pump.finishCurrent();f.restore();}
});

test('stopping U revokes publication but retains late-result cleanup and telemetry ownership',async()=>{
  const f=browserFixture(),h=hairDeliveryFixture(f,'hair-release');
  try {
    f.offer(1);await h.wait(()=>h.requests.length===1);h.requests[0]!.release();h.pump.stop();
    h.requests[0]!.complete();await h.pump.finishCurrent();
    assert.equal(h.rows.length,0);assert.equal(h.stats().hairDelivery.ownedImages,0);assert.equal(h.stats().hairDelivery.pendingResults,0);
    assert.equal(h.traces.at(-1)!.timing!.outcome,'completed');assert.equal(h.traces.at(-1)!.publicationAtMs,null);
    assert.deepEqual(h.errors,[]);
  } finally {for(const request of h.requests)request.complete();h.pump.stop();await h.pump.finishCurrent();f.restore();}
});

test('U preserves replacement of a pending snapshot before inference while keeping two image leases',async()=>{
  const f=browserFixture(),started=completion(),release=completion();
  const h=rateFixture(f,'hair-release',{detect:async()=>{started.resolve();await release.promise;return {landmarks:[]};}});
  try {
    f.offer(1);await started.promise;f.offer(2);f.offer(3);
    assert.equal(h.stats().captured,3);assert.equal(h.stats().replaced,1);assert.equal(h.stats().pendingFrames,1);
    assert.equal((h.pump.stats() as {hairDelivery:{ownedImages:number;maxOwnedImages:number}}).hairDelivery.ownedImages,2);
    release.resolve();await h.published(2);await h.pump.finishCurrent();
    assert.deepEqual(h.rows.map(row=>row.sequence),[1,3],'The latest unprocessed image replaces its older pending predecessor.');
    assert.equal((h.pump.stats() as {hairDelivery:{ownedImages:number;maxOwnedImages:number}}).hairDelivery.maxOwnedImages,2);
    assert.equal((h.pump.stats() as {hairDelivery:{ownedImages:number;maxOwnedImages:number}}).hairDelivery.ownedImages,0);
    assert.deepEqual(h.errors,[]);
  } finally {release.resolve();h.pump.stop();await h.pump.finishCurrent();f.restore();}
});
