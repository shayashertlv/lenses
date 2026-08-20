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
 * it. Doubling `stiffnessNPerMm` changes the settled height by about 0.5 mm and
 * changes which frames fit which faces not at all, because the wedge slope
 * (~0.5 mm of half-width per mm of descent) dominates. `tests/contact.test.ts`
 * sweeps it across a factor of four and pins how little moves — which is the
 * honest way to ship a number you have not measured.
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
   * derivation attached, and the harness sweeps it across a factor of four to
   * show what depends on it. See `docs/OPEN-QUESTIONS.md` Q4.
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
  /** Clearance repulsion for rims and arms, N/mm. Deliberately stiff — this is
   *  not tissue compliance, it is "geometry must not interpenetrate". */
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
  /** How far the frame settled below the nominal placement, mm. Positive is
   *  lower. This is the number v1's wearer was complaining about. */
  descentMm: number;
  /** Pantoscopic tilt at rest, degrees — front-bottom-toward-the-face. */
  pantoscopicDeg: number;
  /** Roll at rest, degrees. Nonzero means the wearer's nose is asymmetric and
   *  the frame will genuinely sit crooked. */
  rollDeg: number;
  /** Vertex distance: lens plane to the eye, mm, per side. */
  vertexDistanceMm: [number, number];
  /** How far the lens optical centres sit from the pupils, mm, per side. */
  opticalCentreOffsetMm: [number, number];
  /** Worst clearance violation anywhere that is not a pad, mm. */
  worstClearanceMm: number;
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
  padTiltAdviceDeg: [number, number];
  /** Flushness that articulated pads would achieve, mm. */
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

// ---------------------------------------------------------------- the solve

export function solveSeat(
  model: FaceModel, mesh: FaceMesh, regions: Record<string, Region>,
  frame: FrameAsset, options: Partial<SeatOptions> = {},
): SeatResult {
  const opt = { ...SEAT_DEFAULTS, ...options };
  const started = nowMs();
  const notes: string[] = [];

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

  const report = describeSeat(model, mesh, frame, pose, prior, distance, clearance, notes);
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

interface Terms {
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

function energyTerms(
  model: FaceModel, frame: FrameAsset, pose: Pose, distance: MeshDistance,
  clearance: MeshDistance | null, prior: Pose, opt: SeatOptions,
): Terms {
  const cp = emptyClosestPoint();
  const p = v3();
  const samples = frame.padSamples.length / 3;
  const kPerSample = SKIN.stiffnessNPerMm / Math.max(samples, 1);

  let contact = 0;
  for (let i = 0; i < samples; i++) {
    apply(p, pose, frame.padSamples, i * 3);
    if (!distance.query(p[0], p[1], p[2], cp)) continue;
    const penetration = Math.max(0, -cp.distance);
    contact += 0.5 * kPerSample * penetration * penetration;
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
      // The hook: the temple cannot travel forward past the ear.
      const forward = Math.max(0, p[2] - ears[s][2]);
      ear += 0.5 * SKIN.hookStiffnessNPerMm * forward * forward;
    }
  }

  let clear = 0;
  if (clearance) {
    for (const point of clearanceSamples(frame)) {
      apply(p, pose, point, 0);
      if (!clearance.query(p[0], p[1], p[2], cp)) continue;
      if (cp.magnitude > 25) continue; // off the mesh — the sign means nothing
      const penetration = Math.max(0, -cp.distance);
      clear += 0.5 * SKIN.clearanceStiffnessNPerMm * penetration * penetration;
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

function accumulate(
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
    // r = sqrt(k) * (-d);  dr/dx = -sqrt(k) * n^T * dp/dx
    const sk = Math.sqrt(stiffness);
    const r = sk * -cp.distance;
    const row = new Float64Array(6);
    for (let a = 0; a < 6; a++) {
      row[a] = -sk * (cp.nx * J[a] + cp.ny * J[6 + a] + cp.nz * J[12 + a]);
    }
    for (let a = 0; a < 6; a++) {
      g[a] += row[a] * r;
      for (let b = 0; b <= a; b++) H[a * 6 + b] += row[a] * row[b];
    }
  };

  for (let i = 0; i < samples; i++) {
    addOneSided(frame.padSamples, i * 3, distance, kPerSample, 30);
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

      // The hook: d(forward)/dx = +d(z)/dx.
      const forward = p[2] - ears[s][2];
      if (forward > 0) {
        const sk = Math.sqrt(SKIN.hookStiffnessNPerMm);
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
 */
function clearanceSamples(frame: FrameAsset): Float64Array[] {
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
  const make = (eye: number, cheek: number, templeX: number): Vec3 => v3(
    p[templeX * 3],
    p[eye * 3 + 1] + ABOVE_EYE_LINE_MM,
    p[cheek * 3 + 2] - BEHIND_CHEEK_MM,
  );
  return [
    make(LM.EYE_OUTER_R, LM.CHEEK_R, LM.TEMPLE_R),
    make(LM.EYE_OUTER_L, LM.CHEEK_L, LM.TEMPLE_L),
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
      per[side].lift += normalN * Math.max(cp.ny, 0);
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
  const padLoadFraction = weightN > 0 ? clamp(padLift / weightN, 0, 1) : 0;

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
  if (articulation.residualMm > 1.0) {
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
    padSeatErrorMm,
    padWorstGapMm,
    padWorstBuryMm,
    padDepthErrorMm,
    padTiltAdviceDeg: articulation.tiltDeg,
    padSeatErrorArticulatedMm: articulation.residualMm,
    descentMm: prior.t[1] - pose.t[1],
    pantoscopicDeg: (pitch * 180) / Math.PI,
    rollDeg: (roll * 180) / Math.PI,
    vertexDistanceMm: vertex,
    opticalCentreOffsetMm: offset,
    worstClearanceMm: worstClearance,
  };
}

/**
 * How much each pad would have to pivot to lie flat on the skin under it.
 *
 * Fits a plane to the closest surface points beneath each pad's samples and
 * measures the angle to the pad's own plane. The residual after that rotation is
 * what an articulated pad would actually achieve — which separates "this frame
 * does not suit this nose" from "these pads need bending", and only the second
 * of those is fixable by an optician.
 */
function padArticulation(
  model: FaceModel, frame: FrameAsset, pose: Pose, distance: MeshDistance,
): { tiltDeg: [number, number]; residualMm: number } {
  const cp = emptyClosestPoint();
  const p = v3();
  const tilt: [number, number] = [0, 0];
  let residualAcc = 0;
  let residualN = 0;

  for (const [slot, side] of [[0, -1], [1, 1]] as const) {
    // Mean surface normal under this pad, and the pad's own normal in face space.
    let snx = 0, sny = 0, snz = 0;
    let pnx = 0, pny = 0, pnz = 0;
    const local: number[] = [];
    let n = 0;

    for (let i = 0; i < frame.padSamples.length / 3; i++) {
      if (frame.padSide[i] !== side) continue;
      apply(p, pose, frame.padSamples, i * 3);
      if (!distance.query(p[0], p[1], p[2], cp)) continue;
      snx += cp.nx; sny += cp.ny; snz += cp.nz;
      const R = pose.R;
      const nx = frame.padNormals[i * 3], ny = frame.padNormals[i * 3 + 1], nz = frame.padNormals[i * 3 + 2];
      pnx += R[0] * nx + R[1] * ny + R[2] * nz;
      pny += R[3] * nx + R[4] * ny + R[5] * nz;
      pnz += R[6] * nx + R[7] * ny + R[8] * nz;
      local.push(cp.distance);
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
    const mean = local.reduce((a, b) => a + b, 0) / local.length;
    // Remove the linear trend a rigid tilt would take out: approximate by
    // subtracting the best-fit straight line through the samples in order,
    // which for a rectangular sample grid is the dominant mode.
    let num = 0, den = 0;
    for (let k = 0; k < local.length; k++) {
      const x = k - (local.length - 1) / 2;
      num += x * (local[k] - mean);
      den += x * x;
    }
    const slope = den > 0 ? num / den : 0;
    for (let k = 0; k < local.length; k++) {
      const x = k - (local.length - 1) / 2;
      const r = local[k] - mean - slope * x;
      residualAcc += r * r;
      residualN++;
    }
  }

  return {
    tiltDeg: tilt,
    residualMm: residualN ? Math.sqrt(residualAcc / residualN) : 0,
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
