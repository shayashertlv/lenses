/** arview capture --cameras <cameras.json> --no-face, end to end on the GPU (the REAL orchestrator in this process): a box
 *  GLB seen from two explicit cameras, no face.
 *
 *  - exit 0, the report says no face and names the cameras file by sha256; three images per camera;
 *  - each part-id silhouette lies exactly where the cameras file's pinhole projection puts the model's corners (the
 *    box parts' bounding box, within 1.5 px), so the camera reached the renderer unchanged (no face fit, no auto scale);
 *  - the beauty image's corner is the camera's own background colour (the frame the lenses transmit);
 *  - from the +x side the x > 0 temple is drawn and covers the x < 0 one: the near side by geometry, never swapped;
 *  - a bad cameras file is a usage error (exit 2) before any browser starts.
 *  Needs headless Chromium on a hardware GPU, like the other capture tests. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {inflateSync} from 'node:zlib';
import {main} from './capture.mjs';
import {buildGlb, defaultParts} from '../tests/glb-fab.ts';
import {CAMERAS_SCHEMA, projectPixel, parseCameraSet} from './capture/cameras.ts';

/** Decode an 8-bit, non-interlaced RGB or RGBA PNG (what canvas.toDataURL writes). */
function decodePng(bytes) {
  let offset = 8, width = 0, height = 0, channels = 0;
  const idat = [];
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset), type = bytes.toString('latin1', offset + 4, offset + 8);
    const body = bytes.subarray(offset + 8, offset + 8 + length);
    if (type === 'IHDR') {width = body.readUInt32BE(0); height = body.readUInt32BE(4); channels = body[9] === 6 ? 4 : 3;}
    else if (type === 'IDAT') idat.push(body);
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
      data[y * stride + x] = (line[x] + [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][filter]) & 0xff;
    }
  }
  return {width, height, channels, data};
}
const pixel = (image, x, y) => Array.from(image.data.subarray((y * image.width + x) * image.channels, (y * image.width + x) * image.channels + 3));

function lookAt(eye, target = [0, 0, 0]) {
  const sub = (a, b) => a.map((v, i) => v - b[i]), norm = a => {const l = Math.hypot(...a); return a.map(v => v / l);};
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const z = norm(sub(eye, target)), x = norm(cross([0, 1, 0], z)), y = cross(z, x);
  return [...x, -dot(x, eye), ...y, -dot(y, eye), ...z, -dot(z, eye), 0, 0, 0, 1];
}

test('--cameras --no-face renders each explicit camera exactly, without a face', {timeout: 180_000}, async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'arview-cameras-'));
  try {
    const glb = path.join(work, 'boxes.glb');
    fs.writeFileSync(glb, Buffer.from(buildGlb().glb));
    const cameras = {schema: CAMERAS_SCHEMA, cameras: [
      {id: 'front_above', width: 800, height: 480, background: '#f2efe9', view: lookAt([0.02, 0.12, 0.6], [0, 0, -0.04]),
        fx: 1500, fy: 1500, cx: 410.5, cy: 230, near: 0.2, far: 2, meta: {note: 'test'}},
      {id: 'side_px', width: 900, height: 360, background: '#e9eef2', view: lookAt([0.6, 0.005, -0.071], [0, 0.005, -0.071]),
        fx: 1300, fy: 1300, cx: 449.5, cy: 179.5, near: 0.2, far: 2}]};
    const file = path.join(work, 'cameras.json');
    fs.writeFileSync(file, JSON.stringify(cameras));
    const out = path.join(work, 'out');
    const code = await main([glb, '--cameras', file, '--no-face', '--out', out, '--step-timeout-s', '30']);
    const report = JSON.parse(fs.readFileSync(path.join(out, 'report.json'), 'utf8'));
    assert.equal(code, 0, `exit ${code}: ${report.status} ${report.infrastructure_failure ?? ''} ${report.errors.join('; ')}`);
    assert.equal(report.request.no_face, true); assert.equal(report.request.fixed_scale, true); assert.equal(report.request.poses, null);
    assert.deepEqual(report.request.cameras.ids, ['front_above', 'side_px']);
    assert.deepEqual(report.images.map(image => image.file).sort(),
      ['front_above__beauty.png', 'front_above__part-id.png', 'front_above__silhouette.png',
        'side_px__beauty.png', 'side_px__part-id.png', 'side_px__silhouette.png']);
    assert.equal(report.portrait, null, 'no face, no portrait');
    assert.deepEqual(report.poses.map(entry => [entry.id, entry.kind, entry.explicit]), [['front_above', 'explicit_camera', true], ['side_px', 'explicit_camera', true]]);
    assert.deepEqual(report.poses[0].meta, {note: 'test'});
    for (const pose of report.poses) {
      assert.deepEqual(pose.temple_pose.requested, {});
      assert.deepEqual(pose.temple_pose.applied, {});
      assert.deepEqual(pose.temple_pose.moved_meshes, {'+1': 0, '-1': 0});
    }
    // every box corner of every part, projected by the file's own pinhole camera
    const corners = Object.values(defaultParts()).flatMap(({box: [x0, x1, y0, y1, z0, z1]}) =>
      [x0, x1].flatMap(x => [y0, y1].flatMap(y => [z0, z1].map(z => [x / 1000, y / 1000, z / 1000]))));
    for (const spec of parseCameraSet(cameras).cameras) {
      const partId = decodePng(fs.readFileSync(path.join(out, `${spec.id}__part-id.png`)));
      assert.deepEqual([partId.width, partId.height], [spec.width, spec.height]);
      const silhouette = decodePng(fs.readFileSync(path.join(out, `${spec.id}__silhouette.png`)));
      for (let y = 0; y < partId.height; y++) for (let x = 0; x < partId.width; x++) {
        const rgb = pixel(silhouette, x, y);
        assert.ok(rgb[0] === rgb[1] && rgb[1] === rgb[2], 'coverage is a scalar');
        assert.equal(rgb[0] > 0, Math.max(...pixel(partId, x, y)) > 0, 'same geometry/coverage as part-id');
      }
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (let y = 0; y < partId.height; y++) for (let x = 0; x < partId.width; x++) {
        if (Math.max(...pixel(partId, x, y)) <= 40) continue;
        minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
      const uv = corners.map(point => projectPixel(spec, point));
      const expected = [Math.min(...uv.map(p => p[0])), Math.max(...uv.map(p => p[0])), Math.min(...uv.map(p => p[1])), Math.max(...uv.map(p => p[1]))];
      // pixel centres covered by the silhouette: the projected extent rounded inwards, within 1.5 px
      for (const [got, want] of [[minX, expected[0]], [maxX, expected[1]], [minY, expected[2]], [maxY, expected[3]]]) {
        assert.ok(Math.abs(got - want) <= 1.5, `${spec.id}: silhouette edge ${got} vs projected ${want.toFixed(2)} (${JSON.stringify(expected)})`);
      }
      const beauty = decodePng(fs.readFileSync(path.join(out, `${spec.id}__beauty.png`)));
      const want = [1, 3, 5].map(i => parseInt(spec.background.slice(i, i + 2), 16));
      assert.ok(pixel(beauty, 2, 2).every((v, i) => Math.abs(v - want[i]) <= 1), `${spec.id}: corner ${pixel(beauty, 2, 2)} vs ${want}`);
    }
    // from the +x side, level with the temples' centre line: the x > 0 temple (palette side 'right', by geometry) is drawn,
    // the x < 0 one (farther, so smaller around the same line) hidden behind it
    const side = decodePng(fs.readFileSync(path.join(out, 'side_px__part-id.png')));
    const count = name => {
      const entry = report.id_palette.find(item => item.name === name);
      let n = 0;
      for (let i = 0; i < side.width * side.height; i++) {
        if ([0, 1, 2].every(c => Math.abs(side.data[i * side.channels + c] - entry.rgb[c]) <= entry.decode_tolerance)) n++;
      }
      return {n, side: entry.side};
    };
    const near = count('Temple_R'), far = count('Temple_L');
    assert.equal(near.side, 'left'); assert.equal(far.side, 'right');
    assert.ok(near.n > 2000 && far.n < near.n / 20, `near temple ${near.n} px, far temple ${far.n} px`);
  } finally {fs.rmSync(work, {recursive: true, force: true});}
});

test('a bad cameras file is a usage error before anything starts', async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'arview-cameras-bad-'));
  try {
    const glb = path.join(work, 'boxes.glb');
    fs.writeFileSync(glb, Buffer.from(buildGlb().glb));
    const file = path.join(work, 'cameras.json');
    fs.writeFileSync(file, JSON.stringify({schema: CAMERAS_SCHEMA, cameras: [{id: 'x', width: 4000}]}));
    assert.equal(await main([glb, '--cameras', file, '--no-face', '--out', path.join(work, 'out')]), 2);
    fs.writeFileSync(file, '{not json');
    assert.equal(await main([glb, '--cameras', file, '--no-face', '--out', path.join(work, 'out2')]), 2);
    assert.equal(await main([glb, '--cameras', path.join(work, 'missing.json'), '--no-face', '--out', path.join(work, 'out3')]), 2);
    assert.equal(fs.existsSync(path.join(work, 'out')), false, 'nothing ran, nothing was written');
  } finally {fs.rmSync(work, {recursive: true, force: true});}
});
