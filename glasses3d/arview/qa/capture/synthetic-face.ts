/** The synthetic face detection of the posed captures (pure; port of the pre-WP0 prototype's ar_capture/page.js
 *  detection(), deleted 2026-10-13; see git tag review-20261013-pre-cleanup).
 *
 *  The 468 canonical-face positions (centimetres, ../../ar/public/models/canonical-face.json, verified against the
 *  pinned face_landmarker.task) are posed with compose(T = (0, 0, -depth), R = Euler(pitch, yaw, roll, 'YXZ'), S = 1)
 *  and projected through the MediaPipe virtual camera (63° vertical field of view, src/render/projection.ts):
 *  x = .5 + X / (-Z h), y = .5 - Y / (-Z v), z = (-Z / meanDepth - 1) / h, with v = 2 tan(31.5°) and h = v W / H.
 *  FaceSurface.reconstruct (src/render/face-surface.ts) recovers exactly these camera-space points, and poseAngles reads
 *  the requested angles back (YXZ). The renderer requires 478 landmarks (src/face/protocol.ts); nothing reads the ten
 *  iris points, but unlike the prototype's five identical points per eye they form a real iris: centre plus four points
 *  at an 5.85 mm radius (an 11.7 mm iris) in the face's own plane, so an iris-based measure would not silently see zero. */
import {Euler, Matrix4, Quaternion, Vector3} from 'three';

export const VERTICAL_FOV_DEGREES = 63;
export const IRIS_RADIUS_CM = 0.585;
/** Eye-corner pairs whose canonical midpoint is each iris centre: subject right (468-472), subject left (473-477). */
export const IRIS_CORNERS: readonly (readonly [number, number])[] = Object.freeze([[33, 133], [362, 263]]);

export interface SyntheticPose {readonly yaw: number; readonly pitch: number; readonly roll?: number; readonly depthCm: number;}
export interface SyntheticLandmark {x: number; y: number; z: number;}
export interface SyntheticDetection {matrix: number[]; landmarks: SyntheticLandmark[]; inferenceMs: number;}

/** The pose matrix (column-major, camera space, centimetres) of a synthetic face. */
export function syntheticPoseMatrix(pose: SyntheticPose): Matrix4 {
  const rotation = new Quaternion().setFromEuler(new Euler(pose.pitch * Math.PI / 180, pose.yaw * Math.PI / 180, (pose.roll ?? 0) * Math.PI / 180, 'YXZ'));
  return new Matrix4().compose(new Vector3(0, 0, -pose.depthCm), rotation, new Vector3(1, 1, 1));
}

/** A complete 478-landmark detection of the canonical face at `pose`, framed for a `width` x `height` camera image. */
export function syntheticDetection(canonicalPositions: ArrayLike<number>, width: number, height: number, pose: SyntheticPose): SyntheticDetection {
  if (canonicalPositions.length !== 468 * 3) throw new Error('The canonical face needs 468 positions.');
  if (!(width > 0 && height > 0)) throw new Error('The synthetic camera frame is empty.');
  if (!(pose.depthCm > 0)) throw new Error('The synthetic face must be in front of the camera.');
  const matrix = syntheticPoseMatrix(pose);
  const camera: Vector3[] = [];
  let meanDepth = 0;
  const point = new Vector3();
  for (let i = 0; i < 468; i++) {
    point.set(canonicalPositions[i * 3]!, canonicalPositions[i * 3 + 1]!, canonicalPositions[i * 3 + 2]!).applyMatrix4(matrix);
    camera.push(point.clone()); meanDepth -= point.z / 468;
  }
  // The iris points, in the canonical face frame, then posed like every other point.
  for (const [a, b] of IRIS_CORNERS) {
    const centre = new Vector3(
      (canonicalPositions[a * 3]! + canonicalPositions[b * 3]!) / 2,
      (canonicalPositions[a * 3 + 1]! + canonicalPositions[b * 3 + 1]!) / 2,
      (canonicalPositions[a * 3 + 2]! + canonicalPositions[b * 3 + 2]!) / 2);
    for (const [dx, dy] of [[0, 0], [IRIS_RADIUS_CM, 0], [0, IRIS_RADIUS_CM], [-IRIS_RADIUS_CM, 0], [0, -IRIS_RADIUS_CM]] as const) {
      camera.push(centre.clone().add(new Vector3(dx, dy, 0)).applyMatrix4(matrix));
    }
  }
  const vertical = 2 * Math.tan(VERTICAL_FOV_DEGREES * Math.PI / 360), horizontal = vertical * width / height;
  const landmarks = camera.map(p => {
    if (!(p.z < 0)) throw new Error('A synthetic landmark is behind the camera.');
    return {x: .5 + p.x / (-p.z * horizontal), y: .5 - p.y / (-p.z * vertical), z: (-p.z / meanDepth - 1) / horizontal};
  });
  return {matrix: matrix.toArray(), landmarks, inferenceMs: 0};
}
