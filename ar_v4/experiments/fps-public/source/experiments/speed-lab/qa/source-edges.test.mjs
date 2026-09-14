import test from 'node:test';
import assert from 'node:assert/strict';
import {patternPixels, diffPixels} from './source-edges-browser.mjs';

test('source-edge ramps cover every channel byte, checkerboards include saturated extrema, and A returns exactly', () => {
  const a = patternPixels(641, 427, 'A'), b = patternPixels(641, 427, 'B');
  for (let channel = 0; channel < 3; channel++) {
    const values = new Set(); for (let i = channel; i < a.length; i += 4) values.add(a[i]);
    assert.equal(values.size, 256);
  }
  for (const pixels of [a, b]) for (let i = 3; i < pixels.length; i += 4) assert.equal(pixels[i], 255);
  assert.deepEqual(Array.from(b.subarray(0, 4)), [0, 0, 0, 255]);
  assert.deepEqual(Array.from(b.subarray(8 * 4, 8 * 4 + 4)), [255, 255, 255, 255]);
  assert.deepEqual(Array.from(b.subarray(16 * 4, 16 * 4 + 4)), [255, 0, 0, 255]);
  assert.deepEqual(patternPixels(641, 427, 'A'), a);
  assert.ok(diffPixels(a, b, 641).changedPixels > 0);
});

test('exact RGBA checks reject one alpha byte and report its odd-width coordinate without a tolerance', () => {
  const a = patternPixels(641, 3, 'A'), b = a.slice(), index = (641 + 640) * 4;
  b[index + 3] = 254;
  const result = diffPixels(b, a, 641);
  assert.equal(result.testedPixels, 641 * 3); assert.equal(result.changedPixels, 1); assert.equal(result.maxDelta, 1);
  assert.deepEqual(result.firstChanged[0], {x: 640, y: 1,
    actual: Array.from(b.subarray(index, index + 4)), expected: Array.from(a.subarray(index, index + 4))});
  assert.equal(diffPixels(b, a, 641, x => x < 640).changedPixels, 0);
  assert.throws(() => diffPixels(a, a.subarray(4), 641), /dimensions/);
});

test('unsupported alpha control keeps a colored half-alpha pixel, not an invisible black-only case', () => {
  const a = patternPixels(641, 427, 'A'), alpha = patternPixels(641, 427, 'A', true);
  assert.deepEqual(Array.from(alpha.subarray(0, 4)), [200, 100, 40, 128]);
  assert.deepEqual(alpha.subarray(4), a.subarray(4));
});
