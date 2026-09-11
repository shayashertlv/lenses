import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { Euler, Matrix4, Vector3 } from 'three';
import { createNasalShape, NASAL_SHAPE_ID, NASAL_SHAPE_VERSION } from '../../src/render/nasal-shape.ts';

const canonical = JSON.parse(readFileSync(new URL('../../public/models/canonical-face.json', import.meta.url), 'utf8')) as {
  positions: number[]; indices: number[];
};
const shape = createNasalShape(canonical.positions, canonical.indices);
function coordinates(yaw = 0, pitch = 0, roll = 0) {
  const pose = new Matrix4().makeRotationFromEuler(new Euler(pitch, yaw, roll));
  pose.setPosition(.75, -1.25, -60);
  const surfacePositions = new Float32Array(1404), vertex = new Vector3();
  for (let i = 0; i < surfacePositions.length; i += 3) vertex.fromArray(canonical.positions, i).applyMatrix4(pose).toArray(surfacePositions, i);
  return { surfacePositions, rawMatrix: pose.toArray() };
}

test('raw nasal shape preserves frozen Option17 Float32 outputs at known frontal and both-turn poses', () => {
  assert.equal(NASAL_SHAPE_ID, 'central-wp020-dp015');
  assert.equal(NASAL_SHAPE_VERSION, 'raw-nasal-shape-v1');
  // Synthetic canonical-coordinate goldens from the frozen central-wp020-dp015
  // generator SHA256 03a4250c89ccbc753893fe87247aa6c38d6cced4a44c9f65b8350d73c6d64832.
  // Tests have no dependency on private recordings or ignored experimental code.
  const cases: readonly { angles: readonly [number, number, number]; hash: string }[] = [
    { angles: [0, 0, 0], hash: 'd2c61eef3050cb79b27aada74dac6ae4e100b6c1f1660af13a982f64d7a8b7c3' },
    { angles: [.6, 0, 0], hash: 'd7d08410b35c49d0592817ec552281fb57898f11c7abca4db24fb14c0dadc6c8' },
    { angles: [-.6, 0, 0], hash: '5189b5e30d7adf41a3aa86432d26f4fc5e1c9df2942c62cefd47f4d7ccb2147a' },
    { angles: [.6, .12, -.05], hash: '51f038de72d1eb4a01725f2845c2189d4d4910ba313fddfe7068f30912fe03ce' },
    { angles: [-.6, -.12, .05], hash: 'c5b8044ba00cd4226faeb84e8e00d093de71e48a85b8cca7a1f7a9c782cb3453' },
  ];
  for (const { angles, hash } of cases) {
    const input = coordinates(...angles), original = structuredClone(input), result = shape.apply(input);
    assert.equal(result.accepted, true);
    assert.equal(createHash('sha256').update(new Uint8Array(result.surfacePositions.buffer)).digest('hex'), hash);
    assert.deepEqual(input, original, 'original paired surface and pose remain unchanged');
    assert.equal(result.diagnostics.footprintVertexCount, 72);
    assert.equal(result.diagnostics.outputChangedVertexCount, 72);
    assert.equal(result.diagnostics.checkedTriangleCount, 178);
    assert.deepEqual(result.diagnostics.rejectionReasons, []);
  }
});

test('known central coordinates move forward while nonnasal coordinates and frontal Y stay exact', () => {
  const input = coordinates(), result = shape.apply(input);
  assert.equal(result.surfacePositions[4 * 3], input.surfacePositions[4 * 3], 'central midline does not acquire lateral width');
  assert.equal(result.surfacePositions[4 * 3 + 2], Math.fround(input.surfacePositions[4 * 3 + 2]! + .15), 'full central weight adds the fixed forward depth');
  for (const id of [10, 33, 127, 152, 263, 356]) {
    assert.deepEqual(result.surfacePositions.slice(id * 3, id * 3 + 3), input.surfacePositions.slice(id * 3, id * 3 + 3));
  }
  for (let i = 1; i < input.surfacePositions.length; i += 3) assert.equal(result.surfacePositions[i], input.surfacePositions[i]);
});

test('a rejected shape returns the exact owned raw surface and no partially shaped vertices', () => {
  const input = coordinates(); input.surfacePositions[51 * 3]! += 5;
  const original = input.surfacePositions.slice(), result = shape.apply(input);
  assert.equal(result.accepted, false);
  assert.equal(result.diagnostics.status, 'rejected');
  assert.ok(result.diagnostics.rejectionReasons.includes('local-displacement-limit'));
  assert.ok(result.diagnostics.proposedChangedVertexCount > 0);
  assert.equal(result.diagnostics.outputChangedVertexCount, 0);
  assert.deepEqual(result.diagnostics.outputChangedVertexIndices, []);
  assert.deepEqual(result.surfacePositions, original);
  assert.deepEqual(input.surfacePositions, original);
  assert.notStrictEqual(result.surfacePositions, input.surfacePositions);
});

test('the fixed near-plane guard rejects a proposal from finite initially valid raw coordinates', () => {
  const input = coordinates(); input.surfacePositions[4 * 3 + 2] = Math.fround(-1.05);
  const result = shape.apply(input);
  assert.equal(result.accepted, false);
  assert.ok(result.diagnostics.rejectionReasons.includes('near-plane'));
  assert.deepEqual(result.surfacePositions, input.surfacePositions);
});

test('malformed geometry and pose fail explicitly before any usable shape is returned', () => {
  assert.throws(() => createNasalShape(canonical.positions.slice(3), canonical.indices), /468-point canonical/);
  const badTopology = [...canonical.indices]; badTopology[0] = 468;
  assert.throws(() => createNasalShape(canonical.positions, badTopology), /triangle topology/);
  const nonFinite = coordinates(); nonFinite.surfacePositions[0] = Number.NaN;
  assert.throws(() => shape.apply(nonFinite), /finite original Float32/);
  const imprecise = coordinates(), surface = Array.from(imprecise.surfacePositions); surface[0]! += .00000000001;
  assert.throws(() => shape.apply({ ...imprecise, surfacePositions: surface }), /finite original Float32/);
  const singular = coordinates(); singular.rawMatrix[0] = 0;
  assert.throws(() => shape.apply(singular), /singular or reflected/);
  const correctedInsteadOfAffine = coordinates(); correctedInsteadOfAffine.rawMatrix[3] = 1;
  assert.throws(() => shape.apply(correctedInsteadOfAffine), /affine detector pose/);
});

test('factory inputs, application order and caller-owned outputs cannot leak across pairs', () => {
  const positions = [...canonical.positions], indices = [...canonical.indices], isolated = createNasalShape(positions, indices);
  positions.fill(0); indices.fill(0);
  const left = coordinates(-.6), right = coordinates(.6), first = isolated.apply(left), expected = first.surfacePositions.slice();
  isolated.apply(right);
  assert.deepEqual(first.surfacePositions, expected);
  first.surfacePositions.fill(0);
  assert.deepEqual(isolated.apply(left).surfacePositions, expected);
  assert.deepEqual(isolated.apply(left).surfacePositions, shape.apply(left).surfacePositions);
});
