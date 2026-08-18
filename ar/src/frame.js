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
import {
  measureAnchors, canonicalAnchors, clampAnchors, medianAnchors, createRailCounter,
} from './anchors.js';
import { LM } from './canonical-face.js';
import { aimTemples, resetTemples, fadeTemples } from './temples.js';
import {
  updateOccluder, headProfileFor, surfaceOf, resetOccluderPerson,
} from './occluder.js';
import { estimateYaw } from './tracker.js';
import { createPersonModel, W_MAX } from './person.js';
import { solveRestConfiguration, S_REFINE } from './seat-equilibrium.js';
import { createAgreement, foldAgreement } from './agreement.js';
import { SETTLE_WINDOW_S } from './settle.js';
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
 * The standoff is a face constant, and only a square-on pose may teach it —
 * the wearer's "the glasses are being pushed forward for no reason" at >40° of
 * yaw, third and final attempt (2026-08-17) — but "square-on" is a fact about
 * THIS SESSION'S CAMERA, not an absolute cone (2026-08-17, camera geometry).
 *
 * The first two attempts failed for the same unexamined reason. Freezing the
 * channel at low trust locked in an inflation that had already happened;
 * admitting readings weighted by pose trust let them in anyway. The
 * z-decomposition says why, and it is the whole answer: **the surface law is
 * already wrong at 10–20° of yaw, where the trust ramps still read 0.87–1.0**
 * (+1.7 to +3.5 mm of fictional interference, measured). A weight cannot
 * discount a reading that arrives at full weight. So the seat stops asking the
 * ramps for a *weight* and asks them for a *rank*: is this pose among the
 * squarest this session actually achieves?
 *
 * **The refusal is categorical, and that part was measured to work.** What was
 * wrong was its REFERENCE. The bar used to be the absolute constant 0.999
 * against `w = wy·wp·wr`, i.e. "inside every ramp's start" (yaw 9.7°, pitch
 * 11.5°, roll 14.3°) — a cone about the CAMERA axis, and a camera is not
 * anybody's eye level. A laptop lid 12 cm below the eyes at 50 cm puts a user
 * who is looking straight at their screen at atan(12/50) = 13.5° of pose
 * pitch, where `wp = 1 − smoothstep(0.20, 0.60, 0.236) = 0.977`; with ±1.5° of
 * ordinary postural wander the session's BEST frame reads 0.9984 against a bar
 * of 0.999. Measured on the synthetic camera ladder: 0/600 frames admitted
 * over 20 s, no standoff reference, no equilibrium ever solved, `hasSolve`
 * false for the session — 6.1 mm of solved standoff for an eye-level user and
 * exactly 0 for the laptop one. A phone in the lap (30°, `w` ≈ 0.16) was the
 * same, worse. That is not a low-trust problem; it is a knife-edge equality on
 * a product of three smoothsteps, and it fails silently for a whole class of
 * hardware.
 *
 * So the bar is now the session's own top decile:
 *
 *     band = max over the session of  Q90( last FIT_WINDOW values of w )
 *     square-on  ⟺  w ≥ SEAT_REF_SLACK · band
 *
 * **No new threshold: the quantile IS the definition of square-on** — the same
 * move `NoiseFloor` makes in `smoothing.js` (measure the live level, use it,
 * never let it eat the thing it protects). Four properties, all load-bearing:
 *
 *  - **A point mass passes wholesale.** A quantile is a LEVEL, not a rate
 *    limiter: a user whose pose is habitually the same reads the same `w` every
 *    frame, so Q90 equals that value and every frame is admitted. An eye-level
 *    user's `w` is exactly 1.0 inside the cone, so `band = 1`, the test is
 *    `w ≥ 0.999` and the behaviour is **bit-identical to the measured-good
 *    one**. The laptop user's band centres on their own habitual pose instead.
 *  - **It cannot starve.** The band is a level the session's own distribution
 *    demonstrated ~4 frames of in 31, so there is always a best-available look
 *    to learn from. What it refuses is the WORSE 87% — which is the categorical
 *    refusal, unchanged, with the reference moved off the camera and onto the
 *    user.
 *  - **It cannot drift.** The band is the running MAXIMUM of that quantile, so
 *    a minute spent turned away cannot redefine turned-away as square-on: the
 *    ring empties, Q90 collapses, the band does not move, and every one of
 *    those frames is refused. That is the "bound it relative to the session's
 *    own best observed trust" requirement — and the bound and the band are the
 *    same object, so it needs no second coefficient. The recorded cost of the
 *    ratchet: a session that moves to a WORSE camera mid-way keeps the better
 *    reference and pauses further learning rather than re-adapting downward,
 *    which is exactly what the invariant already prescribes for an off-square
 *    pose ("what the last square-on look measured is what a turned head
 *    wears"), and it can only happen after the seat has already learned from
 *    the better data. `__ar.seat.squareOn` publishes the band, the live
 *    quantile and the admitted count so this is diagnosable live; `resetFit`
 *    clears it with the rest of the seat.
 *  - **The test is not an equality on a product.** That is what broke it. The
 *    level is an order statistic OF THE SAME product, drawn from the session
 *    that is being tested, so it moves with the distribution instead of
 *    standing in front of it; the harness asserts the point-mass property
 *    directly (a pose held constant at ANY pitch admits every frame).
 *
 * **The trust stays COMBINED (`w = wy·wp·wr`), and a visibility gate was
 * refused for a measured reason.** `min(facingTrust)` over the pad columns is
 * the predicate this comment used to ask for, and it is wrong: pitch does not
 * occlude a sidewall but it does degrade the reconstruction, and the surface
 * law is already wrong at 10–20° of YAW where both sidewalls are plainly
 * visible and `facingTrust ≈ 1`. A yaw-only gate was tried and measured: it
 * recovered pitch (excursion 2.44 → 0.69 mm) and took the wearer's actual
 * complaint backwards, the push past 40° of yaw going +0.51 → −1.43 mm under
 * the combined gate but **+0.91 under yaw-only**, i.e. worse than before any
 * of this work. Occlusion is not the only way a surface reading goes bad, so
 * the gate keeps grading the whole pose — it only stops pretending it knows
 * where the camera is.
 *
 * The residual cost is honest and recorded: pitch excursion 0.00 → 2.44 mm and
 * browse 1.17 → 2.79 mm, the standoff going stale while off-square and the
 * guard bridging it (3 → 13 pushes). The wearer validated this trade live
 * ("much better") on the axis they raised; the pitch tail is the open item.
 *
 * `SEAT_REF_SLACK` is the old 0.999 keeping the ONE job its own comment always
 * claimed — "float equality on a product of three smoothsteps would be a coin
 * toss at the boundary" — and losing the job it was silently doing, which was
 * defining square-on as a cone about the camera. It is a relative slack now, so
 * it means the same thing at every band.
 */
const SEAT_REF_QUANTILE = 0.9;
const SEAT_REF_SLACK = 0.999;

/** Hermite smoothstep, rising 0 → 1 across [edge0, edge1]. */
function smoothstep(edge0, edge1, x) {
  const t = Math.min(Math.max((x - edge0) / (edge1 - edge0), 0), 1);
  return t * t * (3 - 2 * t);
}

/**
 * Folds this frame's pose trust into the square-on band (see SEAT_REF_QUANTILE).
 *
 * The ring is `FIT_WINDOW` long, and it has to be: the band decides admission
 * to a `FIT_WINDOW`-slot window (`needEst`), so a band measured over a longer
 * horizon would admit more samples than the window can hold and one measured
 * over a shorter one would refuse samples it has room for. One window, one
 * horizon. `FIT_WINDOW` being odd is the other half — it is why its own median
 * is exact, and it makes the decile `sorted[floor(0.9·30)]` an exact order
 * statistic rather than an interpolation between two.
 *
 * **The cold-start rule is that rule, not a number: there is no band until the
 * ring that measures it is full.** A quantile over a partly-filled ring is a
 * quantile of a different distribution, and the honest cold value is therefore
 * today's cone (`band = 1`), which is today's behaviour exactly — 31 frames is
 * ~1 s at 30 fps, well inside the settle budget, and nothing is visible in it
 * because a pre-solve session is already wearing the standalone raw law. The
 * cold value is deliberately NOT recorded into `best`: it is a fallback, not an
 * observation, and letting it ratchet the band to 1.0 would reproduce the very
 * bug this replaces.
 *
 * Insertion sort over 31 elements into a held scratch array — `NoiseFloor`'s
 * own arithmetic, no allocation, ~0.5 µs.
 */
function updateSquareOnBand(seat, wPose) {
  const sq = seat.squareOn;
  const w = wPose ?? 1;
  sq.frames++;
  sq.wMin = Math.min(sq.wMin, w);
  sq.wMax = Math.max(sq.wMax, w);
  sq.ring[sq.index] = w;
  sq.index = (sq.index + 1) % FIT_WINDOW;
  if (sq.fill < FIT_WINDOW) sq.fill += 1;
  if (sq.fill < FIT_WINDOW) {
    sq.live = null;
    sq.q10 = null;
    sq.q50 = null;
    sq.band = 1;
    return;
  }
  const sorted = sq.sorted;
  for (let i = 0; i < FIT_WINDOW; i++) {
    const value = sq.ring[i];
    let j = i - 1;
    for (; j >= 0 && sorted[j] > value; j--) sorted[j + 1] = sorted[j];
    sorted[j + 1] = value;
  }
  sq.live = sorted[Math.floor(SEAT_REF_QUANTILE * (FIT_WINDOW - 1))];
  sq.q10 = sorted[Math.floor(0.1 * (FIT_WINDOW - 1))];
  sq.q50 = sorted[(FIT_WINDOW - 1) >> 1];
  // The ratchet: rises to a better demonstrated level, never falls. This IS the
  // drift bound (see SEAT_REF_QUANTILE) — one object, not a band plus a floor.
  //
  // MEASURED NEGATIVE RESULT, 2026-08-18, written down so the next attempt does
  // not repeat it. The ratchet does starve the seat at an off-axis camera, and
  // the mechanism is exact: a running maximum of a NOISY statistic converges on
  // the global maximum of `w`, and `0.999 ×` a global maximum is very nearly a
  // measure-zero set. At eye level `w` is pinned at 1 so the band is 1 and every
  // frame passes forever; at a camera 30° below the eyes `w` wanders, the bar
  // climbs to the session's single best frame, and admission decays with session
  // length — 75 of the first 300 frames, then NINETEEN MORE in the next 1500,
  // three solves in a minute against a hundred and twelve at eye level.
  //
  // Bounding the ratchet's memory to `SETTLE_WINDOW_S` (a two-epoch rolling
  // maximum) fixes the starvation and does not fix the thing the starvation was
  // suspected of causing: admitted 94 → 159 and solves 3 → 7 at 30° over 60 s,
  // and the three cameras' disagreement moved from 0.818 mm to 0.826 mm. It also
  // costs the property the ratchet exists for — a minute turned away drops the
  // band to the turned-away level, which is the pinned "a minute turned away
  // cannot redefine square-on" check going red, and re-admits exactly the hard-
  // yaw solves the latch was built to refuse. Bought nothing, cost the safety:
  // reverted. The disagreement's real cause is one layer up (see the ladder).
  if (sq.live > sq.best) sq.best = sq.live;
  sq.band = sq.best;
}

/**
 * This frame's square-on verdict — ONE expression with three consumers: the
 * standoff reference's admission, the resting height's ease, and the solve
 * scheduler's refusal. It used to be three copies of the same comparison
 * against the same absolute constant, which is the duplicated-literal class
 * that has already produced drift elsewhere in this tree; a band that lived in
 * three places would drift the same way.
 */
function isSquareOn(seat, wPose) {
  return (wPose ?? 1) >= SEAT_REF_SLACK * seat.squareOn.band;
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
 * Half a pixel: the resolution below which a displacement on this screen is
 * not a displacement at all.
 *
 * Not a new number — it is the one this tree already argues every visibility
 * claim against, in three places that each wrote it out separately: the relief
 * cap's own effect deadband (`RELIEF_DEADBAND_PX`, occluder.js), the settle
 * metric's rejected absolute band (settle.js), and the roll's `0.235°` visible
 * angle (fit.js). Stated once here so the conversions below can reference the
 * claim instead of restating it.
 */
export const VISIBLE_PX = 0.5;

/**
 * The standoff channel's effect deadband: the ease only re-arms when its
 * target has moved past the re-arm distance from the value it is holding, and
 * goes back to holding once the eased value lands within the release distance
 * of the target. Between those, the seat literally RESTS — zero movement, not
 * small movement.
 *
 * Two floors, and until 2026-08-18 only one of them was real.
 *
 * **The noise floor is the derivation and it stands.** 0.15 mm is more than
 * twice the measured 0.02–0.08 mm noise of the eased push, so noise can never
 * wake the channel; the 0.05 mm release is the hysteresis, because one
 * threshold would chatter exactly at its own edge — the latch lesson this
 * codebase already paid for once.
 *
 * **The visibility floor was one session's arithmetic, and the arithmetic was
 * wrong.** The comment here claimed 0.15 mm was "0.026 px at 45 cm
 * (1.74 px/mm)". 0.15 × 1.74 = 0.261 px, not 0.026 — a factor of ten, in the
 * flattering direction, and the same slip appears on `FIT_DEADBAND.eyeLineY`
 * and on the >40° recovery note below. Corrected, the shipped value is 0.26 px
 * at THAT session's scale: under half a pixel, so the claim survives — barely
 * — for that wearer at that distance. It does not survive generally. px/mm is
 * a property of the camera, the resolution and how far away the wearer is
 * sitting, not of the pipeline: this repository's own two capture sessions
 * measured **1.74 and 4.22 px/mm**, a factor of 2.4 apart, and at 4.22 the
 * shipped re-arm is **0.63 px** — over the half-pixel bar, i.e. a rest the
 * wearer can see the frame taking.
 *
 * So the visibility floor stops being a constant and becomes the measurement:
 * `VISIBLE_PX / imageScale`, from `measureImageScale` below, live every frame.
 * The channel takes the SMALLER of the two, which is what makes this a strict
 * improvement rather than a re-tuning — the noise floor can only be relaxed by
 * a claim about noise, never by a claim about pixels, and the shipped pair is
 * the answer wherever the wearer sits far enough away for it to be invisible.
 * At the fixture that pinned these numbers the visibility bound is 0.287 mm
 * against the shipped 0.15, so the shipped value wins and the replay is
 * untouched; a wearer holding a phone at arm's length gets 0.118 mm instead.
 */
const ZETA_REARM = 0.015;
const ZETA_RELEASE = 0.005;
/** The shipped pair's hysteresis ratio, kept when the bound moves. */
const ZETA_HYSTERESIS = ZETA_RELEASE / ZETA_REARM;

/**
 * How many image pixels one face-space centimetre spans, this frame.
 *
 * The pipeline's absolute ruler for LENGTH is the iris (`metricScale`); this
 * is its ruler for VISIBILITY, and the two are independent — a wearer with an
 * average head at arm's length and one with an average head at a metre have
 * the same `metricScale` and a three-fold difference in what they can see.
 *
 * Exact, and pose-free by construction. A face-space offset `r` from the head
 * origin lands `headScale·r` centimetres from it in camera space; at depth `d`
 * under a vertical field of view `fov`, one camera centimetre perpendicular to
 * the view is `heightPx / (2·d·tan(fov/2))` pixels. Both factors are already
 * measured — `headScale` is the pose fit's own scale, `d` is the pose's own
 * translation, `fov` is the camera the whole pipeline unprojects through, and
 * `heightPx` is the frame being drawn into. Nothing is assumed and no landmark
 * pair is differenced, so it does not foreshorten with yaw the way a temple
 * span would.
 *
 * `null` when the pose is degenerate, which is the caller's cue to keep the
 * shipped floor rather than invent a scale.
 */
function measureImageScale(headScale, depthCm, fovDeg, heightPx) {
  const d = Math.abs(depthCm);
  const tanHalf = Math.tan((fovDeg * Math.PI) / 360);
  if (!(d > 1e-6) || !(heightPx > 0) || !(tanHalf > 1e-9) || !(headScale > 1e-9)) return null;
  const px = (headScale * heightPx) / (2 * d * tanHalf);
  return Number.isFinite(px) && px > 0 ? px : null;
}

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
 * How many solves the resting-height estimate stands on — the same look-back in
 * SECONDS that the stab meter and the settle metric use, expressed at the
 * cadence solves actually arrive. See `restEst` for what a window stated in
 * slots instead of seconds cost when it was measured.
 */
const REST_WINDOW = Math.round(SETTLE_WINDOW_S / SOLVE_MIN_INTERVAL);

/**
 * G1's confidence scale is MEASURED AGREEMENT, not elapsed evidence.
 *
 * It used to be `clamp(noseMeanW / 50, 0, 1)` — the person model's accumulated
 * weight against a fixed count of "enough" frames. Fifty unit-weight frames is
 * about two seconds, and every wearer paid those two seconds whatever their
 * data was worth. Measured on the fifteen-subject set, that clock is wrong in
 * both directions at once: the canonical face's solve answers −1.50 mm on frame
 * one and −1.375 mm nineteen seconds later (a journey of 0.125 mm, inside the
 * channel's own 0.3 mm deadband) and still waited 2.9 s; while S08's solve
 * disagrees with itself across 1.5 mm all session and was believed at full
 * confidence anyway, walking the applied height up and down the wedge for 15.2 s.
 *
 * So the scale is now the seat's own answers grading themselves: the solved
 * heights go into a bounded window, and `conf` is that window's positive-part
 * empirical-Bayes shrinkage `max(0, 1 − sigma²/value²)`, with `sigma` the
 * location's own standard deviation from the MEASURED scatter, an effective
 * sample count discounted by the MEASURED lag-1 autocorrelation, and a floor at
 * the solve's own `S_REFINE` resolution. See `agreement.js` for every line of
 * the derivation; nothing here chooses a number, and `CONF_FULL_W` is gone.
 *
 * A cold session still holds today's optical height BY CONSTRUCTION, and for a
 * better reason than before: scatter is undefined on a single observation, so
 * the first solve earns `conf = 0` exactly rather than a small number that
 * happened to fall under the deadband.
 */

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
   * larger than the converged median's frame-to-frame noise, so the eye line
   * rests between real changes the way the widths do.
   *
   * It used to also claim "smaller than anybody can see (0.035 px at 45 cm)".
   * That was the same factor-of-ten slip `ZETA_REARM` carried: 0.2 mm at that
   * session's 1.74 px/mm is **0.35 px**, and at the diag stills' 4.22 px/mm it
   * is 0.84 px — visible. The claim is withdrawn rather than repaired, because
   * this deadband does not need it: it is on a CARRIED PROPORTION whose whole
   * job is to stop a median creeping, and the thing that would be seen is the
   * creep it refuses, not the step it permits. The visibility argument belongs
   * where a channel eases toward a target, which is the seat's, and there it
   * is now measured (see `VISIBLE_PX`).
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
    needEst: createAgreement(FIT_WINDOW),
    /**
     * The carried estimate of the SOLVED RESTING HEIGHT, and the source of the
     * seat's confidence (2026-08-18).
     *
     * Same window, same grammar, same eviction as `needEst` above — the two are
     * literally one estimator with two instances, which is the point: the seat
     * has exactly one idea of what "the carried estimate of a face-space
     * constant" means. What it adds is that the window also GRADES itself, and
     * that grade is what `conf` is (see the G1 note above and `agreement.js`).
     *
     * The height needed this treatment more than the standoff did and had never
     * had it. `sTarget` used to be `conf · seat.sStar` — the LATEST solve, raw,
     * with no window at all — so a single freak answer moved the applied height
     * the moment confidence was full. Measured: S06's solve reads −0.25 mm for
     * the whole session except two answers of −0.5 mm at t ≈ 8 s, and those two
     * alone dragged the applied height off zero and the standoff behind it,
     * which is the entire 9.2 s that subject's standoff took to settle. A
     * median over the window ignores them by construction.
     *
     * `resolution` is the solve's own bisection step (`S_REFINE`), because a
     * quantised observer cannot disagree with itself by less than its quantum
     * and a window of identical grid answers must not be allowed to claim
     * infinite precision from two lucky samples.
     *
     * THE CAPACITY IS STATED IN TIME, and it is the one thing here that is NOT
     * `needEst`'s. `FIT_WINDOW` is 31 slots because that is about a second of a
     * 30 Hz stream, which is what the anchors and the standoff readings are.
     * Solves arrive at 2 Hz, so the same 31 slots would be a FIFTEEN-SECOND
     * memory — longer than the whole convergence it is supposed to describe. It
     * was measured being exactly that: on the wearer's own recording the solve's
     * answer steps from 0 to −11.3 mm at 3.6 s (the surface finishing its own
     * measurement), and a 31-solve window went on holding the pre-step answers
     * for ten seconds after they stopped being true. So the window looks back
     * `SETTLE_WINDOW_S` — the same 5 s the stab meter and the settle metric look
     * back, already derived as 2.5 periods of the measured 0.5 Hz gaze coupling
     * — at the solve cadence the scheduler itself enforces. The estimator
     * remembers exactly as far as the instrument that grades it, and no further.
     */
    restEst: createAgreement(REST_WINDOW, S_REFINE),
    /**
     * The carried estimate of the SOLVED BALANCING ROLL — the same estimator a
     * third time, and for the same reason both the others have it.
     *
     * The roll cannot ride the HEIGHT's confidence, which was the obvious thing
     * and is wrong in a way that is invisible until measured: `conf` is a
     * shrinkage of the height toward the prior `s = 0`, so on any face whose
     * seat does not descend — twelve of the fifteen synthetic subjects on the
     * shipped asset — the height is exactly 0, the shrinkage is exactly 0, and
     * a roll gated on it is dead for the whole session. The height's confidence
     * is a statement about the height and about nothing else.
     *
     * Its own window says the same thing about the roll: shrink the solved
     * angle toward the prior "no roll" by how much the session's own solved
     * angles agree with each other. One observation earns exactly zero, which
     * is what keeps frame one bit-identical to the standalone law. And because
     * `phiStar` is the ABSOLUTE balanced angle rather than an increment — the
     * secant Newton step returns the same answer from any base — shrinking it
     * has no fixed-point problem: a partly-applied roll does not make the next
     * solve ask for more.
     *
     * No resolution floor: unlike the height, the roll is not bisected onto a
     * grid, so it has no quantum of its own to be floored at.
     */
    rollEst: createAgreement(REST_WINDOW),
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
    /** The re-arm distance actually in force — min(noise floor, half a pixel
     * at the measured image scale). Published so a live session can read which
     * of the two bounds it is sitting on. */
    zetaRearm: ZETA_REARM,
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
    /**
     * Frames on which the non-penetration guard hit `GUARD_MAX`. Here, on the
     * per-session seat state, rather than in `fit.js`'s module scope where it
     * used to live: `resetFit` clears `state.seatConfig` whole, so a wearer's
     * cap overflows are their own. A module-level counter reported the
     * PREVIOUS person's total into the next person's session and made the
     * telemetry replay order-dependent within one process. `solvePlacement`
     * takes this object as its `stats`.
     */
    guardOverflow: 0,
    /** Solve attempts refused for pose trust below the stage-6 bar —
     * counted apart from `holds` so the replay can tell "the field said
     * hold" from "the pose was not asked". Monotone, like the rest. */
    refusals: 0,
    /**
     * Solves that ran the whole sweep and found NO bearing height, at any roll
     * the frame is allowed — the seat honestly falling back to the pre-stage-5
     * optical height on a nose these pads cannot take two-sided.
     *
     * A counter and not just a mode string, because the mode is an instant and
     * this is a session property: a wearer whose frame never bears wants that
     * visible over a minute, not in whatever frame happens to be sampled. It
     * was the silence the generality sweep named — an asymmetric wearer got
     * the 1-DOF seat and nothing said so.
     */
    noBearing: 0,
    /** Solves that seated on the bridge saddle: the frame's bridge reaches the
     * nose before its pads can, at every height in the box. A true statement
     * about the pairing (a wide bridge on a narrow high nose), counted so a
     * catalogue can see which frames answer which faces that way. */
    saddles: 0,
    /** Solves whose bearing needed the balancing roll — this wearer's nose is
     * asymmetric enough that the frame has to follow it. */
    rollBore: 0,
    /**
     * The square-on band's estimator state (see SEAT_REF_QUANTILE). It lives
     * HERE, on the seat state, for the isolation reason and not for tidiness:
     * `resetFit` clears `state.seatConfig` whole, so one user's pose
     * distribution cannot define square-on for the next one. A module-level
     * ring would be exactly the leak class the isolation work is chasing.
     */
    squareOn: {
      ring: new Float64Array(FIT_WINDOW),
      sorted: new Float64Array(FIT_WINDOW),
      fill: 0,
      index: 0,
      /** The level in force: the session's best demonstrated top decile. */
      band: 1,
      /** This frame's own top decile, null until the ring is full. */
      live: null,
      q10: null,
      q50: null,
      /** The ratchet's high-water mark. Never seeded from the cold fallback. */
      best: 0,
      wMin: Infinity,
      wMax: 0,
      frames: 0,
      admitted: 0,
    },
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
function easeSeatChannels(seat, fit, dt, wPose, imageScale = null) {
  seat.sinceSolve += Math.max(dt, 0);
  // The confidence the height rides is the SOLVE WINDOW'S OWN GRADE, folded by
  // `scheduleSeatSolve` at the last adopted solve — last frame's reading, the
  // same one-frame staleness the ζ target has always had, on a channel whose
  // fastest time constant is 0.15 s. The person model is not consulted: how
  // many frames it has seen is not the question, whether the seat's answers
  // agree with each other is.
  seat.conf = seat.restEst.conf;

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
  // Square-on only (see SEAT_REF_QUANTILE). The weighted-median grammar below
  // is kept — it still refuses noise and a single freak reading — but the gate
  // in front of it is categorical, because the failure it exists to stop
  // arrives at full weight; and its reference is this session's own top decile,
  // because a camera is not anybody's eye level.
  //
  // The band is folded FIRST, on every eased frame, so all three consumers ask
  // the same question of the same frame. (Held under 'frozen' with the rest of
  // the estimator state, because `easeSeatChannels` is not called at all — the
  // pose distribution of a frozen warm-up is not this session's.)
  updateSquareOnBand(seat, wPose);
  const seatSquareOn = isSquareOn(seat, wPose);
  if (seatSquareOn) seat.squareOn.admitted++;
  if (seat.rawNeeded !== null && seatSquareOn) {
    foldAgreement(seat.needEst, seat.rawNeeded, wPose);
  }
  seat.applied.needRef = seat.needEst.value;
  // Published for `solvePlacement`'s guard: penetration is only answerable
  // from a pose that can actually see the contact (see SEAT_REF_QUANTILE).
  seat.applied.squareOn = seatSquareOn;

  if (!seat.hasSolve) return;

  const alpha = (tau) => 1 - Math.exp(-Math.max(dt, 0) / tau);

  // The resting height latches on exactly the same argument as the standoff
  // (SEAT_REF_QUANTILE), and it needs saying because the solve gate alone did not
  // cover it: `sTarget` is `conf · sStar`, and `conf` climbs as the person
  // model accumulates — so the height kept easing DOWN THE WEDGE at any pose,
  // with no new solve involved, and the wedge has a forward component. The
  // z-decomposition caught it as the last term standing at >40° once ζ and the
  // guard were latched (dRest +3.06 mm on the yaw sweep's 40+ bucket, against
  // +1.57 frontal in the same segment). Where the frame rests on the nose is a
  // face constant; turning cannot slide it. The confidence ramp simply pauses
  // while off-square and resumes on the next square-on look.
  //
  // The target is the CARRIED estimate of the solved height, not the latest
  // solve (2026-08-18 — see `restEst`), for the same reason the ζ target became
  // the carried estimate the day before: the resting height is a face-space
  // constant, and the ease's job is to settle onto the ESTIMATE of that
  // constant, which only moves when admitted evidence moves the window's
  // median. One freak solve now moves the applied height by nothing at all.
  const sTarget = seat.conf * (seat.restEst.value ?? 0);
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
  // wearer's own frontal reference, so the standoff settles back onto it,
  // through the same deadband as ever. (The "≈ 0.4 px" this note used to
  // carry was the third instance of the same factor-of-ten slip: 2.5 mm at
  // 1.74 px/mm is 4.4 px, and the recovery is a visible settle — which is
  // what SEAT_TAU's 0.15 s ease is FOR. The motion is deliberate and eased,
  // not invisible, and calling it sub-pixel was the only part that was wrong.)
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
  // LATCHED off-square (see SEAT_REF_QUANTILE). Not "eased more slowly", not
  // "frozen at a bar chosen to be safe" — simply not touched. The number the
  // last square-on look measured is the number a turned head wears, because
  // that is what a pair of glasses does.
  if (seatSquareOn) {
    // The two floors, taken at their tighter (see ZETA_REARM): the noise floor
    // the pair was derived against, and half a pixel at THIS frame's measured
    // image scale. `imageScale` null — a degenerate pose, or a caller with no
    // camera — keeps the shipped pair, because "no measurement" must never
    // read as "no bound".
    const visible = imageScale ? VISIBLE_PX / imageScale : Infinity;
    const rearm = Math.min(ZETA_REARM, visible);
    const release = rearm * ZETA_HYSTERESIS;
    seat.zetaRearm = rearm;
    const est = seat.needEst;
    const zetaTarget = est.value !== null
      ? est.value
      : (seat.rawNeeded ?? seat.zetaAt(seat.applied.s));
    if (seat.zetaHolding && Math.abs(zetaTarget - seat.zetaHeld) > rearm) {
      seat.zetaHolding = false;
    }
    if (!seat.zetaHolding) {
      seat.applied.zeta += (zetaTarget - seat.applied.zeta) * alpha(SEAT_TAU);
      if (Math.abs(seat.applied.zeta - zetaTarget) <= release) {
        seat.zetaHolding = true;
        seat.zetaHeld = zetaTarget;
      }
    }
  }

  // The roll rides its OWN window's grade, exactly as the height rides its.
  //
  // Target `conf_roll · median(φ*)`, not the latest `φ*`: a roll is an estimate
  // of the wearer's own nasal asymmetry, one observation measures no scatter, so
  // the grade is exactly zero and the target is exactly zero. That is what keeps
  // FRAME ONE bit-identical to the standalone law with the balance lit — the
  // no-scan-phase invariant, which a whole-adopted first roll broke the moment
  // the flag went on (measured: the cold-session pin went red on 'the roll
  // exactly 0'). The median rather than the latest for the same reason the
  // height took one on 2026-08-18: a single freak solve must not cock the frame.
  //
  // And LATCHED off-square, exactly as the height above is. `isSquareOn` was
  // documented as "ONE expression with three consumers"; the roll was a fourth
  // channel quietly left out of it, and it is the one whose evidence a hard pose
  // destroys most completely — past 40° of yaw the far pad has left the modelled
  // patch, so the pad-load DIFFERENCE the roll is made of is exactly the
  // quantity that cannot be read there. Kept on that argument and not on a
  // measured win: on the wearer's own recording it moved the >40° glances mean
  // by 0.002 mm (2.0337 → 2.0353), which is to say not at all. It matters when
  // the balance is lit; while the balance is dark it is inert.
  if (fit.padBalance === true) {
    if (seatSquareOn) {
      const phiTarget = seat.rollEst.conf * (seat.rollEst.value ?? 0);
      seat.applied.phi += (phiTarget - seat.applied.phi) * alpha(PHI_TAU);
    }
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
  // The bar is the square-on band, the same question the reference and the
  // guard now ask, through the same one expression (2026-08-17). It was 0.3,
  // and 0.3 was measured letting the solve adopt equilibria off a surface
  // already inflated at 10–20° — the rest channel then walked the frame down a
  // wedge that is not there (+0.3 to +2.1 mm mean, +4.3 worst). One pose
  // teaches the seat; every other pose wears what it taught. Which pose that is
  // is now a fact about this session's camera rather than a cone about the
  // camera axis (see SEAT_REF_QUANTILE) — the band was folded by
  // `easeSeatChannels` earlier this same frame, so the two agree by
  // construction rather than by two copies of a literal.
  if (!isSquareOn(seat, wPose)) {
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

  if (solve.mode === 'unbalanced') seat.noBearing++;
  if (solve.mode === 'saddle') seat.saddles++;
  if (solve.rollBore) seat.rollBore++;

  const first = !seat.hasSolve;
  seat.hasSolve = true;
  seat.solve = solve;
  seat.mode = solve.mode;
  seat.sStar = solve.sStar;
  seat.zetaAt = solve.zetaAt;
  seat.phiStar = solve.phiStar;

  // The answer joins the window and the window re-grades itself, in that order,
  // so `conf` below is a statement about evidence that INCLUDES this solve.
  // Weighted at this frame's pose trust, exactly as the standoff's readings
  // are — and every solve that reaches this line already passed the square-on
  // gate, so the weight is a fine grading of admitted looks, never the gate
  // itself. `S_REFINE` is the window's resolution floor (see `restEst`).
  foldAgreement(seat.restEst, solve.sStar, wPose);
  seat.conf = seat.restEst.conf;
  foldAgreement(seat.rollEst, solve.phiStar ?? 0, wPose);

  if (first) {
    // Adopted whole, through the channels' own deadbands: the height starts
    // at zero unless the confidence-scaled target has already left the
    // deadband, and on a true frame one it has not — a single observation
    // measures no scatter, so `conf` is exactly zero and the target is exactly
    // zero. (Under the frame-count ramp this was an arithmetic accident:
    // conf ≤ 1/50 and |s*| ≤ 12 mm put the target under 0.24 mm, just inside
    // the 0.3 mm deadband. Same applied height, now by construction.) The
    // standoff adopts the sweep's answer at that height verbatim, exactly as
    // the scalar seat adopted its first push.
    const sTarget = seat.conf * (seat.restEst.value ?? 0);
    seat.applied.s = Math.abs(sTarget) > REST_DEADBAND ? sTarget : 0;
    seat.applied.zeta = solve.zetaAt(seat.applied.s);
    seat.zetaHolding = true;
    seat.zetaHeld = seat.applied.zeta;
    // Graded by its own window exactly as the height above is, and on a true
    // frame one that grade is exactly zero — so the first adoption applies NO
    // roll, whatever the solve found. See `easeSeatChannels`.
    seat.applied.phi = fit.padBalance === true
      ? seat.rollEst.conf * (seat.rollEst.value ?? 0) : 0;
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
   * Anchoring-v3 instrument: which pin
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
}) {
  // A face in frame ends any run of lost ones. See `noteFaceLost`.
  state.lostSeconds = 0;

  // MediaPipe hands back a column-major 4x4, which is the layout three expects.
  poseMatrix.fromArray(detection.matrix);
  poseMatrix.decompose(rawPosition, rawQuaternion, rawScale);

  // The transform is a similarity: one rotation, one translation, one uniform
  // scale. Averaging the three guards against a near-degenerate decomposition.
  const rawHeadScale = (rawScale.x + rawScale.y + rawScale.z) / 3;

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
  // nothing anywhere (offsets zero, pin maturity zero, parallax zero),
  // which is one of the three mechanisms that keep frame one bit-identical.
  if (adaptToFace && !state.person) state.person = createPersonModel(face);
  const person = adaptToFace ? state.person : null;

  // Wrapped rather than inlined because the identity conviction below has to be
  // able to run it a second time: every input marked "one frame stale" is a
  // statement about the PREVIOUS person, and on the one frame that decides the
  // person changed, "previous" means somebody else. See the re-measure there.
  // The rail tally (`__ar.rails`), created with the session and cleared with it.
  // A clamp that rewrites a face has to be visible in production or the bound
  // is unfalsifiable — see `createRailCounter`.
  //
  // Re-read off `state` at every call rather than captured once, and that is not
  // a style choice: `measureObserved` runs a SECOND time on the frame an
  // identity conviction fires, after `resetFit` has cleared the field. A captured
  // reference would post the new wearer's first measurement into the old
  // wearer's counter and leave `state.rails` empty until the next frame, so the
  // swapped session and a cold one would disagree by exactly one frame — which
  // is how the isolation proof caught it.
  const railsOf = () => state.rails ?? (state.rails = createRailCounter());

  const measureObserved = () => clampAnchors(measureAnchors({
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
  }), face, railsOf());
  let observed = measureObserved();

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
  //
  // Wrapped, like `measureObserved` above and for the same reason: the identity
  // conviction below empties the neutral reference mid-frame, and the reading
  // itself belongs to the NEW wearer (these are their landmarks) — only the
  // reference it was compared against belonged to the last one. Re-run after the
  // reset, the new session seeds its neutral from this frame, which is what a
  // cold session does; left un-run, the new wearer's first frame has no gaze
  // signal at all and the seed slips to their second.
  const measureGaze = () => {
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
  };
  measureGaze();
  let gazeNeutral = state.gaze ? state.gaze.neutral : true;
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
          resetFit(state, smoother);
          // ...and this frame's own sample is re-measured against the cleared
          // estimators before it is admitted.
          //
          // The measurement above consumed two things that describe the
          // PREVIOUS person: the occluder's depth fit ("one frame stale", and
          // on this one frame the previous frame belongs to somebody else) and
          // the carried surface underneath it. Admitted as it stood, it made
          // the new wearer's frame-one fit — the sample that is adopted whole,
          // by the invariant — the one sample in their whole session measured
          // through the last person's depth EMA, and every deadbanded field
          // then carried that offset until something moved it. The alternative
          // of refusing the sample is worse: the anchors fall back to canonical
          // for a frame, so the swap becomes two visible steps instead of one.
          // Re-measuring costs a dozen unprojects on an event that fires at
          // most once per wearer, and it is what makes frame one of the new
          // session bit-identical to frame one of a cold one — which the
          // isolation check asserts across the whole trajectory rather than
          // taking on trust.
          observed = measureObserved();
          // ...and so is the gaze reading, for the same reason one layer over:
          // the delta above was this wearer's eyes against the LAST wearer's
          // neutral. Re-seeded here, the new session's admission door opens on
          // its own first frame instead of on its second.
          measureGaze();
          gazeNeutral = state.gaze ? state.gaze.neutral : true;
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
  // now rides the estimate, not the instantaneous landmark. The
  // pose-systematic slide the landmark was compensating at extremes is a
  // known, accepted residual: it rides the MediaPipe matrix's own error at
  // extreme yaw, and nothing downstream of here corrects it.
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
  // per-still-cold) is UNCORRECTED and known: a rigid-subset pose refit was
  // built and measured against it, and lost the wearer's live A/B, so the
  // slide is accepted rather than traded for the refit's own failure modes.
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
  // What one face-space centimetre is worth in pixels, this frame, on this
  // camera, at this distance. Measured rather than assumed — see
  // `measureImageScale`. Kept on the state so the readouts and the harness can
  // both see the number the deadbands are being taken against.
  state.imageScale = measureImageScale(
    headScale, position.z, scene.camera.fov, source.height,
  );
  if (pinMode !== 'frozen') easeSeatChannels(seatState, fit, dt, wPose, state.imageScale);

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
    stats: seatState,
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
      stats: seatState,
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
    // The give-ups, as session counts rather than as this frame's mode. A
    // non-zero `noBearing` says the seat ran and could not find a two-sided
    // rest; `saddles` says the frame bears on its bridge, which is an answer
    // rather than a failure; `rollBore` says the balance is carrying the fit.
    readout.noBearing = seatState.noBearing;
    readout.saddles = seatState.saddles;
    readout.rollBore = seatState.rollBore;
    readout.asymMm = seatState.solve?.asym != null ? seatState.solve.asym * 10 : null;
    readout.handednessMm = seatState.solve?.handedness != null
      ? seatState.solve.handedness * 10 : null;
    // The carried surface reference and its evidence mass (2026-08-17) —
    // beside the raw law it admits from, so a live session shows the
    // refusal working: at a hard pose `needMm` (the raw morph) climbs while
    // `needRefMm` stands.
    readout.needRefMm = seatState.needEst.value !== null ? seatState.needEst.value * 10 : null;
    readout.needMass = seatState.needEst.weight;
    readout.needMm = seatState.rawNeeded !== null ? seatState.rawNeeded * 10 : null;
    // The height's carried estimate and the grade `conf` is READ FROM
    // (2026-08-18). Published beside `sRestMm`, the latest raw solve, because
    // the pair is the whole diagnosis in a live session: `sRestMm` jumping
    // around `sRestEstMm` while `restSigmaMm` is large IS "this wearer's seat
    // is not determined", and it is now visible instead of inferred. `restNEff`
    // beside `restN` shows how much of the window's count the measured
    // autocorrelation actually bought.
    readout.rollEstDeg = seatState.rollEst.value !== null
      ? seatState.rollEst.value * (180 / Math.PI) : null;
    readout.rollConf = seatState.rollEst.conf;
    readout.sRestEstMm = seatState.restEst.value !== null ? seatState.restEst.value * 10 : null;
    readout.restSigmaMm = seatState.restEst.sigma !== null ? seatState.restEst.sigma * 10 : null;
    readout.restScaleMm = seatState.restEst.scale !== null ? seatState.restEst.scale * 10 : null;
    readout.restRho = seatState.restEst.rho;
    readout.restN = seatState.restEst.n;
    readout.restNEff = seatState.restEst.nEff;
    readout.phiDeg = seatState.applied.phi * (180 / Math.PI);
    readout.solves = seatState.solves;
    readout.holds = seatState.holds;
    readout.refusals = seatState.refusals;
    // The square-on band, live (see SEAT_REF_QUANTILE). This is the readout
    // that would have caught the camera-geometry bug in a live session
    // instead of in an audit: `admitted` at 0 with `frames` in the hundreds
    // says "this camera never gets a look in", and `band` beside `live` says
    // whether the reason is the ratchet or the pose. One reused object.
    {
      const sq = seatState.squareOn;
      const band = readout.squareOn ?? (readout.squareOn = {});
      /** The bar in force this frame: w must reach SEAT_REF_SLACK × this. */
      band.band = sq.band;
      band.barrier = SEAT_REF_SLACK * sq.band;
      /** This frame's own top decile over the last FIT_WINDOW frames. */
      band.live = sq.live;
      /** The band's inputs: the same ring's other quantiles. */
      band.q10 = sq.q10;
      band.q50 = sq.q50;
      /** Session-long extremes of the pose trust itself. */
      band.wMin = Number.isFinite(sq.wMin) ? sq.wMin : null;
      band.wMax = sq.wMax;
      band.frames = sq.frames;
      band.admitted = sq.admitted;
      band.warm = sq.fill >= FIT_WINDOW;
    }
    readout.solveAgeMs = Number.isFinite(seatState.sinceSolve)
      ? seatState.sinceSolve * 1000 : null;
    readout.deadbandHolding = seatState.zetaHolding && !seatState.sSettling;
    // The image scale and the bound it is buying: `zetaRearmMm` is the smaller
    // of the noise floor and half a pixel HERE, and `zetaRearmVisible` says
    // which of the two is binding — the one live readout that tells a wearer
    // sitting too close why the standoff channel got twitchier.
    readout.pxPerCm = state.imageScale;
    readout.zetaRearmMm = seatState.zetaRearm * 10;
    readout.zetaRearmVisible = state.imageScale !== null
      && VISIBLE_PX / state.imageScale < ZETA_REARM;
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
    width: widthVerdict({ model, anchors, placement, face }),
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

/*
 * ===========================================================================
 * THE ISOLATION BOUNDARY
 * ===========================================================================
 *
 * One rule, stated here and enforced by `pipeline-check`'s `isolationSwap`:
 *
 *     Every piece of state that is a statement about A PARTICULAR PERSON, in
 *     front of a particular source, wearing a particular model, is enumerated
 *     in `PER_SESSION_STATE` below, is created lazily on the state object (or
 *     on `occluder.userData`), and is cleared through ONE entry point per
 *     reset class. Module-level mutable state is forbidden for anything
 *     person-derived, because module scope is the one place a reset cannot
 *     reach.
 *
 * The rule exists because this pipeline is a chain of estimators and every
 * one of them is a measurement of somebody. An estimator that survives a
 * change of wearer does not merely report the wrong number: it reports the
 * PREVIOUS person's number with the NEXT person's confidence, and every
 * mechanism downstream — admission doors, easing channels, warm starts,
 * shrinkage floors — is built to trust exactly that. The failures are
 * therefore silent and slow, which is why they need a manifest and a check
 * rather than a convention.
 *
 * THE FIVE RESET CLASSES, and what each must clear:
 *
 *   identity change   `isDifferentFace` convicts on IDENTITY_STRIKES
 *                     confident disagreements → `resetFit`
 *                     → everything person-derived.
 *   remeasure         the *Re-measure face* button, the *Adapt to face*
 *                     toggle, a new source → `remeasure`
 *                     → everything `resetFit` clears, plus the source-derived
 *                       fields (`templesAimed`, `lostSeconds`).
 *   source switch     camera/sample change, in `main.js`
 *                     → everything `remeasure` clears, plus the input clocks
 *                       and counters `main.js` owns (frame timing, dropped
 *                       frames, detect rotation, the source epoch) and the
 *                       pose filter WHOLE (`smoother.reset()`, level included
 *                       — a new source really is a new geometry, so the pose
 *                       is discontinuous and adopting frame one whole is
 *                       correct).
 *   model swap        a new glasses asset → asset-derived state only. The
 *                     face is deliberately untouched: the wearer did not
 *                     change, and re-measuring them because they tried on a
 *                     different pair is the scan phase in disguise. Note the
 *                     `needEst` window deliberately spans a model swap — the
 *                     standoff law legitimately changes and converges in
 *                     ~0.5–1 s at full trust, where an EMPTIED window at a bad
 *                     pose falls back to the raw morph exactly when a slider
 *                     is moved mid-turn.
 *   face loss         `LOST_SECONDS_BEFORE_RESET` → the pose filter's velocity
 *                     state ONLY. The fit is deliberately NOT touched: it used
 *                     to be, and every dropout long enough to cross the line
 *                     restarted the measurement, so users watched their
 *                     glasses re-size several times a minute.
 *
 * WHAT DELIBERATELY SURVIVES, and why (the interesting half of a boundary is
 * always the exceptions):
 *
 *   the smoothed POSE LEVEL — the composite is frame-locked, so a pose is a
 *     statement about a FRAME, not about a person. Nobody moved when the
 *     estimator changed its mind about whose face it is, and clearing the
 *     level would put a visible pop on screen for a reason the viewer cannot
 *     see. Only the two `NoiseFloor` calibrations go (`smoother.resetNoise`).
 *   lifetime event counters — `person.resets`, `person.decays`,
 *     `state.identity`'s four event counts, the occluder's rebuild counters
 *     and `frameCount`. A counter that resets with the thing it counts cannot
 *     report the reset; `state.identity.convictions` in particular is
 *     incremented on the same frame `resetFit` runs, so clearing it would make
 *     the conviction invisible to the replay that event-counts it. Attribution
 *     is restored instead by `state.sessionEpoch`, which counts resets, so any
 *     lifetime counter can be differenced across a boundary.
 *   the geometry — `face`, `scene`, `tracker`, `occluder`'s mesh/subdivision/
 *     materials, the loaded model. None of it is anybody's face.
 *
 * The manifest below is machine-readable on purpose: `resetFit` iterates it,
 * and so does the check. A field added to `state` without a manifest entry
 * fails `isolationSwap` — which is what turns this comment into an invariant.
 */
/**
 * The seat channels' own numbers, exported so the harness gates on the same
 * constants the channels run on rather than on copies of them. Every one is
 * derived and documented at its definition above; this block adds nothing.
 */
export const SEAT_CONSTANTS = {
  REST_TAU, REST_DEADBAND, SEAT_TAU, ZETA_REARM, ZETA_RELEASE,
  SOLVE_MIN_INTERVAL, SOLVE_HEARTBEAT, FIT_WINDOW, REST_WINDOW, SEAT_REF_SLACK,
  VISIBLE_PX, ZETA_HYSTERESIS,
};

export const PER_SESSION_STATE = {
  /**
   * Cleared by `resetFit` — every one of these is a statement about the
   * person in the chair. The value is what the field returns to, which is by
   * construction the value a session that never saw anybody else has.
   */
  perPerson: {
    /** The carried fit. */
    anchors: null,
    sampleSet: null,
    anchorSamples: 0,
    /**
     * A fresh face is a fresh seat. The solved configuration is the OLD
     * face's nose — its equilibrium height, its standoff, its roll; easing
     * the new wearer in from it drags a visible re-seat across their first
     * fraction of a second. Cleared whole (channels, solve, scheduler edges,
     * the square-on band, the guard-overflow count), so the next frame's
     * first solve is adopted whole.
     */
    seatConfig: null,
    /**
     * The gaze admission door's neutral reference — the single most damaging
     * survivor in the set, because it is the door in front of everything
     * else. A habitually left-looking wearer settles the neutral EMA 0.10 off
     * centre; the next person sits down looking straight ahead;
     * `|B − A_neutral| > GAZE_ADMIT` refuses EVERY one of B's samples, the
     * anchors stay canonical, the person model never accumulates, and B wears
     * the average face for the tens of seconds it takes a τ = 10 s EMA to
     * crawl onto them. Nothing on screen explains it.
     */
    gaze: null,
    /**
     * The identity streak. A conviction and an acquittal both zero it, but a
     * remeasure did not, so up to `IDENTITY_STRIKES − 1` strikes accumulated
     * against the previous person carried over and the next one convicted a
     * strike early. (The `state.identity` READOUT is a lifetime instrument —
     * see `allowedToSurvive`.)
     */
    identityStrikes: 0,
    /** The pin's reused scratch for the person model's bridge estimate. */
    bridgeEstimate: null,
    /** Readout mirrors of per-person estimators, rewritten every frame the
     *  estimator behind them runs; cleared so a stale line cannot be read
     *  against the wrong person. (`poseTrust` and `measuringLatch` are NOT
     *  here — see `allowedToSurvive`: they describe the pose, not the wearer.) */
    depthFit: null,
    seat: null,
    pin: null,
    /**
     * The `LIMITS` rail tally (`__ar.rails`). Person-derived twice over: it
     * counts how often THIS wearer's measurements fell outside the bounds, and
     * carried across a change of face it would report the previous person's
     * anatomy as this one's. Cleared whole, so a rail count is always
     * attributable to one session.
     */
    rails: null,
  },

  /**
   * Cleared by `remeasure`, on top of everything above. These describe the
   * SOURCE and the session's relationship to it rather than the face.
   */
  perSource: {
    templesAimed: false,
    lostSeconds: 0,
  },

  /**
   * Owned and cleared by `main.js` (the browser half), named here so this
   * manifest is a complete map of `state` rather than of half of it. The
   * value is the reset class that owns the field.
   */
  ownedByApp: {
    detectRotation: 'source switch',
    rollSearch: 'source switch',
    facelessResults: 'source switch',
    frameAtMs: 'source switch',
    lastFrameMs: 'source switch',
    lastSubmitMs: 'source switch',
    lastResultMs: 'source switch',
    lastAppliedDetectionMs: 'source switch',
    cameraIntervalMs: 'source switch',
    droppedFrames: 'source switch',
    upstreamMs: 'source switch',
    upstreamMeasured: 'source switch',
    cameraSettings: 'source switch',
    sourceEpoch: 'source switch',
    detected: 'source switch',
    switching: 'source switch',
    mirrorDelayMs: 'source switch',
    latencyMeasured: 'source switch',
    background: 'source switch',
    source: 'source switch',
    model: 'model swap',
    modelRoot: 'model swap',
    modelValue: 'model swap',
    temples: 'model swap',
    fit: 'model swap (the sliders; `resetFit` in main.js is the UI button)',
    smoother: 'source switch (whole) / identity (noise only)',
    stabMeter: 'source switch + identity',
    occluder: 'identity (its person-derived half — see resetOccluderPerson)',
    occlusionMask: 'never — a renderer resource',
    recovery: 'never — a lifetime dropout instrument',
    fps: 'never — a display readout',
    trackFps: 'never — a display readout',
    face: 'never — the canonical mesh',
    scene: 'never — the renderer',
    tracker: 'never — the landmarker',
    faceDebug: 'never — a debug overlay',
    anchorDebug: 'never — a debug overlay',
    frameCanvas: 'never — a display surface',
    frameCtx: 'never — a display surface',
    captureCanvas: 'never — a display surface',
    captureCtx: 'never — a display surface',
    detectCanvas: 'never — a display surface',
    detectCtx: 'never — a display surface',
    noise: 'never — a getter over the smoother',
    stab: 'never — a getter over the stab meter',
    rebuilds: 'never — a getter over the occluder',
    person: 'identity (in place — see `person.reset`)',
    identity: 'never — a page-lifetime attribution instrument (created here, in'
      + ' `frame.js`, but owned by nobody\'s reset; see `allowedToSurvive`)',
    poseTrust: 'never — a statement about the pose (see allowedToSurvive)',
    imageScale: 'never — a statement about the camera (see allowedToSurvive)',
    measuringLatch: 'never — derived from poseTrust on the same frame',
    sessionEpoch: 'never — it COUNTS the resets',
  },

  /**
   * Fields that cross the boundary on purpose. The reason is the entry: a
   * survivor without one is a leak that has been renamed.
   *
   * This is an ANNOTATION, not a fourth bucket — every key here also appears
   * in `ownedByApp`, and `pipeline-check` asserts that containment, so a field
   * cannot be excused from the boundary without also being given an owner.
   */
  allowedToSurvive: {
    person: 'the object survives; `person.reset()` empties it in place, and its'
      + ' two event counters (`resets`, `decays`) must outlive what they count',
    identity: 'a page-lifetime attribution instrument. Its four event counters'
      + ' (asked, strikeEvents, acquittals, convictions) are incremented on the'
      + ' same frame `resetFit` runs, so clearing them would hide the conviction'
      + ' from the replay that event-counts it. Every VALUE field (driver,'
      + ' obs/car/dev) is rewritten on every frame the question runs, so it'
      + ' carries nothing; difference the counters across `sessionEpoch`',
    sessionEpoch: 'counts the resets, so it cannot be reset by one',
    occluder: 'one occluder per page, reused across every face — the mesh,'
      + ' subdivision, materials and rebuild counters are asset and instrument;'
      + ' its person-derived half goes through `resetOccluderPerson`',
    smoother: 'the pose LEVEL is a statement about a frame, not a person, and'
      + ' the composite is frame-locked; only `resetNoise()` runs on a swap',
    face: 'the canonical mesh',
    scene: 'the renderer',
    tracker: 'the landmarker',
    fit: 'the asset sliders — a change of wearer is not a change of taste',
    poseTrust: 'the three trust ramps and the true pose eulers for THIS frame —'
      + ' a statement about the pose, which takes the same exception the pose'
      + ' level does: the composite is frame-locked, nobody moved when the'
      + ' estimator changed its mind about whose face it is, and the one consumer'
      + ' that reads it stale (the smoother activity lever, one frame back) is'
      + ' reading the previous FRAME, whose pose is this frame\'s pose. Rewritten'
      + ' unconditionally every frame before anything else touches it',
    measuringLatch: 'derived from `poseTrust` on the same frame, and a pure'
      + ' readout since graft G12 — no behaviour branches on it',
    imageScale: 'pixels per face-space centimetre — a statement about the'
      + ' CAMERA and how far away somebody is sitting, not about whose face it'
      + ' is. Two wearers at the same desk share it and one wearer who leans in'
      + ' changes it, which is the opposite of a person-derived field.'
      + ' Recomputed from this frame\'s pose and this frame\'s source every'
      + ' frame, before anything reads it, with no memory of any kind',
  },
};

/**
 * Empties everything that is a statement about the person in the chair.
 *
 * The single entry point for the identity-change reset class: `resetFit`
 * iterates `PER_SESSION_STATE.perPerson` and then calls the one exported reset
 * of each collaborator that owns state of its own. Adding a field to the
 * manifest is therefore the whole of adding it to the reset, and the
 * isolation check fails on a `state` field that is in neither the manifest nor
 * the allowlist.
 *
 * `smoother` is passed rather than read off `state` so the harness — which
 * hands `updateFrame` a smoother as an argument and keeps none on its state
 * object — resets the same things the app does. The app keeps `state.smoother`
 * and gets it from the default.
 */
function resetFit(state, smoother = state.smoother ?? null) {
  for (const [key, zero] of Object.entries(PER_SESSION_STATE.perPerson)) {
    state[key] = zero;
  }
  // The person model is the slowest state there is, and it is a statement
  // about the PREVIOUS person: carried across an identity change it would
  // spend its whole adaptation tau (20–60 s) morphing one face into another —
  // cross-person bleed, the exact failure the harness's swap check bounds at
  // half a millimetre. Reset in place: the arrays clear, the object survives,
  // and the next frame accumulates a fresh person from sample one.
  state.person?.reset();
  // The occluder's person-derived half — the deformation, the warm start, the
  // depth fit, the relief latch. The occluder itself is one object for the
  // page's life, shared across every face that sits in front of it, so
  // without this the next wearer's first sample is EASED into the previous
  // wearer's shape instead of adopted whole.
  resetOccluderPerson(state.occluder);
  // The two noise-DC calibrations, and not the pose level. See `resetNoise`.
  smoother?.resetNoise?.();
  // A stillness window spanning two different faces reads the swap as one
  // enormous step — and this is the meter read aloud in the live protocol.
  state.stabMeter?.reset?.();
  // Counts the resets, so every lifetime counter above can be differenced
  // across a boundary and attributed to the right person.
  state.sessionEpoch = (state.sessionEpoch ?? 0) + 1;
  // The identity readout's streak field mirrors `identityStrikes`, which the
  // manifest just zeroed; the object itself survives (see `allowedToSurvive`).
  if (state.identity) state.identity.strikes = 0;
}

/**
 * Throws away the measured fit so the next head-on frames measure it afresh.
 *
 * Now only ever called deliberately — the *Re-measure face* button, switching
 * *Adapt to face*, and a new source. Losing the face does not call it, and that
 * is the point: see `IDENTITY_TOLERANCE`.
 */
export function remeasure(state, smoother = state.smoother ?? null) {
  resetFit(state, smoother);
  for (const [key, zero] of Object.entries(PER_SESSION_STATE.perSource)) {
    state[key] = zero;
  }
  // The depth fit's EMA is a statement about the previous face in front of the
  // previous source — its offset rides that head's geometry, and the anchors
  // consume it one frame stale from this very handle. Cleared whole: the next
  // face's first fit is adopted whole (see `conditionDepthFit`), exactly like
  // the pin's first sample. `resetFit` clears it too, through
  // `resetOccluderPerson`; stated here as well because the argument written at
  // this call site is the argument that belongs at both, and the asymmetry
  // between the two paths was one of the confirmed leaks.
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
