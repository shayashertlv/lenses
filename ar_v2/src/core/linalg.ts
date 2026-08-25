/**
 * The arithmetic everything else stands on.
 *
 * Three rules this file follows, and they are the reason it is hand-written
 * rather than pulled from three.js:
 *
 *  1. **Float64 everywhere.** The bundle adjustment in `enroll/` accumulates
 *     normal equations over ~150 frames x ~500 landmarks. In Float32 the
 *     information matrix loses conditioning long before the solve does, and the
 *     failure is silent — it reads as "the optimiser converged", to the wrong
 *     answer. three.js is Float32 by contract because it feeds a GPU. This
 *     layer feeds a solver.
 *
 *  2. **No allocation in a loop.** Every routine takes an explicit output
 *     argument. A BA iteration touches these functions ~10^6 times; a Vector3
 *     per call is a garbage-collector pause in the middle of a user-facing scan.
 *
 *  3. **Rotations live on the manifold.** Increments are rotation VECTORS
 *     (so(3), 3 numbers) and states are rotation MATRICES (SO(3)). This is not
 *     stylistic: parameterising a solve by euler angles or by an unnormalised
 *     quaternion puts a gimbal singularity or a gauge freedom inside the normal
 *     equations, and both show up as a solver that stalls at exactly the poses
 *     this project cares about — large yaw. `expSO3`/`logSO3` below are the
 *     whole fix, and they are why pose estimation here does not degrade at 60
 *     degrees the way an euler parameterisation does.
 *
 * Units are millimetres throughout `core/`. v1 used centimetres in face space
 * and millimetres in prose, and every constant had to be read twice to know
 * which. One unit, stated once: **mm**.
 */

export type Vec3 = Float64Array;
export type Mat3 = Float64Array; // row-major, 9
export type Quat = Float64Array; // x, y, z, w

export const v3 = (x = 0, y = 0, z = 0): Vec3 => Float64Array.of(x, y, z);
export const m3 = (): Mat3 => Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1);

export const EPS = 1e-12;

// --------------------------------------------------------------------- Vec3

export function vset(out: Vec3, x: number, y: number, z: number): Vec3 {
  out[0] = x; out[1] = y; out[2] = z; return out;
}
export function vcopy(out: Vec3, a: ArrayLike<number>, ao = 0): Vec3 {
  out[0] = a[ao]; out[1] = a[ao + 1]; out[2] = a[ao + 2]; return out;
}
export function vadd(out: Vec3, a: ArrayLike<number>, b: ArrayLike<number>): Vec3 {
  out[0] = a[0] + b[0]; out[1] = a[1] + b[1]; out[2] = a[2] + b[2]; return out;
}
export function vsub(out: Vec3, a: ArrayLike<number>, b: ArrayLike<number>): Vec3 {
  out[0] = a[0] - b[0]; out[1] = a[1] - b[1]; out[2] = a[2] - b[2]; return out;
}
export function vscale(out: Vec3, a: ArrayLike<number>, s: number): Vec3 {
  out[0] = a[0] * s; out[1] = a[1] * s; out[2] = a[2] * s; return out;
}
export function vaddScaled(out: Vec3, a: ArrayLike<number>, b: ArrayLike<number>, s: number): Vec3 {
  out[0] = a[0] + b[0] * s; out[1] = a[1] + b[1] * s; out[2] = a[2] + b[2] * s; return out;
}
export const vdot = (a: ArrayLike<number>, b: ArrayLike<number>): number =>
  a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export function vcross(out: Vec3, a: ArrayLike<number>, b: ArrayLike<number>): Vec3 {
  const x = a[1] * b[2] - a[2] * b[1];
  const y = a[2] * b[0] - a[0] * b[2];
  const z = a[0] * b[1] - a[1] * b[0];
  out[0] = x; out[1] = y; out[2] = z; return out;
}
export const vlen = (a: ArrayLike<number>): number => Math.hypot(a[0], a[1], a[2]);
export const vlen2 = (a: ArrayLike<number>): number => a[0] * a[0] + a[1] * a[1] + a[2] * a[2];
export function vnormalize(out: Vec3, a: ArrayLike<number>): Vec3 {
  const l = vlen(a);
  if (l < EPS) return vset(out, 0, 0, 0);
  return vscale(out, a, 1 / l);
}
export const vdist = (a: ArrayLike<number>, b: ArrayLike<number>): number =>
  Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

// --------------------------------------------------------------------- Mat3

/** out = A * b. Aliasing-safe. */
export function m3apply(out: Vec3, A: Mat3, b: ArrayLike<number>, bo = 0): Vec3 {
  const x = b[bo], y = b[bo + 1], z = b[bo + 2];
  out[0] = A[0] * x + A[1] * y + A[2] * z;
  out[1] = A[3] * x + A[4] * y + A[5] * z;
  out[2] = A[6] * x + A[7] * y + A[8] * z;
  return out;
}
/** out = A^T * b. */
export function m3applyT(out: Vec3, A: Mat3, b: ArrayLike<number>, bo = 0): Vec3 {
  const x = b[bo], y = b[bo + 1], z = b[bo + 2];
  out[0] = A[0] * x + A[3] * y + A[6] * z;
  out[1] = A[1] * x + A[4] * y + A[7] * z;
  out[2] = A[2] * x + A[5] * y + A[8] * z;
  return out;
}
/** out = A * B. Aliasing-safe. */
export function m3mul(out: Mat3, A: Mat3, B: Mat3): Mat3 {
  const a0 = A[0], a1 = A[1], a2 = A[2], a3 = A[3], a4 = A[4];
  const a5 = A[5], a6 = A[6], a7 = A[7], a8 = A[8];
  const b0 = B[0], b1 = B[1], b2 = B[2], b3 = B[3], b4 = B[4];
  const b5 = B[5], b6 = B[6], b7 = B[7], b8 = B[8];
  out[0] = a0 * b0 + a1 * b3 + a2 * b6;
  out[1] = a0 * b1 + a1 * b4 + a2 * b7;
  out[2] = a0 * b2 + a1 * b5 + a2 * b8;
  out[3] = a3 * b0 + a4 * b3 + a5 * b6;
  out[4] = a3 * b1 + a4 * b4 + a5 * b7;
  out[5] = a3 * b2 + a4 * b5 + a5 * b8;
  out[6] = a6 * b0 + a7 * b3 + a8 * b6;
  out[7] = a6 * b1 + a7 * b4 + a8 * b7;
  out[8] = a6 * b2 + a7 * b5 + a8 * b8;
  return out;
}
export function m3transpose(out: Mat3, A: Mat3): Mat3 {
  const a1 = A[1], a2 = A[2], a5 = A[5];
  out[0] = A[0]; out[4] = A[4]; out[8] = A[8];
  out[1] = A[3]; out[3] = a1;
  out[2] = A[6]; out[6] = a2;
  out[5] = A[7]; out[7] = a5;
  return out;
}
export function m3identity(out: Mat3): Mat3 {
  out.set([1, 0, 0, 0, 1, 0, 0, 0, 1]); return out;
}

// ------------------------------------------------------------------- SO(3)

/**
 * Rodrigues: rotation vector (axis * angle, radians) -> rotation matrix.
 *
 * The small-angle branch is not an optimisation, it is required. The general
 * form divides by `theta` and by `theta^2`; at the increments an LM step
 * actually produces near convergence (1e-8 rad and below) that is 0/0 in
 * floating point, and the solver's final iterations are exactly where it
 * happens. The Taylor branch keeps the last two digits of the answer.
 */
export function expSO3(out: Mat3, w: ArrayLike<number>, wo = 0): Mat3 {
  const wx = w[wo], wy = w[wo + 1], wz = w[wo + 2];
  const t2 = wx * wx + wy * wy + wz * wz;
  const t = Math.sqrt(t2);

  let a: number; // sin(t)/t
  let b: number; // (1-cos(t))/t^2
  if (t < 1e-6) {
    a = 1 - t2 / 6 + (t2 * t2) / 120;
    b = 0.5 - t2 / 24 + (t2 * t2) / 720;
  } else {
    a = Math.sin(t) / t;
    b = (1 - Math.cos(t)) / t2;
  }

  const wx2 = wx * wx, wy2 = wy * wy, wz2 = wz * wz;
  const wxy = wx * wy, wxz = wx * wz, wyz = wy * wz;

  out[0] = 1 - b * (wy2 + wz2);
  out[1] = -a * wz + b * wxy;
  out[2] = a * wy + b * wxz;
  out[3] = a * wz + b * wxy;
  out[4] = 1 - b * (wx2 + wz2);
  out[5] = -a * wx + b * wyz;
  out[6] = -a * wy + b * wxz;
  out[7] = a * wx + b * wyz;
  out[8] = 1 - b * (wx2 + wy2);
  return out;
}

/** Rotation matrix -> rotation vector. The inverse of `expSO3`. */
export function logSO3(out: Vec3, R: Mat3): Vec3 {
  const trace = R[0] + R[4] + R[8];
  const cos = Math.min(Math.max((trace - 1) / 2, -1), 1);
  const theta = Math.acos(cos);

  if (theta < 1e-6) {
    // Near identity: the antisymmetric part IS the rotation vector, to O(t^3).
    out[0] = (R[7] - R[5]) / 2;
    out[1] = (R[2] - R[6]) / 2;
    out[2] = (R[3] - R[1]) / 2;
    return out;
  }
  if (Math.PI - theta < 1e-6) {
    // Near pi the antisymmetric part vanishes; recover the axis from the
    // symmetric part instead. This branch matters here: a wearer who turns to
    // full profile and back passes through large rotations relative to a
    // keyframe, and a silent failure is a keyframe pair dropped from the bundle
    // for no visible reason.
    const d = Float64Array.of(R[0] - cos, R[4] - cos, R[8] - cos);
    let k = 0;
    if (d[1] > d[k]) k = 1;
    if (d[2] > d[k]) k = 2;
    const denom = 2 * (1 - cos);
    const s = Math.sqrt(Math.max(d[k], 0) / (1 - cos));
    const axis = v3();
    axis[k] = s;
    if (s > EPS) {
      if (k === 0) { axis[1] = (R[1] + R[3]) / (denom * s); axis[2] = (R[2] + R[6]) / (denom * s); }
      if (k === 1) { axis[0] = (R[1] + R[3]) / (denom * s); axis[2] = (R[5] + R[7]) / (denom * s); }
      if (k === 2) { axis[0] = (R[2] + R[6]) / (denom * s); axis[1] = (R[5] + R[7]) / (denom * s); }
    }
    vnormalize(axis, axis);
    // Sign from the antisymmetric part, which is small but not zero.
    const sgn = (R[7] - R[5]) * axis[0] + (R[2] - R[6]) * axis[1] + (R[3] - R[1]) * axis[2];
    return vscale(out, axis, sgn >= 0 ? theta : -theta);
  }

  const s = theta / (2 * Math.sin(theta));
  out[0] = s * (R[7] - R[5]);
  out[1] = s * (R[2] - R[6]);
  out[2] = s * (R[3] - R[1]);
  return out;
}

/** Re-orthonormalises a matrix that has drifted off SO(3). Gram-Schmidt. */
export function orthonormalize(out: Mat3, A: Mat3): Mat3 {
  const c0 = v3(A[0], A[3], A[6]);
  const c1 = v3(A[1], A[4], A[7]);
  vnormalize(c0, c0);
  vaddScaled(c1, c1, c0, -vdot(c1, c0));
  vnormalize(c1, c1);
  const c2 = v3();
  vcross(c2, c0, c1);
  out[0] = c0[0]; out[3] = c0[1]; out[6] = c0[2];
  out[1] = c1[0]; out[4] = c1[1]; out[7] = c1[2];
  out[2] = c2[0]; out[5] = c2[1]; out[8] = c2[2];
  return out;
}

// ------------------------------------------------------------------- SE(3)

/** A rigid transform. `R` is row-major SO(3); `t` is millimetres. */
export interface Pose {
  R: Mat3;
  t: Vec3;
}

export const poseIdentity = (): Pose => ({ R: m3(), t: v3() });

export function poseCopy(out: Pose, a: Pose): Pose {
  out.R.set(a.R); out.t.set(a.t); return out;
}

export const poseClone = (a: Pose): Pose => ({ R: new Float64Array(a.R), t: new Float64Array(a.t) });

/** out = pose * p (point). Aliasing-safe. */
export function poseApply(out: Vec3, pose: Pose, p: ArrayLike<number>, po = 0): Vec3 {
  const x = p[po], y = p[po + 1], z = p[po + 2];
  const R = pose.R;
  out[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
  out[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
  out[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
  return out;
}

/** out = pose^-1 * p. */
export function poseApplyInverse(out: Vec3, pose: Pose, p: ArrayLike<number>, po = 0): Vec3 {
  const x = p[po] - pose.t[0], y = p[po + 1] - pose.t[1], z = p[po + 2] - pose.t[2];
  const R = pose.R;
  out[0] = R[0] * x + R[3] * y + R[6] * z;
  out[1] = R[1] * x + R[4] * y + R[7] * z;
  out[2] = R[2] * x + R[5] * y + R[8] * z;
  return out;
}

/** Rotates a direction by the pose (no translation). */
export function poseRotate(out: Vec3, pose: Pose, d: ArrayLike<number>, dobj = 0): Vec3 {
  return m3apply(out, pose.R, d, dobj);
}

export function poseMul(out: Pose, a: Pose, b: Pose): Pose {
  const t = v3();
  poseApply(t, a, b.t);
  m3mul(out.R, a.R, b.R);
  out.t.set(t);
  return out;
}

export function poseInvert(out: Pose, a: Pose): Pose {
  const R = m3();
  m3transpose(R, a.R);
  const t = v3();
  m3apply(t, R, a.t);
  out.R.set(R);
  vscale(out.t, t, -1);
  return out;
}

/**
 * Left-multiplies a pose by an incremental rotation, and adds a translation.
 *
 * The convention is fixed here once and every jacobian in the tree assumes it:
 * an increment `(w, u)` updates a pose as `R <- exp(w) R`, `t <- t + u`. The
 * rotation is perturbed in the CAMERA frame and the translation is plain
 * additive. Mixing conventions between the residual and the update is the
 * classic bundle-adjustment bug — it does not diverge, it converges slowly to a
 * slightly wrong answer, which is indistinguishable from "the data is noisy".
 */
export function poseOplus(out: Pose, a: Pose, delta: ArrayLike<number>, off = 0): Pose {
  const dR = m3();
  expSO3(dR, delta, off);
  m3mul(out.R, dR, a.R);
  out.t[0] = a.t[0] + delta[off + 3];
  out.t[1] = a.t[1] + delta[off + 4];
  out.t[2] = a.t[2] + delta[off + 5];
  orthonormalize(out.R, out.R);
  return out;
}

// -------------------------------------------------------------- Quaternion

export function quatFromMat3(out: Quat, R: Mat3): Quat {
  const trace = R[0] + R[4] + R[8];
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1);
    out[3] = 0.25 / s;
    out[0] = (R[7] - R[5]) * s;
    out[1] = (R[2] - R[6]) * s;
    out[2] = (R[3] - R[1]) * s;
  } else if (R[0] > R[4] && R[0] > R[8]) {
    const s = 2 * Math.sqrt(1 + R[0] - R[4] - R[8]);
    out[3] = (R[7] - R[5]) / s;
    out[0] = 0.25 * s;
    out[1] = (R[1] + R[3]) / s;
    out[2] = (R[2] + R[6]) / s;
  } else if (R[4] > R[8]) {
    const s = 2 * Math.sqrt(1 + R[4] - R[0] - R[8]);
    out[3] = (R[2] - R[6]) / s;
    out[0] = (R[1] + R[3]) / s;
    out[1] = 0.25 * s;
    out[2] = (R[5] + R[7]) / s;
  } else {
    const s = 2 * Math.sqrt(1 + R[8] - R[0] - R[4]);
    out[3] = (R[3] - R[1]) / s;
    out[0] = (R[2] + R[6]) / s;
    out[1] = (R[5] + R[7]) / s;
    out[2] = 0.25 * s;
  }
  return out;
}

export function mat3FromQuat(out: Mat3, q: ArrayLike<number>): Mat3 {
  const x = q[0], y = q[1], z = q[2], w = q[3];
  const x2 = x + x, y2 = y + y, z2 = z + z;
  const xx = x * x2, xy = x * y2, xz = x * z2;
  const yy = y * y2, yz = y * z2, zz = z * z2;
  const wx = w * x2, wy = w * y2, wz = w * z2;
  out[0] = 1 - (yy + zz); out[1] = xy - wz;       out[2] = xz + wy;
  out[3] = xy + wz;       out[4] = 1 - (xx + zz); out[5] = yz - wx;
  out[6] = xz - wy;       out[7] = yz + wx;       out[8] = 1 - (xx + yy);
  return out;
}

/**
 * Yaw / pitch / roll of a head pose, radians, in the intrinsic YXZ order the
 * eyewear domain actually talks in ("turned 30 degrees, looking down 10").
 *
 * Returned as a named object rather than a vector because every consumer reads
 * them by name, and `euler[1]` at a call site is how v1's image-asymmetry yaw
 * and its true yaw got confused with each other.
 */
export function eulerYXZ(R: Mat3): { yaw: number; pitch: number; roll: number } {
  // R = Ry(yaw) Rx(pitch) Rz(roll)
  const m12 = R[5];
  const pitch = Math.asin(-Math.min(Math.max(m12, -1), 1));
  if (Math.abs(m12) < 0.9999999) {
    return { yaw: Math.atan2(R[2], R[8]), pitch, roll: Math.atan2(R[3], R[4]) };
  }
  return { yaw: Math.atan2(-R[6], R[0]), pitch, roll: 0 };
}

/** Builds R = Ry(yaw) Rx(pitch) Rz(roll). The inverse of `eulerYXZ`. */
export function mat3FromEulerYXZ(out: Mat3, yaw: number, pitch: number, roll: number): Mat3 {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cx = Math.cos(pitch), sx = Math.sin(pitch);
  const cz = Math.cos(roll), sz = Math.sin(roll);
  out[0] = cy * cz + sy * sx * sz;
  out[1] = -cy * sz + sy * sx * cz;
  out[2] = sy * cx;
  out[3] = cx * sz;
  out[4] = cx * cz;
  out[5] = -sx;
  out[6] = -sy * cz + cy * sx * sz;
  out[7] = sy * sz + cy * sx * cz;
  out[8] = cy * cx;
  return out;
}

/** Geodesic angle between two rotations, radians. The honest pose-error metric. */
export function rotationAngleBetween(A: Mat3, B: Mat3): number {
  // trace(A^T B) = 1 + 2 cos(theta)
  let tr = 0;
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) tr += A[j * 3 + i] * B[j * 3 + i];
  }
  return Math.acos(Math.min(Math.max((tr - 1) / 2, -1), 1));
}

// ------------------------------------------------------- small dense solves

/**
 * In-place LDL^T of a symmetric matrix stored row-major, n x n.
 *
 * Returns false if the matrix is not positive definite to the given floor,
 * which is the signal LM uses to raise its damping rather than to step into a
 * saddle. Chosen over Cholesky because LDL needs no square roots and degrades
 * more informatively: a non-positive pivot is a diagnosis, not a NaN.
 */
export function ldlt(A: Float64Array, n: number, floor = 1e-14): boolean {
  for (let j = 0; j < n; j++) {
    let d = A[j * n + j];
    for (let k = 0; k < j; k++) d -= A[j * n + k] * A[j * n + k] * A[k * n + k];
    if (!(d > floor)) return false;
    A[j * n + j] = d;
    for (let i = j + 1; i < n; i++) {
      let s = A[i * n + j];
      for (let k = 0; k < j; k++) s -= A[i * n + k] * A[j * n + k] * A[k * n + k];
      A[i * n + j] = s / d;
    }
  }
  return true;
}

/** Solves A x = b in place on `b`, given the LDL^T factorisation from `ldlt`. */
export function ldltSolve(A: Float64Array, n: number, b: Float64Array): void {
  for (let i = 0; i < n; i++) {
    let s = b[i];
    for (let k = 0; k < i; k++) s -= A[i * n + k] * b[k];
    b[i] = s;
  }
  for (let i = 0; i < n; i++) b[i] /= A[i * n + i];
  for (let i = n - 1; i >= 0; i--) {
    let s = b[i];
    for (let k = i + 1; k < n; k++) s -= A[k * n + i] * b[k];
    b[i] = s;
  }
}

/** Convenience: solve a fresh symmetric system, leaving `A` factorised. */
export function solveSymmetric(A: Float64Array, n: number, b: Float64Array): boolean {
  if (!ldlt(A, n)) return false;
  ldltSolve(A, n, b);
  return true;
}

/** Inverts a symmetric positive-definite n x n in place. Small n only. */
export function invertSymmetric(A: Float64Array, n: number): boolean {
  const work = new Float64Array(A);
  if (!ldlt(work, n)) return false;
  const col = new Float64Array(n);
  const out = new Float64Array(n * n);
  for (let c = 0; c < n; c++) {
    col.fill(0); col[c] = 1;
    ldltSolve(work, n, col);
    for (let r = 0; r < n; r++) out[r * n + c] = col[r];
  }
  A.set(out);
  return true;
}

// ------------------------------------------------------------------ helpers

export const clamp = (x: number, lo: number, hi: number): number =>
  (x < lo ? lo : x > hi ? hi : x);

export function smoothstep(edge0: number, edge1: number, x: number): number {
  if (!(edge1 > edge0)) return x >= edge1 ? 1 : 0;
  const t = clamp((x - edge0) / (edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
}

/** Weighted median. Ported from v1, where it was the right answer. */
export function weightedMedian(values: number[], weights?: number[]): number {
  if (values.length === 0) return NaN;
  const order = values.map((_, i) => i).sort((a, b) => values[a] - values[b]);
  if (!weights) {
    const mid = order.length >> 1;
    return order.length % 2
      ? values[order[mid]]
      : (values[order[mid - 1]] + values[order[mid]]) / 2;
  }
  let total = 0;
  for (const w of weights) total += w;
  if (!(total > 0)) return weightedMedian(values);
  const half = total / 2;
  const tie = total * 1e-9;
  let cum = 0;
  for (let k = 0; k < order.length; k++) {
    cum += weights[order[k]];
    if (cum >= half - tie) {
      return cum <= half + tie && k + 1 < order.length
        ? (values[order[k]] + values[order[k + 1]]) / 2
        : values[order[k]];
    }
  }
  return values[order[order.length - 1]];
}

/** Median absolute deviation about the (weighted) median. */
export function mad(values: number[], centre?: number): number {
  if (values.length === 0) return 0;
  const c = centre ?? weightedMedian(values);
  return weightedMedian(values.map((v) => Math.abs(v - c)));
}

/** Percentile of an unsorted array, linear interpolation. For reporting. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return NaN;
  const s = [...values].sort((a, b) => a - b);
  const idx = clamp(p, 0, 1) * (s.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (idx - lo);
}
