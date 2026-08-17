/**
 * Filters, and the pose predictor built on them.
 *
 * Per-frame face tracking is noisy: the pose wobbles a little even when the head
 * is perfectly still, and glasses welded to that pose shimmer. A plain low-pass
 * fixes the shimmer but adds lag, so the frame swims behind fast head turns.
 *
 * One Euro adapts instead: the cutoff frequency rises with the speed of the
 * signal, so it filters hard when the head is still and barely at all when the
 * head is moving. Two knobs — `minCutoff` sets how still "still" looks, `beta`
 * sets how quickly it gets out of the way. Casiez, Roussel & Vogel, CHI 2012.
 *
 * That law is the whole of this module: `Predicted` derives its `alpha` from it
 * every frame. It used to be exported as a filter class in its own right for the
 * facial anchors, which describe proportions that are not changing — but a
 * measurement that does not have to be *timely* does not want a time constant
 * either, and `anchors.js` now carries a weighted median instead.
 *
 * The head pose gets the same filter, tuned very differently, and the tuning is the
 * whole story behind glasses that trail the face. A first-order low-pass at cutoff
 * `fc` lags a moving signal by its own time constant, `tau = 1 / (2 pi fc)` — 133 ms
 * at 1.2 Hz, which at a gentle 25 cm/s is over three centimetres of slip. One Euro's
 * answer is to raise `fc` with the speed of the signal, and that answer works: the
 * arithmetic is `beta >= (wanted_fc - minCutoff) / speed`, and at `beta = 0.5` the
 * cutoff reaches 13 Hz on that same movement, worth 3 mm.
 *
 * Two things have to be true for it to work, and both were false here:
 *
 *  1. **The speed signal must come from the measurement.** It was being read off the
 *     filter's own trend estimate, which converges over about a second — so through
 *     every head movement shorter than that it read zero, `beta` had nothing to
 *     multiply, and an adaptive filter behaved as a fixed 0.9 Hz pole. See `observe`.
 *  2. **`beta` must be sized against the speeds a head actually reaches.** It was
 *     0.06, an order of magnitude short.
 *
 * Measured on a 0.5 Hz look-around peaking at 25 cm/s, the two together were worth
 * 29.5 mm of lag; fixed, 3.8 mm.
 *
 * `Predicted` below can also carry a velocity and extrapolate, and that capability is
 * kept and tested — but it is **off** in the shipped settings. A velocity term is
 * free timeliness on a constant-rate ramp and overshoot at every reversal, and a head
 * reverses two or three times a second. On the same sweep it costs three times what
 * it saves. See `DEFAULT_SMOOTHING`.
 *
 * One refinement on top of One Euro proper: the speed signal that opens the cutoff
 * is never zero on a real detector, because detector noise reads as speed. That DC
 * is measured and subtracted before `beta` sees it — see `NoiseFloor` — so "still"
 * genuinely reads as still at the poses where the detector is noisiest, which is
 * exactly where the frame used to shimmer.
 */

import * as THREE from 'three';

const alphaFor = (cutoff, dt) => {
  const tau = 1 / (2 * Math.PI * cutoff);
  return 1 / (1 + tau / dt);
};

/**
 * 0 below `edge0`, 1 above `edge1`, and smooth in between — zero derivative at both
 * ends, which is what keeps a correction that switches on from being visible as a
 * kink. A plain linear ramp is discontinuous in velocity at each edge and reads as
 * a small tick every time the bound engages.
 */
const smoothstep = (edge0, edge1, x) => {
  if (!(edge1 > edge0)) return x >= edge1 ? 1 : 0;
  const t = Math.min(Math.max((x - edge0) / (edge1 - edge0), 0), 1);
  return t * t * (3 - 2 * t);
};

class LowPass {
  constructor() {
    this.value = null;
  }

  filter(x, alpha) {
    this.value = this.value === null ? x : alpha * x + (1 - alpha) * this.value;
    return this.value;
  }

  reset() {
    this.value = null;
  }
}

// ------------------------------------------------------------- noise floor

/**
 * How many rate samples the floor estimator remembers: ~1.07 s at 30 fps. Long
 * enough that a dropout burst or one fast gesture cannot own the estimate, short
 * enough that a lighting change re-calibrates within a couple of seconds.
 */
const NOISE_WINDOW = 32;

/**
 * The floor is the 25th percentile of the window. A low quantile, because the
 * estimate must describe the *quietest* the signal gets — the noise DC — and the
 * low tail of the smoothed rate is dominated by still moments even when the window
 * also contains movement.
 */
const NOISE_QUANTILE = 0.25;

/**
 * A sample joins the window only when it reads below `NOISE_GATE` times the current
 * floor. This is the freeze rule, and it is what keeps the floor a *noise*
 * measurement: real motion runs 5–20x above an honestly calibrated floor, so it is
 * refused outright and the calibration simply waits, frozen, for the head to be
 * still again. Without the gate, a half-second of movement fills a quarter of the
 * window and the floor starts chasing real speed — which is the one failure this
 * class must never have, because everything it subtracts comes off the cutoff.
 * 3x sits above the spread of an honest noise magnitude (a ~Maxwell distribution
 * puts nearly everything under 3x its 25th percentile) and below slow deliberate
 * motion at ~5x.
 */
const NOISE_GATE = 3;

/**
 * How fast the floor may rise, as a fraction of the hard cap per second — an
 * absolute rate, deliberately, because a relative one is degenerate at the cold
 * start (10% of zero is zero forever). Rising is the dangerous direction: a floor
 * that climbs is a floor absorbing something, so it climbs at no more than a tenth
 * of the worst credible noise level per second and any real-speed contamination
 * that slips past the gate is bounded to a sliver. Falling is the safe direction —
 * quieter noise means less to subtract — so falls are immediate.
 *
 * `NOISE_RISE * cap` also seeds the admission gate while the window is first
 * filling, when the floor is still zero and `NOISE_GATE x 0` would refuse
 * everything: until the first calibration exists, "plausibly noise" can only mean
 * "small on the scale of the cap". A filter born mid-movement therefore admits
 * nothing until the movement pauses — the conservative failure, no floor at all —
 * rather than calibrating motion as noise.
 */
const NOISE_RISE = 0.10;

/**
 * The noise DC of the pose's activity signal, so it can be subtracted (C2).
 *
 * Detector noise on a perfectly still head reads as speed — the rectified magnitude
 * of a noisy rate has a positive mean, and at hard poses it is several centimetres
 * per second. Fed to `beta`, that DC holds the adaptive cutoff partly open on a
 * still head, and per-frame noise walks through the very filter that exists to stop
 * it (the diagnosed jitter at difficult angles, cause 1). This class measures that
 * DC live — the low quantile of the recent post-`dCutoff` smoothed rate — and
 * `condition` returns the activity with the floor removed.
 *
 * What it must never do is eat real motion, and three mechanisms bound that from
 * three directions, per the spec's G10 hardening: the admission gate freezes
 * calibration during movement, the rise cap bounds what any contamination can do,
 * and the hard `cap` bounds the floor absolutely — sized above any honest webcam's
 * noise DC (measured 1–5 cm/s and, once the rotation rate is signed, 0.02–0.09
 * rad/s) and well below real head speeds (10–25 cm/s, 0.5–3 rad/s). And the
 * subtraction only ever feeds `beta`: the level path, `minCutoff`, and the lag
 * ceilings never see the floor, so even a floor gone wrong cannot stop the filter
 * tracking — it can only slow the cutoff's opening.
 *
 * A `cap` of zero disables the whole mechanism arithmetically: nothing is ever
 * admitted, the floor stays zero, and `condition` returns its input — which is what
 * the harness's A/B controls rely on.
 */
export class NoiseFloor {
  constructor(cap = 0) {
    this.cap = cap;
    this.ring = new Float64Array(NOISE_WINDOW);
    this.sorted = new Float64Array(NOISE_WINDOW);
    this.fill = 0;
    this.index = 0;
    /** The current noise-DC estimate, in the signal's own units per second. */
    this.floor = 0;
  }

  /** The capped window quantile. Insertion sort: 32 elements, no allocation. */
  estimate() {
    const n = this.fill;
    if (n === 0) return 0;
    const sorted = this.sorted;
    for (let i = 0; i < n; i++) {
      const value = this.ring[i];
      let j = i - 1;
      for (; j >= 0 && sorted[j] > value; j--) sorted[j + 1] = sorted[j];
      sorted[j + 1] = value;
    }
    return Math.min(sorted[Math.floor(NOISE_QUANTILE * (n - 1))], this.cap);
  }

  /**
   * Folds in one smoothed-rate sample and returns the activity with the floor
   * subtracted. Call once per measurement, with the same `dt` the filter used.
   */
  condition(rate, dt) {
    if (dt > 0) {
      // Freeze rule, with the fill-phase seed — see `NOISE_GATE` / `NOISE_RISE`.
      const reference = this.fill < NOISE_WINDOW
        ? Math.max(this.floor, NOISE_RISE * this.cap)
        : this.floor;
      if (rate < NOISE_GATE * reference) {
        this.ring[this.index] = rate;
        this.index = (this.index + 1) % NOISE_WINDOW;
        if (this.fill < NOISE_WINDOW) this.fill += 1;
      }
      const estimate = this.estimate();
      if (estimate < this.floor) this.floor = estimate; // falls free
      else this.floor = Math.min(estimate, this.floor + NOISE_RISE * this.cap * dt);
    }
    return Math.max(0, rate - this.floor);
  }

  /**
   * Back to the constructed state, ring included.
   *
   * The ring is zeroed even though `estimate` only ever reads `ring[0..fill)`,
   * so the stale samples are already unreachable. "Unreachable" is a claim
   * about today's read pattern; "absent" is a claim about the object. The
   * isolation check compares a reset estimator field by field against a
   * constructed one, and a reset that leaves the previous wearer's samples
   * sitting in the buffer cannot pass that comparison — nor should it have to
   * argue its way past it.
   */
  reset() {
    this.ring.fill(0);
    this.sorted.fill(0);
    this.fill = 0;
    this.index = 0;
    this.floor = 0;
  }
}

/*
 * `OneEuro` and `OneEuroVector` — plain One Euro over a scalar and over a
 * vector's components — lived here, and both outlived their consumers. The
 * anchors moved to a weighted median (see `medianAnchors`: a filter's time
 * constant was never what that measurement wanted), and `Predicted` below
 * derives its own alpha from the One Euro law directly rather than wrapping
 * the class. `InnovationFilter` (design C3 / graft G11 — the bridge pin's
 * hybrid-activity innovation filter) went the same way at anchoring-v3, when
 * the 2026-08-17 telemetry attribution run measured the filtered innovation's
 * entire screen contribution at 0.03 px and the pin's innovation term was
 * deleted whole. The law is still the one this module runs on and is derived
 * at the top of the file; only the unused wrappers are gone. Git history and
 * the spec's landing notes carry the designs.
 */

// ---------------------------------------------------------------- prediction

/**
 * A filter that carries a velocity, so it does not have to lag.
 *
 * Double exponential smoothing — Holt's method, and in tracking specifically
 * LaViola's DESP (Graphics Interface 2003, where it matched a Kalman filter on
 * head and hand tracking for a fraction of the cost). One state more than a plain
 * low-pass, and that state changes the character completely:
 *
 *     level    <- alpha * measured + (1 - alpha) * (level + velocity * dt)
 *     velocity <- velocity + gamma * ((level - previousLevel) / dt - velocity)
 *
 * The prediction inside the level update is what removes the lag. Feed it a signal
 * moving at a constant rate and `level = measured`, `velocity = rate` is a fixed
 * point — **no steady-state lag at all**, where a single exponential lags by `tau`
 * forever. What is left is the *transient* after an acceleration, which decays, and
 * that is a far better failure mode than a constant offset: a head sweeping across
 * frame is tracked, not trailed.
 *
 * `alpha` still comes from One Euro, so a still head is still filtered hard.
 *
 * `gamma` is not an independent knob, and this is the part that is easy to get
 * wrong. The recurrence has characteristic polynomial
 * `z^2 - (2 - alpha - alpha*gamma) z + (1 - alpha)`, whose roots go complex —
 * ringing — as soon as `gamma` exceeds `(2 - alpha - 2*sqrt(1 - alpha)) / alpha`.
 * Expanded for small alpha that bound is `alpha / 4`, and it stays above `alpha / 4`
 * for every alpha in (0, 1). So the trend gain is expressed as a *fraction of*
 * `alpha`, and any `trendGain <= 0.25` is guaranteed not to ring. Tuned as an
 * independent cutoff it rings for about a second after every stop, which reads as
 * the glasses bouncing on the nose.
 */
export class Predicted {
  constructor({ minCutoff = 1.0, beta = 0.0, trendGain = 0.25, dCutoff = 1.0 } = {}) {
    Object.assign(this, { minCutoff, beta, trendGain, dCutoff });
    this.level = null;
    this.velocity = 0;
    // The measured rate of change, and the value it was last differenced from. This
    // is what `beta` reads — see `observe`. Distinct from `velocity`, which is the
    // *estimated* trend used for prediction: that one is deliberately slow to move,
    // and reading it here is what made the filter lag.
    this.rate = new LowPass();
    this.previousValue = null;
    // Carried between `updateLevel` and `updateTrend`. `step` of 0 means there is no
    // half-finished update outstanding, which is what makes `updateTrend` safe to
    // call twice or not at all.
    this.step = 0;
    this.alpha = 0;
    this.previousLevel = 0;
  }

  /**
   * Folds in one measurement.
   *
   * `dt` is the interval since the *previous measurement*, not since the last
   * render — the two are different once detection runs on the camera's clock, and
   * using the render interval would tell the filter the head moved that far in half
   * the time it actually took.
   *
   * `activity` is the speed the adaptive cutoff reads, and **it must come from the
   * measurement, not from this filter's own trend state.** That distinction was a
   * one-line default here and it was the entire reason the glasses trailed the face.
   *
   * The trend converges at gain `trendGain * alpha` — 0.22 x 0.16 at 30 fps, about
   * 0.035 per frame, a time constant near a full second. Read as `activity`, it is
   * therefore still nearly zero throughout any head movement shorter than that, which
   * is *every* head movement: `beta` has nothing to read, the cutoff never opens, and
   * an adaptive filter quietly behaves as a fixed 0.9 Hz low-pass whose lag is its own
   * time constant — 177 ms, or three and a half centimetres at a gentle 20 cm/s. The
   * head moves, the frame follows a fifth of a second later, and every knob meant to
   * prevent that is reading a number that cannot rise in time to matter.
   *
   * `observe` below computes the honest thing instead: the *measured* rate of change,
   * lightly smoothed at `dCutoff`, which is what One Euro specifies and what makes
   * `beta` mean anything. It responds within a frame or two rather than a second.
   */
  update(value, dt) {
    this.updateLevel(value, dt, this.observe(value, dt));
    this.updateTrend();
    return this.level;
  }

  /**
   * Folds in the new measurement's own rate of change and returns it, smoothed.
   *
   * Must run before `updateLevel`, whose cutoff depends on it. Split out because a
   * vector has to collect every component's rate before any component can be
   * filtered — they share one speed, or a diagonal movement comes out responsive
   * along one axis and sluggish along another and the head appears to slide.
   */
  observe(value, dt) {
    if (this.previousValue === null || !(dt > 0)) {
      this.previousValue = value;
      return Math.abs(this.rate.value ?? 0);
    }
    const rate = (value - this.previousValue) / dt;
    this.previousValue = value;
    return Math.abs(this.rate.filter(rate, alphaFor(this.dCutoff, dt)));
  }

  /**
   * The first half: move the level, remembering what it moved from.
   *
   * Split from the trend update so a caller can correct the level *in between* the
   * two — which is what the lag bound does, and the order is not cosmetic. Bounding
   * after the trend update means the next frame differences against a level that was
   * moved behind the estimator's back, and reads the correction as the head having
   * moved *less* than it did: the velocity estimate collapses, and the filter that
   * was meant to gain a lag ceiling silently loses the trend term that was keeping it
   * timely in the first place. Measured, that turned a 25 cm/s ramp the estimator
   * used to track exactly into a 10 cm/s one it trailed.
   *
   * Corrected in between, the same yank is evidence, and good evidence: the level
   * moved further this frame than predicted, so the head is going faster than
   * believed, and the trend update — which now sees the corrected level — picks
   * exactly that up.
   */
  updateLevel(value, dt, activity = Math.abs(this.velocity)) {
    this.step = 0;
    if (this.level === null) {
      this.level = value;
      this.velocity = 0;
      return this.level;
    }
    // A zero or negative step divides into the trend. Hold rather than propagate a
    // NaN — one of those reaches the head matrix and the frame leaves the face.
    if (!(dt > 0)) return this.level;

    const alpha = alphaFor(this.minCutoff + this.beta * activity, dt);
    this.step = dt;
    this.alpha = alpha;
    this.previousLevel = this.level;

    const predicted = this.level + this.velocity * dt;
    this.level = predicted + alpha * (value - predicted);
    return this.level;
  }

  /** The second half: read the trend off whatever the level ended up at. */
  updateTrend() {
    if (!(this.step > 0)) return;
    this.velocity += (this.trendGain * this.alpha)
      * (((this.level - this.previousLevel) / this.step) - this.velocity);
    this.step = 0;
  }

  /** Where the signal is expected to be `lead` seconds after the last measurement. */
  at(lead) {
    return this.level === null ? 0 : this.level + this.velocity * lead;
  }

  reset() {
    this.level = null;
    this.velocity = 0;
    this.step = 0;
    this.rate.reset();
    this.previousValue = null;
  }
}

/** `Predicted` over each component of a vector, sharing one speed estimate. */
export class PredictedVector {
  constructor(size, options) {
    this.filters = Array.from({ length: size }, () => new Predicted(options));
    /** How hard the lag ceiling pulled on the last update, 0..1. */
    this.bounded = 0;
    /** Noise-DC estimator over the shared speed — `noiseCap` 0 leaves it inert. */
    this.noise = new NoiseFloor(options?.noiseCap ?? 0);
    /** The smoothed measured speed, before the floor: the honest rate readout. */
    this.measuredSpeed = 0;
    /** What `beta` actually read this update: `measuredSpeed` minus the floor. */
    this.activityEff = 0;
  }

  set options(options) {
    for (const f of this.filters) Object.assign(f, options);
  }

  /** How fast the whole vector is moving, in units per second. */
  get speed() {
    let sum = 0;
    for (const f of this.filters) sum += f.velocity * f.velocity;
    return Math.sqrt(sum);
  }

  get ready() {
    return this.filters[0].level !== null;
  }

  /**
   * Folds in one measurement, with the lag ceiling applied between the level and
   * trend halves — see `Predicted.updateLevel` for why the order matters.
   *
   * `soft`/`hard` are the ceiling in the vector's own units. Past `soft` the level is
   * dragged toward what was actually measured, on a smoothstep so nothing kinks;
   * past `hard` it is simply placed there, so the filter cannot lag by more than
   * `hard` whatever the head does and whatever the tuning is.
   *
   * The error is measured across all three axes at once rather than per component,
   * for the same reason `activity` is: a bound that fires on x alone would correct a
   * diagonal movement along one axis only, and the head would visibly skew.
   *
   * Sets `bounded` to how hard it had to pull, 0..1 — worth a readout, because a
   * filter that is bounded on most frames is mistuned rather than merely insured.
   */
  update(values, dt, soft = Infinity, hard = Infinity, trust = 1) {
    // Every component's measured rate first, then one shared speed for all of them.
    // Per-axis speed — what a naive One Euro does — makes a diagonal movement
    // responsive along one axis and sluggish along another, and the head appears to
    // slide as it moves.
    let sum = 0;
    for (let i = 0; i < this.filters.length; i++) {
      const rate = this.filters[i].observe(values[i], dt);
      sum += rate * rate;
    }
    this.measuredSpeed = Math.sqrt(sum);

    // The floor comes off *here* and nowhere else: the activity `beta` reads is the
    // measured speed minus its own noise DC, while the level update below still
    // sees every measurement and `minCutoff` still tracks any motion the cutoff
    // never opens for. On a still head at a hard pose the DC *is* the whole speed
    // signal, the cutoff finally closes to `minCutoff`, and the shimmer that rode
    // the open cutoff dies; in real motion the speed dwarfs the (frozen) floor and
    // the subtraction is a rounding error. See `NoiseFloor`.
    //
    // `trust` then scales what beta may believe (design C1's optional third
    // lever, wired at stage 6 for the deep-tilt giggle): at an untrusted pose the
    // measurement noise is several-fold and the floor — frozen by G10's own
    // admission gate during motion — cannot have calibrated to it, so the speed
    // signal over-reads exactly where it is least meaningful. Scaling the
    // activity toward the floor at low trust keeps the cutoff near `minCutoff`
    // at the poses the detector barely handles; real motion there tracks through
    // `minCutoff` and the lag ceilings exactly as a still hard pose does.
    this.activityEff = this.noise.condition(this.measuredSpeed, dt)
      * (0.3 + 0.7 * Math.min(Math.max(trust, 0), 1));

    for (let i = 0; i < this.filters.length; i++) {
      this.filters[i].updateLevel(values[i], dt, this.activityEff);
    }

    this.bounded = this.applyBound(values, soft, hard);

    for (const filter of this.filters) filter.updateTrend();
  }

  applyBound(values, soft, hard) {
    if (!this.ready || !(soft < Infinity)) return 0;

    let sum = 0;
    for (let i = 0; i < this.filters.length; i++) {
      const d = values[i] - this.filters[i].level;
      sum += d * d;
    }
    const error = Math.sqrt(sum);
    if (!(error > soft)) return 0;

    const t = smoothstep(soft, hard, error);
    for (let i = 0; i < this.filters.length; i++) {
      this.filters[i].level += (values[i] - this.filters[i].level) * t;
    }
    return t;
  }

  /**
   * Reads the vector out `lead` seconds ahead, with the velocity bounded.
   *
   * The bound is a backstop against a divergent estimate, and it has to be sized as
   * one. Bounding the *displacement* instead is the obvious way to write it and it
   * was wrong, expensively: a 2 cm cap over a 120 ms horizon starts biting at 17 cm/s
   * of head speed, which is a slow lean. Measured against ground truth, the frame
   * came out 34 mm behind at 60 cm/s and 70 mm behind at 100 cm/s — the prediction
   * throttled to a third of its job by its own safety net, on exactly the fast
   * movements where the lag is worth removing.
   *
   * A *speed* bound has no such coupling to the horizon. Set it above anything a head
   * can physically do and it never touches a real movement, while still containing an
   * estimate that has run away.
   */
  at(lead, maxSpeed, out) {
    let seconds = lead > 0 ? lead : 0;
    const speed = this.speed;
    if (seconds > 0 && speed > maxSpeed) seconds *= maxSpeed / speed;

    for (let i = 0; i < this.filters.length; i++) {
      const filter = this.filters[i];
      out[i] = (filter.level ?? 0) + filter.velocity * seconds;
    }
    return out;
  }

  reset() {
    for (const f of this.filters) f.reset();
    this.noise.reset();
    this.measuredSpeed = 0;
    this.activityEff = 0;
  }
}

const measured = new THREE.Quaternion();
const stepQuaternion = new THREE.Quaternion();
const stepAxis = new THREE.Vector3();

/**
 * The same idea on a rotation, with the velocity carried as an angular one.
 *
 * Quaternion components cannot simply be smoothed and extrapolated independently:
 * four numbers moving in straight lines do not describe a rotation turning at a
 * constant rate, and the error grows with the angle. So the level is a quaternion,
 * blended by `slerp`, and the trend is an angular velocity vector in rad/s, applied
 * by composing the rotation it generates over the interval.
 *
 * `slerp` takes the short way round, which incidentally handles the sign: q and -q
 * are the same rotation at opposite ends of 4-space, and blended naively they swing
 * the head through an unrelated arc.
 */
export class PredictedRotation {
  constructor({ minCutoff = 1.0, beta = 0.0, trendGain = 0.25, dCutoff = 1.0,
    noiseCap = 0 } = {}) {
    Object.assign(this, { minCutoff, beta, trendGain, dCutoff });
    this.level = new THREE.Quaternion();
    this.omega = new THREE.Vector3();
    // The previous *measurement*, and the smoothed rate between measurements — what
    // `beta` reads. Distinct from `omega`, which is the estimated trend. The rate is
    // carried as a *signed* axis-angle vector and rectified only on the way out —
    // see the note in `update`.
    this.lastMeasured = new THREE.Quaternion();
    this.rateAxis = new THREE.Vector3();
    this.rateStarted = false;
    this.started = false;
    this.scratch = new THREE.Quaternion();
    this.previous = new THREE.Quaternion();
    /** How hard the lag ceiling pulled on the last update, 0..1. */
    this.bounded = 0;
    /** Noise-DC estimator over the turn rate — `noiseCap` 0 leaves it inert. */
    this.noise = new NoiseFloor(noiseCap);
    /** The smoothed measured turn rate, before the floor, rad/s. */
    this.measuredRate = 0;
    /** What `beta` actually read this update: `measuredRate` minus the floor. */
    this.activityEff = 0;
  }

  set options(options) {
    Object.assign(this, options);
  }

  get ready() {
    return this.started;
  }

  /** How fast the head is turning, rad/s. */
  get speed() {
    return this.omega.length();
  }

  /**
   * `q` is `[x, y, z, w]`. `soft`/`hard` are the lag ceiling in radians.
   *
   * Level, then bound, then trend — the same three-phase order as
   * `PredictedVector.update`, and for the same reason: a correction applied after the
   * trend is read gets subtracted from the turn rate instead of added to it.
   */
  update(q, dt, soft = Infinity, hard = Infinity, trust = 1) {
    measured.set(q[0], q[1], q[2], q[3]);

    if (!this.started) {
      this.level.copy(measured);
      this.lastMeasured.copy(measured);
      this.omega.set(0, 0, 0);
      this.started = true;
      return;
    }
    if (!(dt > 0)) return;

    // The *measured* turn rate, not `omega` — same fix as `Predicted.observe`, and
    // the same reason. `omega` is the estimated trend and takes about a second to
    // respond, so reading it here left `beta` inert through every real head turn and
    // the rotation filter running as a fixed 1 Hz low-pass with 160 ms of lag.
    //
    // The rate is smoothed as the SIGNED per-axis step, and the magnitude is taken
    // *after* the pole — the position path's treatment, where `Predicted.observe`
    // filters the signed derivative and rectifies the result. Rectifying first,
    // which is what this used to do, turned alternating-sign detector noise into a
    // DC turn rate: +δ then −δ averaged to δ instead of to zero, so at the hard
    // poses where the detector wobbles most the cutoff sat open on a perfectly
    // still head and per-frame noise walked straight through the filter built to
    // stop it (the diagnosed hard-angle jitter, cause 1). Smoothed with its signs,
    // noise cancels; a real turn, whose steps agree in direction, reads exactly as
    // before. Double cover cannot flip the sign under us: the step axis comes out
    // of `rotationVector`, which reads q and -q identically (|w| in the angle, w's
    // sign folded into the axis), so a measurement that hops hemispheres between
    // frames contributes the same signed axis either way.
    this.scratch.copy(measured).multiply(this.previous.copy(this.lastMeasured).invert());
    rotationVector(this.scratch, stepAxis);
    stepAxis.multiplyScalar(1 / dt);
    if (this.rateStarted) {
      this.rateAxis.lerp(stepAxis, alphaFor(this.dCutoff, dt));
    } else {
      // First sample adopted whole — the same rule every filter here starts by.
      this.rateAxis.copy(stepAxis);
      this.rateStarted = true;
    }
    this.measuredRate = this.rateAxis.length();
    this.lastMeasured.copy(measured);

    // Floor off, beta only — the same one line as `PredictedVector.update`, and the
    // same reasoning; the slerp below still sees every measurement. See `NoiseFloor`.
    // `trust` scales it exactly as in `PredictedVector.update` (the deep-tilt lever).
    this.activityEff = this.noise.condition(this.measuredRate, dt)
      * (0.3 + 0.7 * Math.min(Math.max(trust, 0), 1));

    const alpha = alphaFor(this.minCutoff + this.beta * this.activityEff, dt);

    this.previous.copy(this.level).invert();
    // Where the head would be if it kept turning, then corrected towards what was
    // actually seen — the rotational form of the level update above.
    turn(this.level, this.omega, dt, this.level);
    this.level.slerp(measured, alpha);

    this.bounded = this.applyBound(soft, hard);

    // The rotation that got us from the previous level to this one, as a rate.
    this.scratch.copy(this.level).multiply(this.previous);
    rotationVector(this.scratch, stepAxis);
    stepAxis.multiplyScalar(1 / dt);
    this.omega.lerp(stepAxis, this.trendGain * alpha);
  }

  /**
   * The rotational lag ceiling. `measured` is already loaded by `update`.
   *
   * The error is the angle between the two rotations, which is the only measure of
   * "how far apart" that means anything here; per-component quaternion differences
   * are not an angle and go wrong exactly where it matters, at speed.
   */
  applyBound(soft, hard) {
    if (!(soft < Infinity)) return 0;

    // |dot| rather than dot: q and -q are the same rotation, and the signed form
    // reports a head that is perfectly still as being 180° out.
    const dot = Math.min(Math.abs(this.level.dot(measured)), 1);
    const error = 2 * Math.acos(dot);
    if (!(error > soft)) return 0;

    const t = smoothstep(soft, hard, error);
    // `slerp` takes the short way round, so the sign is handled for us.
    this.level.slerp(measured, t);
    return t;
  }

  /** Reads the rotation out `lead` seconds ahead, with the turn rate bounded. */
  at(lead, maxRate, out) {
    let seconds = lead > 0 ? lead : 0;
    const rate = this.omega.length();
    if (seconds > 0 && rate > maxRate) seconds *= maxRate / rate;

    turn(this.level, this.omega, seconds, this.scratch);
    out[0] = this.scratch.x; out[1] = this.scratch.y;
    out[2] = this.scratch.z; out[3] = this.scratch.w;
    return out;
  }

  reset() {
    this.level.identity();
    this.omega.set(0, 0, 0);
    this.lastMeasured.identity();
    this.rateAxis.set(0, 0, 0);
    this.rateStarted = false;
    this.noise.reset();
    this.measuredRate = 0;
    this.activityEff = 0;
    this.started = false;
  }
}

const turned = new THREE.Quaternion();

/**
 * `out = exp(omega * seconds / 2) * q` — q advanced by turning at `omega` for
 * `seconds`. Safe to call with `out` aliasing `q`, which the level update does.
 */
function turn(q, omega, seconds, out) {
  const angle = omega.length() * seconds;
  if (Math.abs(angle) < 1e-9) return out.copy(q);
  stepQuaternion.setFromAxisAngle(stepAxis.copy(omega).normalize(), angle);
  turned.copy(stepQuaternion).multiply(q);
  return out.copy(turned);
}

/**
 * A rotation as an axis scaled by its angle.
 *
 * `atan2` rather than `acos(w)`: near identity — which is where every frame-to-frame
 * delta lives — `acos` loses most of its precision, and this feeds a velocity that
 * is then multiplied by the frame rate.
 */
function rotationVector(q, out) {
  const vectorLength = Math.hypot(q.x, q.y, q.z);
  if (vectorLength < 1e-12) return out.set(0, 0, 0);
  // The short way round: -q is the same rotation, and taking the long one would
  // report a head snapping through 2pi.
  const sign = q.w < 0 ? -1 : 1;
  const angle = 2 * Math.atan2(vectorLength, Math.abs(q.w));
  return out.set(q.x, q.y, q.z).multiplyScalar((sign * angle) / vectorLength);
}

/**
 * Smooths a full rigid-plus-uniform-scale pose, and reports it at a chosen instant.
 *
 * Position, rotation and scale get separate settings on purpose. Scale is the
 * twitchiest of the three — it is inferred from apparent head size, so it absorbs
 * most of the depth ambiguity — and it needs the heaviest hand; it is also the one
 * that is never predicted, because a head does not change size and any velocity in
 * it is noise.
 *
 * `update` and `sample` are deliberately separate calls. A detection arrives on the
 * camera's clock; a render happens on the display's, more often and at a different
 * phase. Splitting them lets every rendered frame ask for the pose *at that frame's
 * display time* — which both compensates the pipeline's latency and fills in the
 * renders that fall between two detections, so 30 fps of tracking drives 60 fps of
 * motion instead of stepping.
 */
export class PoseSmoother {
  constructor(settings) {
    this.position = new PredictedVector(3, settings.position);
    this.rotation = new PredictedRotation(settings.rotation);
    this.scale = new Predicted(settings.scale);
    this.limits = { ...settings.limits };
    this.pose = { position: [0, 0, 0], quaternion: [0, 0, 0, 1], scale: 1 };
    /** Smoothed 0..1: how hard the lag bound is having to pull. See `update`. */
    this.bounded = 0;
  }

  set settings(settings) {
    this.position.options = settings.position;
    this.rotation.options = settings.rotation;
    Object.assign(this.scale, settings.scale);
    this.limits = { ...settings.limits };
  }

  get ready() {
    return this.position.ready && this.rotation.ready;
  }

  /**
   * Folds in one detection. `dt` is the interval since the previous detection.
   *
   * Filtering, then bounding. The bound runs *after* the update and against the same
   * raw measurement, so what it limits is the finished output — which is the only
   * quantity anybody sees, and the only one worth making a promise about.
   *
   * That promise: the drawn pose is never more than `limits.positionLag` centimetres
   * or `limits.rotationLag` radians away from what the detector actually reported for
   * this frame. It is a stronger and much more useful statement than any cutoff, and
   * it is what lets the cutoffs below stay low enough to kill the shimmer.
   */
  update({ position, quaternion, scale }, dt, trust = 1) {
    this.position.update(position, dt, this.limits.positionSettle, this.limits.positionLag, trust);
    this.rotation.update(quaternion, dt, this.limits.rotationSettle, this.limits.rotationLag, trust);
    // Scale carries no ceiling: it is never predicted, so it has no trend that could
    // run away, and its "lag" is a slow settle onto a constant — which is the
    // behaviour wanted, not a fault to be corrected.
    this.scale.update(scale, dt);

    // Reported, not just applied. A filter that is bounded on most frames is telling
    // you its cutoffs are wrong; without a number for it, that shows up only as a
    // vague complaint about lag months later.
    const pulled = Math.max(this.position.bounded, this.rotation.bounded);
    this.bounded += (pulled - this.bounded) * 0.1;
  }

  /**
   * The pose `lead` seconds after the last detection.
   *
   * `lead` of zero gives the filtered pose at the moment the frame was captured,
   * which is the honest answer and also the late one — the pixels it was solved from
   * are already on their way to the screen. See `main.js` for where the number
   * comes from.
   */
  sample(lead = 0) {
    const bounded = Math.min(Math.max(lead, 0), this.limits.lead);
    this.position.at(bounded, this.limits.speed, this.pose.position);
    this.rotation.at(bounded, this.limits.turnRate, this.pose.quaternion);
    this.pose.scale = this.scale.level ?? 1;
    return this.pose;
  }

  reset() {
    this.position.reset();
    this.rotation.reset();
    this.scale.reset();
    this.bounded = 0;
  }

  /**
   * Throws away the two noise-DC calibrations and NOTHING else — the narrow
   * reset a change of wearer wants, where `reset()` would be wrong.
   *
   * The distinction is the whole point. A `NoiseFloor` is a measurement of one
   * person in one room in front of one camera: their distance, their lighting,
   * their skin contrast, how well the detector likes their face. Carried into
   * the next wearer it is bounded and self-healing in one direction and not in
   * the other — the floor falls free, so a QUIETER second wearer re-calibrates
   * on the next window, but a NOISIER one inherits a floor that is too low and
   * may only climb it at `NOISE_RISE · cap` per second (~0.8 cm/s per second),
   * so for several seconds the adaptive cutoff sits open on noise it has been
   * told is signal and the new wearer sees exactly the shimmer this class
   * exists to remove. Worse for the population Goal 1 is for: the users most
   * likely to be the noisier of a pair are the ones the detector already
   * serves worst.
   *
   * The pose LEVEL deliberately survives, and that is not laziness. The
   * composite is frame-locked: the pose drawn on a frame is the pose solved
   * from that frame. Clearing the level makes the next detection be adopted
   * whole, which is correct after a dropout (the velocity describes a movement
   * that is over) and wrong here — nobody moved, the estimator changed its
   * mind about whose face it is, and a jump would put a visible pop on screen
   * for a reason the viewer cannot see. So a swap re-calibrates the noise and
   * keeps the pose continuous, and the isolation check asserts both halves.
   */
  resetNoise() {
    this.position.noise.reset();
    this.position.measuredSpeed = 0;
    this.position.activityEff = 0;
    this.rotation.noise.reset();
    this.rotation.measuredRate = 0;
    this.rotation.activityEff = 0;
    // The RATE estimators go with the floors, and the line between them and the
    // level is where this reset earns its name. A `NoiseFloor` calibrates the
    // measured rate; the rate is a difference between two consecutive
    // MEASUREMENTS, so the first one taken across a change of wearer is a
    // difference between two people's readings — and it is admitted to the
    // freshly-cleared ring as though it described the new one. Its VALUE is
    // usually near zero (nobody moved), but its arrival is not: the ring is one
    // sample further on forever after, which is the previous wearer showing
    // through a calibration that claims to be the new wearer's. Clearing the
    // derivative costs exactly one frame of `beta` (the cutoff sits at
    // `minCutoff` until the next measurement gives it a rate), which is what a
    // cold session's first frame does anyway.
    //
    // The LEVEL is untouched by all of it, and that is the invariant: the
    // composite is frame-locked, so the pose drawn on a frame is the pose
    // solved from that frame. Nobody moved when the estimator changed its mind
    // about whose face it is, and a level reset would put a visible pop on
    // screen for a reason the viewer cannot see.
    for (const f of this.position.filters) {
      f.previousValue = null;
      f.rate.reset();
    }
    this.rotation.rateAxis.set(0, 0, 0);
    this.rotation.rateStarted = false;
  }
}

/**
 * Filter settings that look right for a face at arm's length, 30–60 fps.
 *
 * `beta` carries the timeliness and `minCutoff` carries the stillness, in the units
 * of each signal: centimetres per second for position, radians per second for
 * rotation. `trendGain` is 0 — see below.
 *
 * **This filter is not the first one in the chain, and that is easy to forget.**
 * MediaPipe smooths the landmarks internally whenever `numFaces` is 1 — their own
 * documentation says so plainly, "Smoothing is only applied when num_faces is set to
 * 1" — and the transform is solved *from* those landmarks, so everything here
 * arrives pre-filtered. Two filters in series do not add their lags politely; the
 * second one is reading a signal whose velocity has already been attenuated, so it
 * under-estimates the trend and lags more than its own tuning predicts. `?faces=2`
 * turns MediaPipe's off (see `tracker.js`), which is the A/B worth running on any
 * machine that feels sluggish.
 *
 * The lag *ceiling* in `limits` catches the case where all of this is wrong anyway —
 * but read the note there before treating it as the answer to lag. It is a
 * divergence backstop, and this filter's honest defence against trailing a moving
 * head is the velocity term, not the ceiling.
 */
export const DEFAULT_SMOOTHING = {
  /**
   * `beta` is the knob that decides whether the glasses keep up, and both of these
   * were an order of magnitude too small.
   *
   * The arithmetic is not subtle once the speed signal is honest. A first-order pole
   * lags a moving signal by its own time constant, so the error is `tau * speed`; to
   * hold 4 mm at 25 cm/s needs `tau <= 16 ms`, which is a 10 Hz cutoff. Reaching
   * 10 Hz at 25 cm/s takes `beta = (10 - 0.7) / 25 = 0.37`. At 0.06 the cutoff
   * reached 2.4 Hz there, worth 17 mm of lag before the reversal overshoot on top —
   * measured at 29.5 mm on a gentle 0.5 Hz look-around, which is what a wearer
   * describes as the frame following them a beat late.
   *
   * The cost is paid where it does not show: at rest the measured speed falls to
   * ~1 cm/s, the cutoff closes back to ~1.1 Hz, and per-frame detector noise is
   * attenuated to a fifth. That separation only works because the speed signal is
   * the *measured* rate smoothed at `dCutoff` — noise alone reads as ~3.6 cm/s
   * frame to frame, and a 1 Hz pole on the derivative is what stops it being
   * mistaken for a head that is moving.
   */
  /**
   * `trendGain: 0` — **no velocity is carried, deliberately, and this reverses an
   * earlier decision that a whole round of latency work was built on.**
   *
   * The case for carrying one is real and was measured: a constant-rate ramp is a
   * fixed point of the recurrence, so a velocity-carrying filter tracks one to
   * 0.00 mm where this configuration sits 3.0 mm behind. That is a genuine win and
   * the harness still pins it.
   *
   * It is the wrong test. A head does not ramp; it reverses, two or three times a
   * second, and at every reversal the carried velocity sails past the turn before the
   * trend can catch up — the trend converges at `trendGain * alpha`, which is slower
   * than the reversal. Measured on the same 0.5 Hz look-around: 3.8 mm without a
   * trend, 12.0 mm with one. It costs three times what it saves, and the harness
   * measures both side by side so the trade cannot quietly invert again.
   *
   * There is nothing else the trend was buying, because the composite is frame-locked
   * and samples at lead 0. Prediction remains available on `Predicted` for a caller
   * that wants it; the app is not one.
   *
   * `dCutoff: 3.0` rather than One Euro's usual 1.0. The speed signal is what opens
   * the cutoff, so *its* lag is lag too: at 1 Hz the speed estimate was still rising
   * when the head reached peak speed, and the cutoff opened after it was needed —
   * worth 8.5 mm against 3.8 mm on the slow sweep. It cannot go much higher without
   * per-frame noise reading as movement and opening the cutoff on a still head, which
   * is the failure the `does not shimmer` check exists to catch.
   */
  /**
   * `noiseCap` is the hard ceiling on each filter's noise floor — see `NoiseFloor`
   * for the mechanism. The caps are sized from the two populations the floor must
   * separate: a webcam's measured noise DC (1–5 cm/s of phantom speed; 0.02–0.09
   * rad/s once the rotation rate is signed) against real head motion (10–25 cm/s,
   * 0.5–3 rad/s). 8 cm/s and 0.25 rad/s sit above everything the first population
   * measures and at a third of where the second begins, so even a floor pinned at
   * its cap costs a moving head only a sliver of cutoff — at the 0.5 Hz sweep's
   * 25 cm/s peak, subtracting the whole position cap moves the cutoff 30.6 →
   * 21 Hz, under a millimetre of extra lag at peak speed.
   */
  position: { minCutoff: 0.6, beta: 1.2, trendGain: 0.0, dCutoff: 3.0, noiseCap: 8 },
  // Radians per second here, so the number looks large next to the position one and
  // is the same statement: ~28 Hz of cutoff at a brisk 1.6 rad/s (92°/s) turn.
  rotation: { minCutoff: 0.7, beta: 17.0, trendGain: 0.0, dCutoff: 3.0, noiseCap: 0.25 },
  // Never predicted: `trendGain` 0 leaves the velocity at zero, so the pose's scale
  // cannot be extrapolated anywhere — and, since the adaptive cutoff reads that same
  // velocity, this is a fixed 0.6 Hz pole rather than an adaptive one. Which is the
  // right filter for it: the facial transform is rigid, its scale is 1.0 on every
  // face, and a signal that does not move has no speed to adapt to.
  scale: { minCutoff: 0.6, trendGain: 0 },
  /**
   * How far prediction is allowed to reach, how fast it is allowed to believe the
   * head is going, and — the addition that matters — how far behind it is allowed
   * to fall.
   *
   * `lead` caps the horizon, because a stalled detector must not let the frame sail
   * off the head while it waits. It is generous enough to cover a slow machine's
   * whole chain — a webcam's capture latency alone can be 80 ms, and a horizon
   * shorter than the latency simply leaves the difference on the table as lag.
   *
   * `speed` and `turnRate` are **physiological bounds, not tuning**. A head does not
   * travel at 1.5 m/s or turn at 690°/s, so nothing a real wearer does comes near
   * them; what does come near them is an estimate that has diverged, which is the
   * only thing they exist to contain. Anything tighter stops being a backstop and
   * starts being a limiter — see the note on `PredictedVector.at`.
   *
   * The four `*Settle` / `*Lag` figures are the lag ceiling, and they are **a
   * backstop, in the same family as `speed` and `turnRate` above — not a lag
   * management device**. That distinction cost a round of tuning to arrive at and is
   * worth stating, because the technique is borrowed from WebAR.rocks, where it is
   * doing something quite different.
   *
   * Their filter is a plain One Euro: a first-order low-pass, which lags a moving
   * signal by `tau` *forever*, proportional to speed. A tight ceiling is exactly
   * right there, because the lag it clips is real, constant and unavoidable. Ours
   * carries a velocity, and a constant-velocity ramp is a fixed point of the
   * recurrence — **there is no steady-state lag to clip.** Measured in the harness,
   * the estimator sits 0.00 mm behind a 25 cm/s ramp. Setting the ceiling at the
   * millimetre scale their filter needs therefore does not remove lag we have; it
   * fires on every ordinary movement, overrides the filter with raw jitter, and — in
   * the first version of this — silently ate the velocity estimate as well.
   *
   * So the numbers are sized as insurance. 3 cm is a fifth of a face width and 20°
   * is a visible turn: nothing a correctly-behaving filter does comes near either,
   * and what does is an estimate that has genuinely diverged. If the `lag bound`
   * readout is anything but 0% in normal use, the cutoffs are wrong — which is the
   * real reason it is reported.
   *
   * None of this touches the *other* lag in the chain, and it cannot: MediaPipe's
   * own smoother runs upstream of every number here, so a ceiling measured against
   * its output cannot see what it already removed. `?faces=2` is the instrument for
   * that one.
   */
  limits: {
    lead: 0.2,
    speed: 150,
    turnRate: 12,
    positionSettle: 3.0,
    positionLag: 6.0,
    rotationSettle: 0.35,
    rotationLag: 0.7,
  },
};
