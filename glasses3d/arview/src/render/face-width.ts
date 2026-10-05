/** Relative face-width observations for bounded anterior-head calibration, plus static arm-spread geometry.
 *  TempleHeadFit uses the observations to fit the occluder. Production eyewear keeps a fixed 18 mm spread;
 *  these observations never resize or bend it.
 *
 *  What it estimates: how wide this wearer's face is *in proportion*, compared with the canonical face the eyewear
 *  geometry is authored against. It is not anatomy, not an ear position and not a measurement in millimetres; the only
 *  number it produces is a bounded ratio around 1.
 *
 *  How distance and rotation are separated from proportion: each observation is taken in head-relative coordinates.
 *  The reconstructed camera-space surface (face-surface.ts, before the nasal shape) is carried back through the raw
 *  detector pose, a similarity transform whose rotation, translation and fitted scale are exactly the head rotation and
 *  the camera distance. What is left is the face in the canonical frame, where a lateral span can be compared with the
 *  canonical face's own span directly. No screen-space bounding box, and no division by cos(yaw).
 *
 *  Which points: five left/right region pairs around the outer eyes and the upper sides of the face, each an average of
 *  two or three neighbouring landmarks, so no single "temple" landmark decides the result. Every pair yields its own
 *  ratio and the median of the five is the observation; MediaPipe gives no per-landmark confidence and none is invented.
 *
 *  What is rejected: a strongly turned, pitched or rolled head; a missing or non-finite landmark; an observation whose
 *  pairs are not symmetric about the head's own midline (a turned or partly occluded face); pairs that disagree with
 *  each other; a ratio outside the plausible window.
 *
 *  The tuning values here are visual choices, not measured anatomical facts. */
import {Matrix4, Vector3} from 'three';
import {poseAngles} from './pose-stabilizer.ts';

export const WIDTH_FIT_METHOD = 'face-width-fit-v1';

/** The lateral plane shared by arm-deformation eligibility, endpoint stations and visibility. A moved arm point
 *  stays on its original side so the geometry and fragment rules classify the same shaft. */
export const ARM_LATERAL_MIN_M = 0.045;

/** The geometry helper's hard cap on outward splay per arm. Production uses a fixed 18 mm within this bound.
 *
 *  The number is the arms' own inward curl. Read straight out of the shipped GLBs (2026-09-18): each arm's outer
 *  surface runs from |x| 7.2 cm along the hinge shaft to 5.1 cm at the ear hook on Amber Horizon, and 7.25 to 5.3 cm on
 *  Tom Ford — about 21 mm and 19 mm of inward taper. At the cap the bend has straightened that curl and the arm runs
 *  back parallel to the frame front; there is no reading of "outward" beyond it that is still a pair of glasses. */
export const MAX_ARM_SPREAD_M = 0.024;

/** The hinge: how the bend is distributed along the arm.
 *
 *  A real temple does not bow. It pivots at the hinge, just behind the rims, and runs straight back from there. Until
 *  2026-09-18 this ramp was a smoothstep over the whole arm, which left the shaft curved: its widest bulge sat about
 *  5 cm behind the lens, nowhere near a hinge, and the wearer read it as a warped frame rather than an adjusted one.
 *  The ramp is now a straight line from the pivot — the shaft keeps its own shape and simply points further out —
 *  rounded over its first SPREAD_HINGE_ROUND_M so the pivot is a hinge radius in the mesh and not a crease.
 *
 *  The pivot is the plane where the frame front ends and the temple shaft begins, which rear-drop.ts reads out of the
 *  asset's own cross-section. Geometry callers may move it backwards; production keeps the asset hinge. It cannot
 *  move forwards because the same lateral band then contains the rim and endpiece, which must stay fixed.
 *
 *  These are visual choices measured against a proxy head, not a wearer's anatomy. */
export const SPREAD_HINGE_ROUND_M = 0.006;
export const SPREAD_PIVOT_RANGE_M = Object.freeze({min: 0, max: 0.030});

/** Combine geometry spread contributions within the same bound and 0.1 mm quantum. Production uses a fixed sum. */
export function totalArmSpreadM(fitSpreadM: number, bendM: number): number {
  const sum = (Number.isFinite(fitSpreadM) ? fitSpreadM : 0) + (Number.isFinite(bendM) ? bendM : 0);
  const capped = Math.min(MAX_ARM_SPREAD_M, Math.max(-MAX_ARM_SPREAD_M, sum));
  return Math.round(capped / WIDTH_FIT.spreadQuantumM) * WIDTH_FIT.spreadQuantumM;
}

export const WIDTH_FIT = Object.freeze({
  /** Gates on the raw pose: only near-frontal observations are collected. */
  maxYawDeg: 12, maxPitchDeg: 15, maxRollDeg: 12,
  /** A pair's centre may sit this far off the head midline, as a share of that pair's own span. */
  maxAsymmetry: 0.12,
  /** A sanity bound on how far the five region ratios may spread apart, not a quality test: the ordinary spread
   *  between these bands is a property of the face, not of the frame. Measured on the checked-in fixture face through
   *  the shipped modules (2026-09-18): 0.115 undistorted, and 0.296 for the same face stretched 1.78x by the synthetic
   *  harness, which no similarity pose can absorb. This bound accepts the first and refuses the second. */
  maxRegionSpread: 0.20,
  /** An observation outside this window is not believed at all. */
  acceptMinRatio: 0.80, acceptMaxRatio: 1.20,
  /** What may actually be applied, whatever the observations say. */
  appliedMinRatio: 0.92, appliedMaxRatio: 1.08,
  /** Accepted observations needed before anything is applied, and the window the median is taken over. */
  minSamples: 30, bufferSamples: 120,
  /** Share of the remaining distance the applied ratio closes per accepted observation. */
  ease: 0.05,
  /** Continuous milliseconds without a tracked face after which the estimate is dropped. */
  lostResetMs: 3000,
  /** Optional ratio-to-spread geometry conversion; it is not applied to production eyewear. */
  armSpreadPerRatioM: 0.07,
  /** Bound and quantization for the optional ratio-to-spread helper. Head-occluder calibration uses the ratio itself. */
  maxArmSpreadM: 0.006, spreadQuantumM: 0.0001,
  /** Kept clear of ARM_LATERAL_MIN_M when a point would otherwise be pulled across it. */
  lateralGuardM: 0.0005,
});

/** Left/right region pairs, around the outer eyes and the upper sides of the face. Each side is the average of its
 *  landmarks, so one bad landmark moves a region by a fraction of its own error. */
export const WIDTH_REGIONS: readonly {readonly name: string; readonly left: readonly number[]; readonly right: readonly number[]}[] = Object.freeze([
  {name: 'temple-upper', left: Object.freeze([21, 162]), right: Object.freeze([251, 389])},
  {name: 'temple-side', left: Object.freeze([127, 234]), right: Object.freeze([356, 454])},
  {name: 'temple-front', left: Object.freeze([227, 137]), right: Object.freeze([447, 366])},
  {name: 'eye-outer', left: Object.freeze([33, 130, 226]), right: Object.freeze([263, 359, 446])},
  {name: 'eye-lateral', left: Object.freeze([143, 156]), right: Object.freeze([372, 383])},
].map(region => Object.freeze(region)));

export type WidthFitState = 'off' | 'collecting' | 'stable' | 'fallback';
/** Why an observation was not collected. */
export type WidthRejection = 'pose' | 'landmarks' | 'asymmetry' | 'inconsistent' | 'range';
export interface WidthObservation {
  /** The median of the five region ratios. A face's bands do not share one proportion, so this is a summary of a
   *  profile, not a measurement: the profile itself is kept in `regionRatios` and reaches the audit metadata. */
  readonly ratio: number;
  readonly regionRatios: readonly number[];
  /** The largest distance of a pair's centre from the head midline, as a share of that pair's span. */
  readonly asymmetry: number;
}
export type WidthObservationResult = {readonly observation: WidthObservation; readonly rejected: null}
  | {readonly observation: null; readonly rejected: WidthRejection};

const median = (values: readonly number[]): number => {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle]! : (sorted[middle - 1]! + sorted[middle]!) / 2;
};
const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/** The lateral spans of the region pairs on the canonical face. Throws when the canonical mesh cannot supply them. */
export function canonicalRegionSpans(canonicalPositions: ArrayLike<number>): number[] {
  if (canonicalPositions.length < 468 * 3) throw new Error('The face-width reference needs the canonical face.');
  const mean = (indices: readonly number[]): number => {
    let sum = 0;
    for (const index of indices) {
      const x = canonicalPositions[index * 3];
      if (typeof x !== 'number' || !Number.isFinite(x)) throw new Error('The canonical face contains an unusable width landmark.');
      sum += x;
    }
    return sum / indices.length;
  };
  return WIDTH_REGIONS.map(region => {
    const span = mean(region.right) - mean(region.left);
    if (!(span > 0.5)) throw new Error(`The canonical width region ${region.name} has no usable span.`);
    return span;
  });
}

/** One observation, in head-relative coordinates. `surfacePositions` is the reconstructed camera-space surface as
 *  face-surface.ts leaves it (centimetres, before the nasal shape); `rawMatrix` is the same frame's raw detector pose. */
export function observeFaceWidth(surfacePositions: ArrayLike<number>, canonicalSpans: readonly number[],
  rawMatrix: readonly number[]): WidthObservationResult {
  if (rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)) return {observation: null, rejected: 'pose'};
  const matrix = new Matrix4().fromArray(rawMatrix);
  if (!(Math.abs(matrix.determinant()) > 1e-12)) return {observation: null, rejected: 'pose'};
  const angles = poseAngles(rawMatrix);
  if (Math.abs(angles.yawDeg) > WIDTH_FIT.maxYawDeg || Math.abs(angles.pitchDeg) > WIDTH_FIT.maxPitchDeg
    || Math.abs(angles.rollDeg) > WIDTH_FIT.maxRollDeg) return {observation: null, rejected: 'pose'};
  if (surfacePositions.length < 468 * 3 || canonicalSpans.length !== WIDTH_REGIONS.length) return {observation: null, rejected: 'landmarks'};
  const inverse = matrix.clone().invert();
  const point = new Vector3();
  const headX = (indices: readonly number[]): number | null => {
    let sum = 0;
    for (const index of indices) {
      const x = surfacePositions[index * 3], y = surfacePositions[index * 3 + 1], z = surfacePositions[index * 3 + 2];
      if (typeof x !== 'number' || typeof y !== 'number' || typeof z !== 'number'
        || !Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return null;
      point.set(x, y, z).applyMatrix4(inverse);
      if (!Number.isFinite(point.x)) return null;
      sum += point.x;
    }
    return sum / indices.length;
  };
  const regionRatios: number[] = [];
  let asymmetry = 0;
  for (const [index, region] of WIDTH_REGIONS.entries()) {
    const left = headX(region.left), right = headX(region.right);
    if (left === null || right === null) return {observation: null, rejected: 'landmarks'};
    const span = right - left;
    if (!(span > 0.5)) return {observation: null, rejected: 'landmarks'};
    // The canonical face is symmetric about x = 0 and the pose carries that midline with it, so a pair whose centre
    // drifts off the midline is a turned or partly occluded observation, not a wider face.
    asymmetry = Math.max(asymmetry, Math.abs(left + right) / span);
    regionRatios.push(span / canonicalSpans[index]!);
  }
  if (asymmetry > WIDTH_FIT.maxAsymmetry) return {observation: null, rejected: 'asymmetry'};
  if (Math.max(...regionRatios) - Math.min(...regionRatios) > WIDTH_FIT.maxRegionSpread) return {observation: null, rejected: 'inconsistent'};
  const ratio = median(regionRatios);
  if (!Number.isFinite(ratio) || ratio < WIDTH_FIT.acceptMinRatio || ratio > WIDTH_FIT.acceptMaxRatio) return {observation: null, rejected: 'range'};
  return {observation: {ratio, regionRatios, asymmetry}, rejected: null};
}

/** The width ratio of a live session: collected while the camera runs, combined robustly, applied slowly, held through
 *  turns and brief tracking failures, and dropped after a sustained loss. There is no scan and no user step. */
export class FaceWidthEstimator {
  private readonly spans: number[];
  private readonly samples: number[] = [];
  private applied = 1;
  private stable = false;
  private fellBack = false;
  private lastTrackedAtMs: number | null = null;
  private rejection: WidthRejection | null = null;
  private acceptedCount = 0;
  private regionRatios: readonly number[] | null = null;

  constructor(canonicalPositions: ArrayLike<number>) {this.spans = canonicalRegionSpans(canonicalPositions);}

  /** The ratio to apply now: exactly 1 until there is enough reliable evidence, and exactly 1 again after a reset. */
  get ratio(): number {return this.applied;}
  get state(): Exclude<WidthFitState, 'off'> {return this.stable ? 'stable' : this.fellBack ? 'fallback' : 'collecting';}
  /** Accepted observations held in the window. */
  get sampleCount(): number {return this.samples.length;}
  /** Accepted observations since the last reset. */
  get accepted(): number {return this.acceptedCount;}
  /** Why the last observation was not collected, or null when it was. */
  get lastRejection(): WidthRejection | null {return this.rejection;}
  /** The per-region ratios of the last accepted observation, in WIDTH_REGIONS order (numbers only, for the audit). */
  get lastRegionRatios(): readonly number[] | null {return this.regionRatios;}
  /** The median of the collected observations, or null while none are held. */
  get observedRatio(): number | null {return this.samples.length ? median(this.samples) : null;}

  /** Back to the original geometry with no collected evidence. `afterLoss` keeps the state visible as a fallback. */
  reset(afterLoss = false): void {
    this.samples.length = 0; this.applied = 1; this.acceptedCount = 0;
    this.fellBack = afterLoss && (this.stable || this.fellBack);
    this.stable = false; this.lastTrackedAtMs = null; this.rejection = null; this.regionRatios = null;
  }

  /** A tracked frame. Returns the accepted observation, or null when this frame was not collected. */
  observe(surfacePositions: ArrayLike<number>, rawMatrix: readonly number[], timestampMs: number): WidthObservation | null {
    this.lastTrackedAtMs = timestampMs;
    const result = observeFaceWidth(surfacePositions, this.spans, rawMatrix);
    this.rejection = result.rejected;
    if (!result.observation) return null;
    this.acceptedCount++;
    this.regionRatios = result.observation.regionRatios;
    this.samples.push(result.observation.ratio);
    if (this.samples.length > WIDTH_FIT.bufferSamples) this.samples.splice(0, this.samples.length - WIDTH_FIT.bufferSamples);
    if (!this.stable) {
      // The original geometry until the evidence is there; then the applied ratio leaves 1 slowly, never in one step.
      if (this.samples.length < WIDTH_FIT.minSamples) return result.observation;
      this.stable = true; this.fellBack = false; this.applied = 1;
    }
    const target = clamp(median(this.samples), WIDTH_FIT.appliedMinRatio, WIDTH_FIT.appliedMaxRatio);
    this.applied = clamp(this.applied + (target - this.applied) * WIDTH_FIT.ease, WIDTH_FIT.appliedMinRatio, WIDTH_FIT.appliedMaxRatio);
    return result.observation;
  }

  /** A frame with no tracked face. A brief loss changes nothing; a sustained one drops the estimate. */
  miss(timestampMs: number): void {
    if (this.lastTrackedAtMs === null) return;
    if (timestampMs - this.lastTrackedAtMs > WIDTH_FIT.lostResetMs) this.reset(true);
  }
}

/** Optional width-ratio conversion: bounded, rounded, and zero at ratio 1. Production eyewear does not call it. */
export function armSpreadM(ratio: number): number {
  if (!Number.isFinite(ratio)) return 0;
  const raw = (clamp(ratio, WIDTH_FIT.appliedMinRatio, WIDTH_FIT.appliedMaxRatio) - 1) * WIDTH_FIT.armSpreadPerRatioM;
  const capped = clamp(raw, -WIDTH_FIT.maxArmSpreadM, WIDTH_FIT.maxArmSpreadM);
  return Math.round(capped / WIDTH_FIT.spreadQuantumM) * WIDTH_FIT.spreadQuantumM;
}

/** The z run the bend is spread over: pivot plane to clip cap. Throws on a span or a spread it cannot draw. */
function spreadRampSpan(startZM: number, cutoffZM: number, spreadM: number): number {
  if (![startZM, cutoffZM, spreadM].every(Number.isFinite) || startZM <= cutoffZM + SPREAD_HINGE_ROUND_M) {
    throw new Error('The arm-spread span is invalid.');
  }
  if (Math.abs(spreadM) > MAX_ARM_SPREAD_M + 1e-9) throw new Error('The arm spread is out of range.');
  return startZM - cutoffZM;
}

/** The lateral ramp shared by the arm geometry and the projected arm centrelines: 0 at the pivot plane, where the frame
 *  front ends, and the full spread at the clip cap, in a straight line between them. The first SPREAD_HINGE_ROUND_M is
 *  rounded, so the ramp leaves the untouched front of the frame with a zero slope and reaches its constant angle over a
 *  hinge's worth of arm rather than a corner.
 *
 *  This is what "bend the temples outward at the hinge" means here: the bridge, rims, lenses and endpieces do not move
 *  at all, and the arm swings out behind them as a real temple splays from its hinge. */
export function armSpreadCurve(z: number, startZM: number, cutoffZM: number, spreadM: number): number {
  const span = spreadRampSpan(startZM, cutoffZM, spreadM);
  if (!Number.isFinite(z)) throw new Error('The arm-spread span is invalid.');
  if (spreadM === 0) return 0;
  const run = clamp(startZM - z, 0, span);
  const slope = spreadM / (span - SPREAD_HINGE_ROUND_M / 2);
  return run < SPREAD_HINGE_ROUND_M ? slope * run * run / (2 * SPREAD_HINGE_ROUND_M) : slope * (run - SPREAD_HINGE_ROUND_M / 2);
}

/** d(offset)/dz of `armSpreadCurve`, used to keep deformed normals and tangents consistent with the shaft. */
export function armSpreadSlope(z: number, startZM: number, cutoffZM: number, spreadM: number): number {
  const span = spreadRampSpan(startZM, cutoffZM, spreadM);
  if (!Number.isFinite(z)) throw new Error('The arm-spread span is invalid.');
  if (spreadM === 0) return 0;
  const run = startZM - z;
  if (run <= 0 || run >= span) return 0;
  const slope = spreadM / (span - SPREAD_HINGE_ROUND_M / 2);
  return -(run < SPREAD_HINGE_ROUND_M ? slope * run / SPREAD_HINGE_ROUND_M : slope);
}

/** Lateral position after spread: identity at zero and never crossing ARM_LATERAL_MIN_M, so visibility,
 *  endpoint stations and deformation eligibility continue to classify the same shaft. */
export function spreadArmX(x: number, z: number, startZM: number, cutoffZM: number, spreadM: number): number {
  if (!Number.isFinite(x)) throw new Error('The arm-spread position is invalid.');
  if (spreadM === 0) return x;
  const magnitude = Math.abs(x);
  if (magnitude <= ARM_LATERAL_MIN_M) return x;
  const floor = Math.min(magnitude, ARM_LATERAL_MIN_M + WIDTH_FIT.lateralGuardM);
  return Math.sign(x) * Math.max(magnitude + armSpreadCurve(z, startZM, cutoffZM, spreadM), floor);
}
