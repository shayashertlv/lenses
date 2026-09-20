import {Matrix4} from 'three';

export const SHADOW_RECEIVER = Object.freeze({shapeTimeConstantMs: 220, resetGapMs: 500});

/** Shadow lookup coordinates, separate from the face vertices used for rasterization/occlusion.
 * Local facial shape is smoothed, then attached with the exact same pose as the glasses. This
 * removes raw-vs-filtered depth/rotation disagreement without delaying head motion or the image.
 * The receiver still rasterizes the current observed face and samples the current camera pixels. */
export class ShadowReceiver {
  private readonly inverse = new Matrix4();
  private readonly attachment = new Matrix4();
  private local: Float64Array | null = null;
  private nextLocal: Float64Array = new Float64Array(0);
  private staged = new Float32Array(0);
  private lastMs: number | null = null;

  reset(): void {this.local = null; this.lastMs = null;}

  apply(observed: Float32Array, rawPose: readonly number[], attachedPose: readonly number[], timestampMs: number,
    output: Float32Array): boolean {
    const fail = (): false => {this.reset(); return false;};
    const affine = (matrix: readonly number[]): boolean => matrix.length === 16 && matrix.every(Number.isFinite)
      && Math.abs(matrix[3]!) < 1e-9 && Math.abs(matrix[7]!) < 1e-9 && Math.abs(matrix[11]!) < 1e-9 && Math.abs(matrix[15]! - 1) < 1e-9;
    const size = observed.length;
    if (!size || size % 3 !== 0 || output.length !== size || !affine(rawPose) || !affine(attachedPose)
      || !Number.isFinite(timestampMs) || timestampMs < 0) return fail();
    this.inverse.fromArray(rawPose); this.attachment.fromArray(attachedPose);
    if (Math.abs(this.inverse.determinant()) < 1e-12 || Math.abs(this.attachment.determinant()) < 1e-12) return fail();
    this.inverse.invert();
    const inverse = this.inverse.elements, attached = this.attachment.elements;
    if (!inverse.every(Number.isFinite)) return fail();
    if (this.staged.length !== size) {this.staged = new Float32Array(size); this.nextLocal = new Float64Array(size);}
    const dt = this.lastMs === null ? 0 : timestampMs - this.lastMs;
    const restart = this.local === null || this.local.length !== size || dt > SHADOW_RECEIVER.resetGapMs;
    const integrate = restart || dt > 0;
    const alpha = restart ? 1 : dt > 0 ? -Math.expm1(-dt / SHADOW_RECEIVER.shapeTimeConstantMs) : 0;
    for (let i = 0; i < size; i += 3) {
      const x = observed[i]!, y = observed[i + 1]!, z = observed[i + 2]!;
      if (![x, y, z].every(Number.isFinite) || z >= -1e-6) return fail();
      const lx = inverse[0]! * x + inverse[4]! * y + inverse[8]! * z + inverse[12]!;
      const ly = inverse[1]! * x + inverse[5]! * y + inverse[9]! * z + inverse[13]!;
      const lz = inverse[2]! * x + inverse[6]! * y + inverse[10]! * z + inverse[14]!;
      const sx = restart ? lx : this.local![i]! + alpha * (lx - this.local![i]!);
      const sy = restart ? ly : this.local![i + 1]! + alpha * (ly - this.local![i + 1]!);
      const sz = restart ? lz : this.local![i + 2]! + alpha * (lz - this.local![i + 2]!);
      this.nextLocal[i] = sx; this.nextLocal[i + 1] = sy; this.nextLocal[i + 2] = sz;
      this.staged[i] = attached[0]! * sx + attached[4]! * sy + attached[8]! * sz + attached[12]!;
      this.staged[i + 1] = attached[1]! * sx + attached[5]! * sy + attached[9]! * sz + attached[13]!;
      this.staged[i + 2] = attached[2]! * sx + attached[6]! * sy + attached[10]! * sz + attached[14]!;
      if (![this.staged[i], this.staged[i + 1], this.staged[i + 2]].every(Number.isFinite) || this.staged[i + 2]! >= -1e-6) return fail();
    }
    output.set(this.staged);
    if (integrate) {
      const previous = this.local; this.local = this.nextLocal;
      this.nextLocal = previous?.length === size ? previous : new Float64Array(size); this.lastMs = timestampMs;
    }
    return true;
  }
}
