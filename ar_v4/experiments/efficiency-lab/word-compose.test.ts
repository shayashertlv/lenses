import test from 'node:test';
import assert from 'node:assert/strict';
import {composeHairArms, comparePixels, inside} from '../hair-arm-preview/compose.ts';
import type {HairArmInput} from '../hair-arm-preview/compose.ts';
import type {LiveHairArmInput} from '../hair-live-preview/live-mask.ts';
import {composeHairArmsFast as baseline} from '../performance-candidate/fast-compose.ts';
import {composeHairArmsFast, checkHairProtection, CompositionScratch} from './fast-compose.ts';
import type {FastHairArmResult} from './fast-compose.ts';

function fixture(seed: number, width = 31, height = 19): HairArmInput {
  let state = seed;
  const random = (): number => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
  const labels = seed % 2 ? ['background', 'hair'] : ['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others'];
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64),
    eyewearModel: Math.floor(seed / 2) % 2 ? 'tom-ford-clear' : 'amber-horizon'};
  const maskWidth = Math.floor(width * [.4, 1, 1.7][seed % 3]!), maskHeight = Math.floor(height * .7);
  const category = new Uint8Array(maskWidth * maskHeight), confidence = new Float32Array(category.length);
  for (let index = 0; index < category.length; index++) {
    category[index] = random() < .75 ? 1 : Math.floor(random() * labels.length); confidence[index] = random();
  }
  const before = new Uint8ClampedArray(width * height * 4), background = before.slice();
  const input: HairArmInput = {width, height, before, background, pair, geometryPair: {...pair},
    expectedModel: {id: 'test-pinned-model', sha256: '3'.repeat(64), labels, hairIndex: 1},
    mask: {...pair, model: 'test-pinned-model', modelSHA256: '3'.repeat(64), categorySHA256: '4'.repeat(64),
      confidenceSHA256: '5'.repeat(64), labels, hairIndex: 1, width: maskWidth, height: maskHeight, category, confidence},
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 2, y0: 2, x1: 12, y1: 6}, {x0: 8, y0: 4, x1: 18, y1: 10}],
      editableRects: [{x0: 1, y0: 0, x1: width, y1: height}, {x0: 0, y0: 3, x1: width - 2, y1: height - 1}]},
    noseRoi: {x0: 10, y0: 7, x1: 15, y1: 13}};
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const bounded = input.protection.protectedRects.some(rect => inside(rect, x, y))
      || input.protection.editableRects.some(rect => inside(rect, x, y)) || inside(input.noseRoi, x, y);
    for (let channel = 0; channel < 4; channel++) {
      const offset = (y * width + x) * 4 + channel;
      before[offset] = background[offset] = Math.floor(random() * 256);
      if (bounded && random() < .55) before[offset] = Math.floor(random() * 256);
    }
  }
  return input;
}

function originalFields(result: FastHairArmResult): ReturnType<typeof baseline> {
  const {wordComparisonRequested: _requested, wordComparisonUsed: _used, wordComparedPixels: _count, ...original} = result;
  return original;
}

function categoryOnly(input: HairArmInput): LiveHairArmInput {
  const {confidence: _confidence, confidenceSHA256: _hash, ...category} = input.mask;
  return {...input, mask: {...category, outputMode: 'category-only'}};
}

function offsetCopy(array: Uint8ClampedArray, offset: number): Uint8ClampedArray {
  const buffer = new ArrayBuffer(array.byteLength + offset + 8);
  new Uint8Array(buffer).fill(231);
  const view = new Uint8ClampedArray(buffer, offset, array.length); view.set(array); return view;
}

test('X aligned word scan equals independent frozen pixels, weights and every statistic across models and category shapes', () => {
  let edited = 0;
  for (let seed = 1; seed <= 48; seed++) {
    const input = fixture(seed);
    if (seed % 4 === 0) input.mask.category.fill(1);
    if (seed % 4 === 1) input.mask.category.fill(0);
    if (seed % 4 === 2) for (let index = 0; index < input.mask.category.length; index++) input.mask.category[index] = index % 2;
    const savedBefore = input.before.slice(), savedBackground = input.background.slice(), savedMask = input.mask.category.slice();
    const result = composeHairArmsFast(input, {wordCompose: true, collectEligibleResidualIndices: true});
    const {regions: _regions, eligibleResidualIndices: _indices, ...frozenFields} = originalFields(result);
    assert.deepEqual(frozenFields, composeHairArms(input));
    assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
    assert.deepEqual(originalFields(composeHairArmsFast(categoryOnly(input), {wordCompose: true, collectEligibleResidualIndices: true})), originalFields(result));
    assert.equal(result.wordComparisonRequested, true); assert.equal(result.wordComparisonUsed, true);
    assert.equal(result.wordComparedPixels, input.width * input.height); assert.equal(result.fallbackReason, null);
    edited += Number(result.statistics.changedPixels > 0);
    assert.notEqual(result.pixels.buffer, input.before.buffer);
    assert.deepEqual(input.before, savedBefore); assert.deepEqual(input.background, savedBackground); assert.deepEqual(input.mask.category, savedMask);
  }
  assert.ok(edited > 24, 'The independent comparisons must exercise actual hair edits.');
});

test('X counts alpha-only residuals without replacing alpha or manufacturing a visible edit', () => {
  const input = fixture(1); input.before.set(input.background); input.mask.category.fill(1);
  const index = input.width + 1, offset = index * 4;
  input.before[offset + 3] = (input.background[offset + 3]! + 17) % 256;
  const result = composeHairArmsFast(input, {wordCompose: true, collectEligibleResidualIndices: true});
  assert.equal(result.fallbackReason, null); assert.equal(result.statistics.renderResidualPixels, 1);
  assert.equal(result.statistics.eligibleResidualPixels, 1); assert.equal(result.statistics.hairEligibleResidualPixels, 1);
  assert.equal(result.statistics.changedPixels, 0); assert.equal(result.weights[index], 0);
  assert.deepEqual(result.eligibleResidualIndices, Uint32Array.of(index)); assert.deepEqual(result.pixels, input.before);
  assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
});

test('X respects aligned subarray offsets and falls back to bytes for either unaligned RGBA view', () => {
  for (const [beforeOffset, backgroundOffset] of [[4, 8], [0, 0], [1, 0], [0, 3], [1, 3]] as const) {
    const input = fixture(7);
    input.before = offsetCopy(input.before, beforeOffset); input.background = offsetCopy(input.background, backgroundOffset);
    const result = composeHairArmsFast(input, {wordCompose: true, collectEligibleResidualIndices: true});
    assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
    assert.equal(result.fallbackReason, null);
    const aligned = beforeOffset % 4 === 0 && backgroundOffset % 4 === 0;
    assert.equal(result.wordComparisonUsed, aligned); assert.equal(result.wordComparedPixels, aligned ? input.width * input.height : 0);
  }
  const input = fixture(7), shared = new Uint8ClampedArray(new SharedArrayBuffer(input.before.byteLength));
  shared.set(input.before); input.before = shared;
  const result = composeHairArmsFast(input, {wordCompose: true});
  assert.deepEqual(originalFields(result), baseline(input)); assert.equal(result.wordComparisonUsed, false);
});

test('X clean-reference rejection retains all-four-channel delta, accepted pixels, and actual completed scan telemetry', () => {
  for (const channel of [0, 1, 2, 3]) {
    const input = fixture(3); input.before[channel] = 100; input.background[channel] = 117;
    const result = composeHairArmsFast(input, {wordCompose: true});
    assert.deepEqual(originalFields(result), baseline(input)); assert.deepEqual(result.pixels, input.before);
    assert.equal(result.fallbackReason, 'The clean native camera pass is not byte-exact outside the saved eyewear bounds.');
    assert.equal(result.backgroundReferenceCheck.changedPixels, 1); assert.equal(result.backgroundReferenceCheck.maxDelta, 17);
    assert.equal(result.statistics.changedPixels, 0); assert.equal(result.statistics.renderResidualPixels, 0);
    assert.equal(result.wordComparisonUsed, true); assert.equal(result.wordComparedPixels, input.width * input.height);
  }
});

test('X validates pairing, categories, confidence and bounds before word work and keeps original rejection ordering', () => {
  const invalid: ((input: HairArmInput) => void)[] = [
    input => {input.mask.sourceSHA256 = '9'.repeat(64);},
    input => {input.geometryPair.detectionSHA256 = '9'.repeat(64);},
    input => {input.mask.modelSHA256 = '9'.repeat(64);},
    input => {input.mask.category[0] = 255; input.mask.confidence[0] = NaN;},
    input => {input.mask.confidence[0] = Infinity;}, input => {input.mask.width++;},
    input => {input.background = input.background.subarray(1);},
    input => {input.noseRoi = {x0: -1, y0: 0, x1: 2, y1: 2};},
  ];
  for (const mutate of invalid) {
    const input = fixture(5); mutate(input);
    const result = composeHairArmsFast(input, {wordCompose: true});
    assert.deepEqual(originalFields(result), baseline(input)); assert.ok(result.fallbackReason);
    assert.deepEqual(result.pixels, input.before); assert.equal(result.wordComparisonUsed, false); assert.equal(result.wordComparedPixels, 0);
  }
  const input = categoryOnly(fixture(5)); input.mask.category[0] = 255;
  assert.deepEqual(originalFields(composeHairArmsFast(input, {wordCompose: true})), baseline(input));
  const invalidBefore = {...fixture(5), before: new Uint8ClampedArray(3)};
  assert.throws(() => composeHairArmsFast(invalidBefore, {wordCompose: true}), /accepted output itself is invalid/);
});

test('X leaves independent final optical/nose/outside/background checks able to reject later RGB and alpha corruption', () => {
  const input = fixture(9), result = composeHairArmsFast(input, {wordCompose: true});
  for (const [x, y, channel] of [[11, 8, 3], [0, 0, 0]] as const) {
    const offset = (y * input.width + x) * 4 + channel;
    result.pixels[offset] = (input.before[offset]! + 1) % 256;
  }
  const actual = checkHairProtection(input, result.pixels, result.regions);
  assert.deepEqual(actual.protectedCheck, comparePixels(input.before, result.pixels, input.width, input.height,
    (x, y) => input.protection.protectedRects.some(rect => inside(rect, x, y))));
  assert.deepEqual(actual.noseCheck, comparePixels(input.before, result.pixels, input.width, input.height, (x, y) => inside(input.noseRoi, x, y)));
  assert.equal(actual.protectedCheck.changedPixels, 1); assert.equal(actual.noseCheck.changedPixels, 1);
  assert.equal(actual.outsideEditableCheck.changedPixels, 1); assert.equal(actual.backgroundPreservationCheck.changedPixels, 1);
});

test('X keeps default byte mode, optional weights, scratch reuse/resize and independently owned retained output', () => {
  const scratch = new CompositionScratch(), input = fixture(12), first = composeHairArmsFast(input, {wordCompose: true, scratch, collectWeights: false});
  const saved = first.pixels.slice();
  for (const next of [fixture(15), fixture(14, 37, 23), fixture(14, 1280, 853)]) {
    const result = composeHairArmsFast(categoryOnly(next), {wordCompose: true, scratch, collectWeights: false, collectEligibleResidualIndices: true});
    assert.deepEqual(originalFields(result), baseline(categoryOnly(next), {collectWeights: false, collectEligibleResidualIndices: true}));
    assert.equal(result.weights.length, 0); assert.deepEqual(first.pixels, saved); assert.notEqual(first.pixels.buffer, result.pixels.buffer);
  }
  scratch.clear();
  for (const options of [{}, {wordCompose: false}]) {
    const result = composeHairArmsFast(input, options); assert.deepEqual(originalFields(result), baseline(input));
    assert.equal(result.wordComparisonRequested, false); assert.equal(result.wordComparisonUsed, false); assert.equal(result.wordComparedPixels, 0);
  }
});
