import test from 'node:test';
import assert from 'node:assert/strict';
import {checkHairProtection} from './fast-compose.ts';
import type {LiveHairArmInput} from './live-mask.ts';
import {comparePixels, inside} from '../hair-arm-preview/compose.ts';

function fixture(width: number, height: number, offset: number): LiveHairArmInput {
  const length = width * height * 4;
  const before = new Uint8ClampedArray(new ArrayBuffer(length + offset), offset, length);
  const background = new Uint8ClampedArray(new ArrayBuffer(length + offset), offset, length);
  for (let index = 0; index < length; index++) before[index] = background[index] = (index * 29 + 31) % 256;
  for (let pixel = 0; pixel < width * height; pixel += 13) background[pixel * 4 + 2]! ^= 63;
  const pair = {sourceSHA256: '1'.repeat(64), detectionSHA256: '2'.repeat(64), eyewearModel: 'tom-ford-clear'};
  const model = {id: 'hair-only', sha256: '3'.repeat(64), labels: ['background', 'hair'], hairIndex: 1};
  return {width, height, before, background, pair, geometryPair: {...pair}, expectedModel: model,
    mask: {...pair, model: model.id, modelSHA256: model.sha256, categorySHA256: '4'.repeat(64),
      confidenceSHA256: '5'.repeat(64), width: 1, height: 1, labels: model.labels, hairIndex: 1,
      category: new Uint8Array([1]), confidence: new Float32Array([1])},
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 2, y0: 1, x1: width - 2, y1: 5}, {x0: 4, y0: 3, x1: 7, y1: height - 1}],
      editableRects: [{x0: 1, y0: 2, x1: width - 1, y1: height - 2}, {x0: 5, y0: 0, x1: 9, y1: height}]},
    noseRoi: {x0: 3, y0: 4, x1: 8, y1: height - 2}};
}

function oracle(input: LiveHairArmInput, after: Uint8ClampedArray) {
  const {before, background, width, height} = input;
  return {
    protectedCheck: comparePixels(before, after, width, height, (x, y) => input.protection.protectedRects.some(rect => inside(rect, x, y))),
    noseCheck: comparePixels(before, after, width, height, (x, y) => inside(input.noseRoi, x, y)),
    outsideEditableCheck: comparePixels(before, after, width, height, (x, y) => !input.protection.editableRects.some(rect => inside(rect, x, y))),
    backgroundPreservationCheck: comparePixels(before, after, width, height, (x, y) => {
      const offset = (y * width + x) * 4;
      return before.subarray(offset, offset + 4).every((value, channel) => value === background[offset + channel]);
    }),
  };
}

test('packed and unaligned byte audits agree with independent rectangle checks including alpha-only violations', () => {
  for (const offset of [0, 1, 2, 3, 4, 12]) {
    const input = fixture(23, 17, offset);
    const after = new Uint8ClampedArray(new ArrayBuffer(input.before.length + offset), offset, input.before.length);
    after.set(input.before);
    assert.deepEqual(checkHairProtection(input, after), oracle(input, after));
    for (let pixel = 0; pixel < input.width * input.height; pixel += 7) after[pixel * 4 + (pixel % 4)]! ^= 127;
    after[3] = input.before[3] === 0 ? 255 : 0;
    const beforeCopy = input.before.slice(), backgroundCopy = input.background.slice(), afterCopy = after.slice();
    assert.deepEqual(checkHairProtection(input, after), oracle(input, after));
    assert.deepEqual(input.before, beforeCopy); assert.deepEqual(input.background, backgroundCopy); assert.deepEqual(after, afterCopy);
  }
});

test('native-size sparse differences and caller-supplied membership preserve every tested count and maximum', () => {
  const input = fixture(1280, 853, 0), after = input.before.slice(), membership = new Uint8Array(input.width * input.height);
  for (const [rects, bit] of [[input.protection.protectedRects, 1], [[input.noseRoi], 2], [input.protection.editableRects, 4]] as const)
    for (const rect of rects) for (let y = rect.y0; y < rect.y1; y++) for (let x = rect.x0; x < rect.x1; x++) membership[y * input.width + x]! |= bit;
  for (let pixel = 0; pixel < input.width * input.height; pixel += 997) after[pixel * 4 + pixel % 4]! ^= 255;
  const expected = oracle(input, after);
  assert.deepEqual(checkHairProtection(input, after, membership), expected);
  assert.deepEqual(checkHairProtection(input, after), expected);
  assert.throws(() => checkHairProtection(input, after.subarray(1)), /Comparison sizes/);
  assert.throws(() => checkHairProtection(input, after, membership.subarray(1)), /membership dimensions/);
});
