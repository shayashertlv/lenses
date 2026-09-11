import assert from 'node:assert/strict';
import {test} from 'node:test';
import {validateHairCostRequest, validateCategoryExtractionMetrics} from './protocol.ts';
import type {HairExpectedPair} from './protocol.ts';
import {extractCategoryMask} from './extraction.ts';
import type {CategoryMaskSource} from './extraction.ts';

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
