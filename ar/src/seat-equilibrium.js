/**
 * The seat equilibrium — where on the nasal wedge this frame actually rests.
 *
 * The seat used to be a one-dimensional afterthought: the optics chose the
 * height, and a single z-push moved the frame out until its deepest contact
 * sat half a millimetre into the skin. That produces a frame at the right
 * height with a plausible standoff, and it is not what glasses do. Real
 * glasses slide DOWN the nasal wedge until the pads bear on BOTH sidewalls;
 * the resting height is set by where the wedge's width matches the frame's
 * pad separation, and height, standoff and (on an asymmetric nose) roll are
 * one coupled equilibrium. On the canonical wedge the trade is steep — the
 * half-width grows ~0.54 mm per mm of descent, so a frame whose pads are 2 mm
 * too far apart rests ~3.7 mm lower — and the old solve could not express any
 * of it: it seated the DBL-mismatched frame at the optical height with the
 * pads either floating or buried, which is the user's complaint ("the
 * interaction of the glasses with the nose is not good at all") in one
 * sentence.
 *
 * So this module inverts the causality (design physics-first B.4, mounted in
 * stability-first's plumbing): sweep candidate heights along the bridge
 * direction, and at each height ask the contact field — the same soft-max
 * side-split kernel the per-frame guard uses, `sideInterference` — whether
 * the frame's PADS are the geometry carrying it. The mechanics:
 *
 *  - **The standoff is closed-form per height.** Pushing along +Z changes
 *    every contact's interference by exactly −ζ (the field is queried in x/y
 *    only), so ζ(s) = max(s) − PAD_SINK needs no inner solve — two contact
 *    passes per candidate height (full set for the level, pad subsets for
 *    the bearing), nine heights, ≈0.3 ms, and only ever on solve EVENTS
 *    (≤ 2 Hz), never per frame.
 *
 *  - **Bearing is a deficit between two soft reductions.** A frame hung too
 *    high on the wedge does not stop touching — its near-centreline bridge
 *    geometry rides the nose's ridge at any height — it stops touching WITH
 *    ITS PADS. So the discriminating quantity is the bearing deficit
 *    `soft(everything) − min(soft(padL), soft(padR))`: how far the lighter
 *    pad's load trails the whole contact set's. Pads carrying the frame ⇒
 *    deficit within EPS_BEAR; frame hanging on its bridge edge or saddle ⇒
 *    the pads trail by millimetres. Both ends of the comparison are soft
 *    (log-sum-exp at SOFTMAX_TAU) so a contact flip slides the deficit
 *    instead of stepping it, and both share the same reduction so the
 *    comparison is anchored — measured on the catalogue: deficit 0.10–0.39 mm
 *    seated, 0.9–1.5 mm hung 4 mm high.
 *
 *  - **Descent only, from the optical height** (spec B.5(a): "if two-sided
 *    bearing already holds at s = 0 the solve returns s* = 0"). The optical
 *    height is where the optics — and the optician's adjustable-pads
 *    narrative behind `maxPupilCorrection` — WANT the frame; the equilibrium
 *    exists to detect the one physical failure a fixed height cannot
 *    express, the frame sliding down a wedge too narrow for its pads. A
 *    frame that COULD also bear higher is not moved up: gravity does not
 *    slide frames up noses, and the stage-5 probe showed the canonical face
 *    offers two-sided bearing well into the orbital region, so "the highest
 *    height that bears" degenerates to the search box's own ceiling. The +s
 *    grid rows are still evaluated — they feed the monotonicity assert and
 *    the saddle diagnosis — they are simply not eligible answers.
 *
 * The degeneracy ladder (B.7) is most of the safety story: flat/wide noses
 * fall back to s = 0 with the 1-DOF push — today's behaviour, never worse;
 * saddle-bridge frames seat on their saddle at the optical height (G4);
 * NaN-starved pad sets and non-monotone deficits freeze the applied
 * constants (G3, mode 'hold'); cold sessions are handled upstream by G1's
 * confidence scaling (the solve runs, its height lands scaled by ~0);
 * asymmetric noses get the honest pad gap reported and, behind
 * `fit.padBalance`, the G5 roll that closes it.
 */

import * as THREE from 'three';
import { sideInterference, widthAt, PAD_SINK } from './nose.js';

/**
 * The candidate heights, in vertical cm relative to the optical target,
 * highest first. The box is the pupil prior (B.6): descent bounded at 12 mm —
 * the wedge trade (~1.9 mm height per mm of DBL mismatch) times a plausible
 * ±4 mm mismatch, plus margin. The two ascent rows are diagnostic only (see
 * the header): candidate ANSWERS are s ≤ 0.
 */
export const S_GRID = [0.4, 0.2, 0, -0.2, -0.4, -0.6, -0.8, -1.0, -1.2];

/** Bisection resolution, cm of height. Via the wedge trade (0.43 mm standoff
 * per mm of height), 0.25 mm of height ≈ 0.1 mm of standoff — the seat's own
 * precision; refining further would be resolving field noise. */
export const S_REFINE = 0.025;

/**
 * How far the lighter pad's soft load may trail the full contact set's and
 * still count as bearing, cm.
 *
 * 0.8 mm — the diagnosis's own two-sided measurable: a pad within 0.8 mm of
 * the load is carrying its share on compliant skin, while a larger gap is a
 * visible float at close range (1.74 px/mm at 45 cm). Sits comfortably
 * between the seated catalogue's measured deficits (0.10–0.39 mm) and the
 * hung-high signature (0.9–1.5 mm), which is exactly the separation a
 * threshold needs.
 */
export const EPS_BEAR = 0.08;

/** G4's saddle margin, cm: the centre band must beat BOTH sides by this
 * across the whole sweep before the frame is declared saddle-borne. */
const SADDLE_MARGIN = 0.03;

/** G3's starvation gate: a pad set losing more than this fraction of its
 * contacts to NaN lookups (off the modelled patch) makes the solve
 * untrustworthy — the far sidewall at hard yaw is held or hallucinated. */
const NAN_SIDE_LIMIT = 0.6;

/** Monotonicity tolerance on the bearing deficit, cm — one field cell of
 * interpolation slack. The deficit must not IMPROVE with ascent: a wedge
 * that bears better higher up is not a wedge, it is a field artefact. */
const MONO_TOL = 0.02;

/** The G5 roll clamp, radians (±3°). Real nose asymmetries produce
 * sub-degree to ~2° equilibrium rolls over a ~17 mm pad lever; past 3° the
 * input is bad data, not anatomy. */
export const ROLL_CLAMP = (3 * Math.PI) / 180;

/**
 * The frame's own left-right handedness, and why it is subtracted.
 *
 * The seat reads a wearer's nasal asymmetry as `Î_L − Î_R`, the difference
 * between the two pads' soft loads. That difference has two authors, and only
 * one of them is the wearer:
 *
 *   D(F, S) = a(F) + b(S)      a = the FRAME's own asymmetry, b = the FACE's
 *
 * Mirroring the frame's contacts about the model's own centreline — the same
 * axis the L/R split is taken about, so the decomposition is self-consistent —
 * and swapping which set is called left flips `a` and leaves `b` alone, because
 * the surface has not moved. So the two readings separate exactly:
 *
 *   a(F) = [ D(F, S) − D(mF, S) ] / 2        the asset's
 *   b(S) = [ D(F, S) + D(mF, S) ] / 2        the wearer's
 *
 * Both are evaluated at the SAME height on the SAME surface, so there is no
 * reference configuration to get wrong and nothing is assumed about the face.
 *
 * This matters because the numbers are not small next to the thing being
 * measured. Across the eleven catalogue assets `a(F)` on the canonical face
 * spans −0.62 mm (khronos) to +0.07 mm, against a wearer signal of ~0.44 mm of
 * pad-load difference per millimetre of nasal deviation: two of the shipped
 * frames carry more handedness than a millimetre-deviated nose does. Left in,
 * it is a roll every wearer gets from the frame they picked, and — measured in
 * the generality sweep — the reason a nose deviated one way dropped out of
 * two-sided bearing half a millimetre before the same nose deviated the other.
 *
 * The record it corrects: the spec attributed that handedness to the canonical
 * MESH ("≈−1.2° of solved baseline roll on it"). The mesh is innocent, and
 * exactly so — all 468 vertices of `canonical_face_model.obj` have an exact
 * mirror partner (worst residual 0.0 cm, 28 vertices on the axis), and the
 * depth field it rasterises to reads `b(S) = 0.0000 mm` for every asset. The
 * handedness was always the frame's.
 */
const mirrorCache = new WeakMap();

/**
 * The mirrored contact set for a contact array, cached on the ARRAY's identity.
 *
 * Asset-derived and nothing else: two wearers in the same frame get the same
 * mirror, so this is not person state and the isolation boundary does not
 * reach it. Keyed on the array rather than the model so a harness that hands
 * the solver a modified contact set (the settle sweep bends the pads outward)
 * gets that set's own mirror instead of a stale one.
 */
function mirrorContacts(contacts, centreX) {
  const hit = mirrorCache.get(contacts);
  if (hit) return hit;
  const made = contacts.map((p) => new THREE.Vector3(2 * centreX - p.x, p.y, p.z));
  mirrorCache.set(contacts, made);
  return made;
}

const scratchScale = new THREE.Vector3();

/**
 * Solves the resting configuration of a placed frame on the current field.
 *
 * `base` is `placement.seatBase` — the optical-height placement (s = 0), with
 * whatever roll is currently applied baked in; `phiStar` reported back is
 * ABSOLUTE (current roll + solved correction), so the fixed point of
 * repeated solves is the balanced configuration, not a runaway.
 *
 * Returns `{ mode, sStar, zetaStar, phiStar, table, zetaAt, bearing,
 * perSide, monotone, widthAtRestCm }`. `zetaAt(s)` interpolates the sweep's
 * closed-form standoff so the caller can keep its applied standoff
 * consistent with its confidence-scaled height between solves.
 */
export function solveRestConfiguration({
  surface, model, anchors, base, padBalance = false,
}) {
  const result = {
    mode: 'wedge',
    sStar: 0,
    zetaStar: 0,
    phiStar: base?.phi ?? 0,
    monotone: true,
    table: null,
    zetaAt: () => 0,
    bearing: { deficit: null, gap: null },
    perSide: null,
    widthAtRestCm: null,
    /** The wearer's own nasal asymmetry LEFT STANDING at the reported
     * configuration — pad-load difference in cm, with the frame's own
     * handedness removed (see `mirrorContacts`) and whatever roll was applied
     * already taken out of it. Reads ~0 on a balanced seat and carries the
     * whole uncorrected asymmetry on an 'unbalanced' one, which is what makes
     * the give-up legible. Null when the balance is dark or there are no pads
     * to read it from. */
    asym: null,
    /** The frame's own handedness at this configuration, cm — the quantity
     * subtracted. A property of the asset; reported so a session can say
     * which frame is doing the leaning. */
    handedness: null,
    /** Was a bearing height reachable only WITH the balancing roll? True says
     * this wearer needs the roll, which is the honest form of "asymmetric". */
    rollBore: false,
  };

  if (!surface || !model?.noseContacts?.length || !base) {
    result.mode = 'flat';
    return result;
  }

  const contacts = model.noseContacts;
  const sides = model.noseSides ?? null;
  const pads = sides?.padL?.length && sides?.padR?.length
    ? { L: sides.padL, R: sides.padR, C: [] } : null;
  const toFace = composeBase(base);
  const up = base.up;
  const upScale = 1 / Math.max(up.y, 0.2);

  // --- the sweep: two soft-max passes per candidate height (full set for
  // the level and the saddle question, pad subsets for the bearing) ---
  const offset = { x: 0, y: 0, z: 0 };
  const evaluateAt = (s, rollZ = 0, dy = 0) => {
    offset.x = up.x * s * upScale;
    offset.y = up.y * s * upScale + dy;
    offset.z = up.z * s * upScale;
    const shared = {
      surface, contacts, toFace, anchors, offset,
      rollZ, pivotX: base.target?.x ?? 0, pivotY: base.target?.y ?? 0,
    };
    const full = sideInterference({ ...shared, sides });
    const pad = pads ? sideInterference({ ...shared, sides: pads }) : null;
    const padL = pad ? pad.perSide.L : { soft: -Infinity, count: 0, dropped: 0 };
    const padR = pad ? pad.perSide.R : { soft: -Infinity, count: 0, dropped: 0 };
    return {
      s,
      zeta: full.touched > 0 ? full.max - PAD_SINK : 0,
      soft: full.soft,
      IL: full.perSide.L.soft,
      IR: full.perSide.R.soft,
      IC: full.perSide.C.soft,
      padL: padL.soft,
      padR: padR.soft,
      /** The bearing deficit — the header's discriminant. Infinity when a
       * pad set has no valid contact, which correctly never bears. */
      deficit: Number.isFinite(padL.soft) && Number.isFinite(padR.soft)
        && full.touched > 0
        ? full.soft - Math.min(padL.soft, padR.soft) : Infinity,
      nanL: padL.count + padL.dropped > 0
        ? padL.dropped / (padL.count + padL.dropped) : 1,
      nanR: padR.count + padR.dropped > 0
        ? padR.dropped / (padR.count + padR.dropped) : 1,
      touched: full.touched,
    };
  };

  /**
   * The pads' loads with the frame MIRRORED about its own centreline and the
   * two sets' names swapped — the second half of the handedness split. One
   * extra pad-set pass, on solve events only.
   */
  const evaluateMirrored = (s, rollZ = 0) => {
    if (!pads) return null;
    offset.x = up.x * s * upScale;
    offset.y = up.y * s * upScale;
    offset.z = up.z * s * upScale;
    return sideInterference({
      surface,
      contacts: mirrorContacts(contacts, model.centre?.x ?? 0),
      sides: { L: pads.R, R: pads.L, C: [] },
      toFace,
      anchors,
      offset,
      rollZ,
      pivotX: base.target?.x ?? 0,
      pivotY: base.target?.y ?? 0,
    });
  };

  const rows = S_GRID.map((s) => evaluateAt(s));
  result.table = rows;
  result.zetaAt = (s) => interpolateZeta(rows, s);

  // --- the degeneracy ladder, in the order the failures are diagnosable ---

  // No usable pads at load time: the model's bridge is a bar, not a pad
  // pair, and two-sided bearing is not a question its geometry can answer.
  // The 1-DOF push at the optical height is today's behaviour exactly.
  if (!model.hasPads || !pads) {
    result.mode = 'flat';
    adoptFallback(result, rows);
    return result;
  }

  // G3 — a starved pad. The optical-height row is the representative one (it
  // is where the frame actually is); a pad losing most of its lookups to NaN
  // there means the sidewall has left the modelled patch (hard yaw, held or
  // hallucinated far side). The caller freezes its applied constants; the
  // per-frame guard still protects.
  const atZero = rows.find((r) => r.s === 0) ?? rows[Math.floor(rows.length / 2)];
  if (atZero.nanL > NAN_SIDE_LIMIT || atZero.nanR > NAN_SIDE_LIMIT) {
    result.mode = 'hold';
    return result;
  }

  // G4 — the saddle. If the centre band out-interferes BOTH side sets by the
  // margin across the whole sweep, the frame genuinely bears on its saddle:
  // the wedge never engages the pads, descending buys nothing, and the pad
  // deficit would only ever report the asset's own shape. Height stays
  // optical, the standoff is the same max-law the guard applies, and the
  // mode is reported so a saddle asset is never mistaken for a solved wedge.
  const saddle = rows.every((r) => Number.isFinite(r.IC)
    && r.IC > Math.max(r.IL, r.IR) + SADDLE_MARGIN);
  if (saddle) {
    result.mode = 'saddle';
    adoptFallback(result, rows);
    return result;
  }

  // --- what the wearer's asymmetry actually is, and what a roll does to it ---
  //
  // Two measurements, both on THIS wearer's surface, both taken before the
  // search rather than after it — because on an asymmetric nose the height and
  // the roll are not two answers, they are one answer with two coordinates,
  // and a search that fixes the roll at zero is searching the wrong set. The
  // module header has said "height, standoff and (on an asymmetric nose) roll
  // are one coupled equilibrium" since it was written; the code solved them in
  // series, so the channel built for an asymmetric nose only ever ran on a nose
  // that had already borne without it.
  //
  // Why descending cannot substitute: a lateral deviation moves BOTH sidewalls
  // the same way, so every millimetre of descent adds load to both pads
  // together and leaves the difference between them untouched. Measured on the
  // deviation sweep, the pad deficit at the best height is flat to 0.01 mm
  // across the whole search box while the pad GAP is the entire story. Rolling
  // is the only degree of freedom that closes it.
  //
  // (1) THE HANDEDNESS, subtracted so the roll answers the nose and not the
  //     frame — see `mirrorContacts` for the algebra, and for the correction it
  //     makes to this file's own record.
  const mirroredAtZero = padBalance ? evaluateMirrored(0) : null;
  const handedness = mirroredAtZero
    && Number.isFinite(atZero.padL) && Number.isFinite(atZero.padR)
    && Number.isFinite(mirroredAtZero.perSide.L.soft)
    && Number.isFinite(mirroredAtZero.perSide.R.soft)
    ? ((atZero.padL - atZero.padR)
      - (mirroredAtZero.perSide.L.soft - mirroredAtZero.perSide.R.soft)) / 2
    : null;
  result.handedness = handedness;
  /** The wearer's own asymmetry at a row: the pad-load difference, less the
   * frame's. Positive means the LEFT pad is carrying more. */
  const asymOf = (row) => (row.padL - row.padR) - handedness;

  // (2) THE LEVER — dΔ/dφ, measured as a secant across the roll's own legal
  //     range instead of modelled from a pad lever and a vertical gradient.
  //
  //     SECOND FIELD AMENDMENT, and the same lesson as the first (which
  //     replaced an along-bridge gradient with a pure-vertical probe): the
  //     modelled lever `2·x̄_pad·κ` is not the derivative of the thing the solve
  //     actually does. A roll about the hang point does not only raise one pad
  //     and lower the other, it also slides BOTH sideways by −y_rel·φ, and on a
  //     V-shaped wedge a common lateral slide is itself an asymmetry — the near
  //     wall comes to one pad and away from the other. Carrying that term,
  //
  //         dΔ/dφ = −2·( x̄·κ_y − y_rel·w' )
  //
  //     where w' is the sidewall's lateral falloff, and the model kept only the
  //     first half. Measured, the omission is worth 1.6–1.9× of gain: the
  //     modelled law overshot balance by that factor on every deviated nose in
  //     the sweep, leaving a residual of the same size and the opposite sign,
  //     and the resulting solve-to-solve iteration converged at |−0.65| per
  //     solve — a frame that visibly rocks its way to level.
  //
  //     A secant needs neither term. Two evaluations at ±ROLL_CLAMP give the
  //     mean derivative across exactly the interval the answer is allowed to
  //     land in, the handedness cancels in the difference, and the answer is a
  //     Newton step that lands in one solve. It also DELETES two constants: the
  //     `κ > 0.05` gradient gate and the `x̄ > 0.1 cm` lever gate, both
  //     arbitrary, replaced by one derived admission — a full-range roll must
  //     move the asymmetry by more than the bearing tolerance it is trying to
  //     close, or the roll is not a usable degree of freedom on this face and
  //     applying it would be dressing up field noise as an angle.
  const leverAt = (s) => {
    const plus = evaluateAt(s, ROLL_CLAMP);
    const minus = evaluateAt(s, -ROLL_CLAMP);
    if (![plus.padL, plus.padR, minus.padL, minus.padR].every(Number.isFinite)) return 0;
    const span = (plus.padL - plus.padR) - (minus.padL - minus.padR);
    return Math.abs(span) > EPS_BEAR ? span / (2 * ROLL_CLAMP) : 0;
  };
  /** The Newton step that nulls a row's asymmetry, radians, clamped. */
  const balanceFor = (row, lever) => (
    lever < 0 && handedness !== null
      && Number.isFinite(row.padL) && Number.isFinite(row.padR)
      ? Math.min(Math.max(-asymOf(row) / lever, -ROLL_CLAMP), ROLL_CLAMP)
      : 0
  );

  // The equilibrium. Candidate answers are s ≤ 0 (see the header); the
  // FIRST bearing row from the optical height downward is the highest
  // resting height the wedge supports.
  const unrolledCandidates = rows.filter((r) => r.s <= 0);
  let candidates = unrolledCandidates;
  let lever = 0;
  let found = -1;
  for (let i = 0; i < candidates.length; i++) {
    if (candidates[i].deficit <= EPS_BEAR) { found = i; break; }
  }

  // Monotonicity (B.4 step 4, restated for the deficit): bearing must not
  // improve with ascent ALONG THE SEARCH PATH — from the top of the grid
  // down to the chosen rest, the deficit may only fall or hold within
  // tolerance. A pocket that bears better above a height that does not is
  // a field artefact, and a solve on it would be a fiction (G3 → hold).
  // Deliberately NOT asserted below the equilibrium: on a real nose the
  // deficit has a genuine minimum — descend far enough and the pads slide
  // past the wedge onto the nostril flare, where bearing honestly worsens —
  // and the first real face this ran against (face-a) showed exactly that
  // valley. Structure below the answer is the wedge ending, not the field
  // lying; the search never reaches it.
  //
  // Read off the UNROLLED rows, because it is a question about the SURFACE:
  // whether the wedge widens the way a wedge does. The roll is the frame's
  // answer to the surface and cannot also be part of the evidence about it.
  const floor = found === -1 ? rows.length : rows.indexOf(unrolledCandidates[found]);
  for (let i = 1; i <= floor && i < rows.length; i++) {
    if (rows[i].deficit > rows[i - 1].deficit + MONO_TOL
      && rows[i - 1].deficit <= EPS_BEAR && rows[i].deficit > EPS_BEAR) {
      result.mode = 'hold';
      result.monotone = false;
      return result;
    }
  }

  // THE BALANCED SWEEP, and it runs ONLY when the plain one gave up.
  //
  // A lateral deviation is the one case a height search cannot answer: the
  // wedge's two walls move together, so every millimetre of descent adds load
  // to both pads and leaves the difference between them untouched — measured,
  // the deficit at the best height is flat to 0.01 mm across the whole box
  // while the pad GAP is the entire story. So the search is re-run over
  // BALANCED configurations, each height evaluated at the roll that levels the
  // pads there. That is the module header's own "height, standoff and roll are
  // one coupled equilibrium", finally solved as one.
  //
  // The conditional is not an optimisation, it is the ratchet: every face that
  // bears today bears at exactly the same height by exactly the same
  // arithmetic, whatever the flag says, and the extra passes are paid only by
  // the faces that were getting the pre-stage-5 seat for free. Running it
  // unconditionally was tried and measured — on a face that already bears, the
  // roll the landmark noise asks for is a fraction of a degree, but it
  // perturbs every row of the height sweep, and the solved heights then scatter
  // enough to cost the settle law a cell (S09 at ×4 noise: 5.4 s against its
  // own 4.52 s bound). A degree of freedom that is not needed must not be
  // exercised.
  if (found === -1 && padBalance) {
    lever = leverAt(0);
    candidates = unrolledCandidates.map((row) => {
      const phi = balanceFor(row, lever);
      const out = phi === 0 ? row : evaluateAt(row.s, phi);
      out.phi = phi;
      return out;
    });
    for (let i = 0; i < candidates.length; i++) {
      if (candidates[i].deficit <= EPS_BEAR) { found = i; break; }
    }
  }
  const coupled = candidates !== unrolledCandidates;

  // B.7.1 — nowhere in the box do the pads take the load, at any roll the frame
  // is allowed. Today's 1-DOF behaviour is the floor, never a cliff — but it is
  // REPORTED rather than silent, and it reports the numbers that explain it.
  // Three physically different give-ups used to share one word: an asset with
  // no pad pair, a nose too flat or wide for these pads, and a nose whose
  // asymmetry the frame cannot follow. The first keeps 'flat' — the geometry
  // cannot answer the question at all. The other two are 'unbalanced', and the
  // best row's own deficit, gap and residual asymmetry ride out with the
  // verdict instead of the NaN that made an asymmetric wearer invisible: a
  // deficit that is mostly `asym` is the second case, one that is not is the
  // first.
  if (found === -1) {
    result.mode = 'unbalanced';
    adoptFallback(result, rows);
    const best = candidates.reduce((a, b) => (b.deficit < a.deficit ? b : a));
    result.perSide = { IL: best.padL, IR: best.padR };
    result.bearing = {
      deficit: Number.isFinite(best.deficit) ? best.deficit : null,
      gap: Number.isFinite(best.padL) && Number.isFinite(best.padR)
        ? Math.abs(best.padL - best.padR) : null,
    };
    result.asym = handedness !== null && Number.isFinite(best.padL)
      && Number.isFinite(best.padR) ? asymOf(best) : null;
    return result;
  }

  // Bisection refine between the failing row above and the bearing row —
  // 2–3 extra contact passes for 0.25 mm of height resolution. When the
  // optical height itself bears (found === 0 with s = 0), there is nothing
  // to refine: the answer IS the optical height, by B.5(a).
  let atStar = candidates[found];
  let sStar = atStar.s;
  if (found > 0 || (candidates[0].deficit > EPS_BEAR && atStar.s < 0)) {
    const failIndex = rows.indexOf(unrolledCandidates[found]) - 1;
    if (failIndex >= 0) {
      let low = atStar.s; // bears
      let high = Math.min(rows[failIndex].s, 0); // does not (or the ceiling)
      while (high - low > S_REFINE) {
        const mid = (low + high) / 2;
        let row = evaluateAt(mid);
        row.phi = 0;
        if (coupled) {
          const phi = balanceFor(row, lever);
          if (phi !== 0) { row = evaluateAt(mid, phi); row.phi = phi; }
        }
        if (row.deficit <= EPS_BEAR) { low = mid; atStar = row; } else { high = mid; }
      }
      sStar = low;
    }
  }

  result.sStar = sStar;
  result.zetaStar = atStar.zeta;
  result.perSide = { IL: atStar.padL, IR: atStar.padR };
  result.bearing = {
    deficit: atStar.deficit,
    gap: Number.isFinite(atStar.padL) && Number.isFinite(atStar.padR)
      ? Math.abs(atStar.padL - atStar.padR) : null,
  };
  if (handedness !== null && Number.isFinite(atStar.padL) && Number.isFinite(atStar.padR)) {
    result.asym = asymOf(atStar);
  }

  // G5 proper — the balancing roll at the answer, which is where it belongs on
  // a face that found its height without help. `atStar` already carries
  // whatever roll the coupled search needed, so the increment closes what is
  // left on top of it and the two stages compose rather than overwrite.
  //
  // The solved roll is ABSOLUTE — the base's own plus this solve's increment —
  // so repeated solves converge on the balanced configuration instead of
  // accumulating, and the secant returns the same absolute answer from any
  // base, which is what lets the caller shrink it toward zero without the next
  // solve asking for more. `rollBore` says the roll was LOAD-BEARING: at zero
  // roll this height did not carry the pads and with the roll it does — the
  // machine-readable form of "this wearer's nose is asymmetric enough that the
  // frame has to follow it".
  if (padBalance) {
    const carried = atStar.phi ?? 0;
    const delta = balanceFor(atStar, coupled ? lever : leverAt(sStar));
    const phiRel = carried + delta;
    if (phiRel !== 0) {
      const phi = Math.min(Math.max((base.phi ?? 0) + phiRel, -ROLL_CLAMP), ROLL_CLAMP);
      result.phiStar = phi;
      // One re-evaluation at (s*, φ*) so the reported standoff and bearing are
      // the balanced configuration's, not the pre-roll one (B.4 step 5). The
      // roll is applied about the face-space Z axis through the hang point —
      // exactly how `solvePlacement`'s orientation stage will apply it.
      const rolled = evaluateAt(sStar, phi - (base.phi ?? 0));
      result.zetaStar = rolled.zeta;
      result.perSide = { IL: rolled.padL, IR: rolled.padR };
      result.bearing = {
        deficit: rolled.deficit,
        gap: Number.isFinite(rolled.padL) && Number.isFinite(rolled.padR)
          ? Math.abs(rolled.padL - rolled.padR) : null,
      };
      if (Number.isFinite(rolled.padL) && Number.isFinite(rolled.padR)) {
        result.asym = asymOf(rolled);
      }
    }
    if (carried) {
      const unrolled = rows.find((r) => r.s === atStar.s);
      result.rollBore = !(unrolled && unrolled.deficit <= EPS_BEAR);
    }
  }

  // A diagnostic the harness's resting-height law reads live: the wedge's
  // width at the resting pads' own height and depth. Field coordinates come
  // from the same bridge-relative shift the kernel uses, so the width is the
  // width of the surface the solve actually consulted.
  result.widthAtRestCm = restWidth({ surface, model, anchors, base, sStar, upScale });

  return result;
}

/** The fallback adoption shared by 'flat' and 'saddle': optical height, the
 * 1-DOF max-law push, and a constant zetaAt — today's behaviour as the
 * floor. */
function adoptFallback(result, rows) {
  const atZero = rows.find((r) => r.s === 0) ?? rows[Math.floor(rows.length / 2)];
  result.sStar = 0;
  result.zetaStar = atZero.zeta;
  result.perSide = Number.isFinite(atZero.padL) || Number.isFinite(atZero.padR)
    ? { IL: atZero.padL, IR: atZero.padR } : null;
  result.zetaAt = () => atZero.zeta;
}

/** Linear interpolation of the sweep's closed-form standoff at height s,
 * clamped to the box — the caller's bridge between its eased height and a
 * consistent standoff. */
function interpolateZeta(rows, s) {
  if (s >= rows[0].s) return rows[0].zeta;
  for (let i = 1; i < rows.length; i++) {
    if (s >= rows[i].s) {
      const hi = rows[i - 1];
      const lo = rows[i];
      const t = hi.s === lo.s ? 0 : (hi.s - s) / (hi.s - lo.s);
      return hi.zeta + (lo.zeta - hi.zeta) * t;
    }
  }
  return rows[rows.length - 1].zeta;
}

/** The wedge width at the resting pad row, in cm — diagnostic only. */
function restWidth({ surface, model, anchors, base, sStar, upScale }) {
  const padCentroids = model.noseSides?.padCentroids;
  if (!padCentroids?.left || !padCentroids?.right) return null;

  // The pad centroids carried to the resting placement, then into field
  // coordinates by the kernel's own bridge-relative shift.
  const up = base.up;
  const mid = padCentroids.left.clone().add(padCentroids.right)
    .multiplyScalar(0.5 * base.scale)
    .applyQuaternion(base.quaternion)
    .add(base.position)
    .addScaledVector(up, sStar * upScale);
  const fieldY = surface.origin[1] + (mid.y - anchors.bridge.y);
  const fieldZ = mid.z - (anchors.bridge.z - surface.origin[2]);
  const width = widthAt(surface, fieldY, fieldZ);
  return Number.isFinite(width) ? width : null;
}

/** The base placement as a matrix, cached on the base object — the solver
 * evaluates a dozen rows against one base and the compose is not free. */
function composeBase(base) {
  if (!base.matrix) {
    base.matrix = new THREE.Matrix4().compose(
      base.position, base.quaternion, scratchScale.setScalar(base.scale),
    );
  }
  return base.matrix;
}
