/**
 * Pose from a known 3D model and 2D observations.
 *
 * This is the whole of live tracking in v2, and the reason the ">40 degrees"
 * complaint is an architectural fix rather than a tuning one.
 *
 * v1 did not do this. It took MediaPipe's `facialTransformationMatrix`, which is
 * a rigid similarity fit of the *average* head to the landmarks. At large yaw
 * that fit has two problems at once: the landmarks are worse, and the shape
 * being fitted is wrong — so the solver trades shape error against pose, and the
 * pose absorbs it as depth and forward push. Exactly the reported symptom.
 *
 * Here the shape is *this wearer's*, solved once and frozen. Only six numbers
 * are free. Three hundred visible correspondences on a known rigid body
 * over-determine six numbers by a factor of a hundred, and the conditioning
 * stays good far past the angle where a shape-and-pose fit falls apart. Nothing
 * in this file has a yaw term, a trust ramp, or a gate; it does not need one,
 * because the estimator is no longer asked a question it cannot answer.
 *
 * Two stages:
 *   1. `posit` — a coarse pose from scratch, for acquisition and recovery.
 *   2. `refinePnP` — robust LM on the true perspective model, from any
 *      initialisation. In steady state the initialisation is the previous
 *      frame's pose and this converges in two iterations.
 */

import {
  type Intrinsics, dProjDPose, project,
} from '../core/camera.js';
import {
  type Pose, type Vec3, invertSymmetric, ldlt, ldltSolve, m3, orthonormalize,
  poseClone, poseIdentity, poseOplus, v3, vcross, vnormalize,
} from '../core/linalg.js';
import { type RobustLoss, huber } from '../core/robust.js';

export interface Correspondence {
  /** Index into the model's vertex array. */
  vertex: number;
  /** Observed pixel. */
  u: number;
  v: number;
  /** One-sigma in pixels. Larger means "the detector is guessing". */
  sigmaPx: number;
}

export interface PnPResult {
  pose: Pose;
  /** Robustified cost at the solution. */
  cost: number;
  /** RMS reprojection in raw pixels over the inliers. */
  rmsPx: number;
  /** How many correspondences carried at least half their weight. */
  inliers: number;
  iterations: number;
  converged: boolean;
  /** 6x6 covariance of the pose increment (rotation rad, translation mm),
   *  from the inverse Hessian at the solution. Null if it was singular. */
  covariance: Float64Array | null;
}

// ------------------------------------------------------------------- POSIT

/**
 * Pose from Orthography and Scaling with ITerations (DeMenthon & Davis, 1995).
 *
 * Chosen over EPnP for one reason that matters here and nowhere else: it needs
 * no correspondence-free initialisation and no RANSAC to produce a usable
 * answer on a *dense, mostly-correct* correspondence set, which is exactly what
 * a face landmarker gives. Its weakness — it assumes weak perspective, so it
 * degrades when the object's depth range approaches its distance — is not
 * reached by a head at 300 mm or more.
 *
 * Its output is never used directly; it is the initialisation for `refinePnP`,
 * which is on the true perspective model.
 */
export function posit(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, iterations = 12,
): Pose {
  const n = correspondences.length;
  const pose = poseIdentity();
  if (n < 4) { pose.t.set([0, 0, 500]); return pose; }

  // Reference point: the first correspondence. Object points are relative to it.
  const ref = correspondences[0].vertex;
  const rx = positions[ref * 3], ry = positions[ref * 3 + 1], rz = positions[ref * 3 + 2];

  // Normalised image coordinates.
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  const P = new Float64Array(n * 3);
  for (let i = 0; i < n; i++) {
    const c = correspondences[i];
    xs[i] = (c.u - intrinsics.cx) / intrinsics.f;
    ys[i] = (c.v - intrinsics.cy) / intrinsics.f;
    P[i * 3] = positions[c.vertex * 3] - rx;
    P[i * 3 + 1] = positions[c.vertex * 3 + 1] - ry;
    P[i * 3 + 2] = positions[c.vertex * 3 + 2] - rz;
  }

  // Pseudo-inverse of the object matrix: (P^T P)^-1 P^T, a 3x3 inverse.
  const PtP = new Float64Array(9);
  for (let i = 0; i < n; i++) {
    for (let a = 0; a < 3; a++) {
      for (let b = 0; b < 3; b++) PtP[a * 3 + b] += P[i * 3 + a] * P[i * 3 + b];
    }
  }
  if (!invertSymmetric(PtP, 3)) { pose.t.set([0, 0, 500]); return pose; }

  const eps = new Float64Array(n);
  const I = v3(), J = v3(), r3 = v3();
  let Z0 = 500;

  for (let it = 0; it < iterations; it++) {
    // Right-hand sides.
    const bx = v3(), by = v3();
    for (let i = 0; i < n; i++) {
      const wx = xs[i] * (1 + eps[i]);
      const wy = ys[i] * (1 + eps[i]);
      for (let a = 0; a < 3; a++) {
        bx[a] += P[i * 3 + a] * wx;
        by[a] += P[i * 3 + a] * wy;
      }
    }
    for (let a = 0; a < 3; a++) {
      I[a] = PtP[a * 3] * bx[0] + PtP[a * 3 + 1] * bx[1] + PtP[a * 3 + 2] * bx[2];
      J[a] = PtP[a * 3] * by[0] + PtP[a * 3 + 1] * by[1] + PtP[a * 3 + 2] * by[2];
    }

    const li = Math.hypot(I[0], I[1], I[2]);
    const lj = Math.hypot(J[0], J[1], J[2]);
    if (!(li > 1e-12 && lj > 1e-12)) break;
    Z0 = 2 / (li + lj);

    const R = pose.R;
    R[0] = I[0] * Z0; R[1] = I[1] * Z0; R[2] = I[2] * Z0;
    R[3] = J[0] * Z0; R[4] = J[1] * Z0; R[5] = J[2] * Z0;
    vcross(r3, Float64Array.of(R[0], R[1], R[2]), Float64Array.of(R[3], R[4], R[5]));
    vnormalize(r3, r3);
    R[6] = r3[0]; R[7] = r3[1]; R[8] = r3[2];
    orthonormalize(R, R);

    let moved = 0;
    for (let i = 0; i < n; i++) {
      const e = (P[i * 3] * R[6] + P[i * 3 + 1] * R[7] + P[i * 3 + 2] * R[8]) / Z0;
      moved = Math.max(moved, Math.abs(e - eps[i]));
      eps[i] = e;
    }
    if (moved < 1e-6) break;
  }

  // Translation: put the reference point at depth Z0 on its own ray, then
  // subtract the rotated reference offset so the pose maps model -> camera.
  const refCam = v3(xs[0] * Z0, ys[0] * Z0, Z0);
  const R = pose.R;
  const rotRef = v3(
    R[0] * rx + R[1] * ry + R[2] * rz,
    R[3] * rx + R[4] * ry + R[5] * rz,
    R[6] * rx + R[7] * ry + R[8] * rz,
  );
  pose.t[0] = refCam[0] - rotRef[0];
  pose.t[1] = refCam[1] - rotRef[1];
  pose.t[2] = refCam[2] - rotRef[2];

  // POSIT's sign convention admits a mirrored solution. A head behind the
  // camera is not a pose, it is the other root; flip it rather than returning
  // it, because the caller's refinement will happily converge to a beautiful
  // fit of a face turned inside out.
  if (!(pose.t[2] > 0)) {
    pose.t[2] = Math.abs(pose.t[2]) || 500;
  }
  return pose;
}

// ------------------------------------------------------------- refinement

export interface PnPOptions {
  maxIterations: number;
  loss: RobustLoss;
  /** Stop when the rotation step is below this (rad) and translation below
   *  `stepMm`. Both, not either: a pure translation step can be tiny while the
   *  rotation is still moving, and vice versa. */
  stepRad: number;
  stepMm: number;
  /** Compute and return the pose covariance. Off in the hot path. */
  wantCovariance: boolean;
}

export const PNP_DEFAULTS: PnPOptions = {
  maxIterations: 20,
  // 2.5 sigmas. Tighter than the bundle's, because at track time the model is
  // known: a residual that large is an outlier, not an unexplained shape.
  loss: huber(2.5),
  stepRad: 1e-7,
  stepMm: 1e-5,
  wantCovariance: false,
};

export function refinePnP(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, initial: Pose, options: Partial<PnPOptions> = {},
): PnPResult {
  const opt = { ...PNP_DEFAULTS, ...options };
  const pose = poseClone(initial);

  const H = new Float64Array(36);
  const g = new Float64Array(6);
  const Haug = new Float64Array(36);
  const dx = new Float64Array(6);
  const J = new Float64Array(12);
  const cam = v3();
  const rot = v3();
  const uv = new Float64Array(2);

  let lambda = 1e-4;
  let cost = evaluateCost(positions, correspondences, intrinsics, pose, opt.loss);
  let iterations = 0;
  let converged = false;

  for (; iterations < opt.maxIterations; iterations++) {
    H.fill(0); g.fill(0);
    const R = pose.R;

    for (const c of correspondences) {
      if (!(c.sigmaPx > 0 && c.sigmaPx < 1e6)) continue;
      const i = c.vertex;
      const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
      rot[0] = R[0] * x + R[1] * y + R[2] * z;
      rot[1] = R[3] * x + R[4] * y + R[5] * z;
      rot[2] = R[6] * x + R[7] * y + R[8] * z;
      cam[0] = rot[0] + pose.t[0];
      cam[1] = rot[1] + pose.t[1];
      cam[2] = rot[2] + pose.t[2];
      if (!project(uv, intrinsics, cam)) continue;

      const w = 1 / c.sigmaPx;
      const r0 = (uv[0] - c.u) * w;
      const r1 = (uv[1] - c.v) * w;
      const [, drho] = opt.loss.eval(r0 * r0 + r1 * r1);

      dProjDPose(J, 0, intrinsics, cam, rot);
      for (let a = 0; a < 12; a++) J[a] *= w;

      for (let a = 0; a < 6; a++) {
        g[a] += drho * (J[a] * r0 + J[6 + a] * r1);
        for (let b = 0; b <= a; b++) {
          H[a * 6 + b] += drho * (J[a] * J[b] + J[6 + a] * J[6 + b]);
        }
      }
    }

    let stepped = false;
    for (let attempt = 0; attempt < 8; attempt++) {
      Haug.set(H);
      for (let d = 0; d < 6; d++) Haug[d * 6 + d] *= 1 + lambda;
      for (let d = 0; d < 6; d++) Haug[d * 6 + d] += 1e-12;
      for (let d = 0; d < 6; d++) dx[d] = -g[d];
      if (!ldlt(Haug, 6)) { lambda *= 10; continue; }
      ldltSolve(Haug, 6, dx);

      const trial = poseClone(pose);
      poseOplus(trial, trial, dx, 0);
      const c1 = evaluateCost(positions, correspondences, intrinsics, trial, opt.loss);
      if (c1 < cost) {
        pose.R.set(trial.R); pose.t.set(trial.t);
        cost = c1;
        lambda = Math.max(lambda * 0.3, 1e-10);
        stepped = true;
        break;
      }
      lambda *= 10;
      if (lambda > 1e12) break;
    }

    if (!stepped) break;
    const rotStep = Math.hypot(dx[0], dx[1], dx[2]);
    const trStep = Math.hypot(dx[3], dx[4], dx[5]);
    if (rotStep < opt.stepRad && trStep < opt.stepMm) { converged = true; break; }
  }

  const stats = residualStats(positions, correspondences, intrinsics, pose, opt.loss);

  let covariance: Float64Array | null = null;
  if (opt.wantCovariance) {
    const C = new Float64Array(36);
    C.set(H);
    for (let i = 0; i < 6; i++) for (let j = 0; j < i; j++) C[j * 6 + i] = C[i * 6 + j];
    covariance = invertSymmetric(C, 6) ? C : null;
  }

  return {
    pose,
    cost,
    rmsPx: stats.rms,
    inliers: stats.inliers,
    iterations,
    converged,
    covariance,
  };
}

/** POSIT init followed by robust refinement. The acquisition path. */
export function solvePnP(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, initial?: Pose, options: Partial<PnPOptions> = {},
): PnPResult {
  const start = initial ?? posit(positions, correspondences, intrinsics);
  return refinePnP(positions, correspondences, intrinsics, start, options);
}

// ------------------------------------------------------------------ helpers

function evaluateCost(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, pose: Pose, loss: RobustLoss,
): number {
  const cam = v3();
  const uv = new Float64Array(2);
  const R = pose.R;
  let cost = 0;
  for (const c of correspondences) {
    if (!(c.sigmaPx > 0 && c.sigmaPx < 1e6)) continue;
    const i = c.vertex;
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
    if (!project(uv, intrinsics, cam)) { cost += 1e6; continue; }
    const w = 1 / c.sigmaPx;
    const r0 = (uv[0] - c.u) * w;
    const r1 = (uv[1] - c.v) * w;
    cost += loss.eval(r0 * r0 + r1 * r1)[0];
  }
  return cost;
}

function residualStats(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, pose: Pose, loss: RobustLoss,
): { rms: number; inliers: number } {
  const cam = v3();
  const uv = new Float64Array(2);
  const R = pose.R;
  let sum = 0, n = 0, inliers = 0;
  for (const c of correspondences) {
    if (!(c.sigmaPx > 0 && c.sigmaPx < 1e6)) continue;
    const i = c.vertex;
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
    if (!project(uv, intrinsics, cam)) continue;
    const e = Math.hypot(uv[0] - c.u, uv[1] - c.v);
    sum += e * e; n++;
    const w = 1 / c.sigmaPx;
    if (loss.eval((e * w) * (e * w))[1] > 0.5) inliers++;
  }
  return { rms: n ? Math.sqrt(sum / n) : NaN, inliers };
}

/**
 * Builds correspondences from a detector result.
 *
 * `sigmaPx` is where all the intelligence lives, and it is the thing v1 had to
 * hand-build as three trust ramps. A detector that reports per-landmark
 * uncertainty (the 703-landmark class of model) supplies it directly. For
 * MediaPipe, which does not, `detect/uncertainty.ts` estimates it — and the
 * point of routing it through this one field is that swapping the detector
 * changes one file and nothing else.
 */
export function buildCorrespondences(
  landmarks: Float64Array, sigmaPx: Float64Array, vertexCount: number,
  rigidity?: Float64Array, maxSigma = 12,
): Correspondence[] {
  const out: Correspondence[] = [];
  for (let i = 0; i < vertexCount; i++) {
    const u = landmarks[i * 2];
    if (Number.isNaN(u)) continue;
    let sigma = sigmaPx[i];
    if (!(sigma > 0) || sigma > maxSigma) continue;
    // Rigidity enters as a sigma inflation rather than a separate weight, so
    // there is exactly one currency in the solver and one Huber threshold that
    // means the same thing for every residual.
    if (rigidity) {
      const r = rigidity[i];
      if (!(r > 0.01)) continue;
      sigma /= Math.sqrt(r);
    }
    out.push({ vertex: i, u, v: landmarks[i * 2 + 1], sigmaPx: sigma });
  }
  return out;
}
