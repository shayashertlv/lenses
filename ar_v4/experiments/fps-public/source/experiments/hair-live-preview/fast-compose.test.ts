import test from 'node:test';
import assert from 'node:assert/strict';
import {composeHairArms, comparePixels, inside} from '../hair-arm-preview/compose.ts';
import type {HairArmInput} from '../hair-arm-preview/compose.ts';
import {composeHairArmsFast, checkHairProtection} from './fast-compose.ts';
import type {LiveHairArmInput} from './live-mask.ts';

function fixture(seed: number, width = 23, height = 17): HairArmInput {
  let state = seed;
  const random = (): number => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
  const before = new Uint8ClampedArray(width * height * 4), background = before.slice();
  const labels = seed % 2 ? ['background', 'hair'] : ['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others'];
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: seed % 2 ? 'tom-ford-clear' : 'amber-horizon'};
  const maskWidth = Math.max(1, Math.floor(width * ([.4, 1, 1.7][seed % 3]!))), maskHeight = Math.max(1, Math.floor(height * .7));
  const category = new Uint8Array(maskWidth * maskHeight), confidence = new Float32Array(category.length);
  for (let index = 0; index < category.length; index++) {
    category[index] = random() < .75 ? 1 : Math.floor(random() * labels.length); confidence[index] = random();
  }
  const input: HairArmInput = {width, height, before, background, pair, geometryPair: {...pair},
    expectedModel: {id: 'test-pinned-model', sha256: '3'.repeat(64), labels, hairIndex: 1},
    mask: {...pair, model: 'test-pinned-model', modelSHA256: '3'.repeat(64), categorySHA256: '4'.repeat(64), confidenceSHA256: '5'.repeat(64),
      labels, hairIndex: 1, width: maskWidth, height: maskHeight, category, confidence},
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 2, y0: 2, x1: width - 5, y1: 5}, {x0: 5, y0: 4, x1: 8, y1: 9}],
      editableRects: [{x0: 1, y0: 0, x1: width, y1: height}, {x0: 0, y0: 3, x1: width - 2, y1: height - 1}]},
    noseRoi: {x0: 5, y0: 5, x1: 9, y1: 10}};
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const offset = (y * width + x) * 4;
    const bounded = input.protection.protectedRects.some(rect => inside(rect, x, y))
      || input.protection.editableRects.some(rect => inside(rect, x, y)) || inside(input.noseRoi, x, y);
    for (let channel = 0; channel < 4; channel++) {
      const value = Math.floor(random() * 256); background[offset + channel] = before[offset + channel] = value;
      if (bounded && random() < .55) before[offset + channel] = Math.floor(random() * 256);
    }
  }
  return input;
}

function equalFrozen(input: HairArmInput): void {
  const savedBefore = input.before.slice(), savedMask = input.mask.category.slice();
  const frozen = composeHairArms(input), {regions, eligibleResidualIndices, ...fast} = composeHairArmsFast(input, {collectEligibleResidualIndices: true});
  assert.deepEqual(fast, frozen);
  assert.notEqual(fast.pixels, input.before);
  assert.deepEqual(input.before, savedBefore); assert.deepEqual(input.mask.category, savedMask);
  if (!fast.fallbackReason) {
    assert.equal(eligibleResidualIndices!.length, fast.statistics.eligibleResidualPixels);
    assert.equal(new Set(eligibleResidualIndices).size, eligibleResidualIndices!.length);
    for (const index of eligibleResidualIndices!) {
      const x = index % input.width, y = Math.floor(index / input.width), offset = index * 4;
      assert.ok(!inside(input.noseRoi, x, y) && !input.protection.protectedRects.some(rect => inside(rect, x, y))
        && input.protection.editableRects.some(rect => inside(rect, x, y)));
      assert.ok([0, 1, 2, 3].some(channel => input.before[offset + channel] !== input.background[offset + channel]));
    }
    const actual = checkHairProtection(input, fast.pixels, regions);
    const expected = {
      protectedCheck: comparePixels(input.before, fast.pixels, input.width, input.height,
        (x, y) => input.protection.protectedRects.some(rect => inside(rect, x, y))),
      noseCheck: comparePixels(input.before, fast.pixels, input.width, input.height, (x, y) => inside(input.noseRoi, x, y)),
      outsideEditableCheck: comparePixels(input.before, fast.pixels, input.width, input.height,
        (x, y) => !input.protection.editableRects.some(rect => inside(rect, x, y))),
      backgroundPreservationCheck: comparePixels(input.before, fast.pixels, input.width, input.height, (x, y) => {
        const offset = (y * input.width + x) * 4;
        return [0, 1, 2, 3].every(channel => input.before[offset + channel] === input.background[offset + channel]);
      }),
    };
    assert.deepEqual(actual, expected);
  }
}

test('fast full-resolution output, feather weights, all statistics and actual guard audits equal frozen compositor', () => {
  for (let seed = 1; seed <= 96; seed++) equalFrozen(fixture(seed));
});

test('empty, solid, fine disconnected and asymmetrically resampled categories keep frozen edge semantics', () => {
  for (let mode = 0; mode < 4; mode++) for (let seed = 1; seed <= 6; seed++) {
    const input = fixture(seed, 29, 19);
    for (let index = 0; index < input.mask.category.length; index++)
      input.mask.category[index] = mode === 0 ? 0 : mode === 1 ? 1 : mode === 2 ? index % 2 : Number(index < input.mask.width * 3);
    equalFrozen(input);
  }
});

test('native 1280px output with noninteger source-mask scaling is byte-exact at all image and protection edges', () => {
  equalFrozen(fixture(14, 1280, 853));
});

test('explicit category-only masks retain identical pixels and checks without manufacturing confidence', () => {
  for (let seed = 1; seed <= 24; seed++) {
    const input = fixture(seed);
    const {confidence: _confidence, confidenceSHA256: _hash, ...category} = input.mask;
    const lean: LiveHairArmInput = {...input, mask: {...category, outputMode: 'category-only'}};
    assert.deepEqual(composeHairArmsFast(lean), composeHairArmsFast(input));
    assert.equal('confidence' in lean.mask, false);
    assert.ok(composeHairArmsFast({...lean, mask: {...lean.mask, categorySHA256: 'bad'}}).fallbackReason);
    assert.ok(composeHairArmsFast({...lean, mask: {...lean.mask, sourceSHA256: '9'.repeat(64)}}).fallbackReason);
    assert.ok(composeHairArmsFast({...lean, mask: {...lean.mask, confidence: new Float32Array(category.category.length)} as unknown as LiveHairArmInput['mask']}).fallbackReason);
  }
});

test('invalid identity, dimensions, labels, category/confidence, guards and camera reference fail closed identically', () => {
  const mutations: ((input: HairArmInput) => void)[] = [
    input => { input.mask.sourceSHA256 = '9'.repeat(64); },
    input => { input.geometryPair.detectionSHA256 = '9'.repeat(64); },
    input => { input.mask.modelSHA256 = '9'.repeat(64); },
    input => { input.mask.hairIndex = 0; }, input => { input.mask.width++; },
    input => { input.mask.confidence[0] = NaN; }, input => { input.mask.confidence[0] = Infinity; },
    input => { input.mask.confidence[0] = -.001; }, input => { input.mask.category[0] = 255; },
    input => { input.mask.category[0] = 255; input.mask.confidence[1] = NaN; },
    input => { input.protection = {...input.protection, width: 100}; },
    input => { input.noseRoi = {x0: -1, y0: 0, x1: 2, y1: 2}; },
    input => { input.background[0] = (input.before[0]! + 1) % 256; },
    input => { input.protection = {...input.protection, editableRects: []}; },
    input => { input.protection = {...input.protection, editableRects: [{x0: 0, y0: 0, x1: input.width, y1: input.height}]}; },
  ];
  for (const mutate of mutations) { const input = fixture(9); mutate(input); equalFrozen(input); }
});

test('consolidated audit detects an actual later guard violation instead of assuming zero by construction', () => {
  const input = fixture(3), result = composeHairArmsFast(input);
  const offset = (6 * input.width + 6) * 4;
  result.pixels[offset] = (input.before[offset]! + 1) % 256;
  const checks = checkHairProtection(input, result.pixels, result.regions);
  assert.equal(checks.protectedCheck.changedPixels, 1); assert.equal(checks.noseCheck.changedPixels, 1);
});
