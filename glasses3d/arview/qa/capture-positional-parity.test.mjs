/** arview capture: a frame renders the same with or without its metadata (WP1 review E/F, major finding on
 *  src/render/temple-geometry.ts). The Prada PR17WS delivery and its stripped copy (every name renamed, every extras
 *  object removed: no partRole, no LENSES_part_visibility; data/wp1_proof/scripts/make_stripped_copy.py) had byte-identical
 *  part-id images but different beauty images (posed views 1,829-3,039 px, the portrait 4,562 px up to 146 levels): the
 *  positional path gave the one front mesh the arm treatment through the material it shares with the temples, and the
 *  lens-input exclusion removed the cream inner bevel at the outer lens corners from the image seen through the lenses.
 *  Since the positional-front rule (optical-material.ts isPositionalFrontFrame) the two captures agree to within the one
 *  difference the metadata really carries: the authored copy marks 3 of the 5 hinge knuckles per side as front, which no
 *  geometry rule can tell from the other two, so at yaw +-45 those knuckles seen through the far lens differ (measured:
 *  134 and 126 px, at most 13 levels, 9 and 4 px above 8 levels). Every other image is byte-identical.
 *
 *  Needs headless Chromium on a hardware GPU and the two local-only GLBs (gitignored data); skipped when either is absent. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {deflateSync, inflateSync} from 'node:zlib';
import {main} from './capture.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LENSES = path.resolve(HERE, '..', '..', '..');
const PRADA = path.join(LENSES, 'automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb');
const STRIPPED = path.join(LENSES, 'glasses3d/data/wp1_proof/inputs/prada-stripped.glb');
/** Per beauty image: pixels more than 8 levels apart, and the largest channel difference (see the module comment). */
const MAX_PX_ABOVE_8 = 40, MAX_LEVELS = 20;

/** Decode an 8-bit, non-interlaced RGB or RGBA PNG (what canvas.toDataURL writes) to {width, height, channels, data}. */
export function decodePng(bytes) {
  assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', 'not a PNG');
  let offset = 8, width = 0, height = 0, channels = 0;
  const idat = [];
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset), type = bytes.toString('latin1', offset + 4, offset + 8);
    const body = bytes.subarray(offset + 8, offset + 8 + length);
    if (type === 'IHDR') {
      width = body.readUInt32BE(0); height = body.readUInt32BE(4);
      const depth = body[8], colour = body[9], interlace = body[12];
      assert.ok(depth === 8 && (colour === 2 || colour === 6) && interlace === 0, `unsupported PNG (depth ${depth}, colour ${colour}, interlace ${interlace})`);
      channels = colour === 6 ? 4 : 3;
    } else if (type === 'IDAT') idat.push(body);
    else if (type === 'IEND') break;
    offset += 12 + length;
  }
  const raw = inflateSync(Buffer.concat(idat)), stride = width * channels, data = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? data[y * stride + x - channels] : 0, b = y > 0 ? data[(y - 1) * stride + x] : 0;
      const c = x >= channels && y > 0 ? data[(y - 1) * stride + x - channels] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      const predictor = [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][filter];
      assert.ok(predictor !== undefined, `bad PNG filter ${filter}`);
      data[y * stride + x] = (line[x] + predictor) & 0xff;
    }
  }
  return {width, height, channels, data};
}

/** {differing, above8, max} over the RGB channels of two decoded PNGs of the same size. */
export function comparePixels(first, second) {
  assert.equal(first.width, second.width); assert.equal(first.height, second.height);
  let differing = 0, above8 = 0, max = 0;
  for (let i = 0; i < first.width * first.height; i++) {
    let d = 0;
    for (let k = 0; k < 3; k++) d = Math.max(d, Math.abs(first.data[i * first.channels + k] - second.data[i * second.channels + k]));
    if (d > 0) differing++;
    if (d > 8) above8++;
    max = Math.max(max, d);
  }
  return {differing, above8, max};
}

test('decodePng reads an RGBA PNG with every filter type', () => {
  // 2x2 RGBA, rows filtered 1 (Sub) and 4 (Paeth); built by hand: red, green / blue, white.
  const rows = Buffer.from([1, 255, 0, 0, 255, 1, 255, 0, 0, 4, 1, 0, 255, 0, 255, 0, 0, 0]);
  const chunk = (type, body) => {
    const head = Buffer.alloc(8); head.writeUInt32BE(body.length, 0); head.write(type, 4, 'latin1');
    return Buffer.concat([head, body, Buffer.alloc(4)]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(2, 0); ihdr.writeUInt32BE(2, 4); ihdr[8] = 8; ihdr[9] = 6;
  const png = Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
  const image = decodePng(png);
  assert.deepEqual([...image.data], [255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255]);
});

test('the Prada delivery and its metadata-stripped copy render the same (positional front is front)', {timeout: 300_000}, async t => {
  const missing = [PRADA, STRIPPED].filter(file => !fs.existsSync(file));
  if (missing.length) {t.skip(`local-only input missing: ${missing.join(', ')}`); return;}
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'arview-parity-'));
  try {
    const outs = [];
    for (const [name, glb] of [['authored', PRADA], ['stripped', STRIPPED]]) {
      const out = path.join(work, name);
      const code = await main([glb, '--poses', 'default', '--out', out, '--step-timeout-s', '60']);
      const report = JSON.parse(fs.readFileSync(path.join(out, 'report.json'), 'utf8'));
      assert.ok(code === 0 || code === 3, `${name}: exit ${code} (${report.status}: ${report.infrastructure_failure ?? ''})`);
      assert.equal(report.images.length, report.planned_images, `${name}: every planned image was made`);
      outs.push({out, report});
    }
    const [authored, stripped] = outs;
    assert.equal(stripped.report.runtime.temples.decided_by, 'position');
    assert.equal(authored.report.runtime.temples.decided_by, 'LENSES_part_visibility');
    assert.ok(!stripped.report.runtime.temples.left.parts.includes('Object_0') && !stripped.report.runtime.temples.right.parts.includes('Object_0'),
      'the stripped front mesh (Object_0) is not a temple part');
    const files = authored.report.images.map(image => image.file).sort();
    assert.deepEqual(stripped.report.images.map(image => image.file).sort(), files);
    const failures = [];
    for (const file of files) {
      const result = comparePixels(decodePng(fs.readFileSync(path.join(authored.out, file))), decodePng(fs.readFileSync(path.join(stripped.out, file))));
      if (/part-id/.test(file) ? result.differing !== 0 : result.above8 > MAX_PX_ABOVE_8 || result.max > MAX_LEVELS) {
        failures.push(`${file}: ${result.differing} px differ, ${result.above8} above 8 levels, max ${result.max}`);
      }
    }
    assert.deepEqual(failures, [], 'beauty within the hinge-knuckle residual, part-id identical');
  } finally {fs.rmSync(work, {recursive: true, force: true});}
});
