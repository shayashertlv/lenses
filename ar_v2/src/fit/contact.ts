/**
 * Where a pair of glasses actually comes to rest on a face.
 *
 * ## What this replaces
 *
 * v1 hung the frame off landmark 6 and then tried to correct for the
 * consequences. Its own diagnosis is the best statement of the problem:
 *
 *   *"A landmark is a point and a nose is a wedge, and the wedge is steep."*
 *
 * It got most of the way there — it worked out that height, standoff and roll
 * are one coupled equilibrium and that a frame slides down the wedge until both
 * pads bear, and it swept a nine-height search for it. What it could not do was
 * solve against a *real* nose, because it did not have one; the wedge it swept
 * was the average head's, scaled by a measured width. Its own open list says so:
 * *"A tall narrow nose and a low broad one of the same width seat identically."*
 *
 * Here the nose is measured, so the same physics gives a different answer per
 * wearer — and the physics itself is a proper contact solve rather than a
 * one-point-per-side search.
 *
 * ## The model
 *
 * Minimise, over the frame's six degrees of freedom:
 *
 *     E  =  contact  +  gravity  +  ear support  +  clearance  +  a weak prior
 *
 *   **contact**    Each pad sample carries a one-sided linear spring against the
 *                  skin: force appears only once the sample is inside the
 *                  surface, and grows with how far in. Soft-tissue compliance is
 *                  a real, measurable property and it is *not* measured here —
 *                  see `SKIN`, and `docs/OPEN-QUESTIONS.md` Q4.
 *   **gravity**    A linear potential in the frame's centre of mass height. This
 *                  is what makes the frame *slide down the wedge* rather than
 *                  being placed at a chosen height. It is the term v1 was
 *                  reaching for with its "largest-s bearing equilibrium".
 *   **ear**        The temple tips rest on top of the ears, which support from
 *                  below only. One-sided, like contact. Without it, gravity
 *                  rotates the front down without limit and every frame ends up
 *                  with 40 degrees of pantoscopic tilt.
 *   **clearance**  Lens rims and arms must not pass through the brow or cheeks.
 *                  Repulsion only, no attraction.
 *   **prior**      A weak pull toward the nominal placement, which exists only to
 *                  keep the two genuinely unconstrained directions (sliding along
 *                  a locally flat sidewall) from wandering. It is deliberately
 *                  too weak to move a converged answer.
 *
 * The minimiser is Levenberg-Marquardt with a Gauss-Newton Hessian built from
 * the squared terms and the gravity gradient added to the right-hand side —
 * gravity is linear, so it contributes to the gradient and not to the curvature.
 * That is not a hack; it is what Newton's method on this energy is.
 *
 * ## Solved once
 *
 * The answer depends on (face model, frame asset) and nothing else. It is solved
 * at fit time and cached. Nothing in the per-frame path re-solves it, which is
 * the structural reason the frame cannot walk up and down the nose at run time,
 * cannot shimmer, and cannot behave differently at 40 degrees of yaw — there is
 * no per-frame placement left to be wrong.
 */

import {
  type Mat3, type Pose, type Vec3, clamp, ldlt, ldltSolve, logSO3, m3, poseClone,
  poseIdentity, poseOplus, v3,
} from '../core/linalg.js';
import { LM, type FaceMesh, type Region } from '../core/mesh.js';
import {
  type ClosestPoint, type MeshDistance, buildMeshDistance, emptyClosestPoint,
} from '../core/meshdist.js';
import type { FaceModel } from '../core/facemodel.js';
import { type FrameAsset, GRAVITY_N_PER_G } from './frame-asset.js';

/**
 * Soft-tissue properties at the nasal sidewall.
 *
 * **These are stated, not measured, and they are the least-supported numbers in
 * the tree.** The derivation: a 24 g frame weighs 0.235 N; nose pads on a real
 * wearer visibly compress the skin by roughly a millimetre; so the combined
 * stiffness of the two pad contacts is about 0.24 N/mm. That is one observation
 * of one person's nose, which is precisely the class of constant v1's audit
 * found 66 of.
 *
 * What makes it tolerable is that the *shape* of the answer barely depends on
 * it — or so the argument goes: doubling `stiffnessNPerMm` should change the
 * settled height by about 0.5 mm and change which frames fit which faces not at
 * all, because the wedge slope (~0.5 mm of half-width per mm of descent)
 * dominates.
 *
 * Half of that argument is measured and half is not, and they are worth keeping
 * apart:
 *
 *  - The wedge slope IS measured. `testkit/report-seat.ts` sweeps pad
 *    separation and fits `WEDGE_SLOPE_MM_PER_MM` (descent per mm of
 *    separation), which is the same quantity seen from the other side —
 *    1/(2 x 0.92) is the ~0.5 mm of half-width per mm of descent quoted above.
 *  - The stiffness insensitivity is NOT. Nothing in this tree sweeps
 *    stiffness. An earlier version of this header credited a
 *    `tests/contact.test.ts` that has never existed in this repository, and
 *    the report-seat sweep varies a different parameter.
 *
 * So the least-supported constants in the tree are currently also the ones
 * whose unimportance is unverified, which is the wrong way round — and the
 * missing sweep is the cheapest measurement on the whole open-questions list.
 */
export const SKIN = {
  /**
   * Combined stiffness of both pad contacts, newtons per mm of penetration.
   *
   * Derived rather than guessed, and the derivation is worth following because
   * the first version of this constant was out by a factor of four and the
   * symptom was a frame that slid off the face.
   *
   * The nasal sidewall's outward normal, measured on the template at pad
   * height, is (-0.76, +0.24, +0.60): it points outward, **upward**, and
   * forward. The upward component is what carries the frame — a normal force
   * `N` supports `0.24 N` of weight. A 24 g frame weighs 0.235 N, so if the
   * nose carried all of it the pads would need `N = 0.98` newtons between them.
   * Nose pads visibly compress skin by about a millimetre, so the combined
   * stiffness is about 1 N/mm.
   *
   * Still one observation of one nose — but now it is an observation with a
   * derivation attached. The claim that the harness sweeps it across a factor
   * of four is retracted: no such sweep exists (see the file header). See
   * `docs/OPEN-QUESTIONS.md` Q4.
   */
  stiffnessNPerMm: 1.0,
  /**
   * The ear's vertical support stiffness, N/mm.
   *
   * Softer than the nose, not stiffer: the temple rests on the sulcus behind
   * the pinna, which is compliant cartilage and skin over bone, and the contact
   * patch is small. An earlier value of 0.9 N/mm — chosen on the reasoning that
   * the ear "resists more" — made the ear term 22 times the frame's weight at
   * the nominal configuration, so the solve shoved the frame up until the pads
   * left the nose entirely.
   */
  earStiffnessNPerMm: 0.35,
  /**
   * How hard the temple hook resists the frame sliding forward, N/mm.
   *
   * **This term is what keeps glasses on a face, and leaving it out was the
   * single biggest modelling error in the first version of this file.** The
   * sidewall contact normal has a large forward component (+0.60), so the nose
   * pushes the frame *off the face* as hard as it pushes it up. Nothing else in
   * the energy opposed that, so the frame drifted forward, lost contact, and
   * gravity took it 60 mm down the nose with nothing to stop it. In reality the
   * temple bends down behind the ear and simply cannot go forward: that is the
   * hook, and it is one-sided.
   */
  hookStiffnessNPerMm: 0.8,
  /**
   * What the hook is when the temple arm is allowed to BEND: the tip stiffness
   * of the arm as a cantilever, N/mm. `derived`, and this is the arithmetic:
   *
   *     k = 3 E I / L^3                    (tip-loaded cantilever)
   *     E = 2.2 GPa = 2200 N/mm^2          cellulose acetate; published range
   *                                        1.6-3.2 GPa
   *     I = b h^3 / 12
   *       = 2.5 x 4.0^3 / 12 = 13.33 mm^4  rectangular temple section, STATED
   *                                        4.0 mm tall x 2.5 mm thick — the
   *                                        parametric frame carries no temple
   *                                        cross-section to measure. Strong
   *                                        axis, because the forward slide is
   *                                        resisted by the hook riding up the
   *                                        sulcus, which bows the arm in the
   *                                        vertical plane.
   *     L = 93 mm                          hinge (z = -2) to ear rest
   *                                        (z = -95), measured off
   *                                        `parametricFrame`.
   *     k = 3 x 2200 x 13.33 / 93^3 = 88,000 / 804,357 = 0.109 ~ 0.11 N/mm
   *
   * The input spread brackets it: E over its published range gives 0.080-0.159,
   * L over 85-100 mm gives 0.143-0.088. A half/double bracket on 0.11 covers
   * both, which is what the Q15 measurement sweeps.
   *
   * Seven times softer than `hookStiffnessNPerMm` = 0.8 — the wall above is not
   * a slightly stiff arm, it is a different material.
   *
   * **Measured 2026-08-22, and the verdict is KEEP THE WALL.** The adoption
   * rule: ship the compliant arm if, in at least 4 of 5 seeds
   * {11, 23, 37, 41, 53}, it (1) does not worsen median |pad depth error| by
   * more than 0.05 mm, (2) holds median hook force below 1.5 frame-weights,
   * and (3) does not lower the corneal-vertex in-band fraction. One seed of
   * five (53) passed all three. Condition (1) is what failed: compliance
   * worsens median |pad depth error| by 0.09-0.33 mm in the other four seeds.
   * Condition (2) passed 5/5 but vacuously — the wall's own median force is
   * already 1.01x the frame's weight, so the condition could not discriminate
   * (the "2-7x weight" premise that motivated Q15 describes the force tail,
   * not the median; see `SeatResult.hookForceN`). Condition (3) passed 5/5.
   *
   * The comparison: seated against ground truth, 8 subjects x 5 catalogue
   * frames per seed, cell = median over the 5 per-seed medians:
   *
   *     arm    k N/mm   |depth| mm   pad load   hook/wt   vertex mm   in 12-16   descent mm
   *     wall    0.800      0.86        0.86       1.01       13.0        0.54        3.84
   *     k/2     0.055      1.10        0.84       0.86       14.4        0.64        6.72
   *     k       0.110      0.97        0.84       0.92       13.7        0.58        5.28
   *     2k      0.220      0.95        0.85       0.98       13.4        0.54        4.69
   *
   * (vertex is CORNEAL: `SeatResult.vertexDistanceMm` mean of sides minus the
   * 12 mm corneal offset — the definition `advice.ts` grades.)
   *
   * The verdict is robust across the section/modulus bracket — k/2 and 2k also
   * fail the depth condition in >= 3 of 5 seeds — and it is not a finding that
   * compliance is worthless: at k it halves the extreme hook-force tail (max
   * 11.1x -> 5.8x weight; pairs above 3x, 19 -> 7 of 250) and its in-band
   * fraction beats or ties the wall's in 5/5 seeds. What it costs is pad depth
   * (+0.11 mm median, pooled) and descent (+1.4 mm median). A future rule that
   * weighs the force TAIL rather than the median could reach the other branch;
   * this one did not. So this value stays reachable only through
   * `SeatOptions.hookStiffnessNPerMm`, and the wall stays the default.
   */
  hookCantileverNPerMm: 0.11,
  /**
   * Clearance repulsion for rims and arms, N/mm, COMBINED over the whole
   * clearance sample set — `energyTerms` and `accumulate` both divide it by
   * `clearanceSamples(frame).length`, the same convention `stiffnessNPerMm`
   * uses for the pads. Stated per-sample it would change meaning silently the
   * day somebody samples a rim more finely. Deliberately stiff either way: this
   * is not tissue compliance, it is "geometry must not interpenetrate".
   *
   * **The term it scales is currently unreachable, and knowing that is the
   * point of this paragraph.** Over 1,462 solves — 17 synthetic faces against
   * the 5 catalogue frames and against an 81-frame sweep of pad separation
   * (13-22 mm), pad angle (0.20-0.67 rad), mass (20-42 g) and front width
   * (126-150 mm) — not one clearance sample ever penetrated. The closest rim
   * corner sat 3.88 mm clear at worst. It takes `lensAheadOfPadsMm <= -4` to
   * get a single corner to touch and about -8 to engage properly, which is
   * lenses BEHIND the pad contact: inside the wearer's head.
   *
   * It is dead for a second reason that raising `k` would not cure, so do not
   * raise `k` and believe the term now works: `clearanceSamples` models each rim
   * as four corners at exactly the lens centre's depth — a flat rectangle with
   * no wrap, no lens curvature and no descending bottom rim, which is the part
   * of a real frame that actually meets a cheek.
   */
  clearanceStiffnessNPerMm: 6,
} as const;

export interface SeatOptions {
  maxIterations: number;
  /**
   * Start from this pose instead of the nominal placement.
   *
   * With `maxIterations: 0` this makes `solveSeat` a pure *evaluator*: it
   * describes how a given transform sits on a given face without moving it.
   * That is what the harness's controls need — "solve the seat against the
   * wrong nose, then measure how it sits on the right one" is only a control if
   * the second step cannot quietly re-solve.
   */
  initialPose?: Pose;
  /** Weight of the prior that keeps the unconstrained directions from drifting. */
  priorWeight: number;
  /**
   * Weight of the rotational prior, per radian.
   *
   * Small, and it exists for the same reason as the translational one: with
   * only one-sided contacts, a rotation that lifts every contact off the
   * surface costs nothing, so the solve has a flat direction it can wander
   * along. This is not "the frame should be upright" — it is "do not rotate for
   * free".
   */
  rotationPriorWeight: number;
  /** Include the ear support term. Off is how the harness measures what it does. */
  useEars: boolean;
  useClearance: boolean;
  /**
   * Hook stiffness override, N/mm — the Q15 experiment knob.
   *
   * `undefined` is the shipped wall, `SKIN.hookStiffnessNPerMm` = 0.8.
   * `SKIN.hookCantileverNPerMm` = 0.11 is the physically derived bending arm.
   * The term keeps its shape either way — one-sided, quadratic in the forward
   * travel past the ear rest — because a linear spring IS a wall at high k; the
   * two differ only in stiffness, so the option is the stiffness.
   *
   * The Q15 measurement ran (2026-08-22) and said the compliant value should
   * NOT ship: 1 of 5 seeds passed the adoption rule against the >= 4 required,
   * with the pad-depth condition the one that failed (worse by 0.09-0.33 mm in
   * 4 of 5 seeds, against a 0.05 mm allowance). The wall stays the default and
   * this stays the experiment knob — see `SKIN.hookCantileverNPerMm` for the
   * full comparison table.
   */
  hookStiffnessNPerMm?: number;
  trace?: (message: string) => void;
}

export const SEAT_DEFAULTS: SeatOptions = {
  maxIterations: 80,
  priorWeight: 0.004,
  rotationPriorWeight: 0.02,
  useEars: true,
  useClearance: true,
};

export interface PadContact {
  side: -1 | 1;
  /** Fraction of this pad's samples that are bearing. 1.0 is a flush pad. */
  bearingFraction: number;
  /** Mean penetration over the bearing samples, mm. */
  meanPenetrationMm: number;
  /** Peak penetration, mm. A large gap between mean and peak is an edge digging in. */
  peakPenetrationMm: number;
  /** Load carried by this pad, newtons. */
  loadN: number;
}

export interface SeatResult {
  /** Frame-local -> face-space transform. */
  pose: Pose;
  converged: boolean;
  /** Why the solve stopped. Reported rather than reduced to a boolean, because
   *  "settled into a contact corner" and "ran out of iterations" are different
   *  facts and only one of them is a problem. */
  terminationReason: string;
  iterations: number;
  energy: number;
  solveMs: number;

  pads: [PadContact, PadContact];
  /** Total load carried by the pads, as a fraction of the frame's weight. The
   *  remainder is on the ears. */
  padLoadFraction: number;
  /**
   * NET pad vertical force divided by the frame's weight — signed, so a
   * downward-facing contact subtracts.
   *
   * 1.0 is a frame exactly held up by its pads. Above that, something else is
   * pressing them in past static equilibrium — see the note where this is
   * computed for what that something is, and for what it is not.
   */
  padOverClosure: number;
  /** How far the frame settled below the nominal placement, mm. Positive is
   *  lower. This is the number v1's wearer was complaining about. */
  descentMm: number;
  /** Pantoscopic tilt at rest, degrees — front-bottom-toward-the-face. */
  pantoscopicDeg: number;
  /** Roll at rest, degrees. Nonzero means the wearer's nose is asymmetric and
   *  the frame will genuinely sit crooked. */
  rollDeg: number;
  /**
   * Vertex distance: lens plane to the eye, mm, per side — where "the eye" is
   * the CANTHAL PLANE, the midpoint of the eye-corner landmarks, because that
   * is what the mesh has. The optical convention (and the 12-16 mm band the
   * advice grades against) is lens-to-CORNEA, which sits `CORNEAL_APEX_MM` =
   * 12 mm further forward — see `advice.ts`, which subtracts it. Compare this
   * raw number against the band and every frame in the catalogue reads ~13 mm
   * too far from the eye; the Q15 campaign nearly published exactly that.
   */
  vertexDistanceMm: [number, number];
  /** How far the lens optical centres sit from the pupils, mm, per side. */
  opticalCentreOffsetMm: [number, number];
  /** Worst clearance violation anywhere that is not a pad, mm. */
  worstClearanceMm: number;
  /**
   * Total hook force at the settled pose, newtons, summed over both temples,
   * computed with the stiffness the solve used (`SeatOptions.
   * hookStiffnessNPerMm`, defaulting to the wall). This is the force pressing
   * the frame BACK into the nose — the sidewall normal's +0.60 forward
   * component means the nose pushes the frame off the face, and the hook is
   * what pushes back. Q15's premise was that the wall applies "2-7x the
   * frame's weight"; measured on the fixed-RNG population (250 subject-frame
   * pairs over 5 seeds) that describes the TAIL, not the typical pair: median
   * 1.01x, p90 2.40x, max 11.1x, with 30 of 250 pairs above 2x. Divide by
   * `massG * GRAVITY_N_PER_G` for frame-weight units.
   */
  hookForceN: number;
  /**
   * RMS spread of the pad samples' signed distance to the skin, mm.
   *
   * The single number that says whether the pads BED DOWN. Zero means every
   * sample is the same distance from the surface — a flush pad. Large means the
   * pad is touching on an edge, which is what a wearer feels as a pressure
   * point. This is the metric v1 had no way to compute, because it had one
   * contact point per side and a single point is always flush with itself.
   */
  padSeatErrorMm: number;
  /** Worst floating sample, mm. Positive means a corner of a pad in mid-air. */
  padWorstGapMm: number;
  /** Worst buried sample, mm. */
  padWorstBuryMm: number;
  /**
   * How far the mean pad contact is from where skin contact should sit, mm.
   *
   * Positive means the pads are hovering; negative means they are buried. This
   * is the axis a landmark-hung placement gets wrong, and it is separate from
   * flushness on purpose — see the note in `describeSeat`.
   */
  padDepthErrorMm: number;
  /**
   * What an optician would do to the pads, per side, degrees.
   *
   * Real nose pads pivot on their arms and self-align to the sidewall; a rigid
   * pad on a curved nose touches on a line whatever the frame does, so a few
   * millimetres of gap-to-bury across a 12 mm pad is geometry, not a bad seat.
   * This is the angle between the pad's own plane and the skin under it — the
   * actionable version of that geometry, and the thing an optician bends.
   */
  padTiltDeg: [number, number];
  /** Flushness that articulated pads would achieve, mm: the RMS depth spread
   *  left once the best rigid pad pivot has been fitted out, on `n - 3` degrees
   *  of freedom per pad. `PAD_CURVATURE_LIMIT_MM` is where it stops being an
   *  adjustment and starts being the wrong frame. */
  padSeatErrorArticulatedMm: number;
  notes: string[];
}

/**
 * Where a pad sample should sit relative to the skin at rest, mm.
 *
 * Negative — the pad compresses the skin. Half a millimetre is what the
 * derivation in `SKIN` implies for a 24 g frame, and it is the reference the
 * depth error is measured against.
 */
export const TARGET_CONTACT_MM = -0.5;

/**
 * Above this much unfixable curvature across a pad, the frame is wrong for the
 * face rather than the pads being wrong for the frame. Millimetres, RMS.
 *
 * This is a wearer-facing bar — past it the readout stops saying *"an optician
 * would bend them flat in seconds"* and starts saying *"this frame's bridge is
 * the wrong shape for you"* — so it has to be defensible in both directions.
 *
 * **Derivation.** `padSeatErrorArticulatedMm` is an RMS over the pad's samples
 * of what a rigid pivot cannot remove. Measured across the catalogue the worst
 * single sample runs 1.56x that RMS (median; 1.95 at p90), so a 0.9 mm RMS puts
 * the pad's worst corner about 1.4 mm off its own best-fit plane. `SKIN`'s
 * derivation has the nasal sidewall compressing about 1 mm under a 24 g frame:
 * below that the tissue simply closes the gap and the wearer feels a pad, above
 * it part of the pad is in the air and the rest is a pressure point. 1.4 mm is
 * half again the whole compression budget, which is the margin that keeps this
 * off faces the skin can absorb.
 *
 * **What it does on the population.** 5 catalogue frames x 29 synthetic faces,
 * 145 pairs: median 0.54 mm, p90 0.90, max 1.67. The bar fires on 14 of the 145
 * — 7 on `narrow-pads` (pads too close together for a broad nose, which no
 * amount of bending fixes), 3 on `standard`, 2 on `steep-pads`, 1 each on
 * `wide-pads` and `heavy-acetate`.
 *
 * **Why it is not still 1.0.** The old 1.0 mm bar sat on the old index-based
 * detrend and fired on 55 of those same 145 pairs, 22 of them on `steep-pads`
 * — more than on any other frame in the catalogue, and `steep-pads` is the one
 * whose deliberate defect is TILT. The estimator was converting the fixable
 * fault into the unfixable verdict; the threshold had to be re-derived with it
 * rather than carried across.
 *
 * The numbers above are the synthetic population's, so they move when the
 * population does. Two loose ends go with that: `docs/CONSTANTS.md` has no row
 * for this — it was a bare literal, and a bare literal is exactly what
 * `check-constants.mjs` cannot see — and `advice.ts` still grades pad contact
 * against its own bare `1.0` for the same decision. Both should point here.
 */
export const PAD_CURVATURE_LIMIT_MM = 0.9;

/**
 * The pad-approach spring: a WEAK pull toward contact when a pad hovers, as a
 * fraction of the skin stiffness.
 *
 * The contact term is one-sided — it punishes penetration and says nothing
 * about a gap — so nothing in the energy ever closed a hovering pad: a frame
 * held up by its hook alone was a legal equilibrium, and the first real wearer
 * sat in exactly that state after the ear-anchor repair (pads +1.5 mm off,
 * "the frame is being held up by something else and will slide"). Physically
 * the missing effect is that a worn frame settles until the pads BEAR — the
 * nose is where the weight belongs — so the approach spring is the energy's
 * statement of the same fact, weak enough (a few percent of skin) that it can
 * position a hovering frame but never fight a real contact force.
 *
 * Swept on the population (12 truth faces, standard frame; signed padDepth
 * median / worst hover / bury tail / panto):
 *
 *     0.00: 0.92 / 3.58 / -0.49 / 3.5      0.20: 0.61 / 1.68 / -0.52 / 3.9  <- chosen
 *     0.05: 0.84 / 2.93 / -0.50 / 3.6      0.40: 0.51 / 1.07 / -0.54 / 2.8
 *     0.10: 0.78 / 2.51 / -0.51 / 3.8      0.60: 0.40 / 0.68 / -0.57 / 3.5
 *
 * The bury tail never moves — the term provably cannot fight a real contact —
 * so the choice is where the MEANING changes rather than where a metric
 * breaks: past ~0.2 the spring is no longer a weak settling force but a second
 * contact model, and a pad far from the nose on an unusual face would be
 * pulled with force comparable to skin. A fifth of skin stiffness closes a
 * millimetre-class hover and stays a rounding error against genuine contact.
 * Folded into the `contact` bucket of `energyTerms`, so the central-difference
 * gradient test covers it with no new plumbing.
 */
export const PAD_APPROACH_FRACTION = 0.2;

// ---------------------------------------------------------------- the solve

export function solveSeat(
  model: FaceModel, mesh: FaceMesh, regions: Record<string, Region>,
  frame: FrameAsset, options: Partial<SeatOptions> = {},
): SeatResult {
  const opt = { ...SEAT_DEFAULTS, ...options };
  const started = nowMs();
  const notes: string[] = [];

  // Fail where the mistake is: a hook stiffness of 0 is a frame with nothing
  // stopping it sliding off the face (measured: descent 57.7 mm), and a
  // negative one is a hook that pulls the frame forward. Neither is an
  // experiment this option exists to run.
  const hookK = hookStiffnessOf(opt);
  if (!Number.isFinite(hookK) || hookK <= 0) {
    throw new Error(
      `solveSeat: hookStiffnessNPerMm must be a positive finite number, got ${hookK}`,
    );
  }

  // Contact geometry: the nose plus a margin, so a pad can never find its
  // closest point on the far cheek.
  const contactVerts = union(regions.nose.members, regions.cheeks.members);
  const distance = buildMeshDistance(mesh, model.positions, contactVerts);

  // Clearance geometry: the whole face, because a rim can hit a brow.
  const clearance = opt.useClearance
    ? buildMeshDistance(mesh, model.positions)
    : null;

  // The prior is always the nominal placement, whatever the solve starts from:
  // `descentMm` is measured against v1's answer, so that it stays directly
  // comparable to the behaviour this replaces.
  const prior = nominalPose(model, frame);
  const pose = opt.initialPose ? poseClone(opt.initialPose) : poseClone(prior);

  const H = new Float64Array(36);
  const g = new Float64Array(6);
  const Haug = new Float64Array(36);
  const dx = new Float64Array(6);

  let lambda = 1e-2;
  let energy = evaluateEnergy(model, frame, pose, distance, clearance, prior, opt);
  let iterations = 0;
  let converged = false;
  let terminationReason = 'iteration budget';

  for (; iterations < opt.maxIterations; iterations++) {
    H.fill(0); g.fill(0);
    accumulate(model, frame, pose, distance, clearance, prior, opt, H, g);

    let stepped = false;
    for (let attempt = 0; attempt < 10; attempt++) {
      Haug.set(H);
      for (let d = 0; d < 6; d++) {
        Haug[d * 6 + d] = Haug[d * 6 + d] * (1 + lambda) + 1e-9;
      }
      for (let d = 0; d < 6; d++) dx[d] = -g[d];
      if (!ldlt(Haug, 6)) { lambda *= 10; continue; }
      ldltSolve(Haug, 6, dx);

      // A contact solve can propose a step that tunnels the frame through the
      // nose in one jump, and the energy on the far side is genuinely lower —
      // it is a different, wrong, local minimum. Capping the step at a few
      // millimetres keeps the descent on the surface it started on.
      capStep(dx, 4, 0.12);

      const trial = poseClone(pose);
      poseOplus(trial, trial, dx, 0);
      const e = evaluateEnergy(model, frame, trial, distance, clearance, prior, opt);
      if (e < energy) {
        pose.R.set(trial.R); pose.t.set(trial.t);
        energy = e;
        lambda = Math.max(lambda * 0.4, 1e-8);
        stepped = true;
        break;
      }
      lambda *= 6;
      if (lambda > 1e10) break;
    }
    if (!stepped) {
      // No step improved the energy. For a one-sided contact energy that is a
      // local minimum, not a failure — the solve has settled into a corner of
      // the active set. Reporting it as "unconverged" made 5 of 8 subjects look
      // broken while every one of them had a sensible seat.
      converged = true;
      terminationReason = 'no improving step';
      break;
    }
    // Micrometres and microradians. Tighter than this is not convergence, it is
    // chatter: contact is one-sided, so the energy has continuous value and
    // gradient but a *discontinuous* second derivative wherever a sample enters
    // or leaves the surface, and LM near such a point takes tiny alternating
    // steps forever. An earlier 1e-5 mm bar reported 8 of 8 solves unconverged
    // while every one of them had actually settled.
    if (Math.hypot(dx[3], dx[4], dx[5]) < 1e-3 && Math.hypot(dx[0], dx[1], dx[2]) < 1e-5) {
      converged = true;
      terminationReason = 'step below tolerance';
      break;
    }
  }
  if (iterations >= opt.maxIterations) terminationReason = 'iteration budget';

  const report = describeSeat(model, mesh, frame, pose, prior, distance, clearance, notes, hookK);
  opt.trace?.(
    `seat ${frame.id}: descent ${report.descentMm.toFixed(2)} mm, ` +
    `pads ${(report.padLoadFraction * 100).toFixed(0)}% load, ` +
    `pantoscopic ${report.pantoscopicDeg.toFixed(1)} deg`,
  );

  return {
    ...report,
    pose,
    converged,
    terminationReason,
    iterations,
    energy,
    solveMs: nowMs() - started,
    notes,
  };
}

// -------------------------------------------------------------- the energy

export interface Terms {
  contact: number;
  gravity: number;
  ear: number;
  clearance: number;
  prior: number;
}

function evaluateEnergy(
  model: FaceModel, frame: FrameAsset, pose: Pose, distance: MeshDistance,
  clearance: MeshDistance | null, prior: Pose, opt: SeatOptions,
): number {
  const t = energyTerms(model, frame, pose, distance, clearance, prior, opt);
  return t.contact + t.gravity + t.ear + t.clearance + t.prior;
}

/**
 * Exported for the harness, not for callers: a central-difference test of the
 * solver's gradient has to difference THIS function against `accumulate`'s
 * output, and a test that restates the energy instead of importing it is a
 * check that cannot fail (see `pointJacobian`'s test in tests/core.test.ts,
 * which had to extract the shipped function from the compiled build for the
 * same reason). Everything a caller should want is on `solveSeat`.
 */
export function energyTerms(
  model: FaceModel, frame: FrameAsset, pose: Pose, distance: MeshDistance,
  clearance: MeshDistance | null, prior: Pose, opt: SeatOptions,
): Terms {
  const cp = emptyClosestPoint();
  const p = v3();
  const samples = frame.padSamples.length / 3;
  const kPerSample = SKIN.stiffnessNPerMm / Math.max(samples, 1);

  let contact = 0;
  const kApproach = kPerSample * PAD_APPROACH_FRACTION;
  for (let i = 0; i < samples; i++) {
    apply(p, pose, frame.padSamples, i * 3);
    if (!distance.query(p[0], p[1], p[2], cp)) continue;
    const penetration = Math.max(0, -cp.distance);
    contact += 0.5 * kPerSample * penetration * penetration;
    // The approach side: weak, quadratic in the hover gap, pulling a floating
    // pad toward the skin it should bear on. See PAD_APPROACH_FRACTION.
    const gap = Math.max(0, cp.distance);
    contact += 0.5 * kApproach * gap * gap;
  }

  // Gravity: the frame's centre of mass, approximated at the midpoint of the
  // lens centres. A real COM would come from the asset's mass distribution; the
  // approximation moves the pantoscopic result by well under a degree because
  // the lever arm is what matters and it is set by the pads, not the COM's
  // exact position along it.
  const com = v3();
  comOf(com, frame);
  apply(p, pose, com, 0);
  const weightN = frame.massG * GRAVITY_N_PER_G;
  const gravity = weightN * p[1];

  let ear = 0;
  if (opt.useEars) {
    const ears = earRestPoints(model);
    for (let s = 0; s < 2; s++) {
      apply(p, pose, frame.earRests[s], 0);
      // The ear supports from below only: energy appears when the temple would
      // be below the rest point.
      const below = Math.max(0, ears[s][1] - p[1]);
      ear += 0.5 * SKIN.earStiffnessNPerMm * below * below;
      // The hook: the temple resists travelling forward past the ear — a wall
      // at the default stiffness, a bending arm at the compliant one.
      const forward = Math.max(0, p[2] - ears[s][2]);
      ear += 0.5 * hookStiffnessOf(opt) * forward * forward;
    }
  }

  let clear = 0;
  if (clearance) {
    const points = clearanceSamples(frame);
    // Combined over the sample set, exactly as `accumulate` builds its rows —
    // see `clearanceStiffnessNPerMm`. The energy used the full stiffness per
    // sample while the Gauss-Newton row used it divided by the count, which made
    // this the one term where the curvature the minimiser stepped on was 8 times
    // smaller than the energy it was measuring. Dead term or not, a Hessian that
    // does not belong to the energy is not a bug worth keeping.
    const k = SKIN.clearanceStiffnessNPerMm / Math.max(points.length, 1);
    for (const point of points) {
      apply(p, pose, point, 0);
      if (!clearance.query(p[0], p[1], p[2], cp)) continue;
      if (cp.magnitude > 25) continue; // off the mesh — the sign means nothing
      const penetration = Math.max(0, -cp.distance);
      clear += 0.5 * k * penetration * penetration;
    }
  }

  let pr = 0;
  for (let a = 0; a < 3; a++) {
    const d = pose.t[a] - prior.t[a];
    pr += 0.5 * opt.priorWeight * d * d;
  }
  const w = v3();
  rotationVectorBetween(w, pose.R, prior.R);
  for (let a = 0; a < 3; a++) pr += 0.5 * opt.rotationPriorWeight * w[a] * w[a];

  return { contact, gravity, ear, clearance: clear, prior: pr };
}

/** Exported for the same gradient test `energyTerms` is — see there. */
export function accumulate(
  model: FaceModel, frame: FrameAsset, pose: Pose, distance: MeshDistance,
  clearance: MeshDistance | null, prior: Pose, opt: SeatOptions,
  H: Float64Array, g: Float64Array,
): void {
  const cp = emptyClosestPoint();
  const p = v3(), rot = v3();
  const J = new Float64Array(18); // 3x6, d(world point)/d(pose)
  const samples = frame.padSamples.length / 3;
  const kPerSample = SKIN.stiffnessNPerMm / Math.max(samples, 1);

  const addOneSided = (
    local: ArrayLike<number>, offset: number, dist: MeshDistance,
    stiffness: number, maxMagnitude: number,
  ) => {
    applyBoth(p, rot, pose, local, offset);
    if (!dist.query(p[0], p[1], p[2], cp)) return;
    if (cp.magnitude > maxMagnitude) return;
    if (!(cp.distance < 0)) return; // no contact, no force
    pointJacobian(J, rot);
    // r = sqrt(k) * (-d) = sqrt(k) * |p - cp|;  dr/dx = sqrt(k) * u^T * dp/dx,
    // where `u` is the TRUE distance gradient — the unit vector from the
    // closest point to the sample — exactly as in `addApproach` below.
    //
    // **This branch used the interpolated normal until 2026-08-25, and the
    // comment that justified it was wrong twice over.** It read "a penetrating
    // sample's closest feature is the face it went through", but (a)
    // `meshdist.testTriangle` builds `distance = sign * |p - cp|` from the
    // CLOSEST-POINT geometry, so d(distance)/dp = sign * u whatever feature
    // was hit, and (b) `cp.n` is a barycentrically interpolated VERTEX normal
    // — the file that computes it says "Interpolated normal, not the face
    // normal" on the line above — so it is not the facet direction even when
    // the closest point IS face-interior. Measured across the population at
    // settled poses, the two vectors sat 9.0 degrees apart at the median and
    // 34.7 at the worst.
    //
    // The cost was not a slow solve. The step was built from one vector and
    // the accept test judged it by another, so LM drove ITS OWN gradient to
    // zero and reported `converged: true` at a pose where the true gradient
    // of `energyTerms` was still 0.19-0.27 — a stationary point of nothing.
    // Twelve of eighteen pad samples penetrate at a settled pose, so this was
    // most of the contact rows, on every seat the app has ever solved.
    const sk = Math.sqrt(stiffness);
    const r = sk * -cp.distance;
    const inv = 1 / Math.max(cp.magnitude, 1e-9);
    const ux = (p[0] - cp.x) * inv, uy = (p[1] - cp.y) * inv, uz = (p[2] - cp.z) * inv;
    const row = new Float64Array(6);
    for (let a = 0; a < 6; a++) {
      row[a] = sk * (ux * J[a] + uy * J[6 + a] + uz * J[12 + a]);
    }
    for (let a = 0; a < 6; a++) {
      g[a] += row[a] * r;
      for (let b = 0; b <= a; b++) H[a * 6 + b] += row[a] * row[b];
    }
  };

  const addApproach = (
    local: ArrayLike<number>, offset: number, dist: MeshDistance, stiffness: number,
  ) => {
    applyBoth(p, rot, pose, local, offset);
    if (!dist.query(p[0], p[1], p[2], cp)) return;
    if (!(cp.distance > 0)) return; // touching or inside: the contact side owns it
    pointJacobian(J, rot);
    // r = sqrt(k) * d;  dr/dx = sqrt(k) * u^T * dp/dx, where u is the TRUE
    // distance gradient: the unit vector from the closest point to the sample.
    // For a face-interior closest point u equals the FACE normal, but a
    // HOVERING sample's nearest feature is often an edge or vertex, where the
    // interpolated normal is a few degrees off u — enough that the gradient
    // test caught the drift at the fourth significant digit. The penetration
    // side now builds the same row for the same reason; see `addOneSided`
    // for the premise that kept it on `cp.n` until 2026-08-25.
    const sk = Math.sqrt(stiffness);
    const r = sk * cp.distance;
    const inv = 1 / Math.max(cp.magnitude, 1e-9);
    const ux = (p[0] - cp.x) * inv, uy = (p[1] - cp.y) * inv, uz = (p[2] - cp.z) * inv;
    const row = new Float64Array(6);
    for (let a = 0; a < 6; a++) {
      row[a] = sk * (ux * J[a] + uy * J[6 + a] + uz * J[12 + a]);
    }
    for (let a = 0; a < 6; a++) {
      g[a] += row[a] * r;
      for (let b = 0; b <= a; b++) H[a * 6 + b] += row[a] * row[b];
    }
  };

  const kApproach = kPerSample * PAD_APPROACH_FRACTION;
  for (let i = 0; i < samples; i++) {
    addOneSided(frame.padSamples, i * 3, distance, kPerSample, 30);
    addApproach(frame.padSamples, i * 3, distance, kApproach);
  }

  if (clearance) {
    const points = clearanceSamples(frame);
    const k = SKIN.clearanceStiffnessNPerMm / Math.max(points.length, 1);
    for (const point of points) addOneSided(point, 0, clearance, k, 25);
  }

  // Gravity: linear, so it contributes gradient only. dE/dx = w * d(y_com)/dx.
  const com = v3();
  comOf(com, frame);
  applyBoth(p, rot, pose, com, 0);
  pointJacobian(J, rot);
  const weightN = frame.massG * GRAVITY_N_PER_G;
  for (let a = 0; a < 6; a++) g[a] += weightN * J[6 + a];

  if (opt.useEars) {
    const ears = earRestPoints(model);
    const row = new Float64Array(6);
    for (let s = 0; s < 2; s++) {
      applyBoth(p, rot, pose, frame.earRests[s], 0);
      pointJacobian(J, rot);

      // Vertical support: d(below)/dx = -d(y)/dx.
      const below = ears[s][1] - p[1];
      if (below > 0) {
        const sk = Math.sqrt(SKIN.earStiffnessNPerMm);
        const r = sk * below;
        for (let a = 0; a < 6; a++) row[a] = -sk * J[6 + a];
        for (let a = 0; a < 6; a++) {
          g[a] += row[a] * r;
          for (let b = 0; b <= a; b++) H[a * 6 + b] += row[a] * row[b];
        }
      }

      // The hook: d(forward)/dx = +d(z)/dx. Same stiffness as the energy above,
      // whichever the options chose — a Hessian that does not belong to the
      // energy is not a bug worth introducing twice (see the clearance term).
      const forward = p[2] - ears[s][2];
      if (forward > 0) {
        const sk = Math.sqrt(hookStiffnessOf(opt));
        const r = sk * forward;
        for (let a = 0; a < 6; a++) row[a] = sk * J[12 + a];
        for (let a = 0; a < 6; a++) {
          g[a] += row[a] * r;
          for (let b = 0; b <= a; b++) H[a * 6 + b] += row[a] * row[b];
        }
      }
    }
  }

  for (let a = 0; a < 3; a++) {
    const d = pose.t[a] - prior.t[a];
    g[3 + a] += opt.priorWeight * d;
    H[(3 + a) * 6 + (3 + a)] += opt.priorWeight;
  }
  {
    const w = v3();
    rotationVectorBetween(w, pose.R, prior.R);
    for (let a = 0; a < 3; a++) {
      g[a] += opt.rotationPriorWeight * w[a];
      H[a * 6 + a] += opt.rotationPriorWeight;
    }
  }
}

// ------------------------------------------------------------------ helpers

/** The hook stiffness a solve actually uses: the option, or the shipped wall. */
const hookStiffnessOf = (opt: SeatOptions): number =>
  opt.hookStiffnessNPerMm ?? SKIN.hookStiffnessNPerMm;

/** d(R p + t)/d(pose increment), 3x6, row-major. `rot` must be `R p`. */
function pointJacobian(J: Float64Array, rot: Vec3): void {
  // d/dw = -skew(rot); d/dt = I
  const px = rot[0], py = rot[1], pz = rot[2];
  J[0] = 0;    J[1] = pz;   J[2] = -py;  J[3] = 1; J[4] = 0; J[5] = 0;
  J[6] = -pz;  J[7] = 0;    J[8] = px;   J[9] = 0; J[10] = 1; J[11] = 0;
  J[12] = py;  J[13] = -px; J[14] = 0;   J[15] = 0; J[16] = 0; J[17] = 1;
}

function apply(out: Vec3, pose: Pose, p: ArrayLike<number>, o: number): void {
  const x = p[o], y = p[o + 1], z = p[o + 2];
  const R = pose.R;
  out[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
  out[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
  out[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
}

function applyBoth(out: Vec3, rot: Vec3, pose: Pose, p: ArrayLike<number>, o: number): void {
  const x = p[o], y = p[o + 1], z = p[o + 2];
  const R = pose.R;
  rot[0] = R[0] * x + R[1] * y + R[2] * z;
  rot[1] = R[3] * x + R[4] * y + R[5] * z;
  rot[2] = R[6] * x + R[7] * y + R[8] * z;
  out[0] = rot[0] + pose.t[0];
  out[1] = rot[1] + pose.t[1];
  out[2] = rot[2] + pose.t[2];
}

function capStep(dx: Float64Array, maxMm: number, maxRad: number): void {
  const t = Math.hypot(dx[3], dx[4], dx[5]);
  if (t > maxMm) { const s = maxMm / t; dx[3] *= s; dx[4] *= s; dx[5] *= s; }
  const r = Math.hypot(dx[0], dx[1], dx[2]);
  if (r > maxRad) { const s = maxRad / r; dx[0] *= s; dx[1] *= s; dx[2] *= s; }
}

function comOf(out: Vec3, frame: FrameAsset): void {
  out[0] = 0;
  out[1] = (frame.lensCentres[0][1] + frame.lensCentres[1][1]) / 2;
  out[2] = (frame.lensCentres[0][2] + frame.lensCentres[1][2]) / 2;
}

/**
 * Points that must not pass through the face but must not attract it either:
 * the lens rim corners.
 *
 * **The temple arms are deliberately not here**, and an earlier version that
 * included them was wrong in an instructive way. Arms are *supposed* to touch
 * the head — that contact is the splay, and it is load-bearing. Testing them
 * for non-penetration against a straight hinge-to-ear line reported a constant
 * 10 mm foul on every face and every frame, because a straight line from the
 * hinge to a point behind the ear passes through the skull. The solve then
 * spent its whole budget pushing the frame away from a violation that is not a
 * violation.
 *
 * Modelling arm contact properly means modelling the arm as a curve with splay
 * compliance, which is `splayStiffnessNPerMm` and is not yet used. Until it is,
 * the honest thing is to leave arms out rather than to test them wrongly. See
 * `docs/OPEN-QUESTIONS.md` Q6.
 *
 * What is here is four corners per rim at exactly the lens centre's depth: a
 * flat rectangle, no wrap, no lens curvature, no descending bottom rim. That is
 * why the clearance term has never once engaged — see `clearanceStiffnessNPerMm`
 * for the sweep. The missing geometry is the bottom rim, which is the part of a
 * real frame that meets a cheek.
 */
export function clearanceSamples(frame: FrameAsset): Float64Array[] {
  const out: Float64Array[] = [];
  for (let s = 0; s < 2; s++) {
    const c = frame.lensCentres[s];
    const halfLens = frame.frontWidthMm * 0.11;
    for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1]] as const) {
      out.push(Float64Array.of(c[0] + dx * halfLens, c[1] + dy * halfLens * 0.7, c[2]));
    }
  }
  return out;
}

/**
 * Where the temples come to rest on the ears, in face space.
 *
 * The mesh has no ears — MediaPipe's face model stops at the silhouette and its
 * rearmost point is only ~24 mm behind the cheek. So the rest point is
 * extrapolated from this wearer's own ear-top and cheek landmarks, exactly as
 * v1 did, with v1's constants. Approximate by construction; the alternative is
 * arms that run inside the skull.
 *
 * This is a real limitation of the template and it caps how well the ear support
 * term can work. `docs/OPEN-QUESTIONS.md` Q5 is about replacing the template
 * with one that has ears.
 */
const BEHIND_CHEEK_MM = 17;
/**
 * The ear rest's depth: how far BEHIND the outer eye corner the tragion sits,
 * as a posterior offset in face space, mm.
 *
 * This replaced the cheek extrapolation (`cheek.z - BEHIND_CHEEK_MM`, kept
 * above only for its history) after the first real wearer, and the reasoning
 * deserves its length. Fore-aft is the seat's most consequential axis and its
 * anchor was the scan's WORST surface: cheeks repeat at ~1.5 mm on a real face
 * (nose: 0.4), their reconstruction error is larger still, and the ledger's own
 * sensitivity row records 9 mm of cheek moving the lens distance by 5 mm. On
 * the first real face the chain missed by enough to press the pads 1.9 mm into
 * the skin and park the lenses 5 mm from the eyes — and softening the hook
 * (the Q15 experiment) barely moved it, which is the fingerprint of a wrongly
 * PLACED wall rather than a wrongly STIFF one.
 *
 * The replacement anchors on the outer canthus — one of the scan's
 * best-measured landmarks (~0.6 mm on a real face) — using the published
 * ectocanthion-tragion distance: ~71 mm male / ~69 mm female straight-line
 * means (Farkas), SD ~4 mm. The tragion sits ~10 mm below the canthus, so the
 * posterior projection is sqrt(70^2 - 10^2) ~ 69 mm; the temple's rest in the
 * sulcus is at the tragion's depth. `published`, pooled, and honest about its
 * SD — a 4 mm population spread on the anchor is a 2-3 mm lens-distance
 * spread, against the 5-mm-per-9-mm cheek lever it replaces.
 *
 * 73, not the anatomical 69-71: the extra ~3 mm is the sulcus sitting behind
 * the tragion plus the hinge/reach conventions this offset composes with, and
 * it was SWEPT rather than argued (12 truth faces, standard frame, signed
 * padDepth median / bury tail / panto):
 *
 *     69: 1.49 /  0.02 / 6.2       73: 0.92 / -0.49 / 3.5   <- chosen
 *     71: 1.40 / -0.14 / 4.8       75: 0.57 / -0.99 / 2.0
 *                                  77: 0.53 / -1.94 / 0.9
 *
 * Past 73 the pads start burying again and the pantoscopic tilt collapses —
 * the cheek anchor's disease returning through the far door. Re-sweep this if
 * the temple reach, hinge convention, or pad geometry changes.
 */
const EYE_TO_TRAGION_Z_MM = 73;
/**
 * How far above the outer canthus the temple actually rests, mm.
 *
 * Anatomy, not landmark 162. v1 called that landmark `EAR_TOP` and placed the
 * rest 5 mm below it; on the template it sits at y = 41, which is 14.5 mm above
 * the eye line and well above the superior attachment of the pinna — it is a
 * silhouette vertex near the hairline, because this mesh has no ears at all.
 * Using it put the rest point about 10 mm too high, which with any realistic ear
 * stiffness is several newtons of phantom support.
 *
 * The temple rests in the auriculo-cephalic sulcus, roughly level with the
 * outer canthus and a little above it. 6 mm is the midpoint of what the
 * anthropometric literature reports; it is `stated`, and it is bounded by the
 * fact that the term is one-sided — being a few millimetres out changes how
 * much load the ear takes, not whether the frame stays on the face.
 */
const ABOVE_EYE_LINE_MM = 6;

export function earRestPoints(model: FaceModel): [Vec3, Vec3] {
  const p = model.positions;
  // x from the temple silhouette (the head's true width there), y a measured
  // offset above the canthus line, z from the canthus itself — the anchor is
  // the wearer's best landmark plus a published anatomical offset, not the
  // cheek chain that missed the first real face (see EYE_TO_TRAGION_Z_MM).
  const make = (eye: number, templeX: number): Vec3 => v3(
    p[templeX * 3],
    p[eye * 3 + 1] + ABOVE_EYE_LINE_MM,
    p[eye * 3 + 2] - EYE_TO_TRAGION_Z_MM,
  );
  return [
    make(LM.EYE_OUTER_R, LM.TEMPLE_R),
    make(LM.EYE_OUTER_L, LM.TEMPLE_L),
  ];
}

/**
 * The starting placement: the pad centroid on the nasal sidewall at bridge
 * height, front upright.
 *
 * This stands in for v1's answer — hang the pads on the bridge landmark — with
 * one correction that v1 also needed and did not have. Landmark 6 is on the
 * **ridge**, the apex of the nose; the pads bear on the **sidewalls**, which on
 * the template sit 11 mm further back in z. Placing the pad centroid at the
 * ridge starts the frame floating 2 to 10 mm clear of the skin, which is not a
 * plausible initial guess and is not what v1 did either — v1's `analyseModel`
 * found the *rearmost* geometry in a central column, which lands on the pads.
 *
 * The difference between this pose and the solved one is `descentMm`, which
 * makes the report directly comparable to a landmark-hung placement: it is how
 * far a frame hung off a landmark is from where it would actually rest.
 */
export function nominalPose(model: FaceModel, frame: FrameAsset): Pose {
  const pose = poseIdentity();
  const p = model.positions;
  // The sidewall strip the pads bear on, at bridge height.
  const wallY = (p[LM.NOSE_WALL_HIGH_R * 3 + 1] + p[LM.NOSE_WALL_HIGH_L * 3 + 1]) / 2;
  const wallZ = (p[LM.NOSE_WALL_HIGH_R * 3 + 2] + p[LM.NOSE_WALL_HIGH_L * 3 + 2]) / 2;
  pose.t[0] = 0;
  pose.t[1] = wallY;
  pose.t[2] = wallZ;
  return pose;
}

/**
 * v1's actual placement: the pad contact point hung on the bridge landmark.
 *
 * **This is a control, not an initialisation.** `nominalPose` above is the
 * solver's starting guess and it deliberately seats the pads on the nasal
 * sidewall, because starting a contact solve with the pads floating 2 to 10 mm
 * clear of the skin is not a plausible place to begin.
 *
 * But that also made it much better than what v1 ships, and for a while the two
 * jobs shared one function — so the harness was comparing the contact solve
 * against a baseline that had already been given half the answer, and reported
 * the solve as only 1.2x better than "no solve at all". Two jobs, two functions.
 *
 * Landmark 6 is on the RIDGE, the apex of the nose. Hanging the pads there is
 * exactly v1's *"a landmark is a point and a nose is a wedge"* — and the wedge
 * is why it does not work.
 */
export function landmarkHungPose(model: FaceModel, frame: FrameAsset): Pose {
  const pose = poseIdentity();
  const p = model.positions;

  // The PAD CENTROID goes on the landmark, not the frame origin — otherwise the
  // control is testing where this asset happens to put its origin rather than
  // v1's rule. Averaged over the samples, since a pad is a surface.
  let cy = 0;
  let cz = 0;
  const n = frame.padSide.length;
  for (let i = 0; i < n; i++) {
    cy += frame.padSamples[i * 3 + 1];
    cz += frame.padSamples[i * 3 + 2];
  }
  cy /= n;
  cz /= n;

  pose.t[0] = 0;
  pose.t[1] = p[LM.NOSE_BRIDGE * 3 + 1] - cy;
  pose.t[2] = p[LM.NOSE_BRIDGE * 3 + 2] - cz;
  return pose;
}

/** The rotation vector taking `from` to `to`, for the rotational prior. */
function rotationVectorBetween(out: Vec3, to: Mat3, from: Mat3): Vec3 {
  const fT = Float64Array.of(
    from[0], from[3], from[6],
    from[1], from[4], from[7],
    from[2], from[5], from[8],
  );
  const d = m3();
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      d[r * 3 + c] = to[r * 3] * fT[c] + to[r * 3 + 1] * fT[3 + c] + to[r * 3 + 2] * fT[6 + c];
    }
  }
  return logSO3(out, d);
}

// --------------------------------------------------------------- reporting

function describeSeat(
  model: FaceModel, mesh: FaceMesh, frame: FrameAsset, pose: Pose, prior: Pose,
  distance: MeshDistance, clearance: MeshDistance | null, notes: string[],
  hookK: number,
): Omit<SeatResult,
  'pose' | 'converged' | 'terminationReason' | 'iterations' | 'energy' | 'solveMs' | 'notes'
> {
  const cp = emptyClosestPoint();
  const p = v3();
  const samples = frame.padSamples.length / 3;
  const kPerSample = SKIN.stiffnessNPerMm / Math.max(samples, 1);

  const per: Record<number, {
    n: number; bearing: number; sum: number; peak: number; load: number; lift: number;
  }> = {
    [-1]: { n: 0, bearing: 0, sum: 0, peak: 0, load: 0, lift: 0 },
    [1]: { n: 0, bearing: 0, sum: 0, peak: 0, load: 0, lift: 0 },
  };

  // Signed distance of every pad sample, for `padSeatErrorMm` below.
  const signed: number[] = [];

  for (let i = 0; i < samples; i++) {
    const side = frame.padSide[i];
    per[side].n++;
    apply(p, pose, frame.padSamples, i * 3);
    if (!distance.query(p[0], p[1], p[2], cp)) continue;
    signed.push(cp.distance);
    const penetration = Math.max(0, -cp.distance);
    if (penetration > 0) {
      per[side].bearing++;
      per[side].sum += penetration;
      per[side].peak = Math.max(per[side].peak, penetration);
      const normalN = kPerSample * penetration;
      per[side].load += normalN;
      // Only the VERTICAL component of the contact normal carries weight. The
      // nasal sidewall's normal is about 24% vertical, so a normal force
      // magnitude compared against the frame's weight overstates the nose's
      // share by a factor of four — which is how an earlier version of this
      // report showed every frame carrying 100% of its weight on the pads while
      // the ear term was demonstrably engaged.
      //
      // SIGNED, and the `Math.max(cp.ny, 0)` that used to sit here was wrong.
      // Some contacts face DOWNWARD and push the frame down — 77 of 1,316
      // bearing samples over 29 synthetic faces x the 5 catalogue frames, and
      // all 145 of those solves converged. They are not an artefact to clamp
      // away: `addOneSided` above builds the solver's own gradient from the
      // unclamped normal, so it is the SIGNED sum that closes the vertical force
      // balance at a converged pose. Over the 29 pairs that have a downward
      // contact, |weight - padLift - earLift + prior| is 7.1e-3 N signed against
      // 4.7e-2 N clamped, and 5.0e-2 N against 2.6e-1 N at worst. Clamped, this
      // report had pads carrying 194% of the frame's weight.
      per[side].lift += normalN * cp.ny;
    }
  }

  const pad = (side: -1 | 1): PadContact => ({
    side,
    bearingFraction: per[side].n ? per[side].bearing / per[side].n : 0,
    meanPenetrationMm: per[side].bearing ? per[side].sum / per[side].bearing : 0,
    peakPenetrationMm: per[side].peak,
    loadN: per[side].load,
  });
  const pads: [PadContact, PadContact] = [pad(-1), pad(1)];

  const weightN = frame.massG * GRAVITY_N_PER_G;
  const padLift = per[-1].lift + per[1].lift;

  // The EARS have to be in this sum, and for a long time they were not.
  //
  // This was `clamp(padLift / weightN, 0, 1)` — a pad-only ratio, clamped, and
  // therefore a number that reads exactly 1.000 whether the ears carry nothing
  // or seventy percent of the frame. It reached a real wearer as *"Your nose is
  // carrying essentially all the weight"* on a frame whose ears were carrying a
  // quarter of it; the raw ratio behind that 1.000 was 1.66.
  //
  // The comment on `per[side].lift` above records an earlier version of the same
  // bug, fixed by projecting the contact normal onto the vertical. That fix was
  // right and insufficient: the ratio still had the wrong denominator.
  let earLift = 0;
  // The hook force, from the same pass: `hookK` times the forward travel past
  // the ear rest, per side. Reported with the stiffness the SOLVE used, because
  // the number this measures — how hard the arm presses the frame back into the
  // nose — is a property of the equilibrium the solve actually found, and an
  // equilibrium found against a soft spring re-measured against the wall would
  // be a force no configuration ever exerted.
  let hookForceN = 0;
  const earPoints = earRestPoints(model);
  for (let s = 0; s < 2; s++) {
    apply(p, pose, frame.earRests[s], 0);
    earLift += SKIN.earStiffnessNPerMm * Math.max(0, earPoints[s][1] - p[1]);
    hookForceN += hookK * Math.max(0, p[2] - earPoints[s][2]);
  }
  const totalLift = padLift + earLift;
  const padLoadFraction = totalLift > 0 ? clamp(padLift / totalLift, 0, 1) : 0;

  // How hard the pads are pressed compared with what merely holding the frame up
  // would need. `padLoadFraction`'s clamp buries this: above 1 the pads are
  // penetrating past static equilibrium, which is a fault rather than a load
  // share, and it needs its own unclamped number to be visible at all.
  //
  // It is NOT the hook doing it, which is what an earlier version of this
  // comment said. The hook's Jacobian row in `accumulate` is `sk * J[12 + a]` —
  // pure +z, with no y component at all — so however hard it presses it puts
  // nothing into the vertical balance. Nor is it clearance: that term has never
  // engaged on any frame in this catalogue (see `clearanceStiffnessNPerMm`).
  //
  // Measured over 29 faces x 5 frames, 11 pairs read above 1, and every one of
  // them had settled ABOVE the nominal placement — nine on `narrow-pads`, two on
  // `steep-pads`, descent between -0.5 and -8.5 mm. Two things put them there,
  // and both belong to the model rather than to the wearer:
  //
  //   the PRIOR   `priorWeight` times a negative descent pulls a perched frame
  //               back down and the pads carry that too — 1 to 17% of the
  //               frame's weight on these pairs. "Deliberately too weak to move
  //               a converged answer" is true of the pose and not of the load.
  //   the CORNER  56 of the 145 solves stop on 'no improving step', where the
  //               active set flips under any descent direction and the vertical
  //               gradient is still 0 to 23% of the weight when LM gives up.
  //
  // Unclamping the vertical sum shrank both: 20 pairs above 1 became 11, and the
  // worst case 1.94 became 1.26.
  const padOverClosure = weightN > 0 ? padLift / weightN : 0;

  // Two numbers, because they are two different faults and an earlier version
  // of this report conflated them into one that could not tell them apart.
  //
  //   `padSeatErrorMm`  how FLUSH the pad is — the spread about its own mean.
  //                     A pad touching on one edge scores badly here.
  //   `padDepthErrorMm` how DEEP it is — the mean against where skin contact
  //                     should sit. A pad hovering in the air and a pad buried
  //                     four millimetres in both score badly here and identically
  //                     on flushness, which is exactly v1's "the pads float, or
  //                     bury — sign depends on the asset".
  //
  // Reporting only the first made the control table meaningless: a placement
  // hung off the bridge landmark buries the pads 4 mm into the skin and is
  // *perfectly flush* while doing it, so it scored better than the real solve.
  let padSeatErrorMm = 0;
  let padDepthErrorMm = 0;
  if (signed.length > 0) {
    const mean = signed.reduce((a, b) => a + b, 0) / signed.length;
    let acc = 0;
    for (const d of signed) acc += (d - mean) * (d - mean);
    padSeatErrorMm = Math.sqrt(acc / signed.length);
    padDepthErrorMm = mean - TARGET_CONTACT_MM;
  }
  // Worst single sample, signed: positive means floating, negative means buried.
  const padWorstGapMm = signed.length ? Math.max(...signed) : 0;
  const padWorstBuryMm = signed.length ? -Math.min(...signed) : 0;

  const articulation = padArticulation(model, frame, pose, distance);

  // Euler of the settled pose. Pantoscopic tilt is rotation about X: positive
  // when the bottom of the front comes toward the face, which is the convention
  // an optician uses.
  const R = pose.R;
  const pitch = Math.asin(clamp(-R[5], -1, 1));
  const roll = Math.atan2(R[3], R[4]);

  const eyeR = v3(), eyeL = v3();
  const pp = model.positions;
  eyeR.set([
    (pp[LM.EYE_INNER_R * 3] + pp[LM.EYE_OUTER_R * 3]) / 2,
    (pp[LM.EYE_INNER_R * 3 + 1] + pp[LM.EYE_OUTER_R * 3 + 1]) / 2,
    (pp[LM.EYE_INNER_R * 3 + 2] + pp[LM.EYE_OUTER_R * 3 + 2]) / 2,
  ]);
  eyeL.set([
    (pp[LM.EYE_INNER_L * 3] + pp[LM.EYE_OUTER_L * 3]) / 2,
    (pp[LM.EYE_INNER_L * 3 + 1] + pp[LM.EYE_OUTER_L * 3 + 1]) / 2,
    (pp[LM.EYE_INNER_L * 3 + 2] + pp[LM.EYE_OUTER_L * 3 + 2]) / 2,
  ]);

  const vertex: [number, number] = [0, 0];
  const offset: [number, number] = [0, 0];
  const eyes = [eyeR, eyeL];
  for (let s = 0; s < 2; s++) {
    apply(p, pose, frame.lensCentres[s], 0);
    vertex[s] = p[2] - eyes[s][2];
    offset[s] = p[1] - eyes[s][1];
  }

  let worstClearance = 0;
  if (clearance) {
    for (const point of clearanceSamples(frame)) {
      apply(p, pose, point, 0);
      if (!clearance.query(p[0], p[1], p[2], cp)) continue;
      if (cp.magnitude > 25) continue;
      worstClearance = Math.max(worstClearance, Math.max(0, -cp.distance));
    }
  }

  if (pads[0].bearingFraction < 0.15 || pads[1].bearingFraction < 0.15) {
    notes.push('one pad is barely bearing — the frame will rock or sit crooked');
  }
  if (padLoadFraction < 0.35) {
    notes.push('most of the weight is on the ears — the pads are too wide for this nose');
  }
  if (worstClearance > 1.5) {
    notes.push(`the frame fouls the face by ${worstClearance.toFixed(1)} mm away from the pads`);
  }
  const worstTilt = Math.max(articulation.tiltDeg[0], articulation.tiltDeg[1]);
  if (worstTilt > 12) {
    notes.push(
      `the pads sit ${worstTilt.toFixed(0)} degrees off this wearer's sidewall — ` +
      'an optician would bend them',
    );
  }
  if (articulation.residualMm > PAD_CURVATURE_LIMIT_MM) {
    notes.push(
      `even bent flat, the pads cannot follow this nose (${articulation.residualMm.toFixed(1)} mm ` +
      'of curvature across the pad) — this frame does not suit this face',
    );
  }
  if (Math.abs(padDepthErrorMm) > 1.5) {
    notes.push(padDepthErrorMm > 0
      ? `the pads hover ${padDepthErrorMm.toFixed(1)} mm off the skin`
      : `the pads bury ${(-padDepthErrorMm).toFixed(1)} mm into the skin`);
  }

  return {
    pads,
    padLoadFraction,
    padOverClosure,
    padSeatErrorMm,
    padWorstGapMm,
    padWorstBuryMm,
    padDepthErrorMm,
    padTiltDeg: articulation.tiltDeg,
    padSeatErrorArticulatedMm: articulation.residualMm,
    descentMm: prior.t[1] - pose.t[1],
    pantoscopicDeg: (pitch * 180) / Math.PI,
    rollDeg: (roll * 180) / Math.PI,
    vertexDistanceMm: vertex,
    opticalCentreOffsetMm: offset,
    worstClearanceMm: worstClearance,
    hookForceN,
  };
}

/**
 * How much each pad would have to pivot to lie flat on the skin under it.
 *
 * Two numbers from one pass, and they answer different questions:
 *
 *   `tiltDeg`     the angle between the pad's own normal and the mean surface
 *                 normal under it — what an optician bends out, per side.
 *   `residualMm`  the depth spread left after the best rigid pivot has been
 *                 taken out, fitted as a least-squares PLANE in the pad's own
 *                 in-plane axes. This is the sidewall curvature a rigid pad
 *                 cannot follow however it is bent.
 *
 * Keeping them apart is the whole point: only the first is fixable, and an
 * estimator that leaks one into the other tells a wearer their frame is wrong
 * when their pads merely need bending. See the note on the plane fit below for
 * the version that did exactly that.
 */
function padArticulation(
  model: FaceModel, frame: FrameAsset, pose: Pose, distance: MeshDistance,
): { tiltDeg: [number, number]; residualMm: number } {
  const cp = emptyClosestPoint();
  const p = v3();
  const tilt: [number, number] = [0, 0];
  let residualAcc = 0;
  let residualDof = 0;

  for (const [slot, side] of [[0, -1], [1, 1]] as const) {
    // Mean surface normal under this pad, the pad's own normal in face space
    // (for the tilt angle), and the pad's own normal in FRAME space (for the
    // detrend basis below, which projects frame-local sample positions).
    let snx = 0, sny = 0, snz = 0;
    let pnx = 0, pny = 0, pnz = 0;
    let fnx = 0, fny = 0, fnz = 0;
    const local: number[] = [];
    const at: number[] = [];
    let n = 0;

    for (let i = 0; i < frame.padSamples.length / 3; i++) {
      if (frame.padSide[i] !== side) continue;
      apply(p, pose, frame.padSamples, i * 3);
      if (!distance.query(p[0], p[1], p[2], cp)) continue;
      snx += cp.nx; sny += cp.ny; snz += cp.nz;
      const R = pose.R;
      const nx = frame.padNormals[i * 3], ny = frame.padNormals[i * 3 + 1], nz = frame.padNormals[i * 3 + 2];
      fnx += nx; fny += ny; fnz += nz;
      pnx += R[0] * nx + R[1] * ny + R[2] * nz;
      pny += R[3] * nx + R[4] * ny + R[5] * nz;
      pnz += R[6] * nx + R[7] * ny + R[8] * nz;
      local.push(cp.distance);
      at.push(
        frame.padSamples[i * 3], frame.padSamples[i * 3 + 1], frame.padSamples[i * 3 + 2],
      );
      n++;
    }
    if (n === 0) continue;

    const sl = Math.hypot(snx, sny, snz) || 1;
    const pl = Math.hypot(pnx, pny, pnz) || 1;
    // The pad normal points INTO the face; the surface normal points out. They
    // are flush when they are exactly opposed, so the angle of interest is
    // between the pad normal and the negated surface normal.
    const dot = clamp(
      (pnx / pl) * (-snx / sl) + (pny / pl) * (-sny / sl) + (pnz / pl) * (-snz / sl),
      -1, 1,
    );
    tilt[slot] = (Math.acos(dot) * 180) / Math.PI;

    // Residual after removing the best-fit tilt: the spread that a pivot cannot
    // fix, which is the sidewall's curvature across the pad.
    //
    // A pad pivoting on its arm is a RIGID rotation, so what it removes from the
    // depth field is a linear function of BOTH of the pad's in-plane axes — a
    // plane, not a line. This regressed on the sample INDEX `k` instead, a line
    // through the samples in whatever order they happened to arrive. For
    // `parametricFrame`, which emits row-major with the across-pad axis in the
    // inner loop, that recovers about 90% of a vertical gradient and about 10%
    // of an across-pad one: fed an exactly planar field with zero true
    // curvature, it reported 1.084 mm for a 0.35 mm/mm across-pad tilt and
    // crossed the threshold on pure artefact. Measured over the catalogue x 29
    // synthetic faces it fired hardest — 22 of 29 faces past 1.0 mm — on
    // `steep-pads`, the frame whose DELIBERATE defect is tilt, so it was
    // reporting the fixable fault as the unfixable one. That is the exact
    // inversion of the distinction this function exists to draw.
    //
    // The basis comes from the MEAN PAD NORMAL and deliberately not from
    // `padAngleRad`: `derivePads` reads samples off a GLB in whatever order the
    // mesh stores them, so there is no grid to index and no single spec angle to
    // trust. The normal is the one thing a parametric pad and a scanned one both
    // carry per sample. Frame-space throughout — the pose is rigid, so it moves
    // the pad plane and the samples together and cannot change the fit.
    const fl = Math.hypot(fnx, fny, fnz) || 1;
    const nx = fnx / fl, ny = fny / fl, nz = fnz / fl;
    // Any orthonormal pair spanning the pad plane describes the same plane, so
    // seed from world-up, and from world-x for the degenerate pad that faces it.
    const sx = Math.abs(ny) > 0.9 ? 1 : 0;
    const sy = Math.abs(ny) > 0.9 ? 0 : 1;
    const sd = sx * nx + sy * ny;
    let ux = sx - sd * nx, uy = sy - sd * ny, uz = -sd * nz;
    const ul = Math.hypot(ux, uy, uz) || 1;
    ux /= ul; uy /= ul; uz /= ul;
    const vx = ny * uz - nz * uy, vy = nz * ux - nx * uz, vz = nx * uy - ny * ux;

    // Least squares for `d ~ c + ca*u + cb*v`, through the centred sums.
    let ma = 0, mb = 0, ml = 0;
    const ua: number[] = [], vb: number[] = [];
    for (let k = 0; k < n; k++) {
      const a = at[k * 3] * ux + at[k * 3 + 1] * uy + at[k * 3 + 2] * uz;
      const b = at[k * 3] * vx + at[k * 3 + 1] * vy + at[k * 3 + 2] * vz;
      ua.push(a); vb.push(b);
      ma += a; mb += b; ml += local[k];
    }
    ma /= n; mb /= n; ml /= n;
    let saa = 0, sab = 0, sbb = 0, sal = 0, sbl = 0;
    for (let k = 0; k < n; k++) {
      const a = ua[k] - ma, b = vb[k] - mb, d = local[k] - ml;
      saa += a * a; sab += a * b; sbb += b * b; sal += a * d; sbl += b * d;
    }
    // A pad sampled along a single line — three vertices in a row off a scan —
    // has no second axis to fit, so fall back to the axis that has spread rather
    // than dividing by a determinant that is zero.
    const det = saa * sbb - sab * sab;
    let ca = 0, cb = 0;
    if (det > 1e-9 * (saa * sbb + 1e-12)) {
      ca = (sbb * sal - sab * sbl) / det;
      cb = (saa * sbl - sab * sal) / det;
    } else if (saa >= sbb && saa > 1e-9) {
      ca = sal / saa;
    } else if (sbb > 1e-9) {
      cb = sbl / sbb;
    }
    for (let k = 0; k < n; k++) {
      const r = local[k] - ml - ca * (ua[k] - ma) - cb * (vb[k] - mb);
      residualAcc += r * r;
    }
    // n - 3, not n. Three degrees of freedom went into the plane — an offset and
    // two slopes — and charging the residual to all n reports a number that
    // falls as a pad is sampled more finely rather than as the fit improves. A
    // nine-sample pad has six left.
    residualDof += Math.max(n - 3, 1);
  }

  return {
    tiltDeg: tilt,
    residualMm: residualDof ? Math.sqrt(residualAcc / residualDof) : 0,
  };
}

function union(a: Uint32Array, b: Uint32Array): Uint32Array {
  const set = new Set<number>();
  for (const v of a) set.add(v);
  for (const v of b) set.add(v);
  return Uint32Array.from(set);
}

const nowMs = (): number =>
  (typeof performance !== 'undefined' ? performance.now() : Date.now());

export { m3 };
