import {Matrix4, Vector3} from 'three';
import {FaceWidthEstimator, WIDTH_FIT, WIDTH_REGIONS} from './face-width.ts';
import type {WidthRejection} from './face-width.ts';

/** One bilateral, head-local shape estimate. This changes the occluder, never the eyewear. */
export const TEMPLE_HEAD_FIT = Object.freeze({posteriorZCm: -3, anteriorZCm: 0,
  maxBilateralDifference: .025, freezeAcceptedSamples: 90});
export interface TempleHeadFitReport {
  ratio: number; state: 'collecting' | 'stable' | 'fallback'; observedRatio: number | null;
  acceptedSamples: number; frozen: boolean; rejection: WidthRejection | 'bilateral' | null;
}

/** Identity throughout the posterior cap volume; a smooth, positive lateral scale anteriorly. */
export function templeHeadFitX(xCm: number, zCm: number, ratio: number): number {
  if (![xCm, zCm, ratio].every(Number.isFinite) || ratio < WIDTH_FIT.appliedMinRatio || ratio > WIDTH_FIT.appliedMaxRatio) {
    throw new Error('The temple head fit is out of range.');
  }
  if (ratio === 1 || zCm <= TEMPLE_HEAD_FIT.posteriorZCm) return xCm;
  const t = Math.max(0, Math.min(1, (zCm - TEMPLE_HEAD_FIT.posteriorZCm)
    / (TEMPLE_HEAD_FIT.anteriorZCm - TEMPLE_HEAD_FIT.posteriorZCm)));
  return xCm * (1 + (ratio - 1) * t * t * (3 - 2 * t));
}

export class TempleHeadFit {
  private readonly estimator: FaceWidthEstimator;
  private readonly canonical: Float64Array;
  private readonly inverse = new Matrix4();
  private readonly point = new Vector3();
  private lastTrackedMs: number | null = null;
  private lastRejection: TempleHeadFitReport['rejection'] = null;
  private frozen = false;

  constructor(canonicalPositions: readonly number[]) {
    this.canonical = new Float64Array(canonicalPositions);
    this.estimator = new FaceWidthEstimator(canonicalPositions);
  }
  get ratio(): number {return this.estimator.ratio;}
  get report(): TempleHeadFitReport {return {ratio: this.ratio, state: this.frozen ? 'stable' : 'collecting',
    observedRatio: this.estimator.observedRatio, acceptedSamples: this.estimator.accepted, frozen: this.frozen, rejection: this.lastRejection};}
  reset(): void {this.estimator.reset(); this.lastTrackedMs = null; this.lastRejection = null; this.frozen = false;}
  miss(timestampMs: number): void {
    if (this.lastTrackedMs !== null && timestampMs - this.lastTrackedMs >= WIDTH_FIT.lostResetMs) this.reset();
  }
  observe(surfacePositions: ArrayLike<number>, rawMatrix: readonly number[], timestampMs: number): void {
    if (!Number.isFinite(timestampMs)) return;
    this.lastTrackedMs = timestampMs;
    // Freeze the already-eased value, without a final snap. Later changes in distance, detector
    // scale bias or pose cannot reshape a calibrated head. Session/loss reset starts a fresh fit.
    if (this.frozen) return;
    // The ordinary estimator admits a fairly broad pair asymmetry for frame fitting. An occluder
    // must not turn unilateral landmark error into a new bilateral silhouette, so require both
    // halves of the majority of sampled regions to support the same proportional change.
    if (surfacePositions.length < this.canonical.length || rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)) {
      this.lastRejection = 'landmarks'; return;
    }
    this.inverse.fromArray(rawMatrix);
    if (Math.abs(this.inverse.determinant()) < 1e-12) {this.lastRejection = 'pose'; return;}
    this.inverse.invert();
    const ratioFor = (indices: readonly number[]): number => {
      let observed = 0, canonical = 0;
      for (const index of indices) {
        const offset = index * 3;
        this.point.set(surfacePositions[offset]!, surfacePositions[offset + 1]!, surfacePositions[offset + 2]!).applyMatrix4(this.inverse);
        observed += this.point.x; canonical += this.canonical[offset]!;
      }
      return observed / canonical;
    };
    const differences: number[] = [];
    for (const region of WIDTH_REGIONS) {
      const left = ratioFor(region.left), right = ratioFor(region.right);
      if (![left, right].every(Number.isFinite)) {this.lastRejection = 'landmarks'; return;}
      differences.push(Math.abs(left - right));
    }
    differences.sort((a, b) => a - b);
    if (differences[differences.length >> 1]! > TEMPLE_HEAD_FIT.maxBilateralDifference) {this.lastRejection = 'bilateral'; return;}
    this.estimator.observe(surfacePositions, rawMatrix, timestampMs);
    this.lastRejection = this.estimator.lastRejection;
    if (this.estimator.accepted >= TEMPLE_HEAD_FIT.freezeAcceptedSamples) this.frozen = true;
  }
}
