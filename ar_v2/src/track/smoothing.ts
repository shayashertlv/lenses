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
 * ## What changed for v2: off by default in the library, ON in the app
 *
 * The filter smooths a **pose against a known model** rather than a pose fused
 * with an unknown shape, and that input is about three times cleaner. Measured
 * across the synthetic population in 2026-08, every tuning from v1's own down to
 * a very light one was worse than no filter at all, on lag and on jitter both —
 * see the table on `TrackerOptions.smooth`.
 *
 * **Both halves of that have since moved.** A real detector was noisier in
 * exactly the way the caveat below predicted, so the app has run this file on
 * every frame since 2026-08-23 (latched at first, then plain); and re-measured 2026-08-31 the filtered arm now
 * WINS jitter median and p90 5/5 across the campaign seeds (0.945 against 1.469;
 * 1.944 against 2.519). What it still costs is lag. `TRACKER_DEFAULTS.smooth`
 * stays `false` as the library default so tests and goldens are unaffected.
 *
 * The file stays, complete and tested, for two reasons. The measurement was
 * synthetic, and a real detector may be noisier in ways the model here does not
 * capture — which is what happened; and the lessons above are worth keeping
 * written down.
 *
 * ## The adaptive mode, and why a fixed tuning cannot serve a turned head
 *
 * The first real wearer confirmed the "may be noisier" caveat above, with a
 * detail the fixed filter cannot answer: the jiggle GROWS WITH YAW ("a tiny bit
 * smoother, same after 15 degrees"). That is exactly what the uncertainty
 * machinery predicts — at yaw the far-side landmarks are hallucinated and their
 * sigma inflates up to sevenfold — and a One Euro tuned for the frontal noise
 * floor is, at 35 degrees, a filter tuned for somebody else's detector. The
 * fixed tuning cannot be turned up to serve the turned head without lagging the
 * frontal one, which is the exact trade the wearer reported.
 *
 * So the tracker's `'adaptive'` mode rides the sigma the tracker already has:
 * see `noiseScaleFromSigma` for the formula and the constants, and
 * `OneEuro.filter`'s `noiseScale` parameter for where it lands. With
 * `noiseScale = 1` (its default) every path through this file is bit-identical
 * to the fixed filter — asserted golden-value-exact in core.test.ts — so `off`
 * and `on` mean today what they meant yesterday.
 *
 * Reference: Casiez, Roussel & Vogel, "1 Euro Filter", CHI 2012.
 */

import {
  type Pose, type Quat, type Vec3, expSO3, logSO3, m3, m3transpose, mat3FromQuat, orthonormalize,
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
  // 5, up from the classic 1 (2026-08-23). The speed estimate is what OPENS
  // the cutoff, and at 1 Hz it needs ~4 frames to notice a step and never
  // fully opens during a 0.75 Hz head-wave — the wearer localized their
  // "delay" to exactly those two shapes. Swept {1,2,3,5,8} on correlated-
  // wander fixtures (3 seeds, both channels): 5 halves the step response
  // (4 -> 2 frames to 90%) and cuts wave-tracking RMS by a third
  // (3.85 -> 2.61 mm at +/-30 mm 0.75 Hz) for +4% rest jitter
  // (0.0430 -> 0.0448 mm/frame) — nearly free because the NoiseFloor
  // subtracts the DC of the noisier speed signal along with the old one.
  // 8 adds almost nothing (2.61 -> 2.50). See the ledger row.
  derivativeCutoffHz: 5,
  predict: false,
};

/**
 * How far the rotation loop is allowed to sit from critically damped.
 * 0 is critical damping; 1 is the historical loop, which was undamped.
 *
 * ## What was wrong
 *
 * Translation low-passes an ABSOLUTE value: `out = LowPass(x)`, first order,
 * monotone. Rotation cannot — a rotation is not a vector space — so it filters
 * in the tangent space of the previous estimate, and the previous estimate it
 * used was its own OUTPUT. That makes the loop
 *
 *     o_t = o_{t-1} + LowPass(x_t - o_{t-1})
 *
 * which is an integrator with a lagging element inside it: second order, and
 * underdamped for EVERY alpha. Writing e = x - o for a constant x gives
 * `e_t = 2(1-a) e_{t-1} - (1-a) e_{t-2}`, whose discriminant is -4a(1-a) < 0 —
 * complex roots, so the step response is a damped OSCILLATION rather than a
 * decay. Measured on the shipped tuning: a 12 degree turn at 36 deg/s
 * overshot 7.2% and rang for about a second; an instantaneous step overshot
 * 25%. Translation over the same step peaks at 511.995 of 512 mm.
 *
 * ## The fix, and the one that did not work
 *
 * A leak on the accumulator breaks the equality of the two coefficients. With
 * `s_t = rho (1-a) s_{t-1} + a w_t` the characteristic polynomial is
 * `z^2 - (1 + b - a) z + b` for `b = rho (1-a)`, which is critically damped at
 * `b = (1 - sqrt a)^2`, i.e.
 *
 *     rho_critical = (1 - sqrt a) / (1 + sqrt a)
 *
 * — a closed form in the alpha the One Euro already computed, so damping needs
 * no tuning constant of its own. This one only says how much of the way to go.
 *
 * ## The change that was tried here and REFUSED, so nobody tries it twice
 *
 * The rotation channels feed One Euro `d(innovation)/dt` rather than the head's
 * own angular rate, and that looks like a second bug: a filter keeping up with
 * a steady turn has a nearly constant innovation, so the derivative reads near
 * zero and `beta` is close to inert mid-turn. Feeding the MEASUREMENT stream's
 * angular rate instead — `|log(R_raw,t · R_raw,t-1^T)|/dt` — was built,
 * measured, and reverted:
 *
 *     metric (real PoseSmoother)        innovation rate   measurement rate
 *     tracking RMS, 12 deg @ 36 deg/s,
 *       0.35 deg/frame noise                 0.2598 deg        0.2849 deg
 *     tracking RMS, 30 deg @ 60 deg/s        0.3516            0.4070
 *     max lag during a noiseless
 *       36 deg/s ramp                        0.906             0.996
 *
 * Worse on both, on the real filter, in both regimes. A scalar simulation of
 * the same loop said the opposite, which is the reason the measurement is
 * quoted here and the simulation is not: the innovation is not v1's mistake.
 * v1 read speed off the filtered output's own TREND, which converges over a
 * second and genuinely cannot see a short movement; the innovation contains
 * `x_t` directly, so it responds to the measurement on the frame it arrives —
 * and at motion onset it responds harder than the true rate does, which is
 * what buys the lower lag. The damping below is doing all the work, and it
 * does it with the speed signal the file already had.
 *
 * ## The sweep
 *
 * `measured`: swept over the damping blend on ramp-and-hold maneuvers with
 * 0.35 deg of per-frame rotational noise, against the shipped tuning. Median
 * over three ramps (12 deg at 36 deg/s, 30 at 60, 20 at 120), and rest jitter
 * over 200 still frames. (These figures come from the scalar sweep, which the
 * paragraph above shows is not reliable on the speed-signal question; the
 * DAMPING half was confirmed on the real filter — see the step-response
 * numbers in `tests/core.test.ts`, 25.0% -> 4.9% on a 12 degree step and
 * 7.2% -> 1.2% on a 36 deg/s turn, with settling roughly halved.)
 *
 *     blend   pose RMS deg   overshoot %   max lag deg   rest jitter deg/frame
 *     0.00    0.349-0.495     1.2-3.0       1.22-1.95     0.0861
 *     0.25    0.306-0.415     1.4-3.5       1.02-1.78     0.0920
 *     0.40    0.289-0.383     1.8-5.1       1.00-1.68     0.0970
 *     today   0.391-0.538     4.6-11.9      0.93-1.57     0.1376
 *
 * 0.25 is adopted: it beats the shipped loop on RMS in all three maneuvers, on
 * overshoot by roughly 3x, and on rest jitter by a third, for about 0.1 degree
 * of extra lag — 0.15 mm at a 90 mm temple lever, against the 4.7 mm the
 * overshoot was throwing past the face. Going further to 0 buys another point
 * of overshoot and costs 0.2 degrees more lag, and lag is the thing the wearer
 * has actually complained about; 0.4 lets the fast ramp back over 5%.
 *
 * The rest-jitter column is the result that was not predicted: an undamped
 * loop amplifies detector noise as well as steps, so damping it is not only a
 * transient fix. That is the filter's whole job, and it was doing it a third
 * worse than it needed to.
 */
export const ROTATION_DAMPING = 0.25;

export const ROTATION_SMOOTHING: SmoothingSettings = {
  minCutoffHz: 1.5,
  // A brisk head turn is ~3 rad/s and should reach ~15 Hz:
  // beta = (15 - 1.5) / 3 = 4.5.
  beta: 4.5,
  // Same sweep, same knee: step 4 -> 2 frames, wave RMS 0.024 -> 0.019 rad,
  // rest jitter flat to the fourth decimal. See TRANSLATION_SMOOTHING.
  derivativeCutoffHz: 5,
  predict: false,
};

/**
 * The floor the adaptive noise scale is measured against, px.
 *
 * `derived`: the same number as `UNCERTAINTY_DEFAULTS.floorPx` — the detector's
 * measured noise on a still, frontal, well-lit face at the 640 px detection
 * resolution. It is restated here rather than imported because `track/` takes
 * sigma as data and must not depend on how `detect/` produced it; the two
 * constants describe the same physical measurement and must move together.
 *
 * Callers whose landmarks (and therefore sigmas) are in SOURCE pixels rather
 * than detect pixels must pass `floorPx * pixelScale`, the same rule
 * `estimateSigma` itself follows — see `TrackerOptions.adaptiveFloorPx`.
 */
export const ADAPTIVE_SIGMA_FLOOR_PX = 0.7;

/**
 * The most the adaptive mode may slow the filter down, as a multiple of the
 * fixed tuning's cutoff.
 *
 * `measured`: swept over {4, 8, 16} on the synthetic tracked fixture (a quiet
 * centre hold, the capture machinery's wander through 25-40 degrees of yaw,
 * and the 80-degree profile holds; subject S00 across the camera ladder,
 * production `estimateSigma` in the loop). The production sigma stream's mean
 * runs 4.3x the floor on the QUIET hold (see `noiseScaleFromSigma` for why),
 * 5.5x through 25-40 degrees, 7.6x at profile — so:
 *
 *   - cap 4 clips every regime to the same 4.0: the filter stops being
 *     adaptive at all, and high-yaw jitter is worse than cap 8's
 *     (1.383 vs 1.298 mm median frame-to-frame bridge delta).
 *   - cap 16 never binds — the stream tops out at 8.7x, because the occlusion
 *     inflation is capped at 7x per landmark — and is identical to cap 8 to
 *     the third decimal on every segment.
 *   - cap 8 keeps the measured gradient intact while still bounding the frame
 *     nothing here can produce but a real session can: a hand-over-face
 *     disagreement spike inflates sigma without bound, and an uncapped scale
 *     would freeze the pose in response.
 */
export const ADAPTIVE_NOISE_SCALE_MAX = 8;

/**
 * The noise scale for one frame: how much noisier the landmarks are than the
 * clean-frontal floor.
 *
 *     noiseScale = clamp( mean(finite sigmaPx) / floorPx, 1, ADAPTIVE_NOISE_SCALE_MAX )
 *
 * The mean over FINITE sigmas: an absent landmark (sigma Infinity) says nothing
 * about the noise of the ones that are present. The clamp below at 1 means the
 * adaptive filter can only ever smooth MORE than the fixed tuning, never less —
 * a sigma stream at the floor gets exactly the tuned filter, and a sigma below
 * the floor is a calibration accident, not a licence to chase the measurement.
 * The clamp above is `ADAPTIVE_NOISE_SCALE_MAX`.
 *
 * **What the scale actually reads on the production stream** — measured, and
 * worth knowing before wiring a UI to it: `estimateSigma`'s mean never comes
 * near the floor, even on a still frontal face. The mesh rim is permanently
 * oblique so its occlusion inflation never reads zero, and the disagreement
 * EMA rides at the detector noise it is measuring — the mean's medians over
 * the wander fixture are 4.3x (quiet), 5.5x (25-40 degrees), 7.6x (profile
 * hold). So on real input the adaptive filter is a *stronger-than-tuned*
 * filter whose strength then RIDES YAW — the gradient on top of the 4.3x base
 * is the part that answers the wearer's "jiggle grows with yaw". The scale
 * reaches 1 only on sigma streams that really are at the floor (the synthetic
 * capture's own noise model, unit tests).
 *
 * Where the scale lands: `OneEuro.filter` divides its cutoff — minCutoff AND
 * the beta term both — by this factor. Dividing the whole cutoff rather than
 * minCutoff alone is a measured choice, not a stylistic one: at high yaw the
 * inflated landmark noise leaks into the speed estimate faster than the
 * NoiseFloor's 3-second rise can subtract it, so an unscaled beta reads that
 * noise as head motion and re-opens the cutoff exactly when it should be
 * closing. Measured on the 25-40 degree wander segment (median frame-to-frame
 * bridge delta): scaling minCutoff only, 1.527 mm — indistinguishable from
 * the fixed filter's 1.533; scaling both, 1.298 mm, at a cost of one frame of
 * step lag (3 against the fixed filter's 2 frames to 90% of a clean
 * 15-degree step turn).
 */
export function noiseScaleFromSigma(
  sigmaPx: Float64Array, floorPx: number = ADAPTIVE_SIGMA_FLOOR_PX,
): number {
  let sum = 0;
  let n = 0;
  for (let i = 0; i < sigmaPx.length; i++) {
    const s = sigmaPx[i];
    if (Number.isFinite(s)) { sum += s; n++; }
  }
  if (n === 0 || !(floorPx > 0)) return 1;
  const scale = sum / (n * floorPx);
  return scale < 1 ? 1 : scale > ADAPTIVE_NOISE_SCALE_MAX ? ADAPTIVE_NOISE_SCALE_MAX : scale;
}

/** A scalar One Euro channel with a measured noise floor. */
export class OneEuro {
  private readonly value = new LowPass();
  private readonly speed = new LowPass();
  private readonly floor = new NoiseFloor();
  private last: number | null = null;
  /** The tangent-space path's leaky accumulator. Untouched by `filter`. */
  private accumulator = 0;

  constructor(private settings: SmoothingSettings) {}

  /**
   * `noiseScale >= 1` divides the effective cutoff: heavier landmark noise,
   * lower cutoff, more smoothing. See `noiseScaleFromSigma` for the formula and
   * the measurement behind dividing the whole cutoff rather than minCutoff
   * alone. At the default `1` the division is exact in IEEE arithmetic, so the
   * fixed-tuning path is bit-identical to a build that never had the parameter.
   */
  filter(x: number, dt: number, noiseScale = 1): number {
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

    const cutoff = (this.settings.minCutoffHz + this.settings.beta * effective) / noiseScale;
    return this.value.filter(x, alphaFor(cutoff, dt));
  }

  /**
   * One step of the TANGENT-SPACE path, used by the rotation channels.
   *
   * `innovation` is the angle from the previous OUTPUT to the new measurement,
   * about this axis. Returns the increment to compose onto the previous output.
   *
   * Line for line this is `filter`, with one substitution: the plain `LowPass`
   * on the value becomes a LEAKY one. The two coincide exactly at
   * `ROTATION_DAMPING = 1`, since `acc = (1-a) acc + a w` IS the low-pass
   * recursion — which is what makes the damping constant a true dial between
   * the historical behaviour and critical damping, and what lets a sabotage
   * set it to 1 and recover the old filter bit for bit.
   *
   * Separate from `filter` rather than folded into it so the translation path
   * stays bit-identical: every golden in `core.test.ts` goes through `filter`
   * and none of them reach here.
   *
   * See `ROTATION_DAMPING` for the leak, its derivation, and the sweep.
   */
  stepDamped(innovation: number, dt: number, noiseScale = 1): number {
    if (!(dt > 0)) dt = 1 / 30;

    const raw = this.last === null ? 0 : (innovation - this.last) / dt;
    this.last = innovation;

    const smoothedSpeed = Math.abs(
      this.speed.filter(raw, alphaFor(this.settings.derivativeCutoffHz, dt)),
    );
    const noise = this.floor.observe(smoothedSpeed, dt);
    const effective = Math.max(0, smoothedSpeed - noise);

    const cutoff = (this.settings.minCutoffHz + this.settings.beta * effective) / noiseScale;
    const a = alphaFor(cutoff, dt);

    // rho_critical = (1 - sqrt a)/(1 + sqrt a); blend toward the undamped 1.
    const root = Math.sqrt(a);
    const critical = (1 - root) / (1 + root);
    const rho = critical + (1 - critical) * ROTATION_DAMPING;

    this.accumulator = rho * (1 - a) * this.accumulator + a * innovation;
    return this.accumulator;
  }

  reset(): void {
    this.value.reset(); this.speed.reset(); this.floor.reset();
    this.last = null; this.accumulator = 0;
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

  /** `noiseScale` rides through to all six channels — see `OneEuro.filter`. */
  filter(pose: Pose, dt: number, noiseScale = 1): Pose {
    const out = poseClone(pose);
    out.t[0] = this.tx.filter(pose.t[0], dt, noiseScale);
    out.t[1] = this.ty.filter(pose.t[1], dt, noiseScale);
    out.t[2] = this.tz.filter(pose.t[2], dt, noiseScale);

    if (!this.previous) {
      this.previous = poseClone(out);
      // Prime the rotation channels at zero so the first frame is not smoothed
      // toward the identity — which would show as the head snapping upright.
      this.rx.stepDamped(0, dt, noiseScale);
      this.ry.stepDamped(0, dt, noiseScale);
      this.rz.stepDamped(0, dt, noiseScale);
      return out;
    }

    // The INNOVATION: from the previous smoothed output to the new measurement.
    const delta = m3();
    mul3(delta, pose.R, m3transpose(m3(), this.previous.R));
    const w = v3();
    logSO3(w, delta);

    w[0] = this.rx.stepDamped(w[0], dt, noiseScale);
    w[1] = this.ry.stepDamped(w[1], dt, noiseScale);
    w[2] = this.rz.stepDamped(w[2], dt, noiseScale);

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
