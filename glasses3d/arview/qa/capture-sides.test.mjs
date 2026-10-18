import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {checkModel} from '../src/model/check.ts';
import {templeInventory, templeMeasurements, deriveTempleClip} from '../src/render/temple-geometry.ts';
import {TryOnRenderer} from '../src/render/renderer.ts';
import {buildGlb, defaultParts, box} from '../tests/glb-fab.ts';
import {lensTopVsBrow} from './capture/junction.ts';
import {cropFile} from './capture/poses.ts';
import {measureTempleReach, nearSideOfYaw, templeReachWarning} from './capture/temple-reach.ts';
import {runSpecValidator} from './capture/spec-validator.mjs';

test('walk Python/runtime reports, part palettes, junction names, worn-side reach and warnings in wearer convention', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'arview-sides-'));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const parts = defaultParts();
  parts.Temple_L = {...parts.Temple_L, box: [-70, -66, 2, 8, -100, -2]};
  const built = buildGlb({parts}), file = path.join(dir, 'asymmetric.glb');
  await writeFile(file, Buffer.from(built.glb));
  const python = await runSpecValidator(file);
  assert.equal(python.available, true, 'the repository Python validator must run');
  const model = await checkModel(built.glb, {url: 'http://127.0.0.1/asymmetric.glb', renderer: null});
  const inventory = templeInventory(model.scene), measures = templeMeasurements(inventory, deriveTempleClip(inventory));
  for (const report of [python.report, model.report]) {
    assert.equal(report.side_convention, 'wearer: +X left, -X right');
    assert.deepEqual(report.measurements.temple_reach_z_mm, {right: -100, left: -140});
  }
  assert.equal(measures.temples.right.tip_z_mm, -100);
  assert.equal(measures.temples.left.tip_z_mm, -140);
  assert.equal(measures.temple_clip.shorter_side, 'right');
  const palette = TryOnRenderer.prototype.parts.call({eyewearRoot: model.scene, disposed: false, partNames: new Map()});
  assert.equal(palette.find(p => p.name === 'Temple_L').side, 'right');
  assert.equal(palette.find(p => p.name === 'Temple_R').side, 'left');
  const soup = bounds => {const b = box(...bounds); return b.faces.flatMap(f => f.flatMap(i => b.vertices[i].map(v => v / 1000)));};
  const lenses = [...soup([-60, -10, -20, 8, -1, 1]), ...soup([10, 60, -20, 8, -1, 1])];
  const junction = lensTopVsBrow(lenses, []);
  for (const side of junction.sides) {
    assert.equal(side.side, side.x_sign < 0 ? 'right' : 'left');
    assert.ok(side.stations.every(p => Math.sign(p.x_mm) === side.x_sign));
    assert.equal(cropFile('front__beauty.png', side.side), `front__beauty__crop_${side.x_sign < 0 ? 'right' : 'left'}.png`);
  }
  for (const yaw of [50, -50]) {
    const sign = yaw > 0 ? -1 : 1, expected = sign < 0 ? 'right' : 'left';
    const reach = measureTempleReach({side: nearSideOfYaw(yaw), yawDegrees: yaw, centreline: [sign * .065, 0, -.01],
      projected: [0, 0, .5], labels: [-1], width: 1, height: 1, armLabels: new Set([1]), returnStartZM: -.075, capZM: -.1});
    assert.equal(reach.side, expected); assert.equal(reach.x_sign, sign);
    assert.ok(templeReachWarning('capture.png', reach).includes(`wearer's ${expected}, ${sign < 0 ? '-X' : '+X'}`));
  }
});
