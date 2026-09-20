/** A session's visual frame-width calibration. Values are rendering estimates in the model's centimetre space,
 * not measurements of anatomy. Calibration never consumes the already resized frame. */
import {Matrix4, Vector3} from 'three';
import type {Landmark} from '../face/protocol.ts';
import {poseAngles} from './pose-stabilizer.ts';

export const EYEWEAR_FIT = Object.freeze({
  minimumSamples: 20, minimumValidMs: 1800, sampleIntervalMs: 50, maximumGapMs: 600,
  maximumIntervalMs: 150, maximumWindowMs: 3200, maximumSamples: 64,
  maximumRelativeSpread: .02, outlierRelativeDistance: .06,
  minimumWidthCm: 10, maximumWidthCm: 22,
  minimumAutoScale: .85, maximumAutoScale: 1.15, maximumAdjustment: .08,
  /** Apply after automatic bounds so slider zero matches the previous +8% visual fit. */
  defaultFitPreference: 1.08,
  minimumScale: .782, maximumScale: 1.242, easeMs: 200,
  maximumYawDeg: 18, maximumPitchDeg: 20, maximumRollDeg: 15,
  /** A virtual frontal view for comparing proportions. It does not measure or prescribe wearer distance. */
  referenceDistanceCm: 50, referenceFrontDepthCm: 6.5,
});

export interface FitWidthSample {faceWidthCm: number; rejection?: string | null}
export interface EyewearFitReport {
  state: 'collecting' | 'settling' | 'fitted';
  progress: number;
  faceWidthCm: number | null;
  adjustment: number;
  limited: boolean;
  rejection: string | null;
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const quantile = (sorted: readonly number[], q: number): number => {
  const index = (sorted.length - 1) * q, lower = Math.floor(index), upper = Math.ceil(index);
  return sorted[lower]! + (sorted[upper]! - sorted[lower]!) * (index - lower);
};

interface Observation {width: number; timestampMs: number; durationMs: number}

/** One instance can outlive a renderer when the wearer switches frames. Only reset() discards the fitted profile;
 * ordinary tracking misses, changing distance, and later imperfect detections cannot resize a fitted frame. */
export class EyewearFitSession {
  private readonly observations: Observation[] = [];
  private fittedWidth: number | null = null;
  private manualAdjustment = 0;
  private lastObservationMs: number | null = null;
  private rejectedSinceAccepted = false;
  private lastRejection: string | null = null;
  private consecutiveOutliers = 0;
  private currentScale = 1;
  private lastScaleMs: number | null = null;
  private previousTarget = 1;
  private settled = false;
  private sizeLimited = false;

  get report(): EyewearFitReport {
    const validMs = this.observations.reduce((sum, sample) => sum + sample.durationMs, 0);
    return {
      state: this.fittedWidth === null ? 'collecting' : this.settled ? 'fitted' : 'settling',
      progress: this.fittedWidth !== null ? 1 : Math.min(.95,
        this.observations.length / EYEWEAR_FIT.minimumSamples, validMs / EYEWEAR_FIT.minimumValidMs),
      faceWidthCm: this.fittedWidth, adjustment: this.manualAdjustment, rejection: this.lastRejection,
      limited: this.sizeLimited,
    };
  }

  reset(): void {
    this.observations.length = 0; this.fittedWidth = null; this.manualAdjustment = 0;
    this.lastObservationMs = null; this.rejectedSinceAccepted = false; this.lastRejection = null;
    this.consecutiveOutliers = 0; this.previousTarget = 1; this.settled = false;
    this.sizeLimited = false;
    // Keep currentScale and its clock: the next posed frames ease back instead of snapping on reset.
  }

  setAdjustment(value: number): void {
    if (!Number.isFinite(value)) throw new Error('The frame-size adjustment must be finite.');
    const next = clamp(value, -EYEWEAR_FIT.maximumAdjustment, EYEWEAR_FIT.maximumAdjustment);
    if (next !== this.manualAdjustment) {this.manualAdjustment = next; this.settled = false;}
  }

  observe(sample: FitWidthSample, timestampMs: number): void {
    if (this.fittedWidth !== null) return;
    if (!Number.isFinite(timestampMs) || timestampMs < 0
      || this.lastObservationMs !== null && timestampMs <= this.lastObservationMs) {
      this.lastRejection = 'Face tracking is settling.'; return;
    }
    this.lastObservationMs = timestampMs;
    if (sample.rejection || !Number.isFinite(sample.faceWidthCm)
      || sample.faceWidthCm < EYEWEAR_FIT.minimumWidthCm || sample.faceWidthCm > EYEWEAR_FIT.maximumWidthCm) {
      this.lastRejection = sample.rejection || 'Waiting for a clear face.';
      this.rejectedSinceAccepted = true; return;
    }
    let previous = this.observations.at(-1);
    if (previous && timestampMs - previous.timestampMs > EYEWEAR_FIT.maximumGapMs) {
      this.observations.length = 0; previous = undefined; this.consecutiveOutliers = 0;
    }
    if (previous && timestampMs - previous.timestampMs < EYEWEAR_FIT.sampleIntervalMs - 1e-6) return;
    if (this.observations.length >= 5) {
      const sorted = this.observations.map(value => value.width).sort((a, b) => a - b);
      const center = quantile(sorted, .5);
      if (Math.abs(sample.faceWidthCm - center) / center > EYEWEAR_FIT.outlierRelativeDistance) {
        this.lastRejection = 'Keep your head still.'; this.rejectedSinceAccepted = true;
        // A lone spike cannot contaminate the profile. A consistently different initial subject can start a
        // fresh collection; an already fitted profile never enters this path.
        if (++this.consecutiveOutliers < 4) return;
        this.observations.length = 0; previous = undefined;
      }
    }
    this.consecutiveOutliers = 0;
    const durationMs = previous && !this.rejectedSinceAccepted
      ? Math.min(EYEWEAR_FIT.maximumIntervalMs, timestampMs - previous.timestampMs) : 0;
    this.observations.push({width: sample.faceWidthCm, timestampMs, durationMs});
    this.rejectedSinceAccepted = false; this.lastRejection = null;
    while (this.observations.length > EYEWEAR_FIT.maximumSamples
      || this.observations.length > EYEWEAR_FIT.minimumSamples
        && timestampMs - this.observations[0]!.timestampMs > EYEWEAR_FIT.maximumWindowMs) this.observations.shift();
    if (this.observations.length) this.observations[0]!.durationMs = 0;
    const validMs = this.observations.reduce((sum, value) => sum + value.durationMs, 0);
    if (this.observations.length < EYEWEAR_FIT.minimumSamples || validMs < EYEWEAR_FIT.minimumValidMs - 1e-6) return;
    const sorted = this.observations.map(value => value.width).sort((a, b) => a - b);
    const center = quantile(sorted, .5);
    if ((quantile(sorted, .9) - quantile(sorted, .1)) / center > EYEWEAR_FIT.maximumRelativeSpread) {
      this.lastRejection = 'Keep your head still.'; return;
    }
    this.fittedWidth = center; this.lastRejection = null; this.settled = false;
  }

  /** Eases the visual scale using capture time, so audit rerenders cannot move it. Before calibration the
   * automatic scale is exactly one. A manual adjustment is intentional and remains available while collecting. */
  scaleFor(frontWidthCm: number, preferredRatio: number, timestampMs: number): number {
    if (![frontWidthCm, preferredRatio, timestampMs].every(Number.isFinite)
      || frontWidthCm <= 0 || preferredRatio <= 0 || timestampMs < 0) return this.currentScale;
    const automatic = this.fittedWidth === null ? 1
      : clamp(this.fittedWidth * preferredRatio / frontWidthCm, EYEWEAR_FIT.minimumAutoScale, EYEWEAR_FIT.maximumAutoScale)
        * EYEWEAR_FIT.defaultFitPreference;
    const requested = automatic * (1 + this.manualAdjustment);
    const target = clamp(requested, EYEWEAR_FIT.minimumScale, EYEWEAR_FIT.maximumScale);
    this.sizeLimited = Math.abs(requested - target) > 1e-9;
    if (Math.abs(target - this.previousTarget) > 1e-12) {this.previousTarget = target; this.settled = false;}
    if (this.lastScaleMs === null) {
      this.lastScaleMs = timestampMs;
      this.settled = Math.abs(target - this.currentScale) < .001;
      return this.currentScale;
    }
    if (timestampMs <= this.lastScaleMs) return this.currentScale;
    const elapsed = timestampMs - this.lastScaleMs; this.lastScaleMs = timestampMs;
    // A backgrounded camera or frame-model loading gap is not a long animation frame. Resume with a
    // bounded first step while preserving the locked profile and the previously displayed scale.
    const dt = Math.min(100, elapsed);
    this.currentScale += (target - this.currentScale) * (1 - Math.exp(-dt / EYEWEAR_FIT.easeMs));
    if (Math.abs(target - this.currentScale) < .001) {this.currentScale = target; this.settled = true;}
    return this.currentScale;
  }
}

const TEMPLE_GROUPS = [
  {left: [127, 234], right: [356, 454]},
  {left: [21, 162], right: [251, 389]},
  {left: [227, 137], right: [447, 366]},
] as const;

export interface FitWidthInput {
  landmarks: readonly Landmark[];
  rawMatrix: readonly number[];
  /** Observed camera-space centimetres, captured before nasal shaping or head-width fitting. */
  observedPositions: ArrayLike<number>;
  /** Width of the unscaled frame front under this same raw pose, normalized to source image width. */
  baselineFrontWidth: number;
  frontWidthCm: number;
  canonicalPositions: readonly number[];
  sourceAspect?: number;
}

/** Derive a baseline-independent upper-face/front ratio. Three neighbouring temple bands vote on the span;
 * canonical band ratios align their differing heights. Eye landmarks only establish the screen axis and reject
 * asymmetric or collapsed observations. Width is compared in a fixed virtual frontal view, so choosing a closer
 * initial camera position or a mild head tilt cannot change the resulting profile. No current fit scale or manual
 * adjustment is an input. */
export function observeFitWidth(input: FitWidthInput): FitWidthSample {
  const reject = (rejection: string): FitWidthSample => ({faceWidthCm: NaN, rejection});
  const {landmarks, rawMatrix, observedPositions, baselineFrontWidth, frontWidthCm, canonicalPositions} = input;
  const aspect = input.sourceAspect ?? 1;
  if (rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)
    || Math.abs(new Matrix4().fromArray(rawMatrix).determinant()) < 1e-12) return reject('Waiting for a clear face.');
  const pose = poseAngles(rawMatrix);
  if (![pose.yawDeg, pose.pitchDeg, pose.rollDeg, pose.depthCm].every(Number.isFinite) || pose.depthCm <= 0)
    return reject('Waiting for a clear face.');
  if (Math.abs(pose.yawDeg) > EYEWEAR_FIT.maximumYawDeg || Math.abs(pose.pitchDeg) > EYEWEAR_FIT.maximumPitchDeg
    || Math.abs(pose.rollDeg) > EYEWEAR_FIT.maximumRollDeg) return reject('Look straight ahead.');
  if (landmarks.length < 468 || canonicalPositions.length < 468 * 3 || observedPositions.length < 468 * 3 || !Number.isFinite(aspect) || aspect <= 0
    || !Number.isFinite(baselineFrontWidth) || baselineFrontWidth < .04 || baselineFrontWidth > .95
    || !Number.isFinite(frontWidthCm) || frontWidthCm <= 0) return reject('Waiting for a clear face.');
  const required = [...new Set([...TEMPLE_GROUPS.flatMap(pair => [...pair.left, ...pair.right]), 33, 133, 263, 362, 168])];
  for (const index of required) {
    const p = landmarks[index];
    if (!p || ![p.x, p.y, p.z, canonicalPositions[index * 3]].every(Number.isFinite)) return reject('Waiting for a clear face.');
    if (p.x < .02 || p.x > .98 || p.y < .02 || p.y > .98) return reject('Move fully into view.');
  }
  const midpoint = (a: number, b: number): [number, number] => [
    (landmarks[a]!.x + landmarks[b]!.x) / 2, (landmarks[a]!.y + landmarks[b]!.y) / (2 * aspect),
  ];
  const leftEye = midpoint(33, 133), rightEye = midpoint(263, 362);
  const dx = rightEye[0] - leftEye[0], dy = rightEye[1] - leftEye[1], eyeSeparation = Math.hypot(dx, dy);
  if (eyeSeparation < .02 || Math.abs(dx) < .01) return reject('Waiting for a clear face.');
  const axisX = dx / eyeSeparation, axisY = dy / eyeSeparation;
  const project = (index: number): number => landmarks[index]!.x * axisX + landmarks[index]!.y / aspect * axisY;
  const average = (indices: readonly number[], get: (index: number) => number): number =>
    indices.reduce((sum, index) => sum + get(index), 0) / indices.length;
  const eyeWidthA = Math.abs(project(33) - project(133)), eyeWidthB = Math.abs(project(263) - project(362));
  if (Math.min(eyeWidthA, eyeWidthB) < eyeSeparation * .12
    || Math.abs(eyeWidthA - eyeWidthB) / Math.max(eyeWidthA, eyeWidthB) > .42) return reject('Look straight ahead.');
  const middle = (leftEye[0] + rightEye[0]) / 2 * axisX + (leftEye[1] + rightEye[1]) / 2 * axisY;
  const canonicalX = (index: number): number => canonicalPositions[index * 3]!;
  const canonicalSide = Math.abs(average(TEMPLE_GROUPS[0].right, canonicalX) - average(TEMPLE_GROUPS[0].left, canonicalX));
  const spans: number[] = [];
  for (const pair of TEMPLE_GROUPS) {
    const a = average(pair.left, project), b = average(pair.right, project), span = Math.abs(b - a);
    const canonicalSpan = Math.abs(average(pair.right, canonicalX) - average(pair.left, canonicalX));
    if (span < .04 || canonicalSpan < 1e-6 || canonicalSide < 1e-6
      || Math.abs((a + b) / 2 - middle) / span > .15) return reject('Look straight ahead.');
    spans.push(span * canonicalSide / canonicalSpan);
  }
  spans.sort((a, b) => a - b);
  const span = quantile(spans, .5);
  if ((spans[2]! - spans[0]!) / span > .22 || eyeSeparation / span < .22 || eyeSeparation / span > .62)
    return reject('Waiting for a clear face.');
  // Keep the actual frame projection as a plausibility check only. Its front is several centimetres ahead of
  // the temple bands, so using their present pixel-width ratio directly would make a near camera fit smaller.
  const visibleRatio = span * Math.abs(axisX) / baselineFrontWidth;
  if (visibleRatio < .45 || visibleRatio > 1.6) return reject('Waiting for a clear face.');
  const inverse = new Matrix4().fromArray(rawMatrix).invert(), point = new Vector3();
  const observedReference = new Map<number, number>(), canonicalReference = new Map<number, number>();
  for (const pair of TEMPLE_GROUPS) for (const index of [...pair.left, ...pair.right]) {
    const offset = index * 3;
    point.set(observedPositions[offset]!, observedPositions[offset + 1]!, observedPositions[offset + 2]!).applyMatrix4(inverse);
    const observedDepth = EYEWEAR_FIT.referenceDistanceCm - point.z;
    const canonicalDepth = EYEWEAR_FIT.referenceDistanceCm - canonicalPositions[offset + 2]!;
    if (![point.x, point.y, point.z, canonicalDepth].every(Number.isFinite) || observedDepth < 20 || observedDepth > 80
      || canonicalDepth < 20 || canonicalDepth > 80) return reject('Waiting for a clear face.');
    observedReference.set(index, point.x / observedDepth);
    canonicalReference.set(index, canonicalPositions[offset]! / canonicalDepth);
  }
  const canonicalReferenceSide = Math.abs(average(TEMPLE_GROUPS[0].right, index => canonicalReference.get(index)!)
    - average(TEMPLE_GROUPS[0].left, index => canonicalReference.get(index)!));
  const referenceSpans = TEMPLE_GROUPS.map(pair => {
    const observedSpan = Math.abs(average(pair.right, index => observedReference.get(index)!)
      - average(pair.left, index => observedReference.get(index)!));
    const canonicalSpan = Math.abs(average(pair.right, index => canonicalReference.get(index)!)
      - average(pair.left, index => canonicalReference.get(index)!));
    return observedSpan * canonicalReferenceSide / canonicalSpan;
  }).sort((a, b) => a - b);
  const referenceSpan = quantile(referenceSpans, .5);
  if ((referenceSpans[2]! - referenceSpans[0]!) / referenceSpan > .22) return reject('Waiting for a clear face.');
  // All models share this virtual comparison plane. A profile started with Amber is therefore the same
  // profile that would have been collected with Tom Ford, despite their slightly different lens depths.
  const faceWidthCm = referenceSpan * (EYEWEAR_FIT.referenceDistanceCm - EYEWEAR_FIT.referenceFrontDepthCm);
  if (!Number.isFinite(faceWidthCm) || faceWidthCm < EYEWEAR_FIT.minimumWidthCm || faceWidthCm > EYEWEAR_FIT.maximumWidthCm)
    return reject('Waiting for a clear face.');
  return {faceWidthCm, rejection: null};
}
