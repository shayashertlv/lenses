import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Box3, Matrix4, Vector3} from 'three';
import {TryOnRenderer} from './renderer.ts';
import {DEFAULT_SPEED_OPTIONS} from '../speed-options.ts';
import type {OwnedSourceFrame} from '../speed-options.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import {eyewearById} from '../../../references/perfect-temples/src/render/eyewear.ts';

function deferred<T>() {let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};}

function fixture() {
  const events: string[] = [], priorDocument = globalThis.document, priorImage = globalThis.ImageData;
  let current = true, syncReads = 0, callback: (() => void) | undefined;
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
    readonly data: Uint8ClampedArray; readonly width: number; readonly height: number; readonly colorSpace = 'srgb';
    constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }});
  const gl = {SAMPLES: 1, NO_ERROR: 0, RGBA: 2, UNSIGNED_BYTE: 3, getParameter: () => 4,
    readPixels: (_x: number, _y: number, _w: number, _h: number, _f: number, _t: number, pixels: Uint8Array) => {
      syncReads++; pixels.fill(27); events.push('sync-read');}, getError: () => 0, isContextLost: () => false};
  const context = {drawImage: () => {}, putImageData: () => {events.push('publish');}, clearRect: () => {}};
  const canvas = (): HTMLCanvasElement => ({width: 32, height: 24, getContext: (name: string) => name === 'webgl2' ? gl : context}) as unknown as HTMLCanvasElement;
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {createElement: canvas}});
  const matrix = new Matrix4().makeRotationX(.48).setPosition(0, 0, -60).toArray();
  const surface = new Float32Array(468 * 3); for (let offset = 2; offset < surface.length; offset += 3) surface[offset] = -60;
  const snapshot = {eyewearModelId: 'tom-ford-clear', rawMatrix: matrix, correctedMatrix: matrix, eyewearMatrix: matrix,
    surfacePositions: surface, yawDegrees: 0, templeClip: null, templeVisibility: null};
  const pixels = (value: number) => new ImageData(new Uint8ClampedArray(32 * 24 * 4).fill(value), 32, 24);
  const nativePending = deferred<boolean>(), branchPending = deferred<{beauty: ImageData; camera: null}>();
  const beauty = pixels(180), branchPixels = pixels(27);
  const native = {eyewear: eyewearById('tom-ford-clear'), captureSnapshot: snapshot, stageTimings: null,
    nativeImagePair: {beauty, camera: null},
    setFrameOptions: (_options: unknown, _source: unknown, submission?: () => void) => {callback = submission;},
    presentAsync: () => {events.push('native-submit'); callback?.(); return nativePending.promise;}, dispose: () => {events.push('native-dispose');}};
  const box = (x: number) => new Box3(new Vector3(x, -.01, -.03), new Vector3(x + .01, .01, .02));
  const branch = {present: (_frame: unknown, _paired: Detection, actual: number[]) => {
    events.push('branch-submit'); assert.deepEqual(actual, Array.from(surface)); return true;}, captureSnapshot: snapshot,
    clearOwnedFrame: () => {events.push('branch-clear');}, dispose: () => {events.push('branch-dispose');},
    rearDropBounds: {optical: box(-.005), originalArms: [box(-.07), box(.06)], candidateArms: [box(-.07), box(.06)]},
    rearDropDiagnostics: {}};
  const renderer = Reflect.construct(TryOnRenderer, [canvas(), canvas(), canvas(), native, branch]) as TryOnRenderer;
  const pbo = {begin: () => {events.push('pbo-begin');}, capture: () => {events.push('pbo-submit');},
    finish: (isCurrent: () => boolean) => {assert.equal(isCurrent(), true); events.push('pbo-await'); return branchPending.promise;},
    cancel: () => {events.push('pbo-cancel');}, dispose: () => {events.push('pbo-dispose');},
    metrics: {queuedCalls: 1, queuedBytes: branchPixels.data.length, retrievedCalls: 1, retrievedBytes: branchPixels.data.length,
      completed: true, fallbackReason: null}};
  Reflect.set(renderer, 'branchPbo', pbo);
  const frame = canvas(), source: OwnedSourceFrame = {canvas: frame, rgba: pixels(255), sourceSHA256: 'a'.repeat(64),
    generation: 1, sessionId: 1, colorSpace: 'srgb', isCurrent: () => current};
  const detection: Detection = {matrix, landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})), inferenceMs: 0};
  const run = (asyncTemples = true) => {renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, asyncReadback: true, asyncTemples,
    fewerCopies: true, poolReadbackScratch: true}, source, () => {events.push('scheduler-signal');});
    return renderer.presentAsync(frame, detection);};
  return {renderer, events, nativePending, branchPending, branchPixels, beauty, run, detection, syncReads: () => syncReads,
    revoke: () => {current = false;}, restore: () => {
      Object.defineProperty(globalThis, 'document', {configurable: true, value: priorDocument});
      Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: priorImage});
    }};
}

test('early branch submits exact geometry before native retrieval and joins both completions before publication', async () => {
  const f = fixture();
  try {
    const pending = f.run();
    assert.deepEqual(f.events, ['native-submit', 'scheduler-signal', 'branch-submit', 'pbo-begin', 'pbo-submit', 'pbo-await']);
    assert.throws(() => f.renderer.setFrameOptions(DEFAULT_SPEED_OPTIONS), /pending/);
    f.branchPending.resolve({beauty: f.branchPixels, camera: null}); await Promise.resolve();
    assert.equal(f.events.includes('publish'), false); assert.equal(f.renderer.ownedPixels, null);
    f.nativePending.resolve(true); assert.equal(await pending, true);
    assert.equal(f.renderer.stageTimings.efficiencyLab.asyncTemplesUsed, true);
    assert.equal(f.renderer.stageTimings.efficiencyLab.branchSubmittedBeforeBeautyAwait, true);
    assert.equal(f.syncReads(), 0); assert.equal(f.renderer.diagnostics?.fallback, null);
    const protection = f.renderer.captureSnapshot!.protection!, output = f.renderer.ownedPixels!;
    for (const rect of protection.protectedRects) for (let y = rect.y0; y < rect.y1; y++)
      for (let x = rect.x0; x < rect.x1; x++) assert.equal(output.data[(y * output.width + x) * 4], 180);
  } finally {f.renderer.dispose(); f.restore();}
});

test('disabled temple candidate retains the serial G branch after beauty completion and signals native submission once', async () => {
  const f = fixture();
  try {
    const pending = f.run(false);
    assert.deepEqual(f.events, ['native-submit', 'scheduler-signal']);
    f.nativePending.resolve(true); assert.equal(await pending, true);
    assert.equal(f.events.filter(event => event === 'scheduler-signal').length, 1);
    assert.equal(f.events.filter(event => event === 'branch-submit').length, 1);
    assert.equal(f.syncReads(), 1); assert.equal(f.events.includes('pbo-submit'), false);
    assert.equal(f.renderer.stageTimings.efficiencyLab.asyncTemplesRequested, false);
    assert.equal(f.renderer.diagnostics?.fallback, null);
  } finally {f.renderer.dispose(); f.restore();}
});

test('invalid early branch eligibility still drains native ownership before rejecting and permitting another pair', async () => {
  const f = fixture();
  try {
    f.detection.matrix = Array(16).fill(0);
    const pending = f.run();
    // The fake native renderer deliberately returns geometry for this invalid
    // input, to exercise rejection between native submission and branch queue.
    let settled = false; void pending.catch(() => {settled = true;});
    await Promise.resolve(); await Promise.resolve();
    assert.equal(settled, false); assert.throws(() => f.renderer.setFrameOptions(DEFAULT_SPEED_OPTIONS), /pending/);
    f.nativePending.resolve(true); await assert.rejects(pending, /singular/);
    assert.equal(f.renderer.ownedPixels, null); assert.equal(f.events.includes('pbo-submit'), false);
    f.renderer.setFrameOptions(DEFAULT_SPEED_OPTIONS);
  } finally {f.renderer.dispose(); f.restore();}
});

test('failed branch fence renders and reads the same pair synchronously, retaining final optical protection', async () => {
  const f = fixture();
  try {
    const pending = f.run(); f.branchPending.reject(new Error('injected branch fence failure')); f.nativePending.resolve(true);
    assert.equal(await pending, true); assert.equal(f.syncReads(), 1);
    assert.equal(f.events.filter(event => event === 'branch-submit').length, 2);
    assert.equal(f.renderer.stageTimings.efficiencyLab.asyncTemplesUsed, false);
    assert.match(f.renderer.stageTimings.efficiencyLab.branchFallback!, /injected branch fence/);
    assert.equal(f.renderer.diagnostics?.fallback, null); assert.ok(f.renderer.captureSnapshot!.protection);
  } finally {f.renderer.dispose(); f.restore();}
});

test('revoked image or stopped renderer cannot publish either completed half of an early branch join', async () => {
  for (const stop of [false, true]) {
    const f = fixture();
    try {
      const pending = f.run(); if (stop) f.renderer.dispose(); else f.revoke();
      f.branchPending.resolve({beauty: f.branchPixels, camera: null}); f.nativePending.resolve(true);
      await assert.rejects(pending, {name: 'AbortError'});
      assert.equal(f.renderer.ownedPixels, null); assert.equal(f.renderer.captureSnapshot, null);
      assert.equal(f.events.includes('publish'), false); assert.equal(f.syncReads(), 0);
    } finally {f.renderer.dispose(); f.restore();}
  }
});
