import {Matrix4, Vector3} from 'three';
import {templeHeadFitX} from './temple-head-fit.ts';

/** Keep the temple-side occluder in the eyewear's pose. Central face/nose samples remain exactly observed.
 * Canonical lateral shape follows the current attachment without a second temporal filter. */
export class TempleSurface {
  private readonly canonical: Float64Array;
  private readonly weights: Float64Array;
  private readonly attachment = new Matrix4();
  private readonly point = new Vector3();

  constructor(canonical: readonly number[]) {
    this.canonical = new Float64Array(canonical);
    this.weights = new Float64Array(canonical.length / 3);
    for (let i = 0; i < this.weights.length; i++) {
      const t = Math.max(0, Math.min(1, (Math.abs(canonical[i * 3]!) - 4.5) / 2));
      this.weights[i] = t * t * (3 - 2 * t);
    }
  }

  /** Blend fitted canonical sides into the current observations; central samples remain untouched. */
  apply(positions: Float32Array, attached: readonly number[], anteriorRatio = 1): void {
    if (positions.length !== this.canonical.length || attached.length !== 16 || !attached.every(Number.isFinite)) {
      throw new Error('The temple surface inputs are invalid.');
    }
    this.attachment.fromArray(attached);
    if (Math.abs(this.attachment.determinant()) < 1e-12) throw new Error('The temple surface pose is singular.');
    for (let i = 0; i < this.weights.length; i++) {
      const weight = this.weights[i]!; if (weight === 0) continue;
      const offset = i * 3;
      this.point.fromArray(this.canonical, offset);
      this.point.x = templeHeadFitX(this.point.x, this.point.z, anteriorRatio);
      this.point.applyMatrix4(this.attachment);
      for (let axis = 0; axis < 3; axis++) positions[offset + axis] = positions[offset + axis]!
        + weight * (this.point.getComponent(axis) - positions[offset + axis]!);
    }
  }
}
