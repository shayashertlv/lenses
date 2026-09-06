import type { Landmark } from '../runtime/protocol.ts';
import { VIRTUAL_CAMERA } from './projection.ts';

const NOSE_ANCHORS = [197, 6, 195] as const;
const TAN_HALF_VERTICAL_FOV = Math.tan(VIRTUAL_CAMERA.verticalFovDegrees * Math.PI / 360);
const ZERO_CORRECTION_CM = 1e-10;

export interface CorrectedBridgePose {
  matrix: number[];
  correctionCm: [number, number];
  /**
   * Pose-translation magnitude at the median nose-anchor depth, in image-height
   * units. Multiply by frame height for pixels. This is not anatomical accuracy.
   */
  correctionNormalized: number;
  /**
   * Horizontal heading of canonical +Z, in degrees. Returns 0 by diagnostic
   * convention when that axis is vertical and its horizontal heading is undefined.
   */
  yawDegrees: number;
}

function median3(values: readonly [number, number, number]): number {
  return [...values].sort((a, b) => a - b)[1]!;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Pin the canonical nose bridge to image landmarks by camera-space translation.
 * Uses the renderer's assumed 63-degree vertical FOV, square pixels, and 1 cm
 * near plane. Input coordinates must be unmirrored and use the same image crop.
 * Rotation, scale, and depth remain untouched: this does not correct pose angle
 * or personal face shape, even when the bridge lines up perfectly afterwards.
 */
export function correctedBridgePose(
  rawMatrix: readonly number[],
  landmarks: readonly Landmark[],
  canonicalPositions: readonly number[],
  aspect: number,
): CorrectedBridgePose {
  if (!Array.isArray(rawMatrix) || rawMatrix.length !== 16
    || !Array.from(rawMatrix).every(finite)) {
    throw new Error('Bridge correction requires a finite 4 x 4 pose matrix.');
  }
  if (Math.abs(rawMatrix[3]!) > 1e-8 || Math.abs(rawMatrix[7]!) > 1e-8
    || Math.abs(rawMatrix[11]!) > 1e-8 || Math.abs(rawMatrix[15]! - 1) > 1e-8) {
    throw new Error('Bridge correction requires an affine column-major pose matrix.');
  }
  if (!Array.isArray(landmarks) || landmarks.length !== 478) {
    throw new Error('Bridge correction requires a complete face landmark set.');
  }
  for (const point of landmarks) {
    if (!point || !finite(point.x) || !finite(point.y) || !finite(point.z)) {
      throw new Error('Bridge correction received invalid face landmarks.');
    }
  }
  if (!Array.isArray(canonicalPositions) || canonicalPositions.length !== 468 * 3
    || !Array.from(canonicalPositions).every(finite)) {
    throw new Error('Bridge correction requires complete finite canonical face positions.');
  }
  if (!finite(aspect) || aspect <= 0) {
    throw new Error('Bridge correction requires a positive finite image aspect ratio.');
  }

  const m = rawMatrix;
  // Reject a degenerate pose, while permitting the scale already in the matrix.
  const determinant = m[0]! * (m[5]! * m[10]! - m[9]! * m[6]!)
    - m[4]! * (m[1]! * m[10]! - m[9]! * m[2]!)
    + m[8]! * (m[1]! * m[6]! - m[5]! * m[2]!);
  if (!finite(determinant) || Math.abs(determinant) <= 1e-12) {
    throw new Error('Bridge correction received a degenerate pose orientation.');
  }

  const deltaX: [number, number, number] = [0, 0, 0];
  const deltaY: [number, number, number] = [0, 0, 0];
  const depths: [number, number, number] = [0, 0, 0];
  NOSE_ANCHORS.forEach((index, anchor) => {
    const x = canonicalPositions[index * 3]!;
    const y = canonicalPositions[index * 3 + 1]!;
    const z = canonicalPositions[index * 3 + 2]!;
    const cameraX = m[0]! * x + m[4]! * y + m[8]! * z + m[12]!;
    const cameraY = m[1]! * x + m[5]! * y + m[9]! * z + m[13]!;
    const cameraZ = m[2]! * x + m[6]! * y + m[10]! * z + m[14]!;
    if (!finite(cameraX) || !finite(cameraY) || !finite(cameraZ) || -cameraZ <= VIRTUAL_CAMERA.nearCm) {
      throw new Error(`Nose anchor ${index} is invalid or at/behind the camera near plane.`);
    }
    const depth = -cameraZ;
    const point = landmarks[index]!;
    const targetX = (2 * point.x - 1) * depth * TAN_HALF_VERTICAL_FOV * aspect;
    const targetY = (1 - 2 * point.y) * depth * TAN_HALF_VERTICAL_FOV;
    deltaX[anchor] = targetX - cameraX;
    deltaY[anchor] = targetY - cameraY;
    depths[anchor] = depth;
  });

  let dx = median3(deltaX);
  let dy = median3(deltaY);
  if (!deltaX.every(finite) || !deltaY.every(finite) || !finite(dx) || !finite(dy)) {
    throw new Error('Bridge correction produced a nonfinite translation.');
  }
  if (Math.hypot(dx, dy) <= ZERO_CORRECTION_CM) {
    dx = 0;
    dy = 0;
  }
  const matrix = rawMatrix.slice();
  if (dx !== 0 || dy !== 0) {
    matrix[12] = matrix[12]! + dx;
    matrix[13] = matrix[13]! + dy;
  }
  const correctionNormalized = Math.hypot(dx, dy) / (2 * TAN_HALF_VERTICAL_FOV * median3(depths));
  if (!finite(matrix[12]) || !finite(matrix[13]) || !finite(correctionNormalized)) {
    throw new Error('Bridge correction produced an invalid corrected pose.');
  }
  return {
    matrix,
    correctionCm: [dx, dy],
    correctionNormalized,
    yawDegrees: Math.hypot(m[8]!, m[10]!) <= 1e-12
      ? 0 : Math.atan2(m[8]!, m[10]!) * 180 / Math.PI,
  };
}
