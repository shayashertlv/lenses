import type { Landmark } from '../runtime/detector.ts';
import { VIRTUAL_CAMERA } from './projection.ts';

/**
 * Weights: Copyright 2023 The MediaPipe Authors. Licensed under the Apache
 * License, Version 2.0: https://www.apache.org/licenses/LICENSE-2.0
 * Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
 *
 * Verified against geometry_pipeline_metadata_landmarks.binarypb INSIDE the
 * pinned public/models/face_landmarker.task, SHA-256:
 * bdbcda96dfcb7da883da124aaa2c55dee49770d934f0fcc71747f8c21bdc75b4
 * All 468 canonical XYZ vertices match canonical-face.json after float32
 * conversion; all 2694 triangle indices match exactly. input_source = 1.
 *
 * Upstream metadata/schema:
 * https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/cc/vision/face_geometry/data/geometry_pipeline_metadata_landmarks.pbtxt
 */
export const FACE_PROCRUSTES_WEIGHTS: readonly (readonly [number, number])[] = Object.freeze([
  [4, 0.07090993970632553], [6, 0.032100144773721695],
  [10, 0.00844655092805624], [33, 0.05872416868805885],
  [54, 0.007667080033570528], [67, 0.00907805934548378],
  [117, 0.009791937656700611], [119, 0.014565368182957172],
  [121, 0.01859136112034321], [127, 0.005197994410991669],
  [129, 0.12062520533800125], [132, 0.005560018587857485],
  [133, 0.05328618362545967], [136, 0.06689045578241348],
  [143, 0.014816547743976116], [147, 0.014262833632528782],
  [198, 0.025462191551923752], [205, 0.04725227877497673],
  [263, 0.05872416868805885], [284, 0.007667080033570528],
  [297, 0.00907805934548378], [346, 0.009791937656700611],
  [348, 0.014565368182957172], [350, 0.01859136112034321],
  [356, 0.005197994410991669], [358, 0.12062520533800125],
  [361, 0.005560018587857485], [362, 0.05328618362545967],
  [365, 0.06689045578241348], [372, 0.014816547743976116],
  [376, 0.014262833632528782], [420, 0.025462191551923752],
  [425, 0.04725227877497673],
].map(pair => Object.freeze(pair as [number, number])));

const POINT_COUNT = 468;
const NEAR = VIRTUAL_CAMERA.nearCm;
const FRUSTUM_HEIGHT = 2 * NEAR * Math.tan(VIRTUAL_CAMERA.verticalFovDegrees * Math.PI / 360);
const WEIGHT_SUM = FACE_PROCRUSTES_WEIGHTS.reduce((sum, [, weight]) => sum + weight, 0);

/**
 * Reconstructs the camera-space surface used by MediaPipe's face-landmark
 * geometry pipeline. It retains the detector's learned relative depth; it does
 * not measure physical depth or personalize the eyewear asset.
 *
 * Upstream geometry_pipeline.cc ProjectXY/MoveAndRescaleZ/UnprojectXY gives:
 *   Z_i = -(screenZ_i - mean(screenZ) + near) / totalScale.
 * Upstream procrustes_solver.cc computes translation as the weighted mean of
 * (target - scale*rotation*canonical), hence:
 *   weightedMean(target) = rawPose * weightedMean(canonical).
 * Taking the Z component recovers totalScale without repeating either SVD.
 *
 * Sources:
 * https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/cc/vision/face_geometry/libs/geometry_pipeline.cc
 * https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/cc/vision/face_geometry/libs/procrustes_solver.cc
 */
export class FaceSurface {
  /** Camera-space centimeters. Attach directly to an identity scene parent. */
  readonly positions = new Float32Array(POINT_COUNT * 3);
  private readonly scratch = new Float32Array(POINT_COUNT * 3);
  private readonly weightedCanonical: readonly [number, number, number];

  constructor(canonicalPositions: ArrayLike<number>) {
    if (canonicalPositions.length !== POINT_COUNT * 3) throw new Error('The face surface needs 468 canonical vertices.');
    for (let i = 0; i < canonicalPositions.length; i++) {
      if (!Number.isFinite(canonicalPositions[i])) throw new Error('The canonical face contains non-finite positions.');
    }
    const center: [number, number, number] = [0, 0, 0];
    for (const [index, weight] of FACE_PROCRUSTES_WEIGHTS) {
      for (let axis = 0; axis < 3; axis++) {
        // The model metadata is float32; the JSON preserves OBJ decimal text.
        center[axis]! += Math.fround(canonicalPositions[index * 3 + axis]!) * weight / WEIGHT_SUM;
      }
    }
    this.weightedCanonical = center;
  }

  /**
   * Use the original, uncorrected column-major detector pose and input aspect.
   * The returned buffer is reused. Failure leaves its last valid contents intact;
   * callers must honor false rather than drawing stale camera-space geometry.
   */
  reconstruct(landmarks: readonly Landmark[], rawPose: readonly number[], aspect: number): boolean {
    if (landmarks.length < POINT_COUNT || rawPose.length !== 16 || !Number.isFinite(aspect) || aspect <= 0) return false;
    for (let i = 0; i < 16; i++) if (!Number.isFinite(rawPose[i])) return false;
    if (Math.abs(rawPose[3]!) > 1e-5 || Math.abs(rawPose[7]!) > 1e-5
        || Math.abs(rawPose[11]!) > 1e-5 || Math.abs(rawPose[15]! - 1) > 1e-5) return false;
    const frustumWidth = FRUSTUM_HEIGHT * aspect;
    let meanScreenZ = 0;
    for (let i = 0; i < POINT_COUNT; i++) {
      const point = landmarks[i];
      if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y) || !Number.isFinite(point.z)) return false;
      meanScreenZ += point.z * frustumWidth / POINT_COUNT;
    }
    let weightedScreenZ = 0;
    for (const [index, weight] of FACE_PROCRUSTES_WEIGHTS) weightedScreenZ += landmarks[index]!.z * frustumWidth * weight / WEIGHT_SUM;
    const center = this.weightedCanonical;
    const posedCenterZ = rawPose[2]! * center[0] + rawPose[6]! * center[1]
      + rawPose[10]! * center[2] + rawPose[14]!;
    if (!Number.isFinite(posedCenterZ) || posedCenterZ >= -1e-6) return false;
    const totalScale = -(weightedScreenZ - meanScreenZ + NEAR) / posedCenterZ;
    if (!Number.isFinite(totalScale) || totalScale <= 1e-9) return false;

    for (let i = 0; i < POINT_COUNT; i++) {
      const point = landmarks[i]!;
      const distance = (point.z * frustumWidth - meanScreenZ + NEAR) / totalScale;
      if (!Number.isFinite(distance) || distance <= 1e-6) return false;
      const offset = i * 3;
      this.scratch[offset] = (point.x - 0.5) * frustumWidth * distance / NEAR;
      this.scratch[offset + 1] = (0.5 - point.y) * FRUSTUM_HEIGHT * distance / NEAR;
      this.scratch[offset + 2] = -distance;
      if (!Number.isFinite(this.scratch[offset]) || !Number.isFinite(this.scratch[offset + 1])
          || !Number.isFinite(this.scratch[offset + 2])) return false;
    }
    this.positions.set(this.scratch);
    return true;
  }
}
