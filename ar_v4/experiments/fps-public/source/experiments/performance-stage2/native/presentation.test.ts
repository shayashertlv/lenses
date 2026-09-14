import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4} from 'three';
import type {Scene} from 'three';
import {TryOnRenderer} from './renderer.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';

test('actual native presentation saves beauty before clean, skips optional work off/no-face and recovers native beauty on shared readback failure', () => {
  const calls: string[] = [];
  const backend = {capabilities: {samples: 4}, setSize: () => {}, render: (scene: Scene) => {calls.push(scene.children.length ? 'beauty' : 'clean');}};
  const renderer = Reflect.construct(TryOnRenderer, [backend]) as TryOnRenderer;
  const surface = new Float32Array(468 * 3); for (let i = 2; i < surface.length; i += 3) surface[i] = -60;
  Reflect.set(renderer, 'canonicalPositions', Array(468 * 3).fill(0));
  Reflect.set(renderer, 'faceSurface', {positions: surface, reconstruct: () => true});
  const pair = {beauty: {data: new Uint8ClampedArray(24).fill(9), width: 3, height: 2},
    camera: {data: new Uint8ClampedArray(24).fill(77), width: 3, height: 2}};
  let fail = false;
  Reflect.set(renderer, 'pairedReadback', {begin: () => {calls.push('begin');}, capture: (slot: number) => {calls.push(`capture${slot}`);},
    read: () => {calls.push('read'); if (fail) throw new Error('injected shared readback failure'); return pair;}, metrics: {
      mode: 'shared-context-two-readbacks', beautyReadbackMs: 0, cameraReadbackMs: 0,
      readbackMs: 0, extractionMs: 0, readbackCalls: 2, readbackBytes: 48, allocatedBytes: 0}});
  const detection: Detection = {landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})),
    matrix: new Matrix4().makeTranslation(0, 0, -60).toArray(), inferenceMs: 0};
  const frame = {width: 3, height: 2} as HTMLCanvasElement;
  const present = (camera: boolean): boolean => renderer.present(frame, detection, Array.from(surface), null, null, camera);
  assert.equal(present(true), true);
  assert.deepEqual(calls, ['beauty', 'begin', 'capture0', 'clean', 'capture1', 'read']);
  assert.equal(renderer.nativeImagePair, pair); assert.equal(renderer.stageTimings.sharedCameraReady, true);
  calls.length = 0; assert.equal(present(false), true);
  assert.deepEqual(calls, ['beauty']); assert.equal(renderer.nativeImagePair, null);
  assert.equal(renderer.stageTimings.requestedCamera, false); assert.equal(renderer.stageTimings.sharedReadback, null);
  calls.length = 0; assert.equal(renderer.present(frame, {landmarks: [], matrix: null, inferenceMs: 0}, undefined, undefined, undefined, true), false);
  assert.deepEqual(calls, ['beauty']); assert.equal(renderer.nativeImagePair, null); assert.equal(renderer.captureSnapshot, null);
  calls.length = 0; fail = true; assert.equal(present(true), true);
  assert.deepEqual(calls, ['beauty', 'begin', 'capture0', 'clean', 'capture1', 'read', 'beauty']);
  assert.equal(renderer.nativeImagePair, null); assert.ok(renderer.captureSnapshot, 'valid accepted geometry survives optional capture failure');
  assert.equal(renderer.stageTimings.sharedCameraReady, false); assert.equal(renderer.stageTimings.sharedCameraFailure, 'injected shared readback failure');
  calls.length = 0; fail = false; assert.equal(present(true), true); assert.equal(renderer.nativeImagePair, pair);
  Reflect.set(renderer, 'disposed', true); assert.equal(present(true), false); assert.equal(renderer.nativeImagePair, null);
});
