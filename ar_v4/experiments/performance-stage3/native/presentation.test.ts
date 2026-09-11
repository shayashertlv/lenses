import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4} from 'three';
import type {Scene} from 'three';
import {TryOnRenderer} from './renderer.ts';
import {NativeGpuFrames} from './gpu-frame.ts';
import type {WebGLRenderer} from 'three';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';

test('native GPU capture precedes clean, retains beauty off/no-face, invalidates old pairs and rerenders fresh beauty on capture failure', () => {
  const calls: string[] = [];
  const backend = {capabilities: {samples: 4}, setSize: () => {}, render: (scene: Scene) => {calls.push(scene.children.length ? 'beauty' : 'clean');}};
  const renderer = Reflect.construct(TryOnRenderer, [backend]) as TryOnRenderer;
  const surface = new Float32Array(468 * 3); for (let i = 2; i < surface.length; i += 3) surface[i] = -60;
  Reflect.set(renderer, 'canonicalPositions', Array(468 * 3).fill(0));
  Reflect.set(renderer, 'faceSurface', {positions: surface, reconstruct: () => true});
  const frame = {width: 3, height: 2} as HTMLCanvasElement;
  const detection: Detection = {landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})),
    matrix: new Matrix4().makeTranslation(0, 0, -60).toArray(), inferenceMs: 0};
  let fail: 'beauty' | 'camera' | null = null, generation = 0, camera = false;
  Reflect.set(renderer, 'gpuFrames', {invalidate: () => {generation++; camera = false;},
    begin: () => {calls.push('begin');}, capture: (slot: string) => {
      calls.push(`capture-${slot}`); if (slot === fail) throw new Error(`injected ${slot} capture failure`); if (slot === 'camera') camera = true;
    }, lease: () => {calls.push('lease'); return {generation, cameraReady: camera};}, metrics: {captureCalls: 2, readbackCalls: 0}});
  const present = (clean: boolean) => renderer.present(frame, detection, Array.from(surface), null, null, clean);
  assert.equal(present(true), true);
  assert.deepEqual(calls, ['beauty', 'begin', 'capture-beauty', 'clean', 'capture-camera', 'lease']);
  const firstGeneration = renderer.gpuFrame!.generation; assert.equal(renderer.gpuFrame!.cameraReady, true);
  assert.equal(renderer.nativeImagePair, null, 'GPU preparation does not manufacture CPU pixel arrays');
  calls.length = 0; present(false);
  assert.deepEqual(calls, ['beauty', 'begin', 'capture-beauty', 'lease']);
  assert.equal(renderer.gpuFrame!.cameraReady, false); assert.notEqual(renderer.gpuFrame!.generation, firstGeneration);
  calls.length = 0;
  assert.equal(renderer.present(frame, {landmarks: [], matrix: null, inferenceMs: 0}, undefined, undefined, undefined, true), false);
  assert.deepEqual(calls, ['beauty', 'begin', 'capture-beauty', 'lease']); assert.equal(renderer.captureSnapshot, null);
  for (const failingCapture of ['beauty', 'camera'] as const) {
    calls.length = 0; fail = failingCapture; assert.equal(present(true), true);
    assert.equal(calls.at(-1), 'beauty', 'capture failure recreates authoritative beauty for checked fallback');
    assert.equal(renderer.gpuFrame, null); assert.equal(renderer.stageTimings.sharedCameraReady, false);
    assert.equal(renderer.stageTimings.sharedCameraFailure, `injected ${failingCapture} capture failure`);
  }
  fail = null; present(true); assert.ok(renderer.gpuFrame);
  assert.throws(() => renderer.present({width: 0, height: 0} as HTMLCanvasElement, detection), /empty/);
  assert.equal(renderer.gpuFrame, null, 'an invalid new image cannot retain the old GPU lease');
  Reflect.set(renderer, 'disposed', true); assert.equal(present(true), false);
});

test('real GPU lease rejects every stale/context-lost operation and exposes copied metrics', () => {
  let lost = false;
  const gl = {texStorage2D: () => {}, getContextAttributes: () => ({alpha: false}), isContextLost: () => lost};
  const owner = new NativeGpuFrames({getContext: () => gl} as unknown as WebGLRenderer);
  Reflect.set(owner, 'width', 23); Reflect.set(owner, 'height', 17); Reflect.set(owner, 'beautyReady', true);
  const frame = owner.lease(); assert.equal(frame.width, 23); assert.equal(frame.height, 17); assert.equal(frame.cameraReady, false);
  const metrics = frame.metrics; metrics.readbackCalls = 999; assert.equal(frame.metrics.readbackCalls, 0);
  lost = true; assert.throws(() => frame.presentToCanvas('beauty'), /stale or unavailable/); lost = false;
  // The outer wrapper may reject before native.present (empty source, draw or
  // clone failure). Its explicit revocation must still invalidate retained leases.
  const native = Reflect.construct(TryOnRenderer, [{}]) as TryOnRenderer;
  Reflect.set(native, 'gpuFrames', owner); Reflect.set(native, 'currentGpuFrame', frame);
  assert.equal(native.gpuFrame, frame); native.invalidateGpuFrame();
  assert.equal(native.gpuFrame, null); assert.equal(native.nativeImagePair, null);
  native.invalidateGpuFrame();
  for (const operation of [() => frame.metrics, () => frame.presentToCanvas('beauty'), () => frame.readDiagnostic('beauty'),
    () => frame.compose([]), () => frame.audit(), () => frame.runPackedFlags({category: new Uint8Array(1), maskWidth: 1, maskHeight: 1,
      hairIndex: 1, regions: new Uint8Array(391)})]) assert.throws(operation, /stale or unavailable/);
  owner.dispose(); owner.dispose(); assert.throws(() => owner.lease(), /stale or unavailable/);
});
