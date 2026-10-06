/** arview capture: part-id decoding and the junction image metrics (qa/capture/id-palette.ts), on synthetic pixels. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {BACKGROUND_LABEL, bandMetrics, decodeLabels, junctionColumns, MAX_DECODE_TOLERANCE, MIXED_LABEL, paletteDecoder} from './capture/id-palette.ts';
import {LENS_ID_RGB, partColour} from '../src/render/override.ts';

const PALETTE = [
  {rgb: [...LENS_ID_RGB], name: 'Lens_L', class: 'lens'},
  {rgb: partColour(0), name: 'Brow', class: 'frame'},
  {rgb: partColour(1), name: 'Eyewire', class: 'frame'},
  {rgb: [...LENS_ID_RGB], name: 'Lens_R', class: 'lens'},
];

/** A width x height RGBA image from rows of labels: 'L' lens, 'B' brow, 'E' eyewire, '.' background, 'x' a blend. */
function image(rows) {
  const height = rows.length, width = rows[0].length, data = new Uint8ClampedArray(width * height * 4);
  const colour = {L: LENS_ID_RGB, B: partColour(0), E: partColour(1), '.': [0, 0, 0]};
  rows.forEach((row, y) => [...row].forEach((code, x) => {
    const rgb = code === 'x' ? LENS_ID_RGB.map((value, i) => Math.round((value + partColour(0)[i]) / 2)) : colour[code];
    data.set([...rgb, 255], (y * width + x) * 4);
  }));
  return {data, width, height};
}

test('each tolerance stays below half its nearest separation; shared lens magenta is not a conflict, a shared frame colour is', () => {
  const decoder = paletteDecoder(PALETTE);
  assert.ok(decoder.tolerance >= 1 && decoder.tolerance <= MAX_DECODE_TOLERANCE);
  assert.ok(2 * decoder.tolerance < decoder.minimumSeparation);
  assert.equal(decoder.tolerances.length, PALETTE.length);
  assert.deepEqual(decoder.warnings, []);
  // Two lens meshes share the magenta (PALETTE has Lens_L and Lens_R): no warning, asserted above.
  const clash = paletteDecoder([{rgb: [10, 200, 30], name: 'A', class: 'frame'}, {rgb: [10, 200, 30], name: 'B', class: 'lens'}]);
  assert.match(clash.warnings.join(' '), /shared by frame part "A" \(#0\) and the lens/);
  // Two FRAME parts with one colour are a conflict too: they would silently decode as one part.
  const frames = paletteDecoder([{rgb: [...LENS_ID_RGB], name: 'Lens', class: 'lens'}, {rgb: [10, 200, 30], name: 'Brow', class: 'frame'},
    {rgb: [10, 200, 30], name: 'Eyewire', class: 'frame'}]);
  assert.deepEqual(frames.warnings, ['part-id colour 10,200,30 is shared by frame part "Brow" (#1) and frame part "Eyewire" (#2); they decode as one']);
  assert.ok(frames.tolerance >= 1);
  const near = paletteDecoder([{rgb: [100, 100, 100], name: 'A', class: 'frame'}, {rgb: [104, 100, 100], name: 'B', class: 'frame'}]);
  assert.equal(near.tolerance, 1); assert.deepEqual(near.tolerances, [1, 1]);
  assert.match(near.warnings.join(' '), /frame part "A" \(#0\) and frame part "B" \(#1\) \(4 levels, tolerance 1\)/);
});

test('tolerances are per colour: one close pair (the r9 case) shrinks only those two parts, never everyone', () => {
  // r9 under the old palette: 'Frame hinge anchor right' (29,158,24) and 'Tip plaque right' (24,158,29), 5 levels apart.
  const palette = [{rgb: [...LENS_ID_RGB], name: 'Lens', class: 'lens'}, {rgb: [29, 158, 24], name: 'Frame hinge anchor right', class: 'frame'},
    {rgb: [24, 158, 29], name: 'Tip plaque right', class: 'frame'}, {rgb: [200, 200, 40], name: 'Brow', class: 'frame'}, {rgb: [40, 60, 220], name: 'Eyewire', class: 'frame'}];
  const decoder = paletteDecoder(palette);
  assert.deepEqual(decoder.tolerances, [MAX_DECODE_TOLERANCE, 2, 2, MAX_DECODE_TOLERANCE, MAX_DECODE_TOLERANCE]);
  assert.equal(decoder.backgroundTolerance, MAX_DECODE_TOLERANCE); assert.equal(decoder.tolerance, 2); assert.equal(decoder.minimumSeparation, 5);
  assert.match(decoder.warnings.join(' '), /"Frame hinge anchor right" \(#1\) and frame part "Tip plaque right" \(#2\) \(5 levels, tolerance 2\).*every other part keeps its own tolerance/);
  // An antialiased edge pixel 12 levels off the brow, the eyewire, the lens and black still decodes; 3 off the close pair is mixed.
  const pixels = new Uint8ClampedArray([212, 188, 52, 255, 52, 72, 208, 255, 243, 12, 243, 255, 12, 12, 0, 255, 29, 161, 21, 255, 29, 159, 24, 255]);
  assert.deepEqual([...decodeLabels(pixels, palette, decoder)], [3, 4, 0, BACKGROUND_LABEL, MIXED_LABEL, 1]);
  assert.throws(() => decodeLabels(pixels, palette, {tolerances: [1], backgroundTolerance: 1}), /1 tolerances for 5 palette colours/);
});

test('the part-id palette: the first 25 colours 102 levels apart, 200 at least 51 apart and 102 from black and the lens magenta, then 17 apart', () => {
  const reserved = [[0, 0, 0], [...LENS_ID_RGB]];
  const cheb = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));
  const colours = Array.from({length: 600}, (_, i) => partColour(i));
  const minimumAmong = (list, extra = []) => {
    let minimum = Infinity;
    for (let i = 0; i < list.length; i++) {
      for (const other of extra) minimum = Math.min(minimum, cheb(list[i], other));
      for (let j = i + 1; j < list.length; j++) minimum = Math.min(minimum, cheb(list[i], list[j]));
    }
    return minimum;
  };
  for (const count of [2, 8, 25]) assert.ok(minimumAmong(colours.slice(0, count)) >= 102, `first ${count}: ${minimumAmong(colours.slice(0, count))}`);
  assert.ok(minimumAmong(colours.slice(0, 64)) >= 51); assert.ok(minimumAmong(colours.slice(0, 200)) >= 51);
  for (const colour of colours.slice(0, 200)) for (const other of reserved) assert.ok(cheb(colour, other) >= 102, `${colour} vs ${other}`);
  assert.ok(minimumAmong(colours, reserved) >= 17);
  assert.equal(new Set(colours.map(colour => colour.join(','))).size, colours.length);
  assert.deepEqual(partColour(7), partColour(7)); assert.notEqual(partColour(7), partColour(7));   // deterministic, a fresh array
  assert.throws(() => partColour(-1), RangeError);
  // 64 parts decode at the full tolerance: every colour exactly, and an antialiased pixel 20 levels off each one.
  const palette = [{rgb: [...LENS_ID_RGB], name: 'lens', class: 'lens'}, ...colours.slice(0, 64).map((rgb, i) => ({rgb, name: `p${i}`, class: 'frame'}))];
  const decoder = paletteDecoder(palette);
  assert.deepEqual(decoder.warnings, []);
  assert.ok(decoder.tolerances.every(value => value === MAX_DECODE_TOLERANCE), JSON.stringify(decoder.tolerances));
  assert.equal(decoder.backgroundTolerance, MAX_DECODE_TOLERANCE);
  const exact = new Uint8ClampedArray(palette.length * 4), off = new Uint8ClampedArray(palette.length * 4);
  palette.forEach((entry, i) => {exact.set([...entry.rgb, 255], i * 4); off.set([...entry.rgb.map(value => value > 127 ? value - 20 : value + 20), 255], i * 4);});
  assert.deepEqual([...decodeLabels(exact, palette, decoder)], palette.map((_, i) => i));
  assert.deepEqual([...decodeLabels(off, palette, decoder)], palette.map((_, i) => i));
});

test('decode: exact and near colours, black background, antialiased blends are mixed', () => {
  const tolerance = paletteDecoder(PALETTE).tolerance;
  const near = partColour(0).map(value => Math.min(255, value + Math.min(tolerance, 3)));
  const pixels = new Uint8ClampedArray([...LENS_ID_RGB, 255, ...near, 255, 0, 0, 0, 255, 1, 1, 1, 255,
    ...LENS_ID_RGB.map((value, i) => Math.round((value + partColour(1)[i]) / 2)), 255]);
  const labels = [...decodeLabels(pixels, PALETTE)];
  assert.deepEqual(labels, [0, 1, BACKGROUND_LABEL, BACKGROUND_LABEL, MIXED_LABEL]);
});

test('junction columns: contact, gap (with its length), lens only, no lens; band pixels per part', () => {
  const {data, width, height} = image([
    'BBBB.E..',
    'BBBB.E..',
    'BBBB....',
    'xBBB....',
    'LLL.L.LL',
    'LLLLL.LL',
  ]);
  const labels = decodeLabels(data, PALETTE);
  const result = junctionColumns(labels, width, height, PALETTE, {x0: 0, x1: 7}, 4);
  // Columns 0-2: contact (column 0 through the antialiased blend). Column 3: lens top at row 5, one background row, then
  // the brow: a gap of 1. Columns 4, 6, 7: background for the whole walk: gaps of 4. Column 5: no lens pixel at all.
  assert.equal(result.columns, 8);
  assert.equal(result.contact_cols_pct, 37.5);
  assert.equal(result.gap_cols_pct, 50);
  assert.equal(result.no_lens_cols_pct, 12.5);
  assert.equal(result.lens_only_cols_pct, 0);
  assert.equal(result.walk_px, 4);
  assert.equal(result.median_gap_px, 4);
  assert.ok(result.band_pixels.Brow > 0 && result.band_pixels.background > 0 && result.band_pixels.mixed === 1);
});

test('junction columns: a decal riding on the lens reads as the lens it sits on, never as contact', () => {
  // 'E' plays a print on the lens top: as a frame part the column reads contact; named in onLens it is lens surface, and
  // the walk above it finds the background (a gap) as it would on the bare lens.
  const {data, width, height} = image([
    '....',
    '....',
    'EEEE',
    'LLLL',
  ]);
  const labels = decodeLabels(data, PALETTE);
  assert.equal(junctionColumns(labels, width, height, PALETTE, {x0: 0, x1: 3}, 3).contact_cols_pct, 100);
  const onLens = junctionColumns(labels, width, height, PALETTE, {x0: 0, x1: 3}, 3, new Set(['Eyewire']));
  assert.equal(onLens.contact_cols_pct, 0); assert.equal(onLens.gap_cols_pct, 100);
});

test('a lens top covered only by more lens within the walk is lens_only; span clamps to the image', () => {
  const {data, width, height} = image(['LL', 'LL', 'LL']);
  const result = junctionColumns(decodeLabels(data, PALETTE), width, height, PALETTE, {x0: -5, x1: 50}, 2);
  assert.equal(result.columns, 2); assert.equal(result.lens_only_cols_pct, 100);
});

test('band metrics port band_metrics.py: white % and the median vertical edge count', () => {
  const width = 20, height = 40, data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const white = y >= 18 && y < 21;            // a 3-row pale line under the lens top at y0 = 20
    data.set(white ? [240, 240, 238, 255] : [120, 90, 70, 255], (y * width + x) * 4);
  }
  const metrics = bandMetrics(data, width, height, 20, {x0: 0, x1: 20});
  assert.deepEqual(metrics.rows, [12, 40]); assert.deepEqual(metrics.columns, [2, 17]);
  assert.equal(metrics.white_pct, Math.round(3 / 28 * 1000) / 10);
  assert.equal(metrics.median_edges, 2);
  assert.equal(bandMetrics(data, width, height, 20, {x0: 5, x1: 5}), null);
});
