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
 * in this file has a yaw term, and nothing in it refuses a frame; it does not
 * need to, because the estimator is no longer asked a question it cannot answer.
 * It is not weightless, though, and an earlier version of this sentence claimed
 * it was: the robust kernel downweights continuously, and the two statistics the
 * tracker's gates actually refuse on — `rmsPx` and `grossFraction` — are
 * computed here. The refusal is the tracker's; the evidence for it is this
 * file's.
 *
 * Two stages:
 *   1. `posit` — a coarse pose from scratch, for acquisition and recovery.
 *   2. `refinePnP` — robust LM on the true perspective model, from any
 *      initialisation. In steady state the initialisation is the previous
 *      frame's pose.
 *
 * An earlier version of this header said that last case "converges in two
 * iterations". The *pose* does; the *loop* does not, and the distinction is
 * worth keeping because someone will read the iteration counter one day.
 * Measured over 5,526 warm-started frames — the population across the whole
 * camera ladder — capping `maxIterations` at 2 lands within 0.027 deg and
 * 0.069 mm of the fully converged pose (median; 0.128 deg / 0.55 mm at p95) and
 * the error against ground truth is unchanged, 0.464 vs 0.457 deg median. But
 * the loop runs 7 accepted steps (median; 8.1 mean, 12 at p95), warm or cold
 * alike, because `stepRad` and `stepMm` are 1e-7 rad and 1e-5 mm — six orders
 * below the landmark noise floor. Everything after the second step is chasing a
 * tolerance nobody reads. That is a latency argument, not a correctness one, so
 * it is left as it is and written down rather than silently tightened.
 */

import {
  type Intrinsics, dProjDPose, project,
} from '../core/camera.js';
import {
  type Pose, type Vec3, invertSymmetric, ldlt, ldltSolve, logSO3, m3, m3mul,
  m3transpose, orthonormalize, poseClone, poseIdentity, poseOplus, v3, vcross,
  vnormalize,
} from '../core/linalg.js';
import { type RobustLoss, barronDrho, barronRho, huber } from '../core/robust.js';

export interface Correspondence {
  /** Index into the model's vertex array. */
  vertex: number;
  /** Observed pixel. */
  u: number;
  v: number;
  /** One-sigma in pixels. Larger means "the detector is guessing". */
  sigmaPx: number;
  /**
   * Barron shape parameter for THIS correspondence, scheduled by how well
   * the camera can see it. Absent means "use the solver's own loss".
   *
   * Only honoured when `PnPOptions.redescending` is set, which is what keeps
   * the non-convex kernels off every cold path: the same correspondence
   * array is reused by the rms-gate retry and the basin audit, and both of
   * those exist precisely to escape a bad basin, so neither may run a loss
   * that can create one.
   */
  lossAlpha?: number;
  /**
   * Whether `sigmaPx` is an honest NOISE claim, eligible to calibrate the
   * a-posteriori variance factor. Absent means yes.
   *
   * False marks a sigma that was DELIBERATELY inflated as a bias guard —
   * occlusion inflation, rigidity disenfranchisement, the visibility ramp.
   * Those inflations are not noise estimates: the guarded bias does not
   * scale with the session's sigma miscalibration, so their whitened
   * residuals are small by construction and pooling them dragged chi2/dof —
   * and with it every covariance this file returns — 18-28% under the
   * honest value at mid-yaw (review-measured). The solve WEIGHTS are
   * untouched by this flag; only the calibration estimate reads it.
   */
  sigmaCalibrated?: boolean;
}

/**
 * The eligible-inlier count at which the variance factor is fully the
 * calibrated estimate. Below it the estimate BLENDS toward the pooled one —
 * lambda = min(1, eligible/this) — so a thinning eligible set degrades
 * gracefully instead of stepping 18-28% at a threshold (the design review
 * mechanized exactly that step). A known-biased estimate beats a
 * noise-dominated one. At the shipped eligibility cut the probe measured
 * 37-73 eligible vertices across yaw 0-70, so the blend region exists for
 * degenerate frames, not the working regime.
 */
export const VF_CAL_MIN_COUNT = 30;

/**
 * How far off a correspondence must be, in raw pixels, before it counts as
 * describing something OTHER than this face — and, with
 * `TrackerOptions.maxGrossFraction`, the second half of the "these
 * landmarks do not describe this face" gate.
 *
 * **One statistic could not do both jobs, and shipping one that tried cost
 * a wearer and a review to find out.**
 *
 * The gate started as a flat quadratic mean over every correspondence. That
 * counts the far half-face MediaPipe invents at full strength even though
 * the estimator has already muted those landmarks to nearly nothing, so an
 * ordinary deep turn reads 14.8 px at 75 degrees against a 14 px budget and
 * is refused as a stranger — a wearer's "the glasses disappear around the
 * 50 degrees mark".
 *
 * Weighting each residual by the robust weight fixed the turn and opened a
 * hole. With Huber the weight outside the threshold is `delta*sigma/e`, so
 * `sum(w e^2)/sum(w)` reduces to `sum(e)/sum(1/e)` — the GEOMETRIC mean,
 * which for two populations at `a` and `b` is exactly `sqrt(a*b)`. It is
 * dominated by the SMALL residuals and collapses on a MIXTURE, which is
 * what a second face is: measured on a cold acquisition with an intruder
 * capturing 45-60% of the landmark set, the weighted statistic reads
 * 10.6-13.0 px and ACCEPTS while the pose is up to 57 mm wrong. The
 * isotropic-scatter fixture that was supposed to guard this never saw it,
 * because scatter is one population and the failure needs two.
 *
 * Percentiles were tried next and are worse in the loop, for a reason worth
 * recording: the gate is not a filter, it is a LATCH. Accept one bad frame
 * and the tracker follows the intruder, after which every later frame fits
 * it beautifully and the statistic reports a clean frame. A cut below the
 * intruder's capture fraction is simply measuring the face the solve chose
 * (p50 read 1.1 px on a second face), and the cuts above it have no
 * headroom left for a real turn (p80 refuses 65 degrees).
 *
 * A COUNT has neither problem. It cannot be diluted by weighting, and it
 * cannot be fooled by the latch — if the solve locks onto the intruder then
 * the WEARER's landmarks become the grossly-wrong population and the count
 * stays high either way. Measured through the production loop, median of 5
 * seeds, as the fraction of correspondences more than 40 px off:
 *
 *     frontal, yaw 40, yaw 55        0.000
 *     yaw 65 / 70 / 80               0.003 / 0.025 / 0.050
 *     second face, 45% capture       0.409, 0.397
 *     second face, 60% capture       0.278
 *
 * Legitimate frames top out at 0.05 and a second face starts at 0.278 — a
 * factor of 5.6, and `maxGrossFraction` sits at 0.15 inside it, three times
 * above anything a real turn produces.
 *
 * 40 px rather than 20 or 30: all three separate (the >20 px column reads
 * 0.126 legitimate against 0.289 intruder), but the margin widens with the
 * threshold because a deep turn's invented landmarks are wrong by ten-ish
 * pixels while a second face's are wrong by fifty, and 40 sits above the
 * first population and below the second.
 */
export const GROSS_ERROR_PX = 40;

export interface PnPResult {
  pose: Pose;
  /** Robustified cost at the solution. */
  cost: number;
  /** RMS reprojection in raw pixels, each residual weighted by the robust
   *  weight the solve gave it — "how far off are the landmarks I am
   *  actually listening to". */
  rmsPx: number;
  /** Fraction of correspondences whose raw error exceeds `GROSS_ERROR_PX` —
   *  "how much of this frame is describing something else". The half of the
   *  gate a weighted mean structurally cannot provide; see GROSS_ERROR_PX. */
  grossFraction: number;
  /** How many correspondences `grossFraction` was counted over — the denominator,
   *  so a caller can fold in the landmarks the sigma cull kept from it. */
  grossTotal: number;
  /** How many correspondences carried at least half their weight. */
  inliers: number;
  iterations: number;
  converged: boolean;
  /** 6x6 covariance of the pose increment (rotation rad, translation mm),
   *  from the inverse Hessian at the solution. Null if it was singular. */
  covariance: Float64Array | null;
  /** The a-posteriori variance factor the covariance was scaled by — the
   *  measured mis-scale of the honest sigma claims (see residualStats).
   *  Callers fusing a prior need it to keep the prior in the same units. */
  varianceFactor: number;
  /** With a prior: the prior's share of the information at the solution,
   *  per block — trace(prior)/trace(measurement+prior) over the rotation
   *  and translation diagonals. NaN without a prior. The field instrument
   *  for the prior's strength: near 1 the prior owns the pose, near 0 the
   *  landmarks do. Read from the last LM iteration's normal matrix. */
  priorShareRot: number;
  priorShareMm: number;
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
    //
    // DeMenthon & Davis is `P_i . I = x_i (1 + eps_i) - x_0`, and the `- x_0`
    // term was missing here. It cancels only when `sum_i P_i = 0` — when the
    // reference is the centroid. The reference here is `correspondences[0]`, so
    // it does not cancel, and the omission was a real bias in `I` and `J`:
    // 73.9 degrees of median rotation error over 2,016 synthetic frames, against
    // 1.59 with the term restored.
    //
    // The control that isolates it, on exact noise-free projections: place the
    // reference vertex on the optical axis so it projects to the principal
    // point and `x_0 = y_0 = 0`. Both versions then return 0.0000 degrees, over
    // 99 poses from -80 to +80 yaw. Move the reference off the axis and the old
    // code goes to 73.6 median / 158.5 worst while this one stays at 0.0000. It
    // was never noise, sampling or weak perspective — it was one term.
    //
    // Why a 74-degree error shipped unnoticed: `refinePnP` absorbs it. Over the
    // same 2,016 frames `solvePnP`'s rotation error is identical to four
    // decimals, 0.6192 degrees either way — POSIT's job here is only to land
    // inside LM's basin, and even a badly biased weak-perspective pose usually
    // does. What it cost was the iteration count and the tail. Iterations:
    // 10.8 mean against 8.1. Tail, stressed with 8- to 40-point correspondence
    // subsets over 11,760 trials: p99 rotation error 135.0 degrees against 8.0,
    // and 850 solves rejected by a `rmsPx <= 14` gate against 505 — 345 frames
    // the biased start could not track at all. The *rate* of gate-passing
    // answers that are still >5 degrees wrong is unchanged (4.5% vs 4.7%); a
    // near-minimal subset is genuinely ambiguous and no initialisation fixes
    // that. This is a tail fix, not a mean fix, and it should be judged as one.
    const bx = v3(), by = v3();
    for (let i = 0; i < n; i++) {
      const wx = xs[i] * (1 + eps[i]) - xs[0];
      const wy = ys[i] * (1 + eps[i]) - ys[0];
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
  /**
   * A MAP prior on the pose, fused INSIDE the normal equations.
   *
   * `pose` is the prediction and `information` its 6x6 inverse covariance
   * (row-major, rotation block first, rad/mm — ALREADY scaled into the
   * caller's sigma-claim units: this file weights residuals by claimed
   * sigma, so a prior in honest units must arrive pre-multiplied by the
   * caller's variance-factor estimate, or the two sides argue in different
   * currencies). Each iteration adds `information` to H and
   * `information * r_p` to the gradient, where r_p = [log(R*R_predT);
   * t - t_pred] is the tangent-space residual under the same increment
   * convention `poseOplus` uses; the prior Jacobian is taken as identity,
   * which is exact at r_p = 0 and second-order small at the few degrees a
   * frame-to-frame prediction misses by. (An earlier draft of this comment
   * also claimed the process noise shrinks `information` when the miss
   * grows; the design review struck it as false — `information` is built
   * before the residual is known and cannot depend on it. What bounds a
   * violated constant-velocity model is the CALLER's window gating, not
   * anything here.) Bell & Cathey 1993: the converged solve IS the iterated
   * EKF posterior, so there is no second filter to reconcile with — the
   * returned covariance (inverse of the FUSED normal matrix, scaled by the
   * measurement variance factor) is the posterior covariance.
   */
  prior: { pose: Pose; information: Float64Array } | null;
  /**
   * Honour each correspondence's own `lossAlpha` (Barron) instead of the
   * single `loss`. Off by default, and deliberately a SOLVE-level switch
   * rather than a property of the correspondence array: the array is shared
   * with the cold retry and the basin audit, which must stay convex.
   */
  redescending: boolean;
}

export const PNP_DEFAULTS: PnPOptions = {
  maxIterations: 20,
  // 2.5 sigmas. Tighter than the bundle's, because at track time the model is
  // known: a residual that large is an outlier, not an unexplained shape.
  loss: huber(2.5),
  stepRad: 1e-7,
  stepMm: 1e-5,
  wantCovariance: false,
  prior: null,
  redescending: false,
};

export function refinePnP(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, initial: Pose, options: Partial<PnPOptions> = {},
): PnPResult {
  const opt = { ...PNP_DEFAULTS, ...options };
  const pose = poseClone(initial);
  // Barron's scale is the SAME threshold the solver's own loss turns over at,
  // so varying the kernel's shape per landmark never varies what "2.5 sigma"
  // means. See RobustLoss.scale.
  const lossScale2 = opt.loss.scale * opt.loss.scale;

  const H = new Float64Array(36);
  const g = new Float64Array(6);
  const Haug = new Float64Array(36);
  const dx = new Float64Array(6);
  const J = new Float64Array(12);
  const cam = v3();
  const rot = v3();
  const uv = new Float64Array(2);

  // The MAP prior's scratch and its two helpers. r_p is the tangent-space
  // error of the CURRENT pose against the prediction, under the same
  // convention poseOplus applies increments with (rotation first).
  const prior = opt.prior;
  const rp = new Float64Array(6);
  const rpRot = m3();
  const rpT = m3();
  const priorResidual = (p: Pose): Float64Array => {
    logSO3(rp, m3mul(rpRot, p.R, m3transpose(rpT, prior!.pose.R)));
    rp[3] = p.t[0] - prior!.pose.t[0];
    rp[4] = p.t[1] - prior!.pose.t[1];
    rp[5] = p.t[2] - prior!.pose.t[2];
    return rp;
  };
  const priorCost = (p: Pose): number => {
    if (!prior) return 0;
    const r = priorResidual(p);
    const L = prior.information;
    let c = 0;
    for (let a = 0; a < 6; a++) {
      for (let b = 0; b < 6; b++) c += r[a] * L[a * 6 + b] * r[b];
    }
    return c;
  };
  // The last iteration's MEASUREMENT information traces, per block — what
  // the prior share is measured against.
  let trHrot = 0;
  let trHmm = 0;

  let lambda = 1e-4;
  const redescending = opt.redescending;
  let cost = evaluateCost(positions, correspondences, intrinsics, pose, opt.loss, redescending)
    + priorCost(pose);
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
      const s = r0 * r0 + r1 * r1;
      const drho = opt.redescending && c.lossAlpha !== undefined
        ? barronDrho(s, c.lossAlpha, lossScale2)
        : opt.loss.eval(s)[1];

      dProjDPose(J, 0, intrinsics, cam, rot);
      for (let a = 0; a < 12; a++) J[a] *= w;

      for (let a = 0; a < 6; a++) {
        g[a] += drho * (J[a] * r0 + J[6 + a] * r1);
        for (let b = 0; b <= a; b++) {
          H[a * 6 + b] += drho * (J[a] * J[b] + J[6 + a] * J[6 + b]);
        }
      }
    }

    // The prior joins the SAME normal equations — one solver, one step.
    // Traces are taken before it joins, so the share readout compares the
    // two information sources rather than the fused total to itself.
    trHrot = H[0] + H[7] + H[14];
    trHmm = H[21] + H[28] + H[35];
    if (prior) {
      const L = prior.information;
      const r = priorResidual(pose);
      for (let a = 0; a < 6; a++) {
        for (let b = 0; b < 6; b++) g[a] += L[a * 6 + b] * r[b];
        // Only the lower triangle: the damping, LDLT and the covariance
        // mirror all read H as lower-triangular symmetric.
        for (let b = 0; b <= a; b++) H[a * 6 + b] += L[a * 6 + b];
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
      const c1 = evaluateCost(positions, correspondences, intrinsics, trial, opt.loss, redescending)
        + priorCost(trial);
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
  // Ten inliers is the floor under the variance factor: below it the
  // chi-squared/dof estimate is itself noise, and a caller with three
  // correspondences would receive a confidently wild covariance (the
  // tracker's minCorrespondences gate never goes there; this guards the
  // API for callers that might).
  if (opt.wantCovariance && stats.inliers >= 10) {
    const C = new Float64Array(36);
    C.set(H);
    for (let i = 0; i < 6; i++) for (let j = 0; j < i; j++) C[j * 6 + i] = C[i * 6 + j];
    if (invertSymmetric(C, 6)) {
      // Scaled by the a-posteriori variance factor, so the covariance stays
      // honest when the sigma stream's absolute scale is not — see
      // residualStats. Without this, the covariance is a statement about the
      // CLAIMED noise; with it, about the observed noise.
      for (let i = 0; i < 36; i++) C[i] *= stats.varianceFactor;
      covariance = C;
    }
  }

  // The prior's information share per block, off the last iteration's
  // normal matrix. A diagnostic, not a control: nothing downstream gates on
  // it, and the one-step staleness of a broken-out iteration is fine for a
  // readout whose consumers are a plot and a paste.
  let priorShareRot = NaN;
  let priorShareMm = NaN;
  if (prior) {
    const L = prior.information;
    const trLrot = L[0] + L[7] + L[14];
    const trLmm = L[21] + L[28] + L[35];
    priorShareRot = trLrot / (trHrot + trLrot);
    priorShareMm = trLmm / (trHmm + trLmm);
  }

  return {
    pose,
    cost,
    rmsPx: stats.rms,
    grossFraction: stats.grossFraction,
    grossTotal: stats.grossTotal,
    inliers: stats.inliers,
    iterations,
    converged,
    covariance,
    varianceFactor: stats.varianceFactor,
    priorShareRot,
    priorShareMm,
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
  intrinsics: Intrinsics, pose: Pose, loss: RobustLoss, redescending = false,
): number {
  const cam = v3();
  const uv = new Float64Array(2);
  const R = pose.R;
  const c2 = loss.scale * loss.scale;
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
    const s = r0 * r0 + r1 * r1;
    // The SAME kernel the normal equations used, per point. If these two ever
    // disagree the line search is judging a different objective from the one
    // the step was computed for, and LM silently stops being LM.
    cost += redescending && c.lossAlpha !== undefined
      ? barronRho(s, c.lossAlpha, c2)
      : loss.eval(s)[0];
  }
  return cost;
}

function residualStats(
  positions: ArrayLike<number>, correspondences: Correspondence[],
  intrinsics: Intrinsics, pose: Pose, loss: RobustLoss,
): { rms: number; grossFraction: number; grossTotal: number; inliers: number;
  varianceFactor: number } {
  const cam = v3();
  const uv = new Float64Array(2);
  const R = pose.R;
  let sum = 0, n = 0, inliers = 0, gross = 0, total = 0;
  // The robustly-weighted whitened residual sum and its effective degrees of
  // freedom — the per-frame a-posteriori variance factor, the same estimator
  // the enrollment bundle uses to keep its covariance honest. It is what
  // rescales the inverse Hessian when the sigma stream's absolute scale is
  // off: overstate every sigma by 3x and H^-1 grows 9x, but the whitened
  // residuals shrink 9x, and the product stands still.
  //
  // Two populations, because the factor is an estimate of ONE number — how
  // mis-scaled the HONEST sigma claims are — and a deliberately-inflated
  // sigma is not a claim about noise at all (see Correspondence.
  // sigmaCalibrated). The calibrated sums carry the estimate; the pooled
  // sums are the fallback when a degenerate frame leaves too few eligible
  // inliers to estimate from. The 6-parameter dof subtraction is written
  // against the calibrated subset as if the pose were fit to it alone; the
  // subset carries most of the solve's information, so the over-subtraction
  // is at most a few of ~100 dof — conservative by a few percent.
  let whitened = 0, wEff = 0;
  let whitenedCal = 0, wEffCal = 0, inliersCal = 0;
  for (const c of correspondences) {
    if (!(c.sigmaPx > 0 && c.sigmaPx < 1e6)) continue;
    const i = c.vertex;
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
    if (!project(uv, intrinsics, cam)) continue;
    const e = Math.hypot(uv[0] - c.u, uv[1] - c.v);
    const w = 1 / c.sigmaPx;
    const z2 = (e * w) * (e * w);
    // **These statistics deliberately use the solver's REFERENCE loss, not
    // the per-landmark kernel the fit ran.** The design review caught the
    // alternative being wrong in two ways at once.
    //
    // `inliers` is documented as "carried at least half its weight", and
    // that half means a different residual under every kernel — Huber(2.5)
    // crosses 0.5 at 5 sigma, Barron at alpha 1 and alpha -2 cross it
    // somewhere else entirely. Counted against the varying kernel it would
    // stop being a fit statistic and start being a readout of the schedule,
    // while still guarding the covariance's ten-inlier floor.
    //
    // The variance factor's job here is narrower than the textbook one: it
    // is a statement about the SIGMA STREAM's absolute scale, and rank 4's
    // motion prior consumes it as exactly that. A factor computed under a
    // kernel that engages on some frames and not others would step whenever
    // the schedule did, injecting a scale change the sigma stream never had.
    // Measured against a fixed reference it stays comparable frame to frame.
    // The cost of the choice, recorded: it is no longer the a-posteriori
    // factor of the precise objective minimised. The subset it is estimated
    // from is high-visibility by construction (VF_CAL_MIN_VIS 0.9, above the
    // schedule's BARRON_VIS_HI), so those landmarks sit at one end of the
    // schedule and the two readings differ little in practice.
    const lossW = loss.eval(z2)[1];
    if (lossW > 0.5) inliers++;
    // Two questions, deliberately separate. `rms` asks HOW WELL the
    // landmarks the solve listened to fit — weighted, so the far side the
    // estimator already muted cannot refuse an ordinary deep turn. `gross`
    // asks HOW MUCH of the frame is describing something else entirely,
    // which is a count and cannot be diluted by weighting. See
    // GROSS_ERROR_PX for why one statistic could not do both.
    sum += lossW * e * e;
    n += lossW;
    total++;
    if (e > GROSS_ERROR_PX) gross++;
    whitened += lossW * z2;
    wEff += lossW;
    if (c.sigmaCalibrated !== false) {
      whitenedCal += lossW * z2;
      wEffCal += lossW;
      if (lossW > 0.5) inliersCal++;
    }
  }
  const lambda = Math.min(1, inliersCal / VF_CAL_MIN_COUNT);
  const dofCal = Math.max(1, 2 * wEffCal - 6);
  const dofAll = Math.max(1, 2 * wEff - 6);
  const vf = lambda * (whitenedCal / dofCal) + (1 - lambda) * (whitened / dofAll);
  return {
    rms: n > 0 ? Math.sqrt(sum / n) : NaN,
    grossFraction: total ? gross / total : 0,
    grossTotal: total,
    inliers,
    // `errors.length` is the count of correspondences that actually
    // projected — the same guard the old `n` was. (It briefly WAS the bare
    // `n`, which still type-checked after the count was removed because
    // `n` resolves to a numeric-separator-free global in the DOM lib; the
    // suite caught it at runtime within a minute, but it is worth knowing
    // that tsc will not.)
    varianceFactor: total ? vf : 1,
  };
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
  rigidity?: Float64Array, maxSigma = 12, calibrated?: Uint8Array,
  sigmaCulled?: number[],
): Correspondence[] {
  const out: Correspondence[] = [];
  for (let i = 0; i < vertexCount; i++) {
    const u = landmarks[i * 2];
    if (Number.isNaN(u)) continue;
    let sigma = sigmaPx[i];
    if (!(sigma > 0) || sigma > maxSigma) {
      // **Dropped for being uncertain — which is not the same as absent.**
      // `grossFraction` asks how much of this frame describes something else,
      // and it is counted over the array this function returns. So a landmark
      // dropped here leaves the statistic that exists to notice it, and
      // declaring landmarks uncertain became a way to look clean. Callers that
      // gate on gross pass this array and get the ones that had a real position
      // and a real rigidity, so the gate can decide for itself. See
      // `GROSS_ERROR_PX`.
      if (sigmaCulled && sigma > 0 && (!rigidity || rigidity[i] > 0.01)) sigmaCulled.push(i);
      continue;
    }
    // Rigidity enters as a sigma inflation rather than a separate weight, so
    // there is exactly one currency in the solver and one Huber threshold that
    // means the same thing for every residual.
    if (rigidity) {
      const r = rigidity[i];
      if (!(r > 0.01)) continue;
      sigma /= Math.sqrt(r);
    }
    const c: Correspondence = { vertex: i, u, v: landmarks[i * 2 + 1], sigmaPx: sigma };
    // Absent mask means every sigma is an honest claim — the pre-mask
    // behavior, and the right reading for callers whose streams carry no
    // deliberate inflation (the harness, the scan path, the bundle's init).
    if (calibrated && calibrated[i] === 0) c.sigmaCalibrated = false;
    out.push(c);
  }
  return out;
}
