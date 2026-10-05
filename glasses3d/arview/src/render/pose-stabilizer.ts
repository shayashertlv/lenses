/** Pose steadiness, on by default (`?steady=0` turns it off; owner-accepted live on 2026-09-17). MediaPipe re-estimates its facial transformation from each frame's landmarks
 *  alone, so the orientation and the camera depth carry frame-to-frame noise that the glasses copy while the face in the
 *  video stays still. This filter smooths the orientation (a quaternion) and the depth with One Euro filters whose cutoff
 *  rises with their own low-passed velocity: strong while the head is still, light while it moves.
 *
 *  The image-plane position is not filtered: the bridge pin runs after this and re-anchors the glasses to this frame's
 *  nose landmarks, so smoothing cannot slide them across the face. The detector's matrix is rigid (unit column norms,
 *  measured on recorded detections), so there is no scale to filter; any scale present is passed through unchanged.
 *
 *  The velocity that drives the cutoff is low-passed as a signed vector before its magnitude is taken (as One Euro does
 *  for a scalar), so zero-mean detector noise averages out instead of reading as head motion. */
import {Euler, Matrix4, Quaternion, Vector3} from 'three';

export interface SteadyOptions {
  /** Rotation cutoff at rest, Hz. Lower is steadier and slower to follow small movements. */
  readonly rotationMinCutoffHz: number;
  /** Added rotation cutoff per °/s of low-passed angular speed. Higher follows turns more closely. */
  readonly rotationBeta: number;
  /** Depth cutoff at rest, Hz. */
  readonly depthMinCutoffHz: number;
  /** Added depth cutoff per cm/s of low-passed depth speed. */
  readonly depthBeta: number;
  /** Cutoff of the velocity estimates, Hz. */
  readonly derivativeCutoffHz: number;
  /** A gap between poses longer than this restarts the filter from the raw pose, ms. */
  readonly resetGapMs: number;
}

export const DEFAULT_STEADY: Readonly<SteadyOptions> = Object.freeze({
  rotationMinCutoffHz: 1, rotationBeta: .3, depthMinCutoffHz: 1, depthBeta: .8, derivativeCutoffHz: 3, resetGapMs: 500,
});

/** Numbers only, for the timing row: the raw pose, the steadied pose and what the filter did. */
export interface PoseSample {
  yawDeg: number; pitchDeg: number; rollDeg: number; depthCm: number;
  steadyYawDeg: number | null; steadyPitchDeg: number | null; steadyRollDeg: number | null; steadyDepthCm: number | null;
  /** Angle between the raw and the steadied orientation, degrees. */
  steadyLagDeg: number | null;
  steadyRotationCutoffHz: number | null; steadyDepthCutoffHz: number | null;
  /** True on a frame where the filter restarted from the raw pose. */
  steadyReset: boolean | null;
}

export interface SteadyResult {
  matrix: number[];
  lagDeg: number; rotationCutoffHz: number; depthCutoffHz: number; reset: boolean;
}

const RAD_TO_DEG = 180 / Math.PI;
/** Smoothing factor of a first-order low-pass at `cutoffHz` for a step of `dtS` seconds. */
export const smoothingFactor = (cutoffHz: number, dtS: number): number => 1 / (1 + 1 / (2 * Math.PI * cutoffHz * dtS));

export function validateSteadyOptions(options: SteadyOptions): void {
  const positive = [options.rotationMinCutoffHz, options.depthMinCutoffHz, options.derivativeCutoffHz, options.resetGapMs];
  if (!positive.every(value => Number.isFinite(value) && value > 0)
    || ![options.rotationBeta, options.depthBeta].every(value => Number.isFinite(value) && value >= 0)) {
    throw new Error('The pose steadiness settings are invalid.');
  }
}

/** Yaw, pitch and roll (degrees, intrinsic Y-X-Z) and depth (cm, positive in front of the camera) of a pose matrix. */
export function poseAngles(matrix: readonly number[]): {yawDeg: number; pitchDeg: number; rollDeg: number; depthCm: number} {
  const quaternion = new Quaternion(), position = new Vector3(), scale = new Vector3();
  new Matrix4().fromArray(matrix).decompose(position, quaternion, scale);
  const euler = new Euler().setFromQuaternion(quaternion, 'YXZ');
  return {yawDeg: euler.y * RAD_TO_DEG, pitchDeg: euler.x * RAD_TO_DEG, rollDeg: euler.z * RAD_TO_DEG, depthCm: -position.z};
}

export class PoseStabilizer {
  readonly options: Readonly<SteadyOptions>;
  private lastMs: number | null = null;
  private readonly rawQuaternion = new Quaternion();
  private readonly outQuaternion = new Quaternion();
  private readonly angularVelocity = new Vector3();
  private rawDepth = 0;
  private outDepth = 0;
  private depthVelocity = 0;
  private readonly position = new Vector3();
  private readonly scale = new Vector3();
  private readonly quaternion = new Quaternion();
  private readonly step = new Quaternion();
  private readonly matrix = new Matrix4();

  constructor(options: SteadyOptions = DEFAULT_STEADY) {
    validateSteadyOptions(options);
    this.options = Object.freeze({...options});
  }

  /** Forget the history: the next pose passes through unchanged. Call when the face is lost. */
  reset(): void {this.lastMs = null;}

  /** The steadied pose for this frame's raw detector pose (column-major 4×4, camera cm) at `timestampMs`. */
  apply(raw: readonly number[], timestampMs: number): SteadyResult {
    if (raw.length !== 16 || !raw.every(Number.isFinite) || !Number.isFinite(timestampMs)) throw new Error('The pose to steady is invalid.');
    this.matrix.fromArray(raw).decompose(this.position, this.quaternion, this.scale);
    const dtMs = this.lastMs === null ? NaN : timestampMs - this.lastMs;
    if (!(dtMs > 0) || dtMs > this.options.resetGapMs) {
      this.rawQuaternion.copy(this.quaternion); this.outQuaternion.copy(this.quaternion); this.angularVelocity.set(0, 0, 0);
      this.rawDepth = this.outDepth = this.position.z; this.depthVelocity = 0; this.lastMs = timestampMs;
      return {matrix: raw.slice(), lagDeg: 0, rotationCutoffHz: this.options.rotationMinCutoffHz, depthCutoffHz: this.options.depthMinCutoffHz, reset: true};
    }
    const dtS = dtMs / 1000, derivative = smoothingFactor(this.options.derivativeCutoffHz, dtS);
    // Rotation: the camera-frame rotation from the previous raw orientation to this one, as an angular velocity vector.
    if (this.quaternion.dot(this.rawQuaternion) < 0) this.quaternion.set(-this.quaternion.x, -this.quaternion.y, -this.quaternion.z, -this.quaternion.w);
    this.step.copy(this.rawQuaternion).invert().premultiply(this.quaternion); // step · previous = current
    const half = Math.min(1, Math.abs(this.step.w)), angle = 2 * Math.acos(half), sine = Math.sqrt(Math.max(0, 1 - half * half));
    const sign = this.step.w < 0 ? -1 : 1, rate = angle * RAD_TO_DEG / dtS;
    const velocityX = sine > 1e-12 ? sign * this.step.x / sine * rate : 0, velocityY = sine > 1e-12 ? sign * this.step.y / sine * rate : 0, velocityZ = sine > 1e-12 ? sign * this.step.z / sine * rate : 0;
    this.angularVelocity.set(
      this.angularVelocity.x + derivative * (velocityX - this.angularVelocity.x),
      this.angularVelocity.y + derivative * (velocityY - this.angularVelocity.y),
      this.angularVelocity.z + derivative * (velocityZ - this.angularVelocity.z));
    const rotationCutoffHz = this.options.rotationMinCutoffHz + this.options.rotationBeta * this.angularVelocity.length();
    this.outQuaternion.slerp(this.quaternion, smoothingFactor(rotationCutoffHz, dtS));
    this.rawQuaternion.copy(this.quaternion);
    // Depth: the same filter on one axis.
    const depthRate = (this.position.z - this.rawDepth) / dtS;
    this.depthVelocity += derivative * (depthRate - this.depthVelocity);
    const depthCutoffHz = this.options.depthMinCutoffHz + this.options.depthBeta * Math.abs(this.depthVelocity);
    this.outDepth += smoothingFactor(depthCutoffHz, dtS) * (this.position.z - this.outDepth);
    this.rawDepth = this.position.z; this.lastMs = timestampMs;
    const lagDeg = this.outQuaternion.angleTo(this.quaternion) * RAD_TO_DEG;
    this.position.z = this.outDepth;
    const matrix = this.matrix.compose(this.position, this.outQuaternion, this.scale).toArray();
    return {matrix, lagDeg, rotationCutoffHz, depthCutoffHz, reset: false};
  }
}
