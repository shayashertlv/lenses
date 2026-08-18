/**
 * Confidence from measured agreement.
 *
 * An estimator should be confident when its observations concur and cautious
 * when they scatter. That sentence is the whole module, and it replaces a
 * confidence that counted frames instead of reading them.
 *
 * ------------------------------------------------------------ what it replaces
 *
 * The seat's resting height used to be applied scaled by
 *
 *     conf = clamp(noseMeanW / 50, 0, 1)
 *
 * — the person model's accumulated weight against a fixed count of "enough"
 * frames. That is a clock, not a measurement. Every wearer pays the same
 * settling time whatever their data is worth, and the two failures it produces
 * are opposite and were both measured on the 15-subject synthetic set:
 *
 *   - **The wearer whose seat is obvious still waits.** S00's solve answers
 *     −1.50 mm on frame one and −1.375 mm nineteen seconds later; the whole
 *     journey is 0.125 mm, inside the channel's own 0.3 mm deadband. The
 *     evidence was complete before the second frame and the ramp spent 2.4 s
 *     pretending otherwise, which is 2.9 s of measured settle for a number that
 *     never moved.
 *   - **The wearer whose seat is genuinely undecided is believed anyway.**
 *     S08's solves read 0, 0, 0, 0, 0, −0.25, 0 … −1.25, −1, −0.625, −1.5, …
 *     −0.25, 0: the solve disagrees with itself by a millimetre and a half over
 *     the session. At `conf = 1` the applied height chased every one of those
 *     answers — 15.2 s of measured settle, and the frame visibly walking up and
 *     down the wedge while it did.
 *
 * Both are the same defect seen from two sides: the ramp never asked whether
 * the observations agreed. This module asks, and it is the *only* input to the
 * answer.
 *
 * ------------------------------------------------------------------- the law
 *
 * Observations `x_k` of one face-space constant arrive with weights `w_k` into
 * a bounded window (the anchors' grammar, verbatim — see `foldAgreement`):
 *
 *     value    weighted median of the window                 the location
 *     mad      median |x_k − value|                          the scatter
 *     scale    max(2·mad, resolution)                        floored at the
 *                                                            observer's own grid
 *     rho      lag-1 autocorrelation of (x_k − value)        how much of the
 *                                                            scatter is common-mode
 *     nEff     n·(1 − rho)/(1 + rho)                          independent looks
 *     sigma    scale / sqrt(nEff)                            sd of a sample median
 *     conf     max(0, 1 − (sigma/value)²)                     the posterior weight
 *
 * Each line is forced rather than chosen.
 *
 * **1. The median is the location, and `sigma` is that median's own sd.** The
 * asymptotic standard deviation of a sample median is `1/(2·f(m)·sqrt(n))` with
 * `f` the density at the median; estimating the density from the window's own
 * order statistics — half the mass lies within `2·MAD` of the median, so
 * `f = 0.5/(2·MAD)` — collapses the expression to `2·MAD/sqrt(n)`. No assumed distribution and no
 * coefficient: it returns 1.2533·sd/√n for a Gaussian window, a/√n for a
 * uniform one on [−a, a], and it widens on its own for a heavy-tailed one. This
 * is `settle.js`'s construction with one deliberate difference: that file
 * measures the width as an interquartile range and this one as twice a median
 * absolute deviation. They estimate the SAME width for a symmetric window — the
 * identity is `IQR = 2·MAD` — and differ only in breakdown point, 25% against
 * 50%. The seat needs the 50%, and the harness measures the difference rather
 * than asserting it.
 *
 * **2. `nEff` is the correction the arithmetic cannot skip.** Dividing the
 * scatter by the raw sample count asserts that the samples are independent, and
 * these are not: what makes the seat's readings differ frame to frame is mostly
 * the wearer's own slow postural wander, which varies on a ~1 s timescale. At a
 * 2 Hz solve cadence consecutive answers are correlated; at 30 Hz they are
 * correlated by a factor of thirty. Counting them as independent inflates the
 * claimed precision by `sqrt(n)` for nothing. So `rho` is measured — one lag-1
 * autocorrelation over the window's own deviations, no constant, no assumed
 * timescale — and `nEff = n(1−rho)/(1+rho)` is the standard effective sample
 * size. Clamped `rho >= 0`: the correction may only ever REDUCE the
 * independence a window claims, because an anti-correlated window must not be
 * allowed to claim more samples than it has.
 *
 * **3. The scale is floored at the observer's own resolution.** An observation
 * quantised to a grid cannot disagree with itself by less than the grid, so a
 * window of identical answers reports a scatter of 0 and would claim infinite
 * precision from two lucky samples. `resolution` is the caller's own stated
 * precision — for the seat, `S_REFINE`, the bisection step the solve already
 * declares as "the seat's own precision" — so nothing new is introduced and the
 * floor is a fact about the instrument rather than a safety margin. Averaging
 * may still beat the quantum (`sigma` falls as `1/sqrt(nEff)`), which is correct:
 * a dithered quantised signal genuinely resolves below its step, and `rho` is
 * exactly what stops a *correlated* dither from claiming that.
 *
 * **4. `conf` is a shrinkage between the prior and the estimate, and it has no
 * free parameter.** The consumer applies `conf · value` against a prior of zero
 * — "hold what you had". With a Gaussian prior `N(0, tau²)` the posterior mean
 * is `value · tau²/(tau² + sigma²)`, and `tau` is unknown, so it is estimated
 * from the same data: `tau² = max(0, value² − sigma²)` — the positive-part
 * empirical-Bayes (James–Stein) estimate for a single coordinate. Substituting
 * collapses the shrinkage to
 *
 *     conf = max(0, 1 − sigma²/value²)
 *
 * which is the estimate's own signal-to-noise ratio and nothing else. It is 0
 * when the estimate cannot be told from the prior, and it approaches 1 as the
 * estimate stands clear of its own uncertainty. Nobody chooses a number.
 *
 * **5. Fewer than two observations is not a small confidence, it is none.**
 * Scatter is undefined on one sample, so `conf = 0` until a second observation
 * exists — which is also what keeps a cold session holding its prior BY
 * CONSTRUCTION rather than by arithmetic that happens to be small.
 *
 * ------------------------------------------------------- what the law implies
 *
 * The consumer's applied error is `(1 − conf)·|value| = sigma²/|value|`, so the
 * estimate is inside a tolerance `D` once
 *
 *     nEff  >=  scale² / (D · |value|)
 *
 * and with observations arriving at rate `r` and effective rate
 * `r_eff = r(1 − rho)/(1 + rho)` the time to get there is
 *
 *     t_agree  =  max( 2/r ,  scale² / (D · |value| · r_eff) )
 *
 * — a **stated law with a measured argument**, not a fixed wait. The first term
 * is structural (no scatter exists before the second observation); the second is
 * quadratic in the measured scatter, and whether that quadratic matters is a
 * question with a number rather than an opinion: on the 15-subject set at the
 * seat's own 0.25 mm resolution and a 0.3 mm deadband it is under one
 * observation for every subject whose seat moves at all, at every noise rung the
 * matrix runs, so the structural floor is what is actually paid. The harness
 * asserts both halves rather than trusting either.
 *
 * Pure arithmetic. No DOM, no time source, no state beyond the window the
 * caller owns — which is what lets the caller put it where a per-session reset
 * can reach it.
 */

/**
 * The anchors' weighted median (see `medianAnchors`), for one scalar: sort by
 * value, walk the cumulative weight, take the first value at or past half the
 * total, and mean the straddling pair on an exact tie — the generalisation
 * that reproduces the plain median bit for bit under equal weights. The seat's
 * carried raw-law estimate stands on it (2026-08-17) and so does every window
 * here; same law, same tie tolerance, so no two windows in this pipeline can
 * disagree about what "median" means.
 *
 * The tie-straddling average is not a nicety: it is what makes the median
 * equivariant under negation (`m(−x) = −m(x)`), which is what makes a downward
 * convergence answer exactly like the identical upward one. The harness asserts
 * that consequence on a mirrored stream rather than taking this paragraph's
 * word for it.
 */
export function weightedScalarMedian(samples) {
  if (!samples.length) return null;
  const order = samples.map((_, i) => i).sort((a, b) => samples[a].v - samples[b].v);
  let total = 0;
  for (const s of samples) total += s.w;
  if (!(total > 0)) {
    const mid = order.length >> 1;
    return order.length % 2
      ? samples[order[mid]].v
      : (samples[order[mid - 1]].v + samples[order[mid]].v) / 2;
  }
  const half = total / 2;
  const tie = total * 1e-9;
  let cum = 0;
  for (let k = 0; k < order.length; k++) {
    cum += samples[order[k]].w;
    if (cum >= half - tie) {
      return cum <= half + tie && k + 1 < order.length
        ? (samples[order[k]].v + samples[order[k + 1]].v) / 2
        : samples[order[k]].v;
    }
  }
  return samples[order[order.length - 1]].v;
}

/**
 * A fresh window. `capacity` is the caller's own window length — this module
 * introduces none — and `resolution` the caller's own observation grid, in the
 * same unit as the values (0 for a continuous observer).
 *
 * The returned object is plain data with no module-level anything, so a caller
 * can hang it on per-session state and a reset that clears that state has
 * genuinely cleared this estimator. Module scope is the one place a state-keyed
 * reset cannot reach, which is why nothing here lives there.
 */
export function createAgreement(capacity, resolution = 0) {
  return {
    capacity,
    resolution,
    /** `{ v, w }` in ARRIVAL order — `rho` reads it as a time series. */
    samples: [],
    /** The location: the window's weighted median. Null until a first sample. */
    value: null,
    /** Total admitted weight, the same readout `needEst` has always published. */
    weight: 0,
    /** Q3 − Q1 of the window, floored at `resolution`. Null under two samples. */
    scale: null,
    /** The location's own sd — `scale/sqrt(nEff)`. Null under two samples. */
    sigma: null,
    /** Measured lag-1 autocorrelation of the deviations, clamped [0, 0.99]. */
    rho: 0,
    n: 0,
    nEff: 0,
    /** The shrinkage: `max(0, 1 − (sigma/value)²)`. Zero under two samples. */
    conf: 0,
  };
}

/**
 * Recomputes every derived field from the window as it stands. Separate from
 * `foldAgreement` so a caller can re-read an estimator it did not just feed
 * (and so the harness can drive the arithmetic directly).
 */
export function measureAgreement(est) {
  const n = est.samples.length;
  est.n = n;
  est.value = weightedScalarMedian(est.samples);
  let total = 0;
  for (const s of est.samples) total += s.w;
  est.weight = total;
  if (n < 2) {
    // Scatter is undefined on one sample. Not a small confidence — none.
    est.scale = null;
    est.sigma = null;
    est.rho = 0;
    est.nEff = n;
    est.conf = 0;
    return est;
  }

  // The scatter: twice the median absolute deviation, through the same weighted
  // median primitive the location uses. `2*MAD` and the interquartile range
  // estimate the SAME width for a symmetric window - both are "the width holding
  // half the mass about the middle", which is the whole content of the
  // interquartile density estimate - so this is `settle.js`'s construction with
  // its breakdown point doubled, 25% to 50%. That doubling is the reason, and it
  // was measured rather than preferred: a cold session's first solves are
  // one-sided contamination by construction, because they are answers about a
  // surface the pipeline had not finished measuring. On the wearer's own
  // recording the seat's answers step from 0 to -11.3 mm at 3.6 s; an
  // interquartile scale went on calling that step scatter until the early
  // answers fell under a QUARTER of the window, which cost ten seconds of
  // confidence for nothing. A median absolute deviation survives to half.
  const devs = est.samples.map((q) => ({ v: Math.abs(q.v - est.value), w: q.w }));
  const scatter = 2 * weightedScalarMedian(devs);

  // The lag-1 autocorrelation of the window's own deviations, read in arrival
  // order. One pass, no constant, no assumed timescale: whatever correlation
  // this session's observations actually carry is what the effective count is
  // discounted by. Clamped non-negative — the correction may only ever reduce
  // the independence the window claims.
  let s2 = 0;
  let s1 = 0;
  let prev = null;
  for (const s of est.samples) {
    const d = s.v - est.value;
    s2 += d * d;
    if (prev !== null) s1 += d * prev;
    prev = d;
  }
  const rho = s2 > 0 ? Math.min(Math.max(s1 / s2, 0), 0.99) : 0;
  const nEff = Math.max(1, (n * (1 - rho)) / (1 + rho));

  const scale = Math.max(scatter, est.resolution);
  const sigma = scale / Math.sqrt(nEff);
  est.rho = rho;
  est.nEff = nEff;
  est.scale = scale;
  est.sigma = sigma;
  // The positive-part empirical-Bayes shrinkage. An estimate that cannot be
  // told from the prior (value inside its own sigma, or exactly zero) earns no
  // confidence at all, and the guard is written as a comparison rather than a
  // division so a zero location can never produce a NaN.
  est.conf = Math.abs(est.value) > sigma
    ? Math.min(1 - (sigma * sigma) / (est.value * est.value), 1)
    : 0;
  return est;
}

/**
 * Admits one observation and re-measures.
 *
 * Eviction is the anchors' rule verbatim — lowest weight goes, the OLDEST among
 * equals — which is what makes a bounded window floodproof: a full window of
 * full-trust samples evicts every lower-trust arrival, so no dwell at any
 * partial trust, however long, replaces the trustworthy mass, while genuine
 * change at full trust turns the window over in a window's worth of
 * observations. `splice` preserves the order of everything it does not remove,
 * so the array stays an arrival-ordered time series and `rho` remains readable.
 */
export function foldAgreement(est, v, w) {
  est.samples.push({ v, w });
  if (est.samples.length > est.capacity) {
    let evict = 0;
    for (let i = 1; i < est.samples.length; i++) {
      if (est.samples[i].w < est.samples[evict].w) evict = i;
    }
    est.samples.splice(evict, 1);
  }
  return measureAgreement(est);
}

/**
 * The settle law this estimator implies, in seconds, for a consumer whose
 * output tolerance is `deadband` and whose observations arrive at `rate` per
 * second. Exported because a law that is asserted has to be readable in one
 * place, and because the harness gates on it across every subject and every
 * noise rung rather than on a number somebody liked.
 *
 * `t_agree = max(2/rate, scale²/(deadband·|value|·rate_eff))` — the structural
 * floor (no scatter before the second observation) against the quadratic cost
 * of the measured scatter. Returns null for an estimator with no location yet
 * or a location of zero, where there is nothing to converge TO.
 */
export function agreementSettleS(est, deadband, rate) {
  if (est.value === null || !(Math.abs(est.value) > 0) || !(rate > 0)) return null;
  const floor = 2 / rate;
  if (est.scale === null) return floor;
  const rateEff = rate * ((1 - est.rho) / (1 + est.rho));
  const need = (est.scale * est.scale) / (deadband * Math.abs(est.value));
  return Math.max(floor, rateEff > 0 ? need / rateEff : Infinity);
}
