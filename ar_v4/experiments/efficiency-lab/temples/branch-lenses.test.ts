import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BoxGeometry, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial} from 'three';
import {BranchLensOmission} from './branch-lenses.ts';
import {DEFAULT_SPEED_OPTIONS, normalizeSpeedOptions} from '../speed-options.ts';

test('branch lens scope hides only unique visible transmission materials and restores every mode', () => {
  const root = new Group(), geometry = new BoxGeometry();
  const lens = new MeshPhysicalMaterial({transmission: 1}), hidden = new MeshPhysicalMaterial({transmission: 1, visible: false});
  const opaque = new MeshStandardMaterial(), transparent = new MeshStandardMaterial({transparent: true});
  root.add(new Mesh(geometry, [lens, opaque, hidden, transparent]), new Mesh(geometry, lens));
  const controller = new BranchLensOmission(root);
  for (const enabled of [true, false, true]) {
    const metrics = controller.render(enabled, () => {
      assert.equal(lens.visible, !enabled); assert.equal(hidden.visible, false);
      assert.equal(opaque.visible, true); assert.equal(transparent.visible, true);
    });
    assert.deepEqual(metrics, {requested: enabled, used: enabled, omittedMaterials: Number(enabled), fallback: null});
    assert.equal(lens.visible, true); assert.equal(hidden.visible, false);
  }
  geometry.dispose(); for (const material of [lens, hidden, opaque, transparent]) material.dispose();
});

test('branch render failure restores visible lens state without altering geometry or material settings', () => {
  const root = new Group(), lens = new MeshPhysicalMaterial({transmission: .9}), geometry = new BoxGeometry();
  const mesh = new Mesh(geometry, lens); root.add(mesh);
  const original = new Float32Array(geometry.attributes.position!.array);
  const controller = new BranchLensOmission(root), failure = new Error('draw failed');
  assert.throws(() => controller.render(true, () => {throw failure;}), error => error === failure);
  assert.equal(lens.visible, true); assert.equal(lens.transmission, .9);
  assert.equal(mesh.geometry, geometry); assert.deepEqual(geometry.attributes.position!.array, original);
  assert.equal(controller.render(false, () => {}).used, false);
  lens.dispose(); geometry.dispose();
});

test('empty or originally hidden lens sets report no omission and never enable hidden materials', () => {
  const root = new Group(), lens = new MeshPhysicalMaterial({transmission: 1, visible: false});
  const geometry = new BoxGeometry(); root.add(new Mesh(geometry, lens));
  const result = new BranchLensOmission(root).render(true, () => {});
  assert.equal(result.used, false); assert.equal(result.omittedMaterials, 0); assert.match(result.fallback!, /No visible/);
  assert.equal(lens.visible, false); lens.dispose(); geometry.dispose();
});

test('new branch options default off and reject unsupported combined experiments', () => {
  assert.equal(DEFAULT_SPEED_OPTIONS.cropBranchReadback, false); assert.equal(DEFAULT_SPEED_OPTIONS.omitBranchLenses, false);
  for (const option of ['cropBranchReadback', 'omitBranchLenses'] as const) {
    assert.equal(normalizeSpeedOptions({[option]: true})[option], true);
    assert.throws(() => normalizeSpeedOptions({[option]: true, asyncTemples: true}), /independently/);
    assert.throws(() => normalizeSpeedOptions({[option]: 1} as never), /Invalid speed/);
  }
  assert.throws(() => normalizeSpeedOptions({cropBranchReadback: true, omitBranchLenses: true}), /independently/);
});
