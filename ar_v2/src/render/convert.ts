/**
 * The one place computer-vision convention becomes renderer convention.
 *
 * ## Two conventions live in `core/`, not one
 *
 * **Camera space is CV convention**: +X right, +Y **down**, +Z **into the
 * scene**. Every pose that *ends* in the camera lives there — what `track/pnp.ts`
 * solves, what the tracker carries, what `setHeadPose` is handed.
 *
 * **Face space is not.** It is the wearer's own frame: +X to the wearer's left,
 * +Y **up**, +Z **out of the face**. `core/camera.ts`'s `FACE_TO_CAMERA_FLIP =
 * diag(1, -1, -1)` *is* the difference between the two and says so in prose;
 * `fit/frame-asset.ts` declares frame-local geometry in "the same handedness as
 * face space"; and the shipped template settles it numerically — forehead
 * y = +82.6, chin y = -94.0, nose tip z = +75.9.
 *
 * three.js works in GL convention: +X right, +Y **up**, -Z into the scene. So
 * **face space and GL agree**, and the pi-about-X rotation belongs on exactly one
 * edge: face -> camera. `poseToGLMatrix` is the only function that applies it,
 * and it is only correct for a node whose parent is the GL camera. Anything
 * hanging *below* such a node — the seat, the occluder, frame geometry — is
 * already inside a flipped basis and must be written with `poseToUnflippedMatrix`
 * or a plain copy. Applying the flip twice mirrors the child in Y and Z: measured
 * with a real `solveSeat`, the right lens centre landed at
 * (-31.74, -20.13, -460.34) instead of (-31.74, 20.13, -339.66) — 127 mm out,
 * below and behind the head. That is not a subtle wrongness, and it shipped,
 * because this header used to say `core/` was CV throughout.
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

/**
 * The CV-camera -> GL basis change, as a diagonal: (1, -1, -1).
 *
 * The same three numbers as `core/camera.ts`'s `FACE_TO_CAMERA_FLIP`, and for
 * the same reason: face space and GL agree, so "undo the face-to-camera flip"
 * and "convert camera space to GL" are one operation. It applies to camera-space
 * quantities only.
 */
export const CV_TO_GL = Object.freeze([1, -1, -1]);

/**
 * A pose (model -> camera, CV) as a column-major 4x4 model matrix for three.js.
 *
 * Derivation, once: a point in GL camera space is `F * X_cv` with
 * `F = diag(1, -1, -1)`. So the GL model matrix is `F * [R | t]`, i.e. negate
 * rows 1 and 2 of both the rotation and the translation. `F` is its own inverse,
 * which is why the reverse conversion below is the same operation.
 *
 * **Only for a node whose parent is the GL camera** — in this tree that is
 * `headNode` and nothing else. A pose between two face-space frames goes through
 * `poseToUnflippedMatrix`; see the header for what the double flip costs.
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

/**
 * A face-space pose as a column-major 4x4 for a node that is *already* below a
 * flipped one — no basis change, because there is none left to make.
 *
 * It exists as a named function rather than an inline loop so the contrast with
 * `poseToGLMatrix` is visible in one screen: the seat is face-space-to-face-space
 * and face space agrees with GL, so the only correct thing to write is `[R | t]`
 * verbatim. Writing `F.S` here (or `F.S.F`, which was proposed and is equally
 * wrong) is the 127 mm error in the header.
 */
export function poseToUnflippedMatrix(out: Float32Array, pose: Pose): Float32Array {
  const R = pose.R;
  // three.js Matrix4 elements are COLUMN-major: out[col * 4 + row].
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 3; row++) {
      out[col * 4 + row] = R[row * 3 + col];
    }
    out[col * 4 + 3] = 0;
  }
  out[12] = pose.t[0];
  out[13] = pose.t[1];
  out[14] = pose.t[2];
  out[15] = 1;
  return out;
}

/**
 * The inverse of `poseToGLMatrix`: a three.js column-major model matrix back
 * into a CV camera pose. Same caveat — the matrix has to be one that carries the
 * flip, i.e. a `headNode`-level matrix.
 *
 * Not yet wired: nothing in the tree reads a pose back out of the scene graph.
 * Kept because the round trip is the cheapest test there is of the convention
 * above, and deleting it would leave that untestable.
 */
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

/**
 * Millimetres to the renderer's own unit. Kept explicit so it is greppable.
 *
 * Not yet wired: it is 1, so every call site would be a no-op multiply and none
 * exist. It stays because the day it is not 1 the grep has to find something.
 */
export const MM_TO_SCENE = 1;

/**
 * Copies a CV *camera-space* rotation into a three.js column-major 3x3.
 *
 * Not yet wired — no normal matrix is built anywhere. Same rule as
 * `poseToGLMatrix`: a rotation between two face-space frames must NOT go through
 * this, because face space and GL already agree.
 */
export function rotationToGL(out: Float32Array, R: Mat3): Float32Array {
  const s = CV_TO_GL;
  for (let col = 0; col < 3; col++) {
    for (let row = 0; row < 3; row++) out[col * 3 + row] = s[row] * R[row * 3 + col];
  }
  return out;
}

/**
 * The occluder's pose: the head's GL matrix, pushed along the camera axis.
 *
 * Stage 2 of the occlusion plan. The occluder renders the scanned face
 * depth-only, and the depth-bias knob was read off the stage-0 instrument's
 * sweep rather than asserted — see `OCCLUDER_BIAS_MM` in `render/scene.ts` for
 * the numbers. The SIGN CONVENTION is the instrument's, not GL's: **negative
 * bias moves the occluder TOWARD the camera** (hides more, buys X-ray down at
 * the price of forgiven hides). The GL camera sits at the origin looking down
 * -Z, head points have negative z, so "toward the camera" is +z and the
 * translation cell gets `-biasMm`. That double negative is exactly the kind of
 * thing that silently inverts, which is why the sign is pinned by its own test
 * in `tests/core.test.ts` rather than by this paragraph.
 *
 * A translation of the whole matrix rather than a per-vertex push along view
 * rays: at 300-600 mm of subject distance the rays through the face differ
 * from the optical axis by a few degrees, so the approximation error on a
 * 0.5 mm bias is microns — MediaPipe ships the same shortcut.
 */
export function occluderBiasedMatrix(
  out: Float32Array, glHead: Float32Array, biasMm: number,
): Float32Array {
  out.set(glHead);
  out[14] -= biasMm * MM_TO_SCENE;
  return out;
}

// There used to be a `positionsToGL` here, exported, with a docstring saying it
// was "used for the occluder mesh and the frame geometry". It had zero callers,
// and it was a trap for the first one: both of those are authored in FACE space,
// not camera space, so negating their Y and Z would have rotated them pi about X
// underneath a `headNode` that already carries that rotation. On the shipped
// template that puts the chin at y = +94.0 and the forehead at y = -82.6 — chin
// above brow — and the nose tip at z = -75.9, 151.8 mm behind the face it belongs
// on. Vertex positions in face space go to three.js unchanged, so nothing
// replaced it.
