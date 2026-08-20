/**
 * One frame of tracking: landmarks in, a posed frame out.
 *
 * ## How small this file is, is the point
 *
 * v1's equivalent (`frame.js`) is 2,550 lines, because every frame re-solved the
 * placement: it measured anchors, ran them through a weighted median, asked
 * whether the wearer had changed, updated a slow person model, re-solved a seat
 * equilibrium, eased four channels, ran a non-penetration guard, and deformed an
 * occluder. All of that was necessary *because it had no model of the wearer*,
 * so every frame had to keep guessing.
 *
 * Here the wearer is a `FaceModel` solved once and the seat is a transform
 * solved once. The per-frame path has exactly three jobs:
 *
 *   1. Solve six numbers of pose against known geometry.
 *   2. Smooth them.
 *   3. Say how much to trust the result.
 *
 * There is no shape estimation, no seat search, no identity question, no trust
 * ramp, no gate, and no per-frame placement of any kind. The frame is welded to
 * the head by the cached seat transform. Everything that used to be able to
 * drift, shimmer, or walk up and down the nose has no mechanism to do so.
 *
 * ## Why there is no yaw handling
 *
 * There is deliberately nothing in this file that mentions yaw. The reported
 * ">40 degrees pushes the frame forward" was not a yaw problem — it was a
 * consequence of solving shape and pose together from one view. Measured on the
 * synthetic ladder: PnP against a *known* model holds 0.25 to 0.44 degrees of
 * rotation error flat from 0 to 90 degrees of yaw, while the same solve against
 * the average head swings the bridge's depth by about 9 mm over the same range.
 * The fix is that the model is known. Adding a yaw term here would be treating
 * a symptom that no longer exists.
 */

import type { Intrinsics } from '../core/camera.js';
import {
  type Pose, eulerYXZ, poseClone, poseIdentity, rotationAngleBetween,
} from '../core/linalg.js';
import type { FaceModel } from '../core/facemodel.js';
import {
  type Correspondence, type PnPResult, buildCorrespondences, refinePnP, solvePnP,
} from './pnp.js';
import { PoseSmoother } from './smoothing.js';

export interface TrackerOptions {
  /** Landmarks whose sigma exceeds this are not used at all, px. */
  maxSigmaPx: number;
  /** Below this many usable correspondences the frame is refused. */
  minCorrespondences: number;
  /**
   * Reprojection RMS above which the solve is treated as failed, px.
   *
   * A hard threshold, and one of the few in the tree. It is not a quality
   * judgement — it is "these landmarks do not describe this face", which happens
   * when a second person walks in front of the wearer, or a hand crosses the
   * face, and the right response is to keep the previous pose rather than to
   * accept a confident fit of the wrong thing.
   */
  maxRmsPx: number;
  /** Consecutive failed or faceless results ridden out before the frame is
   *  hidden. v1 shipped 4 while documenting 2; here there is one number. */
  holdFrames: number;
  /** After this long with no face, the smoother is reset: a velocity carried
   *  across half a second describes a movement that is over. */
  lostSecondsBeforeReset: number;
  /**
   * Filter the pose. **Off by default, and that is a measured result rather than
   * an omission.**
   *
   * v1 needed One Euro badly, and tuned it carefully: its pose came from
   * MediaPipe's own similarity fit of the AVERAGE head to the landmarks, which
   * is noisy, and the filter removed real shimmer at a real cost in lag (v1
   * measured 29.5 mm of lag on a 25 cm/s look-around before tuning, 3.8 mm
   * after).
   *
   * v2's pose comes from six free parameters against known geometry with 300+
   * correspondences, and it is about three times cleaner. Measured across the
   * synthetic population and camera ladder, comparing four tunings from v1's own
   * against none at all:
   *
   *     tuning                     err med   err p90   jitter med   jitter p90
   *     off                          1.27      2.97        0.472        1.271
   *     v1-like  (1.2 Hz, b 0.047)   3.22      8.34        1.471        4.699
   *     light    (4 Hz,   b 0.10)    2.01      4.74        0.769        2.483
   *     v.light  (8 Hz,   b 0.15)    1.68      3.81        0.598        1.876
   *
   * Smoothing is worse on **both** axes, monotonically. That is not a paradox:
   * jitter here is measured as deviation from the head's TRUE frame-to-frame
   * motion, and a filter that lags is deviating from it. Once the underlying
   * estimate is cleaner than the filter's own time constant, the filter is only
   * adding error.
   *
   * The code, the tuning and the report all stay, because this is a synthetic
   * result: a real detector's noise may be more correlated across landmarks than
   * the model here, in which case the pose noise would be larger and the filter
   * would earn its place again. Flip this to `true` and re-run
   * `npm run report:track` to find out. See `docs/OPEN-QUESTIONS.md` Q7.
   */
  smooth: boolean;
}

export const TRACKER_DEFAULTS: TrackerOptions = {
  maxSigmaPx: 12,
  minCorrespondences: 40,
  maxRmsPx: 14,
  holdFrames: 4,
  lostSecondsBeforeReset: 0.5,
  smooth: false,
};

export interface TrackerState {
  readonly model: FaceModel;
  readonly options: TrackerOptions;
  smoother: PoseSmoother;
  /** Last accepted pose, raw (unsmoothed). The next frame's initialisation. */
  lastRaw: Pose | null;
  /** Last emitted pose, smoothed. What the renderer used. */
  lastSmoothed: Pose | null;
  consecutiveFailures: number;
  lostSeconds: number;
  /** Frames since the last full acquisition. */
  framesTracked: number;
  acquisitions: number;
}

export function createTracker(
  model: FaceModel, options: Partial<TrackerOptions> = {},
): TrackerState {
  return {
    model,
    options: { ...TRACKER_DEFAULTS, ...options },
    smoother: new PoseSmoother(),
    lastRaw: null,
    lastSmoothed: null,
    consecutiveFailures: 0,
    lostSeconds: 0,
    framesTracked: 0,
    acquisitions: 0,
  };
}

export interface TrackInput {
  /** Detector landmarks in pixels for this frame, 2 per landmark. */
  landmarks: Float64Array | null;
  /** Per-landmark sigma in pixels. */
  sigmaPx: Float64Array | null;
  intrinsics: Intrinsics;
  /** Seconds since the previous frame that was actually *consumed*. Not the
   *  camera interval: after a dropout the true gap is longer, and feeding the
   *  short one makes the adaptive cutoff read the accumulated displacement as
   *  one enormous velocity and land the catch-up as a snap. */
  dt: number;
}

export interface TrackResult {
  /** Whether the frame is on the face this frame. */
  tracked: boolean;
  /** The pose to render with. Null when nothing should be drawn. */
  pose: Pose | null;
  /** The unsmoothed solve, for diagnostics and for the harness. */
  rawPose: Pose | null;
  rmsPx: number;
  correspondences: number;
  inliers: number;
  /** True yaw / pitch / roll, radians. Readout only — nothing gates on it. */
  euler: { yaw: number; pitch: number; roll: number } | null;
  /** How far the smoother moved the raw pose this frame: mm and degrees. The
   *  honest measure of the lag the filter is costing. */
  smoothingLagMm: number;
  smoothingLagDeg: number;
  /** Set when the pose is held over from a previous frame. */
  held: boolean;
  reason: string | null;
}

export function track(state: TrackerState, input: TrackInput): TrackResult {
  const { model, options } = state;

  if (!input.landmarks || !input.sigmaPx) {
    return miss(state, input.dt, 'no face detected');
  }

  const correspondences: Correspondence[] = buildCorrespondences(
    input.landmarks, input.sigmaPx, model.vertexCount, undefined, options.maxSigmaPx,
  );
  if (correspondences.length < options.minCorrespondences) {
    return miss(state, input.dt, `only ${correspondences.length} usable landmarks`);
  }

  // Warm start from the previous raw pose; POSIT from scratch only on
  // acquisition. In steady state this converges in two iterations.
  let result: PnPResult;
  if (state.lastRaw) {
    result = refinePnP(model.positions, correspondences, input.intrinsics, state.lastRaw);
    // A warm start that lands badly is usually a warm start that was stale —
    // the head moved a lot while we were not looking. Retry cold before giving
    // up, because a cold solve at any pose is the whole point of having a model.
    if (!(result.rmsPx <= options.maxRmsPx)) {
      const cold = solvePnP(model.positions, correspondences, input.intrinsics);
      if (cold.rmsPx < result.rmsPx) { result = cold; state.acquisitions++; }
    }
  } else {
    result = solvePnP(model.positions, correspondences, input.intrinsics);
    state.acquisitions++;
  }

  if (!(result.rmsPx <= options.maxRmsPx) || !(result.pose.t[2] > 50)) {
    return miss(state, input.dt, `reprojection ${result.rmsPx.toFixed(1)} px`);
  }

  state.consecutiveFailures = 0;
  state.lostSeconds = 0;
  state.framesTracked++;
  state.lastRaw = poseClone(result.pose);

  const smoothed = options.smooth
    ? state.smoother.filter(result.pose, input.dt)
    : poseClone(result.pose);
  state.lastSmoothed = poseClone(smoothed);

  return {
    tracked: true,
    pose: smoothed,
    rawPose: result.pose,
    rmsPx: result.rmsPx,
    correspondences: correspondences.length,
    inliers: result.inliers,
    euler: eulerYXZ(smoothed.R),
    smoothingLagMm: Math.hypot(
      smoothed.t[0] - result.pose.t[0],
      smoothed.t[1] - result.pose.t[1],
      smoothed.t[2] - result.pose.t[2],
    ),
    smoothingLagDeg: (rotationAngleBetween(smoothed.R, result.pose.R) * 180) / Math.PI,
    held: false,
    reason: null,
  };
}

function miss(state: TrackerState, dt: number, reason: string): TrackResult {
  state.consecutiveFailures++;
  state.lostSeconds += Math.max(dt, 0);

  if (state.lostSeconds >= state.options.lostSecondsBeforeReset) {
    // Only the filter. The MODEL is untouched and cannot be touched: it is not
    // a per-session estimate, so there is nothing here that a dropout could
    // corrupt. In v1 this branch had to reason carefully about which of six
    // estimators to discard.
    state.smoother.reset();
    state.lastRaw = null;
  }

  const hold = state.consecutiveFailures <= state.options.holdFrames && state.lastSmoothed;
  return {
    tracked: !!hold,
    pose: hold ? poseClone(state.lastSmoothed!) : null,
    rawPose: null,
    rmsPx: NaN,
    correspondences: 0,
    inliers: 0,
    euler: hold ? eulerYXZ(state.lastSmoothed!.R) : null,
    smoothingLagMm: 0,
    smoothingLagDeg: 0,
    held: !!hold,
    reason,
  };
}

/**
 * Where the glasses go this frame: the seat transform, carried by the head pose.
 *
 * One matrix multiply. That is not a slogan the way it was in v1 — where the
 * README said "the per-frame cost is a matrix multiply" while the code swept a
 * thousand contact bins through a depth field every frame — it is the whole
 * per-frame placement path, and there is nothing else in it.
 */
export function frameToCamera(out: Pose, headPose: Pose, seat: Pose): Pose {
  const R = out.R;
  const A = headPose.R, B = seat.R;
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      R[r * 3 + c] = A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c];
    }
  }
  const x = seat.t[0], y = seat.t[1], z = seat.t[2];
  out.t[0] = A[0] * x + A[1] * y + A[2] * z + headPose.t[0];
  out.t[1] = A[3] * x + A[4] * y + A[5] * z + headPose.t[1];
  out.t[2] = A[6] * x + A[7] * y + A[8] * z + headPose.t[2];
  return out;
}

export { poseIdentity };
