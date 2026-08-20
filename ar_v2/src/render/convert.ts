/**
 * The one place computer-vision convention becomes renderer convention.
 *
 * `core/` works in CV convention: +X right, +Y **down**, +Z **into the scene**.
 * three.js works in GL convention: +X right, +Y **up**, -Z into the scene. The
 * two differ by a rotation of pi about X.
 *
 * **Every conversion in this tree happens in this file and nowhere else.** That
 * is a hard rule, and it is a direct reaction to what v1's convention drift
 * cost: because its arithmetic ran in the renderer's convention, every
 * projection carried an internal sign flip, every camera depth was negative, and
 * the one place it genuinely mattered — walking a ray out to a borrowed depth —
 * needed a paragraph of comment explaining why it divided by a negative number.
 * Nothing there was wrong; it was just impossible to review, because the
 * convention was implicit in a hundred places instead of explicit in one.
 *
 * If you find yourself writing a minus sign on a Y or a Z somewhere else in
 * `render/`, the conversion has leaked and this file is the fix.
 */

import type { Intrinsics } from '../core/camera.js';
import type { Mat3, Pose } from '../core/linalg.js';

/** The CV -> GL basis change, as a diagonal: (1, -1, -1). */
export const CV_TO_GL = Object.freeze([1, -1, -1]);

/**
 * A pose (model -> camera, CV) as a column-major 4x4 model matrix for three.js.
 *
 * Derivation, once: a point in GL camera space is `F * X_cv` with
 * `F = diag(1, -1, -1)`. So the GL model matrix is `F * [R | t]`, i.e. negate
 * rows 1 and 2 of both the rotation and the translation. `F` is its own inverse,
 * which is why the reverse conversion below is the same operation.
 */
export function poseToGLMatrix(out: Float32Array, pose: Pose): Float32Array {
  const R = pose.R;
  const s = CV_TO_GL;
  // three.js Matrix4 elements are COLUMN-major: out[col * 4 + row].
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 3; row++) {
      out[col * 4 + row] = s[row] * R[row * 3 + col];
    }
    out[col * 4 + 3] = 0;
  }
  out[12] = s[0] * pose.t[0];
  out[13] = s[1] * pose.t[1];
  out[14] = s[2] * pose.t[2];
  out[15] = 1;
  return out;
}

/** The inverse: a three.js column-major model matrix back into a CV pose. */
export function glMatrixToPose(out: Pose, m: ArrayLike<number>): Pose {
  const s = CV_TO_GL;
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 3; row++) {
      out.R[row * 3 + col] = s[row] * m[col * 4 + row];
    }
  }
  out.t[0] = s[0] * m[12];
  out.t[1] = s[1] * m[13];
  out.t[2] = s[2] * m[14];
  return out;
}

/**
 * The vertical field of view, in degrees, that makes a three.js
 * `PerspectiveCamera` match the solved intrinsics.
 *
 * This is the number v1 hardcoded to 63 and could only verify for
 * self-consistency. Here it comes from the scan's own solve, and the difference
 * matters exactly as v1's own note said it would: *"if our virtual camera
 * disagrees, the frame will sit correctly at one distance and slide off the face
 * at every other."*
 */
export const verticalFovDegFor = (k: Intrinsics): number =>
  (2 * Math.atan(k.height / (2 * k.f)) * 180) / Math.PI;

/**
 * Whether the intrinsics need an off-centre projection.
 *
 * A `PerspectiveCamera` assumes the principal point is the image centre. When
 * the solve moves it — which it only does if asked — the camera needs a sheared
 * projection instead, and silently ignoring the offset puts a slowly-growing
 * error into everything. Returns the offset in normalised device units, or null
 * when the principal point is central to within a tenth of a pixel.
 */
export function principalPointOffset(k: Intrinsics): { x: number; y: number } | null {
  const dx = k.cx - k.width / 2;
  const dy = k.cy - k.height / 2;
  if (Math.abs(dx) < 0.1 && Math.abs(dy) < 0.1) return null;
  return { x: (2 * dx) / k.width, y: (-2 * dy) / k.height };
}

/** Millimetres to the renderer's own unit. Kept explicit so it is greppable. */
export const MM_TO_SCENE = 1;

/** Copies a CV rotation into a three.js column-major 3x3. */
export function rotationToGL(out: Float32Array, R: Mat3): Float32Array {
  const s = CV_TO_GL;
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 3; row++) out[col * 3 + row] = s[row] * R[row * 3 + col];
  }
  return out;
}

/**
 * Positions from CV into GL, in place-safe form.
 *
 * Used for the occluder mesh and the frame geometry, both of which are authored
 * in face space (CV) and drawn by three.js (GL).
 */
export function positionsToGL(out: Float32Array, positions: ArrayLike<number>): Float32Array {
  for (let i = 0; i < out.length; i += 3) {
    out[i] = positions[i];
    out[i + 1] = -positions[i + 1];
    out[i + 2] = -positions[i + 2];
  }
  return out;
}
