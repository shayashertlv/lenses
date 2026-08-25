/**
 * Which frames the bundle actually gets.
 *
 * A four-second scan at 30 fps is ~120 frames and a bundle does not want all of
 * them. Consecutive frames are nearly the same view: they add residuals without
 * adding *information*, and worse, they add correlated residuals, so the solve's
 * own covariance says it is more certain than it is. That last part is the
 * subtle one and it is the same mistake v1's `agreement.js` eventually corrected
 * downstream — counting correlated samples as independent inflates claimed
 * precision by sqrt(n) for nothing.
 *
 * So: farthest-point sampling in pose space. Start from the most frontal frame,
 * then repeatedly take the frame furthest from everything already chosen. The
 * result is a set that spans the poses the wearer actually visited, at a density
 * set by the count rather than by the frame rate.
 *
 * The distance metric weights the three rotations differently, and the weights
 * are not arbitrary: **yaw is worth the most**, because yaw is what
 * triangulates. Pitch second — it sees the underside of the nose, which nothing
 * else does. Roll last: rolling the head about the view axis gives almost no new
 * geometry, it only rotates the image. A metric that treated them equally would
 * fill the keyframe budget with rolls.
 */

import { headEuler } from '../core/camera.js';
import type { Pose } from '../core/linalg.js';
import type { BundleFrame } from './bundle.js';

export interface KeyframeOptions {
  /** How many frames the bundle gets. */
  count: number;
  /** Refuse frames whose usable landmark count is below this fraction of the
   *  best frame's. A frame the detector gave up on adds noise, not views. */
  minLandmarkFraction: number;
  /** Always keep at least this many of the most frontal frames — they anchor
   *  the gauge and they are the views the iris is readable in. */
  frontalAnchors: number;
}

export const KEYFRAME_DEFAULTS: KeyframeOptions = {
  // 24, adopted 2026-08-22 from the knee replication campaign: 5 seeds
  // {11,23,37,41,53} x 14 subjects x 3 camera geometries, 42 enrollments per
  // cell on byte-identical captures per seed (paired inputs), sweep {48,36,24}.
  // The adoption rule — dropping 48 -> 24 may cost at most 0.05 mm on every
  // per-seed median mm metric and 0.10 mm at p90, in >=4/5 seeds — passed 4/5,
  // failing only seed 53's protrusion p90 (+0.192 mm).
  //
  // The historical knee this comment used to quote ("24 costs 0.15 mm of nose
  // accuracy against 48") does NOT replicate: the worst per-seed median nose
  // cost is +0.025 mm, and the across-seed medians IMPROVE at 24 — nose
  // -0.034 mm, |protrusion| -0.101 mm (better in 5/5 seeds), |standoff|
  // -0.022 mm on the pooled pairing. No geometry pays: pooled nose med/p90
  // moves 1.547/2.288 -> 1.503/2.259 (eye-level), 1.719/2.571 -> 1.626/2.535
  // (laptop-lid), 1.769/3.119 -> 1.742/3.167 (phone-lap). Scale error is
  // pairing noise, not a cost: the paired per-run d|scale%| median is
  // +0.013 pp (n=210) with per-seed paired medians straddling zero — though
  // had scale% been a blocking metric at a 0.05-in-own-units threshold, this
  // call would have flipped, and that judgement is recorded rather than
  // hidden. 36 is not a safer intermediate: it passes the same rule only 2/5
  // (its standoff medians worsen at 3 seeds) — it is noise around the
  // threshold, not a knee. Bundle time 630 -> 331 ms (0.53x): halving
  // keyframes roughly halves bundle time, and the end-to-end figure is on
  // `BUNDLE_DEFAULTS`.
  count: 24,
  minLandmarkFraction: 0.55,
  frontalAnchors: 4,
};

/** Yaw, pitch, roll weights in the pose-space distance. See the file header. */
const AXIS_WEIGHT = { yaw: 1.0, pitch: 0.7, roll: 0.25 };
/** Distance is in radians; a log-distance change of 0.4 (≈ 50%) is worth this
 *  many radians, so the lean beat competes with the turn beat rather than being
 *  swamped by it. */
const DISTANCE_WEIGHT = 0.9;

interface Descriptor {
  yaw: number; pitch: number; roll: number; logZ: number; usable: number;
}

function describe(pose: Pose, frame: BundleFrame): Descriptor {
  // The wearer's own angles, not the camera frame's — see `headEuler`. With the
  // raw euler this function reported a 360-degree yaw span for a capture that
  // never left frontal, because the values sat near +/-180 and wrapped.
  const e = headEuler(pose);
  let usable = 0;
  for (let i = 0; i < frame.sigmaPx.length; i++) {
    if (frame.sigmaPx[i] > 0 && frame.sigmaPx[i] < 1e6 && !Number.isNaN(frame.landmarks[i * 2])) {
      usable++;
    }
  }
  return {
    yaw: e.yaw, pitch: e.pitch, roll: e.roll,
    logZ: Math.log(Math.max(pose.t[2], 1)),
    usable,
  };
}

const poseDistance = (a: Descriptor, b: Descriptor): number => Math.hypot(
  (a.yaw - b.yaw) * AXIS_WEIGHT.yaw,
  (a.pitch - b.pitch) * AXIS_WEIGHT.pitch,
  (a.roll - b.roll) * AXIS_WEIGHT.roll,
  (a.logZ - b.logZ) * DISTANCE_WEIGHT,
);

export interface KeyframeSelection {
  frames: BundleFrame[];
  indices: number[];
  /** Coverage report — what the scan actually achieved, per axis. */
  yawSpanDeg: number;
  pitchSpanDeg: number;
  distanceSpanPct: number;
  rejectedForLandmarks: number;
}

export function selectKeyframes(
  frames: BundleFrame[], options: Partial<KeyframeOptions> = {},
): KeyframeSelection {
  const opt = { ...KEYFRAME_DEFAULTS, ...options };
  const all = frames.map((f) => describe(f.pose, f));

  let bestUsable = 0;
  for (const d of all) bestUsable = Math.max(bestUsable, d.usable);
  const floor = bestUsable * opt.minLandmarkFraction;

  const candidates: number[] = [];
  let rejected = 0;
  for (let i = 0; i < all.length; i++) {
    if (all[i].usable >= floor) candidates.push(i);
    else rejected++;
  }
  if (candidates.length === 0) {
    return {
      frames: [], indices: [], yawSpanDeg: 0, pitchSpanDeg: 0,
      distanceSpanPct: 0, rejectedForLandmarks: rejected,
    };
  }

  const chosen: number[] = [];
  const chosenSet = new Set<number>();

  // Frontal anchors first, by absolute yaw then absolute pitch.
  const byFrontal = [...candidates].sort((a, b) =>
    (Math.abs(all[a].yaw) + Math.abs(all[a].pitch) * 0.7)
    - (Math.abs(all[b].yaw) + Math.abs(all[b].pitch) * 0.7));
  for (let i = 0; i < Math.min(opt.frontalAnchors, byFrontal.length); i++) {
    chosen.push(byFrontal[i]);
    chosenSet.add(byFrontal[i]);
  }

  // Per-axis extremes next — the frames that carry each axis's full span.
  // Farthest-point below optimises a WEIGHTED pose distance, and at 24
  // keyframes it can spend the whole budget on yaw and lean: measured on one
  // sparse draw (S01, 12 frames per beat), a capture holding 21.8 degrees of
  // pitch kept only 8.9 in the selection, the coverage verdict read that
  // collapsed span against its 12-degree threshold, and a compliant wearer was
  // told to nod again — advice about a thing they had already done.
  // Guaranteeing the six extremes costs at most six of the slots and makes the
  // selection's span equal the capture's on every axis by construction. That
  // is a fix for the SOLVE, not only for the advice: the extremes are exactly
  // the frames the conditioning arguments in `COVERAGE_THRESHOLDS` are about.
  const axes: Array<(i: number) => number> = [
    (i) => all[i].yaw, (i) => -all[i].yaw,
    (i) => all[i].pitch, (i) => -all[i].pitch,
    (i) => all[i].logZ, (i) => -all[i].logZ,
  ];
  const cap = Math.min(opt.count, candidates.length);
  for (const axis of axes) {
    if (chosen.length >= cap) break;
    let best = -1;
    let bestV = -Infinity;
    for (const c of candidates) {
      if (axis(c) > bestV) { bestV = axis(c); best = c; }
    }
    if (best >= 0 && !chosenSet.has(best)) {
      chosen.push(best);
      chosenSet.add(best);
    }
  }

  // Then farthest-point.
  const minDist = new Float64Array(all.length).fill(Infinity);
  for (const c of candidates) {
    for (const k of chosen) minDist[c] = Math.min(minDist[c], poseDistance(all[c], all[k]));
  }
  while (chosen.length < Math.min(opt.count, candidates.length)) {
    let best = -1, bestD = -1;
    for (const c of candidates) {
      if (chosenSet.has(c)) continue;
      if (minDist[c] > bestD) { bestD = minDist[c]; best = c; }
    }
    if (best < 0) break;
    chosen.push(best);
    chosenSet.add(best);
    for (const c of candidates) {
      if (chosenSet.has(c)) continue;
      minDist[c] = Math.min(minDist[c], poseDistance(all[c], all[best]));
    }
  }

  chosen.sort((a, b) => a - b);

  const yaws = chosen.map((i) => all[i].yaw);
  const pitches = chosen.map((i) => all[i].pitch);
  const zs = chosen.map((i) => Math.exp(all[i].logZ));
  const deg = 180 / Math.PI;

  return {
    frames: chosen.map((i) => frames[i]),
    indices: chosen,
    yawSpanDeg: (Math.max(...yaws) - Math.min(...yaws)) * deg,
    pitchSpanDeg: (Math.max(...pitches) - Math.min(...pitches)) * deg,
    distanceSpanPct: ((Math.max(...zs) - Math.min(...zs)) / Math.min(...zs)) * 100,
    rejectedForLandmarks: rejected,
  };
}

/**
 * Whether the scan covered enough to be worth solving, and if not, which beat to
 * ask the wearer to repeat.
 *
 * The thresholds are the conditions under which the *solve* is well posed, not
 * an arbitrary quality bar:
 *
 *  - **Yaw span.** Depth error from triangulation goes as roughly
 *    `sigma_px * Z / (f * sin(theta))` for a baseline angle `theta`. At the
 *    ladder's typical `Z/f` of 0.85 mm/px and 0.7 px of landmark noise, 1 mm of
 *    bridge depth needs `sin(theta) > 0.6`, i.e. about 37 degrees of total span.
 *    50 leaves margin for the frames that are not on the extremes.
 *  - **Distance span.** Focal length is only observable through depth
 *    variation; below about 25% the intrinsics solve is ill-conditioned and the
 *    caller should fall back to a fixed camera rather than a badly solved one.
 *  - **Profile.** Not required — the bundle works without it — but its absence
 *    is reported, because nose protrusion is materially worse without it and
 *    the wearer should be told which of their measurements is soft.
 */
export interface CoverageVerdict {
  sufficient: boolean;
  canSolveIntrinsics: boolean;
  hasProfile: boolean;
  missing: string[];
  advice: string | null;
}

/**
 * **These are MEASURED degrees, not physical ones.**
 *
 * The distinction is not pedantic. A wearer reported that the scan's "30
 * degrees" arrived only after roughly seventy degrees of real head turn: the
 * detector's landmarks under-rotate as half the face disappears, so the angle
 * this pipeline can compute is compressed against the angle a person performs,
 * by a factor that has never been calibrated.
 *
 * Every number here is therefore a statement about the pipeline's own readings,
 * and the physical turn it corresponds to is unknown within roughly a factor of
 * two. `yawSpanDeg` in particular was derived from a triangulation argument in
 * *physical* degrees and is applied to *measured* ones, which means it is
 * probably conservative — it asks for more real turn than the derivation
 * intended. Left as is rather than adjusted on a guess. See
 * `docs/OPEN-QUESTIONS.md` Q13.
 */
export const COVERAGE_THRESHOLDS = {
  yawSpanDeg: 50,
  pitchSpanDeg: 12,
  distanceSpanPct: 25,
  // Lowered from 55: at that value, and with the measured angle compressed as
  // it is, essentially no real wearer would ever be credited with a profile
  // view — including ones who turned far enough to give a genuine silhouette.
  profileYawDeg: 38,
};

export function assessCoverage(
  selection: KeyframeSelection, frames: BundleFrame[],
): CoverageVerdict {
  const missing: string[] = [];
  let maxYaw = 0;
  for (const f of frames) maxYaw = Math.max(maxYaw, Math.abs(headEuler(f.pose).yaw));
  const hasProfile = maxYaw * (180 / Math.PI) >= COVERAGE_THRESHOLDS.profileYawDeg;

  if (selection.yawSpanDeg < COVERAGE_THRESHOLDS.yawSpanDeg) missing.push('turn');
  if (selection.pitchSpanDeg < COVERAGE_THRESHOLDS.pitchSpanDeg) missing.push('nod');
  const canSolveIntrinsics = selection.distanceSpanPct >= COVERAGE_THRESHOLDS.distanceSpanPct;
  if (!canSolveIntrinsics) missing.push('lean');
  if (!hasProfile) missing.push('profile');

  const advice = missing.includes('turn')
    ? 'Turn your head further to each side — that is what measures the depth of your nose.'
    : missing.includes('profile')
      ? 'Hold a full side-on view for a moment; it is the only view that sees how far your nose projects.'
      : missing.includes('lean')
        ? 'Lean in and back once — it is what tells us your camera\'s field of view.'
        : missing.includes('nod')
          ? 'Nod once; it measures the underside of your nose, where the pads rest.'
          : null;

  return {
    // Turn and nod are the load-bearing ones; lean and profile degrade the
    // result without invalidating it, and are reported rather than refused.
    sufficient: !missing.includes('turn') && !missing.includes('nod'),
    canSolveIntrinsics,
    hasProfile,
    missing,
    advice,
  };
}
