import assert from 'node:assert/strict';
import {test} from 'node:test';
import {validateHairCostRequest, validateCategoryExtractionMetrics, validateHairWorkerTiming} from './protocol.ts';
import type {HairExpectedPair} from './protocol.ts';
import {extractCategoryMask} from './extraction.ts';
import type {CategoryMaskSource} from './extraction.ts';

test('optional worker timing is copied and rejects nonfinite, negative or overlapping claimed durations', () => {
  assert.equal(validateHairWorkerTiming(undefined, 5, 3), undefined);
  const input = {inputValidationMs: .25, totalMs: 8.5, privateWorkerField: 'not retained'};
  const measured = validateHairWorkerTiming(input, 5, 3); input.totalMs = 100;
  assert.deepEqual(measured, {inputValidationMs: .25, totalMs: 8.5});
  for (const malformed of [null, {}, {inputValidationMs: -1, totalMs: 10},
    {inputValidationMs: Infinity, totalMs: 10}, {inputValidationMs: 0, totalMs: NaN},
    {inputValidationMs: 0, totalMs: -1}, {inputValidationMs: .5, totalMs: 8.4}])
    assert.throws(() => validateHairWorkerTiming(malformed, 5, 3), /timing span/);
});

test('request extension freezes extraction selection without bypassing original nonce, image, shape, hash or sequence checks', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'ImageBitmap');
  class Bitmap {width = 2; height = 2; close() {}}
  Object.defineProperty(globalThis, 'ImageBitmap', {value: Bitmap, configurable: true});
  try {
    const raw = {type: 'segment', image: new Bitmap(), sessionNonce: 'owned-session', requestId: 2,
      sourceSHA256: 'a'.repeat(64), sequence: 0, categoryExtractionMode: 'direct'};
    const valid = validateHairCostRequest(raw); raw.categoryExtractionMode = 'sdk';
    assert.equal(valid.type, 'segment'); assert.equal(valid.categoryExtractionMode, 'direct'); assert.equal(valid.image, raw.image);
    for (const patch of [{categoryExtractionMode: undefined}, {categoryExtractionMode: 'faster'}, {sessionNonce: 'bad'},
      {requestId: -1}, {sourceSHA256: 'invalid'}, {sequence: -.5}, {image: {width: 2, height: 2}},
      {image: Object.assign(new Bitmap(), {width: 0})}]) assert.throws(() => validateHairCostRequest({...raw, ...patch}));
    const initialize = validateHairCostRequest({type: 'initialize', sessionNonce: 'owned-session', requestId: 1, modelId: 'hair-only'});
    assert.equal(initialize.type, 'initialize'); assert.equal(initialize.delegate, 'CPU'); assert.equal(initialize.outputMode, 'full');
    assert.throws(() => validateHairCostRequest({...initialize, modelId: 'unreviewed'}), /pinned/);
  } finally {
    if (previous) Object.defineProperty(globalThis, 'ImageBitmap', previous); else Reflect.deleteProperty(globalThis, 'ImageBitmap');
  }
});

test('real helper metrics pass the boundary for every available representation and both requested modes', () => {
  for (const mode of ['sdk', 'direct'] as const) for (const representation of ['bytes', 'float', 'texture', 'all'] as const) {
    const source: CategoryMaskSource = {width: 2, height: 2,
      hasUint8Array: () => representation === 'bytes' || representation === 'all',
      hasFloat32Array: () => representation === 'float' || representation === 'all',
      hasWebGLTexture: () => representation === 'texture' || representation === 'all',
      getAsUint8Array: () => new Uint8Array([0, 1, 1, 0]), getAsFloat32Array: () => new Float32Array([0, 1 / 255, 1 / 255, 0])};
    const began = performance.now(), output = extractCategoryMask(source, mode), elapsed = performance.now() - began;
    const expected: HairExpectedPair = {width: 2, height: 2, sourceSHA256: 'a'.repeat(64), sequence: 0, categoryExtractionMode: mode};
    const validated = validateCategoryExtractionMetrics(output.metrics, expected, elapsed);
    assert.deepEqual(validated, output.metrics); assert.notEqual(validated, output.metrics);
    assert.deepEqual([...output.category], [0, 1, 1, 0]);
    if (mode === 'direct' && (representation === 'float' || representation === 'texture'))
      assert.equal(validated.explicitFloatTemporaryBytesAvoided, 16);
    else assert.equal(validated.explicitFloatTemporaryBytesAvoided, 0);
  }
});

test('RGBA8 metrics require actual byte retrieval, full original dimensions and honest fallback disposition', () => {
  const expected: HairExpectedPair = {width: 2, height: 2, sourceSHA256: 'a'.repeat(64), sequence: 0,
    outputMode: 'category-only', categoryExtractionMode: 'rgba8'};
  const metrics = {mode: 'rgba8', path: 'rgba8-readback', hasUint8: false, hasFloat32: false, hasWebGLTexture: true,
    retrievalMs: 1, conversionMs: .2, copyMs: 0, totalMs: 1.5, maskPixels: 4, ownedCategoryBytesAllocated: 4,
    retrievedArrayBytes: 16, categoryBytesCopied: 0, categoryBytesConverted: 4,
    explicitFloatTemporaryBytesAvoided: 16, explicitCategoryCopyBytesAvoided: 4,
    rgba8WorkMs: 1.4, rgba8ReadbackBytes: 16, rgba8ResourcesReused: true, rgba8FallbackReason: null};
  assert.deepEqual(validateCategoryExtractionMetrics(metrics, expected, 2), metrics);
  for (const patch of [{path: 'sdk-copy'}, {hasUint8: true}, {hasFloat32: true}, {hasWebGLTexture: false},
    {maskPixels: 1}, {retrievedArrayBytes: 4}, {rgba8ReadbackBytes: 4}, {rgba8ReadbackBytes: undefined},
    {rgba8WorkMs: -1}, {rgba8WorkMs: Infinity}, {rgba8WorkMs: 1.6}, {rgba8ResourcesReused: 'true'},
    {rgba8FallbackReason: 'readback-failure'}, {copyMs: .1}])
    assert.throws(() => validateCategoryExtractionMetrics({...metrics, ...patch}, expected, 2), /extraction/);
  assert.throws(() => validateCategoryExtractionMetrics(metrics, {...expected, outputMode: 'full'}, 2), /disposition/);
  const fallback = {...metrics, path: 'rgba8-sdk-fallback', retrievalMs: .3, conversionMs: 0, copyMs: .1,
    retrievedArrayBytes: 4, categoryBytesCopied: 4, categoryBytesConverted: 0,
    explicitFloatTemporaryBytesAvoided: 0, explicitCategoryCopyBytesAvoided: 0,
    rgba8ReadbackBytes: 0, rgba8FallbackReason: 'readback-failure'};
  assert.deepEqual(validateCategoryExtractionMetrics(fallback, expected, 2), fallback);
  for (const patch of [{rgba8FallbackReason: null}, {rgba8FallbackReason: 'private driver error'},
    {rgba8FallbackReason: 'not-gpu-only'}, {rgba8FallbackReason: 'full-output'}, {rgba8ReadbackBytes: 16}])
    assert.throws(() => validateCategoryExtractionMetrics({...fallback, ...patch}, expected, 2), /extraction|fallback/);
  assert.throws(() => validateCategoryExtractionMetrics({...metrics, mode: 'sdk', path: 'sdk-copy'},
    {...expected, categoryExtractionMode: 'sdk'}, 2), /RGBA8/);
});
