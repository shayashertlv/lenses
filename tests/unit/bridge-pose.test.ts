import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Matrix4, PerspectiveCamera, Quaternion, Vector3 } from 'three';
import { correctedBridgePose } from '../../src/render/bridge-pose.ts';
import type { Landmark } from '../../src/runtime/protocol.ts';

const ANCHORS = [197, 6, 195] as const;
const ASPECT = 4 / 3;

function fixture(yawDegrees = 0, scale = new Vector3(1, 1, 1)) {
  const canonical = Array<number>(468 * 3).fill(0);
  for (const [i, point] of [[197, [0, 3.8, 5.8]], [6, [0, 3.2, 6.2]], [195, [0, 2.6, 6.7]]] as const) {
    canonical.splice(i * 3, 3, ...point);
  }
  const matrix = new Matrix4().compose(
    new Vector3(0.7, -0.4, -40),
    new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), yawDegrees * Math.PI / 180),
    scale,
  ).toArray();
  const landmarks: Landmark[] = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5, z: 0 }));
  for (const index of ANCHORS) {
    const point = project(canonical, matrix, index);
    landmarks[index] = { x: point.x, y: point.y, z: 0 };
  }
  return { canonical, matrix, landmarks };
}

/** Independent Three.js projection verifies the production renderer conventions. */
function project(canonical: readonly number[], matrix: readonly number[], index: number, aspect = ASPECT) {
  const camera = new PerspectiveCamera(63, aspect, 1, 10_000);
  camera.updateMatrixWorld();
  const ndc = new Vector3().fromArray(canonical, index * 3)
    .applyMatrix4(new Matrix4().fromArray(matrix)).project(camera);
  return { x: (ndc.x + 1) / 2, y: (1 - ndc.y) / 2 };
}

function anchorError(canonical: readonly number[], matrix: readonly number[], landmarks: readonly Landmark[]) {
  return Math.max(...ANCHORS.map(index => {
    const point = project(canonical, matrix, index);
    return Math.hypot(point.x - landmarks[index]!.x, point.y - landmarks[index]!.y);
  }));
}

test('bridge translation recovers known projections without changing rotation, scale, or depth', () => {
  for (const yaw of [0, -60, 60]) {
    const { canonical, matrix, landmarks } = fixture(yaw, new Vector3(1.1, 0.9, 1.2));
    const wrong = matrix.slice();
    wrong[12]! += 2.1;
    wrong[13]! -= 1.4;
    const original = wrong.slice();
    const result = correctedBridgePose(wrong, landmarks, canonical, ASPECT);
    assert.ok(anchorError(canonical, wrong, landmarks) > 0.02);
    assert.ok(anchorError(canonical, result.matrix, landmarks) < 1e-12);
    assert.ok(Math.abs(result.correctionCm[0] + 2.1) < 1e-12);
    assert.ok(Math.abs(result.correctionCm[1] - 1.4) < 1e-12);
    assert.ok(Math.abs(result.yawDegrees - yaw) < 1e-12);
    for (let i = 0; i < 16; i++) {
      if (i !== 12 && i !== 13) assert.equal(result.matrix[i], original[i]);
    }
    assert.deepEqual(wrong, original);
    assert.notEqual(result.matrix, wrong);
  }
});
