/** The audit's compose must equal the original full-frame reference (a distance-field feather with per-pixel rectangle
 *  tests), which is kept here as the oracle. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {checkHairProtection, composeHairArms} from '../src/audit/reference.ts';
import type {HairArmInput, HairArmResult, PixelCheck} from '../src/audit/reference.ts';
import type {PixelRect} from '../src/render/protection.ts';
import {validateProtection} from '../src/render/protection.ts';

const inside = (rect: PixelRect, x: number, y: number): boolean => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;
const differs = (a: Uint8ClampedArray, b: Uint8ClampedArray, offset: number): boolean =>
  a[offset] !== b[offset] || a[offset + 1] !== b[offset + 1] || a[offset + 2] !== b[offset + 2] || a[offset + 3] !== b[offset + 3];
function comparePixels(before: Uint8ClampedArray, after: Uint8ClampedArray, width: number, height: number, include: (x: number, y: number) => boolean): PixelCheck {
  const result: PixelCheck = {testedPixels: 0, changedPixels: 0, maxDelta: 0};
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (!include(x, y)) continue; result.testedPixels++;
    const offset = (y * width + x) * 4; let maxDelta = 0;
    for (let channel = 0; channel < 4; channel++) maxDelta = Math.max(maxDelta, Math.abs(before[offset + channel]! - after[offset + channel]!));
    if (maxDelta) result.changedPixels++; result.maxDelta = Math.max(result.maxDelta, maxDelta);
  }
  return result;
}
/** The original inward feather: a clipped Manhattan distance transform over the resampled category mask. */
function inwardWeights(category: Uint8Array, maskWidth: number, maskHeight: number, hairIndex: number, width: number, height: number): Float32Array {
  const distance = new Uint16Array(width * height), weights = new Float32Array(width * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const mx = Math.min(maskWidth - 1, Math.floor((x + .5) * maskWidth / width));
    const my = Math.min(maskHeight - 1, Math.floor((y + .5) * maskHeight / height));
    if (category[my * maskWidth + mx] !== hairIndex) continue;
    const index = y * width + x;
    const left = x ? distance[index - 1]! : 0, above = y ? distance[index - width]! : 0;
    distance[index] = Math.min(3, Math.min(left, above) + 1);
  }
  for (let y = height - 1; y >= 0; y--) for (let x = width - 1; x >= 0; x--) {
    const index = y * width + x; if (!distance[index]) continue;
    const right = x < width - 1 ? distance[index + 1]! : 0, below = y < height - 1 ? distance[index + width]! : 0;
    distance[index] = Math.min(distance[index]!, Math.min(right, below) + 1);
    weights[index] = Math.min(1, distance[index]! / 2);
  }
  return weights;
}
type Oracle = Omit<HairArmResult, 'regions'>;
function oracle(input: HairArmInput): Oracle {
  const {width, height, before, background} = input;
  const result: Oracle = {pixels: before.slice(), weights: new Float32Array(width * height), fallbackReason: null,
    backgroundReferenceCheck: {testedPixels: 0, changedPixels: 0, maxDelta: 0},
    statistics: {changedPixels: 0, renderResidualPixels: 0, eligiblePixels: 0, eligibleResidualPixels: 0,
      hairEligibleResidualPixels: 0, fullReplacementPixels: 0, featheredPixels: 0,
      protectedCorridorHairResidualPixels: 0, hairOverProtectedRenderResidualPixels: 0}};
  try {
    const {mask, expectedModel} = input;
    const check = (value: unknown, message: string): void => {if (!value) throw new Error(message);};
    const isHash = (value: unknown): boolean => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
    check(background.length === before.length, 'Clean camera output dimensions differ.');
    for (const pair of [input.pair, input.geometryPair]) check(isHash(pair.sourceSHA256) && isHash(pair.detectionSHA256)
      && ['tom-ford-clear', 'amber-horizon', 'modeling-auto'].includes(pair.eyewearModel), 'The image/geometry identity is invalid.');
    check(input.pair.sourceSHA256 === input.geometryPair.sourceSHA256 && input.pair.detectionSHA256 === input.geometryPair.detectionSHA256
      && input.pair.eyewearModel === input.geometryPair.eyewearModel, 'pair');
    check(mask.sourceSHA256 === input.pair.sourceSHA256 && mask.detectionSHA256 === input.pair.detectionSHA256, 'mask pair');
    check(isHash(expectedModel.sha256) && mask.model === expectedModel.id && mask.modelSHA256 === expectedModel.sha256 && isHash(mask.categorySHA256), 'model');
    check(JSON.stringify(mask.labels) === JSON.stringify(expectedModel.labels) && mask.hairIndex === expectedModel.hairIndex
      && mask.labels[mask.hairIndex]?.toLowerCase().includes('hair'), 'labels');
    check(Number.isInteger(mask.width) && Number.isInteger(mask.height) && mask.width > 0 && mask.height > 0
      && mask.category instanceof Uint8Array && mask.category.length === mask.width * mask.height, 'storage');
    check(mask.category.every(value => value < mask.labels.length), 'category');
    validateProtection(input.protection, width, height);
    check([input.noseRoi.x0, input.noseRoi.y0, input.noseRoi.x1, input.noseRoi.y1].every(Number.isInteger) && input.noseRoi.x0 >= 0 && input.noseRoi.y0 >= 0
      && input.noseRoi.x1 <= width && input.noseRoi.y1 <= height && input.noseRoi.x1 > input.noseRoi.x0 && input.noseRoi.y1 > input.noseRoi.y0, 'nose');
    const protects = (x: number, y: number): boolean => inside(input.noseRoi, x, y) || input.protection.protectedRects.some(rect => inside(rect, x, y));
    const corridor = (x: number, y: number): boolean => input.protection.editableRects.some(rect => inside(rect, x, y));
    result.backgroundReferenceCheck = comparePixels(before, background, width, height, (x, y) => !protects(x, y) && !corridor(x, y));
    check(result.backgroundReferenceCheck.testedPixels > 0 && result.backgroundReferenceCheck.changedPixels === 0, 'background');
    const weights = inwardWeights(mask.category, mask.width, mask.height, mask.hairIndex, width, height);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const index = y * width + x, offset = index * 4, protectedPixel = protects(x, y), inCorridor = corridor(x, y);
      const residual = differs(before, background, offset), hair = weights[index]! > 0;
      result.statistics.renderResidualPixels += Number(residual);
      if (protectedPixel) {
        result.statistics.hairOverProtectedRenderResidualPixels += Number(residual && hair);
        result.statistics.protectedCorridorHairResidualPixels += Number(residual && hair && inCorridor); continue;
      }
      if (!inCorridor) continue; result.statistics.eligiblePixels++;
      if (!residual) continue; result.statistics.eligibleResidualPixels++;
      if (!hair) continue; result.statistics.hairEligibleResidualPixels++;
      const weight = weights[index]!;
      for (let channel = 0; channel < 3; channel++) result.pixels[offset + channel] = Math.round(before[offset + channel]! * (1 - weight) + background[offset + channel]! * weight);
      result.pixels[offset + 3] = before[offset + 3]!;
      if (!differs(before, result.pixels, offset)) continue;
      result.weights[index] = weight; result.statistics.changedPixels++;
      if (weight === 1) result.statistics.fullReplacementPixels++; else result.statistics.featheredPixels++;
    }
    for (const rect of [...input.protection.protectedRects, input.noseRoi]) for (let y = rect.y0; y < rect.y1; y++) {
      const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
      result.pixels.set(before.subarray(start, end), start);
    }
  } catch (error) { result.pixels.set(before); result.weights.fill(0); result.statistics.changedPixels = 0;
    result.fallbackReason = error instanceof Error ? error.message : String(error); }
  return result;
}

function fixture(seed: number, width = 23, height = 17): HairArmInput {
  let state = seed;
  const random = (): number => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
  const before = new Uint8ClampedArray(width * height * 4), background = before.slice();
  const labels = seed % 2 ? ['background', 'hair'] : ['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others'];
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: seed % 2 ? 'tom-ford-clear' : 'amber-horizon'};
  const maskWidth = Math.max(1, Math.floor(width * ([.4, 1, 1.7][seed % 3]!))), maskHeight = Math.max(1, Math.floor(height * .7));
  const category = new Uint8Array(maskWidth * maskHeight);
  for (let index = 0; index < category.length; index++) category[index] = random() < .75 ? 1 : Math.floor(random() * labels.length);
  const input: HairArmInput = {width, height, before, background, pair, geometryPair: {...pair},
    expectedModel: {id: 'hair-only', sha256: '3'.repeat(64), labels, hairIndex: 1},
    mask: {...pair, sequence: seed, model: 'hair-only', modelSHA256: '3'.repeat(64), categorySHA256: '4'.repeat(64), delegate: 'CPU',
      labels, hairIndex: 1, width: maskWidth, height: maskHeight, category, inferenceMs: 1, extractionMs: 0},
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

function equalOracle(input: HairArmInput): void {
  const savedBefore = input.before.slice(), savedMask = input.mask.category.slice();
  const expected = oracle(input), {regions, ...fast} = composeHairArms(input);
  assert.deepEqual(fast.pixels, expected.pixels); assert.deepEqual(fast.weights, expected.weights);
  assert.equal(fast.fallbackReason === null, expected.fallbackReason === null);
  assert.deepEqual(fast.backgroundReferenceCheck, expected.backgroundReferenceCheck);
  assert.deepEqual(fast.statistics, expected.statistics);
  assert.notEqual(fast.pixels, input.before);
  assert.deepEqual(input.before, savedBefore); assert.deepEqual(input.mask.category, savedMask);
  if (!fast.fallbackReason) {
    const actual = checkHairProtection(input, fast.pixels, regions);
    const checks = {
      protectedCheck: comparePixels(input.before, fast.pixels, input.width, input.height, (x, y) => input.protection.protectedRects.some(rect => inside(rect, x, y))),
      noseCheck: comparePixels(input.before, fast.pixels, input.width, input.height, (x, y) => inside(input.noseRoi, x, y)),
      outsideEditableCheck: comparePixels(input.before, fast.pixels, input.width, input.height, (x, y) => !input.protection.editableRects.some(rect => inside(rect, x, y))),
      backgroundPreservationCheck: comparePixels(input.before, fast.pixels, input.width, input.height, (x, y) => {
        const offset = (y * input.width + x) * 4;
        return [0, 1, 2, 3].every(channel => input.before[offset + channel] === input.background[offset + channel]);
      }),
    };
    assert.deepEqual(actual, checks);
  }
}

test('the compose, feather weights, statistics and guard audits equal the full-frame oracle', () => {
  for (let seed = 1; seed <= 96; seed++) equalOracle(fixture(seed));
});

test('empty, solid, fine disconnected and asymmetrically resampled categories keep the edge semantics', () => {
  for (let mode = 0; mode < 4; mode++) for (let seed = 1; seed <= 6; seed++) {
    const input = fixture(seed, 29, 19);
    for (let index = 0; index < input.mask.category.length; index++)
      input.mask.category[index] = mode === 0 ? 0 : mode === 1 ? 1 : mode === 2 ? index % 2 : Number(index < input.mask.width * 3);
    equalOracle(input);
  }
});

test('1280px output with noninteger source-mask scaling is byte-exact at all image and protection edges', () => {
  equalOracle(fixture(14, 1280, 853));
});

test('invalid identity, dimensions, labels, category, guards and camera reference fail closed identically', () => {
  const mutations: ((input: HairArmInput) => void)[] = [
    input => { input.mask.sourceSHA256 = '9'.repeat(64); },
    input => { input.geometryPair.detectionSHA256 = '9'.repeat(64); },
    input => { input.mask.modelSHA256 = '9'.repeat(64); },
    input => { input.mask.hairIndex = 0; }, input => { input.mask.width++; },
    input => { input.mask.category[0] = 255; },
    input => { input.pair.eyewearModel = 'unknown-frame'; input.geometryPair.eyewearModel = 'unknown-frame'; },
    input => { input.protection = {...input.protection, width: 100}; },
    input => { input.noseRoi = {x0: -1, y0: 0, x1: 2, y1: 2}; },
    input => { input.background[0] = (input.before[0]! + 1) % 256; },
    input => { input.protection = {...input.protection, editableRects: []}; },
    input => { input.protection = {...input.protection, editableRects: [{x0: 0, y0: 0, x1: input.width, y1: input.height}]}; },
  ];
  for (const mutate of mutations) { const input = fixture(9); mutate(input); equalOracle(input); }
});

test('the audit detects an actual later guard violation instead of assuming zero by construction', () => {
  const input = fixture(3), result = composeHairArms(input);
  const offset = (6 * input.width + 6) * 4;
  result.pixels[offset] = (input.before[offset]! + 1) % 256;
  const checks = checkHairProtection(input, result.pixels, result.regions);
  assert.equal(checks.protectedCheck.changedPixels, 1); assert.equal(checks.noseCheck.changedPixels, 1);
});

test('omitting the diagnostic weights preserves every final pixel, statistic and guard result', () => {
  for (let seed = 1; seed <= 48; seed++) {
    const input = fixture(seed);
    const {weights: _weights, ...reference} = composeHairArms(input);
    const {weights, ...live} = composeHairArms(input, {collectWeights: false});
    assert.equal(weights.byteLength, 0);
    assert.deepEqual(live, reference);
    assert.deepEqual(checkHairProtection(input, live.pixels, live.regions), checkHairProtection(input, reference.pixels, reference.regions));
  }
});
