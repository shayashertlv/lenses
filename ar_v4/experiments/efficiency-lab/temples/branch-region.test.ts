import assert from 'node:assert/strict';
import {test} from 'node:test';
import {editableRowBand, readBranchRegion} from './branch-region.ts';
import {composeProtectedPixels} from '../../performance-candidate/temples/protection.ts';
import type {PixelRect, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

const rect = (x0: number, y0: number, x1: number, y1: number): PixelRect => ({x0, y0, x1, y1});
const protection = (editableRects: PixelRect[], protectedRects = [rect(0, 0, 2, 6), rect(6, 0, 8, 6)]): ProtectionConfiguration =>
  ({method: 'temple-optics-copy-v1', width: 8, height: 6, marginPx: 4, protectedRects, editableRects});
const inside = (r: PixelRect, x: number, y: number) => r.x0 <= x && x < r.x1 && r.y0 <= y && y < r.y1;

test('row band subtracts the complete protected union and keeps disjoint editable rows', () => {
  assert.deepEqual(editableRowBand(protection([rect(1, 1, 4, 3), rect(4, 4, 7, 5)])), rect(0, 1, 8, 5));
  assert.equal(editableRowBand(protection([rect(0, 0, 2, 6), rect(6, 0, 8, 6)])), null);
  assert.equal(editableRowBand(protection([])), null);
  assert.equal(editableRowBand(protection([rect(0, 0, 8, 6)], [rect(0, 0, 5, 6), rect(3, 0, 8, 6)])), null);
});

test('bands match an independent per-pixel oracle over overlapping and touching rectangle sets', () => {
  let seed = 731;
  const next = (max: number) => {seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % max;};
  const nextRect = () => {const x = next(8), y = next(6); return rect(x, y, x + 1 + next(8 - x), y + 1 + next(6 - y));};
  for (let i = 0; i < 800; i++) {
    const value = protection(Array.from({length: next(5)}, nextRect), Array.from({length: 2 + next(7)}, nextRect));
    const ys = [];
    for (let y = 0; y < 6; y++) for (let x = 0; x < 8; x++)
      if (value.editableRects.some(r => inside(r, x, y)) && !value.protectedRects.some(r => inside(r, x, y))) ys.push(y);
    assert.deepEqual(editableRowBand(value), ys.length ? rect(0, Math.min(...ys), 8, Math.max(...ys) + 1) : null);
  }
});

function fixture() {
  const priorImage = globalThis.ImageData;
  Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: class {
    readonly data: Uint8ClampedArray; readonly width: number; readonly height: number;
    constructor(data: Uint8ClampedArray, width: number, height: number) {this.data = data; this.width = width; this.height = height;}
  }});
  const state = new Map<number, unknown>([[1, {framebuffer: 'prior'}], [2, {buffer: 'prior'}], [3, 8], [4, 23], [5, 2], [6, 1]]);
  const original = new Map(state), reads: number[][] = [];
  let failure = false, lost = false;
  const gl = {READ_FRAMEBUFFER_BINDING: 1, PIXEL_PACK_BUFFER_BINDING: 2, PACK_ALIGNMENT: 3, PACK_ROW_LENGTH: 4,
    PACK_SKIP_PIXELS: 5, PACK_SKIP_ROWS: 6, READ_FRAMEBUFFER: 7, PIXEL_PACK_BUFFER: 8, RGBA: 9, UNSIGNED_BYTE: 10, NO_ERROR: 0,
    getParameter: (key: number) => state.get(key), isContextLost: () => lost, getError: () => failure ? 1282 : 0,
    bindFramebuffer: (_target: number, value: unknown) => {state.set(1, value);},
    bindBuffer: (_target: number, value: unknown) => {state.set(2, value);}, pixelStorei: (key: number, value: number) => {state.set(key, value);},
    readPixels: (x: number, y: number, width: number, height: number, _format: number, _type: number, bytes: Uint8Array) => {
      reads.push([x, y, width, height]); assert.equal(state.get(1), null); assert.equal(state.get(2), null);
      assert.deepEqual([state.get(3), state.get(4), state.get(5), state.get(6)], [1, 0, 0, 0]);
      for (let row = 0; row < height; row++) for (let column = 0; column < width; column++)
        bytes.fill((5 - (y + row)) * 8 + column, (row * width + column) * 4, (row * width + column + 1) * 4);
    }};
  const canvas = {width: 8, height: 6, getContext: () => gl} as unknown as HTMLCanvasElement;
  const baseline = new ImageData(new Uint8ClampedArray(8 * 6 * 4).fill(200), 8, 6);
  return {canvas, baseline, state, original, reads, fail: () => {failure = true;}, lose: () => {lost = true;},
    restore: () => Object.defineProperty(globalThis, 'ImageData', {configurable: true, value: priorImage})};
}

test('cropped native transfer flips only owned rows and produces exact final protected composition', () => {
  const f = fixture();
  try {
    const guards = protection([rect(1, 1, 4, 3), rect(4, 4, 7, 5)]), band = editableRowBand(guards)!;
    const result = readBranchRegion(f.canvas, f.baseline, band);
    assert.deepEqual(f.reads, [[0, 1, 8, 4]]); assert.deepEqual(f.state, f.original);
    assert.equal(result.readCalls, 1); assert.equal(result.readBytes, 8 * 4 * 4);
    const full = new Uint8ClampedArray(8 * 6 * 4);
    for (let i = 0; i < 48; i++) full.fill(i, i * 4, (i + 1) * 4);
    assert.deepEqual(composeProtectedPixels(f.baseline.data, result.pixels.data, guards), composeProtectedPixels(f.baseline.data, full, guards));
    const prior = result.pixels.data.slice();
    readBranchRegion(f.canvas, f.baseline, rect(0, 0, 8, 1)); f.baseline.data.fill(0);
    assert.deepEqual(result.pixels.data, prior);
  } finally {f.restore();}
});

test('empty band avoids GL work and returns owned baseline bytes; invalid or failed reads never publish stale storage', () => {
  const f = fixture();
  try {
    const result = readBranchRegion(f.canvas, f.baseline, null);
    assert.equal(result.readCalls, 0); assert.equal(result.readBytes, 0); assert.equal(f.reads.length, 0);
    assert.notEqual(result.pixels.data.buffer, f.baseline.data.buffer);
    for (const invalid of [rect(1, 0, 8, 6), rect(0, -1, 8, 2), rect(0, 2, 8, 2), rect(0, 0, 8, 7)])
      assert.throws(() => readBranchRegion(f.canvas, f.baseline, invalid), /row band/);
    f.fail(); assert.throws(() => readBranchRegion(f.canvas, f.baseline, rect(0, 1, 8, 3)), /failed/);
    assert.deepEqual(f.state, f.original);
    f.lose(); assert.throws(() => readBranchRegion(f.canvas, f.baseline, rect(0, 1, 8, 3)), /unavailable/);
  } finally {f.restore();}
});
