import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4} from 'three';
import type {Scene} from 'three';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';
import {DEFAULT_SPEED_OPTIONS} from '../speed-options.ts';
import type {OwnedSourceFrame} from '../speed-options.ts';
import {sourceCameraEligibility} from './source-camera.ts';
import {TryOnRenderer} from './renderer.ts';

const rgba = (): ImageData => ({width: 3, height: 2, colorSpace: 'srgb', data: new Uint8ClampedArray(24).fill(255)}) as ImageData;
function native() {
  const calls: string[] = [], frame = {width: 3, height: 2} as HTMLCanvasElement;
  const backend = {capabilities: {samples: 4}, setSize: () => {}, getContext: () => ({isContextLost: () => false}),
    render: (scene: Scene) => {calls.push(scene.children.length ? 'beauty' : 'clean');}};
  const renderer = Reflect.construct(TryOnRenderer, [backend]) as TryOnRenderer;
  const surface = new Float32Array(468 * 3); for (let i = 2; i < surface.length; i += 3) surface[i] = -60;
  Reflect.set(renderer, 'canonicalPositions', Array(468 * 3).fill(0));
  Reflect.set(renderer, 'faceSurface', {positions: surface, reconstruct: () => true});
  const beauty = rgba(), camera = rgba(); camera.data[0] = 71;
  Reflect.set(renderer, 'pairedReadback', {begin: () => calls.push('begin'), capture: (slot: number) => calls.push(`capture${slot}`),
    read: () => ({beauty, camera}), readBeauty: () => beauty, metrics: {readbackCalls: 1, readbackBytes: 24}});
  const detection: Detection = {landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})),
    matrix: new Matrix4().makeTranslation(0, 0, -60).toArray(), inferenceMs: 0};
  let current = true;
  const source: OwnedSourceFrame = {canvas: frame, rgba: rgba(), sourceSHA256: 'a'.repeat(64), generation: 1, sessionId: 1,
    colorSpace: 'srgb', isCurrent: () => current};
  return {renderer, calls, frame, source, detection, surface: Array.from(surface), beauty, camera, revoke: () => {current = false;}};
}

test('source reuse requires current opaque sRGB same-size bytes and falls back for unsupported input', () => {
  const b = native(); assert.equal(sourceCameraEligibility(b.source, 3, 2), null);
  assert.match(sourceCameraEligibility(undefined, 3, 2)!, /No owned/);
  assert.match(sourceCameraEligibility(b.source, 2, 2)!, /dimensions/);
  const transparent = {...b.source, rgba: rgba()}; transparent.rgba.data[3] = 254;
  assert.match(sourceCameraEligibility(transparent, 3, 2)!, /opaque/);
  assert.match(sourceCameraEligibility({...b.source, rgba: {...rgba(), colorSpace: 'display-p3'} as ImageData}, 3, 2)!, /sRGB/);
  b.renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, reuseSourcePixels: true}, b.source);
  b.renderer.present(b.frame, b.detection, b.surface, null, null, true);
  assert.deepEqual(b.calls, ['beauty', 'begin', 'capture0']);
  assert.equal(b.renderer.nativeImagePair!.camera, b.source.rgba); assert.equal(b.renderer.nativeImagePair!.beauty, b.beauty);
  assert.equal(b.renderer.stageTimings.speedLab.reuseSourcePixelsUsed, true);
  b.calls.length = 0; b.renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, reuseSourcePixels: true}, transparent);
  b.renderer.present(b.frame, b.detection, b.surface, null, null, true);
  assert.deepEqual(b.calls, ['beauty', 'begin', 'capture0', 'clean', 'capture1']);
  assert.equal(b.renderer.nativeImagePair!.camera, b.camera); assert.match(b.renderer.stageTimings.speedLab.sourceReuseFallback!, /opaque/);
  b.revoke(); assert.throws(() => sourceCameraEligibility(b.source, 3, 2), {name: 'AbortError'});
});

test('failed asynchronous native retrieval rerenders the same pair synchronously; cancellation cannot commit', async () => {
  const b = native();
  const pbo = {begin: () => {}, capture: () => {}, cancel: () => {}, metrics: {completed: false, queuedCalls: 1, queuedBytes: 24},
    finish: async () => {throw new Error('injected fence failure');}};
  Reflect.set(b.renderer, 'pbo', pbo);
  b.renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, reuseSourcePixels: true, asyncReadback: true}, b.source);
  assert.equal(await b.renderer.presentAsync(b.frame, b.detection, b.surface, null, null, true), true);
  assert.deepEqual(b.calls, ['beauty', 'beauty', 'begin', 'capture0']);
  assert.equal(b.renderer.nativeImagePair!.beauty, b.beauty); assert.equal(b.renderer.nativeImagePair!.camera, b.source.rgba);
  assert.equal(b.renderer.stageTimings.speedLab.asyncReadbackUsed, false);
  assert.equal(b.renderer.stageTimings.speedLab.asyncFallback, 'injected fence failure');
  Reflect.set(b.renderer, 'pbo', {...pbo, finish: async () => {b.revoke(); return {beauty: b.beauty, camera: null};}});
  b.renderer.setFrameOptions({...DEFAULT_SPEED_OPTIONS, reuseSourcePixels: true, asyncReadback: true}, b.source);
  await assert.rejects(b.renderer.presentAsync(b.frame, b.detection, b.surface, null, null, true), {name: 'AbortError'});
  assert.equal(b.renderer.nativeImagePair, null); assert.equal(b.renderer.captureSnapshot, null);
});

test('disposed native async presentation returns false before old async state or pixel work is consulted', async () => {
  const b = native();
  Reflect.set(b.renderer, 'activeOptions', {...DEFAULT_SPEED_OPTIONS, asyncReadback: true});
  Reflect.set(b.renderer, 'awaiting', true);
  Reflect.set(b.renderer, 'disposed', true);
  const before = b.calls.length;
  assert.equal(await b.renderer.presentAsync(b.frame, b.detection, b.surface, null, null, true), false);
  assert.equal(b.calls.length, before);
  assert.equal(b.renderer.nativeImagePair, null);
  assert.equal(b.renderer.captureSnapshot, null);
});
