/**
 * One frame of tracking, turned into a posed head and a placed frame.
 *
 * This is the per-frame half of the pipeline, kept apart from `main.js` on purpose.
 * `main.js` owns the browser — the animation loop, the controls, the readouts —
 * and none of that can run in a headless or hidden tab. The arithmetic that
 * actually decides where the glasses go can, so it lives here and the test harness
 * drives exactly this function rather than a reimplementation of it.
 */

import * as THREE from 'three';
import { solvePlacement, pupilHeightInLens, pupilVerdict, widthVerdict } from './fit.js';
import { measureAnchors, canonicalAnchors, clampAnchors, medianAnchors } from './anchors.js';
import { LM } from './canonical-face.js';
import { aimTemples, resetTemples, fadeTemples } from './temples.js';
import { updateOccluder, headProfileFor, surfaceOf } from './occluder.js';
import { estimateYaw } from './tracker.js';
import { createPersonModel, W_MAX } from './person.js';
import { createPoseFit } from './pose-fit.js';
import { solveRestConfiguration } from './seat-equilibrium.js';
import { normalAt } from './nose.js';

/**
 * How much a measurement taken at this head pose is worth, as three ramps — one
 * per axis of turn, in radians of TRUE pose euler, each falling smoothly from 1
 * to 0 across its band.
 *
 * This replaced a binary gate, and the reasoning the gate carried is still the
 * reasoning here: foreshortening eats the temple span as the head turns, and the
 * anchor recovery borrows each landmark's canonical depth, so a pitched or rolled
 * head puts every borrowed depth at the wrong height — the ear-top landmarks
 * worst of all, being the furthest from the face's centre of rotation. What
 * changed is the *shape* of the answer. A gate says a measurement at 14° is
 * perfect and one at 16° is worthless, and both halves of that are lies; the ramp
 * says what the errors actually do, which is grow smoothly with the turn.
 *
 * The gate's real failure was measured on the user's own captures: he browses
 * supine on a pillow (yaw to 18°, pitch to 16°, roll from the pillow itself), and
 * the gate — driven by an image-asymmetry yaw estimate that conflates pillow roll
 * with turn — stayed closed on half of them. Closed, the fit never measured his
 * nose at all: `noseWidthRatio` sat pinned at 1.000, the average nose, for whole
 * sessions (the diagnosis's scan-cause 3). A ramp has no closed; it only weighs.
 *
 * The numbers, and where each comes from:
 *
 *  - **Ramp starts sit at today's effective admit thresholds**, so frontal
 *    behaviour is unchanged: yaw 0.17 rad ≈ the old image-asymmetry gate's 0.25
 *    (which corresponds to ~0.157 rad of true yaw, plus the old latch's 15%
 *    hysteresis band the chatter lived in); pitch 0.20 and roll 0.25 sit just
 *    inside the old 0.26 / 0.30 limits, where the old gate was already admitting
 *    every real frontal session.
 *  - **Ramp ends are chosen so trust never hits a hard zero inside the poses a
 *    live wearer actually visits.** The first sizing used the diag captures
 *    (yaw to 18°) and ended the yaw ramp at 0.45 rad — and the first live
 *    session (stage 6, 2026-08-16) promptly held 31° of yaw, read w = 0, and
 *    every protection that scales with trust switched off at once: no samples,
 *    a tripwire fed hallucinated residuals, and the person model bled out at
 *    the exact pose the user calls "difficult angles". The tails now cover the
 *    live regime — yaw to 0.70 rad (40°), pitch to 0.60 (34°) — so a hard pose
 *    is a weak measurement, never a free-fire zone. w at the measured 31° hold:
 *    ≈ 0.25, enough to keep the estimate defended and honestly down-weighted.
 */
const POSE_TRUST = {
  yaw: [0.17, 0.70],
  pitch: [0.20, 0.60],
  roll: [0.25, 0.60],
};

/**
 * Below this much combined trust, a sample is not worth a slot in the window.
 *
 * Not a re-invented gate: at 0.05 the ramps have already said "this measurement
 * is one twentieth of a frontal one", and admitting it would spend one of 31
 * slots — a thirtieth of the estimate's memory — on a reading the weighted
 * median would move micrometres for. The floor throws away nothing that could
 * have mattered; it only refuses to let dead weight age out live samples.
 */
const POSE_TRUST_ADMIT = 0.05;

/**
 * Above this much combined trust, the pose is a confident measurement pose.
 *
 * One consumer inherited from the latch this replaced (graft G12): the
 * `measuringLatch` readout survives as this derived alias so the UI and the
 * harness keep a boolean to show.
 */
const POSE_TRUST_CONFIDENT = 0.5;

/**
 * The identity question is asked only above THIS trust, and only convicts on a
 * streak. 0.5 was not enough: at w ≈ 0.5–0.7 (a 15–20° turn) the measured
 * proportions still carry percent-scale foreshortening and landmark slide, and
 * the first live session reset the person model NINE times in four minutes —
 * each one a converged estimate thrown away because one half-trusted sample
 * read 12% off. So: near-frontal only (0.8), and a single reading convicts
 * nothing — it takes IDENTITY_STRIKES consecutive disagreeing confident
 * samples, and any agreeing one acquits. A real face swap still resets within
 * a fraction of a second of the new wearer facing the camera; a turn no longer
 * can.
 */
const POSE_TRUST_IDENTITY = 0.8;
export const IDENTITY_STRIKES = 5;

/**
 * The standoff is a face constant, and this is the only pose allowed to teach
 * it — the wearer's "the glasses are being pushed forward for no reason" at
 * >40° of yaw, third and final attempt (2026-08-17).
 *
 * The first two attempts failed for the same unexamined reason. Freezing the
 * channel at low trust locked in an inflation that had already happened;
 * admitting readings weighted by pose trust let them in anyway. The
 * z-decomposition says why, and it is the whole answer: **the surface law is
 * already wrong at 10–20° of yaw, where the trust ramps still read 0.87–1.0**
 * (+1.7 to +3.5 mm of fictional interference, measured). A weight cannot
 * discount a reading that arrives at full weight. So the seat stops asking the
 * ramps — which grade *landmark* trust — and asks the only question that
 * matters for a two-pad bearing: is this head square enough to the camera that
 * BOTH nasal sidewalls are genuinely seen? That is `w = 1`: inside every ramp's
 * start (yaw 9.7°, pitch 11.5°, roll 14.3°), the pose the trust model itself
 * rates as carrying no discount at all.
 *
 * Outside it the standoff does not ease, does not re-solve, and is not guarded
 * against — it is simply the number the last square-on look measured. A head
 * turning cannot change how far a frame sits off the nose it rests on, so a
 * pipeline that lets a turn change it is not modelling anything real.
 *
 * **The COMBINED trust `w = wy·wp·wr`, and a yaw-only gate was tried and
 * measured worse.** The reasoning for yaw-only is seductive and wrong: only
 * yaw hides a nasal sidewall, so surely pitch and roll may keep teaching. The
 * replay says otherwise — gating on `wy` alone recovered pitch (excursion
 * 2.44 → 0.69 mm) and took the wearer's actual complaint backwards, the push
 * past 40° of yaw going +0.51 → −1.43 mm under the combined gate but +0.91
 * under yaw-only, i.e. WORSE than before any of this work. Pitch does not
 * occlude a sidewall but it does degrade the reconstruction, so a yaw-only
 * gate admits inflated readings from pitched frames and then carries them
 * into the turn. Occlusion is not the only way a surface reading goes bad.
 *
 * The residual cost is honest and recorded: pitch excursion 0.00 → 2.44 mm and
 * browse 1.17 → 2.79 mm, the standoff going stale while off-square and the
 * guard bridging it (3 → 13 pushes). The wearer validated this trade live
 * ("much better") on the axis they raised; the pitch tail is the open item.
 *
 * 0.999 rather than 1.0 only because `w` is a product of three smoothsteps and
 * float equality on it would be a coin toss at the boundary.
 */
const SEAT_REF_TRUST = 0.999;

/** Hermite smoothstep, rising 0 → 1 across [edge0, edge1]. */
function smoothstep(edge0, edge1, x) {
  const t = Math.min(Math.max((x - edge0) / (edge1 - edge0), 0), 1);
  return t * t * (3 - 2 * t);
}

/**
 * The anchors' weighted median (see `medianAnchors`), for one scalar: sort by
 * value, walk the cumulative weight, take the first value at or past half the
 * total, and mean the straddling pair on an exact tie — the generalisation
 * that reproduces the plain median bit for bit under equal weights. The
 * seat's carried raw-law estimate stands on it (2026-08-17); same law, same
 * tie tolerance, so the two windows can never disagree about what "median"
 * means.
 */
function weightedScalarMedian(samples) {
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
 * The gaze-hardened admission band (anchoring-v3, 2026-08-17 — the measured
 * dominant gaze door). MediaPipe's landmarks follow the EYES: on a held-still
 * head, pure gaze swung the bridge landmark 2.3 mm mean / 4 mm peaks
 * (upstream: google/mediapipe issue #1786) — and the telemetry attribution run
 * measured where that actually reached the screen. Not through the pin (its
 * innovation was worth 0.03 px and is deleted); through ADMISSION: the
 * iris-derived `metricScale` misread by 12–17.8% under deliberate gaze while
 * `widthRatio` held ≤ 1.2%, every one of the fixture's 40 identity strikes was
 * driven by metricScale alone, 4 convictions dumped the converged model
 * mid-eye-circles, the carried metricScale spanned 27.7%, and the carried
 * eyeLineY walked 3.72 mm — each conviction worth more screen motion than the
 * entire pin path. The iris is the pipeline's one absolute ruler, and under
 * off-neutral gaze the ruler is bent.
 *
 * So the gaze signal now guards the DOOR, not the pin: while the wearer's gaze
 * sits further than GAZE_ADMIT from its neutral reference, no measurement
 * sample is admitted to the window at all — not the bent metricScale, not the
 * gaze-displaced bridge/eyeLineY, none of it — and the identity question is
 * not asked (strikes only ever accumulate from neutral-gaze samples; a
 * predicate must not convict on the quantity gaze corrupts most). "Keep the
 * previous estimate, never assume average" is the pipeline's existing
 * semantics for a refused reading, and a carried face-space constant is
 * exactly what should stand while the eyes wander. The neutral reference is a
 * slow EMA (GAZE_NEUTRAL_TAU) so a wearer's resting gaze, whatever it is,
 * reads as zero.
 *
 * GAZE_ADMIT inherits the stage-6 gate's live calibration verbatim (the gate
 * itself is deleted; its measurement survives): eyes-still delta idled at
 * 0.042 mean / 0.050 max, deliberate glances read 0.168 mean / 0.275 max, and
 * 0.055 false-refused 9% of an eyes-still hold — so the bar sits at 0.08,
 * double the still ceiling and half the glance mean. One measured band, no
 * new thresholds: every consumer of the gaze signal reads this same number.
 *
 * Unlike the deleted pin freeze, admission is refused at ANY head speed — the
 * ruler is bent whether or not the head moves (the browse-segment strikes
 * proved it), and refusing a sample has no pose-dependent cost the way
 * freezing a position did.
 */
const GAZE_ADMIT = 0.08;
const GAZE_NEUTRAL_TAU = 10;

/**
 * How quickly the applied nose standoff follows its solved target, as a time
 * constant in seconds.
 *
 * The seat is a face-space quantity on a rigid face: on a still OR moving head it is
 * very nearly constant, and almost everything it does frame to frame is measurement
 * noise — borrowed-depth error at the bridge, eye-line drift sliding the contact
 * column across the nose, the occluder's own rebuild steps. Unfiltered, that noise
 * is the frame visibly breathing in and out of the face whenever the head tilts;
 * the old 1.0 cm clamp used to hide it by simply railing. What is real in the
 * signal — a new pair, a re-measured face — arrives through resets, which clear
 * this hold entirely.
 */
const SEAT_TAU = 0.15;

/**
 * The standoff channel's effect deadband, cm: the ease only re-arms when its
 * target has moved past `ZETA_REARM` from the value it is holding, and goes
 * back to holding once the eased value lands within `ZETA_RELEASE` of the
 * target. Between those, the seat literally RESTS — zero movement, not small
 * movement.
 *
 * 0.15 mm of re-arm is 0.026 px at 45 cm (1.74 px/mm) — invisible — and more
 * than twice the measured 0.02–0.08 mm noise of the eased push, so noise can
 * never wake the channel. The 0.05 mm release is the hysteresis: one
 * threshold would chatter exactly at its own edge, which is the latch lesson
 * this codebase already paid for once.
 */
const ZETA_REARM = 0.015;
const ZETA_RELEASE = 0.005;

/**
 * The resting-height channel: its ease time and its output deadband (G1).
 *
 * The height is a face×frame constant — where THIS frame rests on THIS nose —
 * so it moves on the slowest visible timescale there is: 0.8 s makes any
 * correction read as the frame settling into place, not twitching. The 0.3 mm
 * deadband is on the OUTPUT (the applied value holds until the target has
 * genuinely left its neighbourhood), which is what retires the threshold-
 * riding concern: a target dithering around the band's edge moves the applied
 * height not at all, because the hold is on the distance between them, not on
 * the target's own noise. 0.3 mm matches the depth field's cell-scale noise
 * on the queries the solve reads — below it the solver is reporting texture,
 * not anatomy.
 */
const REST_TAU = 0.8;
const REST_DEADBAND = 0.03;

/**
 * The G5 roll channel's ease, seconds. Slower than the standoff (a roll reads
 * as the frame cocking on the face — it should settle, not snap) and faster
 * than the height (it is bounded ±3° and its solve is already an increment
 * toward balance, so long memory buys nothing).
 */
const PHI_TAU = 0.5;

/**
 * The solve-event cadence (graft G13): at most one equilibrium solve per
 * 500 ms — the spec's ≤ 2 Hz event-work invariant, enforced by the number
 * that IS the cap rather than assumed of the edges. It has to be, because
 * the edges alone do not deliver it: the surface-rebuild trigger rides
 * stage 4's rebuild rate (1–5 per 60 frames) and a converged session was
 * MEASURED sustaining 3.75 Hz at the original 250 ms floor — the refractory
 * was the only thing standing between the edges and the invariant, and it
 * stood in the wrong place. The solve is ~0.3 ms of contact passes, so even
 * at the cap this is under 0.2% of the frame budget; and at least one solve
 * per 5 s, because every trigger below is edge-detected and an edge can be
 * missed (a fit slider dragged during a dropout, a surface that converged
 * without ever crossing a bucket). The heartbeat turns "the scheduler
 * missed it" into "the scheduler is 5 s late", and the extra 250 ms an edge
 * can now wait is a twentieth of that — both invisible under the deadbands.
 */
const SOLVE_MIN_INTERVAL = 0.5;
const SOLVE_HEARTBEAT = 5;

/**
 * G1's confidence scale: the solved resting height is applied scaled by
 * `clamp(noseMeanW / 50, 0, 1)`. 50 unit-weight frames is ~2 s of decent
 * frontal observation — enough that the surface under the pads is this
 * wearer's nose rather than the canonical prior the session woke up with. A
 * cold session therefore holds today's optical height BY CONSTRUCTION (the
 * scale is zero, not merely small), and the height eases in only as fast as
 * the evidence for it accumulates.
 */
const CONF_FULL_W = 50;

/**
 * The G13 confidence-crossing thresholds, on noseMeanW normalised by the
 * person model's own W_MAX cap: each crossing is "the surface got a grade
 * better", which is exactly when a re-solve can move the equilibrium by more
 * than a deadband.
 */
const CONF_BUCKETS = [0.25, 0.5, 0.75];

/**
 * How many recent head-on samples the fit estimate stands on.
 *
 * **There is no scan phase and no lock.** There used to be: 45 head-on samples had
 * to accumulate before the fit was declared final, with a "hold still, measuring
 * your fit… 34%" on the status bar throughout and a full restart after every half
 * second the face went missing. That was the single largest thing wrong with this
 * app, and it was a self-inflicted wound — nobody who ships this ships that.
 * Fittingbox's own start gate is ten stabilisation frames; Ditto's patent puts the
 * sufficient sample at "on the order of 10 frames"; WebAR.rocks carries no per-user
 * state at all and solves against a hard-coded canonical face from frame one.
 *
 * Nothing required the gate in the first place. MediaPipe's transform is a
 * *stateless* closed-form Procrustes fit — every quantity the scan was collecting is
 * already available, at full quality, on the very first detection. What 45 samples
 * bought over 15 was a standard error of 0.187σ against 0.324σ: three times the wait
 * for 1.7× the precision, on a quantity whose residual is dominated by bias that no
 * amount of averaging can touch.
 *
 * So the window is bounded and the estimate simply runs, for the whole session.
 * Robustness still comes from the median — one blink or one hand across the face
 * cannot move it — and the *stability* the lock used to provide comes from the
 * deadband in `commitFit` instead, which is the part that actually mattered: a
 * measurement that rides on the landmarks does creep as the head pitches, and the
 * deadband refuses to act on a creep too small to see. The difference is that a
 * deadband has no cliff, needs no gate, and cannot restart.
 */
const FIT_WINDOW = 31;

/**
 * How far a field has to move before the applied fit follows it.
 *
 * This is what replaced the lock, and it is doing the lock's real job. Left with no
 * threshold at all, the estimate tracks every millimetre of landmark noise and the
 * frame breathes on the face — which is what the lock was protecting against and
 * why it was written. Given a threshold, the first few samples move the estimate
 * freely (the differences are large), and within about a second the median has
 * converged inside the band and stops moving it at all. Asymptotically a lock, with
 * no moment at which anything is declared finished.
 *
 * Sized as "smaller than anybody can see, larger than the noise": half a millimetre
 * on a 155 mm temple span, two tenths on a 23 mm nose, one percent of head size.
 */
const FIT_DEADBAND = {
  templeWidth: 0.05,
  noseWidth: 0.02,
  metricScale: 0.01,
  /**
   * The eye line joined the deadbanded set when it became always-carried (design
   * C4): as a live per-frame value it needed no deadband — it already moved every
   * frame by design — but a carried proportion that the median nudges a few
   * hundredths of a millimetre per admitted sample is exactly the creep the other
   * fields' deadbands exist to refuse. Sized like the nose width's: 0.2 mm is
   * smaller than anybody can see (0.035 px at 45 cm) and larger than the
   * converged median's frame-to-frame noise, so the eye line rests between real
   * changes the way the widths do.
   */
  eyeLineY: 0.02,
};

/**
 * How different a returning face has to look before its fit is thrown away.
 *
 * The old rule was a timer — half a second without a face and the whole measurement
 * went in the bin — and it was the reason the app felt like it was *perpetually*
 * re-stationing: every turn past the yaw gate, every hand across the face, every
 * blink the detector happened to miss, cost another full scan. Fittingbox's patented
 * recovery state does the opposite, and it is obviously right: on losing tracking
 * they re-localise the *pose* against a keyframe bank and never re-measure the
 * wearer's morphology.
 *
 * The thing the timer was actually guarding — someone else sitting down in the
 * chair — is a question about the face, so it is now asked of the face. A returning
 * head whose proportions land this far from the estimate is a different person and
 * gets a fresh window; one that matches keeps its fit and resumes instantly.
 *
 * Well outside per-frame measurement noise (a couple of percent) and well inside the
 * gap between two adults (they differ by tens of percent), so it separates the two
 * cases it exists to separate and is not asked to do anything finer.
 */
const IDENTITY_TOLERANCE = 0.12;

const rawPosition = new THREE.Vector3();
const rawQuaternion = new THREE.Quaternion();
const rawScale = new THREE.Vector3();
const poseMatrix = new THREE.Matrix4();
/** The raw pose's world matrix, held while the node is recomposed with the smoothed one. */
const rawHeadWorld = new THREE.Matrix4();
const poseEuler = new THREE.Euler();
const scratchVector = new THREE.Vector3();
const scratchQuaternion = new THREE.Quaternion();
const rawPositionArray = [0, 0, 0];
const rawQuaternionArray = [0, 0, 0, 1];
/** Candidate B's blended pose, before it replaces the raw one. */
const fitPosition = new THREE.Vector3();
const fitQuaternion = new THREE.Quaternion();
const scaleTriple = new THREE.Vector3();
const bufferSize = new THREE.Vector2();

/**
 * How long without a face before the *pose filter* is thrown away, in seconds.
 *
 * Only the filter. The fit survives — see `IDENTITY_TOLERANCE`. What has to go is
 * the velocity state: the estimator has been carrying a head speed from before the
 * gap, and across half a second that is not a stale estimate but a fictional one,
 * which on re-acquisition throws the frame off the face for as long as it takes to
 * decay. A one-frame dropout is the opposite case — the commonest is a motion-blurred
 * frame mid-turn, exactly where the accumulated velocity is worth the most — so short
 * gaps hold it.
 *
 * In seconds rather than in frames, which is what this used to count. Detection runs
 * on the camera's clock now, and that clock is 30 Hz on one webcam and 60 on the
 * next — so a frame count means half a second on one machine and a quarter on
 * another, for no reason a user could ever see.
 */
export const LOST_SECONDS_BEFORE_RESET = 0.5;

/**
 * A fresh seat state — the three eased channels, the last solve, and the
 * event scheduler's edge detectors (stage 5, replacing the scalar
 * `state.seatPush`).
 *
 * Cleared whole by `resetFit` and by a model swap, exactly as the old scalar
 * was: a new face or a new pair starts from nothing and its first solve is
 * adopted whole, which is what keeps frame one of any session a fit.
 */
function createSeatState() {
  return {
    /** What `solvePlacement` applies this frame: vertical resting height (cm),
     * standoff (cm), pad-balance roll (rad) — plus `needRef`, the carried
     * raw-law estimate the guard baselines on (see `needEst` below; null
     * until it has a first reading, and the guard falls back to the raw
     * law). */
    applied: { s: 0, zeta: 0, phi: 0, needRef: null },
    /**
     * The carried estimate of the raw standoff law — the seat's surface
     * reference, admitted at trust (2026-08-17, the ">40° forward push" fix).
     *
     * The z-decomposition measured the push end to end: at >40° of yaw the
     * view-locked surface morphs under the seat (degraded landmarks — the
     * synthetic control is pose-invariant to 0.01 mm), the raw law reads the
     * morph as interference (+3.0 to +6.3 mm over frontal, rising from 10°),
     * and the chain transmitted it whole — ζ eased into it while w ≥ 0.3,
     * then the guard bridged frozen-ζ to the morph every frame, capped,
     * overflowing 28–54 times a segment. wPose hits 0 at 40° of yaw, so
     * every trust-gated protection was already in its frozen state — the
     * push was precisely the un-gated remainder.
     *
     * So the raw law now goes through the SAME grammar the anchors ride —
     * literally, not analogously: a bounded window (FIT_WINDOW — the same
     * 31), each reading admitted at weight `wPose` (refused whole below
     * POSE_TRUST_ADMIT, like a sample that never earns a slot), eviction by
     * lowest weight with oldest-among-equals, and the weighted median as
     * the estimate. The eviction rule is the load-bearing part, and a
     * mass-weighted EMA was tried first and measured leaking on the ab
     * fixture before this window replaced it: a 95-frame dwell at w ≈ 0.89
     * (the 10–20° band, where the morph already reads +2.4 mm) walked an
     * exponential estimate ~80% of the way up the inflation, because an EMA
     * forgets in proportion to incoming weight. The window cannot be
     * flooded by construction — a full window of frontal w = 1 samples
     * evicts every lower-weight arrival, so no dwell at any partial trust,
     * however long, replaces the trustworthy mass — while genuine change at
     * full trust turns the window over in ~a second, exactly as the fit
     * fields do. Cold adopts the first admitted reading whole (the
     * median-of-one), which keeps frame one a fit.
     *
     * Consumers (both through `applied.needRef`): the ζ channel's target
     * AND the guard's baseline are the carried estimate itself — the
     * standoff reference is a face-space constant at every pose, exactly
     * the diagnosis's sentence. A per-frame blend `ref + w·(raw − ref)` was
     * tried between the two (it kept the raw reading in charge at w = 1)
     * and the ab fixture measured it re-transmitting the morph: the 10–20°
     * band reads w ≈ 0.89 while its surface already carries +2.4 mm of
     * view-morph, so "89% admitted" was the push with one decimal shaved.
     * The window median is what refuses that band — its samples enter and
     * are evicted against the frontal w = 1 mass — and the per-frame
     * reading's whole authority now lives where it is admitted: in the
     * window. Converged frontal behaviour is preserved through the channel
     * deadbands (the median of full-trust readings sits within the noise
     * the deadbands already refuse to act on). No new thresholds: wPose,
     * POSE_TRUST_ADMIT, FIT_WINDOW, and the deadbands it flows through.
     */
    needEst: { samples: [], value: null, weight: 0 },
    /** Whether any equilibrium solve has been adopted yet — before one, the
     * placement seats standalone (the softened raw law), which IS the
     * pre-solve behaviour and the zero-state every reset returns to. */
    hasSolve: false,
    solve: null,
    mode: 'cold',
    /** What the last solve reported, split from `mode` so a 'hold' verdict
     * can show without discarding the constants it froze. */
    lastMode: 'cold',
    sStar: 0,
    phiStar: 0,
    zetaAt: null,
    conf: 0,
    /** Last frame's raw standoff law at the applied placement — the zeta
     * channel's per-frame target. */
    rawNeeded: null,
    /** The standoff channel's effect-deadband latch. */
    zetaHolding: false,
    zetaHeld: 0,
    sSettling: false,
    /** Scheduler state: elapsed-since-solve clock, pending-event latch, and
     * one edge detector per G13 trigger. */
    sinceSolve: Infinity,
    pending: false,
    /** Latched while "Rest on the nose" is off, so re-enabling it is an
     * immediate solve edge rather than a wait for the heartbeat. */
    toggledOff: false,
    confBucket: -1,
    anchorsSig: null,
    fitSig: null,
    modelRef: null,
    rebuildsSeen: -1,
    /** Counters for `__ar.seat` and the harness. */
    solves: 0,
    holds: 0,
    guardPushes: 0,
    /** Solve attempts refused for pose trust below the stage-6 bar —
     * counted apart from `holds` so the replay can tell "the field said
     * hold" from "the pose was not asked". Monotone, like the rest. */
    refusals: 0,
  };
}

/**
 * Advances the three eased channels one frame (stage 5's plumbing, G1).
 *
 * The order of operations IS the design: confidence scales the height target,
 * the height's output deadband decides whether the applied height moves at
 * all, and the standoff target is then read off the solve's own sweep AT THE
 * APPLIED HEIGHT — so the two channels can never disagree about where the
 * frame is, only about how fast they get there. The roll runs behind its
 * flag and pins to exactly zero when dark, which keeps the dark path
 * bit-identical to a build without the feature.
 */
function easeSeatChannels(seat, person, fit, dt, wPose) {
  seat.sinceSolve += Math.max(dt, 0);
  seat.conf = person
    ? Math.min(Math.max(person.noseMeanW / CONF_FULL_W, 0), 1) : 0;

  // The carried raw-law estimate (see `needEst` on the state): last frame's
  // reading — the same one-frame staleness the ζ target always had — admitted
  // at this frame's trust into the bounded window, weighted-median out. Below
  // the admission floor the reading is refused outright, exactly as a sample
  // below POSE_TRUST_ADMIT never earns a slot: the carried value stands,
  // "keep previous, never assume average". Eviction is the anchors' rule
  // verbatim — lowest weight goes, the OLDEST among equals — which is what
  // makes the reference floodproof (see the state comment). Published onto
  // `applied` so `solvePlacement`'s guard answers penetration against the
  // same reference the ζ channel settles on — one reference, two consumers,
  // no second grammar.
  // Square-on only (see SEAT_REF_TRUST). The weighted-median grammar below is
  // kept — it still refuses noise and a single freak reading — but the gate in
  // front of it is now categorical, because the failure it exists to stop
  // arrives at full weight.
  const seatSquareOn = (wPose ?? 1) >= SEAT_REF_TRUST;
  if (seat.rawNeeded !== null && seatSquareOn) {
    const est = seat.needEst;
    est.samples.push({ v: seat.rawNeeded, w: wPose });
    if (est.samples.length > FIT_WINDOW) {
      let evict = 0;
      for (let i = 1; i < est.samples.length; i++) {
        if (est.samples[i].w < est.samples[evict].w) evict = i;
      }
      est.samples.splice(evict, 1);
    }
    est.value = weightedScalarMedian(est.samples);
    est.weight = est.samples.reduce((a, s) => a + s.w, 0);
  }
  seat.applied.needRef = seat.needEst.value;
  // Published for `solvePlacement`'s guard: penetration is only answerable
  // from a pose that can actually see the contact (see SEAT_REF_TRUST).
  seat.applied.squareOn = seatSquareOn;

  if (!seat.hasSolve) return;

  const alpha = (tau) => 1 - Math.exp(-Math.max(dt, 0) / tau);

  // The resting height latches on exactly the same argument as the standoff
  // (SEAT_REF_TRUST), and it needs saying because the solve gate alone did not
  // cover it: `sTarget` is `conf · sStar`, and `conf` climbs as the person
  // model accumulates — so the height kept easing DOWN THE WEDGE at any pose,
  // with no new solve involved, and the wedge has a forward component. The
  // z-decomposition caught it as the last term standing at >40° once ζ and the
  // guard were latched (dRest +3.06 mm on the yaw sweep's 40+ bucket, against
  // +1.57 frontal in the same segment). Where the frame rests on the nose is a
  // face constant; turning cannot slide it. The confidence ramp simply pauses
  // while off-square and resumes on the next square-on look.
  const sTarget = seat.conf * seat.sStar;
  if (seatSquareOn && Math.abs(sTarget - seat.applied.s) > REST_DEADBAND) {
    seat.applied.s += (sTarget - seat.applied.s) * alpha(REST_TAU);
    seat.sSettling = true;
  } else {
    seat.sSettling = false;
  }

  // The standoff channel's target is the PER-FRAME raw law at the applied
  // placement (one frame stale — the guard pass measured it during the last
  // placement), through the effect deadband. Per stability-first's own
  // timescale assignment: the standoff is "per-frame evaluation, event-scale
  // target" — the solve's table anchors the first adoption and keeps the
  // height/standoff pair consistent, but between solves the SKIN is the
  // authority, or a sweep that warps the surface leaves the frame floating
  // on a stale table until the heartbeat (measured: up to 1.6 mm of float
  // across a ±30° sweep when the target rode the table). The deadband is
  // what keeps the noise out, not the staleness.
  //
  // The target is the CARRIED estimate, not the raw reading (2026-08-17 —
  // see `needEst`): the standoff is a face-space constant, and the ease's
  // job is to settle onto the estimate of that constant, which only moves
  // when admitted evidence moves the window's median. Converged frontal
  // behaviour is preserved through the effect deadband — the median of
  // full-trust readings sits inside the noise the deadband already refuses.
  //
  // The stage-6 freeze below w = 0.3 is RETIRED by the same measurement that
  // put it there ("pushed forward in a weird way", found by the wearer
  // live). The freeze answered a target that was the raw morph — holding was
  // strictly better than following. The z-decomposition then measured what
  // the freeze could not cover: ζ absorbed +0.6 to +3.6 mm of view-morph in
  // the w 0.3–0.9 band BEFORE the freeze engaged, and the freeze then LOCKED
  // that inflation through the whole >40° regime (dζ +2.1 to +2.6 mm on the
  // ab fixture's yaw segment, frozen). With the carried target the low-trust
  // ease is not a hazard but the recovery: past 40° the target is the
  // wearer's own frontal reference, so the standoff settles back onto it —
  // sub-pixel motion (≈2.5 mm over SEAT_TAU at 45 cm ≈ 0.4 px), through the
  // same deadband as ever.
  //
  // NOT additionally frozen under off-neutral gaze, and that was measured
  // before it was believed (anchoring-v3 landing): the R0 gazeInjection run
  // suggested the seat stack rides the gaze-morphed surface, so a
  // gaze-holding ζ was tried against the real fixture — it changed the
  // eye-circles and glances RMS by nothing measurable (the real seat spans
  // during gaze run sub-millimetre) and traded the eased ζ for raw capped
  // guard pushes (17 → 172 on the glances segment). The injector's
  // full-amplitude synthetic morph remains a recorded residual exposure;
  // the live segments say the seat is not the gaze path, so the ease keeps
  // following the skin — at its admitted worth.
  // LATCHED off-square (see SEAT_REF_TRUST). Not "eased more slowly", not
  // "frozen at a bar chosen to be safe" — simply not touched. The number the
  // last square-on look measured is the number a turned head wears, because
  // that is what a pair of glasses does.
  if (seatSquareOn) {
    const est = seat.needEst;
    const zetaTarget = est.value !== null
      ? est.value
      : (seat.rawNeeded ?? seat.zetaAt(seat.applied.s));
    if (seat.zetaHolding && Math.abs(zetaTarget - seat.zetaHeld) > ZETA_REARM) {
      seat.zetaHolding = false;
    }
    if (!seat.zetaHolding) {
      seat.applied.zeta += (zetaTarget - seat.applied.zeta) * alpha(SEAT_TAU);
      if (Math.abs(seat.applied.zeta - zetaTarget) <= ZETA_RELEASE) {
        seat.zetaHolding = true;
        seat.zetaHeld = zetaTarget;
      }
    }
  }

  if (fit.padBalance === true) {
    seat.applied.phi += ((seat.phiStar ?? 0) - seat.applied.phi) * alpha(PHI_TAU);
  } else {
    seat.applied.phi = 0;
  }
}

/**
 * The G13 event scheduler: decides whether this frame re-solves the resting
 * configuration, runs the solve, and adopts (or refuses) its answer.
 *
 * Every trigger is an EDGE — something that could legitimately move the
 * equilibrium by more than a deadband: the person model's surface confidence
 * crossing a grade boundary, `commitFit` actually adopting a new proportion,
 * the fit controls or the model changing, the surface being rebuilt. Edges
 * are latched into `pending` so an event during the refractory window
 * (`SOLVE_MIN_INTERVAL`) is deferred, never dropped; the 5 s heartbeat
 * catches anything the edges miss. After convergence the triggers still fire — a rebuilt surface, a
 * crossed bucket — and produce solves whose targets land inside the channel
 * deadbands, which is G13's own acceptance test: re-solving must be a no-op
 * when nothing real changed, and the harness measures that rather than
 * trusting it.
 *
 * Returns 'adopted-first' when this was the session's first adopted solve —
 * the caller re-places THIS frame with the adopted channels, so frame one
 * seats exactly as the scalar seat always did (adopted whole, no ease-in
 * from zero).
 */
function scheduleSeatSolve(seat, {
  surface, model, anchors, fit, person, base, rebuilds, wPose = 1,
}) {
  // "Rest on the nose" off is an EDGE, not merely an absence: while the
  // toggle is off no solve runs (and `updateFrame` withholds the channels,
  // so the placement is the pure landmark hang), and the way OUT is latched
  // so the way BACK IN re-solves immediately — the surface kept converging
  // in the meantime, and a re-enable that waited out the 5 s heartbeat
  // would visibly seat the frame on five-second-old constants first.
  if (fit.seatOnNose === false) {
    seat.toggledOff = true;
    return null;
  }
  if (!surface || !model?.noseContacts?.length || !base) return null;

  let due = !seat.hasSolve || seat.toggledOff;
  seat.toggledOff = false;

  const conf = person ? Math.min(Math.max(person.noseMeanW / W_MAX, 0), 1) : 0;
  let bucket = 0;
  for (const edge of CONF_BUCKETS) if (conf >= edge) bucket++;
  if (bucket !== seat.confBucket) {
    due = due || seat.confBucket !== -1;
    seat.confBucket = bucket;
  }

  // The carried proportions the seat actually stands on — the live bridge is
  // deliberately absent (it moves every frame by design and the solve's
  // bridge-relative kernel cancels it anyway).
  const anchorsSig = `${anchors.noseWidth}|${anchors.eyeLineY}|`
    + `${anchors.templeWidth}|${anchors.metricScale}`;
  if (anchorsSig !== seat.anchorsSig) {
    due = due || seat.anchorsSig !== null;
    seat.anchorsSig = anchorsSig;
  }

  const fitSig = `${fit.pantoscopicTilt}|${fit.sizeMultiplier}|${fit.mode}|`
    + `${fit.widthRatio}|${fit.offsetX}|${fit.offsetY}|${fit.alignToPupils}|`
    + `${fit.pupilTarget}|${fit.padBalance}`;
  if (fitSig !== seat.fitSig || model !== seat.modelRef) {
    due = due || seat.fitSig !== null;
    seat.fitSig = fitSig;
    seat.modelRef = model;
  }

  if (rebuilds !== seat.rebuildsSeen) {
    due = due || seat.rebuildsSeen !== -1;
    seat.rebuildsSeen = rebuilds;
  }

  if (seat.sinceSolve >= SOLVE_HEARTBEAT) due = true;

  seat.pending = seat.pending || due;
  if (!seat.pending) return null;
  // The refractory stands on `sinceSolve` ALONE, which a HELD solve also
  // resets: a session that opens on an untrustworthy field (hard yaw from
  // cold — a realistic first frame) retries at the solve cadence, not at
  // 30 Hz. Gated on `hasSolve` instead, the un-adopted state ran the full
  // 9-row sweep every frame for as long as the pose stayed bad. The
  // first-ever attempt is still immediate — `sinceSolve` opens at Infinity.
  if (seat.sinceSolve < SOLVE_MIN_INTERVAL) return null;

  // Refused below the stage-6 trust bar (2026-08-17 — the same 0.3 the ζ
  // freeze stands on, for the same reason): the sweep would read the
  // view-morphed surface as this nose's wedge. The fixture measured it —
  // 29 solves, 0 holds across a hard-yaw segment, each adopting equilibria
  // off the morph and walking the applied height down the fictional wedge
  // (sApplied −0.7 → −4.1 mm at 40°+). The field's own 'hold' verdict never
  // fires on this, because the field checks CONFIDENCE, which a converged
  // session keeps at any pose; what is untrustworthy here is the POSE, so
  // the pose refuses it. `pending` stays latched — the edge is deferred,
  // never dropped, and trust returning re-solves within the refractory —
  // while the clock resets so a parked bad pose retries at the solve
  // cadence, not at 30 Hz (the same argument as the hold path's).
  // The bar is SEAT_REF_TRUST, the same square-on question the reference and
  // the guard now ask (2026-08-17). It was 0.3, and 0.3 was measured letting
  // the solve adopt equilibria off a surface already inflated at 10–20° — the
  // rest channel then walked the frame down a wedge that is not there
  // (+0.3 to +2.1 mm mean, +4.3 worst). One pose teaches the seat; every other
  // pose wears what it taught.
  if ((wPose ?? 1) < SEAT_REF_TRUST) {
    seat.sinceSolve = 0;
    seat.refusals++;
    seat.lastMode = 'hold';
    return 'held';
  }

  seat.pending = false;
  seat.sinceSolve = 0;
  const solve = solveRestConfiguration({
    surface, model, anchors, base, padBalance: fit.padBalance === true,
  });
  seat.solves++;
  seat.lastMode = solve.mode;

  // G3: a hold freezes the applied constants — targets, tables, everything —
  // and lets the guard do the protecting. Nothing is adopted, including on a
  // first solve: a session that OPENS on an untrustworthy field keeps the
  // standalone seat until the field earns a solve.
  if (solve.mode === 'hold') {
    seat.holds++;
    return 'held';
  }

  const first = !seat.hasSolve;
  seat.hasSolve = true;
  seat.solve = solve;
  seat.mode = solve.mode;
  seat.sStar = solve.sStar;
  seat.zetaAt = solve.zetaAt;
  seat.phiStar = solve.phiStar;

  if (first) {
    // Adopted whole, through the channels' own deadbands: the height starts
    // at zero unless the confidence-scaled target has already left the
    // deadband (on a true frame one, confidence arithmetic guarantees it has
    // not — conf ≤ 1/50 and |s*| ≤ 12 mm put the target under 0.24 mm), and
    // the standoff adopts the sweep's answer at that height verbatim, exactly
    // as the scalar seat adopted its first push.
    const sTarget = seat.conf * solve.sStar;
    seat.applied.s = Math.abs(sTarget) > REST_DEADBAND ? sTarget : 0;
    seat.applied.zeta = solve.zetaAt(seat.applied.s);
    seat.zetaHolding = true;
    seat.zetaHeld = seat.applied.zeta;
    seat.applied.phi = fit.padBalance === true ? solve.phiStar : 0;
    return 'adopted-first';
  }
  return 'solved';
}

/**
 * Poses `scene.head` from a detection and places `scene.glasses` on it.
 *
 * Mutates the scene; returns the measurements worth showing.
 */
export function updateFrame({
  scene, face, model, fit, smoother, state, source, detection, dt,
  smoothing = true, adaptToFace = true, temples = null, lead = 0,
  deformOccluder = true, landmarkDepth = true,
  /**
   * Anchoring-v3 instrument (ar/docs/nose-v3/v3-rethink.txt): which pin
   * composes the placement. Since the pin-innovation deletion (the telemetry
   * attribution run measured the innovation worth 0.03 px — vestigial), the
   * production pin IS the fused base (carried median ⊕ κ·person estimate).
   * The 'rigid' arm the decomposition ran against measured the same path once
   * the innovation went, so the alias is gone with it; the harness asserts
   * that the option is inert unless passed rather than trusting this comment.
   *
   * 'frozen' is the remaining distinct arm — the PURE POSE FLOOR: every
   * estimator HOLDS. No sample admission and no weighted-median commit, no
   * identity question, no person accumulate/commit/tripwires, no depth-fit
   * EMA update, no seat re-solves and no channel easing (the applied
   * constants stand where the caller's warm-up left them), no gaze-EMA
   * motion. The pin composes the frozen base alone, so the screen transform
   * is `smoothedPose ∘ frozen-constants`. Two things deliberately keep
   * running, and they are measurements, not estimators: the view-locked
   * occluder deform (the mask must keep covering THIS image — the
   * single-surface invariant) and the per-frame non-penetration guard (a
   * capped safety, never an estimator). The option is inert unless passed;
   * the harness asserts the hold field by field.
   */
  pinMode = 'production',
  /**
   * CANDIDATE B (anchoring-v3, `pose-fit.js`): whether the rigid-subset pose
   * refit replaces the MediaPipe matrix as the frame's carrier.
   *
   *  - `false` (default): the matrix pose, byte-for-byte today's behaviour.
   *  - `'fit'`: the refit runs and the BLENDED pose (matrix → solved by
   *    wSolve) replaces the raw position/quaternion at the top of the frame —
   *    same smoother, same lead-0 sample, and every measurement inversion
   *    (measureAnchors, carryLandmarks, the occluder deform) inverts the same
   *    pose the frame is drawn with, exactly as the raw-pose rule works
   *    today: one pose in flight per frame. Scale stays the matrix's.
   *  - `'shadow'`: the refit runs — solver state, counters and the
   *    `__ar.poseFit` readout all advance — but the pose is not touched.
   *    This is B.4's bit-parity instrument: a shadow session must equal a
   *    `false` session bit-for-bit, asserted in the harness, which proves
   *    the solver has no side channel into the pipeline.
   *
   * A cold session has wSolve = 0 by arithmetic (the person model's W is the
   * confidence mass), so frame one rides the matrix untouched in every mode —
   * the frame-one bit-equality invariant survives without a special case.
   */
  poseFit = false,
}) {
  // A face in frame ends any run of lost ones. See `noteFaceLost`.
  state.lostSeconds = 0;

  // MediaPipe hands back a column-major 4x4, which is the layout three expects.
  poseMatrix.fromArray(detection.matrix);
  poseMatrix.decompose(rawPosition, rawQuaternion, rawScale);

  // The transform is a similarity: one rotation, one translation, one uniform
  // scale. Averaging the three guards against a near-degenerate decomposition.
  const rawHeadScale = (rawScale.x + rawScale.y + rawScale.z) / 3;

  // CANDIDATE B (anchoring-v3): the rigid-subset refit, at the very top —
  // whatever pose leaves this block IS the frame's one pose: the smoother
  // input, every measurement inversion, the drawn transform. See the option's
  // note and `pose-fit.js` for the mechanism and the measurements behind it.
  if (poseFit && adaptToFace) {
    const solver = state.poseFitSolver ?? (state.poseFitSolver = createPoseFit(face));
    // r(pitch): the codebase's measured pitch-degradation curve — the same
    // POSE_TRUST ramp the sample window admits at, read off the matrix pose
    // (the gauge anchor; the blended pose does not exist yet). Its place in
    // the blend was decided by measurement BOTH ways on the real fixture:
    // removed, the fit's pitch-hold RMS read 5.12 px at wSolve 0.53; present,
    // 4.60 px at wSolve 0.34 — at deep pitch the subset's landmarks are noisy
    // enough (solve residual 4.3 px) that the matrix-plus-estimators carry
    // the hold better, exactly as B.1 specified.
    poseEuler.setFromQuaternion(rawQuaternion, 'YXZ');
    const pitchTrust = 1 - smoothstep(
      POSE_TRUST.pitch[0], POSE_TRUST.pitch[1], Math.abs(poseEuler.x),
    );
    const readout = state.poseFit ?? (state.poseFit = {});
    solver.solve({
      person: state.person ?? null,
      landmarks: detection.landmarks,
      mpPosition: rawPosition,
      mpQuaternion: rawQuaternion,
      scale: rawHeadScale,
      camera: scene.camera,
      width: source.width,
      height: source.height,
      pitchTrust,
      outPosition: fitPosition,
      outQuaternion: fitQuaternion,
      readout,
    });
    // 'shadow' proves the solver has no side channel; wSolve = 0 leaves the
    // matrix untouched by construction (the outputs are bit-copies of it),
    // and the skip below makes that a structural guarantee, not a float one.
    if (poseFit !== 'shadow' && readout.wSolve > 0) {
      rawPosition.copy(fitPosition);
      rawQuaternion.copy(fitQuaternion);
    }
  } else if (state.poseFit) {
    // The readout must never describe a frame the solver did not run on.
    state.poseFit.wSolve = 0;
    state.poseFit.gnIters = 0;
  }

  let position = rawPosition;
  let quaternion = rawQuaternion;
  let headScale = rawHeadScale;

  if (smoothing) {
    rawPosition.toArray(rawPositionArray);
    rawQuaternionArray[0] = rawQuaternion.x;
    rawQuaternionArray[1] = rawQuaternion.y;
    rawQuaternionArray[2] = rawQuaternion.z;
    rawQuaternionArray[3] = rawQuaternion.w;
    // Trust is LAST frame's — the ramps are computed after the smoother runs, and
    // a one-frame-stale scale on an activity signal costs nothing (the signal it
    // scales is itself a smoothed rate). Cold frames pass 1: full responsiveness
    // until the first trust reading exists.
    smoother.update(
      { position: rawPositionArray, quaternion: rawQuaternionArray, scale: rawHeadScale }, dt,
      state.poseTrust ? state.poseTrust.w : 1,
    );

    // `lead` is zero in the app — the composite is frame-locked, so the pose wanted
    // is the pose at this frame's own capture, and what keeps *that* nearly lag-free
    // is the adaptive cutoff reading the measured speed (see `smoothing.js`). A
    // nonzero lead remains available because the filter can answer for it, but
    // predicting past the displayed frame is exactly the two-clock mistake the frame
    // lock removed — and the shipped settings carry no velocity to predict with.
    const pose = smoother.sample(lead);
    position = scratchVector.fromArray(pose.position);
    quaternion = scratchQuaternion.fromArray(pose.quaternion);
    headScale = pose.scale;
  }

  // Measure this face against the observed landmarks — every frame, under the *raw*
  // pose. The measurement inverts the head pose to carry observed landmarks into
  // face space, so it is only self-consistent against the pose those landmarks were
  // solved with; the smoothed pose would fold filter lag into every measurement.
  scene.head.matrix.compose(rawPosition, rawQuaternion, scaleTriple.setScalar(rawHeadScale));
  scene.head.updateMatrixWorld(true);

  // Kept, because the node is about to be recomposed with the smoothed pose and the
  // occluder's deformation has to invert the same pose its landmarks were solved with
  // — for exactly the reason the measurement above does. Inverting the smoothed pose
  // instead would fold the filter's own residual into every vertex of the head.
  rawHeadWorld.copy(scene.head.matrixWorld);

  // All three trust angles come off the pose itself — the raw pose is exactly
  // what the measurement inverts, and unlike the image estimate it separates the
  // axes: a rolled head reads as roll, not as a phantom turn. Taken BEFORE the
  // measurement, which consumes the yaw too: the PD's foreshortening correction
  // (see `measureMetricScale`) divides the projected pupil span by cos(trueYaw).
  poseEuler.setFromQuaternion(rawQuaternion, 'YXZ');
  const trueYaw = Math.abs(poseEuler.y);
  const truePitch = Math.abs(poseEuler.x);
  const trueRoll = Math.abs(poseEuler.z);

  // The person model — the slow face-space estimator (design workstream A) —
  // is owned HERE, beside the fit window and the pin filters it feeds, because
  // its lifecycle is the fit's: created with the session, reset with
  // `resetFit`, and meaningless without adaptation. Empty it contributes
  // nothing anywhere (offsets zero, pin maturity zero, crossfade weight zero),
  // which is one of the three mechanisms that keep frame one bit-identical.
  if (adaptToFace && !state.person) state.person = createPersonModel(face);
  const person = adaptToFace ? state.person : null;

  const observed = clampAnchors(measureAnchors({
    face,
    camera: scene.camera,
    head: scene.head,
    landmarks: detection.landmarks,
    width: source.width,
    height: source.height,
    trueYaw,
    // The occluder's depth fit, one frame stale — it is a slow, global quantity and
    // a frame of staleness costs nothing. This is what keeps the bridge pin on the
    // real nose when the head tilts: without it the pin rides the AVERAGE head's
    // depth, whose error rotates into the image at any tilted pose and pushes the
    // frame (and the occluder translated onto the same pin) forward off the face.
    depthFit: landmarkDepth ? state.occluder?.userData?.depthFit ?? null : null,
    person,
  }), face);

  // The image-asymmetry yaw estimate, demoted to a readout. It used to gate the
  // sample window, and it was the wrong instrument for the job: it conflates
  // pillow roll with turn, and on half of the user's own supine captures it
  // closed a gate the true pose says should have been open. Nothing is gated on
  // it any more; it survives because the "turn" readout it feeds is still an
  // honest description of what the image shows.
  const yaw = estimateYaw(detection.landmarks);

  // Continuous pose-trust in place of the old binary measuring gate (design C4).
  // The product of three ramps, so any one axis being fully untrustworthy zeroes
  // the whole weight, and every intermediate pose is worth exactly what its
  // errors say it is worth. No latch is needed any more: the latch existed
  // because a *boolean* riding a threshold chatters, and a smooth weight riding
  // the same threshold moves by fractions of a percent.
  const wy = 1 - smoothstep(POSE_TRUST.yaw[0], POSE_TRUST.yaw[1], trueYaw);
  const wp = 1 - smoothstep(POSE_TRUST.pitch[0], POSE_TRUST.pitch[1], truePitch);
  const wr = 1 - smoothstep(POSE_TRUST.roll[0], POSE_TRUST.roll[1], trueRoll);
  const wPose = wy * wp * wr;

  // The live trust state, on `state` because `window.__ar` IS the state object —
  // this is the `__ar.poseTrust` readout the design's instrumentation section
  // names. One object reused so a debugger polling it never drives the GC.
  const trust = state.poseTrust ?? (state.poseTrust = {});
  trust.wy = wy;
  trust.wp = wp;
  trust.wr = wr;
  trust.w = wPose;
  trust.trueYawDeg = trueYaw * (180 / Math.PI);
  trust.truePitchDeg = truePitch * (180 / Math.PI);
  trust.trueRollDeg = trueRoll * (180 / Math.PI);
  /** The demoted image-asymmetry estimate, labelled as what it is. */
  trust.imgYaw = yaw;

  // The latch survives as a derived readout and nothing else (graft G12): UI
  // and harness consumers keep their boolean, but no behaviour branches on it —
  // everything that used to is weighted by `wPose` instead.
  state.measuringLatch = adaptToFace && wPose > POSE_TRUST_CONFIDENT;

  // The gaze signal (anchoring-v3): mean iris-centre offset from the
  // eye-corner midpoints, in fractions of the mean eye span — dimensionless,
  // distance- and mirror-invariant. Measured HERE, before the admission
  // block, because admission is now its consumer (see GAZE_ADMIT — the
  // stage-6 pin freeze this signal used to drive is deleted; the measured
  // dominant gaze door was admission, not the pin). A build without iris
  // landmarks simply has no gaze signal and admits everything, exactly as a
  // build without irises has no metricScale. Held whole under 'frozen' — the
  // neutral EMA is estimator state.
  {
    const lms = detection.landmarks;
    if (pinMode !== 'frozen' && adaptToFace && lms && lms.length > LM.IRIS_L_CONTOUR[3]) {
      const g = state.gaze ?? (state.gaze = {
        nx: 0, ny: 0, seeded: false, delta: 0, neutral: true, refusals: 0,
      });
      const oR = lms[LM.EYE_OUTER_R];
      const iR = lms[LM.EYE_INNER_R];
      const cR = lms[LM.IRIS_R_CENTRE];
      const oL = lms[LM.EYE_OUTER_L];
      const iL = lms[LM.EYE_INNER_L];
      const cL = lms[LM.IRIS_L_CENTRE];
      const spanR = Math.hypot(iR.x - oR.x, iR.y - oR.y);
      const spanL = Math.hypot(iL.x - oL.x, iL.y - oL.y);
      const span = (spanR + spanL) / 2;
      if (span > 1e-6) {
        const gx = ((cR.x - (oR.x + iR.x) / 2) + (cL.x - (oL.x + iL.x) / 2)) / (2 * span);
        const gy = ((cR.y - (oR.y + iR.y) / 2) + (cL.y - (oL.y + iL.y) / 2)) / (2 * span);
        if (!g.seeded) { g.nx = gx; g.ny = gy; g.seeded = true; }
        const alpha = 1 - Math.exp(-dt / GAZE_NEUTRAL_TAU);
        g.nx += (gx - g.nx) * alpha;
        g.ny += (gy - g.ny) * alpha;
        g.delta = Math.hypot(gx - g.nx, gy - g.ny);
        g.neutral = g.delta <= GAZE_ADMIT;
      }
    }
  }
  const gazeNeutral = state.gaze ? state.gaze.neutral : true;
  // The attribution counter the telemetry replay differences per segment —
  // every sample the gaze door refused, monotone like the seat's counters.
  if (adaptToFace && pinMode !== 'frozen' && wPose > POSE_TRUST_ADMIT && !gazeNeutral) {
    state.gaze.refusals++;
  }

  // 'frozen' (R0): admission, the median commit and the identity question all
  // hold — the carried anchors are constants for as long as the caller keeps
  // the mode on. The block below is the ONLY writer of the sample window and
  // of `state.anchors`, so skipping it whole is the hold. And since
  // anchoring-v3, the gaze door: an off-neutral gaze refuses the WHOLE sample
  // — the bent iris ruler, the gaze-displaced bridge and eye line, all of it
  // — and pauses the identity question, because every field of the sample
  // rides landmarks the gaze is known to corrupt (see GAZE_ADMIT). The
  // carried estimate stands; "keep previous, never assume average".
  if (adaptToFace && pinMode !== 'frozen' && wPose > POSE_TRUST_ADMIT && gazeNeutral) {
    // Someone else in the chair. Asked before the sample is admitted, so a new face
    // never has its first frames averaged in with the last person's — which was the
    // failure the old half-second timer was really written to prevent, and this asks
    // the question the timer was standing in for. Asked only near-frontal and
    // convicted only on a streak (see POSE_TRUST_IDENTITY): the predicate compares
    // proportions, a turned reading still carries foreshortening at half trust,
    // and one bad sample must never cost a converged estimate.
    if (wPose > POSE_TRUST_IDENTITY) {
      // The identity readout — `__ar.identity`, an R0 attribution instrument:
      // records WHICH comparison drives each strike, at the moment the
      // question is asked. The event counters are MONOTONE (a conviction
      // zeroes the streak, never these), so a replay can difference them per
      // frame; the value fields always describe THIS frame's ask, in exactly
      // the arithmetic `isDifferentFace` convicts on. Pure readout — nothing
      // reads it back, and it is written only on frames the question runs.
      const id = state.identity ?? (state.identity = {
        asked: 0, strikeEvents: 0, acquittals: 0, convictions: 0,
        strikes: 0, driver: null,
        obsWidthRatio: null, carWidthRatio: null, devWidthRatio: 0,
        obsMetricScale: null, carMetricScale: null, devMetricScale: 0,
      });
      const car = state.anchors;
      const dev = (a, b) => (a !== null && a !== undefined && b !== null && b !== undefined
        ? Math.abs(a - b) / Math.max(Math.abs(b), 1e-6) : 0);
      id.asked++;
      id.obsWidthRatio = observed.widthRatio ?? null;
      id.carWidthRatio = car?.widthRatio ?? null;
      id.obsMetricScale = observed.metricScale ?? null;
      id.carMetricScale = car?.metricScale ?? null;
      id.devWidthRatio = dev(observed.widthRatio, car?.widthRatio);
      id.devMetricScale = dev(observed.metricScale, car?.metricScale);

      if (isDifferentFace(state.anchors, observed)) {
        id.strikeEvents++;
        id.driver = id.devWidthRatio > IDENTITY_TOLERANCE
          ? (id.devMetricScale > IDENTITY_TOLERANCE ? 'both' : 'widthRatio')
          : 'metricScale';
        state.identityStrikes = (state.identityStrikes ?? 0) + 1;
        if (state.identityStrikes >= IDENTITY_STRIKES) {
          state.identityStrikes = 0;
          id.convictions++;
          resetFit(state);
        }
      } else {
        if ((state.identityStrikes ?? 0) > 0) id.acquittals++;
        state.identityStrikes = 0;
      }
      id.strikes = state.identityStrikes ?? 0;
    }

    // Admitted carrying its trust, which is what the weighted median stands on:
    // a frontal sample enters at ~1 and a pillow-pose one at 0.2, so the pillow
    // regime keeps personalising without ever outvoting the frames that were
    // actually worth more.
    observed.wPose = wPose;
    state.sampleSet = state.sampleSet ?? [];
    state.sampleSet.push(observed);
    // Bounded — the estimate keeps following the face for the whole session
    // instead of freezing on whatever the first second happened to see — but NOT
    // first-in-first-out. FIFO eviction was weight-blind, and it quietly defeated
    // the weighted median it feeds: hold a yaw for one second and thirty low-trust
    // samples flood the window, aging out every frontal sample that was worth
    // five of them — the median then stands on nothing but the tail it was built
    // to outvote, and the frame breathes as the estimate walks. So eviction asks
    // the same question admission does: the sample evicted is the lowest-weight
    // one in the window, the OLDEST among equals. Two properties, both
    // load-bearing: equal weights reproduce FIFO exactly (the strict `<` keeps
    // the earliest minimum, so every equal-weight caller and every pre-weighting
    // session is bit-for-bit on the old semantics), and by induction the window
    // always holds the highest-weight samples seen since the last reset — no run
    // of low-trust poses, however long, can flush the high-trust mass. That is
    // the right bias for a face-space constant: a frontal measurement of a rigid
    // face does not go stale, and a genuinely new face goes through `resetFit`,
    // not through attrition.
    if (state.sampleSet.length > FIT_WINDOW) {
      const weightOf = (s) => (Number.isFinite(s.wPose) ? s.wPose : 1);
      let evict = 0;
      for (let i = 1; i < state.sampleSet.length; i++) {
        if (weightOf(state.sampleSet[i]) < weightOf(state.sampleSet[evict])) evict = i;
      }
      state.sampleSet.splice(evict, 1);
    }
    state.anchorSamples = state.sampleSet.length;

    state.anchors = commitFit(state.anchors, medianAnchors(state.sampleSet, face), face);
  }

  scene.head.matrix.compose(position, quaternion, scaleTriple.setScalar(headScale));
  scene.head.visible = true;
  scene.head.updateMatrixWorld(true);

  if (adaptToFace) {
    // Nothing measured yet (first frames, or acquired mid-turn) — the average face
    // is a better starting point than nothing.
    if (!state.anchors) state.anchors = canonicalAnchors(face);
  } else if (!state.anchors || state.anchors.measured !== false) {
    // The average face, rebuilt only when the current set is not already it.
    state.anchors = canonicalAnchors(face);
  }

  // What the placement stands on, and how the answer changed with anchoring-v3:
  //
  //   **proportions AND position from the estimators; the pose carries both.**
  //
  // The original split — "proportions from the scan, position from the
  // landmarks" — existed because the pose was a similarity fit of the AVERAGE
  // head: measured on a real face at ±10° of yaw, that canonical fit put the
  // eye region 12–15 mm sideways of the observed eyes, so pinning to the raw
  // landmark was the only honest choice. Two things retired it. The person
  // model made the carried geometry THIS face's (0.08–0.18 mm transverse
  // convergence, zero identity bleed), so the constant being carried stopped
  // being wrong; and the live telemetry measured what the raw landmark was
  // actually contributing per frame once the carried base existed: 0.03 px of
  // signal, plus the full 2.3–4 mm gaze swing of MediaPipe's eye-following
  // bridge (upstream #1786). Position is still re-measured every frame — the
  // sample window admits this frame's reading at its honest trust and the
  // median moves freely while samples genuinely disagree — but the drawn pin
  // now rides the estimate, not the instantaneous landmark, and the
  // pose-systematic slide the landmark was compensating at extremes belongs
  // to the pose refit (`pose-fit.js`), which re-derives the carrier from
  // personally-converged points instead.
  //
  // The eye line, the bridge direction and the ear rest points are ALWAYS the
  // carried values — the weighted-median, deadbanded estimate — at every pose.
  //
  // They used to be live while the measuring gate was open and carried outside
  // it, and that binary payload was a step generator by construction: every gate
  // flip swapped eyeLineY, bridgeUp and both ears between their observed and
  // carried values in a single frame — ~2.2 mm of seat step and an arm twitch
  // per flip, right in the turn range where the far-arm handover happens
  // (diagnosis jitter-cause 5). The flip was also never buying anything. These
  // are face-space *proportions*, as static as the widths; the median over the
  // window is a better estimate of a static quantity than any single frame's
  // reading, head-on included. The bridge now stands in exactly the same
  // regime — the whole payload is carried, and there is no live/carried
  // crossing left anywhere to step.
  //
  // The pin IS the fused base (anchoring-v3 — the innovation deletion). The
  // observed bridge used to be composed on top of this base as a filtered
  // per-frame innovation; the telemetry attribution run measured that term's
  // entire worth at 0.03 px during the gaze segment (8.57 px production vs
  // 8.54 rigid — vestigial), while everything it needed to stay safe (the
  // gaze gate, the hybrid activity, the noise-floor cap, the nose lever) was
  // live machinery with live failure modes. So the term is deleted, not
  // conditioned: the pin is the carried weighted-median bridge fused toward
  // the person model's committed estimate by its maturity κ — face-space
  // constants on their honest timescales, carried by the pose. Position is
  // still re-measured every frame; what changed is WHICH estimator carries
  // it: the median window (admitting this frame's sample at its trust) and
  // the person model, instead of a raw landmark whose per-frame motion was
  // measured to be mostly gaze and noise. The extreme-pose slide the
  // innovation was compensating (R0 rigidMiss: up to 13 px at −18° yaw,
  // per-still-cold) is candidate B's job now — the pose refit re-derives the
  // carrier from personally-converged points per frame (`pose-fit.js`).
  //
  // The pin then feeds EVERY consumer — `solvePlacement`'s hang target, the
  // occluder's translation, the seat's bridge-relative query — from this one
  // object. That is the structural weld: the frame and the occlusion
  // boundary cannot ride two different bridges, because there is only one
  // bridge value in flight. The harness asserts the weld bit-exactly.
  //
  // With *Adapt to face* off, none of this applies: that toggle now means "the old
  // behaviour, whole" — average proportions AND rigid pose-carried position — which
  // is the honest A/B against everything this pipeline does.
  const carried = state.anchors;
  let anchors = carried;
  if (adaptToFace) {
    // The fused pin base (stage 4, design "Bridge pin fusion"): the carried
    // median blended toward the person model's committed bridge estimate by
    // its maturity κ = clamp(W_bridge/W_PIN_FULL, 0, 1). The median is the
    // best estimate a memoryless window can make and stays the whole base on
    // a cold session (κ = 0 — frame one is the first admitted sample,
    // adopted whole through the median-of-one, bit-identical to the observed
    // reading); the person estimate is the same quantity fused across MORE
    // poses with per-vertex trust. Under 'frozen' every input below is held
    // state, so the composed bridge is a face-space constant — same
    // arithmetic, frozen operands.
    let baseX = carried.bridge.x;
    let baseY = carried.bridge.y;
    let baseZ = carried.bridge.z;
    if (person) {
      const k = person.bridgeMaturity();
      if (k > 0) {
        const est = person.bridgeEstimate(state.bridgeEstimate
          ?? (state.bridgeEstimate = { x: 0, y: 0, z: 0 }));
        baseX += (est.x - baseX) * k;
        baseY += (est.y - baseY) * k;
        baseZ += (est.z - baseZ) * k;
      }
    }

    // A fresh vector per frame, like the payload object around it: harness
    // and UI consumers hold whole payloads across frames, and a pin they all
    // aliased would rewrite history under them. Both surviving modes compose
    // this same base — 'frozen' differs only in what the estimators upstream
    // were allowed to do, never in this composition.
    anchors = {
      ...carried,
      bridge: new THREE.Vector3(baseX, baseY, baseZ),
    };

    // The pin at a glance — `__ar.pin`, per the design's instrumentation
    // section. One object reused so polling it never drives GC.
    // `observedDeltaMm` is what the deleted innovation term WOULD have
    // carried — the distance from this frame's raw landmark reading to the
    // composed base — kept as a readout because it is the live measure of
    // how far the landmark wanders off the carried estimate (gaze, slide,
    // noise), which is worth seeing precisely because nothing follows it
    // any more.
    const pin = state.pin ?? (state.pin = {});
    pin.observedDeltaMm = Math.hypot(
      observed.bridge.x - baseX,
      observed.bridge.y - baseY,
      observed.bridge.z - baseZ,
    ) * 10;
    pin.maturity = person ? person.bridgeMaturity() : 0;
    pin.zConfBridge = person ? person.zConfBridge : 0;
    pin.gazeDelta = state.gaze ? +state.gaze.delta.toFixed(4) : 0;
    pin.gazeNeutral = gazeNeutral;
    pin.gazeRefusals = state.gaze ? state.gaze.refusals : 0;
    pin.baseX = baseX;
    pin.baseY = baseY;
    pin.baseZ = baseZ;
  }

  // The head that hides the frame is deformed onto *this* face, and the frame is then
  // seated against that same head. Ordering matters: this runs before
  // `solvePlacement`, so the surface the seat queries below is the one the occluder
  // has just been rebuilt to, rather than the previous frame's.
  //
  // `measuring` deliberately does NOT gate the shape — the occluder re-measures at
  // every pose, with per-vertex facing and self-occlusion weights supplying the
  // safety a gate used to (see the pose-gate note in `updateOccluder`). What is
  // passed is the derived readout alias (w_pose confident — see G12), so the
  // occluder can still tell confident poses apart if it ever needs to; today
  // nothing behavioural reads it.
  // Screen pixels per centimetre of face, at the distance the wearer actually is.
  //
  // The occluder's offsets are physical and their cost is not: a fixed millimetre is two
  // pixels at arm's length and seven when someone leans in to look closely, which is
  // precisely when they are looking closely. This is what lets the relief know which of
  // those it is in. See `MAX_RELIEF_PX`.
  const viewHeight = scene.renderer?.getDrawingBufferSize?.(bufferSize)?.y ?? 0;
  const pixelsPerCm = viewHeight > 0 && Math.abs(position.z) > 1
    ? (viewHeight / 2) / (Math.abs(position.z) * Math.tan((scene.camera.fov * Math.PI) / 360))
    : 0;

  updateOccluder(state.occluder, {
    face,
    camera: scene.camera,
    headMatrixWorld: rawHeadWorld,
    landmarks: detection.landmarks,
    anchors,
    measuring: state.measuringLatch === true,
    dt,
    deform: adaptToFace && deformOccluder,
    useLandmarkDepth: landmarkDepth,
    pixelsPerCm,
    // The person model accumulates inside `measureShape`, from the identical
    // ray-pinned observations and facing×visibility weights the deform runs
    // on — one measurement, two timescales. `wPose` is this frame's honest
    // worth, the same continuous trust the sample window admits at.
    person,
    wPose,
    // 'frozen' (R0): the slow estimators under the deform — person
    // accumulate/commit and the depth-fit EMA — hold; the view-locked
    // residual still eases, because the mask must keep covering THIS image.
    freezeEstimators: pinMode === 'frozen',
  });

  // The depth gate's live state — `__ar.depthFit`, per the design's
  // instrumentation section: raw beside smoothed, so a gate gone quiet and a gate
  // gone dead read differently, and the per-frame weight delta, which is the
  // number the quiescence budget is stated in. One object reused, like the rest.
  const conditionedFit = state.occluder?.userData?.depthFit ?? null;
  if (conditionedFit) {
    const readout = state.depthFit ?? (state.depthFit = {});
    readout.r2Raw = conditionedFit.r2Raw ?? conditionedFit.r2;
    readout.r2Smoothed = conditionedFit.r2;
    readout.rmsNoseMm = (conditionedFit.rmsNose ?? 0) * 10;
    readout.weightApplied = conditionedFit.weight;
    readout.weightDeltaPerFrame = conditionedFit.weightDelta ?? 0;
    readout.nExcluded = conditionedFit.nExcluded ?? 0;
  } else {
    state.depthFit = null;
  }

  // No glasses yet — the model loads in parallel with the camera so the scan can
  // begin the moment there is a face, instead of waiting out a download. Track,
  // measure and pose the head; the placement resumes when there is something to place.
  if (!model) {
    return {
      headScale,
      yaw,
      anchors,
      faceWidthRatio: anchors.widthRatio,
      distanceCm: Math.abs(position.z),
      placement: null,
      samples: state.anchorSamples ?? 0,
      metricScale: anchors.metricScale ?? null,
      pdCm: anchors.pdCm ?? null,
      pupilHeight: null,
      width: null,
    };
  }

  // --- the seat: three eased channels, an event-driven solve, a raw guard ---
  //
  // The resting configuration — height along the bridge, standoff along z,
  // optionally roll about z — is a face×frame CONSTANT, so it lives on the
  // event timescale: `solveRestConfiguration` runs on G13's events (≤ 2 Hz)
  // and per frame the placement only APPLIES the eased channels plus the G2
  // non-penetration guard. Position still rides the landmarks raw — the
  // placement principle survives untouched; what changed is that the seat's
  // three numbers stopped being re-guessed per frame at all.
  //
  // The channels ease BEFORE the placement (this frame applies last frame's
  // convergence step — one frame of staleness on a ≥ 0.15 s time constant),
  // and the scheduler runs AFTER it, because the solve sweeps from the
  // placement's own optical-height base. A session's FIRST adopted solve
  // re-places the same frame, so frame one seats with its solved standoff
  // exactly as the scalar seat used to adopt its first push whole.
  const seatState = state.seatConfig ?? (state.seatConfig = createSeatState());
  // 'frozen' (R0): the applied constants stand exactly where the warm-up left
  // them — no easing, no clock, and (below) no solves. What still runs is the
  // per-frame guard inside `solvePlacement`, which is a capped safety, and the
  // `rawNeeded` readout, which feeds nothing while the ease is held.
  if (pinMode !== 'frozen') easeSeatChannels(seatState, person, fit, dt, wPose);

  const surface = surfaceOf(state.occluder);
  // The channels are withheld — not merely frozen — while "Rest on the
  // nose" is off: a solved height applied without its solve running is a
  // frame lowered down the bridge with nothing keeping it honest, and the
  // toggle's contract is the pure landmark hang. (`solvePlacement` gates on
  // the same flag itself; both sides state it so neither drifts.)
  let placement = solvePlacement({
    model, anchors, fit, face, surface,
    seatConfig: seatState.hasSolve && fit.seatOnNose !== false
      ? seatState.applied : null,
  });

  const adopted = pinMode === 'frozen' ? null : scheduleSeatSolve(seatState, {
    surface: surface ?? face?.surface ?? null,
    model,
    anchors,
    fit,
    person,
    base: placement.seatBase,
    rebuilds: state.occluder?.userData?.rebuilds?.surface ?? 0,
    wPose,
  });
  if (adopted === 'adopted-first') {
    placement = solvePlacement({
      model, anchors, fit, face, surface, seatConfig: seatState.applied,
    });
  }

  if (placement.noseSeat?.guard > 0) seatState.guardPushes++;
  // The raw law's own answer at this frame's applied placement — next
  // frame's standoff target (see `easeSeatChannels`). Null when nothing
  // touched, so a face leaving the patch holds the channel instead of
  // easing it to a fiction.
  seatState.rawNeeded = placement.noseSeat && placement.noseSeat.touched > 0
    ? placement.noseSeat.push : null;

  // `__ar.seat` — the design's instrumentation contract, one reused object.
  // Every number one of the five seat measurables asserts is visible here
  // live: the mode, the solved and applied constants, the per-side bearing,
  // the guard, and the pupil verdict the height demotion turned honest.
  {
    const readout = state.seat ?? (state.seat = {});
    const ns = placement.noseSeat;
    readout.mode = seatState.hasSolve || seatState.lastMode === 'hold'
      ? seatState.lastMode : 'cold';
    readout.conf = seatState.conf;
    readout.sRestMm = seatState.sStar * 10;
    readout.sAppliedMm = seatState.applied.s * 10;
    readout.zetaMm = seatState.applied.zeta * 10;
    readout.appliedMm = ns ? ns.easedPush * 10 : 0;
    readout.guardMm = ns ? ns.guard * 10 : 0;
    readout.guardPushes = seatState.guardPushes;
    // The carried surface reference and its evidence mass (2026-08-17) —
    // beside the raw law it admits from, so a live session shows the
    // refusal working: at a hard pose `needMm` (the raw morph) climbs while
    // `needRefMm` stands.
    readout.needRefMm = seatState.needEst.value !== null ? seatState.needEst.value * 10 : null;
    readout.needMass = seatState.needEst.weight;
    readout.needMm = seatState.rawNeeded !== null ? seatState.rawNeeded * 10 : null;
    readout.phiDeg = seatState.applied.phi * (180 / Math.PI);
    readout.solves = seatState.solves;
    readout.holds = seatState.holds;
    readout.refusals = seatState.refusals;
    readout.solveAgeMs = Number.isFinite(seatState.sinceSolve)
      ? seatState.sinceSolve * 1000 : null;
    readout.deadbandHolding = seatState.zetaHolding && !seatState.sSettling;
    if (ns?.perSide) {
      const side = readout.perSide ?? (readout.perSide = {});
      side.ILmm = Number.isFinite(ns.perSide.L.soft) ? ns.perSide.L.soft * 10 : null;
      side.IRmm = Number.isFinite(ns.perSide.R.soft) ? ns.perSide.R.soft * 10 : null;
      side.gapMm = ns.perSide.gap !== null ? ns.perSide.gap * 10 : null;
    } else {
      readout.perSide = null;
    }
    readout.restedSide = ns?.restedSide ?? null;
    // Ridge or sidewall, from the field's own normal at the rested contact —
    // the difference between a saddle fit and a pad fit, live.
    readout.contactClass = null;
    const skin = surface ?? face?.surface ?? null;
    if (ns?.restedAt && skin) {
      const normal = normalAt(
        skin,
        skin.origin[0] + (ns.restedAt.x - anchors.bridge.x),
        skin.origin[1] + (ns.restedAt.y - anchors.bridge.y),
      );
      if (normal) readout.contactClass = Math.abs(normal.x) > 0.5 ? 'sidewall' : 'ridge';
    }
    readout.bearingGapMm = seatState.solve?.bearing?.gap != null
      ? seatState.solve.bearing.gap * 10 : null;
    readout.widthAtRestMm = seatState.solve?.widthAtRestCm != null
      ? seatState.solve.widthAtRestCm * 10 : null;
    readout.verdict = pupilVerdict(pupilHeightInLens({ model, anchors, placement })).verdict;
  }

  scene.glasses.matrix.compose(
    placement.position, placement.quaternion, scaleTriple.setScalar(placement.scale),
  );

  // Swing the arms onto this face's ears.
  //
  // Every frame, not only while the fit is settling. The anchors lock but the
  // placement does not: tilt, size, the offsets and the sizing mode all keep moving
  // it from the controls. An arm aimed against a placement that has since changed
  // rotates and translates rigidly with the front, which is precisely the coupling
  // the hinges exist to remove — drag the tilt slider and the arms would dive
  // through the ears. Re-aiming is a handful of vector operations on two nodes, and
  // it is a no-op when nothing moved.
  if (fit.fitTemples !== false) {
    // The head the arms have to get past, at this face's width. Null when there is
    // no occluder — the arms then aim straight at the ear, as they always did.
    aimTemples(temples, anchors, placement, headProfileFor(state.occluder));
    state.templesAimed = true;
  } else if (state.templesAimed) {
    resetTemples(temples);
    state.templesAimed = false;
  }

  // Independent of whether the arms are aimed: the fade is about what a viewer can
  // believe, not about where the arm was put. `scene.head.matrixWorld` is the
  // smoothed pose composed a few lines above, so this reads the orientation actually
  // being drawn rather than the one that was measured.
  fadeTemples(temples, scene.head.matrixWorld);

  return {
    headScale,
    yaw,
    anchors,
    faceWidthRatio: anchors.widthRatio,
    distanceCm: Math.abs(position.z),
    placement,
    samples: state.anchorSamples ?? 0,
    metricScale: anchors.metricScale ?? null,
    pdCm: anchors.pdCm ?? null,
    pupilHeight: pupilHeightInLens({ model, anchors, placement }),
    width: widthVerdict({ model, anchors, placement }),
  };
}

/**
 * Adopts the new estimate, field by field, but only where it has moved enough to be
 * worth moving.
 *
 * The deadband is the whole mechanism (see `FIT_DEADBAND`). Note which fields it
 * covers and which it does not: only the ones the fit *carries* — the widths, the
 * true-size ratio, and (since the payload became always-carried) the eye line.
 * Position is not here at all, because position is re-measured from the observed
 * landmarks every single frame and always was; a deadband on it would be a lag on
 * the one signal that must not have any.
 *
 * `previous` of null is the first ever sample, which is adopted whole. That is the
 * property that makes the gate unnecessary: frame one is already a fit.
 */
function commitFit(previous, next, face) {
  if (!previous || !previous.measured) return next;

  const hold = (key) => Math.abs(next[key] - previous[key]) <= FIT_DEADBAND[key];

  if (hold('templeWidth')) {
    next.templeWidth = previous.templeWidth;
    next.widthRatio = previous.widthRatio;
    // Ear reach follows the width, so it has to be re-derived from whichever width
    // actually won — otherwise a held width gets paired with a moved ear x and the
    // arms drift outward a fraction of a millimetre per frame, forever.
    const halfWidth = Math.abs(face.point(LM.TEMPLE_R)[0]) * next.widthRatio;
    next.ears.right.x = -halfWidth;
    next.ears.left.x = halfWidth;
  }
  if (hold('noseWidth')) {
    next.noseWidth = previous.noseWidth;
    next.noseWidthRatio = previous.noseWidthRatio;
  }
  // Since the eye line became always-carried (C4) it is applied from here at
  // every pose, so it rests behind the same kind of deadband as the widths.
  if (hold('eyeLineY')) {
    next.eyeLineY = previous.eyeLineY;
  }

  // Carried forward rather than deadbanded when this window had no reading at all —
  // a run of blinks must not silently drop the fit back to average-sized.
  if (next.metricScale === null) {
    next.metricScale = previous.metricScale;
    next.pdCm = previous.pdCm;
  } else if (previous.metricScale !== null && hold('metricScale')) {
    next.metricScale = previous.metricScale;
    next.pdCm = previous.pdCm;
  }

  return next;
}

/**
 * Whether a freshly observed face is a different person from the one the fit
 * describes.
 *
 * `widthRatio` alone — a *shape* comparison against the canonical head, which is
 * what the old timer was implicitly protecting. `metricScale` used to stand
 * beside it as a second arm ("absolute size from the iris — the case shape
 * cannot catch, an adult and a child proportioned alike"), and the wearer's own
 * telemetry convicted it as a witness (anchoring-v3, 2026-08-17): every one of
 * the fixture's 40 logged identity strikes was metricScale-driven while
 * widthRatio sat at 0.1–1.2%, and after the gaze door refused the off-neutral
 * readings a browse episode STILL swung the admitted metricScale 13.5–16.5%
 * off the carried value for five consecutive frames at neutral-reading gaze
 * and wPose 0.81–0.95 — one more converged-model dump, on the wearer's own
 * face. The instrument's measured noise exceeds the 12% tolerance it was
 * asked to convict at, and a predicate must not convict on a reading its own
 * fixture shows lying. The ruler still serves everything it is good for —
 * the size verdicts, the PD readout, the median-carried scale (whose
 * robustness and deadbands absorb the swings) — it just no longer carries a
 * death sentence. The proportioned-alike-adult-and-child case reverts to the
 * *Re-measure face* button, which is the honest price of an instrument this
 * noisy; a real face swap convicts on shape, which the swap check pins.
 */
export function isDifferentFace(anchors, observed) {
  if (!anchors || !anchors.measured) return false;

  const apart = (a, b) => a !== null && a !== undefined && b !== null && b !== undefined
    && Math.abs(a - b) / Math.max(Math.abs(b), 1e-6) > IDENTITY_TOLERANCE;

  return apart(observed.widthRatio, anchors.widthRatio);
}

/** Empties the estimate without touching anything else. */
function resetFit(state) {
  state.anchors = null;
  state.sampleSet = null;
  state.anchorSamples = 0;
  // A fresh face is a fresh gauge: the pose refit's warm start is a statement
  // about the OLD face's divergence from the matrix, and its confidence mass
  // (the person model's W) is about to be zeroed below — the solver forgets
  // its warm delta so the new wearer's first solved frame starts from the
  // matrix, exactly as a new session does.
  state.poseFitSolver?.reset();
  // And a fresh face is a fresh seat. The solved configuration is the OLD
  // face's nose — its equilibrium height, its standoff, its roll; easing the
  // new wearer in from it drags a visible re-seat across their first fraction
  // of a second. Cleared whole (channels, solve, scheduler edges), so the
  // next frame's first solve is adopted whole — frame one of the new fit is a
  // fit, exactly like frame one of a session. `remeasure` always cleared the
  // old scalar; the identity swap used to keep it, for no reason anyone
  // chose; both now clear the whole state through this one line.
  state.seatConfig = null;
  // The person model is the slowest state there is, and it is a statement
  // about the PREVIOUS person: carried across an identity change it would
  // spend its whole adaptation tau (20–60 s) morphing one face into another —
  // cross-person bleed, the exact failure the harness's swap check bounds at
  // half a millimetre. Reset in place: the arrays clear, the object survives,
  // and the next frame accumulates a fresh person from sample one.
  state.person?.reset();
}

/**
 * Throws away the measured fit so the next head-on frames measure it afresh.
 *
 * Now only ever called deliberately — the *Re-measure face* button, switching
 * *Adapt to face*, and a new source. Losing the face does not call it, and that
 * is the point: see `IDENTITY_TOLERANCE`.
 */
export function remeasure(state) {
  resetFit(state);
  state.templesAimed = false;
  state.lostSeconds = 0;
  // The depth fit's EMA is a statement about the previous face in front of the
  // previous source — its offset rides that head's geometry, and the anchors
  // consume it one frame stale from this very handle. Cleared whole: the next
  // face's first fit is adopted whole (see `conditionDepthFit`), exactly like
  // the pin's first sample.
  if (state.occluder?.userData) state.occluder.userData.depthFit = null;
}

/**
 * Records a frame with no face in it, and reports the moment the pose filter's
 * velocity state has gone stale enough to throw away.
 *
 * The *fit* is not touched here and no longer can be. It used to be: this function
 * called `remeasure` after half a second, so every dropout long enough to cross that
 * line restarted a 45-frame scan and the user watched their glasses re-size
 * themselves several times a minute. What survives is the narrow, correct half —
 * a velocity carried across a long gap describes a movement that is over.
 *
 * Returns true on the frame the filter should be reset, so the caller can do it.
 */
export function noteFaceLost(state, dt = 1 / 30) {
  const before = state.lostSeconds ?? 0;
  if (before >= LOST_SECONDS_BEFORE_RESET) return false;

  state.lostSeconds = before + Math.max(dt, 0);
  if (state.lostSeconds < LOST_SECONDS_BEFORE_RESET) return false;

  // Held at the threshold so a longer absence does not re-report on every frame.
  state.lostSeconds = LOST_SECONDS_BEFORE_RESET;
  return true;
}

/**
 * How many consecutive faceless results are ridden out before the glasses go.
 *
 * The detector's commonest failure is a single motion-blurred frame mid-turn, and
 * the honest response to one is to change nothing: keep showing the previous frame
 * with the previous pose. That costs a beat of a frozen mirror and it does **not**
 * break the frame lock — the pair on screen is still a frame shown with the pose
 * solved from that same frame, which is the whole invariant. It is the one option
 * that never draws glasses on a face that is not where they are drawn.
 *
 * Four, not two, because at 40–50 degrees of turn the landmarker fails in *bursts*
 * — a face on roughly every other frame, and blur runs of three — and a two-frame
 * hold popped the glasses off binarily at exactly the angled poses under complaint.
 * Four rides out a ~130 ms burst at 30 fps; a genuinely absent face still clears
 * within a sixth of a second.
 */
export const HOLD_FACELESS_RESULTS = 4;

/**
 * Records one faceless detection result: advances the run counter, runs the lost
 * clock, and says whether the caller should ride the dropout out.
 *
 * Here rather than in `main.js` for the same reason `updateFrame` is: this is
 * arithmetic the harness has to be able to drive, and the harness cannot import
 * a module that boots the app.
 *
 * **The lost clock runs from the FIRST faceless result.** The hold decides what
 * is *shown*, never whether the face is gone — and the two used to be conflated:
 * `main.js` returned out of the held frames before the clock ran, so `lostSeconds`
 * started counting only after the hold, which quietly moved the pose-filter reset
 * from 0.5 s of true absence to 0.5 s PLUS the hold (~0.13 s at 30 fps, double at
 * 15). That starvation also broke the recovery dt's own justification: C5 clamps
 * the re-acquisition dt to `LOST_SECONDS_BEFORE_RESET` on the stated grounds that
 * any longer gap has already reset the filter — which was simply not true of gaps
 * inside the starved band, so a face returning at 0.55 s met a live filter still
 * carrying pre-gap velocity AND a clamped-short dt, and the catch-up landed as
 * the very snap C5 exists to remove. With the clock running through holds the
 * clamp's premise holds by construction; and a hold spanning one tab-hidden
 * multi-second frame now resets the filter DURING the hold, instead of carrying
 * a fictional velocity into the re-acquisition.
 *
 * Returns `{ hold, resetFilter }`: `hold` while the dropout should be ridden out
 * (a face was being tracked, and the run is still short), `resetFilter` on the
 * one result the velocity state goes stale — the caller resets the smoother, as
 * with `noteFaceLost`.
 */
export function noteFacelessResult(state, dt = 1 / 30) {
  state.facelessResults = (state.facelessResults ?? 0) + 1;
  const resetFilter = noteFaceLost(state, dt);
  const hold = !!state.detected && state.facelessResults <= HOLD_FACELESS_RESULTS;
  return { hold, resetFilter };
}
