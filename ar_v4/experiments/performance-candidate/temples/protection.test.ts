import assert from 'node:assert/strict';
import {test} from 'node:test';
import {composeProtectedPixels} from './protection.ts';
import {composeProtectedPixels as acceptedCompose} from '../../../references/perfect-temples/experiments/temple-sagittal/protection.ts';
import type {PixelRect, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

test('row spans preserve accepted bytes and counts for overlapping, nested, touching and empty edit regions', () => {
  let seed = 0x97164;
  const integer = (max: number): number => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % max; };
  for (let sample = 0; sample < 400; sample++) {
    const width = 1 + integer(43), height = 1 + integer(31);
    const rectangle = (): PixelRect => {
      const x0 = integer(width), y0 = integer(height);
      return {x0, y0, x1: x0 + 1 + integer(width - x0), y1: y0 + 1 + integer(height - y0)};
    };
    const protection: ProtectionConfiguration = {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: Array.from({length: 2 + integer(7)}, rectangle), editableRects: Array.from({length: integer(5)}, rectangle)};
    const baseline = Uint8ClampedArray.from({length: width * height * 4}, () => integer(256));
    const candidate = baseline.map((value, index) => index % 5 ? value : integer(256));
    const baselineBefore = baseline.slice(), candidateBefore = candidate.slice();
    assert.deepEqual(composeProtectedPixels(baseline, candidate, protection), acceptedCompose(baseline, candidate, protection), `case ${sample}`);
    assert.deepEqual(baseline, baselineBefore); assert.deepEqual(candidate, candidateBefore);
  }
});

test('row spans retain validation and alpha-channel comparisons', () => {
  const baseline = new Uint8ClampedArray(6 * 3 * 4).fill(99), candidate = baseline.slice();
  candidate[(1 * 6 + 3) * 4 + 3] = 200;
  const protection: ProtectionConfiguration = {method: 'temple-optics-copy-v1', width: 6, height: 3, marginPx: 4,
    protectedRects: [{x0: 0, y0: 0, x1: 2, y1: 3}, {x0: 0, y0: 0, x1: 1, y1: 1}],
    editableRects: [{x0: 0, y0: 0, x1: 6, y1: 3}, {x0: 2, y0: 0, x1: 6, y1: 3}]};
  const result = composeProtectedPixels(baseline, candidate, protection);
  assert.equal(result.changedPixels, 1); assert.equal(result.protectedPixels, 6); assert.equal(result.editablePixels, 12);
  assert.throws(() => composeProtectedPixels(baseline.subarray(1), candidate, protection), /sizes differ/);
  assert.throws(() => composeProtectedPixels(baseline, candidate, {...protection, editableRects: [{x0: -1, y0: 0, x1: 2, y1: 2}]}), /rectangle/);
});
