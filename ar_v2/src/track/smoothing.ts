/**
 * One Euro, and the reasoning v1 paid for.
 *
 * Ported rather than rewritten. This is one of the eight things from v1 that was
 * simply right, and the file exists in v2 mostly to carry its lessons forward
 * intact:
 *
 *  1. **The speed signal must come from the measurement, not from the filter's
 *     own trend estimate.** Reading speed off the filtered output means the
 *     estimate converges over about a second, so every head movement shorter
 *     than that reads as zero speed, `beta` has nothing to multiply, and an
 *     adaptive filter behaves as a fixed low-pass. v1 shipped that bug and
 *     measured its cost: 29.5 mm of lag on a 25 cm/s look-around.
 *  2. **`beta` must be sized against the speeds a head actually reaches.**
 *     v1's was 0.06, an order of magnitude short. Fixed, the same sweep cost
 *     3.8 mm.
 *  3. **The prediction term is off.** A velocity extrapolation is free
 *     timeliness on a constant-rate ramp and overshoot at every reversal, and a
 *     head reverses two or three times a second. Measured on the same sweep, it
 *     costs three times what it saves. The capability is kept and tested; the
 *     default is off.
 *  4. **Detector noise reads as speed.** The DC component of the speed signal
 *     is never zero on a real detector, so the adaptive cutoff never fully
 *     closes and "still" shimmers. The noise floor is measured and subtracted.
 *
 * ## What changed for v2: this file is now OFF by default
 *
 * The filter smooths a **pose against a known model** rather than a pose fused
 * with an unknown shape, and that input is about three times cleaner. Measured
 * across the synthetic population, every tuning from v1's own down to a very
 * light one is worse than no filter at all, on lag and on jitter both — see the
 * table on `TrackerOptions.smooth`.
 *
 * The file stays, complete and tested, for two reasons. The measurement is
 * synthetic, and a real detector may be noisier in ways the model here does not
 * capture; and the lessons above are worth keeping written down whether or not
 * the code runs today. Turning it on is one flag.
 *
 * Reference: Casiez, Roussel & Vogel, "1 Euro Filter", CHI 2012.
 */

import {
  type Pose, type Quat, type Vec3, expSO3, logSO3, m3, mat3FromQuat, orthonormalize,
  poseClone, quatFromMat3, v3,
} from '../core/linalg.js';

const alphaFor = (cutoffHz: number, dt: number): number => {
  const tau = 1 / (2 * Math.PI * cutoffHz);
  return 1 / (1 + tau / dt);
};

class LowPass {
  value: number | null = null;

  filter(x: number, alpha: number): number {
    this.value = this.value === null ? x : this.value + alpha * (x - this.value);
    return this.value;
  }

  reset(): void { this.value = null; }
}

/**
 * The measured DC of a speed signal — what the detector reports when nothing is
 * moving.
 *
 * Tracked as a slowly-decaying minimum rather than an average: the *floor* of
 * the speed signal is the noise, and its average is the noise plus whatever
 * genuine movement happened. An average would rise during a head turn and then
 * subtract real motion from the next second of stillness.
 */
class NoiseFloor {
  private floor = Infinity;
  private readonly riseTau: number;

  constructor(riseSeconds = 3) { this.riseTau = riseSeconds; }

  observe(speed: number, dt: number): number {
    if (!Number.isFinite(this.floor)) { this.floor = speed; return this.floor; }
    if (speed < this.floor) {
      this.floor = speed;
    } else {
      // Let it climb back slowly, so a genuinely noisier regime (worse light)
      // is eventually recognised without a single quiet frame resetting it.
      const a = 1 - Math.exp(-dt / this.riseTau);
      this.floor += a * (speed - this.floor) * 0.15;
    }
    return this.floor;
  }

  reset(): void { this.floor = Infinity; }
}

export interface SmoothingSettings {
  /** Cutoff at zero speed, Hz. Lower filters harder when still. */
  minCutoffHz: number;
  /** How fast the cutoff opens with speed, Hz per (unit/second). */
  beta: number;
  /** Cutoff of the speed estimate itself, Hz. */
  derivativeCutoffHz: number;
  /** Extrapolate forward by the velocity. Off — see the file header. */
  predict: boolean;
}

/**
 * Translation is in millimetres and rotation in radians, so the two need
 * different `beta` — a `beta` sized for millimetres per second is meaningless
 * for radians per second. This is the mistake that a single shared tuning
 * constant makes inevitable, and v1 avoided it only because its translation
 * happened to be in centimetres.
 */
export const TRANSLATION_SMOOTHING: SmoothingSettings = {
  minCutoffHz: 1.2,
  // 25 cm/s = 250 mm/s should open the cutoff to ~13 Hz:
  // beta = (13 - 1.2) / 250 = 0.047.
  beta: 0.047,
  derivativeCutoffHz: 1.0,
  predict: false,
};

export const ROTATION_SMOOTHING: SmoothingSettings = {
  minCutoffHz: 1.5,
  // A brisk head turn is ~3 rad/s and should reach ~15 Hz:
  // beta = (15 - 1.5) / 3 = 4.5.
  beta: 4.5,
  derivativeCutoffHz: 1.0,
  predict: false,
};

/** A scalar One Euro channel with a measured noise floor. */
export class OneEuro {
  private readonly value = new LowPass();
  private readonly speed = new LowPass();
  private readonly floor = new NoiseFloor();
  private last: number | null = null;

  constructor(private settings: SmoothingSettings) {}

  filter(x: number, dt: number): number {
    if (!(dt > 0)) dt = 1 / 30;
    const raw = this.last === null ? 0 : (x - this.last) / dt;
    this.last = x;

    // The speed signal comes from the MEASUREMENT, not from the filtered value.
    // See the file header, point 1.
    const smoothedSpeed = Math.abs(
      this.speed.filter(raw, alphaFor(this.settings.derivativeCutoffHz, dt)),
    );
    const noise = this.floor.observe(smoothedSpeed, dt);
    const effective = Math.max(0, smoothedSpeed - noise);

    const cutoff = this.settings.minCutoffHz + this.settings.beta * effective;
    return this.value.filter(x, alphaFor(cutoff, dt));
  }

  reset(): void {
    this.value.reset(); this.speed.reset(); this.floor.reset(); this.last = null;
  }
}

/**
 * Smooths a full pose.
 *
 * Rotation is filtered **in the tangent space of the previous estimate**, not
 * as four independent quaternion channels. Filtering a quaternion
 * component-wise is the standard shortcut and it is wrong in a way that only
 * shows at speed: the four components are not independent, so the filtered
 * quaternion leaves the unit sphere and the renormalisation that follows moves
 * the rotation by an amount that depends on how fast it was turning. Filtering
 * the rotation *vector* from the previous pose has no such coupling — it is the
 * same operation the solver's own increments use.
 */
export class PoseSmoother {
  private readonly tx: OneEuro;
  private readonly ty: OneEuro;
  private readonly tz: OneEuro;
  private readonly rx: OneEuro;
  private readonly ry: OneEuro;
  private readonly rz: OneEuro;
  private previous: Pose | null = null;

  constructor(
    translation: SmoothingSettings = TRANSLATION_SMOOTHING,
    rotation: SmoothingSettings = ROTATION_SMOOTHING,
  ) {
    this.tx = new OneEuro(translation);
    this.ty = new OneEuro(translation);
    this.tz = new OneEuro(translation);
    this.rx = new OneEuro(rotation);
    this.ry = new OneEuro(rotation);
    this.rz = new OneEuro(rotation);
  }

  filter(pose: Pose, dt: number): Pose {
    const out = poseClone(pose);
    out.t[0] = this.tx.filter(pose.t[0], dt);
    out.t[1] = this.ty.filter(pose.t[1], dt);
    out.t[2] = this.tz.filter(pose.t[2], dt);

    if (!this.previous) {
      this.previous = poseClone(out);
      // Prime the rotation channels at zero so the first frame is not smoothed
      // toward the identity — which would show as the head snapping upright.
      this.rx.filter(0, dt); this.ry.filter(0, dt); this.rz.filter(0, dt);
      return out;
    }

    // delta = R_new * R_prev^-1, as a rotation vector.
    const RprevT = Float64Array.of(
      this.previous.R[0], this.previous.R[3], this.previous.R[6],
      this.previous.R[1], this.previous.R[4], this.previous.R[7],
      this.previous.R[2], this.previous.R[5], this.previous.R[8],
    );
    const delta = m3();
    mul3(delta, pose.R, RprevT);
    const w = v3();
    logSO3(w, delta);

    w[0] = this.rx.filter(w[0], dt);
    w[1] = this.ry.filter(w[1], dt);
    w[2] = this.rz.filter(w[2], dt);

    const applied = m3();
    expSO3(applied, w);
    mul3(out.R, applied, this.previous.R);
    orthonormalize(out.R, out.R);

    this.previous = poseClone(out);
    return out;
  }

  reset(): void {
    this.tx.reset(); this.ty.reset(); this.tz.reset();
    this.rx.reset(); this.ry.reset(); this.rz.reset();
    this.previous = null;
  }
}

function mul3(out: Float64Array, A: Float64Array, B: Float64Array): void {
  const a = Array.from(A), b = Array.from(B);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
    }
  }
}

export { quatFromMat3, mat3FromQuat, type Quat, type Vec3 };
