import test from 'node:test';
import assert from 'node:assert/strict';
import {copyHairMask, liveNasalRoi, OwnedLiveOutputs} from './ownership.ts';
import type {Detection} from '../../src/runtime/detector.ts';
import type {HairMask} from '../hair-arm-preview/compose.ts';

test('live nasal ROI matches frozen-study known coordinates, margins, clamp and no-face clearing', () => {
  const detection: Detection = {matrix: Array(16).fill(0), inferenceMs: 0, landmarks: Array.from({length: 478}, () => ({x: .5, y: .4, z: 0}))};
  detection.landmarks[98] = {x: .4, y: .3, z: 0}; detection.landmarks[327] = {x: .6, y: .5, z: 0};
  assert.deepEqual(liveNasalRoi(detection, 1000, 800), {x0: 394, y0: 234, x1: 606, y1: 406});
  assert.equal(liveNasalRoi({...detection, matrix: null}, 1000, 800), null);
  detection.landmarks[1] = {x: NaN, y: .4, z: 0}; assert.equal(liveNasalRoi(detection, 1000, 800), null);
});
test('held accepted/hair toggles own their bytes and cannot reread a mutated camera or previous output', () => {
  const owned = new OwnedLiveOutputs(), before = new Uint8ClampedArray(8).fill(10), after = new Uint8ClampedArray(8).fill(20);
  owned.replace(2, 1, before, after); before.fill(99); after.fill(88);
  assert.equal(owned.copy('accepted')!.pixels[0], 10); assert.equal(owned.copy('hair')!.pixels[0], 20);
  const returned = owned.copy('hair')!; returned.pixels.fill(77); assert.equal(owned.copy('hair')!.pixels[0], 20);
  owned.replace(2, 1, new Uint8ClampedArray(8).fill(30));
  assert.equal(owned.copy('hair')!.pixels[0], 30); assert.equal(owned.copy('accepted')!.pixels[0], 30);
  owned.clear(); assert.equal(owned.copy('accepted'), null); assert.equal(owned.copy('hair'), null);
});
test('a failed next output-size contract clears both preceding buffers', () => {
  const owned = new OwnedLiveOutputs(); owned.replace(1, 1, new Uint8ClampedArray(4));
  assert.throws(() => owned.replace(2, 2, new Uint8ClampedArray(4)), /dimensions/);
  assert.equal(owned.copy('accepted'), null); assert.equal(owned.copy('hair'), null);
});
test('accepted hair mask retention deep-copies category, confidence and labels for an exact held diagnostic', () => {
  const mask: HairMask = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), model: 'hair-only', modelSHA256: '3'.repeat(64),
    categorySHA256: '4'.repeat(64), confidenceSHA256: '5'.repeat(64), labels: ['background', 'hair'], hairIndex: 1,
    category: new Uint8Array([1, 0]), confidence: new Float32Array([.75, .1]), width: 2, height: 1};
  const retained = copyHairMask(mask); mask.category.fill(0); mask.confidence.fill(0); mask.labels = ['bad'];
  assert.deepEqual(retained.category, new Uint8Array([1, 0])); assert.equal(retained.confidence[0], .75);
  assert.deepEqual(retained.labels, ['background', 'hair']);
});

test('category-only retention owns category bytes and explicitly omits confidence', () => {
  const mask = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), model: 'hair-only', modelSHA256: '3'.repeat(64),
    categorySHA256: '4'.repeat(64), labels: ['background', 'hair'], hairIndex: 1,
    outputMode: 'category-only' as const, category: new Uint8Array([1, 0]), width: 2, height: 1};
  const retained = copyHairMask(mask); mask.category.fill(0); mask.labels[1] = 'bad';
  assert.deepEqual(retained.category, new Uint8Array([1, 0])); assert.equal(retained.labels[1], 'hair');
  assert.equal('confidence' in retained, false); assert.equal('confidenceSHA256' in retained, false);
});
