import test from 'node:test';
import assert from 'node:assert/strict';
import {composeHairArms, inwardWeights} from './compose.ts';
import type {HairArmInput} from './compose.ts';

function fixture(): HairArmInput {
  const width = 12, height = 8, before = new Uint8ClampedArray(width * height * 4).fill(100), background = before.slice();
  for (let index = 3; index < before.length; index += 4) before[index] = background[index] = 255;
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: 'tom-ford-clear'};
  const labels = ['background', 'hair'];
  return {width, height, before, background, pair, geometryPair: {...pair},
    expectedModel: {id: 'hair-only', sha256: '3'.repeat(64), labels, hairIndex: 1},
    mask: {...pair, model: 'hair-only', modelSHA256: '3'.repeat(64), categorySHA256: '4'.repeat(64), confidenceSHA256: '5'.repeat(64),
      labels, hairIndex: 1, width, height, category: new Uint8Array(width * height).fill(1), confidence: new Float32Array(width * height).fill(.9)},
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 4, y0: 2, x1: 8, y1: 5}, {x0: 5, y0: 4, x1: 7, y1: 6}],
      editableRects: [{x0: 2, y0: 1, x1: 10, y1: 6}]}, noseRoi: {x0: 5, y0: 4, x1: 7, y1: 7}};
}
const paint = (input: HairArmInput, x: number, y: number, value = 0): void => { for (let channel = 0; channel < 3; channel++) input.before[(y * input.width + x) * 4 + channel] = value; };
test('actual arm residual is replaced while protected optics, nasal ROI and untouched camera remain exact', () => {
  const input = fixture(); paint(input, 3, 3); paint(input, 6, 3); paint(input, 6, 6);
  const before = input.before.slice(), category = input.mask.category.slice(), result = composeHairArms(input);
  assert.equal(result.fallbackReason, null); assert.equal(result.statistics.changedPixels, 1);
  assert.equal(result.pixels[(3 * input.width + 3) * 4], 100);
  assert.equal(result.pixels[(3 * input.width + 6) * 4], 0); assert.equal(result.pixels[(6 * input.width + 6) * 4], 0);
  assert.deepEqual(input.before, before); assert.deepEqual(input.mask.category, category);
  assert.equal(result.statistics.protectedCorridorHairResidualPixels, 1);
});
test('blank hair mask preserves the whole accepted output and cannot reuse a preceding mask', () => {
  const input = fixture(); paint(input, 3, 3); assert.equal(composeHairArms(input).statistics.changedPixels, 1);
  input.mask.category.fill(0); const result = composeHairArms(input);
  assert.equal(result.fallbackReason, null); assert.deepEqual(result.pixels, input.before); assert.equal(result.statistics.changedPixels, 0);
});
test('bad image, model, geometry, label, mask storage, or finite-value contracts fall back to an owned accepted copy', () => {
  const mutations: ((value: HairArmInput) => void)[] = [
    value => { value.mask.sourceSHA256 = '9'.repeat(64); }, value => { value.geometryPair.detectionSHA256 = '9'.repeat(64); },
    value => { value.mask.modelSHA256 = '9'.repeat(64); }, value => { value.mask.hairIndex = 0; },
    value => { value.mask.width++; }, value => { value.mask.confidence[0] = NaN; }, value => { value.mask.category[0] = 2; },
    value => { value.protection = {...value.protection, width: 100}; }, value => { value.noseRoi = {x0: -1, y0: 0, x1: 2, y1: 2}; },
  ];
  for (const mutate of mutations) { const input = fixture(); paint(input, 3, 3); mutate(input); const result = composeHairArms(input);
    assert.ok(result.fallbackReason); assert.deepEqual(result.pixels, input.before); assert.notEqual(result.pixels, input.before); assert.equal(result.statistics.changedPixels, 0); }
});
test('any clean-camera discrepancy outside all valid saved eyewear bounds rejects composition', () => {
  const input = fixture(); paint(input, 3, 3); input.background[0] = 101;
  const result = composeHairArms(input); assert.match(result.fallbackReason!, /clean native camera pass/);
  assert.equal(result.backgroundReferenceCheck.changedPixels, 1); assert.deepEqual(result.pixels, input.before);
});
test('category winner defines support; two-pixel inward feather never expands hair or treats confidence as opacity', () => {
  const category = new Uint8Array(7 * 7);
  for (let y = 1; y < 6; y++) for (let x = 1; x < 6; x++) category[y * 7 + x] = 1;
  const weights = inwardWeights(category, 7, 7, 1, 7, 7);
  assert.equal(weights[0], 0); assert.equal(weights[8], .5); assert.equal(weights[24], 1);
  const input = fixture(); paint(input, 3, 3); input.mask.confidence.fill(.01);
  assert.equal(composeHairArms(input).statistics.changedPixels, 1);
});
test('known pixel-center mapping preserves asymmetric mask orientation at different resolutions', () => {
  const weights = inwardWeights(new Uint8Array([1, 0, 0, 0]), 2, 2, 1, 8, 8);
  assert.equal(weights[2 * 8 + 2], 1); assert.equal(weights[2 * 8 + 6], 0); assert.equal(weights[6 * 8 + 2], 0);
});
