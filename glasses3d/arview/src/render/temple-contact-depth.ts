import {Matrix4} from 'three';

export const TEMPLE_CONTACT_DEPTH = Object.freeze({
  shapeTimeConstantMs: 45,
  maximumCorrectionCm: .075,
  resetGapMs: 250,
});

/** Filters only observed face shape used by the private cheek-depth pass. The current raw pose
 * restores camera-space depth, and current image rays restore XY: neither pose nor the visible
 * face boundary receives temporal lag. Positions and pose translations are in centimetres. */
export class TempleContactDepth {
  private readonly pose = new Matrix4();
  private readonly inverse = new Matrix4();
  private shape: Float64Array | null = null;
  private nextShape: Float64Array = new Float64Array(0);
  private staged = new Float32Array(0);
  private timestampMs: number | null = null;

  reset(): void {
    this.shape = null;
    this.timestampMs = null;
  }

  /** On failure, history is reset and output is untouched. Callers must not present an old output.
   * Repeated/non-increasing timestamps render without integrating another shape observation. */
  apply(rawPositions: Float32Array, rawMatrix: readonly number[], timestampMs: number, output: Float32Array): boolean {
    const fail = (): false => {this.reset(); return false;};
    const size = rawPositions.length;
    if (!size || size % 3 !== 0 || output.length !== size || rawMatrix.length !== 16
      || !Number.isFinite(timestampMs) || timestampMs < 0 || !rawMatrix.every(Number.isFinite)) return fail();
    // A projective matrix cannot be used as the metric head-local pose.
    if (Math.abs(rawMatrix[3]!) > 1e-9 || Math.abs(rawMatrix[7]!) > 1e-9
      || Math.abs(rawMatrix[11]!) > 1e-9 || Math.abs(rawMatrix[15]! - 1) > 1e-9) return fail();
    this.pose.fromArray(rawMatrix);
    const determinant = this.pose.determinant();
    if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-12) return fail();
    this.inverse.copy(this.pose).invert();
    const inverse = this.inverse.elements;
    if (!inverse.every(Number.isFinite)) return fail();
    if (this.staged.length !== size) {
      this.staged = new Float32Array(size);
      this.nextShape = new Float64Array(size);
    }
    const dt = this.timestampMs === null ? 0 : timestampMs - this.timestampMs;
    const restart = this.shape === null || this.shape.length !== size || dt > TEMPLE_CONTACT_DEPTH.resetGapMs;
    const integrate = restart || dt > 0;
    const alpha = restart ? 1 : dt > 0 ? -Math.expm1(-dt / TEMPLE_CONTACT_DEPTH.shapeTimeConstantMs) : 0;
    const pose = this.pose.elements;
    for (let i = 0; i < size; i += 3) {
      const x = rawPositions[i]!, y = rawPositions[i + 1]!, z = rawPositions[i + 2]!;
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z) || z >= -1e-6) return fail();
      const lx = inverse[0]! * x + inverse[4]! * y + inverse[8]! * z + inverse[12]!;
      const ly = inverse[1]! * x + inverse[5]! * y + inverse[9]! * z + inverse[13]!;
      const lz = inverse[2]! * x + inverse[6]! * y + inverse[10]! * z + inverse[14]!;
      if (!Number.isFinite(lx) || !Number.isFinite(ly) || !Number.isFinite(lz)) return fail();
      const sx = restart ? lx : this.shape![i]! + alpha * (lx - this.shape![i]!);
      const sy = restart ? ly : this.shape![i + 1]! + alpha * (ly - this.shape![i + 1]!);
      const sz = restart ? lz : this.shape![i + 2]! + alpha * (lz - this.shape![i + 2]!);
      this.nextShape[i] = sx; this.nextShape[i + 1] = sy; this.nextShape[i + 2] = sz;
      if (restart) {
        this.staged[i] = x; this.staged[i + 1] = y; this.staged[i + 2] = z;
      } else {
        const filteredZ = pose[2]! * sx + pose[6]! * sy + pose[10]! * sz + pose[14]!;
        if (!Number.isFinite(filteredZ)) return fail();
        const correction = Math.max(-TEMPLE_CONTACT_DEPTH.maximumCorrectionCm,
          Math.min(TEMPLE_CONTACT_DEPTH.maximumCorrectionCm, filteredZ - z));
        const currentZ = Math.min(-1e-6, z + correction), rayScale = currentZ / z;
        this.staged[i] = x * rayScale; this.staged[i + 1] = y * rayScale; this.staged[i + 2] = currentZ;
        if (!Number.isFinite(this.staged[i]!) || !Number.isFinite(this.staged[i + 1]!)
          || !Number.isFinite(this.staged[i + 2]!)) return fail();
      }
    }
    // Commit only after every vertex passes validation; a bad late vertex cannot pollute history.
    output.set(this.staged);
    if (integrate) {
      const previous = this.shape;
      this.shape = this.nextShape;
      this.nextShape = previous?.length === size ? previous : new Float64Array(size);
      this.timestampMs = timestampMs;
    }
    return true;
  }
}
