/**
 * The camera model, and its derivatives.
 *
 * ## The convention, stated once
 *
 * `core/` works in **computer-vision convention**: right-handed, +X right,
 * +Y **down**, +Z **into the scene**. A point in front of the camera has
 * positive z. three.js uses +Y up and -Z forward; the conversion happens in
 * exactly one place (`render/frame-convert.ts`) and nowhere else.
 *
 * This is a deliberate reversal of v1, which did all its arithmetic in the
 * renderer's convention because the renderer was there first. The cost was that
 * every projection had a sign flip inside it and every depth was negative, and
 * the one place it mattered — walking a ray to a borrowed depth — had to divide
 * by a negative z and got a comment explaining why. Solvers are the harder
 * consumer; they get the natural convention and the renderer pays the
 * conversion, once.
 *
 * ## Why the intrinsics are estimated, not assumed
 *
 * v1 hardcoded MediaPipe's assumed 63-degree vertical field of view and its
 * harness verified that the pipeline was *self-consistent* with that number.
 * Self-consistency is not truth: if the real camera is 55 degrees, every stage
 * agrees with every other stage and the frame still sits correctly at exactly
 * one distance and slides at all others. Focal length is observable from a
 * rigid object seen at varying depth — which is why the enrollment protocol has
 * a lean-in/lean-out beat (see `enroll/protocol.ts`). It is solved, and its
 * uncertainty is reported.
 */

import {
  type Mat3, type Pose, type Vec3, clamp, eulerYXZ, m3, m3apply,
  mat3FromEulerYXZ, v3,
} from './linalg.js';

export interface Intrinsics {
  /** Focal length in pixels. One number: webcam pixels are square to well under
   *  a percent, and estimating fx and fy separately is near-degenerate with
   *  scale on a single rigid object. */
  f: number;
  /** Principal point, pixels. Defaults to the image centre. */
  cx: number;
  cy: number;
  /** Single radial term. Zero disables distortion entirely (and skips its cost). */
  k1: number;
  /** Image size the above are expressed in. */
  width: number;
  height: number;
}

export function intrinsicsFromFov(
  width: number, height: number, verticalFovDeg: number,
): Intrinsics {
  const f = height / 2 / Math.tan((verticalFovDeg * Math.PI) / 180 / 2);
  return { f, cx: width / 2, cy: height / 2, k1: 0, width, height };
}

export const verticalFovDeg = (k: Intrinsics): number =>
  (2 * Math.atan(k.height / 2 / k.f) * 180) / Math.PI;

/**
 * MediaPipe's assumed camera, kept as a *starting point* for the solve and as
 * the fallback when no enrollment has run. Named so that any code still relying
 * on the assumption says so out loud.
 */
export const MEDIAPIPE_ASSUMED_VERTICAL_FOV = 63;

export function scaleIntrinsics(k: Intrinsics, width: number, height: number): Intrinsics {
  const s = width / k.width;
  return {
    f: k.f * s,
    cx: k.cx * s,
    cy: k.cy * (height / k.height),
    k1: k.k1,
    width,
    height,
  };
}

// ------------------------------------------------------------- projection

/** Projects a camera-space point to pixels. Returns false if behind the camera. */
export function project(out: Float64Array, k: Intrinsics, X: ArrayLike<number>, o = 0): boolean {
  const z = X[o + 2];
  if (!(z > 1e-6)) return false;
  const xn = X[o] / z;
  const yn = X[o + 1] / z;
  if (k.k1 !== 0) {
    const r2 = xn * xn + yn * yn;
    const d = 1 + k.k1 * r2;
    out[0] = k.f * xn * d + k.cx;
    out[1] = k.f * yn * d + k.cy;
  } else {
    out[0] = k.f * xn + k.cx;
    out[1] = k.f * yn + k.cy;
  }
  return true;
}

/** Projects a model point through a pose. Writes the camera-space point too. */
export function projectPosed(
  outPx: Float64Array, outCam: Vec3, k: Intrinsics, pose: Pose, X: ArrayLike<number>, o = 0,
): boolean {
  const R = pose.R;
  const x = X[o], y = X[o + 1], z = X[o + 2];
  outCam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
  outCam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
  outCam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
  return project(outPx, k, outCam);
}

/**
 * The ray direction through a pixel, in camera space, normalised.
 *
 * Undistortion is a fixed-point iteration rather than a closed form: for a
 * single radial term the inverse has a closed form only as the root of a cubic,
 * and the iteration converges in three steps at any distortion a webcam
 * actually has. Five is the cap because a lens bad enough to need more is a
 * lens whose k1 model is wrong anyway.
 */
export function rayThrough(out: Vec3, k: Intrinsics, px: number, py: number): Vec3 {
  let xn = (px - k.cx) / k.f;
  let yn = (py - k.cy) / k.f;
  if (k.k1 !== 0) {
    const dx = xn, dy = yn;
    for (let i = 0; i < 5; i++) {
      const r2 = xn * xn + yn * yn;
      const d = 1 + k.k1 * r2;
      if (Math.abs(d) < 1e-9) break;
      const nx = dx / d, ny = dy / d;
      if (Math.abs(nx - xn) < 1e-12 && Math.abs(ny - yn) < 1e-12) { xn = nx; yn = ny; break; }
      xn = nx; yn = ny;
    }
  }
  const l = Math.hypot(xn, yn, 1);
  out[0] = xn / l; out[1] = yn / l; out[2] = 1 / l;
  return out;
}

/** The point at a given camera-space depth along the ray through a pixel. */
export function pointAtDepth(out: Vec3, k: Intrinsics, px: number, py: number, z: number): Vec3 {
  rayThrough(out, k, px, py);
  const s = z / out[2];
  out[0] *= s; out[1] *= s; out[2] = z;
  return out;
}

// -------------------------------------------------------------- jacobians

/**
 * d(u,v) / d(X_cam), 2x3, row-major into `J` at `off`.
 *
 * Written out rather than differenced. A numeric jacobian is 6 extra
 * projections per residual per iteration, and on a 150-frame bundle that is the
 * difference between a scan that finishes while the user is still looking at
 * the dot and one that does not. `tests/jacobians.test.ts` checks every analytic
 * jacobian in the tree against central differences, which is the discipline
 * that makes writing them by hand safe.
 */
export function dProjDPoint(J: Float64Array, off: number, k: Intrinsics, X: ArrayLike<number>): void {
  const x = X[0], y = X[1], z = X[2];
  const zi = 1 / z;
  const xn = x * zi, yn = y * zi;

  if (k.k1 === 0) {
    J[off + 0] = k.f * zi;   J[off + 1] = 0;          J[off + 2] = -k.f * xn * zi;
    J[off + 3] = 0;          J[off + 4] = k.f * zi;   J[off + 5] = -k.f * yn * zi;
    return;
  }

  const r2 = xn * xn + yn * yn;
  const d = 1 + k.k1 * r2;
  // d(u)/d(xn) = f * (d + xn * dd/dxn), dd/dxn = 2 k1 xn
  const duxn = k.f * (d + 2 * k.k1 * xn * xn);
  const duyn = k.f * (2 * k.k1 * xn * yn);
  const dvxn = k.f * (2 * k.k1 * xn * yn);
  const dvyn = k.f * (d + 2 * k.k1 * yn * yn);
  // d(xn)/dX = (zi, 0, -xn*zi); d(yn)/dX = (0, zi, -yn*zi)
  J[off + 0] = duxn * zi;
  J[off + 1] = duyn * zi;
  J[off + 2] = -(duxn * xn + duyn * yn) * zi;
  J[off + 3] = dvxn * zi;
  J[off + 4] = dvyn * zi;
  J[off + 5] = -(dvxn * xn + dvyn * yn) * zi;
}

/**
 * d(u,v) / d(pose increment), 2x6, in the order (wx, wy, wz, tx, ty, tz).
 *
 * `Prot` must be `R * X_model` — the rotated point WITHOUT the translation.
 * This is the half of the convention that is easy to get wrong: because
 * `poseOplus` applies `R <- exp(w) R` and `t <- t + u`, the rotation increment
 * acts on the rotated point only, and using the full camera-space point here
 * produces a jacobian that is correct at the origin and increasingly wrong as
 * the head moves away from it — i.e. it works on a test rig and degrades on a
 * real capture, which is the worst possible failure shape.
 */
export function dProjDPose(
  J: Float64Array, off: number, k: Intrinsics, Xcam: ArrayLike<number>, Prot: ArrayLike<number>,
): void {
  const dPt = new Float64Array(6);
  dProjDPoint(dPt, 0, k, Xcam);

  // d(Xcam)/d(w) = -skew(Prot)
  const px = Prot[0], py = Prot[1], pz = Prot[2];
  // skew(P) = [ 0, -pz, py; pz, 0, -px; -py, px, 0 ]; we need its negation.
  const S = Float64Array.of(
    0, pz, -py,
    -pz, 0, px,
    py, -px, 0,
  );

  for (let r = 0; r < 2; r++) {
    for (let c = 0; c < 3; c++) {
      J[off + r * 6 + c] =
        dPt[r * 3 + 0] * S[0 * 3 + c] +
        dPt[r * 3 + 1] * S[1 * 3 + c] +
        dPt[r * 3 + 2] * S[2 * 3 + c];
    }
    // d(Xcam)/d(t) = I
    J[off + r * 6 + 3] = dPt[r * 3 + 0];
    J[off + r * 6 + 4] = dPt[r * 3 + 1];
    J[off + r * 6 + 5] = dPt[r * 3 + 2];
  }
}

/** d(u,v) / d(X_model), 2x3 — the point jacobian through the pose rotation. */
export function dProjDModelPoint(
  J: Float64Array, off: number, k: Intrinsics, Xcam: ArrayLike<number>, R: Mat3,
): void {
  const dPt = new Float64Array(6);
  dProjDPoint(dPt, 0, k, Xcam);
  for (let r = 0; r < 2; r++) {
    for (let c = 0; c < 3; c++) {
      J[off + r * 3 + c] =
        dPt[r * 3 + 0] * R[0 * 3 + c] +
        dPt[r * 3 + 1] * R[1 * 3 + c] +
        dPt[r * 3 + 2] * R[2 * 3 + c];
    }
  }
}

/** Which intrinsic parameters a bundle is allowed to move. */
export interface IntrinsicsMask {
  f: boolean;
  pp: boolean;
  k1: boolean;
}

export const INTRINSICS_FREE_F: IntrinsicsMask = { f: true, pp: false, k1: false };
export const INTRINSICS_FIXED: IntrinsicsMask = { f: false, pp: false, k1: false };

export const intrinsicsDof = (mask: IntrinsicsMask): number =>
  (mask.f ? 1 : 0) + (mask.pp ? 2 : 0) + (mask.k1 ? 1 : 0);

/**
 * d(u,v) / d(intrinsics), 2 x dof, in the mask's declared order: f, cx, cy, k1.
 */
export function dProjDIntrinsics(
  J: Float64Array, off: number, stride: number,
  k: Intrinsics, Xcam: ArrayLike<number>, mask: IntrinsicsMask,
): void {
  const zi = 1 / Xcam[2];
  const xn = Xcam[0] * zi, yn = Xcam[1] * zi;
  const r2 = xn * xn + yn * yn;
  const d = k.k1 === 0 ? 1 : 1 + k.k1 * r2;

  let c = 0;
  if (mask.f) {
    J[off + 0 * stride + c] = xn * d;
    J[off + 1 * stride + c] = yn * d;
    c++;
  }
  if (mask.pp) {
    J[off + 0 * stride + c] = 1; J[off + 1 * stride + c] = 0; c++;
    J[off + 0 * stride + c] = 0; J[off + 1 * stride + c] = 1; c++;
  }
  if (mask.k1) {
    J[off + 0 * stride + c] = k.f * xn * r2;
    J[off + 1 * stride + c] = k.f * yn * r2;
    c++;
  }
}

export function applyIntrinsicsDelta(
  k: Intrinsics, delta: ArrayLike<number>, off: number, mask: IntrinsicsMask,
): Intrinsics {
  let c = 0;
  const out: Intrinsics = { ...k };
  if (mask.f) { out.f = Math.max(k.f + delta[off + c], k.width * 0.2); c++; }
  if (mask.pp) { out.cx = k.cx + delta[off + c]; c++; out.cy = k.cy + delta[off + c]; c++; }
  if (mask.k1) { out.k1 = clamp(k.k1 + delta[off + c], -0.6, 0.6); c++; }
  return out;
}

// ------------------------------------------------------------------ misc

/**
 * The wearer's own yaw / pitch / roll, radians — turn, nod and tilt as a person
 * would describe them.
 *
 * **Not `eulerYXZ(pose.R)`.** That extracts the euler of the *model-to-camera*
 * rotation, and face space (+Y up, +Z out of the face) differs from CV camera
 * space (+Y down, +Z forward) by a rotation of pi about X. So a head looking
 * straight at the camera is not the identity in that frame — it IS that pi
 * rotation — and the numbers that come out are relative to a flipped basis:
 *
 *     a frontal face      reads yaw = -180, roll = -180
 *     looking down 16 deg reads pitch = -16       (sign inverted)
 *     turning right       reads yaw near -180 rather than near -30
 *
 * That is not a cosmetic difference. It shipped, and it made the guided scan
 * impossible to complete: the protocol's `nod-down` beat asks for
 * `|yaw| < 20 AND pitch >= 12`, the first clause is never true at yaw = -180,
 * and the wearer was left looking down at a prompt that would never advance.
 * Meanwhile `turn-right` (`yaw <= -30`) was satisfied instantly, without the
 * wearer turning at all. The keyframe coverage check reported a yaw span of
 * 360 degrees for a capture that never left frontal — a number I saw in the
 * very first run and read as "the sweep worked" instead of "that is impossible".
 *
 * Undoing the flip first costs one 3x3 multiply and makes every angle mean what
 * its name says. `FACE_TO_CAMERA_FLIP` is its own inverse, which is why the same
 * constant appears on both sides of the conversion.
 *
 * Sign conventions, stated so no caller has to guess:
 *   yaw   > 0  the wearer turned to their own LEFT
 *   pitch > 0  the wearer looked DOWN
 *   roll  > 0  the wearer tipped their head toward their own RIGHT shoulder
 */
export const FACE_TO_CAMERA_FLIP: Mat3 = Float64Array.of(1, 0, 0, 0, -1, 0, 0, 0, -1);

export function headEuler(pose: Pose): { yaw: number; pitch: number; roll: number } {
  // LEFT-multiply. `pose.R` maps model to camera, and a head rotation happens in
  // MODEL space, so the pose factorises as `R = FLIP * R_head` and the head's own
  // rotation is recovered by `FLIP * R` (FLIP being its own inverse).
  //
  // Right-multiplying instead — `R * FLIP` — returns the CONJUGATE
  // `FLIP * R_head * FLIP`, which for a rotation about Y or Z is that rotation
  // NEGATED. So it gets pitch right and silently inverts yaw and roll.
  //
  // That shipped, and it is worth understanding why no test caught it: the
  // synthetic capture generator made the same mistake, composing `R_euler * FLIP`
  // instead of `FLIP * R_euler`. The two errors cancel exactly, so the whole
  // synthetic pipeline was self-consistent and wrong together — 56 passing tests
  // over a system that inverted head yaw on every real face. The fix for the
  // TEST is in `tests/protocol.test.ts`: the expected sign is now derived from
  // where landmarks land in the image, which is an observable and cannot be
  // conjugated away.
  const R = pose.R;
  const F = FACE_TO_CAMERA_FLIP;
  const head = new Float64Array(9);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      head[r * 3 + c] = F[r * 3] * R[c] + F[r * 3 + 1] * R[3 + c] + F[r * 3 + 2] * R[6 + c];
    }
  }
  return eulerYXZ(head);
}

/**
 * Builds a model-to-camera pose rotation for a head at the given angles.
 *
 * The inverse of `headEuler`, and it exists so that anything constructing a
 * synthetic pose composes it the same way a real one factorises. Every place
 * that built one by hand got the side wrong at least once.
 */
export function poseRotationFromHeadEuler(
  out: Mat3, yaw: number, pitch: number, roll: number,
): Mat3 {
  const head = m3();
  mat3FromEulerYXZ(head, yaw, pitch, roll);
  const F = FACE_TO_CAMERA_FLIP;
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r * 3 + c] = F[r * 3] * head[c] + F[r * 3 + 1] * head[3 + c] + F[r * 3 + 2] * head[6 + c];
    }
  }
  return out;
}

/** The camera centre expressed in the model's own frame. */
export function cameraInModel(out: Vec3, pose: Pose): Vec3 {
  // camera centre is at origin in camera space; X_model = R^T (0 - t)
  const t = v3(-pose.t[0], -pose.t[1], -pose.t[2]);
  const RT = Float64Array.of(
    pose.R[0], pose.R[3], pose.R[6],
    pose.R[1], pose.R[4], pose.R[7],
    pose.R[2], pose.R[5], pose.R[8],
  );
  return m3apply(out, RT, t);
}

/**
 * The angle, in radians, between the camera's view ray at a model point and the
 * model's own +Z axis. This is the parallax angle the enrollment bundle
 * accumulates — and unlike v1, nothing here penalises it. It is the signal.
 */
export function viewAngleAt(pose: Pose, modelPoint: ArrayLike<number>): number {
  const c = v3();
  cameraInModel(c, pose);
  const dx = modelPoint[0] - c[0];
  const dy = modelPoint[1] - c[1];
  const dz = modelPoint[2] - c[2];
  const l = Math.hypot(dx, dy, dz);
  if (l < 1e-9) return 0;
  return Math.acos(clamp(Math.abs(dz) / l, 0, 1));
}
