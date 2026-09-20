import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Euler, Matrix4, Vector3} from 'three';
import {TempleHeadFit, templeHeadFitX, TEMPLE_HEAD_FIT} from '../src/render/temple-head-fit.ts';
import {createTempleHeadShell, createTempleHeadShellFit} from '../src/render/temple-head-shell.ts';
import {SHIPPED_EYEWEAR} from '../src/eyewear/catalog.ts';
import {TempleSurface} from '../src/render/temple-surface.ts';

const face: {positions: number[]; indices: number[]} = JSON.parse(await readFile(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8'));
const pose = (depth = 45, pitch = 0, yaw = 0) => new Matrix4().makeRotationFromEuler(new Euler(pitch * Math.PI / 180, yaw * Math.PI / 180, 0, 'YXZ')).setPosition(0, 0, -depth);
const observed = (matrix: Matrix4, ratio = 1, sideErrorCm = 0) => new Float32Array(face.positions.flatMap((_, i) => {
  if (i % 3) return [];
  const p = new Vector3().fromArray(face.positions, i); p.x = templeHeadFitX(p.x, p.z, ratio);
  if (p.x < -4.5) {const t = Math.min(1, (-p.x - 4.5) / 2); p.x -= sideErrorCm * t * t * (3 - 2 * t);}
  return p.applyMatrix4(matrix).toArray();
}));

test('one bilateral anterior ratio learns a narrow wearer and holds through pitch, distance and unilateral noise', () => {
  const fit = new TempleHeadFit(face.positions), frontal = pose();
  for (let i = 0; i < 160; i++) fit.observe(observed(frontal, .94), frontal.toArray(), i * 33);
  assert.equal(fit.report.state, 'stable'); assert.ok(Math.abs(fit.ratio - .94) < .003); assert.equal(fit.report.acceptedSamples, 90);
  const accepted = fit.report.acceptedSamples, ratio = fit.ratio;
  for (let i = 0; i < 40; i++) {const matrix = pose(20 + i, 20 + i / 2, i % 2 ? 20 : -20);
    fit.observe(observed(matrix, .94), matrix.toArray(), 6000 + i * 33);}
  assert.equal(fit.ratio, ratio); assert.equal(fit.report.acceptedSamples, accepted);
  for (const error of [.3, .6, -.3, -.6]) fit.observe(observed(frontal, .94, error), frontal.toArray(), 8000 + error);
  assert.equal(fit.ratio, ratio); assert.equal(fit.report.acceptedSamples, accepted);
  // Even biased but otherwise admissible near-frontal observations cannot reshape a completed fit.
  for (let i = 0; i < 50; i++) {const matrix = pose(20 + i);fit.observe(observed(matrix, 1.06), matrix.toArray(), 9000 + i * 33);}
  assert.equal(fit.ratio, ratio);
  fit.miss(8500); assert.equal(fit.ratio, ratio);
  fit.miss(14000); assert.equal(fit.ratio, 1); assert.equal(fit.report.acceptedSamples, 0);
});

test('unilateral side noise is refused during collection without requiring perfect bilateral regions', () => {
  const fit = new TempleHeadFit(face.positions), matrix = pose();
  for (let i = 0; i < 60; i++) fit.observe(observed(matrix, .94), matrix.toArray(), i * 33);
  const ratio = fit.ratio, accepted = fit.report.acceptedSamples;
  for (const error of [.3, .6, -.3, -.6]) fit.observe(observed(matrix, .94, error), matrix.toArray(), 2200 + error);
  assert.equal(fit.ratio, ratio); assert.equal(fit.report.acceptedSamples, accepted); assert.equal(fit.report.rejection, 'bilateral');
});

test('canonical calibration stays at identity, and the anterior mapping is monotone, symmetric and fixed posteriorly', () => {
  const fit = new TempleHeadFit(face.positions);
  for (let i = 0; i < 180; i++) {const matrix = pose(20 + i % 50, 0, 0);fit.observe(observed(matrix), matrix.toArray(), i * 33);}
  assert.ok(Math.abs(fit.ratio - 1) < 1e-6);
  for (const ratio of [.92, 1, 1.08]) for (const z of [-8, -3, -2, -1, 0, 4]) {
    assert.equal(templeHeadFitX(-7, z, ratio), -templeHeadFitX(7, z, ratio));
    assert.ok(templeHeadFitX(7, z, ratio) > templeHeadFitX(6, z, ratio));
    if (z <= -3 || ratio === 1) assert.equal(templeHeadFitX(7, z, ratio), 7);
  }
});

test('fitting the closed shell preserves shared edges and all posterior facets, including both assets terminal bands', () => {
  const geometry = createTempleHeadShell(face.positions, face.indices);
  try {
    const fit = createTempleHeadShellFit(geometry), p = geometry.getAttribute('position'), baseline = p.array.slice();
    for (const ratio of [.92, 1.08, .94, 1]) {
      fit.set(ratio); const edges = new Map<string, number>(); let preserved = 0, moved = 0;
      for (let i = 0; i < p.count; i += 3) {
        const z = [0, 1, 2].map(c => p.getZ(i + c));
        assert.ok(Math.max(...z) <= -3 || Math.min(...z) >= -3, 'no facet spans the protected posterior boundary');
        for (let c = 0; c < 3; c++) {
          const k = (i + c) * 3;
          assert.equal(p.getY(i + c), baseline[k + 1]); assert.equal(p.getZ(i + c), baseline[k + 2]);
          if (z[c]! <= -3) {assert.equal(p.getX(i + c), baseline[k]); preserved++;}
          if (p.getX(i + c) !== baseline[k]) moved++;
        }
        const key = (j: number) => `${p.getX(j)},${p.getY(j)},${p.getZ(j)}`;
        for (const [a, b] of [[i, i + 1], [i + 1, i + 2], [i + 2, i]]) {
          const ka = key(a!), kb = key(b!); assert.notEqual(ka, kb);
          const edge = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`; edges.set(edge, (edges.get(edge) ?? 0) + 1);
        }
      }
      for (const count of edges.values()) assert.equal(count, 2, 'the fitted shell stays watertight');
      assert.ok(preserved > 100); if (ratio !== 1) assert.ok(moved > 100);
    }
    assert.deepEqual(p.array, baseline, 'returning to identity exactly restores the owned base positions');
    for (const model of Object.values(SHIPPED_EYEWEAR)) {
      const terminalFrontCm = (model.templeClipLocalZM + .025 + .005) * 100 + model.offsetCm[2];
      assert.ok(terminalFrontCm < TEMPLE_HEAD_FIT.posteriorZCm - 1, `${model.name} terminal band is wholly inside unchanged posterior volume`);
    }
  } finally {geometry.dispose();}
});

test('the rigid face uses the same anterior fit while central observations remain untouched', () => {
  const shape = [0, 1, 5, -7, 2, 1, 7, 2, 1, -7, 2, -4, 7, 2, -4], matrix = pose(25, 40), surface = new TempleSurface(shape);
  const points = new Float32Array(shape.flatMap((_, i) => i % 3 ? [] : new Vector3().fromArray(shape, i).applyMatrix4(matrix).toArray()));
  const before = points.slice(); surface.apply(points, matrix.toArray(), .94);
  assert.deepEqual(points.slice(0, 3), before.slice(0, 3)); assert.deepEqual(points.slice(9), before.slice(9));
  for (const i of [3, 6]) {const expected = new Vector3(templeHeadFitX(shape[i]!, shape[i + 2]!, .94), shape[i + 1], shape[i + 2]).applyMatrix4(matrix);
    for (let axis = 0; axis < 3; axis++) assert.ok(Math.abs(points[i + axis]! - expected.getComponent(axis)) < 1e-5);}
});
