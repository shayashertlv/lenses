/**
 * The settle metric — how long a channel takes to stop CONVERGING, measured
 * apart from how much it JITTERS.
 *
 * Two different things move the drawn frame while somebody sits still, and the
 * whole of Goal 2 is about one of them:
 *
 *   - **jitter** — per-frame, zero-mean, already measured everywhere in this
 *     tree (`stab.rmsPx`, `screenTail.rmsPx`, the seat measurable-4 push RMS).
 *     A real session carries 3–4 px of it on the composed origin and nothing in
 *     the convergence work is expected to change that.
 *   - **convergence drift** — a monotone-ish walk of the placement's MEAN over
 *     seconds, as the person model accumulates and the seat's confidence ramp
 *     lets the solved height in. This is what the wearer sees as the frame
 *     creeping, and it is the quantity the settle target is about.
 *
 * The metric this replaces could not tell them apart, and the arithmetic says
 * so before any run does. It was a last-exit time on the RAW composed position
 * against an absolute 0.5 px band:
 *
 *     settleMs = min{ t : |p(t') − p_∞| ≤ 0.5 px  ∀ t' ≥ t }
 *
 * On a signal whose own still-segment RMS is 3.97 px (telemetry, production)
 * the band is exceeded on essentially every frame, so the last exit is the run
 * length on every subject, before and after any change alike. A gate that reads
 * "never settled" identically in both arms is not a gate. Worse, the failure is
 * silent: it looks like a very strict bar rather than a broken instrument.
 *
 * ---------------------------------------------------------------- the metric
 *
 * Measure the LOCATION, not the sample. For a channel x sampled at times t_k:
 *
 *   window(t)  the samples in (t − W, t]           W = SETTLE_WINDOW_S
 *   m(t)       median of the window                 the location estimate
 *   c(t)       (t_oldest + t_newest) / 2            the time that median is OF
 *   IQR(t)     Q3 − Q1 of the window
 *   rho(t)     lag-1 autocorrelation of x − m inside the window
 *   nEff       n·(1 − rho)/(1 + rho)
 *   sigmaLoc   IQR / sqrt(nEff)                     sd of the sample median
 *   sigmaDelta sqrt(2) · sigmaLoc                   sd of a difference of two
 *   band       max( tolerance, z · sigmaDelta )
 *   settle     c(t*),  t* = min{ t : |m(t') − m(T)| ≤ band  ∀ t' ≥ t }
 *
 * Five parts, each of which is forced rather than chosen:
 *
 * **1. The median, and why it separates the two quantities.** Zero-mean jitter
 * enters the location estimate divided by roughly sqrt(n) — at 5 s of 30 fps
 * (n = 150) the sample median's sd is 1.2533·sd/sqrt(150), a factor of 9.8 —
 * while a drift slower than the window passes through untouched. So the
 * metric's gain on drift-over-jitter is about 10:1 BY CONSTRUCTION, which is
 * the separation the raw-sample metric never had. Median
 * rather than mean because a single occluder rebuild step is a real event in
 * this pipeline and must not drag the location; `weightedScalarMedian` is
 * already the tree's robust-location primitive for exactly this reason.
 *
 * **2. The window is time-stamped at its CENTRE, and that is not a
 * correction.** For any MONOTONE trajectory the median of a window is the
 * signal's value at the window's own time-midpoint — exactly, not
 * approximately, because a monotone map preserves order and therefore
 * preserves which sample is the middle one. The same identity holds exactly for
 * a step: the median of a window straddling a step sits on the old level until
 * the window is half full of the new one, i.e. it switches at the midpoint.
 * Convergence drift is monotone-ish, so time-stamping at the centre makes the
 * reported settle time the TRUE settle time on precisely the class of signal
 * the metric exists to measure. Stamping at the window's right edge — the
 * obvious thing — would report every settle a full half-window late, which on a
 * 2 s acceptance target is larger than the target.
 *
 * **3. The band's noise floor is measured from the session's own signal, and it
 * is distribution-free.** The asymptotic sd of a sample median is
 * `1/(2·f(m)·sqrt(n))` where `f` is the density at the median. Estimating that
 * density from the window's own order statistics — `f = 0.5/IQR`, the standard
 * interquartile density estimate — collapses the whole expression to
 * `sigmaLoc = IQR/sqrt(n)`. No assumed distribution and no coefficient: it
 * returns 1.2533·sd/sqrt(n) for a Gaussian window, a/sqrt(n) for a uniform one
 * on [−a, a], and it widens on its own for a heavy-tailed one. A jittery
 * subject therefore gets a WIDER band rather than an automatic failure, which
 * is the property the absolute 0.5 px band lacked and the reason it could never
 * generalise past one person's noise level.
 *
 * `nEff` is the standard lag-1 autocorrelation correction, `n(1−rho)/(1+rho)`,
 * and it is here because this pipeline's jitter is NOT white: landmark slide is
 * pose-correlated, and correlated samples buy less independence than their
 * count claims. Clamped at rho ≥ 0 so the correction can only ever REDUCE the
 * claimed independence — an anti-correlated window must not be allowed to claim
 * more samples than it has.
 *
 * **4. The band's floor is the channel's own deadband.** Asserting that a
 * channel converged to finer than the deadband it refuses to act within is
 * asserting something the mechanism cannot deliver: `applied.s` literally
 * cannot move by less than `REST_DEADBAND`. That tolerance already exists, it
 * was already derived, and reusing it introduces no number. The composed screen
 * position has no deadband of its own — it tracks the head every frame — so its
 * tolerance is zero and its band is purely the instrument's resolution. That is
 * also why the screen version is REPORTED and the channel versions are
 * ASSERTED: on the screen channel the band and the drift are within a factor of
 * a few, and on the seat channels they differ by an order of magnitude.
 *
 * **5. `z` comes from a stated false-alarm rate, not from taste.** A last-exit
 * time is fragile in one specific way: any band with a non-negligible per-look
 * exceedance probability under a settled null pushes the last exit to the end of
 * the run. So the band is set from a run-wise budget: with `Nw = span/W`
 * independent window positions in the run, `z` solves
 * `(1 − 2(1 − Phi(z)))^Nw = 1 − ALPHA`. A 30 s run at W = 5 s holds Nw = 5
 * complete windows and gives z = 3.09; a 60 s run gives 3.34. The rate is the
 * constant, not the
 * multiplier — which is the same shape as this tree's existing
 * `NOISE_QUANTILE`/`NOISE_GATE` pair, where the 1.2% false-refusal rate is
 * stated and asserted rather than the 3 being defended. And the null is
 * MEASURED, not argued: the harness drives settled streams at three jitter
 * distributions and three amplitudes and requires every one to settle at zero.
 *
 * ------------------------------------------------------------ what it is not
 *
 * A small settle time is necessary and NOT sufficient: a channel that never
 * moves settles instantly. Every gate built on this must pair it with the
 * convergence arm — `final` is published for exactly that comparison, against
 * the same subject run longer.
 *
 * Pure arithmetic, no DOM, no allocation beyond the working arrays. Callers
 * decide what "still" means and pass only the samples they want counted, the
 * same division of labour `stab.js` uses.
 */

import { STAB_WINDOW_S } from './stab.js';

/**
 * The trailing window, seconds — the stab meter's own, deliberately.
 *
 * The band is built out of the window's measured jitter and applied to the same
 * window's median, so the two have to describe the same span of time or they
 * are statements about different things. `STAB_WINDOW_S` is already justified
 * (2.5 periods of the measured 0.5 Hz gaze coupling; short enough to answer "is
 * it still NOW"), and reusing it means the jitter number in a settle readout and
 * the jitter number in `__ar.stab` are the same measurement.
 */
export const SETTLE_WINDOW_S = STAB_WINDOW_S;

/**
 * Fewer samples than this in a window and it is not evaluated — a median and an
 * IQR over a handful of frames are noise about noise. `stab.js`'s own floor,
 * for the same reason and to the same number.
 */
const MIN_WINDOW_SAMPLES = 8;

/**
 * The run-wise budget for a settled channel being reported as never settled.
 * One percent: the rate is what is chosen, and the harness asserts it by
 * driving nulls.
 */
export const SETTLE_ALPHA = 0.01;

/**
 * Inverse standard normal CDF (Acklam's rational approximation, |error| <
 * 1.15e-9 over the whole open interval). Needed for one thing only: turning the
 * run-wise false-alarm budget into the band's multiplier, so that the multiplier
 * is a consequence of the run's length rather than a number somebody liked.
 */
export function normalQuantile(p) {
  if (!(p > 0 && p < 1)) return NaN;
  const a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
    1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
  const b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
    6.680131188771972e+01, -1.328068155288572e+01];
  const c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
    -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
  const d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
    3.754408661907416e+00];
  const pLow = 0.02425;
  if (p < pLow) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
      / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > 1 - pLow) return -normalQuantile(1 - p);
  const q = p - 0.5;
  const r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/**
 * The band multiplier for a run holding `windows` independent window positions,
 * at the stated run-wise false-alarm budget. Stated as its own function because
 * it is the one place a number enters, and it should be readable on its own.
 */
export function settleZ(windows, alpha = SETTLE_ALPHA) {
  const nw = Math.max(1, windows);
  const perLook = 1 - (1 - alpha) ** (1 / nw);
  return normalQuantile(1 - perLook / 2);
}

/**
 * Quantile of a sorted array, linearly interpolated between order statistics.
 *
 * Interpolated rather than the floor-index form this tree uses elsewhere, and
 * the reason is a sign check rather than precision. A floor-index quantile is
 * not equivariant under negation on an even-length sample — the lower of the
 * two middle elements of `x` is not the negative of the lower of the two middle
 * elements of `-x` — so a metric built on it answers a downward drift slightly
 * differently from the identical upward one. The tree's own floor-index uses
 * are all on ODD, fixed-length windows (`FIT_WINDOW` is odd deliberately, and
 * says so); this window is by TIME, so its length varies and cannot be relied
 * on to be odd. The interpolated form satisfies `q_p(-x) = -q_{1-p}(x)`
 * exactly, which makes the median negation-equivariant and the IQR
 * negation-invariant — and the harness asserts the consequence: a drift and its
 * mirror image settle at bit-identical times.
 *
 * Exported because `agreement.js` builds the seat's confidence out of the same
 * IQR-over-root-n construction this file uses for its band, and the metric that
 * GRADES the seat's convergence and the estimator that DRIVES it must not hold
 * two opinions about what a quantile is.
 */
export const interpolatedQuantile = (sorted, q) => {
  const h = q * (sorted.length - 1);
  const lo = Math.floor(h);
  const hi = Math.ceil(h);
  return lo === hi ? sorted[lo] : sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
};

/**
 * Measures one channel's settle.
 *
 * `samples` is `[{ t, x }]` in any consistent units — seconds for `t`, the
 * channel's own unit for `x`. Samples the caller considers unusable (head
 * moving, face lost) should simply be left out; the window is by time, so gaps
 * are handled by construction.
 *
 * `tolerance` is the channel's own output deadband in the same unit as `x`, or
 * 0 for a channel that has none.
 */
export function measureSettle(samples, { tolerance = 0, alpha = SETTLE_ALPHA } = {}) {
  const blank = (reason) => ({
    settleS: null,
    settleMs: null,
    reason,
    band: null,
    sigmaLoc: null,
    sigmaDelta: null,
    z: null,
    driftAmp: null,
    driftZ: null,
    jitterRms: null,
    final: null,
    windows: 0,
    n: samples ? samples.length : 0,
  });
  if (!samples || samples.length < MIN_WINDOW_SAMPLES) return blank('too few samples');
  const t0 = samples[0].t;
  const span = samples[samples.length - 1].t - t0;
  // Three windows is the floor for a settle to be a statement at all: one for
  // the settled tail the band is measured from, and at least two more for the
  // trajectory to have somewhere to have come from. A 60-frame diag still (2 s)
  // is deliberately refused here rather than answered badly.
  if (span < 3 * SETTLE_WINDOW_S) return blank(`run spans ${span.toFixed(2)} s, under 3 windows`);

  const trail = [];
  const work = [];
  const centres = [];
  const medians = [];
  let tail = null;
  let head = 0;
  for (let k = 0; k < samples.length; k++) {
    const { t, x } = samples[k];
    if (!Number.isFinite(x)) continue;
    trail.push({ t, x });
    while (trail.length - head > 1 && trail[head].t <= t - SETTLE_WINDOW_S) head++;
    const n = trail.length - head;
    if (n < MIN_WINDOW_SAMPLES) continue;

    work.length = 0;
    for (let i = head; i < trail.length; i++) work.push(trail[i].x);
    work.sort((p, q) => p - q);
    const median = interpolatedQuantile(work, 0.5);
    const iqr = interpolatedQuantile(work, 0.75) - interpolatedQuantile(work, 0.25);

    // Lag-1 autocorrelation of the window's own deviations, and the RMS about
    // the median in the same pass. Clamped non-negative: the correction may
    // only ever reduce the independence a window claims.
    let s2 = 0;
    let s1 = 0;
    let rms = 0;
    let prev = null;
    for (let i = head; i < trail.length; i++) {
      const d = trail[i].x - median;
      s2 += d * d;
      rms += d * d;
      if (prev !== null) s1 += d * prev;
      prev = d;
    }
    const rho = s2 > 0 ? Math.min(Math.max(s1 / s2, 0), 0.99) : 0;
    const nEff = Math.max(2, (n * (1 - rho)) / (1 + rho));

    centres.push((trail[head].t + t) / 2);
    medians.push(median);
    tail = {
      sigmaLoc: iqr / Math.sqrt(nEff),
      jitterRms: Math.sqrt(rms / n),
      n,
      nEff,
      rho,
    };
  }
  if (medians.length < 2 || !tail) return blank('no evaluable window');

  const windows = Math.max(1, Math.floor(span / SETTLE_WINDOW_S));
  const z = settleZ(windows, alpha);
  const sigmaDelta = Math.SQRT2 * tail.sigmaLoc;
  const band = Math.max(tolerance, z * sigmaDelta);
  const final = medians[medians.length - 1];

  let lastViolation = -1;
  for (let i = 0; i < medians.length; i++) {
    if (Math.abs(medians[i] - final) > band) lastViolation = i;
  }
  const settleAt = lastViolation + 1;
  const settleS = settleAt < medians.length
    ? Math.max(centres[settleAt] - t0, 0)
    : null;

  let lo = medians[0];
  let hi = medians[0];
  for (const m of medians) { if (m < lo) lo = m; if (m > hi) hi = m; }

  return {
    /** Seconds from the first sample to the location's last exit from the band. */
    settleS,
    settleMs: settleS === null ? null : settleS * 1000,
    reason: settleS === null ? 'never re-entered the band' : null,
    band,
    /** The trailing median's own sd on the settled tail. */
    sigmaLoc: tail.sigmaLoc,
    sigmaDelta,
    z,
    /** Total excursion of the LOCATION over the run — the drift's amplitude. */
    driftAmp: hi - lo,
    /** That amplitude in units of what the instrument can resolve. */
    driftZ: sigmaDelta > 0 ? (hi - lo) / sigmaDelta : Infinity,
    /** Per-frame scatter about the location on the settled tail — the OTHER
     * quantity, reported beside the drift so the two are never confused. */
    jitterRms: tail.jitterRms,
    /** The converged location. Pair with a longer run: settling fast is only
     * half the claim, and this is the half a never-moving channel fails. */
    final,
    windows,
    tailN: tail.n,
    tailNEff: tail.nEff,
    tailRho: tail.rho,
    n: samples.length,
  };
}

/**
 * The metric this one replaces, kept runnable so the comparison is a
 * measurement rather than an argument: a last-exit time on the RAW sample
 * against an absolute band.
 */
export function measureSettleRaw(samples, bandAbs) {
  if (!samples || samples.length < 2) return null;
  const t0 = samples[0].t;
  const tEnd = samples[samples.length - 1].t;
  const tail = samples.filter((s) => s.t >= tEnd - 2 && Number.isFinite(s.x))
    .map((s) => s.x).sort((a, b) => a - b);
  if (!tail.length) return null;
  const pInf = interpolatedQuantile(tail, 0.5);
  let lastViolation = -1;
  for (let i = 0; i < samples.length; i++) {
    if (Math.abs(samples[i].x - pInf) > bandAbs) lastViolation = i;
  }
  return lastViolation + 1 < samples.length
    ? samples[lastViolation + 1].t - t0
    : null;
}
