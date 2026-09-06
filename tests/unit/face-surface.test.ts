import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { FaceSurface } from '../../src/render/face-surface.ts';
import type { Landmark } from '../../src/runtime/protocol.ts';

const canonical = JSON.parse(await readFile(new URL('../../public/models/canonical-face.json', import.meta.url), 'utf8')) as { positions: number[] };
const positions = canonical.positions.map(Math.fround);

function observation(yawDegrees: number, pitchDegrees: number, aspect: number, depth: number, scale: number, zOffset = 0.2) {
  const yaw = yawDegrees * Math.PI / 180, pitch = pitchDegrees * Math.PI / 180;
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  const tx = 1.7, ty = -0.8;
  const matrix = [
    scale * cy, 0, -scale * sy, 0,
    scale * sy * sp, scale * cp, scale * cy * sp, 0,
    scale * sy * cp, -scale * sp, scale * cy * cp, 0,
    tx, ty, -depth, 1,
  ];
  const cameraPoints: number[] = [];
  for (let index = 0; index < 468; index++) {
    const x = positions[index * 3]!, y = positions[index * 3 + 1]!;
    // These zero-weight nose vertices vary in depth without changing the
    // independently known pose in this coordinate fixture.
    const z = positions[index * 3 + 2]! + ([196, 197, 419].includes(index) ? 0.65 : 0);
    cameraPoints.push(
      scale * (cy * x + sy * sp * y + sy * cp * z) + tx,
      scale * (cp * y - sp * z) + ty,
      scale * (-sy * x + cy * sp * y + cy * cp * z) - depth,
    );
  }
  // Encode a known physical camera-space mesh into normalized observations.
  // This is the forward camera model, not the implementation's centroid formula.
  const meanDepth = cameraPoints.filter((_value, index) => index % 3 === 2).reduce((sum, z) => sum - z / 468, 0);
  const relativeDepthScale = 1 / meanDepth;
  const verticalSize = 2 * Math.tan(63 * Math.PI / 360);
  const horizontalSize = verticalSize * aspect;
  const landmarks: Landmark[] = Array.from({ length: 468 }, (_, index) => {
    const x = cameraPoints[index * 3]!, y = cameraPoints[index * 3 + 1]!, z = cameraPoints[index * 3 + 2]!;
    return {
      x: 0.5 + x / (-z * horizontalSize),
      y: 0.5 - y / (-z * verticalSize),
      z: (-z * relativeDepthScale - 1 + zOffset) / horizontalSize,
    };
  });
  return { matrix, landmarks, cameraPoints };
}


test('observed surface recovers known camera coordinates and projection at opposite yaws', () => {
  for (const [yaw, pitch, aspect, depth, scale] of [[-60, 12, 16 / 9, 42, 1], [60, -10, 9 / 16, 55, 0.93]]) {
    const expected = observation(yaw!, pitch!, aspect!, depth!, scale!);
    const surface = new FaceSurface(canonical.positions);
    assert.equal(surface.reconstruct(expected.landmarks, expected.matrix, aspect!), true);
    const height = 900, width = height * aspect!;
    const focal = height / (2 * Math.tan(63 * Math.PI / 360));
    for (let index = 0; index < 468; index++) {
      for (let axis = 0; axis < 3; axis++) {
        assert.ok(Math.abs(surface.positions[index * 3 + axis]! - expected.cameraPoints[index * 3 + axis]!) < 2e-5,
          `camera coordinate ${index}/${axis}, including the varied nose depth`);
      }
      const x = surface.positions[index * 3]!, y = surface.positions[index * 3 + 1]!, z = surface.positions[index * 3 + 2]!;
      assert.ok(Math.abs(width / 2 + focal * x / -z - expected.landmarks[index]!.x * width) < 1e-4);
      assert.ok(Math.abs(height / 2 - focal * y / -z - expected.landmarks[index]!.y * height) < 1e-4);
    }
  }
});
