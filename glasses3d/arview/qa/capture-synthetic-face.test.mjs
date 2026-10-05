/** arview capture: the synthetic face detection round-trips through the renderer's own face reconstruction
 *  (qa/capture/synthetic-face.ts; src/render/face-surface.ts, src/render/pose-stabilizer.ts poseAngles). */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {Vector3} from 'three';
import {IRIS_RADIUS_CM, syntheticDetection, syntheticPoseMatrix} from './capture/synthetic-face.ts';
import {FaceSurface} from '../src/render/face-surface.ts';
import {poseAngles} from '../src/render/pose-stabilizer.ts';
import {validateDetection} from '../src/face/protocol.ts';

const canonical = JSON.parse(fs.readFileSync(new URL('../../../ar/public/models/canonical-face.json', import.meta.url), 'utf8')).positions;

test('478 landmarks, accepted by the worker protocol; the requested YXZ angles and depth read back exactly', () => {
  for (const pose of [{yaw: 0, pitch: 0, depthCm: 23}, {yaw: 30, pitch: -20, depthCm: 23}, {yaw: -45, pitch: 0, depthCm: 40}, {yaw: 12, pitch: 20, roll: 5, depthCm: 30}]) {
    const detection = syntheticDetection(canonical, 1280, 854, pose);
    assert.equal(detection.landmarks.length, 478);
    validateDetection(detection);
    const angles = poseAngles(detection.matrix);
    assert.ok(Math.abs(angles.yawDeg - pose.yaw) < 1e-9 && Math.abs(angles.pitchDeg - pose.pitch) < 1e-9
      && Math.abs(angles.rollDeg - (pose.roll ?? 0)) < 1e-9 && Math.abs(angles.depthCm - pose.depthCm) < 1e-9, JSON.stringify({pose, angles}));
  }
});

test('FaceSurface.reconstruct recovers the posed canonical points from the landmarks (camera-space cm)', () => {
  for (const [width, height, pose] of [[1280, 854, {yaw: 30, pitch: 20, depthCm: 23}], [1024, 1024, {yaw: -45, pitch: -20, depthCm: 45}]]) {
    const detection = syntheticDetection(canonical, width, height, pose);
    const surface = new FaceSurface(canonical);
    assert.equal(surface.reconstruct(detection.landmarks, detection.matrix, width / height), true);
    const matrix = syntheticPoseMatrix(pose), expected = new Vector3();
    let worst = 0;
    for (let i = 0; i < 468; i++) {
      expected.fromArray(canonical, i * 3).applyMatrix4(matrix);
      worst = Math.max(worst, Math.hypot(surface.positions[i * 3] - expected.x, surface.positions[i * 3 + 1] - expected.y, surface.positions[i * 3 + 2] - expected.z));
    }
    // float32 storage of the reconstruction; the algebra is exact.
    assert.ok(worst < 1e-4, `worst reconstruction error ${worst} cm`);
  }
});

test('the iris points form a real iris (centre + four points at the radius), not five identical points', () => {
  const detection = syntheticDetection(canonical, 1280, 854, {yaw: 0, pitch: 0, depthCm: 23});
  for (const first of [468, 473]) {
    const [centre, ...ring] = detection.landmarks.slice(first, first + 5);
    for (const point of ring) assert.ok(Math.hypot((point.x - centre.x) * 1280, (point.y - centre.y) * 854) > 5, 'ring point away from the centre');
  }
  // With a 63° vertical field of view, 5.85 mm projects to 854 * 0.585 / (2 * d * tan 31.5°) px at the iris' own camera
  // distance d = 23 cm minus the eye corners' canonical z (the eyes sit in front of the face origin).
  const centre = detection.landmarks[468], up = detection.landmarks[470];
  const eyeZ = (canonical[33 * 3 + 2] + canonical[133 * 3 + 2]) / 2;
  const expected = 854 * IRIS_RADIUS_CM / (2 * Math.tan(31.5 * Math.PI / 180) * (23 - eyeZ));
  assert.ok(Math.abs(Math.abs(up.y - centre.y) * 854 - expected) < 1.5, `${Math.abs(up.y - centre.y) * 854} vs ${expected}`);
});

test('invalid input is refused', () => {
  assert.throws(() => syntheticDetection(canonical.slice(3), 1280, 854, {yaw: 0, pitch: 0, depthCm: 23}), /468/);
  assert.throws(() => syntheticDetection(canonical, 0, 854, {yaw: 0, pitch: 0, depthCm: 23}), /empty/);
  assert.throws(() => syntheticDetection(canonical, 1280, 854, {yaw: 0, pitch: 0, depthCm: -1}), /in front/);
});
