import test from 'node:test';
import assert from 'node:assert/strict';
import {composeHairArmsFast, checkHairProtection, CompositionScratch} from '../runtime/experiments/performance-candidate/fast-compose.ts';
import type {FastHairArmResult, FastHairOptions} from '../runtime/experiments/performance-candidate/fast-compose.ts';
import {composeHairArms as frozenCompose, comparePixels, inside} from '../../hair-arm-preview/compose.ts';
import type {HairArmInput as FrozenInput} from '../../hair-arm-preview/compose.ts';
import type {LiveHairArmInput as Input} from '../runtime/experiments/hair-live-preview/live-mask.ts';
import {liveFrameIdentity} from '../runtime/frame-identity.ts';

function fixture(seed: number, width = 23, height = 17): Input {
  let state = seed;
  const random = (): number => {state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32;};
  const before = new Uint8ClampedArray(width * height * 4), background = before.slice();
  const labels = seed % 2 ? ['background', 'hair'] : ['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others'];
  const pair = {sourceIdentity: '1'.repeat(64), detectionIdentity: '2'.repeat(64), eyewearModel: seed % 2 ? 'tom-ford-clear' : 'amber-horizon'};
  const maskWidth = Math.max(1, Math.floor(width * [.4, 1, 1.7][seed % 3]!)), maskHeight = Math.max(1, Math.floor(height * .7));
  const category = new Uint8Array(maskWidth * maskHeight), confidence = new Float32Array(category.length);
  for (let index = 0; index < category.length; index++) {
    category[index] = random() < .75 ? 1 : Math.floor(random() * labels.length); confidence[index] = random();
  }
  const input: Input = {width, height, before, background, pair, geometryPair: {...pair},
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

function withoutMetrics(result: FastHairArmResult): Omit<FastHairArmResult, 'cpuComposition'> {
  const {cpuComposition: _metrics, ...rest} = result; return rest;
}

function parity(input: Input, options: FastHairOptions = {}): FastHairArmResult {
  const savedBefore = input.before.slice(), savedBackground = input.background.slice(), savedCategory = input.mask.category.slice();
  const old = composeHairArmsFast(input, {...options, cpuCompose: false});
  const next = composeHairArmsFast(input, {...options, cpuCompose: true});
  assert.deepEqual(withoutMetrics(next), old);
  assert.deepEqual(input.before, savedBefore); assert.deepEqual(input.background, savedBackground); assert.deepEqual(input.mask.category, savedCategory);
  assert.notEqual(next.pixels.buffer, input.before.buffer);
  assert.notEqual(next.pixels.buffer, input.background.buffer);
  assert.deepEqual(checkHairProtection(input, next.pixels, next.regions), checkHairProtection(input, old.pixels, old.regions));
  return next;
}

function frozenInput(input: Input): FrozenInput {
  const pair = (p: Input['pair']): FrozenInput['pair'] => ({sourceSHA256: p.sourceIdentity, detectionSHA256: p.detectionIdentity, eyewearModel: p.eyewearModel});
  assert.ok(input.mask.outputMode !== 'category-only');
  return {...input, pair: pair(input.pair), geometryPair: pair(input.geometryPair),
    mask: {...input.mask, sourceSHA256: input.mask.sourceIdentity, detectionSHA256: input.mask.detectionIdentity,
      confidence: input.mask.confidence!, confidenceSHA256: input.mask.confidenceSHA256!}};
}

function independentChecks(input: Input, pixels: Uint8ClampedArray): ReturnType<typeof checkHairProtection> {
  return {
    protectedCheck: comparePixels(input.before, pixels, input.width, input.height,
      (x, y) => input.protection.protectedRects.some(rect => inside(rect, x, y))),
    noseCheck: comparePixels(input.before, pixels, input.width, input.height, (x, y) => inside(input.noseRoi, x, y)),
    outsideEditableCheck: comparePixels(input.before, pixels, input.width, input.height,
      (x, y) => !input.protection.editableRects.some(rect => inside(rect, x, y))),
    backgroundPreservationCheck: comparePixels(input.before, pixels, input.width, input.height, (x, y) => {
      const offset = (y * input.width + x) * 4;
      return [0, 1, 2, 3].every(channel => input.before[offset + channel] === input.background[offset + channel]);
    }),
  };
}

test('round two is opt-in and the preceding path has no new performance metadata', () => {
  const input = fixture(1), result = composeHairArmsFast(input);
  assert.equal('cpuComposition' in result, false);
  assert.deepEqual(result, composeHairArmsFast(input, {cpuCompose: false}));
  const next = parity(input);
  assert.equal(next.cpuComposition?.used, true); assert.equal(next.cpuComposition?.wordComparisonUsed, true);
  assert.equal(next.cpuComposition?.auditedReferencePixels, input.width * input.height);
  assert.equal(next.cpuComposition?.backgroundOnlyPixels, next.backgroundReferenceCheck.testedPixels);
  assert.equal(next.cpuComposition?.finalAuditPixels, 0, 'composition does not claim the later renderer audit');
});

test('both model label sets and frame identities match frozen pixels, weights, all counts and continuity indices', () => {
  let changed = 0, full = 0, feathered = 0, protectedHair = 0;
  for (let seed = 1; seed <= 64; seed++) {
    const input = fixture(seed), result = parity(input, {collectEligibleResidualIndices: true});
    const {regions: _regions, eligibleResidualIndices: indices, ...actual} = withoutMetrics(result);
    assert.deepEqual(actual, frozenCompose(frozenInput(input)));
    assert.equal(indices!.length, result.statistics.eligibleResidualPixels);
    assert.deepEqual(checkHairProtection(input, result.pixels, result.regions), independentChecks(input, result.pixels));
    changed += result.statistics.changedPixels; full += result.statistics.fullReplacementPixels;
    feathered += result.statistics.featheredPixels; protectedHair += result.statistics.hairOverProtectedRenderResidualPixels;
  }
  assert.ok(changed > 1000 && full > 100 && feathered > 100 && protectedHair > 100);
});

test('empty, solid, thin disconnected and resampled hair retain all category and image-edge semantics', () => {
  for (let mode = 0; mode < 4; mode++) for (let seed = 1; seed <= 6; seed++) {
    const input = fixture(seed, 29, 19);
    for (let index = 0; index < input.mask.category.length; index++)
      input.mask.category[index] = mode === 0 ? 0 : mode === 1 ? 1 : mode === 2 ? index % 2 : Number(index < input.mask.width * 3);
    parity(input, {collectWeights: mode % 2 === 0, collectEligibleResidualIndices: true});
  }
});

test('half feather rounds every byte pair exactly and leaves all 65536 accepted alpha values intact', () => {
  const width = 512, height = 258, input = fixture(3, width, height);
  const category = new Uint8Array(width * height), confidence = new Float32Array(category.length).fill(.6);
  input.mask = {...input.mask, width, height, category, confidence, confidenceSHA256: '5'.repeat(64), outputMode: 'full'};
  input.protection = {...input.protection, protectedRects: [{x0: 0, y0: 0, x1: 2, y1: 1}, {x0: 3, y0: 0, x1: 5, y1: 1}],
    editableRects: [{x0: 0, y0: 1, x1: width, y1: height}]};
  input.noseRoi = {x0: 6, y0: 0, x1: 8, y1: 1};
  input.before.set(input.background);
  for (let a = 0; a < 256; a++) for (let b = 0; b < 256; b++) {
    const index = (b + 1) * width + a * 2, offset = index * 4; category[index] = 1;
    input.before.set([a, b, 255 - a, (a + b) % 256], offset);
    input.background.set([b, a, 255 - b, (a + b + 7) % 256], offset);
  }
  const result = parity(input, {collectEligibleResidualIndices: true});
  assert.equal(result.fallbackReason, null);
  for (let a = 0; a < 256; a++) for (let b = 0; b < 256; b++) {
    const offset = ((b + 1) * width + a * 2) * 4;
    assert.equal(result.pixels[offset], Math.round(a * .5 + b * .5));
    assert.equal(result.pixels[offset + 1], Math.round(b * .5 + a * .5));
    assert.equal(result.pixels[offset + 2], Math.round((255 - a) * .5 + (255 - b) * .5));
    assert.equal(result.pixels[offset + 3], input.before[offset + 3]);
  }
});

test('alpha-only residuals enter continuity but never count as changed RGB or receive a weight', () => {
  const input = fixture(1); input.mask.category.fill(1); input.before.set(input.background);
  for (let index = 0; index < input.width * input.height; index++) {
    if (index % input.width === 0) continue;
    input.before[index * 4 + 3] = (input.background[index * 4 + 3]! + 1) % 256;
  }
  const result = parity(input, {collectEligibleResidualIndices: true});
  assert.equal(result.fallbackReason, null); assert.ok(result.statistics.eligibleResidualPixels > 0);
  assert.equal(result.statistics.changedPixels, 0); assert.equal(result.statistics.fullReplacementPixels, 0);
  assert.equal(result.statistics.featheredPixels, 0); assert.ok(result.weights.every(value => value === 0));
  assert.equal(result.eligibleResidualIndices!.length, result.statistics.eligibleResidualPixels);
});

test('invalid pairing/model/category/confidence/guards/reference reject with the exact prior fallback', () => {
  const changes: ((input: Input) => void)[] = [
    input => {input.mask.sourceIdentity = '9'.repeat(64);}, input => {input.geometryPair.detectionIdentity = '9'.repeat(64);},
    input => {input.mask.modelSHA256 = '9'.repeat(64);}, input => {input.mask.categorySHA256 = 'bad';},
    input => {input.mask.hairIndex = 0;}, input => {input.mask.width++;},
    input => {input.mask.confidence![0] = NaN;}, input => {input.mask.confidence![0] = Infinity;},
    input => {input.mask.confidence![0] = -.001;}, input => {input.mask.confidence![0] = 1.001;},
    input => {input.mask.category[0] = 255;}, input => {input.mask.category[0] = 255; input.mask.confidence![1] = NaN;},
    input => {input.protection = {...input.protection, width: 100};},
    input => {input.noseRoi = {x0: -1, y0: 0, x1: 2, y1: 2};},
    input => {input.protection = {...input.protection, protectedRects: []};},
    input => {input.protection = {...input.protection, editableRects: [{x0: 0, y0: 0, x1: input.width, y1: input.height}]};},
    input => {input.background[0] = (input.before[0]! + 1) % 256;},
    input => {input.background[3] = (input.before[3]! + 201) % 256;},
    input => {input.background = new Uint8ClampedArray(4);},
  ];
  for (const mutate of changes) {
    const input = fixture(9); mutate(input);
    const old = composeHairArmsFast(input), next = composeHairArmsFast(input, {cpuCompose: true});
    assert.deepEqual(withoutMetrics(next), old); assert.ok(next.fallbackReason); assert.deepEqual(next.pixels, input.before);
    assert.equal(next.statistics.changedPixels, 0); assert.ok(next.weights.every(value => value === 0));
  }
  for (const input of [{...fixture(1), width: NaN}, {...fixture(1), height: -1}, {...fixture(1), before: new Uint8ClampedArray(4)}]) {
    assert.throws(() => composeHairArmsFast(input, {cpuCompose: true}), /accepted output itself is invalid/);
    assert.throws(() => composeHairArmsFast(input), /accepted output itself is invalid/);
  }
});

test('reference audit counts all changed background pixels and maximum RGBA error without early exit', () => {
  const input = fixture(6);
  for (let index = 0; index < 3; index++) {
    const offset = index * input.width * 4; input.before[offset] = 0; input.background[offset] = [1, 91, 255][index]!;
  }
  const result = parity(input);
  assert.ok(result.fallbackReason); assert.equal(result.backgroundReferenceCheck.changedPixels, 3);
  assert.equal(result.backgroundReferenceCheck.maxDelta, 255);
  assert.equal(result.cpuComposition?.auditedReferencePixels, input.width * input.height);
});

test('unaligned before or camera views retain exact byte fallback without changing borrowed bytes', () => {
  for (let shift = 1; shift <= 3; shift++) for (const field of ['before', 'background'] as const) {
    const input = fixture(shift), bytes = input[field];
    const backing = new Uint8ClampedArray(bytes.length + 8).fill(97); backing.set(bytes, shift);
    input[field] = backing.subarray(shift, shift + bytes.length);
    const result = parity(input, {collectEligibleResidualIndices: true});
    assert.equal(result.fallbackReason, null); assert.equal(result.cpuComposition?.used, false);
    assert.equal(result.cpuComposition?.wordComparisonUsed, false); assert.equal(backing[0], 97);
    assert.equal(backing[backing.length - 1], 97);
    assert.equal(result.cpuComposition?.rgbWritePixels, 0, 'the optimized branch did no work');
    input.background[0] = input.background[0]! ^ 255;
    const rejected = parity(input);
    assert.ok(rejected.fallbackReason);
    assert.equal(rejected.cpuComposition?.used, false);
    assert.equal(rejected.cpuComposition?.auditedReferencePixels, 0);
    assert.equal(rejected.cpuComposition?.rgbWritePixels, 0);
  }
});

test('private scratch handles changed regions, masks and dimensions while each published output remains owned', () => {
  const scratch = new CompositionScratch();
  const first = parity(fixture(1), {scratch, collectWeights: false, collectEligibleResidualIndices: true}), saved = first.pixels.slice();
  const second = fixture(2); second.before.set(second.background);
  second.protection = {...second.protection, protectedRects: [{x0: 0, y0: 0, x1: 3, y1: 2}, {x0: 10, y0: 10, x1: 13, y1: 14}]};
  parity(second, {scratch, collectWeights: false, collectEligibleResidualIndices: true});
  assert.deepEqual(first.pixels, saved); assert.equal(scratch.lastAllocation.regionsReused, true);
  parity(fixture(3, 41, 21), {scratch, collectWeights: false});
  assert.deepEqual(first.pixels, saved); scratch.clear();
  parity(fixture(4), {scratch}); assert.deepEqual(first.pixels, saved);
});

test('every post-compose pixel/channel corruption is audited against an independent four-check oracle', () => {
  const input = fixture(4), result = composeHairArmsFast(input, {cpuCompose: true});
  assert.equal(result.fallbackReason, null);
  for (let offset = 0; offset < result.pixels.length; offset++) {
    const after = result.pixels.slice(); after[offset] = (input.before[offset]! + 113) % 256;
    assert.deepEqual(checkHairProtection(input, after, result.regions), independentChecks(input, after));
  }
  // Re-evaluate current background equality; an earlier composition reference
  // scan must not substitute for this independent audit of actual final data.
  input.background.set(input.before);
  assert.deepEqual(checkHairProtection(input, result.pixels, result.regions), independentChecks(input, result.pixels));
  assert.equal(checkHairProtection(input, result.pixels, result.regions).backgroundPreservationCheck.testedPixels, input.width * input.height);
});

test('overlapping optical/nasal/corridor corruption keeps independent counts and maximum deltas', () => {
  const input = fixture(3), result = composeHairArmsFast(input, {cpuCompose: true});
  const offset = (6 * input.width + 6) * 4; result.pixels[offset] = 0; input.before[offset] = 255;
  const checks = checkHairProtection(input, result.pixels, result.regions);
  assert.deepEqual(checks, independentChecks(input, result.pixels));
  assert.equal(checks.protectedCheck.changedPixels, 1); assert.equal(checks.noseCheck.changedPixels, 1);
  assert.equal(checks.protectedCheck.maxDelta, 255); assert.equal(checks.noseCheck.maxDelta, 255);
});

test('existing category-only contract and valid session-frame identities retain exact output and strict pairing', () => {
  const input = fixture(4), full = parity(input);
  const {confidence: _confidence, confidenceSHA256: _hash, ...mask} = input.mask;
  input.mask = {...mask, outputMode: 'category-only'};
  assert.deepEqual(parity(input).pixels, full.pixels); assert.equal('confidence' in input.mask, false);
  input.pair = {sourceIdentity: liveFrameIdentity('source', 'round-two-session', 7),
    detectionIdentity: liveFrameIdentity('detection', 'round-two-session', 7), eyewearModel: input.pair.eyewearModel};
  input.geometryPair = {...input.pair}; input.mask = {...input.mask, ...input.pair};
  assert.deepEqual(parity(input).pixels, full.pixels);
  input.mask.detectionIdentity = liveFrameIdentity('detection', 'round-two-session', 8);
  assert.ok(parity(input).fallbackReason);
});
