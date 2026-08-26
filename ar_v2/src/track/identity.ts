/**
 * Is this still the person we scanned?
 *
 * v2 solves pose against a KNOWN rigid model. Everything downstream — the seat,
 * the calibration field, the fit verdict, the PD — is a statement about the
 * wearer that scan measured. So the one question nothing in this tree asked was
 * whether the face in front of the camera is still that wearer. It was not a
 * gap in the estimator; it was a gap in the session. **A second person sitting
 * down in front of a warm session silently inherited the first one's FaceModel,
 * cached seat and calibration field**, and every number on screen went on
 * describing somebody who had left the room.
 *
 * v1 had this and v2 dropped it in the rewrite. This is not a port — v1's
 * predicate was a temple-width ratio against a canonical head, which is the
 * right answer for a tree with no scanned model and the wrong one for a tree
 * that has the wearer's own geometry. But three of v1's four structural
 * decisions were re-derived independently by the measurement below, and its
 * scar tissue is quoted where it applies.
 *
 * ## The signal, and the one that looked obvious and is useless
 *
 * The obvious candidate is the reprojection residual: a stranger's landmarks
 * should not fit this model. **Measured, it barely separates at all.** Over 10
 * synthetic subjects x 3 camera geometries, 5 campaign seeds, cold solves,
 * matched (A's landmarks vs A's model) against impostor (A's vs B's):
 *
 *     statistic              matched med   impostor med   ratio    AUC     EER
 *     rmsPx (robust)             5.96          8.06       1.35x   0.773   0.316
 *     rms mm at the face         3.80          5.78       1.52x   0.866   0.217
 *     outlier fraction           0.006         0.336        52x   0.946   0.139
 *     varianceFactor             1.93          8.82       4.56x   0.936   0.150
 *
 * `varianceFactor` — the robustly-weighted whitened chi-squared per degree of
 * freedom, `track/pnp.ts:reprojectionStats` — is the only one worth building on,
 * and it is already computed on every frame and already carried out on
 * `TrackResult`. Nothing read it.
 *
 * **Why the whitened statistic and not the pixel one.** A residual in pixels
 * mixes three things it cannot tell apart: geometry error, detector noise, and
 * apparent face size. The whitened form divides each residual by the sigma the
 * detector claimed for that landmark, so an honestly-noisy landmark contributes
 * about 1 whatever its pixel error. Two confounds fall out of that, both
 * measured:
 *
 *  - **Occlusion.** A hand across the face inflates those landmarks' sigma, so
 *    they stop contributing. Matched `varianceFactor` moves 1.78 -> 1.80 across
 *    0-45% occlusion. Matched `rmsPx` at only 15% occlusion (p90 7.75 px)
 *    already exceeds the *impostor* median (7.12 px): any pixel threshold that
 *    catches a stranger also convicts a wearer scratching their chin.
 *  - **Camera geometry.** Matched `rmsPx` is 4.71 px at eye level and 8.13 px on
 *    a phone in the lap, where the eye-level *impostor* median is 6.83 px. **A
 *    phone user's genuine frames read worse than a laptop user's impostor
 *    frames.** A pixel threshold is not transferable between devices; this one
 *    is dimensionless.
 *
 * ## Why the decision is made near-frontal, and it is NOT to avoid false alarms
 *
 * Matched `varianceFactor` is flat in yaw — 1.74 to 2.02 across 0 to 90 degrees
 * — so a wearer turning their head does not look like a stranger, which is the
 * failure v1 spent a live session chasing. What decays is the OTHER side:
 *
 *     yaw    matched med   impostor med   impostor p10   in-bucket AUC
 *      0        2.00          10.98           4.87           0.975
 *     30        1.89           8.52           4.50           0.969
 *     60        1.83           5.31           3.14           0.903
 *     90        2.02           3.43           2.11           0.814
 *
 * At deep yaw the far half of the face is hallucinated and arrives with a large
 * claimed sigma, so the correspondences that actually carry identity are muted
 * and a stranger becomes *harder to catch*. The frontal gate here is therefore a
 * guard against **false negatives**, and that is a different argument from
 * v1's — which reached the same gate from the opposite direction, its own
 * predicate having fired nine times in four minutes on a wearer's own turns.
 * Same constant, opposite reason, and both reasons are real.
 *
 * ## The bar is a RATIO to this wearer, and it cannot be a constant
 *
 * This is the finding that shapes the whole module. `varianceFactor` is by
 * construction residual over *claimed* sigma, so a detector that understates its
 * own noise is arithmetically indistinguishable from a wrong face:
 *
 *     detector           matched vf med   clean-impostor vf med   verdict
 *     0.7 px, honest          1.78                9.23            fine
 *     1.5 px, claims 0.7      4.26                9.23            fine
 *     3.0 px, claims 0.7     12.36                9.23            EVERY genuine
 *     6.0 px, claims 0.7     42.35                9.23            frame convicts
 *
 * A 4.3x sigma understatement puts every genuine frame above the impostor
 * median while the in-arm AUC stays 0.995: **the signal is intact and the
 * calibration is gone.** MediaPipe reports no per-landmark uncertainty at all —
 * `detect/uncertainty.ts` estimates it — so an absolute threshold here would
 * rest entirely on that estimator staying calibrated, on every device, forever.
 *
 * v1 met the same shape of problem and deleted an entire arm of its predicate
 * over it: *"the instrument's measured noise exceeds the tolerance it was asked
 * to convict at, and a predicate must not convict on a reading its own fixture
 * shows lying."* The answer here is the same rule applied differently — compare
 * the wearer against **their own reference**, learned live, on this device, in
 * this session.
 *
 * ## The correction: an OFFSET is harmless, a DRIFT is not
 *
 * **The paragraph above used to end "a constant miscalibration then cancels",
 * and stop there. That was true and it was the wrong emphasis**, and the
 * experiment that says so is the reason `IDENTITY_SIGMA_DRIFT_MAX` exists.
 * Measured end to end, the claimed sigma scaled by a factor:
 *
 *     arm                        same-person worst ratio   false convictions
 *     honest                             1.687                   0/36
 *     OFFSET, 2x overconfident           1.722                   0/36
 *     OFFSET, 4x overconfident           1.797                   0/36
 *     DRIFT to 2x mid-session            4.720                  36/36
 *     DRIFT to 4x mid-session           16.847                  36/36
 *
 * A permanently overconfident detector is a non-event: it inflates the
 * reference and the reading together and the ratio is unmoved. A detector that
 * changes its mind about its own noise PART WAY THROUGH convicts every single
 * genuine wearer, because the reference was learned on one scale and the
 * verdict is taken on another. The table this file used to lead with described
 * the harmless case in detail and gave the fatal one half a sentence.
 *
 * The guard is possible because the denominator is observable: an identity
 * change moves the mean claimed sigma by at most 1.35x, a harmful drift by 2x
 * or more. When it moves, the reference is retired and learned again rather
 * than judged against — `'recalibrating'`, which is not a verdict about the
 * wearer at all. With it, the drift arms go 36/36 false convictions to 0/36
 * while honest and offset are untouched.
 *
 * **And here is what that costs, because it is a real hole and not a rounding
 * error.** When a drift and a change of wearer arrive in the SAME frames, the
 * watch recalibrates rather than convicting — and it recalibrates onto the
 * stranger. Detection in those arms falls from 93% to 0-5%. The trade is
 * deliberate (36 wrongly-convicted wearers against one stranger who gets
 * through) but it is not free, and it is the strongest argument for giving
 * `detect/uncertainty.ts` a calibration check of its own: that would tell a
 * detector which has genuinely become noisier from one which is merely lying
 * about it, and this guard cannot.
 *
 * ## What it refuses to answer
 *
 * **No reference, no verdict.** The reference is learned from the first
 * qualifying frames after a model is adopted, and until it exists this returns
 * `'learning'` and can never convict. That is not a soft start: a model restored
 * from `localStorage` at boot was scanned in a previous session, possibly on
 * another device, and the person in front of the camera may be anyone. Learning
 * a reference from them would reference the stranger and guarantee silence.
 * Abstaining says so instead. The gap this module closes is the one the parity
 * ledger named — a second wearer arriving mid-session — and a shared device at
 * cold boot is a different problem needing a different answer.
 *
 * Also, honestly: **every number above is synthetic.** The population is drawn
 * from the same shape basis the estimator fits, no expression change, no weight
 * change, no ageing, no glasses already on the face, and no relatives. And the
 * margin thins with population size — impostor-min falls 30% between 5 and 30
 * subjects while matched-worst rises 2%, closing at a linear-extrapolated 35-45.
 * Treat the ratio below as bounded by measurement, not optimised by it.
 */

/** What the watch saw this frame. Everything comes off `TrackResult`. */
export interface IdentityObservation {
  /** The frame produced a solve of its own — not held, not dropped. */
  readonly solved: boolean;
  /** `TrackResult.varianceFactor`. NaN on a held or dropped frame. */
  readonly varianceFactor: number;
  /** Absolute yaw in radians, from `TrackResult.euler`. NaN if unavailable. */
  readonly yawRad: number;
  /** Absolute pitch in radians. NaN if unavailable. */
  readonly pitchRad: number;
  /** `TrackResult.correspondences` — how much face the solve actually had. */
  readonly correspondences: number;
  /**
   * The mean of the sigmas the detector CLAIMED this frame, px.
   *
   * The denominator of the whole statistic, made visible. `varianceFactor` is
   * residual over claimed sigma, so it moves for two reasons that have nothing
   * to do with each other: the geometry stopped fitting (a different face), or
   * the claimed sigma changed scale (the estimator's calibration moved). This
   * is what tells them apart — see `IDENTITY_SIGMA_DRIFT_MAX`.
   */
  readonly meanSigmaPx: number;
}

export type IdentityVerdict =
  /** Not enough qualifying frames yet to know this wearer's own reading. */
  | 'learning'
  /** Asked and answered: the same person. */
  | 'same'
  /** The frame did not qualify — turned away, occluded, held. Nothing learned,
   *  nothing decided, and crucially no strike accrued OR cleared. */
  | 'abstain'
  /** A different person, on `IDENTITY_STRIKES` consecutive qualifying frames. */
  | 'changed'
  /**
   * The claimed-sigma scale moved, so the reference is on the wrong scale and
   * is being learned again. Not a verdict about the wearer at all.
   */
  | 'recalibrating';

export interface IdentityWatch {
  /**
   * Whether this watch is allowed to judge anybody at all.
   *
   * False until `armWearer`, and `armWearer` is called from exactly one place:
   * immediately after a scan taken on this device, in this session, from a
   * camera. Everything else abstains forever, and that is the design rather
   * than a limitation of it — see the header, "What it refuses to answer".
   * A model restored from `localStorage` was measured in a previous session and
   * possibly on another device, so there is nobody in the room this watch can
   * be sure of; learning a reference from whoever is sitting down would
   * reference the stranger and guarantee silence.
   */
  armed: boolean;
  /** The wearer's own reading, once learned. NaN while learning. */
  reference: number;
  /** The mean claimed sigma the reference was learned at, px. NaN until then. */
  referenceSigma: number;
  /** Qualifying readings collected toward the reference. */
  readonly learning: number[];
  /** The claimed sigmas seen while learning, alongside them. */
  readonly learningSigma: number[];
  /**
   * The rolling window the verdict is taken on.
   *
   * Named `recent` rather than `window` because `check-isolation.mjs` reads
   * `watch.window.push(...)` as a touch of the browser global and fails the
   * build — correctly. A headless module with a field called `window` is asking
   * for that confusion, and the gate catching it on the first run is the gate
   * working rather than being in the way.
   */
  readonly recent: number[];
  /** Consecutive qualifying frames whose window median was over the bar. */
  strikes: number;
  /** Lifetime counters. Deliberately NOT reset with the thing they count — v1's
   *  rule, and it is the only way a reset can be reported after it happens. */
  asked: number;
  struck: number;
  acquitted: number;
  convictions: number;
  /** Times the reference was thrown away because the sigma scale moved. */
  recalibrations: number;
}

/**
 * How far from frontal a frame may be and still be asked about.
 *
 * `measured`. Not a false-alarm guard — matched `varianceFactor` is flat to 90
 * degrees. This protects against the impostor signal DECAYING with yaw: the
 * in-bucket AUC runs 0.975 at frontal, 0.969 at 30, 0.903 at 60 and 0.814 at 90,
 * because the far half-face is hallucinated and its inflated sigma mutes the
 * correspondences that carry identity. Gating at 25 degrees keeps the decision
 * in the two buckets where separation is essentially total, and costs nothing —
 * a wearer looking at their own try-on is frontal most of the time.
 *
 * v1 gated the same decision at an equivalent place (`POSE_TRUST_IDENTITY` 0.8,
 * about 15-20 degrees) for the opposite reason, having watched its own predicate
 * convict a turning wearer nine times in four minutes.
 */
export const IDENTITY_MAX_YAW_DEG = 25;

/** The same, for pitch. Nodding foreshortens the same way turning does. */
export const IDENTITY_MAX_PITCH_DEG = 20;

/**
 * How many landmarks a frame must place before its reading means anything.
 *
 * `stated`. `buildCorrespondences` yields up to 468; a frame that placed a
 * quarter of them is a frame whose whitened residual is dominated by whichever
 * quarter survived. 200 is comfortably below any frontal frame's count and
 * comfortably above the point where the statistic is a sample rather than a
 * measurement.
 */
export const IDENTITY_MIN_CORRESPONDENCES = 200;

/**
 * How many qualifying frames establish the wearer's own reference.
 *
 * `stated`, bounded below by the spread of the statistic. Per-frame matched
 * `varianceFactor` has a p90/median of about 1.36, so a single frame is a poor
 * reference; the median of 12 puts the reference's own scatter well under the
 * ratio bar it is compared against. At 30 fps a frontal wearer supplies 12
 * qualifying frames in well under a second, so this is not a wait anybody sees.
 */
export const IDENTITY_REFERENCE_FRAMES = 12;

/**
 * How many recent qualifying readings the verdict is taken on.
 *
 * `measured`, in the sense that the session-level statistic is what separates
 * cleanly and the per-frame one does not: per-frame EER is 0.150 pooled and
 * 0.017 near-frontal, while the median of a whole capture separates with EER
 * 0.000 in 14 of 15 seed x geometry cells. A median of 5 buys most of that
 * averaging for 0.17 s of latency. Median rather than mean because one wild
 * frame should not carry the verdict — the same reason `robust.ts` exists.
 */
export const IDENTITY_WINDOW = 5;

/**
 * How far over their own reference a wearer's reading may sit before it counts
 * against them.
 *
 * `measured`, on the scenario this module exists for rather than on a summary
 * statistic. 5 seeds x 8 subjects x 3 camera geometries, each session run in two
 * halves against ONE model and ONE watch with no reset between them: the watch
 * learns from the wearer, and then either the wearer continues or a different
 * person's frames arrive. Peak window-median over own-reference after that
 * point:
 *
 *     same person   med 1.548   p90 1.664   WORST 1.723
 *     new person    med 7.422   p10 3.010   min   0.981
 *
 *     bar    false convictions    caught
 *     1.50      61/80  (76%)    222/240 (93%)
 *     1.75       0/80  ( 0%)    222/240 (93%)
 *     2.00       0/80  ( 0%)    222/240 (93%)
 *     2.50       0/80  ( 0%)    222/240 (93%)
 *     3.00       0/80  ( 0%)    216/240 (90%)
 *     4.00       0/80  ( 0%)    203/240 (85%)
 *
 * **It is a plateau, not a knife edge**, and 2.0 is the middle of it: anything
 * from 1.75 to 2.5 gives the same answer on both arms. 1.75 would work and sits
 * 1.6% above the worst genuine session; 2.0 leaves 16% of headroom for the
 * things the synthetic population does not model at all — expression, a
 * different room's light, glasses already on the face, a year of ageing — at a
 * cost of nothing measurable in detection.
 *
 * End to end at the shipped constants: **0 of 80 false convictions, 214 of 240
 * caught (89%), median 7 qualifying frames to convict** — about a quarter of a
 * second of the new wearer facing the camera.
 *
 * **The 11% that get through are the honest cost**, and the asymmetry is on
 * purpose. A false conviction throws away a scan the wearer sat through and is
 * instantly visible; a missed stranger leaves a try-on showing a seat that is
 * slightly wrong. Between those two, err toward missing.
 *
 * **The first calibration of this constant was wrong and is worth recording.**
 * It learned the reference from the impostor's own frames — referencing the
 * stranger, then asking the mechanism to detect a change that had already
 * happened before it was watching. That arm reported 20% detection at this bar
 * and it was measuring nothing at all. A reference is a statement about WHEN it
 * was taken as much as about what it holds.
 */
export const IDENTITY_VF_RATIO = 2.0;

/**
 * Consecutive qualifying frames over the bar before the verdict changes.
 *
 * `published` from v1, which arrived at 5 after a single-frame conviction reset
 * a live session nine times in four minutes. The window above already medians 5
 * frames, so this is 5 strikes on top of a 5-frame median — a real face swap
 * still converts in about a third of a second of the new wearer facing the
 * camera, and no single frame can convict.
 *
 * A qualifying frame whose WINDOW MEDIAN falls at or under the bar clears the
 * streak whole rather than decrementing it — v1's acquittal rule, and the reason
 * it holds: a genuine wearer produces agreeing frames constantly, so a streak
 * can only survive if the disagreement is sustained.
 *
 * **Note what the window does to that rule.** The verdict is taken on the
 * median of the last `IDENTITY_WINDOW` readings, so a single agreeing frame does
 * not acquit — the median still holds four disagreeing ones. Three do. The
 * evidence a conviction actually needs is therefore the window turning over
 * PLUS the streak, which is more than either constant reads on its own. That
 * interaction is why the constants were calibrated by running the whole
 * mechanism end to end rather than by reasoning about them separately.
 */
export const IDENTITY_STRIKES = 5;

/**
 * How far the mean claimed sigma may move before the reference is retired.
 *
 * `measured`, and this constant exists because the module's own documentation
 * emphasised the wrong risk and an experiment said so.
 *
 * The header argues that a ratio to the wearer's own reference cancels a
 * miscalibrated sigma estimator. **It does — completely — and only for a
 * CONSTANT miscalibration.** Run end to end, the wearer learned first and then
 * either continuing or replaced, with the claimed sigma scaled by a factor:
 *
 *     arm                       same-person worst ratio   false convictions
 *     honest                            1.687                  0/36
 *     OFFSET, 4x overconfident          1.797                  0/36
 *     DRIFT to 2x, mid-session          4.720                 36/36
 *     DRIFT to 4x, mid-session         16.847                 36/36
 *
 * An offset is harmless: it inflates the reference and the reading together. A
 * DRIFT is a 100% false-conviction machine, because the reference was learned
 * on one scale and the verdict is taken on another. The original text featured
 * the offset case in a table and mentioned drift in half a sentence; it had the
 * emphasis exactly backwards, and this guard is the correction.
 *
 * The two are separable because the denominator is observable. Mean claimed
 * sigma, second half of a session against the first:
 *
 *     SAME person   med 1.222   range 1.089 - 1.329
 *     NEW  person   med 1.217   range 1.007 - 1.349
 *     a 2x drift          0.500
 *     a 4x drift          0.250
 *
 * A change of wearer is invisible in this quantity — the two distributions are
 * on top of each other, which is exactly what makes it a clean discriminator.
 * (Both medians sit near 1.22 because the later beats of a capture carry more
 * turned frames and honestly claim more noise; that is the protocol, not the
 * person, and it happens equally to both.)
 *
 * 1.6 sits above every identity-driven excursion measured (max 1.349, a 19%
 * margin) and below the smallest drift that causes a false conviction (2.0). It
 * is a plateau rather than a knife edge in the same sense the ratio bar is:
 * anything from about 1.4 to 1.9 separates the same two populations.
 */
export const IDENTITY_SIGMA_DRIFT_MAX = 1.6;

export function createIdentityWatch(): IdentityWatch {
  return {
    armed: false,
    reference: NaN,
    referenceSigma: NaN,
    learning: [],
    learningSigma: [],
    recent: [],
    strikes: 0,
    asked: 0,
    struck: 0,
    acquitted: 0,
    convictions: 0,
    recalibrations: 0,
  };
}

/** Median of a short array. Copies — these are at most 12 long. */
function median(values: readonly number[]): number {
  if (values.length === 0) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Does this frame get a vote?
 *
 * Exported because the app reports the qualifying rate in diagnostics: on the
 * shipped v1 fixture the equivalent question was asked on 46% of frames, and
 * knowing whether that number is 46 or 4 is the difference between a live
 * predicate and a dead one.
 */
export function qualifies(obs: IdentityObservation): boolean {
  if (!obs.solved) return false;
  if (!Number.isFinite(obs.varianceFactor) || obs.varianceFactor <= 0) return false;
  if (obs.correspondences < IDENTITY_MIN_CORRESPONDENCES) return false;
  if (!Number.isFinite(obs.meanSigmaPx) || obs.meanSigmaPx <= 0) return false;
  // A missing euler is not a frontal frame — it is an unknown one.
  if (!Number.isFinite(obs.yawRad) || !Number.isFinite(obs.pitchRad)) return false;
  const deg = 180 / Math.PI;
  if (Math.abs(obs.yawRad) * deg > IDENTITY_MAX_YAW_DEG) return false;
  if (Math.abs(obs.pitchRad) * deg > IDENTITY_MAX_PITCH_DEG) return false;
  return true;
}

/**
 * One frame's worth of watching.
 *
 * A non-qualifying frame returns `'abstain'` and changes NOTHING — it neither
 * strikes nor acquits. That is v1's structure and it matters: the "window" the
 * streak lives in is consecutive *asked* frames, not consecutive frames, so a
 * blink, a hand, or a hard turn can neither build a streak nor break one. They
 * are invisible to it.
 */
export function observeIdentity(
  watch: IdentityWatch, obs: IdentityObservation,
): IdentityVerdict {
  if (!watch.armed) return 'abstain';
  if (!qualifies(obs)) return 'abstain';

  // Learning the wearer's own reading. Cannot convict, by construction.
  if (!Number.isFinite(watch.reference)) {
    watch.learning.push(obs.varianceFactor);
    watch.learningSigma.push(obs.meanSigmaPx);
    if (watch.learning.length < IDENTITY_REFERENCE_FRAMES) return 'learning';
    watch.reference = median(watch.learning);
    watch.referenceSigma = median(watch.learningSigma);
    watch.learning.length = 0;
    watch.learningSigma.length = 0;
    return 'learning';
  }

  // **Did the ruler change, or did the face?**
  //
  // Both raise `varianceFactor` and only one of them is this module's business.
  // The claimed sigma is the statistic's denominator and it is observable, so
  // the two are separable — measured, an identity change moves the mean claimed
  // sigma by at most 1.35x while the drifts that break the watch move it 2-4x.
  //
  // When the ruler moved, the reference is on the wrong scale and everything
  // measured against it is meaningless. Learn it again rather than judging:
  // abstaining instead would be safe for one session and would kill the feature
  // permanently, because a drifted estimator does not drift back.
  const sigmaScale = obs.meanSigmaPx / watch.referenceSigma;
  if (sigmaScale > IDENTITY_SIGMA_DRIFT_MAX || sigmaScale < 1 / IDENTITY_SIGMA_DRIFT_MAX) {
    watch.recalibrations++;
    watch.reference = NaN;
    watch.referenceSigma = NaN;
    watch.learning.length = 0;
    watch.learningSigma.length = 0;
    watch.recent.length = 0;
    watch.strikes = 0;
    return 'recalibrating';
  }

  watch.asked++;
  watch.recent.push(obs.varianceFactor);
  while (watch.recent.length > IDENTITY_WINDOW) watch.recent.shift();
  // The window has to be full before it is a median rather than a sample.
  if (watch.recent.length < IDENTITY_WINDOW) return 'same';

  const over = median(watch.recent) > watch.reference * IDENTITY_VF_RATIO;
  if (!over) {
    if (watch.strikes > 0) watch.acquitted++;
    watch.strikes = 0;
    return 'same';
  }

  watch.struck++;
  watch.strikes++;
  if (watch.strikes < IDENTITY_STRIKES) return 'same';

  watch.strikes = 0;
  watch.convictions++;
  // The window goes too. Whatever comes next is a different person's readings
  // and must not be medianed together with the evidence that convicted.
  watch.recent.length = 0;
  return 'changed';
}

/**
 * Forgets the wearer, keeps the counters.
 *
 * Called after a conviction and after any deliberate reset. The lifetime counts
 * survive on purpose — v1's rule, and the sharpest small idea in that tree: *a
 * counter that resets with the thing it counts cannot report the reset.*
 * `convictions` is what tells a paste from a live session whether this mechanism
 * has ever fired, and it would read 0 forever if a conviction cleared it.
 */
export function armWearer(watch: IdentityWatch): void {
  forgetWearer(watch);
  watch.armed = true;
}

export function forgetWearer(watch: IdentityWatch): void {
  watch.armed = false;
  watch.reference = NaN;
  watch.referenceSigma = NaN;
  watch.learning.length = 0;
  watch.learningSigma.length = 0;
  watch.recent.length = 0;
  watch.strikes = 0;
}
