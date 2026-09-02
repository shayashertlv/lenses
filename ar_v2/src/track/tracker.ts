/**
 * One frame of tracking: landmarks in, a posed frame out.
 *
 * ## What this file no longer re-solves, is the point
 *
 * This heading read "How small this file is, is the point" from 2026-08-20 until
 * it was retired. It broke in a single refactor and then stood unedited while the
 * file multiplied in size, which is the argument against making smallness the
 * point of anything. `docs/ARCHITECTURE.md`'s tracking section carries the
 * stamped line counts and the retraction; they are not repeated here, so that
 * they cannot rot in two places.
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
 * There is no shape estimation, no seat search, and no per-frame placement: the
 * frame is welded to the head by the cached seat transform, and nothing here
 * re-derives where it sits. Everything that used to be able to drift, shimmer, or
 * walk up and down the nose has no mechanism to do so.
 *
 * The sentence that stood here denied what the file grew into. It used to claim
 * "no trust ramp, no gate, and no identity question" as well, and two of those
 * three are false of this file. The trust ramps: the variance-factor EMA and the
 * visibility fade that takes a landmark's weight down instead of dropping it, on
 * every frame; the two prior-miss EMAs whenever the motion prior is on; the
 * learned latch floor under `smooth: 'locked'`. The gates: four refusals that end
 * the frame in `miss()`, the latch's enter thresholds, the drift guard, and the
 * hold-then-reset path. The identity claim is true of this
 * file only — `identity.ts` asks that question on every wear frame, off the
 * variance factor computed here, so it is false of `src/track/` as a whole.
 *
 * ## Why there is no yaw handling
 *
 * There is deliberately nothing in this file that mentions yaw. The reported
 * ">40 degrees pushes the frame forward" was not a yaw problem — it was a
 * consequence of solving shape and pose together from one view. Measured across
 * the synthetic population and the whole camera ladder: PnP against a *known*
 * model holds 0.42 degrees of median rotation error at frontal and single degrees
 * through the turn, while the same solve against the average head swings the
 * bridge's depth several times further. Which multiple depends on the smoothing
 * arm — the unfiltered library default and the app's filtered one differ by an
 * order of magnitude — so the comparison is only meaningful with the arm named.
 * The fix is that the model is known. Adding a yaw term would be treating a
 * symptom that no longer exists.
 *
 * The per-angle digits that used to sit here — 0.93 at 60, 0.88 at 90, and 5.1 mm
 * against 0.37 — were written on 2026-08-20 and were never re-measured, including
 * when `report:track` was re-run on 2026-08-31 in `421dc30`.
 * `docs/ARCHITECTURE.md`'s tracking section carries the current rotation figures
 * as a median of five seeds, and its diagnosis section the current depth swing
 * per arm. `reports/track.txt` is the single checked-in seed-11 realisation and
 * will not match those digit for digit. Take them from either, with its basis
 * named — not from a comment nothing re-runs.
 */


import {
  type Pose, expSO3, invertSymmetric, logSO3, m3, m3mul, m3transpose,
  orthonormalize, poseClone, poseIdentity, rotationAngleBetween, smoothstep, v3,
} from '../core/linalg.js';
import type { Intrinsics } from '../core/camera.js';
import { headEuler, project } from '../core/camera.js';
import { landmarkSurface, type FaceModel } from '../core/facemodel.js';
import type { SilhouetteStrip } from '../core/mesh.js';
import {
  type Correspondence, type PnPResult, GROSS_ERROR_PX, buildCorrespondences,
  pixelGateScale, refinePnP, solvePnP,
} from './pnp.js';
import { ADAPTIVE_SIGMA_FLOOR_PX, PoseSmoother, noiseScaleFromSigma } from './smoothing.js';

export interface TrackerOptions {
  /** Landmarks whose sigma exceeds this are not used at all, px. */
  maxSigmaPx: number;
  /** Below this many usable correspondences the frame is refused. */
  minCorrespondences: number;
  /**
   * Reprojection error above which the solve is treated as failed, px —
   * read against `RMS_PERCENTILE` of the raw residuals, so it means "a
   * quarter of these landmarks are further off than this".
   *
   * A hard threshold, and one of the few in the tree. It is not a quality
   * judgement — it is "these landmarks do not describe this face", which happens
   * when a second person walks in front of the wearer, or a hand crosses the
   * face, and the right response is to keep the previous pose rather than to
   * accept a confident fit of the wrong thing.
   *
   * 25, re-derived when the statistic changed (see `RMS_PERCENTILE` for why
   * it had to). Legitimate held turns measure 12.5-13.7 px all the way from
   * 60 to 85 degrees; a cold acquisition that has locked onto a second face
   * measures 71-89. 25 sits between them with 1.8x headroom above anything
   * legitimate and 2.8x margin below anything wrong. The old 14 belonged to
   * a quadratic mean and would refuse a 75-degree turn outright.
   */
  maxRmsPx: number;
  /**
   * Fraction of correspondences allowed to be grossly wrong (further off
   * than `GROSS_ERROR_PX`) before the frame is refused — the other half of
   * "these landmarks do not describe this face".
   *
   * `maxRmsPx` asks how well the landmarks the solve listened to fit;
   * this asks how much of the frame is describing something else. A
   * weighted mean structurally cannot answer the second question, and the
   * review found a second face passing it at a 57 mm pose error. 0.15
   * against a legitimate ceiling of 0.05 (a held 80-degree turn) and an
   * intruder floor of 0.278 — see `GROSS_ERROR_PX` for the measurement and
   * for the two statistics that were tried and failed first.
   */
  maxGrossFraction: number;
  /** Consecutive failed or faceless results ridden out before the frame is
   *  hidden. v1 shipped 4 while documenting 2; here there is one number. */
  holdFrames: number;
  /** After this long with no face, the smoother is reset: a velocity carried
   *  across half a second describes a movement that is over. */
  lostSecondsBeforeReset: number;
  /**
   * Filter the pose. **Off by default in this library, and that is a measured
   * result rather than an omission — but the app turns it on.** `app/main.ts`
   * boots `smooth: true` (decided 2026-08-23; it reaches this repository in
   * `f9c9093`), so this default governs tests and goldens, not what a wearer
   * runs — and not `report:track` either, which overrides it for two of its
   * three arms. The measured verdict below has since reversed on jitter; the
   * note after the table says how.
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
   * would earn its place again. That is what happened: the app has booted
   * the One Euro since 2026-08-23 — latched first, then plain `smooth: true`
   * (`app/main.ts`) — so this `false` is the
   * LIBRARY default — what tests and goldens get — and not the shipped one.
   * `report:track` overrides it too, running two of its three arms filtered.
   * Re-measured 2026-08-31, the filtered arm now wins jitter median and p90 5/5
   * across the campaign seeds and loses on lag. **The four-row sweep above
   * predates all of this** (it is pre-`f9c9093`) and has never been re-run.
   * See `docs/OPEN-QUESTIONS.md` Q7.
   *
   * **`'adaptive'`** is the answer to what the first real wearer then reported:
   * the caveat above came true, and it came true YAW-SHAPED. Jiggle grows as
   * the head turns — the far-side landmarks are hallucinated and their sigma
   * inflates — and the fixed tuning "barely helps, same after 15 degrees". A
   * single tuning cannot serve both regimes; the sigma the tracker is already
   * holding says which regime each frame is in. `'adaptive'` keeps the One Euro
   * structure and divides its cutoff by a per-frame noise scale derived from
   * the mean finite landmark sigma relative to the clean-frontal floor — the
   * formula, its two constants and what the scale actually reads on the
   * production sigma stream all live on `noiseScaleFromSigma` in smoothing.ts.
   * Measured on the wander fixture (median frame-to-frame bridge delta, mm):
   * through 25-40 degrees of yaw, off 1.837 / fixed 1.533 / adaptive 1.298;
   * quiet hold 1.458 / 0.873 / 0.554 — for one extra frame of response to a
   * step turn (3 against fixed's 2 frames to 90%). `false` and `true` are
   * bit-identical to the pre-adaptive build, asserted in core.test.ts.
   */
  smooth: boolean | 'adaptive' | 'locked';
  /**
   * Every this-many tracked frames, run a COLD solve alongside the warm one
   * and adopt it if it wins decisively. The warm start is a local search: the
   * first real wearer's recording caught it falling into a rolled basin during
   * a fast slide across the frame — +12-15 degrees of roll their face never
   * performed, held for ten seconds, reprojection just good enough that the
   * rms gate never fired. A basin the gate cannot see needs an audit the gate
   * does not run. Amortised cost at 30: a POSIT+refine every second — tenths
   * of a millisecond per frame.
   */
  basinAuditInterval: number;
  /** The cold solve must beat the warm rms by this ratio to be adopted —
   *  decisive wins only, so landmark noise cannot flap between basins. */
  basinRescueRatio: number;
  /**
   * The adoption deadband, mm and degrees. A cold solve that beats the warm
   * rms decisively but lands within BOTH of these is the SAME basin seen
   * through this frame's noise — adopting it buys nothing and costs a
   * smoother reset, which is a visible pop. The first real wearer's "choppy"
   * report had exactly that signature: near-equal adoptions about once a
   * second on real correlated noise, each one resetting the filter. Below
   * the deadband the warm pose stands; above it, the adoption is a genuine
   * rescue and lands through the same crossfade as a latch release.
   */
  basinAdoptMinMm: number;
  basinAdoptMinDeg: number;
  /**
   * Per-vertex rigidity for the pose solve, or null for all-rigid.
   *
   * A rigid-pose solver may only listen to landmarks that move rigidly with
   * the SKULL, and MediaPipe's eye region does not: the lids and eye
   * contours deform with gaze (the documented upstream defect — the mesh
   * follows the pupil), so a wearer whose face is perfectly still can steer
   * the solved pose with their eyes. That is not noise the latch can absorb;
   * it is a coherent fake motion, and the fix is disenfranchisement, not
   * filtering. The array feeds `buildCorrespondences`' rigidity parameter:
   * sigma is inflated by 1/sqrt(r), and r <= 0.01 excludes the vertex
   * entirely. The app builds it with `trackingRigidity`: the lid rings —
   * where gaze and blinks actually live — get no vote at all, and the
   * orbital surround keeps a feathered half vote, because the first,
   * broader exclusion demonstrably cost the solve its stability at tilt.
   */
  rigidity: Float64Array | null;
  /**
   * Fuse a constant-velocity MAP prior into every warm solve.
   *
   * The mechanism (Bell & Cathey 1993 — the converged LM solve with a prior
   * residual block IS the iterated-EKF posterior): the last few raw poses
   * predict this frame's pose by an exact least-squares constant-velocity
   * fit, and the prediction joins the solve's normal equations weighted by
   * its own inverse covariance — endpoint noise and a physical
   * head-acceleration bound, both priced per frame from the window's actual
   * timestamps (see `buildMotionPrior`). The result is certainty-gated
   * smoothing INSIDE the estimator: at frontal rest the landmarks carry
   * ~50x the prior's information and nothing changes; at a 40-degree tilt
   * the landmark information collapses and the prior steadies exactly the
   * axes the solve no longer knows — with no yaw term and no second filter
   * to reconcile with the first.
   *
   * It does carry a gate, one per channel, and this sentence claimed it did
   * not until 2026-09-01. Without one a 1-1.5 Hz reversal made the prior
   * 7-19x WORSE than no prior at all, because `accel` prices only the
   * window's timestamps: see `priorMissLast` / `priorMissTransLast` and
   * `PRIOR_MISS_EMA_RATE`. The rotation channel got its gate first and the
   * translation channel was graded by it for a while, which is the defect
   * `ce4da5e` and `792da2c` closed.
   *
   * Off by default so the library's behavior is bit-identical to the
   * pre-prior build; the app turns it on (`?prior=off` is the A/B lever).
   */
  motionPrior: boolean;
  /**
   * Schedule a redescending robust kernel by per-landmark visibility — see
   * `BARRON_VIS_LO`.
   *
   * Requires `motionPrior`, and not as a convenience: a redescending loss is
   * non-convex, and this file's own Cauchy note already says such a loss
   * "could only ever run after the first iterations have found the right
   * one". The motion prior's prediction is what holds the solve in the right
   * basin, so the schedule is applied only on frames that carry one, and only
   * to the solve that carries it.
   *
   * **Off by default, and that is a measured verdict rather than caution.**
   * It was built to answer the partially-hallucinated landmark — the one that
   * survives the visibility cull because it is still front-facing, and that
   * Huber then lets pull with constant force forever. Measured on exactly
   * that fixture (production visibility, coherent hallucination, paired
   * seeds, median translation error mm):
   *
   *     yaw   huber   kernel only   scheduled
   *      0    0.090      0.089        0.089
   *     40   11.365     12.268       12.484
   *     55   15.811     17.006       17.846
   *
   * It makes the very thing it was built for WORSE, and the reason is a
   * property of the failure rather than of the implementation. A redescending
   * estimator assumes the bad points are a MINORITY it can shed. The
   * hallucinated far side is not a minority within its visibility band — it
   * is a coherent, pose-correlated majority — so redescending lets that
   * majority silence the honest points that disagree with it, and the solve
   * settles deeper into the biased consensus. This is the same sentence the
   * research plan already wrote about rank 6, arriving a rank early:
   * reweighting can only mute a bias, never remove it. Measured, it cannot
   * even mute it.
   *
   * The machinery stays, tested and unwired, for the regime where the
   * assumption does hold — a MINORITY of coherently displaced landmarks,
   * which is what an occluding hand actually is. On that fixture the schedule
   * beats Huber by 32% at frontal (1.579 -> 1.079 mm). Read the caveat on
   * that number in the ledger before wiring it: the occluded patch there sat
   * in the low-visibility band, and visibility is the wrong signal for an
   * occluder the raster cannot see at all.
   */
  redescending: boolean;
  /**
   * Per-oval-landmark candidate strips from `silhouetteStrips`, or null to
   * match those landmarks to fixed vertices as every other landmark is.
   *
   * This is the one BIAS fix in the tracker. An oval landmark marks the
   * face's occluding contour at its height, and the 3-D point on that
   * contour slides across the surface as the head turns — 34.6 mm on
   * average by 40 degrees of yaw — so a fixed correspondence is wrong in a
   * pose-correlated way. Each frame the strip is marched under the pose we
   * currently believe and the landmark is matched to whichever candidate is
   * actually on the contour there. See `silhouetteStrips` for why
   * reweighting cannot substitute.
   *
   * **Null by default, and the reason is a split measurement rather than
   * caution.** Marching assumes the oval landmark is an HONEST observation
   * of the outline. Where it is, the gain is large and holds up: at a held
   * 40 degrees the settled error goes 0.513 -> 0.104 mm (-80%), at 55
   * degrees 0.974 -> 0.153 (-84%). But MediaPipe draws the far half-face
   * from its frontal prior, and where the landmark is an INVENTION rather
   * than an observation, moving its 3-D point onto the true contour only
   * widens the disagreement: the same code costs +37% at 55 degrees on a
   * fixture whose far side is invented. No synthetic here can say which
   * regime a given wearer's detector is in — that is what `?march=on`
   * exists to settle, on a face, in a minute. A visibility guard (march only
   * onto a contour vertex the depth buffer says is visible) was measured and
   * REJECTED: it cost most of the upside at 55-65 degrees and removed none
   * of the downside.
   */
  ovalStrips: SilhouetteStrip[] | null;
  /**
   * The floor `'adaptive'` measures sigma against, px. Must be in the SAME
   * pixels as the `sigmaPx` fed to `track()`: the default is the detect-
   * resolution floor, so a caller handing sigma in source pixels scales this
   * by the same `pixelScale` it gave `estimateSigma` — otherwise a perfectly
   * clean frame at a 1280-wide capture reads as 2x noise and the filter
   * over-smooths every frame. Ignored unless `smooth` is `'adaptive'`.
   */
  adaptiveFloorPx: number;
}

export const TRACKER_DEFAULTS: TrackerOptions = {
  maxSigmaPx: 12,
  minCorrespondences: 40,
  maxRmsPx: 14,
  maxGrossFraction: 0.15,
  holdFrames: 4,
  lostSecondsBeforeReset: 0.5,
  smooth: false,
  basinAuditInterval: 30,
  basinRescueRatio: 0.85,
  basinAdoptMinMm: 3,
  basinAdoptMinDeg: 2,
  rigidity: null,
  motionPrior: false,
  redescending: false,
  ovalStrips: null,
  adaptiveFloorPx: ADAPTIVE_SIGMA_FLOOR_PX,
};

export interface TrackerState {
  readonly model: FaceModel;
  readonly options: TrackerOptions;
  smoother: PoseSmoother;
  /** Last accepted pose, raw (unsmoothed). The next frame's initialisation. */
  lastRaw: Pose | null;
  /** Last emitted pose, smoothed. What the renderer used. */
  lastSmoothed: Pose | null;
  /** The stillness latch ('locked' mode): consecutive frames the windowed
   *  velocity has stayed under the enter thresholds, and the pose being held
   *  while latched. */
  latchQuiet: number;
  latchedPose: Pose | null;
  /** Scratch for the per-frame effective rigidity (static map x visibility
   *  ramp), reused to avoid a per-frame allocation. */
  rigidityScratch: Float64Array;
  /** Scratch for the per-frame variance-factor eligibility mask (static
   *  rigidity untouched AND visibility above `VF_CAL_MIN_VIS`), same reuse. */
  calibratedScratch: Uint8Array;
  /** Scratch mapping an oval landmark to the vertex it marched to this
   *  frame, -1 for every landmark that is not an oval one. Same reuse. */
  marchScratch: Int32Array;
  /**
   * `model.positions` in the DETECTOR's convention — see `landmarkSurface`.
   *
   * Every use of the model's geometry inside `track` is a comparison against
   * the detector's own output: the PnP correspondences, the cold retry, the
   * basin audit, the culled-landmark gross fold and the strip march. None of
   * them wants skin, and `model.positions` is skin — `enroll.ts` subtracts
   * `landmarkBiasMm` before the model leaves. So the tracker holds the other
   * surface and never reads `model.positions` at all.
   *
   * Identical to `model.positions` while the bias is zero, which it is until a
   * calibration exists (Q2). Built once here rather than per frame.
   */
  landmarkPositions: Float64Array;
  /** The last LATCH_VEL_WINDOW+1 raw poses with arrival times and the
   *  solve's own one-sigma pose uncertainty (mm / deg, from the calibrated
   *  covariance; carried forward when a frame's covariance was singular) —
   *  the window the latch's velocity gate reads, and the noise scale it is
   *  read AGAINST. Cleared whenever the raw stream stops being one
   *  continuous motion (dropout reset, basin adoption). */
  velRing: { pose: Pose; time: number; sigmaMm: number; sigmaDeg: number }[];
  /** Cumulative consumed-frame time, the clock `velRing` stamps against. */
  velTime: number;
  /** The per-session rest floor, per channel: mean and absolute-deviation
   *  EMAs over LATCHED-frame windowed velocities — the frames the latch can
   *  vouch for as rest. Null until the first latched frame; survives
   *  dropouts and basin adoptions because the session's noise regime does. */
  floorMm: { m: number; d: number } | null;
  floorDeg: { m: number; d: number } | null;
  /** The gates the latch actually ran this frame, for the diagnostics
   *  readout: enter per channel (learned, clamped), exit = enter * ratio. */
  latchEnterMmS: number;
  latchEnterDegS: number;
  /** Last frame's lifted drift guards — the contraction-decay's memory. */
  guardMmLast: number;
  guardDegLast: number;
  /** The previous accepted solve's posterior covariance (honest units) —
   *  the motion prior's endpoint uncertainty. Null until a solve carries
   *  one, and nulled when the motion it described is over (dropout reset);
   *  a singular-Hessian frame stores null, which simply skips the next
   *  frame's prior rather than inventing certainty. */
  lastCovariance: Float64Array | null;
  /** The session's variance-factor EMA and the previous frame's raw value —
   *  the sigma-claim honesty scale the prior is weighted by. The EMA
   *  SURVIVES dropouts (honesty is a property of the session, like the rest
   *  floor); the per-frame value dies with the motion. */
  vfEma: number | null;
  vfPrev: number | null;
  /**
   * How many of its own claimed sigmas the motion prior's prediction was wrong
   * by on the last frame that carried one, and an EMA of the same. Null until
   * a prior has been graded.
   *
   * This is the signal that lets one process-noise constant serve both a still
   * head and a reversing one — see the `miss` term in `buildMotionPrior`.
   *
   * **Per channel, since 2026-08-31.** Until then only the rotation residual
   * was graded and the resulting miss scaled BOTH channels' process noise, so
   * a translation reversal — the lean-in/lean-back beat of an actual try-on —
   * had no way to trip the gate at all. Measured on the ground-truth model,
   * 0.7 px landmark noise, 210 frames after burn-in, 5 seeds, paired arms,
   * translation RMS of the RAW pose with the prior off then on:
   *
   *     still                     0.226 -> 0.149 mm   (0.66x, the rest win)
   *     lean z 0.5 Hz +/-50 mm    0.226 -> 1.026      (4.5x WORSE)
   *     lean z 1.0 Hz +/-25 mm    0.228 -> 2.336      (10.2x WORSE)
   *     yaw shake 1.0 Hz +/-10 d  0.225 -> 0.150      (0.67x — the gate working)
   *
   * **Every one of those cells is yaw 40, z 520 mm, 0.7 px**, and the residual
   * is not flat across the operating envelope: the same 1 Hz lean reads 1.78x
   * nominal, 2.33x at yaw 55 / z 700 mm, 2.57x at 2.5 px of landmark noise and
   * 2.42x at 5 px. The fix improves all of them — the defect reads 10.3x,
   * 8.2x, 5.5x and 3.1x on the same four — but 1.74x is the mildest point
   * measured, not the worst.
   *
   * And one cell where per-channel grading is strictly the WEAKER arrangement,
   * recorded because it is a motion the wearer makes constantly. A neck-pivot
   * turn translates the head origin along an arc, and under one shared grade a
   * fast yaw stood the TRANSLATION prior aside as a side effect. It no longer
   * does. At a 100 mm pivot the exchange is invisible (rotation 1.03x -> 1.04x,
   * translation 1.00x -> 0.98x); at 200 mm it is a real trade and a favourable
   * one (rotation 1.00x -> 1.04x, translation 1.45x -> 1.07x).
   *
   * The yaw-shake row is the point: the gate rescues exactly the channel it
   * grades and nothing else. The rotation channel's own numbers show the same shape
   * mirrored — a lateral sway cost rotation 1.83x and a vertical bob 2.62x
   * while translation barely moved — because a dragged translation leaks into
   * the coupled solve.
   *
   * **And on the pose the wearer is actually shown**, which is the smoothed
   * one — `smooth: true` and `motionPrior: true` are both app defaults, and
   * the renderer gets `result.pose`, not `rawPose`. Same construction, same
   * seeds, `smooth: true`, translation RMS of the EMITTED pose:
   *
   *                               before        after
   *     still                     1.01x         1.01x
   *     lean z 0.5 Hz +/-50 mm    1.10x         1.01x
   *     lean z 1.0 Hz +/-25 mm    1.46x         1.01x   (3.749 -> 2.598 mm)
   *     sway x 1.0 Hz +/-25 mm    1.01x         1.00x
   *     rotation, lean z 1.0 Hz   3.34x         1.02x
   *     rotation, sway x 1.0 Hz   3.64x         1.03x
   *
   * The One Euro filter absorbs most of the resting difference — which is why
   * the raw table's 0.66x reads 1.01x here — but it cannot absorb a systematic
   * drag against a reversal, and that is what the defect was: 1.15 mm of real
   * wearer-visible error on a lean, and three-and-a-half times the rotation
   * error on motions with no rotation in them at all.
   */
  priorMissLast: number | null;
  priorMissEma: number | null;
  /** The same grade for the TRANSLATION channel, against the translational
   *  sigma the prediction claimed. Kept separate rather than combined: a
   *  reversal in one channel is not evidence about the other, and the whole
   *  defect above was one channel's grade standing in for both. */
  priorMissTransLast: number | null;
  priorMissTransEma: number | null;
  /** A release/adoption crossfade in flight. `fadeFrom` is armed where the
   *  eye last saw the glasses; the first fade frame converts it into
   *  `fadeOffset` — the error from the live pose, as a translation and a
   *  rotation vector — which then decays to zero over `fadeLeft` frames
   *  while live motion rides through underneath at full rate. */
  fadeFrom: Pose | null;
  fadeOffset: { t: Float64Array; w: Float64Array } | null;
  fadeLeft: number;
  /** Diagnostics counts: how often the latch engaged, released on velocity,
   *  re-anchored on the drift guard — and how many frames it HELD, so a
   *  paste can report the average latch spell (latchedFrames / engages)
   *  even after the interesting seconds have rolled out of the app's short
   *  readout ring. Three field pastes in a row lost their still segment to
   *  that ring before this counter existed. */
  latchEngages: number;
  latchReleases: number;
  latchReanchors: number;
  latchedFrames: number;
  /** Times the basin audit replaced a warm pose — a diagnostics count. */
  basinEscapes: number;
  /** Cold audits solved, and decisive-rms wins skipped because the pose
   *  difference sat inside the adoption deadband. */
  basinAuditsRun: number;
  basinAdoptionsSkipped: number;
  consecutiveFailures: number;
  lostSeconds: number;
  /**
   * Frames this SESSION whose solve passed the gate. Cumulative and never
   * reset — **not** per-acquisition, whatever this comment used to say.
   *
   * Two readers depend on the cumulative reading. The basin audit's cadence
   * (`framesTracked % basinAuditInterval`, and see that option's own docstring,
   * which describes it correctly) needs it to hold an amortised rate; and the
   * diagnostics paste emits it beside `acquisitions`, where
   * `framesTracked / acquisitions` is the mean frames per lock only under this
   * reading.
   *
   * **Measured before touching it, because the obvious fix is backwards.**
   * Resetting the counter at each acquisition — which is what the old comment
   * described — starves the audit exactly where a wrong basin is most likely.
   * Both semantics against the same sessions (smooth: true, 20 deg yaw + 20 mm
   * lateral wander, sigma 0.7 px, dropouts long enough to pass the 0.5 s reset):
   *
   *     session                  tracked  acqs   cumulative   per-acquisition
   *     no dropouts, 600 frames    600      1    19 audits    19  — identical
   *     20-frame gap every 90      480      7    13           13  — identical
   *     20-frame gap every 37      428     24    13            1
   *     20-frame gap every 31      339     29    10            1
   *
   * The implemented semantics holds 2.7–3.2 audits per 100 tracked frames in
   * every regime; the documented one collapses to ONE audit in an entire flaky
   * session, because a counter that restarts at every reacquisition never
   * reaches 30 when reacquisition arrives every ~17 tracked frames. A session
   * that keeps losing and regaining the face is precisely the one whose warm
   * chain is most likely to be in the wrong basin.
   *
   * `src/testkit/report-occlusion.ts` has a same-named `StabilityResult
   * .framesTracked` which is `perFrame.length` and has no relation to this. It
   * is exactly the shape of thing a later reader "fixes" into agreement.
   */
  framesTracked: number;
  acquisitions: number;
}

export function createTracker(
  model: FaceModel, options: Partial<TrackerOptions> = {},
): TrackerState {
  return {
    model,
    options: { ...TRACKER_DEFAULTS, ...options },
    rigidityScratch: new Float64Array(model.vertexCount),
    calibratedScratch: new Uint8Array(model.vertexCount),
    marchScratch: new Int32Array(model.vertexCount).fill(-1),
    landmarkPositions: landmarkSurface(model),
    smoother: new PoseSmoother(),
    lastRaw: null,
    lastSmoothed: null,
    latchQuiet: 0,
    latchedPose: null,
    velRing: [],
    velTime: 0,
    floorMm: null,
    floorDeg: null,
    latchEnterMmS: LATCH_ENTER_VEL_MMS,
    latchEnterDegS: LATCH_ENTER_VEL_DEGS,
    guardMmLast: LATCH_DRIFT_MM,
    guardDegLast: LATCH_DRIFT_DEG,
    lastCovariance: null,
    vfEma: null,
    vfPrev: null,
    priorMissLast: null,
    priorMissEma: null,
    priorMissTransLast: null,
    priorMissTransEma: null,
    fadeFrom: null,
    fadeOffset: null,
    fadeLeft: 0,
    latchEngages: 0,
    latchReleases: 0,
    latchReanchors: 0,
    latchedFrames: 0,
    basinEscapes: 0,
    basinAuditsRun: 0,
    basinAdoptionsSkipped: 0,
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
  /**
   * Per-vertex visibility in [0,1] from the uncertainty estimator's raster
   * (computed against the PREVIOUS pose — no same-frame feedback loop), or
   * null when unknown (acquisition, tests that predate it). The solve CULLS
   * what it cannot see: MediaPipe invents the far half-face from its frontal
   * prior, and those landmarks are bias, not noise — sigma inflation mutes
   * them (7x still clears the 12 px cutoff) where only exclusion removes
   * them. Applied as a smoothstep ramp into the rigidity channel, so weight
   * fades in and out instead of popping at a threshold.
   */
  visibility?: Float64Array | null;
  intrinsics: Intrinsics;
  /**
   * Seconds since the previous frame `track()` was CALLED on — the frame lock's
   * submit interval, not the consumed-frame interval.
   *
   * **This docstring used to say the opposite, and no caller has ever honoured
   * it.** `app/main.ts` passes `FrameLock.captureDt` — "seconds since the
   * previously SUBMITTED frame" — on missed and consumed frames alike. Time
   * inside a dropout is banked by `miss()` in `state.lostSeconds` and credited
   * back on the recovering frame by every clock in `track()`, so a caller that
   * pre-added the gap here would have it counted twice, by the motion prior and
   * the stall reset and the velocity clock.
   *
   * The old text described the right failure — a short `dt` makes the adaptive
   * cutoff read the accumulated displacement as one enormous velocity — and put
   * the remedy in the wrong place. It belongs at the smoother's call site, where
   * `gapSeconds` is in scope.
   */
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
  /** The noise scale the adaptive filter ran at this frame: 1 on a clean frame
   *  (and always, unless `smooth` is `'adaptive'`), up to
   *  `ADAPTIVE_NOISE_SCALE_MAX` on a heavily occluded one. NaN on a held or
   *  dropped frame — no solve, no sigma to scale by. Diagnostics readout;
   *  nothing gates on it. */
  noiseScale: number;
  /** Windowed velocity of the raw pose over the last LATCH_VEL_WINDOW frames,
   *  mm/s and deg/s — the signal the stillness latch gates on. NaN until the
   *  window fills and on held or dropped frames. Reported in EVERY mode, so a
   *  real session's diagnostics can re-derive the latch thresholds from the
   *  face they failed on. */
  velMmS: number;
  velDegS: number;
  /** This frame's one-sigma pose uncertainty from the calibrated solve
   *  covariance (mm / deg), and the still-velocity noise the window would
   *  read on a perfectly still head — the denominators the latch gates lift
   *  against. NaN when unavailable. */
  sigmaMm: number;
  sigmaDeg: number;
  noiseVelMmS: number;
  noiseVelDegS: number;
  /** The motion prior's information share of this frame's solve, per block
   *  (trace ratio at the solution). NaN when no prior ran — cold frames,
   *  the ring refilling, `motionPrior` off. The field instrument for the
   *  prior's strength: at frontal rest it should read near zero, at a
   *  tilted rest a substantial fraction, mid-turn near zero again. */
  priorShareRot: number;
  priorShareMm: number;
  /** The solve's a-posteriori variance factor — how mis-scaled the honest
   *  sigma claims measured this frame. NaN on held or dropped frames. */
  varianceFactor: number;
  /** The emitted pose is the latch anchor, bit-frozen. */
  latched: boolean;
  /** The emitted pose is inside a release/adoption crossfade. */
  fading: boolean;
  /** Set when the pose is held over from a previous frame. */
  held: boolean;
  reason: string | null;
}

/**
 * The stillness latch, v2 — `smooth: 'locked'`.
 *
 * What a resting wearer's eye wants is not smoothed motion but ZERO motion:
 * "the glasses jiggle in place" was the first real wearer's exact complaint,
 * and the adaptive filter's answer (smooth everything harder) bought delay
 * instead, because its noise proxy is permanently elevated on real frames.
 * While latched, the tracker emits the held pose EXACTLY — bit-frozen, no
 * residual crawl.
 *
 * **Why v1 was retired — the same wearer's next report.** v1 gated on
 * DISPLACEMENT: innovation against the held pose inside an enter band
 * (1.2 mm / 0.5°) latched, past an exit band (2.2 mm / 0.9°) released, both
 * as hard cuts. On their face it read "stuck and choppy", and the mechanism
 * is structural: slow deliberate motion moves well under a millimetre per
 * frame, so the enter band cannot tell it from rest — the latch engages
 * MID-MOTION, holds until the drift accumulates past exit, snaps 2 mm,
 * re-engages, and turns a slow head turn into a stutter. No displacement
 * band fixes that, because per-frame displacement is the wrong axis: rest
 * and slow motion differ in *persistence*, not in step size.
 *
 * v2 gates on a WINDOWED VELOCITY: displacement of the raw pose across the
 * last `LATCH_VEL_WINDOW` frames, divided by the elapsed time. Zero-mean
 * detector wander largely cancels over the window; persistent motion does
 * not. Enter needs both channels quiet for `LATCH_ENTER_FRAMES`; release
 * needs sustained velocity past the exit thresholds (hysteresis, so the
 * boundary cannot chatter) — OR the drift guard: innovation against the
 * anchor beyond `LATCH_DRIFT_MM`/`DEG`, which catches creep slower than the
 * velocity floor before it can accumulate a visible offset. Every release —
 * velocity, drift guard, or a basin-audit adoption — lands as a
 * `LATCH_FADE_FRAMES` crossfade toward the live pose instead of a cut: the
 * anchor's job is to own rest, not to make leaving rest an event the eye can
 * see.
 *
 * ## The gates calibrate themselves — nobody's face is the constant
 *
 * The enter/exit thresholds started as fixed constants from a synthetic
 * correlated-noise sweep, and the first real face convicted them inside a
 * day: that wearer's at-rest rotational wander (windowed p50 0.87 deg/s,
 * p90 2.14) sat ABOVE both the modeled enter (0.8) and exit (1.4), so the
 * latch chattered on their stillness — 34.7% latched, seven engage/release
 * cycles in nine seconds of sitting still. No fixed pair of numbers
 * survives this, because the rest floor is a property of the SESSION —
 * camera, detector, light, distance — not of the algorithm, and the same
 * wearer's fps drifted 30-36 across three diagnostics pastes.
 *
 * So the gates are LEARNED, per channel, from the one population of frames
 * the latch can vouch for: while latched, the emitted pose is bit-frozen
 * and the innovation is bounded by the drift guard, so those frames ARE
 * this session's rest, and their windowed velocities sample its floor. A
 * mean + absolute-deviation EMA over latched-frame velocities sets
 * enter = m + LATCH_FLOOR_MARGIN*d, clamped to [prior, prior*cap], with
 * exit = enter * LATCH_EXIT_RATIO. The estimate ratchets itself open:
 * latched samples are censored at the current exit, but every raise of the
 * gate widens what the next samples can show, so a floor 3x the prior is
 * learned in a few seconds of wear. It relaxes back the same way — when
 * the regime quiets, the latched samples quiet, and the EMA follows them
 * down to the prior clamp. Motion never feeds the estimator: unlatched
 * frames are somebody moving, and learning from them would teach the latch
 * to call motion rest.
 *
 * The shipped constants below are therefore PRIORS and CLAMPS, not
 * thresholds: the enter values are what an unlearned session starts from
 * (and the floor a learned gate may never tighten below — a quieter-than-
 * prior session just latches eagerly, which costs nothing), and the caps
 * bound what noise may claim (LATCH_FLOOR_CAP_DEG * 0.8 = 4.8 deg/s keeps
 * a 5 deg/s deliberate head turn followable in ANY regime). Provenance for
 * the priors is the original synthetic sweep; the caps and the estimator's
 * two tuning numbers are sized against the first wearer's field pastes and
 * verified as a MECHANISM across synthetic regimes at 1-10x the modeled
 * floor — see the ledger rows. The diagnostics panel reports the live
 * learned gates, so any session's paste shows what its face taught them.
 */
/**
 * The visibility cull ramp: below LO a landmark's vote is zero, above HI it
 * is untouched, smoothstepped between so weight fades instead of popping as
 * the head turns. Values measured on the hallucination fixture sweep — see
 * the ledger rows and the "visible half owns the solve" tests.
 */
export const VIS_CULL_LO = 0.1;
export const VIS_CULL_HI = 0.35;
/**
 * Visibility at or above which a landmark's sigma still counts as an HONEST
 * noise claim, eligible to calibrate the variance factor (see
 * `Correspondence.sigmaCalibrated`). At this facing the occlusion inflation
 * is 1 + 6*(1-v)^2 <= 1.06 — a <=4% residual bias on the estimate, against
 * the 18-28% understatement the pooled estimator carried. Measured on the
 * production raster (probe, template mesh): 43-73 static-rigid vertices
 * clear this cut across yaw 0-40, so the estimate never starves in the
 * working regime; the price is a noisier per-frame factor (dof ~80-140,
 * ~16% relative sd at frontal against the pooled ~5%), absorbed by the
 * guard's contraction decay and, for the motion prior, by its EMA.
 */
export const VF_CAL_MIN_VIS = 0.9;
/**
 * The redescending schedule: Barron's shape parameter as a function of how
 * well the camera can see a landmark.
 *
 * The cull removes what the raster says is hidden, and the sigma inflation
 * mutes what is oblique, but neither reaches the landmark this rank is for:
 * one that is only PARTIALLY hallucinated. Such a point stays front-facing,
 * survives any cull, carries a plausible sigma — and is simply in the wrong
 * place, because the detector drew that part of the face from its frontal
 * prior. Huber gives it constant force forever. Measured on the production
 * fixture, that is worth ~10.9 mm of translation bias at 40 degrees of yaw,
 * and it is the same number at every cull band, because no threshold
 * separates "partly invented" from "merely oblique".
 *
 * A redescending kernel does: past a few sigmas its influence falls back
 * toward zero, so a landmark that disagrees with the consensus stops
 * arguing. `BARRON_ALPHA_HIGH` = 1 is where the well-visible landmarks sit,
 * `BARRON_ALPHA_LOW` = -2 is Geman-McClure, and the schedule smoothsteps
 * between them across
 * [`BARRON_VIS_LO`, `BARRON_VIS_HI`] of raw facing cosine. The band brackets
 * the populated part of the partially-visible range: the measured facing
 * histogram puts ~112 vertices between 0.35 and 0.8 at 40 degrees, which is
 * exactly the population that survives the cull and should not be trusted
 * unconditionally.
 *
 * **alpha = 1 is NOT the pre-rank-5 behaviour, and an earlier draft of this
 * comment claimed it was.** Barron at alpha 1 is Charbonnier, a SMOOTH
 * Huber, and its weight sits below true Huber's everywhere inside the
 * threshold — 0.928 against 1 at one sigma, 0.707 against 1 at the
 * threshold itself. So on a scheduled frame EVERY correspondence changes
 * weight, not only the poorly-seen ones, and any measured effect is the sum
 * of a global kernel change and the schedule proper. The two are separated
 * by measurement rather than by assertion: the frontal experiment runs three
 * cells — Huber (control), alpha pinned to ALPHA_HIGH everywhere (the
 * kernel change alone), and the full schedule — and the ledger row carries
 * all three.
 *
 * One relationship is a hard invariant rather than a tuning choice:
 * `BARRON_VIS_HI` must stay at or below `VF_CAL_MIN_VIS`, so every landmark
 * the variance factor calibrates from sits at the schedule's fixed upper
 * end. Otherwise the factor rank 4's motion prior consumes would drift with
 * the schedule instead of describing the sigma stream. Asserted in the
 * tests, not merely written here.
 *
 * The schedule reads RAW visibility, deliberately not the effective
 * rigidity: the eye region's disenfranchisement is about gaze, a different
 * question, and compounding the two would be double-counting the same
 * hiddenness twice over — which the ledger already records the cull ramp and
 * the sigma inflation doing.
 */
export const BARRON_ALPHA_HIGH = 1;
export const BARRON_ALPHA_LOW = -2;
export const BARRON_VIS_LO = 0.35;
export const BARRON_VIS_HI = 0.8;

export const LATCH_VEL_WINDOW = 10;
export const LATCH_ENTER_VEL_MMS = 8.5;
export const LATCH_ENTER_VEL_DEGS = 0.8;
export const LATCH_EXIT_RATIO = 1.8;
export const LATCH_FLOOR_MARGIN = 3;
export const LATCH_FLOOR_RATE = 0.02;
export const LATCH_FLOOR_CAP_MM = 3;
export const LATCH_FLOOR_CAP_DEG = 6;
export const LATCH_DRIFT_MM = 2.2;
export const LATCH_DRIFT_DEG = 0.9;
/**
 * The covariance lifts, dimensionless. `LATCH_GATE_SNR` multiplies the
 * window's predicted still-velocity noise (endpoint sigmas in quadrature
 * over the span) into a gate floor; `LATCH_GUARD_SNR` multiplies the frame's
 * pose sigma into a drift-guard floor. Both are "how many sigmas of the
 * solve's own noise before we call it motion" — the regime-free form of the
 * question every absolute threshold here was approximating. Values from the
 * hallucination-fixture sweep; the followability caps bound both.
 */
export const LATCH_GATE_SNR = 3.5;
export const LATCH_GUARD_SNR = 5;
/**
 * Where the leaky anchor wakes, as a fraction of the drift guard.
 *
 * The first calibrated field session held the latch beautifully and then
 * BREATHED: the wearer's rest wander accumulated against the bit-frozen
 * anchor until the drift guard paid it out as a glide every couple of
 * seconds — 11 of 12 latch exits were drift re-anchors, and the wearer saw
 * every one. A stillness whose corrections arrive as periodic events is not
 * stillness; corrections must be continuous and individually invisible.
 *
 * So inside this fraction of the guard the anchor is EXACTLY frozen — that
 * deadband is the stillness the latch promises — and beyond it the anchor
 * pursues the raw pose just fast enough to hold the innovation at the
 * boundary, capped at the channel's ENTER velocity: the speed the gate
 * itself defines as indistinguishable from rest, so the pursuit is
 * invisible by the same definition that makes the latch latch. Each channel
 * pursues only while its windowed velocity reads under its enter gate —
 * genuine motion gets no pursuit, keeps accumulating, and leaves through
 * the guard or the velocity release as before; the guard survives as the
 * hard backstop the pursuit should rarely let it reach. A welcome corollary:
 * sub-enter creep, which used to advance as guard-snap sawteeth, is now
 * simply followed at creep speed one deadband behind.
 */
export const LATCH_SLEW_START = 0.5;
export const LATCH_ENTER_FRAMES = 3;
/**
 * The motion prior's constant-velocity fit runs over this many trailing
 * velRing entries (fewer while the ring refills; two is the working
 * minimum). The count sets the prior's structural strength ceiling: the
 * predictor's noise gain c = sum(w_i^2) is 5 at two points (the recursion's
 * fixed point caps the sigma reduction at 11% — the design review proved
 * the two-point form dead on arrival), 1.5 at four (ceiling 42%), under 1
 * at six — but the exact constant-acceleration error the fit commits grows
 * with the window (2.5 vs 1.0 units of a*dt^2 at four vs two points), so a
 * longer window trades onset lag for rest strength. Four is the shipped
 * balance; the Q constants below are sized against it.
 */
export const PRIOR_POINTS = 4;
/**
 * The head-acceleration bounds behind the prior's process noise, one per
 * channel type, face- and session-independent, priced through the window's
 * exact error functional (see `buildMotionPrior`).
 *
 * **These are 37x below the peak acceleration a head reaches, and that is
 * the correct reading of what they are.** The first draft sized them from
 * the wearer's recorded traces at the PEAK of a deliberate turn onset —
 * ~380 mm/s reached in ~0.25 s is ~1400 mm/s^2, and ~3 rad/s over the same
 * onset is ~12 rad/s^2 — which is the right number for the wrong quantity.
 * What the process noise has to describe is the residual acceleration a
 * constant-velocity fit over four frames fails to capture, across the whole
 * POPULATION of frames, and that population is dominated by a head sitting
 * nearly still. Sizing it at the peak asserts that the wearer might be
 * accelerating maximally on every frame, which throws the prior away on the
 * frames it exists to serve.
 *
 * The values are therefore MEASURED, by a paired-seed sweep over five
 * octaves (see the ledger rows), and the safety question the small numbers
 * obviously raise — does a prior this strong lag a genuinely hard motion? —
 * is answered by the sweep's adversarial cell rather than by argument: a
 * ~1.7 rad/s turn sweeping 25-55 degrees of yaw, where these constants are
 * violated by two orders of magnitude, costs 0.35 frames (12 ms) of lag and
 * IMPROVES accuracy by 3.0%, in 5 seeds of 5. The mechanism that makes that
 * safe is the information share: even a badly violated prior carries only
 * 2% of the solve at frontal and 17% at 40 degrees, so it can move the
 * answer by a bounded fraction and no more. The untested corner, stated:
 * hard acceleration beyond ~55 degrees, where the landmark information is
 * weakest and the share is highest.
 */
export const MOTION_PRIOR_ACCEL_MM_S2 = 37.5;
export const MOTION_PRIOR_ACCEL_RAD_S2 = 0.375;

/**
 * How fast the prior's honesty estimate forgets, per frame that carried a
 * prior.
 *
 * `stated`: 0.25 is about four frames of memory at 30 fps, chosen against the
 * thing being tracked rather than swept — a head reverses two or three times a
 * second, so the estimate has to relax between reversals or a single shake
 * would suppress the prior for the rest of the session. It is deliberately the
 * SLOW half of the pair: `buildMotionPrior` takes `max(last, EMA)`, so the
 * stand-aside on a violated prediction is immediate at any rate, and this
 * constant governs only how quickly trust returns.
 */
export const PRIOR_MISS_EMA_RATE = 0.25;
/**
 * The longest interval between two ring entries the prior will fit across.
 *
 * The process noise already prices a long lever arm — a window straddling a
 * 0.4 s gap reads 3.3x the acceleration slack of an ordinary one — but that
 * pricing assumes CONSTANT acceleration, and darkness can hide a reversal,
 * which no acceleration bound covers. So a gap simply ends the window: the
 * prior fits only frames whose motion something actually watched, and costs
 * one prior-less frame after any sub-reset dropout. 0.15 s is about four
 * frames at 30 fps and five at 36 — comfortably past ordinary detector
 * jank at either end of the session fps drift this app has measured, and
 * well under the 0.5 s at which everything else here declares the motion
 * over.
 */
export const PRIOR_MAX_STEP_S = 0.15;
/**
 * EMA rate on the per-frame variance factor — the session's sigma-claim
 * honesty estimate that scales the prior into the solve's claim units.
 * ~10-frame time constant: fast enough to track a regime change inside a
 * second, slow enough to hold the per-frame factor's ~16% noise to a few
 * percent. The prior's weight uses max(EMA, previous frame's factor), so a
 * one-frame spike (a hand the raster cannot see) strengthens the prior
 * immediately instead of waiting out the EMA's lag.
 */
export const VF_EMA_RATE = 0.1;
/**
 * 3, down from 5 (2026-08-23): the wearer's localization experiment placed
 * the "delay" squarely in the first instant after stillness, and the fade
 * is the deliberate half of that instant. The leaky anchor shrank the
 * offset a release pays out (innovation is pinned near the pursuit deadband
 * instead of the full drift guard), so the shorter fade's per-frame steps
 * stay comparable to the old five-frame payout of a bigger offset.
 */
export const LATCH_FADE_FRAMES = 3;

export function track(state: TrackerState, input: TrackInput): TrackResult {
  const { model, options } = state;
  // The model's geometry in the detector's convention, for every comparison
  // below — see `TrackerState.landmarkPositions`. `model` itself is still read
  // for `vertexCount`; its `positions` are skin and belong to the seat.
  const positions = state.landmarkPositions;

  if (!input.landmarks || !input.sigmaPx) {
    return miss(state, input.dt, 'no face detected');
  }

  // The unclaimed dropout time riding on this frame, read BEFORE anything
  // else: the motion prior must judge the same COMBINED darkness the stall
  // reset below judges — a split judgement already let a second of gap
  // through two half-checks once. (The prior's gate uses the 1/30 fallback
  // where the reset's uses 0 for a non-positive dt; the prior is merely
  // skipped more eagerly, which costs one prior-less frame.)
  const gapSeconds = state.lostSeconds;
  const dtSolve = input.dt > 0 ? input.dt : 1 / 30;
  // The constant-velocity MAP prior — see `buildMotionPrior`. Only for warm
  // solves on a live ring: a cleared ring is a motion that is over, and a
  // gap past the reset span is about to clear it.
  //
  // Built HERE, above the correspondences, because the redescending schedule
  // below has to know whether this frame will carry a prior: a non-convex
  // kernel is only safe once something is holding the solve in the right
  // basin, and the prior's prediction is that something. Nothing in
  // `buildMotionPrior` reads the correspondences, so the order is free.
  const prior = options.motionPrior && state.lastRaw && state.lastCovariance
      && state.velRing.length >= 2
      && dtSolve + gapSeconds <= options.lostSecondsBeforeReset
    ? buildMotionPrior(state, dtSolve + gapSeconds)
    : null;

  // The effective rigidity this frame: the static map (the eye region's
  // disenfranchisement) times the visibility ramp (the far side's) — one
  // currency into the solver. The wearer's phrasing, made literal: the solve
  // is owned by the half of the face the camera can actually see.
  let rigidity = options.rigidity ?? undefined;
  if (input.visibility) {
    const eff = state.rigidityScratch;
    for (let i = 0; i < model.vertexCount; i++) {
      const ramp = smoothstep(VIS_CULL_LO, VIS_CULL_HI, input.visibility[i]);
      eff[i] = (options.rigidity ? options.rigidity[i] : 1) * ramp;
    }
    rigidity = eff;
  }
  // The variance-factor eligibility mask: a sigma is an honest noise claim
  // only where no deliberate inflation touched it — the STATIC rigidity map
  // untouched (the ramp half of the effective product is bounded by the
  // visibility test: ramp < 1 implies visibility < VIS_CULL_HI < the cut)
  // and the occlusion inflation negligible. See VF_CAL_MIN_VIS.
  const calibrated = state.calibratedScratch;
  for (let i = 0; i < model.vertexCount; i++) {
    calibrated[i] = (options.rigidity == null || options.rigidity[i] >= 0.999)
      && (input.visibility == null || input.visibility[i] >= VF_CAL_MIN_VIS) ? 1 : 0;
  }
  const sigmaCulled: number[] = [];
  const correspondences: Correspondence[] = buildCorrespondences(
    input.landmarks, input.sigmaPx, model.vertexCount,
    rigidity, options.maxSigmaPx, calibrated, sigmaCulled,
  );
  if (correspondences.length < options.minCorrespondences) {
    return miss(state, input.dt, `only ${correspondences.length} usable landmarks`);
  }

  // Landmark marching — see `silhouetteStrips`. The oval landmarks are
  // rematched to whichever vertex is actually on the occluding contour under
  // the pose we currently believe: the prior's prediction where there is
  // one, the previous raw pose otherwise. Never the pose being solved for,
  // which would close a loop around the estimate.
  const predicted = prior ? prior.pose : state.lastRaw;
  if (options.ovalStrips && predicted) {
    for (const strip of options.ovalStrips) {
      state.marchScratch[strip.landmark] = marchStrip(strip, positions, predicted);
    }
    for (const c of correspondences) {
      const marched = state.marchScratch[c.vertex];
      // **This is NOT a no-op at frontal, which this comment used to claim.**
      // A strip is a HORIZONTAL row of candidates, and on the top and bottom
      // arcs of the oval the rim runs horizontally too — so the row lies
      // ALONG the contour instead of across it, and the most edge-on vertex in
      // it is a more lateral neighbour. At an EXACT frontal pose 10 of the 34
      // strips remap, by 11.7 to 20.0 mm (mean 15.1): 338->297, 297->332,
      // 377->400, 400->378, 378->379, their four mirrors, and 67->103. It is
      // the regime `silhouetteStrips`' `midlineMm` cut already exempts 10 and
      // 152 for, reaching further round the ring than that cut does.
      //
      // Nor is it a prediction artefact: the chosen vertex is IDENTICAL for a
      // prediction wrong by up to 2 degrees on any axis, and by 10 in pitch or
      // roll — the first change is one strip at 5 degrees of yaw.
      //
      // Left standing, and measured both ways. See `docs/CONSTANTS.md`'s
      // `silhouetteStrips` row: over twelve seeds the frontal displacement
      // costs nothing visible, and exempting those ten strips gives back the
      // ENTIRE marching gain at 25 degrees of yaw. `core.test.ts` pins that
      // trade so the obvious repair cannot be made quietly.
      if (marched >= 0) c.vertex = marched;
    }
    state.marchScratch.fill(-1);
  }

  // The redescending schedule — see `BARRON_VIS_LO`. A landmark the camera
  // can see keeps a Huber-like kernel; one it can barely see gets a
  // redescending one, so a partially-hallucinated point's influence can fall
  // to zero instead of pulling with constant force forever. Only stamped
  // when a prior exists to hold the basin, and only honoured by the solve
  // that carries that prior — the cold retry and the basin audit reuse this
  // same array and must stay convex.
  const redescending = options.redescending && prior !== null && input.visibility != null;
  if (redescending) {
    for (const c of correspondences) {
      c.lossAlpha = BARRON_ALPHA_HIGH + (BARRON_ALPHA_LOW - BARRON_ALPHA_HIGH)
        * (1 - smoothstep(BARRON_VIS_LO, BARRON_VIS_HI, input.visibility![c.vertex]));
    }
  }

  // Warm start from the prediction where a prior exists (the fit's
  // extrapolation is the best available start during motion), from the
  // previous raw pose otherwise; POSIT from scratch only on acquisition.
  // Every solve that could become this frame's result carries its calibrated
  // covariance — the per-frame statement of how much the solve can know,
  // which the latch gates are normalized against. One 6x6 inversion.
  const COV = { wantCovariance: true };
  // Both pixel gates below are sized for THIS camera rather than for the one
  // they were measured on — see `GATE_REFERENCE_F_PX`. 1, and therefore a
  // no-op, at every geometry in the synthetic ladder.
  const gateScale = pixelGateScale(input.intrinsics);
  const rmsBarPx = options.maxRmsPx * gateScale;
  let result: PnPResult;
  let coldAcquired = false;
  if (state.lastRaw) {
    result = prior
      ? refinePnP(positions, correspondences, input.intrinsics, prior.pose,
        { wantCovariance: true, prior, redescending })
      : refinePnP(positions, correspondences, input.intrinsics, state.lastRaw, COV);
    // A warm start that lands badly is usually a warm start that was stale —
    // the head moved a lot while we were not looking. Retry cold before giving
    // up, because a cold solve at any pose is the whole point of having a model.
    // The cold solve carries NO prior, deliberately: its whole job is to
    // escape wherever the warm chain — prior included — got stuck.
    if (!(result.rmsPx <= rmsBarPx)) {
      const cold = solvePnP(positions, correspondences, input.intrinsics, undefined, COV);
      if (cold.rmsPx < result.rmsPx) { result = cold; coldAcquired = true; }
    }
  } else {
    result = solvePnP(positions, correspondences, input.intrinsics, undefined, COV);
    coldAcquired = true;
  }

  if (!(result.rmsPx <= rmsBarPx) || !(result.pose.t[2] > 50)) {
    // The bar travels with the number, because on a wide-angle or low-resolution
    // camera it is no longer the 14 px `docs/CONSTANTS.md` names and a
    // diagnostics paste would otherwise be unreadable against the ledger.
    return miss(state, input.dt,
      `reprojection ${result.rmsPx.toFixed(1)} px (bar ${rmsBarPx.toFixed(1)})`);
  }
  // The second half of the gate: how much of this frame is describing
  // something that is not this face. Checked AFTER the rms so the reason
  // string names whichever question actually failed.
  //
  // **Counted over the landmarks the sigma cull dropped as well.** `gross` was
  // documented as "a count and cannot be diluted by weighting", and that was
  // true and beside the point: it was being diluted by CULLING instead, one
  // step earlier. A landmark whose sigma passed `maxSigmaPx` never reached
  // `residualStats`, so a frame could be made to look clean by declaring the
  // inconvenient landmarks uncertain — and a BETTER sigma stream does exactly
  // that, isolating an intruding face so cleanly that the solve is handed a
  // coherent subset of it to fit. Measured on `core.test.ts`'s second-face
  // stream at 60%: 468 landmarks produced, 49 surviving, 0.102 over the
  // survivors against 0.355 over the frame, accepted at 11.7 mm from the
  // INTRUDER's truth and 47.7 mm from the wearer's.
  //
  // A culled landmark is counted only if it is actually gross — projected at
  // the solved pose and more than `GROSS_ERROR_PX` away. Counting its absence
  // instead was tried and measured to refuse the wearer for TALKING: lip
  // landmarks are outside the rigidity map and fully visible, sustained speech
  // inflates them past the cull, and charging that as "elsewhere" refused 22 of
  // 35 frames on a still frontal face. A lip moves tens of pixels; a second
  // face's landmarks are fifty off. The threshold already separates them.
  let grossN = result.grossFraction * result.grossTotal;
  let grossTotal = result.grossTotal;
  if (sigmaCulled.length > 0) {
    const cam = v3();
    const uv = new Float64Array(2);
    const R = result.pose.R;
    for (const i of sigmaCulled) {
      const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + result.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + result.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + result.pose.t[2];
      if (!project(uv, input.intrinsics, cam)) continue;
      grossTotal++;
      if (Math.hypot(uv[0] - input.landmarks[i * 2], uv[1] - input.landmarks[i * 2 + 1])
        > GROSS_ERROR_PX * gateScale) grossN++;
    }
  }
  const grossFraction = grossTotal > 0 ? grossN / grossTotal : 0;
  if (grossFraction > options.maxGrossFraction) {
    return miss(state, input.dt,
      `${(grossFraction * 100).toFixed(0)}% of landmarks are elsewhere`);
  }
  // Counted only past the gate: 'acquisitions' means times the tracker
  // actually acquired from scratch. Counting at the solve, as this used to,
  // let a hand-over-face spell — cold retry beating a stale warm start,
  // gate rejecting both — inflate the counter by one per occluded frame,
  // ~60 phantom acquisitions per two-second occlusion in the diagnostics.
  if (coldAcquired) state.acquisitions++;

  // The basin audit — see `TrackerOptions.basinAuditInterval`. Runs on frames
  // the gate is HAPPY with, because that is exactly where a wrong basin hides.
  if (state.lastRaw && options.basinAuditInterval > 0
      && state.framesTracked % options.basinAuditInterval === 0) {
    const audit = solvePnP(positions, correspondences, input.intrinsics, undefined, COV);
    state.basinAuditsRun++;
    if (audit.rmsPx <= rmsBarPx
        && audit.rmsPx < result.rmsPx * options.basinRescueRatio) {
      // The adoption deadband: a decisive rms win at a near-identical pose is
      // the same basin, and adopting it would spend a smoother reset on
      // nothing. See `TrackerOptions.basinAdoptMinMm`.
      const dMm = Math.hypot(
        audit.pose.t[0] - result.pose.t[0],
        audit.pose.t[1] - result.pose.t[1],
        audit.pose.t[2] - result.pose.t[2],
      );
      const dDeg = (rotationAngleBetween(audit.pose.R, result.pose.R) * 180) / Math.PI;
      if (dMm < options.basinAdoptMinMm && dDeg < options.basinAdoptMinDeg) {
        state.basinAdoptionsSkipped++;
      } else {
        // One expression, deliberately: the adoption, its count, and the death
        // of the old basin's memory (latch, smoother, velocity window) are a
        // single unit, so no surgical edit can keep the bookkeeping while
        // dropping the rescue — the first breakage pass caught exactly that
        // split and this shape is the fix.
        result = adoptAuditPose(state, audit);
      }
    }
  }

  // Dropout frames consume real time but never reach the clock below —
  // miss() banks their dt in lostSeconds, and the frame that recovers must
  // credit it, or the velocity window straddles the gap with a foreshortened
  // span and over-reads by up to ~2.4x (a sub-reset spell can be 14 frames)
  // for the next full window: enough to refuse a resting latch, or to
  // spuriously release one right after the wearer's hand leaves their face.
  // (`gapSeconds` is read above the solve, because the motion prior has to
  // make the same judgement about the same darkness.)
  state.consecutiveFailures = 0;
  state.lostSeconds = 0;
  state.framesTracked++;
  state.lastRaw = poseClone(result.pose);

  // A consumed frame arriving after more unobserved time than the dropout-
  // reset span is a stall — a tab switch, a GC pause, detector jank — and
  // the ring's history predates a gap nothing observed. Credited into the
  // span it would DILUTE the windowed velocity instead (one huge denominator
  // over ten old poses), and a head panning at 30 mm/s right after a
  // one-second stall would read ~1-3 mm/s for three frames: quiet enough to
  // latch mid-pan. The judgement is the miss path's, at the same threshold,
  // over the same quantity — dt PLUS any sub-reset miss gap riding on this
  // frame, because darkness split across the two counters is still one gap
  // (the review caught ~1.0 s of unobserved motion evading both halves).
  // And it is the miss path's WHOLE judgement: the anchor, the fade and the
  // pursuit memory describe a motion that is over — a latch held across a
  // two-second tab-switch used to pay the entire gap displacement out as a
  // three-frame swoop from the stale anchor, booked as a drift re-anchor.
  if ((input.dt > 0 ? input.dt : 0) + gapSeconds > options.lostSecondsBeforeReset) {
    state.velRing.length = 0;
    state.latchQuiet = 0;
    state.latchedPose = null;
    state.fadeFrom = null;
    state.fadeOffset = null;
    state.fadeLeft = 0;
    // The motion prior needs no separate kill here: it reads the ring, and
    // the ring is now empty, so it cannot fire until two observed frames
    // have rebuilt it. `vfEma` deliberately survives — how honest this
    // session's sigma claims are is a property of the camera and the light,
    // like the latch's rest floor, and darkness does not change it.
  }

  // The prior's memory for the NEXT frame. A singular Hessian stores null —
  // the next frame then solves prior-less rather than reusing a covariance
  // that describes a different frame, which is the same honesty rule the
  // sigma carry-forward above breaks deliberately for a pure readout.
  state.lastCovariance = result.covariance;

  // Grade the prediction this frame's prior made, against where the solve
  // actually landed, in units of the sigma the prior itself claimed. Only on
  // frames that carried a prior — a prior-less frame says nothing about the
  // constant-velocity model, and letting it decay the estimate would hand the
  // next reversal a stale all-clear.
  if (prior) {
    const relPrior = m3();
    const wPrior = v3();
    logSO3(wPrior, m3mul(relPrior, result.pose.R, m3transpose(m3(), prior.pose.R)));
    const residRot = Math.hypot(wPrior[0], wPrior[1], wPrior[2]);
    const ratio = residRot / Math.max(prior.sigmaRot, 1e-9);
    state.priorMissLast = ratio;
    state.priorMissEma = state.priorMissEma === null
      ? ratio
      : state.priorMissEma + PRIOR_MISS_EMA_RATE * (ratio - state.priorMissEma);

    // And the same question of the translation, which until 2026-08-31 was
    // never asked. `prior.pose.t` is the constant-velocity prediction and
    // `result.pose.t` is where the landmarks put it, so their distance in
    // units of the sigma the prediction CLAIMED is exactly the rotation
    // channel's grade with the other three degrees of freedom in it.
    const residMm = Math.hypot(
      result.pose.t[0] - prior.pose.t[0],
      result.pose.t[1] - prior.pose.t[1],
      result.pose.t[2] - prior.pose.t[2],
    );
    const ratioMm = residMm / Math.max(prior.sigmaMm, 1e-9);
    state.priorMissTransLast = ratioMm;
    state.priorMissTransEma = state.priorMissTransEma === null
      ? ratioMm
      : state.priorMissTransEma + PRIOR_MISS_EMA_RATE * (ratioMm - state.priorMissTransEma);
  }

  if (Number.isFinite(result.varianceFactor) && result.varianceFactor > 0) {
    state.vfPrev = result.varianceFactor;
    state.vfEma = state.vfEma === null
      ? result.varianceFactor
      : state.vfEma + VF_EMA_RATE * (result.varianceFactor - state.vfEma);
  }

  // This frame's one-sigma pose uncertainty, mm and degrees, from the
  // calibrated covariance. A singular Hessian (rare, catastrophic frames the
  // rms gate usually refuses anyway) carries the previous frame's value
  // forward rather than inventing certainty or panic.
  let sigmaMm = NaN;
  let sigmaDeg = NaN;
  if (result.covariance) {
    const C = result.covariance;
    sigmaDeg = (Math.sqrt(Math.max(0, (C[0] + C[7] + C[14]) / 3)) * 180) / Math.PI;
    sigmaMm = Math.sqrt(Math.max(0, (C[21] + C[28] + C[35]) / 3));
  } else if (state.velRing.length > 0) {
    const last = state.velRing[state.velRing.length - 1];
    sigmaMm = last.sigmaMm;
    sigmaDeg = last.sigmaDeg;
  }

  // The velocity window rides the raw stream in every mode: the latch gates
  // on it, and the readout reports it so a real session's numbers can put the
  // thresholds on trial.
  state.velTime += (input.dt > 0 ? input.dt : 1 / 30) + gapSeconds;
  state.velRing.push({ pose: poseClone(result.pose), time: state.velTime, sigmaMm, sigmaDeg });
  if (state.velRing.length > LATCH_VEL_WINDOW + 1) state.velRing.shift();
  let velMmS = NaN;
  let velDegS = NaN;
  let noiseVelMmS = NaN;
  let noiseVelDegS = NaN;
  if (state.velRing.length === LATCH_VEL_WINDOW + 1) {
    const a = state.velRing[0], b = state.velRing[state.velRing.length - 1];
    const span = b.time - a.time;
    if (span > 0) {
      velMmS = Math.hypot(
        b.pose.t[0] - a.pose.t[0], b.pose.t[1] - a.pose.t[1], b.pose.t[2] - a.pose.t[2],
      ) / span;
      velDegS = ((rotationAngleBetween(b.pose.R, a.pose.R) * 180) / Math.PI) / span;
      // What the windowed velocity would read on a PERFECTLY STILL head: the
      // window is an endpoint difference, so its noise is the two endpoint
      // sigmas in quadrature over the span. This is the denominator that
      // makes the latch gates dimensionless — "velocity in units of what
      // this regime's solve can know".
      if (Number.isFinite(a.sigmaMm) && Number.isFinite(b.sigmaMm)) {
        noiseVelMmS = Math.hypot(a.sigmaMm, b.sigmaMm) / span;
        noiseVelDegS = Math.hypot(a.sigmaDeg, b.sigmaDeg) / span;
      }
    }
  }

  // `'adaptive'` reads the frame's noise off the sigma the tracker was handed
  // anyway; `true` passes the neutral scale, which is bit-identical to a build
  // that never had the parameter (the cutoff is divided by exactly 1).
  const noiseScale = options.smooth === 'adaptive'
    ? noiseScaleFromSigma(input.sigmaPx, options.adaptiveFloorPx)
    : 1;
  /**
   * The dropout gap belongs to the filter's clock too, and it was the one clock
   * that never got it.
   *
   * `track()` reads `input.dt` in five places. The motion prior, the stall reset
   * and the velocity clock all add `gapSeconds` — the time `miss()` banked in
   * `state.lostSeconds` while the face was gone. This call did not, so on the
   * frame that recovers a dropout the filter was told one frame had passed when
   * up to fourteen had. (The latch pursuit below is uncredited as well, and is
   * left that way deliberately: it walks an anchor toward the raw pose only
   * while the head reads as still, so nothing observed the gap.)
   *
   * **The test that says the credit belongs here is hermeticity.** A gap the
   * tracker WATCHED go dark and a gap it was simply not called during describe
   * the same wall clock and must produce the same pose. Measured, noiseless,
   * a 120 mm/s slide, dropout of N frames:
   *
   *     N     shipped |A - B|     credited
   *      1       0.754 mm            0
   *      5       2.081               4e-28
   *     14       2.884 mm            0
   *
   * And the recovery lag stops depending on how long the face was gone: 5.798
   * mm at N=14 becomes 2.914, which is just the filter's steady-state lag at
   * that speed.
   *
   * **What the short `dt` actually did is not only lag.** `raw = dx/dt` inflates
   * by up to 15x, which blows the beta term's cutoff wide open, so the one frame
   * of gross lag is followed by about five frames running effectively
   * unfiltered — a jerk and then a wobble, once per dropout.
   *
   * The trade is explicit and small: up to 2.4 mm less recovery lag for up to
   * 2.7 mm more on the single catch-up step. Gaps of 1-5 frames move nothing
   * either way (-0.13 to +0.39 mm).
   *
   * **It is NOT the mechanism behind the locked latch feeling stuck**, which is
   * the hypothesis this was measured to test. In `'locked'` at rest the fix is
   * bit-identical to four decimals over a 600-frame session with 150 dropped
   * frames, because a latched frame emits `poseClone(state.latchedPose)` and the
   * One Euro output is never read at all. The honest case for the change is
   * simpler than the complaint: a filter told the wrong `dt` does not have a
   * cutoff in Hz.
   */
  let smoothed = options.smooth
    ? state.smoother.filter(
      result.pose, (input.dt > 0 ? input.dt : 1 / 30) + gapSeconds, noiseScale)
    : poseClone(result.pose);

  // The gates the latch runs THIS frame. Two noise sources, two mechanisms,
  // composed by max():
  //   - COMMON-MODE DETECTOR WANDER (the whole landmark set drifts together)
  //     is invisible to the solve's residuals — the pose genuinely wanders —
  //     so it can only be LEARNED, per session, by the rest-floor calibrator.
  //   - SOLVE NOISE (hallucinated far side culled, conditioning thinned at
  //     tilt) is exactly what the calibrated covariance predicts, per frame,
  //     with no learning and no bootstrap: the gate lifts the moment the
  //     regime worsens. This is what un-deadlocks the latch at tilt — the
  //     field session where 11 s of tilted rest latched zero frames.
  // max() rather than quadrature: the learned floor already contains the
  // solve noise of the regime it was learned in; adding them would double-
  // count. The followability ceiling binds both.
  const capMm = LATCH_ENTER_VEL_MMS * LATCH_FLOOR_CAP_MM;
  const capDeg = LATCH_ENTER_VEL_DEGS * LATCH_FLOOR_CAP_DEG;
  const enterMmBase = gateFrom(state.floorMm, LATCH_ENTER_VEL_MMS, LATCH_FLOOR_CAP_MM);
  const enterDegBase = gateFrom(state.floorDeg, LATCH_ENTER_VEL_DEGS, LATCH_FLOOR_CAP_DEG);
  const enterMm = Number.isFinite(noiseVelMmS)
    ? Math.min(capMm, Math.max(enterMmBase, LATCH_GATE_SNR * noiseVelMmS))
    : enterMmBase;
  const enterDeg = Number.isFinite(noiseVelDegS)
    ? Math.min(capDeg, Math.max(enterDegBase, LATCH_GATE_SNR * noiseVelDegS))
    : enterDegBase;
  state.latchEnterMmS = enterMm;
  state.latchEnterDegS = enterDeg;

  // The drift guard and pursuit deadband lift the same way: at tilt the
  // innovation against a frozen anchor is noisier because the SOLVE is, and
  // a fixed 2.2 mm budget would trip on noise. Capped at twice the budget —
  // accuracy is still the point, and offsets below the solve's own noise
  // floor are imperceptible in exactly the regimes that need the room.
  let guardMm = Math.min(LATCH_DRIFT_MM * 2, Math.max(
    LATCH_DRIFT_MM, Number.isFinite(sigmaMm) ? LATCH_GUARD_SNR * sigmaMm : 0,
  ));
  let guardDeg = Math.min(LATCH_DRIFT_DEG * 2, Math.max(
    LATCH_DRIFT_DEG, Number.isFinite(sigmaDeg) ? LATCH_GUARD_SNR * sigmaDeg : 0,
  ));
  // While a latch is held the guard may not CONTRACT faster than 10% per
  // frame: the pursuit parks the innovation just past the deadband the WIDE
  // guard granted, and a one-frame sigma drop (an occluding hand leaving, a
  // singular-covariance frame right after a ring clear) would pull the guard
  // under that parked, perfectly-legal innovation and release a still head.
  // The review mechanized exactly that release; the decay gives the pursuit
  // the handful of frames it needs to walk the innovation back down.
  if (state.latchedPose) {
    if (guardMm < state.guardMmLast * 0.9) guardMm = state.guardMmLast * 0.9;
    if (guardDeg < state.guardDegLast * 0.9) guardDeg = state.guardDegLast * 0.9;
  }
  state.guardMmLast = guardMm;
  state.guardDegLast = guardDeg;

  let latched = false;
  if (options.smooth === 'locked') {
    if (state.latchedPose) {
      const dMm = Math.hypot(
        result.pose.t[0] - state.latchedPose.t[0],
        result.pose.t[1] - state.latchedPose.t[1],
        result.pose.t[2] - state.latchedPose.t[2],
      );
      const dDeg = (rotationAngleBetween(result.pose.R, state.latchedPose.R) * 180) / Math.PI;
      const velRelease = Number.isFinite(velMmS)
        && (velMmS > enterMm * LATCH_EXIT_RATIO || velDegS > enterDeg * LATCH_EXIT_RATIO);
      const driftRelease = dMm > guardMm || dDeg > guardDeg;
      if (velRelease || driftRelease) {
        // Real motion, or creep slower than the velocity floor that has
        // finally accumulated: either way the anchor is done, and it leaves
        // through the crossfade rather than as a cut. The smoother is NOT
        // reset — it filtered every frame straight through the latch, so its
        // state is current and the fade's moving target is the pose the
        // wearer already judged acceptable in 'on'.
        if (velRelease) state.latchReleases++;
        else state.latchReanchors++;
        state.fadeFrom = poseClone(state.latchedPose);
        state.fadeLeft = LATCH_FADE_FRAMES;
        state.latchedPose = null;
        state.latchQuiet = 0;
      } else {
        // The leaky anchor — see LATCH_SLEW_START. Runs BEFORE the emit
        // clone so this frame's output carries the pursuit step; the release
        // checks above used the pre-pursuit innovation on purpose, so the
        // pursuit can never mask a release the guard was owed.
        const dt = input.dt > 0 ? input.dt : 1 / 30;
        const startMm = guardMm * LATCH_SLEW_START;
        if (Number.isFinite(velMmS) && velMmS < enterMm && dMm > startMm) {
          const pull = Math.min(enterMm * dt, dMm - startMm) / dMm;
          state.latchedPose.t[0] += (result.pose.t[0] - state.latchedPose.t[0]) * pull;
          state.latchedPose.t[1] += (result.pose.t[1] - state.latchedPose.t[1]) * pull;
          state.latchedPose.t[2] += (result.pose.t[2] - state.latchedPose.t[2]) * pull;
        }
        const startDeg = guardDeg * LATCH_SLEW_START;
        if (Number.isFinite(velDegS) && velDegS < enterDeg && dDeg > startDeg) {
          // R_anchor <- exp(s*w) * R_anchor, w = log(R_raw * R_anchor^T):
          // the same geodesic pursuit the crossfade uses, scaled to pull the
          // rotational innovation back to the deadband at sub-enter rate.
          const w = v3();
          logSO3(w, m3mul(m3(), result.pose.R, m3transpose(m3(), state.latchedPose.R)));
          const s = Math.min(enterDeg * dt, dDeg - startDeg) / dDeg;
          const slewed = m3mul(m3(), expSO3(m3(), v3(s * w[0], s * w[1], s * w[2])), state.latchedPose.R);
          orthonormalize(state.latchedPose.R, slewed);
        }
        smoothed = poseClone(state.latchedPose);
        latched = true;
        state.latchedFrames++;
        // A latched frame is the one kind the latch can vouch for as rest —
        // the emitted pose is frozen inside the pursuit deadband and the
        // drift guard bounds the innovation — so its windowed velocity
        // samples this session's rest floor. Motion never reaches this
        // branch, so it can never teach the gates to call motion rest. The
        // samples are censored at the current exit, and that is what makes
        // the estimate a RATCHET: each raise of the gate widens what the
        // next samples can show, so a floor well above the prior is learned
        // in a few seconds of chatter instead of never.
        if (Number.isFinite(velMmS)) {
          state.floorMm = feedFloor(state.floorMm, velMmS);
          state.floorDeg = feedFloor(state.floorDeg, velDegS);
        }
      }
    } else if (state.fadeLeft === 0 && Number.isFinite(velMmS)
        && velMmS < enterMm && velDegS < enterDeg) {
      state.latchQuiet++;
      if (state.latchQuiet >= LATCH_ENTER_FRAMES) {
        state.latchedPose = poseClone(smoothed);
        state.latchEngages++;
        state.latchedFrames++;
        latched = true;
      }
    } else {
      state.latchQuiet = 0;
    }
  } else {
    state.latchedPose = null;
    state.latchQuiet = 0;
  }

  // The crossfade. Not a pose blend: blending toward a target that is itself
  // moving compounds the payout with the motion, and the last fade steps of a
  // slide end up nearly as large as the cut being avoided. Instead the first
  // fade frame captures the ERROR — where the eye last saw the glasses,
  // relative to the live pose — and that fixed offset decays to zero over
  // LATCH_FADE_FRAMES while live motion rides through underneath at full
  // rate. The final fade frame carries zero offset: the fade cannot end on a
  // step, by construction.
  if (!latched && state.fadeLeft > 0) {
    if (state.fadeFrom) {
      const w = v3();
      logSO3(w, m3mul(m3(), state.fadeFrom.R, m3transpose(m3(), smoothed.R)));
      state.fadeOffset = {
        t: v3(
          state.fadeFrom.t[0] - smoothed.t[0],
          state.fadeFrom.t[1] - smoothed.t[1],
          state.fadeFrom.t[2] - smoothed.t[2],
        ),
        w,
      };
      state.fadeFrom = null;
    }
    if (state.fadeOffset) {
      const rem = (state.fadeLeft - 1) / LATCH_FADE_FRAMES;
      const o = state.fadeOffset;
      const out = poseClone(smoothed);
      out.t[0] += rem * o.t[0];
      out.t[1] += rem * o.t[1];
      out.t[2] += rem * o.t[2];
      m3mul(out.R, expSO3(m3(), v3(rem * o.w[0], rem * o.w[1], rem * o.w[2])), smoothed.R);
      orthonormalize(out.R, out.R);
      smoothed = out;
    }
    state.fadeLeft--;
    if (state.fadeLeft === 0) state.fadeOffset = null;
  }
  state.lastSmoothed = poseClone(smoothed);

  return {
    tracked: true,
    pose: smoothed,
    rawPose: result.pose,
    rmsPx: result.rmsPx,
    correspondences: correspondences.length,
    inliers: result.inliers,
    euler: headEuler(smoothed),
    noiseScale,
    smoothingLagMm: Math.hypot(
      smoothed.t[0] - result.pose.t[0],
      smoothed.t[1] - result.pose.t[1],
      smoothed.t[2] - result.pose.t[2],
    ),
    smoothingLagDeg: (rotationAngleBetween(smoothed.R, result.pose.R) * 180) / Math.PI,
    velMmS,
    velDegS,
    sigmaMm,
    sigmaDeg,
    noiseVelMmS,
    noiseVelDegS,
    priorShareRot: result.priorShareRot,
    priorShareMm: result.priorShareMm,
    varianceFactor: result.varianceFactor,
    latched,
    fading: state.fadeLeft > 0,
    held: false,
    reason: null,
  };
}

/**
 * Which vertex of a strip is on the occluding contour under this pose.
 *
 * The contour is where the surface turns away from the eye: the vertex whose
 * camera-space normal is most nearly perpendicular to the ray that reaches
 * it. Twenty dot products against normals computed once at strip time.
 *
 * The ray matters — using the view AXIS instead would be a weak-perspective
 * approximation, and the whole reason this file solves the true perspective
 * model is that a face at 300 mm is not far enough away for that to be free.
 */
function marchStrip(
  strip: SilhouetteStrip, positions: ArrayLike<number>, pose: Pose,
): number {
  const R = pose.R;
  let best = strip.landmark;
  let bestDot = Infinity;
  for (let k = 0; k < strip.candidates.length; k++) {
    const v = strip.candidates[k];
    const nx = strip.normals[k * 3], ny = strip.normals[k * 3 + 1], nz = strip.normals[k * 3 + 2];
    const ncx = R[0] * nx + R[1] * ny + R[2] * nz;
    const ncy = R[3] * nx + R[4] * ny + R[5] * nz;
    const ncz = R[6] * nx + R[7] * ny + R[8] * nz;
    const x = positions[v * 3], y = positions[v * 3 + 1], z = positions[v * 3 + 2];
    const cx = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
    const cy = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
    const cz = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
    const len = Math.hypot(cx, cy, cz) || 1;
    const d = Math.abs((ncx * cx + ncy * cy + ncz * cz) / len);
    if (d < bestDot) { bestDot = d; best = v; }
  }
  return best;
}

/**
 * This frame's constant-velocity MAP prior: a predicted pose and the
 * information (inverse covariance) to weight it by.
 *
 * ## Why a least-squares fit and not the last two poses
 *
 * The obvious predictor — velocity from the last two poses, extrapolated one
 * frame — is arithmetically dead on arrival, and the design review proved it
 * before a line was written. Its error is `(1+p)e1 - p*e2` for `p = dt/span`,
 * so at 30 fps its variance is FIVE times a single pose's: feed that back
 * through the estimator's own recursion and the fixed point caps the prior's
 * information share at 1/5 and the achievable noise reduction at 11%. The
 * rank's whole purpose is bigger than that.
 *
 * So the velocity comes from an exact ordinary-least-squares constant-velocity
 * fit over the last `PRIOR_POINTS` ring entries. The fit's weights `w_i` are
 * the standard OLS extrapolation weights, and they carry both quality factors
 * this file needs, computed per frame from the window's ACTUAL timestamps
 * rather than assumed:
 *
 *   c = sum(w_i^2)          the predictor's noise gain — 5 at two points,
 *                           1.50 at four, so the reduction ceiling moves from
 *                           11% to 42%.
 *   s = |t*^2/2 - sum(w_i t_i^2/2)|
 *                           the EXACT error the fit commits under one unit of
 *                           sustained acceleration. Origin-invariant, because
 *                           the weights reproduce constants and linears
 *                           exactly. This is where the hand-derived
 *                           `a*dt^2/2` of the first draft was wrong by 2x:
 *                           the finite-difference velocity is ITSELF corrupted
 *                           by the acceleration, in the same direction as the
 *                           extrapolation, and the functional prices both.
 *
 *     P_pred = c * P_last + diag(sigma_accel * s)^2
 *
 * The second term is the process noise, and it is one physical constant per
 * channel type — a head's sustained acceleration bound — not a tuning knob
 * per face. It also removes a whole class of gap defect for free: a window
 * that straddles unobserved darkness has a long lever arm, so `s` grows
 * automatically (measured at 3.3x for a 0.4 s gap, dropping the prior's
 * information to 9% of its resting value) where a `dt`-only process noise
 * would have read the gap as an ordinary frame and claimed full confidence
 * on a velocity averaged across the dark.
 *
 * That is the graceful half. The hard half is that a gap can hide a
 * REVERSAL, which no constant-acceleration bound covers, so the window is
 * also TRIMMED to the contiguous tail of frames separated by less than
 * `PRIOR_MAX_STEP_S`: the prior predicts only from motion something actually
 * watched. One prior-less frame after any sub-reset gap is the entire cost.
 *
 * Returns null when the window cannot support a fit — fewer than two
 * contiguous entries, a degenerate time span, or a covariance that will not
 * invert. Every one of those is "no prior this frame", which is always safe:
 * the solve simply runs as it did before this rank existed.
 */
function buildMotionPrior(
  state: TrackerState, dtPredict: number,
): { pose: Pose; information: Float64Array; sigmaRot: number; sigmaMm: number } | null {
  const ring = state.velRing;
  const P = state.lastCovariance;
  if (!P || ring.length < 2 || !(dtPredict > 0)) return null;

  // The contiguous tail: walk back while consecutive frames are close enough
  // in time to have watched the motion between them.
  let first = ring.length - 1;
  while (first > 0
    && ring.length - first < PRIOR_POINTS
    && ring[first].time - ring[first - 1].time <= PRIOR_MAX_STEP_S) first--;
  // The newest interval itself must be observed motion, or the window's own
  // last step already straddles darkness.
  if (ring.length - first < 2) return null;
  if (ring[ring.length - 1].time - ring[ring.length - 2].time > PRIOR_MAX_STEP_S) return null;

  const k = ring.length - first;
  const last = ring[ring.length - 1];
  // Times relative to the newest entry: the fit is origin-invariant, and a
  // small origin keeps t^2 well-conditioned against the session clock.
  const t = new Float64Array(k);
  for (let i = 0; i < k; i++) t[i] = ring[first + i].time - last.time;
  const tStar = dtPredict;

  let tBar = 0;
  for (let i = 0; i < k; i++) tBar += t[i];
  tBar /= k;
  let sxx = 0;
  for (let i = 0; i < k; i++) sxx += (t[i] - tBar) * (t[i] - tBar);
  if (!(sxx > 1e-9)) return null;

  const w = new Float64Array(k);
  let c = 0;
  let accel = 0.5 * tStar * tStar;
  for (let i = 0; i < k; i++) {
    w[i] = 1 / k + ((tStar - tBar) * (t[i] - tBar)) / sxx;
    c += w[i] * w[i];
    accel -= w[i] * 0.5 * t[i] * t[i];
  }
  accel = Math.abs(accel);

  // The predicted pose. Translation channel-wise; rotation in the tangent
  // space at the newest pose, where a window's worth of head rotation is a
  // few degrees and the log is unambiguous.
  const pose = poseClone(last.pose);
  pose.t[0] = 0; pose.t[1] = 0; pose.t[2] = 0;
  const xi = v3();
  const wRot = v3();
  const rel = m3();
  const relT = m3();
  for (let i = 0; i < k; i++) {
    const e = ring[first + i];
    pose.t[0] += w[i] * e.pose.t[0];
    pose.t[1] += w[i] * e.pose.t[1];
    pose.t[2] += w[i] * e.pose.t[2];
    logSO3(wRot, m3mul(rel, e.pose.R, m3transpose(relT, last.pose.R)));
    xi[0] += w[i] * wRot[0];
    xi[1] += w[i] * wRot[1];
    xi[2] += w[i] * wRot[2];
  }
  m3mul(pose.R, expSO3(m3(), xi), last.pose.R);
  orthonormalize(pose.R, pose.R);

  // P_pred = c * P_last + Q, then inverted. The variance-factor scale puts
  // the information into the solver's CLAIM units: `refinePnP` weights its
  // residuals by claimed sigma, so an honest-units prior must be multiplied
  // by the measured mis-scale or the two sides argue in different currencies.
  // max(EMA, last frame) rather than the EMA alone: a hand crossing the face
  // spikes the true factor immediately while the EMA needs ten frames, and
  // those are exactly the frames where the prior is the only clean
  // information in the room. Monotone-safe — screaming residuals can only
  // strengthen the prior, never silence it.
  // **The process noise is gated on the prior's own recent honesty.**
  //
  // `accel` above is a function of the ring's TIMESTAMPS alone — it contains
  // no term for how the head is actually moving — so one scalar
  // `MOTION_PRIOR_ACCEL_RAD_S2` has to price a still head and a reversing one
  // identically. It cannot: sized for rest it is a 7x-19x yaw regression on a
  // 1-1.5 Hz head shake, and sized for the shake it deletes the rest win it
  // was adopted for. Retuning the constant is therefore not available, and
  // this is the term that makes the choice unnecessary.
  //
  // `priorMiss` is how many of its own claimed sigmas the prediction was
  // wrong by, last frame. A constant-velocity model tracking a constant
  // velocity reads about 1 and nothing happens. A model being contradicted —
  // which is exactly what a reversal is — reads many, and the prediction
  // covariance grows as its SQUARE, so the prior stands aside in one frame
  // and comes back as soon as the motion is smooth again. `max(last, EMA)`
  // for the same reason `vfScale` below uses it: the recovery should be
  // gradual but the stand-aside must be immediate.
  // Per channel. One grade scaling both was the defect: a lean reversal left
  // the rotation grade at rest levels, so `qMm` stayed rest-sized while the
  // constant-velocity prediction was millimetres wrong and the prior dragged
  // translation against the reversal at full weight. See `priorMissLast`.
  const missRot = Math.max(state.priorMissLast ?? 1, state.priorMissEma ?? 1, 1);
  const missMm = Math.max(state.priorMissTransLast ?? 1, state.priorMissTransEma ?? 1, 1);
  const qRot = MOTION_PRIOR_ACCEL_RAD_S2 * accel * missRot;
  // Linear in the grade, the same form the rotation channel uses — the defect
  // was a channel with no gate, not a gate of the wrong shape.
  //
  // **A squared stand-aside was measured here and NOT adopted**, recorded
  // because it looks attractive on the raw solve. On `rawPose` it takes the
  // 1 Hz lean from 1.74x of the prior-off error to 1.28x. But the app renders
  // the SMOOTHED pose (`smooth: true` at `main.ts`, and `result.pose`, not
  // `rawPose`), and there the two forms are indistinguishable: 2.598 mm
  // against 2.564 on that cell, with linear marginally ahead at rest (0.078
  // against 0.079) and on a yaw shake (0.079 against 0.080). Squaring also
  // makes the gate's own weight chatter — the mean absolute frame-to-frame
  // step in the prior's translational share more than triples at rest — and on
  // this tree's own jitter metric, the one `MOTION_PRIOR_ACCEL_MM_S2` was
  // adopted against, the linear form is the better arm on every cell measured.
  // Buying 0.03 mm of a quantity nobody sees, by chattering a weight, is not a
  // trade worth a channel asymmetry.
  const qMm = MOTION_PRIOR_ACCEL_MM_S2 * accel * missMm;
  const information = new Float64Array(36);
  for (let i = 0; i < 36; i++) information[i] = c * P[i];
  information[0] += qRot * qRot;
  information[7] += qRot * qRot;
  information[14] += qRot * qRot;
  information[21] += qMm * qMm;
  information[28] += qMm * qMm;
  information[35] += qMm * qMm;
  // The rotational one-sigma this prediction is CLAIMING, before the matrix
  // is inverted into information. The next frame grades the prediction
  // against it; see `state.priorMissLast`.
  const sigmaRot = Math.sqrt(Math.max(0, (information[0] + information[7] + information[14]) / 3));
  // The translational one-sigma, from the other three diagonal blocks and for
  // the same reason: the next frame grades this prediction against it.
  const sigmaMm = Math.sqrt(Math.max(0, (information[21] + information[28] + information[35]) / 3));
  if (!invertSymmetric(information, 6)) return null;
  const vfScale = Math.max(state.vfEma ?? 1, state.vfPrev ?? 1);
  if (vfScale !== 1) for (let i = 0; i < 36; i++) information[i] *= vfScale;

  return { pose, information, sigmaRot, sigmaMm };
}

/**
 * The basin audit's adoption: pose, count, and memory-reset as one unit.
 *
 * Everything that remembers the old basin dies here — the latch, the
 * smoother, the velocity window — and the crossfade is armed from the last
 * emitted pose, so the rescue lands as a glide instead of the ~1/s pop the
 * first real wearer's "choppy" report was made of.
 */
function adoptAuditPose(state: TrackerState, audit: PnPResult): PnPResult {
  state.basinEscapes++;
  state.latchedPose = null;
  state.latchQuiet = 0;
  state.smoother.reset();
  state.velRing.length = 0;
  if (state.lastSmoothed) {
    state.fadeFrom = poseClone(state.lastSmoothed);
    state.fadeOffset = null;
    state.fadeLeft = LATCH_FADE_FRAMES;
  }
  return audit;
}

/**
 * One channel's gate: the learned rest floor where one exists, the prior
 * where none does, clamped to [prior, prior*cap]. The lower clamp means a
 * session quieter than the prior just latches eagerly — tightening below
 * the prior buys nothing and risks refusing rest on a lucky lull. The upper
 * cap bounds what noise may claim as rest, and it is sized so deliberate
 * slow motion stays followable in any regime (see the constants header).
 */
function gateFrom(
  floor: { m: number; d: number } | null, prior: number, cap: number,
): number {
  if (!floor) return prior;
  const learned = floor.m + LATCH_FLOOR_MARGIN * floor.d;
  return learned < prior ? prior : learned > prior * cap ? prior * cap : learned;
}

/** One latched-frame velocity sample into a channel's floor estimate: mean
 *  and absolute-deviation EMAs at LATCH_FLOOR_RATE. The first sample seeds
 *  the deviation at half itself — wide enough that one quiet first frame
 *  does not start the gate at the prior's knife edge. */
function feedFloor(
  floor: { m: number; d: number } | null, v: number,
): { m: number; d: number } {
  if (!floor) return { m: v, d: v * 0.5 };
  const m = floor.m + LATCH_FLOOR_RATE * (v - floor.m);
  const d = floor.d + LATCH_FLOOR_RATE * (Math.abs(v - m) - floor.d);
  return { m, d };
}

function miss(state: TrackerState, dt: number, reason: string): TrackResult {
  state.consecutiveFailures++;
  state.lostSeconds += Math.max(dt, 0);
  // The quiet streak is a claim about CONSECUTIVE OBSERVED frames, and a
  // dropped frame was not observed: a streak banked before even a two-frame
  // blink would let the latch engage one frame after recovery, on a window
  // nobody watched. Cleared on every miss, not only on the full reset.
  state.latchQuiet = 0;

  if (state.lostSeconds >= state.options.lostSecondsBeforeReset) {
    // Only the filter and its per-motion memory. The MODEL is untouched and
    // cannot be touched: it is not a per-session estimate, so there is
    // nothing here that a dropout could corrupt. In v1 this branch had to
    // reason carefully about which of six estimators to discard. The latch
    // anchor, the velocity window and any in-flight fade die with the motion
    // they described: half a second of darkness later, the head is wherever
    // it is, and an anchor from before the gap is a pose to snap FROM.
    state.smoother.reset();
    state.lastRaw = null;
    state.latchedPose = null;
    state.latchQuiet = 0;
    state.velRing.length = 0;
    state.fadeFrom = null;
    state.fadeOffset = null;
    state.fadeLeft = 0;
  }

  const hold = state.consecutiveFailures <= state.options.holdFrames && state.lastSmoothed;
  return {
    tracked: !!hold,
    pose: hold ? poseClone(state.lastSmoothed!) : null,
    rawPose: null,
    rmsPx: NaN,
    correspondences: 0,
    inliers: 0,
    euler: hold ? headEuler(state.lastSmoothed!) : null,
    smoothingLagMm: 0,
    smoothingLagDeg: 0,
    noiseScale: NaN,
    velMmS: NaN,
    velDegS: NaN,
    sigmaMm: NaN,
    sigmaDeg: NaN,
    noiseVelMmS: NaN,
    noiseVelDegS: NaN,
    priorShareRot: NaN,
    priorShareMm: NaN,
    varianceFactor: NaN,
    latched: false,
    fading: false,
    held: !!hold,
    reason,
  };
}

/**
 * Where the glasses go this frame: the seat transform, carried by the head pose.
 *
 * One matrix multiply. That is not a slogan the way it was in v1 — where the
 * README said "the per-frame cost is a matrix multiply" while the code swept a
 * thousand contact bins through a depth field every frame — it is the whole of
 * what placement costs once the seat is cached.
 *
 * **Nothing calls this.** It used to say it was "the whole per-frame placement
 * path, and there is nothing else in it", which was never the claim it looked
 * like: the app composes these same two transforms in the scene graph, with
 * `applySeat` writing the seat once when a frame is chosen and `setHeadPose`
 * writing the head pose each frame. This is that composition written out in one
 * checkable place, and the only thing a headless caller can reach for when it
 * wants the composed matrix without a scene graph. It is not on the shipped path,
 * and a reader should not take it for the code that runs.
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
