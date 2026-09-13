import test from 'node:test';
import assert from 'node:assert/strict';
import {composeHairArms, comparePixels, inside} from '../../hair-arm-preview/compose.ts';
import type {HairArmInput} from '../../hair-arm-preview/compose.ts';
import type {LiveHairArmInput} from '../../hair-live-preview/live-mask.ts';
import {composeHairArmsFast as baseline} from '../../performance-candidate/fast-compose.ts';
import {composeHairArmsReview, ReviewCompositionScratch} from './compose.ts';
import {checkHairProtection} from '../fast-compose.ts';
import {CompositionOutputPool} from './output-pool.ts';
import type {ReviewHairArmResult} from './compose.ts';

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

function originalFields(result: ReviewHairArmResult): ReturnType<typeof baseline> {
  const {outputLease: _lease, reviewCompose: _review, wordComparisonRequested: _requested, wordComparisonUsed: _used, wordComparedPixels: _count, ...original} = result;
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

test('Review composition aligned word scan equals independent frozen pixels, weights and every statistic across models and category shapes', () => {
  let edited = 0;
  for (let seed = 1; seed <= 48; seed++) {
    const input = fixture(seed);
    if (seed % 4 === 0) input.mask.category.fill(1);
    if (seed % 4 === 1) input.mask.category.fill(0);
    if (seed % 4 === 2) for (let index = 0; index < input.mask.category.length; index++) input.mask.category[index] = index % 2;
    const savedBefore = input.before.slice(), savedBackground = input.background.slice(), savedMask = input.mask.category.slice();
    const result = composeHairArmsReview(input, {collectEligibleResidualIndices: true});
    const {regions: _regions, eligibleResidualIndices: _indices, ...frozenFields} = originalFields(result);
    assert.deepEqual(frozenFields, composeHairArms(input));
    assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
    assert.deepEqual(originalFields(composeHairArmsReview(categoryOnly(input), {collectEligibleResidualIndices: true})), originalFields(result));
    assert.equal(result.wordComparisonRequested, true); assert.equal(result.wordComparisonUsed, true);
    assert.equal(result.wordComparedPixels, input.width * input.height); assert.equal(result.fallbackReason, null);
    edited += Number(result.statistics.changedPixels > 0);
    assert.notEqual(result.pixels.buffer, input.before.buffer);
    assert.deepEqual(input.before, savedBefore); assert.deepEqual(input.background, savedBackground); assert.deepEqual(input.mask.category, savedMask);
  }
  assert.ok(edited > 24, 'The independent comparisons must exercise actual hair edits.');
});

test('Review composition counts alpha-only residuals without replacing alpha or manufacturing a visible edit', () => {
  const input = fixture(1); input.before.set(input.background); input.mask.category.fill(1);
  const index = input.width + 1, offset = index * 4;
  input.before[offset + 3] = (input.background[offset + 3]! + 17) % 256;
  const result = composeHairArmsReview(input, {collectEligibleResidualIndices: true});
  assert.equal(result.fallbackReason, null); assert.equal(result.statistics.renderResidualPixels, 1);
  assert.equal(result.statistics.eligibleResidualPixels, 1); assert.equal(result.statistics.hairEligibleResidualPixels, 1);
  assert.equal(result.statistics.changedPixels, 0); assert.equal(result.weights[index], 0);
  assert.deepEqual(result.eligibleResidualIndices, Uint32Array.of(index)); assert.deepEqual(result.pixels, input.before);
  assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
});

test('Review composition respects aligned subarray offsets and falls back to bytes for either unaligned RGBA view', () => {
  for (const [beforeOffset, backgroundOffset] of [[4, 8], [0, 0], [1, 0], [0, 3], [1, 3]] as const) {
    const input = fixture(7);
    input.before = offsetCopy(input.before, beforeOffset); input.background = offsetCopy(input.background, backgroundOffset);
    const result = composeHairArmsReview(input, {collectEligibleResidualIndices: true});
    assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
    assert.equal(result.fallbackReason, null);
    const aligned = beforeOffset % 4 === 0 && backgroundOffset % 4 === 0;
    assert.equal(result.wordComparisonUsed, aligned); assert.equal(result.wordComparedPixels, aligned ? input.width * input.height : 0);
  }
  const input = fixture(7), shared = new Uint8ClampedArray(new SharedArrayBuffer(input.before.byteLength));
  shared.set(input.before); input.before = shared;
  const result = composeHairArmsReview(input, {});
  assert.deepEqual(originalFields(result), baseline(input)); assert.equal(result.wordComparisonUsed, false);
});

test('Review composition clean-reference rejection retains all-four-channel delta, accepted pixels, and actual completed scan telemetry', () => {
  for (const channel of [0, 1, 2, 3]) {
    const input = fixture(3); input.before[channel] = 100; input.background[channel] = 117;
    const result = composeHairArmsReview(input, {});
    assert.deepEqual(originalFields(result), baseline(input)); assert.deepEqual(result.pixels, input.before);
    assert.equal(result.fallbackReason, 'The clean native camera pass is not byte-exact outside the saved eyewear bounds.');
    assert.equal(result.backgroundReferenceCheck.changedPixels, 1); assert.equal(result.backgroundReferenceCheck.maxDelta, 17);
    assert.equal(result.statistics.changedPixels, 0); assert.equal(result.statistics.renderResidualPixels, 0);
    assert.equal(result.wordComparisonUsed, true); assert.equal(result.wordComparedPixels, input.width * input.height);
  }
});

test('Review composition validates pairing, categories, confidence and bounds before word work and keeps original rejection ordering', () => {
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
    const result = composeHairArmsReview(input, {});
    assert.deepEqual(originalFields(result), baseline(input)); assert.ok(result.fallbackReason);
    assert.deepEqual(result.pixels, input.before); assert.equal(result.wordComparisonUsed, false); assert.equal(result.wordComparedPixels, 0);
  }
  const input = categoryOnly(fixture(5)); input.mask.category[0] = 255;
  assert.deepEqual(originalFields(composeHairArmsReview(input, {})), baseline(input));
  const invalidBefore = {...fixture(5), before: new Uint8ClampedArray(3)};
  assert.throws(() => composeHairArmsReview(invalidBefore, {}), /accepted output itself is invalid/);
});

test('Review composition leaves independent final optical/nose/outside/background checks able to reject later RGB and alpha corruption', () => {
  const input = fixture(9), result = composeHairArmsReview(input, {});
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


function narrowFixture(seed: number, width = 64, height = 48): HairArmInput {
  const input = fixture(seed, width, height);
  input.protection = {...input.protection,
    editableRects: [{x0: 5, y0: 6, x1: 24, y1: 15}, {x0: 30, y0: 8, x1: 52, y1: 19}]};
  // Move all pixels outside the new region union back to their exact source.
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if ([...input.protection.editableRects, ...input.protection.protectedRects, input.noseRoi].some(rect => inside(rect, x, y))) continue;
    const offset = (y * width + x) * 4; input.before.set(input.background.subarray(offset, offset + 4), offset);
  }
  return input;
}

test('Restricted detailed scan retains complete residual/background audit, row gaps and mask-edge semantics', () => {
  for (let seed = 1; seed <= 32; seed++) {
    const input = narrowFixture(seed), result = composeHairArmsReview(input, {collectEligibleResidualIndices: true});
    assert.deepEqual(originalFields(result), baseline(input, {collectEligibleResidualIndices: true}));
    const stats = result.reviewCompose, pixels = input.width * input.height;
    assert.equal(stats.used, true); assert.equal(stats.scanRestrictionUsed, true);
    assert.equal(stats.residualScannedPixels, pixels);
    assert.equal(stats.compositionScannedPixels + stats.backgroundScannedPixels, pixels);
    assert.ok(stats.compositionScannedPixels < pixels / 3);
    assert.equal(stats.outputInitialCopyPixels, pixels); assert.equal(stats.finalAuditScannedPixels, 0);
    assert.equal(result.wordComparedPixels, pixels);
    for (const check of Object.values(checkHairProtection(input, result.pixels, result.regions))) assert.equal(check.changedPixels, 0);
    result.outputLease.release();
  }
});

test('An RGB or alpha mismatch in excluded rows or row gaps still rejects the clean camera reference', () => {
  for (const [x, y] of [[63, 47], [0, 10], [28, 17]] as const) for (const channel of [0, 1, 2, 3]) {
    const input = narrowFixture(12), offset = (y * input.width + x) * 4 + channel;
    input.before[offset] = 17; input.background[offset] = 33;
    const result = composeHairArmsReview(input);
    assert.deepEqual(originalFields(result), baseline(input)); assert.ok(result.fallbackReason);
    assert.equal(result.backgroundReferenceCheck.changedPixels, 1); assert.equal(result.backgroundReferenceCheck.maxDelta, 16);
    assert.equal(result.reviewCompose.residualScannedPixels, input.width * input.height);
    assert.deepEqual(result.pixels, input.before); result.outputLease.release();
  }
});

test('Retained published output and handoff copies survive reuse, resolution changes and scratch resize', () => {
  const outputPool = new CompositionOutputPool(), scratch = new ReviewCompositionScratch();
  const first = composeHairArmsReview(narrowFixture(12), {outputPool, scratch, collectWeights: false});
  const retained = first.pixels.slice(), handoff = first.outputLease.copyPixels();
  assert.equal(first.outputLease.copiedBytes, first.pixels.byteLength);
  assert.equal(first.reviewCompose.outputOwnershipCopyBytes, first.pixels.byteLength);
  assert.equal(structuredClone(first.reviewCompose).outputOwnershipCopyBytes, first.pixels.byteLength);
  let reused = 0;
  for (const input of [narrowFixture(14), narrowFixture(17), narrowFixture(18, 80, 52), narrowFixture(21, 80, 52), narrowFixture(25)]) {
    const next = composeHairArmsReview(input, {outputPool, scratch, collectWeights: false});
    assert.deepEqual(originalFields(next), baseline(input, {collectWeights: false}));
    assert.equal(next.weights.length, 0); assert.deepEqual(first.pixels, retained);
    assert.notEqual(next.pixels.buffer, first.pixels.buffer); assert.ok(outputPool.pooledBufferCount <= 2);
    reused += Number(next.reviewCompose.outputBufferReused); next.outputLease.release();
  }
  assert.equal(reused, 2); first.outputLease.release();
  const replacement = composeHairArmsReview(narrowFixture(32), {outputPool, scratch});
  assert.equal(replacement.reviewCompose.outputBufferReused, true); assert.equal(replacement.reviewCompose.outputBufferBytesAllocated, 0);
  assert.deepEqual(handoff, retained); assert.throws(() => first.outputLease.copyPixels(), /already released/);
  replacement.outputLease.release(); assert.equal(outputPool.retainedLeaseCount, 0);
});

test('Two retained buffers force a declared owned fallback and double release cannot revoke reuse', () => {
  const outputPool = new CompositionOutputPool();
  const first = composeHairArmsReview(narrowFixture(1), {outputPool}), second = composeHairArmsReview(narrowFixture(2), {outputPool});
  const firstSaved = first.pixels.slice(), secondSaved = second.pixels.slice();
  const extra = composeHairArmsReview(narrowFixture(3), {outputPool});
  assert.equal(outputPool.pooledBufferCount, 2); assert.equal(outputPool.retainedLeaseCount, 2);
  assert.match(extra.reviewCompose.outputPoolFallbackReason!, /Both pooled outputs are retained/);
  assert.equal(extra.reviewCompose.outputBufferBytesAllocated, extra.pixels.byteLength);
  assert.notEqual(extra.pixels.buffer, first.pixels.buffer); assert.notEqual(extra.pixels.buffer, second.pixels.buffer);
  assert.deepEqual(first.pixels, firstSaved); assert.deepEqual(second.pixels, secondSaved);
  first.outputLease.release();
  const replacement = composeHairArmsReview(narrowFixture(4), {outputPool});
  first.outputLease.release(); assert.equal(outputPool.retainedLeaseCount, 2);
  assert.equal(replacement.reviewCompose.outputBufferReused, true);
  second.outputLease.release(); extra.outputLease.release(); replacement.outputLease.release();
});

test('Cancellation, rejected input, clear/restart and disposal keep ownership bounded and retained bytes intact', () => {
  const outputPool = new CompositionOutputPool(), scratch = new ReviewCompositionScratch();
  const abort = new AbortController(); abort.abort();
  assert.throws(() => composeHairArmsReview(narrowFixture(1), {outputPool, scratch, signal: abort.signal}), {name: 'AbortError'});
  assert.equal(outputPool.retainedLeaseCount, 0); assert.equal(outputPool.pooledBufferCount, 0);
  const input = narrowFixture(5); input.mask.sourceSHA256 = '9'.repeat(64);
  const rejected = composeHairArmsReview(input, {outputPool, scratch});
  assert.ok(rejected.fallbackReason); assert.equal(rejected.reviewCompose.used, false);
  assert.deepEqual(rejected.pixels, input.before); rejected.outputLease.release();
  const held = composeHairArmsReview(narrowFixture(6), {outputPool, scratch}), saved = held.pixels.slice();
  outputPool.clear(); scratch.clear();
  const restarted = composeHairArmsReview(narrowFixture(7), {outputPool, scratch});
  assert.equal(restarted.reviewCompose.outputBufferReused, false); assert.equal(restarted.reviewCompose.spanStorageReused, false);
  held.outputLease.release(); assert.equal(outputPool.retainedLeaseCount, 1); assert.deepEqual(held.pixels, saved);
  const restartSaved = restarted.pixels.slice(); outputPool.dispose();
  assert.deepEqual(restarted.pixels, restartSaved); restarted.outputLease.release();
  assert.throws(() => composeHairArmsReview(narrowFixture(8), {outputPool}), /disposed/);
});
