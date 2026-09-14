import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createTempleVisibilityConfiguration, hasTempleVisibilityEffect, TEMPLE_VISIBILITY_METHOD,
  LEGACY_TEMPLE_VISIBILITY_METHOD} from './temple-visibility.ts';
import {Matrix4} from 'three';

test('visibility fast path is limited to exact-zero side and frontal contributions', () => {
  const matrix = new Matrix4().makeTranslation(0, 0, -60).toArray();
  const zero = createTempleVisibilityConfiguration(matrix, 4);
  assert.equal(hasTempleVisibilityEffect(zero), false);
  assert.equal(hasTempleVisibilityEffect({...zero, positiveXWeight: Number.MIN_VALUE}), true);
  assert.equal(hasTempleVisibilityEffect({...zero, negativeXWeight: 1}), true);
  assert.equal(hasTempleVisibilityEffect({method: TEMPLE_VISIBILITY_METHOD, negativeXWeight: 0,
    positiveXWeight: 0, frontalOcclusionWeight: Number.MIN_VALUE, coverage: 'ordered-dither'}), true);
  assert.equal(hasTempleVisibilityEffect({method: LEGACY_TEMPLE_VISIBILITY_METHOD, negativeXWeight: 0,
    positiveXWeight: 0, coverage: 'ordered-dither'}), false);
});
