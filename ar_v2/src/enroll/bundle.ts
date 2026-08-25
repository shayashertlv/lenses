/**
 * The enrollment bundle: one joint solve for who this wearer is.
 *
 * This is the file the whole rewrite exists to make possible. v1 had no such
 * solve — it estimated the wearer incrementally, inside the per-frame path,
 * from one view at a time, and every structural problem it had follows from
 * that. Here the wearer is solved once, from many views at once, and the two
 * complaints that motivated v2 fall out as consequences:
 *
 *  - **">40 degrees of yaw pushes the frame forward."** In v1 a large yaw meant
 *    a degraded landmark set *and* a rigid fit that had to absorb shape error
 *    into pose, *and* a trust law that switched off the personalisation at
 *    exactly that pose. Here yaw is the measurement. The bundle wants it: two
 *    views 60 degrees apart triangulate the bridge to a fraction of a
 *    millimetre, and nothing in this file penalises the angle that provides it.
 *
 *  - **"The nose and the glasses interact badly."** In v1 depth was borrowed
 *    from the average head, forever, for structural reasons its own audit
 *    identified: parallax and pose trust are the same angle with opposite
 *    signs, so the estimator could never accumulate what it needed. Here the
 *    protocol asks for the angle, the solve consumes it, and the nose surface
 *    is a free-form field with no average to fall back to.
 *
 * ## The staging, and why it is staged
 *
 * A single monolithic solve over poses, shape, intrinsics *and* ~150 free-form
 * nose displacements has a reduced system of about 170 parameters after Schur.
 * That is solvable, but the displacements and the shape coefficients are
 * strongly coupled through the same residuals, and the resulting Hessian is
 * both dense and badly scaled — LM spends its damping budget deciding whether a
 * millimetre of bridge belongs to `noseBridgeDepth` or to the field beneath it.
 *
 * Alternation removes the coupling from the linear algebra without removing it
 * from the answer:
 *
 *   A. **Global**: poses + shape + intrinsics, with the field held fixed.
 *      Schur-eliminate the per-frame poses; the reduced system is ~20x20.
 *   B. **Field**: the nose displacements, with everything else held fixed.
 *      Linear given frozen normals; one dense solve of ~150x150.
 *   Repeat A/B, refreshing the normals between rounds.
 *
 * **The monolithic comparison has not been run.** This header used to claim
 * that three rounds reach the same optimum as a monolithic solve to within
 * 0.02 mm RMS at a fifth of the cost, and cited a `tests/enroll-bundle.test.ts`
 * that has never existed in this repository. No monolithic solve is
 * implemented, nothing anywhere compares the two, and those two numbers have no
 * provenance — they are removed rather than re-cited.
 *
 * What *is* measured is the alternation on its own terms: the end-to-end
 * accuracy in `tests/pipeline.test.ts` and `reports/enroll.txt` is the
 * alternating solver's, so the shipped numbers are honest about what shipped.
 * They just do not establish that alternation costs nothing against the
 * monolithic solve it replaced — that remains an argument from the conditioning
 * above, which is a good argument and not a result.
 *
 * ## What this solve does NOT estimate, on purpose
 *
 * **Per-landmark detector bias.** The offset between where the detector puts a
 * landmark and where the skin actually is cannot be separated, from that
 * detector's output alone, from the face genuinely being that shape — they are
 * the same observation. It is a property of the *detector*, measurable once
 * against ground-truth scans, not per wearer. v1 estimated it from two faces and
 * shipped it; here it is a calibration asset (`enroll/detector-bias.ts`),
 * defaults to zero, and the harness can measure it because synthetic truth is
 * known. See `docs/OPEN-QUESTIONS.md` Q2.
 *
 * **Expression.** The scan asks for a neutral face over four seconds. Rather
 * than spend per-frame parameters on it, landmarks below the subnasale are
 * down-weighted to near nothing (`landmarkRigidity`) — they are
 * expression-dominated and irrelevant to where a pair of glasses sits.
 */

import {
  type Intrinsics, type IntrinsicsMask, applyIntrinsicsDelta, dProjDIntrinsics,
  dProjDModelPoint, dProjDPose, intrinsicsDof, project,
} from '../core/camera.js';
import {
  type Pose, ldlt, ldltSolve, poseClone, poseOplus, v3,
} from '../core/linalg.js';
import { type RobustLoss, huber } from '../core/robust.js';
import {
  type FaceMesh, type Region, computeVertexNormals, LM,
} from '../core/mesh.js';
import { type ShapeBasis, evaluateBasis } from '../core/shape/basis.js';
import {
  type DisplacementField, type DisplacementPriors, accumulateDisplacementPriors,
  applyDisplacement, DISPLACEMENT_PRIORS, displacementJacobian,
  displacementStats, refreshNormals,
} from '../core/shape/displacement.js';

// ------------------------------------------------------------------- inputs

/** One view handed to the bundle. */
export interface BundleFrame {
  /** Detector landmarks in pixels, 2 per landmark. NaN where absent. */
  landmarks: Float64Array;
  /** Per-landmark one-sigma in pixels. Infinity excludes the landmark. */
  sigmaPx: Float64Array;
  /** Per-landmark visibility 0..1, used only for reporting coverage. */
  visibility: Float64Array;
  /** Silhouette samples in pixels, or null. */
  silhouette: Float64Array | null;
  /** Initial pose estimate, model -> camera. Refined in place. */
  pose: Pose;
  /** Free-text tag, carried through to the report. */
  beat: string;
}

export interface BundleOptions {
  intrinsicsMask: IntrinsicsMask;
  /** Outer A/B rounds. */
  rounds: number;
  /** LM iterations per stage. */
  iterationsGlobal: number;
  iterationsField: number;
  /** Huber threshold in sigmas, for the first round. Tightened each round. */
  huberStart: number;
  huberEnd: number;
  /** Weight of the shape prior, per unit coefficient. */
  shapePrior: number;
  /**
   * Weight on a silhouette residual, in raw pixels.
   *
   * Not "relative to a landmark", which is what this said before and was never
   * true: a landmark residual is whitened by `sqrt(rigidity)/sigmaPx`, so at
   * a typical sigma the two currencies differ by a factor of about three. This
   * is the multiplier applied to the contour's pixel error and nothing more.
   */
  silhouetteWeight: number;
  /** Solve the free-form field at all. Off is how the harness measures what it
   *  is worth, rather than assuming. */
  solveField: boolean;
  /**
   * Multiplier on BOTH `DISPLACEMENT_PRIORS` weights — smoothness and magnitude
   * together — in stage B's normal equations and its line search. 1 is the base
   * derivation in `displacement.ts`; the shipped value is measured, and the
   * sweep that chose it is recorded at `BUNDLE_DEFAULTS.fieldPriorScale`. The
   * two weights have never been swept separately, which is why this is one
   * scalar and not two.
   */
  fieldPriorScale: number;
  useSilhouette: boolean;
  trace?: (message: string) => void;
}

/**
 * ## What these defaults cost, end to end
 *
 * Wearer-facing scan-solve wall time, measured 2026-08-22 on the knee
 * replication campaign (5 seeds x 14 subjects x 3 camera geometries, 42
 * enrollments per cell, across-seed median of per-seed medians; one dev machine
 * under partial load, so the RATIOS are the portable figures):
 *
 *   pre-campaign defaults  keyframes=48, rounds=3, field on:  807 ms (bundle 630)
 *   these defaults         keyframes=24, rounds=3, field on:  491 ms (bundle 331)
 *
 * A 1.64x speedup, and "halving keyframes roughly halves bundle time" is
 * confirmed: 630 -> 331 ms is 0.53x. Those cells ran at `fieldPriorScale` 1;
 * the prior scale changes no system's size, so the milliseconds carry to the
 * shipped x8. Dropping to rounds=1 as well would give 273 ms — 2.96x against
 * the old defaults — and measured BETTER on the pooled paired medians, but it
 * failed its per-seed adoption rule; see `rounds` below.
 */
export const BUNDLE_DEFAULTS: BundleOptions = {
  intrinsicsMask: { f: true, pp: false, k1: false },
  // Kept at 3 by the adoption rule, not because 3 measured best. Replicated
  // 2026-08-22 over 5 seeds x 42 paired enrollments per config: the pooled
  // PAIRED per-run medians favour rounds=1 on every mm metric with the field on
  // (nose -0.071, |protrusion| -0.112, |standoff| -0.063 mm, n=210), but the
  // rule — no per-seed median mm metric worse by more than 0.02 mm, in at
  // least 4 of 5 seeds — passed only 3/5, in BOTH field configurations
  // (field on: |standoff| +0.072 at seed 11, +0.031 at seed 41; field off:
  // seed 37 |protrusion| +0.038 and |standoff| +0.030, seed 53 nose +0.025).
  // The interaction is confirmed: with the field off, every r1-vs-r3 median
  // delta sits within +/-0.04 mm — the rounds exist for the A/B alternation
  // with the field, and collapse naturally without it. If the 2.96x combined
  // speedup of rounds=1 is ever wanted, the per-seed standoff tail is the
  // thing to re-examine.
  rounds: 3,
  iterationsGlobal: 12,
  iterationsField: 8,
  huberStart: 4.0,
  huberEnd: 2.0,
  shapePrior: 1.0,
  // Re-swept after the silhouette was put into stage B (see `solveField`). The
  // old 0.5 was swept against a term that only half the alternation could see,
  // so it was not a measurement of this residual's worth.
  //
  // Swept over 0, 0.25, 0.5, 0.75, 1, 1.5, 2, 3 on two independent blocks of 72
  // enrollments each (6 subjects x 3 camera geometries x 3 capture seeds), error
  // against ground truth as median / p90 / worst mm. The medians are flat inside
  // the noise across the whole range; the sweep is decided on the tail, which is
  // this harness's stated policy. Shipped (term omitted, w=0.5) -> here (w=1):
  //
  //   |protrusion| worst   3.54 -> 2.76   and   3.95 -> 3.07
  //   |standoff|   p90     2.37 -> 1.94   and   2.15 -> 1.63
  //   |standoff|   worst   4.50 -> 3.41   and   2.81 -> 2.52
  //   pad-strip    median  1.60 -> 1.68   and   1.34 -> 1.41   (the price)
  //
  // Past 1 the protrusion tail keeps improving, but both blocks show the cost
  // arriving: the pad strip — the surface the nose pads actually rest on —
  // degrades monotonically, and by w=2 the standoff tail has turned as well
  // (p90 1.66 -> 1.91 on the second block). 1.0 is the last value at which
  // nothing has turned.
  silhouetteWeight: 1.0,
  // The field stays ON, and the claim that it earns its place is now a
  // replicated measurement rather than a single draw — see `fieldPriorScale`
  // for the campaign that settled it.
  solveField: true,
  // 8, adopted by the 2026-08-22 displacement-field campaign: 5 seeds
  // {11,23,37,41,53} x 14 subjects, eye-level, 2268 cells, 0 degraded, in two
  // configs — "shipped" (pooled iris, detector bias on) and "clean" (true
  // iris, bias off). The adoption rule (field-on <= field-off on median nose
  // RMS in >=4/5 seeds in BOTH configs, pad strip better, not merely within
  // 0.05 mm) passed at x1, x2, x4 and x8; x8 is the measured-best qualifying
  // cell. Median-of-seeds nose RMS, mm:
  //
  //   shipped:  off 1.439  x1 1.417  x2 1.393  x4 1.293  x8 1.269  (gain 0.170)
  //   clean:    off 0.884  x1 0.750  x2 0.721  x4 0.688  x8 0.668  (gain 0.216)
  //   pad strip, x8: shipped 1.353 -> 1.030, clean 0.762 -> 0.471 — better, not
  //   merely unhurt. Laptop-lid spot-check at x8: field wins 3/3 seeds in both
  //   configs, pad strip better in all 12 cells.
  //
  // Three honesty notes that must not be lost:
  //  - x8 is the EDGE of the sweep and the curve had not turned there (x4 is
  //    within 0.03 mm in both configs). Anywhere in x2-x8 is supported by the
  //    rule; the optimum may lie beyond x8 and nobody has looked.
  //  - Seed 41 in the shipped config is a genuine losing realisation at every
  //    scale (off 1.439 vs on 1.645 at x8): adoption accepts that roughly one
  //    noise realisation in five still shows a field deficit under pooled-iris
  //    + bias conditions. The previously published "field loses, 1.03 vs 0.83"
  //    figure was a single unseeded draw from exactly that family.
  //  - Why a stronger prior rescues the field (the Q21 separator): scaling the
  //    actual landmark noise while holding the claimed sigma fixed, the
  //    field's paired median deficit shrinks toward zero as noise -> 0
  //    (shipped +0.066 mm at 0.7 px -> +0.016 at 0) or is already a growing
  //    win (clean -0.097 -> -0.125; ultraclean control -0.125), so the field
  //    chases landmark noise and detector bias, NOT a mis-modelled surface —
  //    there is no residual deficit at zero noise for registration or normal
  //    error to hide in.
  //
  // The field is not smoothed into inertness at x8: median solved-field RMS is
  // 0.799 mm (shipped) / 0.711 mm (clean), against 1.048/0.860 at x1.
  fieldPriorScale: 8,
  useSilhouette: true,
};

const silhouetteCache = new WeakMap<BundleState, (SilhouetteIndex | null | undefined)[]>();

/**
 * The observed silhouette of a frame never changes, so its index is built once
 * per state and reused for every iteration of every round.
 */
function silhouetteIndexFor(
  state: BundleState, f: number, frame: BundleFrame,
): SilhouetteIndex | null {
  let list = silhouetteCache.get(state);
  if (!list) {
    list = new Array(state.frames.length).fill(undefined);
    silhouetteCache.set(state, list);
  }
  if (list[f] === undefined) {
    list[f] = frame.silhouette ? buildSilhouetteIndex(frame.silhouette) : null;
  }
  return list[f] ?? null;
}

export interface BundleState {
  mesh: FaceMesh;
  basis: ShapeBasis;
  field: DisplacementField;
  frames: BundleFrame[];
  intrinsics: Intrinsics;
  shapeCoeffs: Float64Array;
  /** Working geometry: basis(shape) + field. Refreshed by `updateGeometry`. */
  positions: Float64Array;
  /** Rigidity weight per mesh landmark, 0..1. */
  rigidity: Float64Array;
  noseRegion: Region;
}

export interface BundleReport {
  rounds: number;
  reprojectionRmsPx: number;
  reprojectionP95Px: number;
  residualsUsed: number;
  silhouetteResiduals: number;
  focalPx: number;
  focalMovedPct: number;
  ms: number;
  perRound: { round: number; rmsPx: number; costGlobal: number; costField: number }[];
  converged: boolean;
  /** Rounds in which the nose field could not be solved. Non-zero means the
   *  reconstruction's nose is the shape basis alone. */
  fieldFailures: number;
  /** RMS of the solved free-form field, mm. Zero on a real face means the
   *  stage did not run — it is not a plausible measurement. */
  fieldRmsMm: number;
  /**
   * The a-posteriori variance factor, `s0^2` — chi-square over redundancy.
   *
   * 1.0 means the detector's claimed per-landmark sigma was right. Above 1 it
   * was optimistic and every covariance derived from it is too tight by the same
   * factor; below 1 it was pessimistic.
   *
   * This exists because `perVertexUncertainty` builds its information matrix as
   * `A = sum w*J^T J / sigma^2` and reports `sqrt(trace(A^-1)/3)` — so the
   * millimetres it returns scale linearly with whatever sigma the detector layer
   * asserted, and nothing in that chain ever compares them to a residual. The
   * number could not notice being wrong. Standard photogrammetric practice is to
   * rescale the formal covariance by this factor, which is what `enroll` now
   * does.
   */
  varianceFactor: number;
}

// ------------------------------------------------------------------ rigidity

/**
 * How much each mesh landmark is allowed to speak, 0..1.
 *
 * Full weight above the subnasale, falling to near nothing below it. That line
 * is not aesthetic: everything below it moves with speech and expression, and
 * nothing below it has any bearing on where a pair of glasses sits. Spending
 * bundle weight on the lower lip buys a worse nose.
 *
 * The nose region gets a further boost, because it is both the smallest target
 * and the one the whole exercise is for.
 */
export function landmarkRigidity(mesh: FaceMesh, noseRegion: Region): Float64Array {
  const out = new Float64Array(mesh.vertexCount);
  const subnasaleY = mesh.positions[LM.SUBNASALE * 3 + 1];
  const eyeY = (mesh.positions[LM.EYE_OUTER_R * 3 + 1] + mesh.positions[LM.EYE_OUTER_L * 3 + 1]) / 2;
  const band = Math.max(eyeY - subnasaleY, 1e-6);

  for (let i = 0; i < mesh.vertexCount; i++) {
    const y = mesh.positions[i * 3 + 1];
    // Smooth, because a hard line puts a discontinuity in the weighting exactly
    // across the alae — half the nostril rim in, half out.
    const t = Math.min(Math.max((y - (subnasaleY - band * 0.35)) / (band * 0.5), 0), 1);
    const base = 0.12 + 0.88 * (t * t * (3 - 2 * t));
    out[i] = base * (1 + 0.8 * noseRegion.weight[i]);
  }
  return out;
}

// --------------------------------------------------------------------- solve

export function createBundleState(
  mesh: FaceMesh, basis: ShapeBasis, field: DisplacementField, noseRegion: Region,
  frames: BundleFrame[], intrinsics: Intrinsics,
): BundleState {
  const positions = new Float64Array(mesh.vertexCount * 3);
  const state: BundleState = {
    mesh, basis, field, frames, noseRegion,
    intrinsics: { ...intrinsics },
    shapeCoeffs: new Float64Array(basis.dim),
    positions,
    rigidity: landmarkRigidity(mesh, noseRegion),
  };
  updateGeometry(state);
  refreshNormals(field, mesh, state.positions);
  return state;
}

/** Rebuilds `positions` from the current shape coefficients and field. */
export function updateGeometry(state: BundleState): void {
  evaluateBasis(state.basis, state.shapeCoeffs, state.positions);
  applyDisplacement(state.field, state.positions);
}

export function runBundle(
  state: BundleState, options: Partial<BundleOptions> = {},
): BundleReport {
  const opt = { ...BUNDLE_DEFAULTS, ...options };
  const started = nowMs();
  fieldFactorisationFailures = 0;
  const perRound: BundleReport['perRound'] = [];
  const focal0 = state.intrinsics.f;
  let converged = true;

  for (let round = 0; round < opt.rounds; round++) {
    const t = opt.rounds === 1 ? 1 : round / (opt.rounds - 1);
    const loss = huber(opt.huberStart + (opt.huberEnd - opt.huberStart) * t);

    const costGlobal = solveGlobal(state, opt, loss);
    let costField = 0;
    if (opt.solveField) {
      refreshNormals(state.field, state.mesh, basisOnly(state));
      // Hygiene, and labelled as hygiene. `refreshNormals` changes the
      // directions the field rides on; without this line `solveField` builds its
      // residuals on the *previous* round's geometry while its jacobian already
      // uses the new normals.
      //
      // What that is worth, measured as a paired comparison over 36 enrollments
      // (12 subjects x 3 camera geometries, with and without this one line):
      // nose RMS moves by -0.0006 mm on the mean, and 14 of the 36 get WORSE.
      // That is not an accuracy fix and must not be sold as one. Round 0 is
      // exactly a no-op — the field is all zeros and `applyDisplacement`
      // early-returns — and the discrepancy shrinks as stage A converges, so
      // there was never much of it to recover. It is here because "the residual
      // and the jacobian describe the same state" is a property worth having
      // for free, not because it buys millimetres.
      updateGeometry(state);
      costField = solveField(state, opt, loss);
    }
    updateGeometry(state);

    const stats = reprojectionStats(state);
    perRound.push({ round, rmsPx: stats.rms, costGlobal, costField });
    opt.trace?.(
      `round ${round}: rms ${stats.rms.toFixed(3)} px, ` +
      `f ${state.intrinsics.f.toFixed(1)} px, cost ${costGlobal.toFixed(1)}/${costField.toFixed(1)}`,
    );
    if (!Number.isFinite(stats.rms)) { converged = false; break; }
  }

  const stats = reprojectionStats(state);
  return {
    rounds: opt.rounds,
    reprojectionRmsPx: stats.rms,
    reprojectionP95Px: stats.p95,
    residualsUsed: stats.count,
    silhouetteResiduals: opt.useSilhouette ? countSilhouette(state) : 0,
    focalPx: state.intrinsics.f,
    focalMovedPct: ((state.intrinsics.f - focal0) / focal0) * 100,
    ms: nowMs() - started,
    perRound,
    converged,
    fieldFailures: fieldFactorisationFailures,
    fieldRmsMm: displacementStats(state.field).rmsMm,
    // Redundancy: two residual components per landmark observation, less the
    // parameters the solve was free to move — six per frame pose, the shape
    // coefficients, and whichever intrinsics were unlocked.
    varianceFactor: (() => {
      const params = 6 * state.frames.length + state.basis.dim
        + intrinsicsDof(opt.intrinsicsMask);
      const redundancy = 2 * stats.count - params;
      return redundancy > 0 && Number.isFinite(stats.chiSquare)
        ? stats.chiSquare / redundancy
        : 1;
    })(),
  };
}

/** The geometry from the basis alone — the surface the field's normals ride on. */
function basisOnly(state: BundleState): Float64Array {
  const p = new Float64Array(state.positions.length);
  evaluateBasis(state.basis, state.shapeCoeffs, p);
  return p;
}

// ------------------------------------------------------- stage A: the globals

/**
 * Poses + shape + intrinsics, with the per-frame poses eliminated by Schur
 * complement.
 *
 * The structure, once, because it is the only non-obvious linear algebra here:
 *
 *     [ Hpp  Hpg ] [dp]   [ -gp ]
 *     [ Hgp  Hgg ] [dg] = [ -gg ]
 *
 * `Hpp` is block-diagonal — 6x6 per frame, because no residual touches two
 * poses. Eliminating it gives the reduced system
 *
 *     (Hgg - sum_f Hgp_f Hpp_f^-1 Hpg_f) dg = -gg + sum_f Hgp_f Hpp_f^-1 gp_f
 *
 * which is ~20x20 whatever the frame count, and then each `dp_f` back-solves
 * independently. This is what makes the frame count essentially free, which in
 * turn is what makes it reasonable to ask for a hundred-frame scan.
 */
function solveGlobal(state: BundleState, opt: BundleOptions, loss: RobustLoss): number {
  const G = state.basis.dim + intrinsicsDof(opt.intrinsicsMask);
  const F = state.frames.length;

  const Hgg = new Float64Array(G * G);
  const gg = new Float64Array(G);
  const Hpp = new Float64Array(F * 36);
  const gp = new Float64Array(F * 6);
  const Hpg = new Float64Array(F * 6 * G);

  let lambda = 1e-3;
  let cost = Infinity;

  for (let iter = 0; iter < opt.iterationsGlobal; iter++) {
    Hgg.fill(0); gg.fill(0); Hpp.fill(0); gp.fill(0); Hpg.fill(0);
    const c0 = accumulateGlobal(state, opt, loss, Hgg, gg, Hpp, gp, Hpg, G);
    if (iter === 0) cost = c0;

    // Schur: fold each frame's block into the reduced system.
    const reduced = new Float64Array(Hgg);
    const rhs = new Float64Array(G);
    for (let i = 0; i < G; i++) rhs[i] = -gg[i];
    // Mirror Hgg's lower triangle up — the Schur update is not triangular.
    symmetrizeInPlace(reduced, G);

    const blockInv = new Float64Array(F * 36);
    const ok = new Uint8Array(F);
    const work = new Float64Array(36);
    const col = new Float64Array(6);

    for (let f = 0; f < F; f++) {
      work.set(Hpp.subarray(f * 36, f * 36 + 36));
      symmetrizeInPlace(work, 6);
      for (let d = 0; d < 6; d++) work[d * 6 + d] *= 1 + lambda;
      if (!ldlt(work, 6)) continue;
      ok[f] = 1;
      for (let c = 0; c < 6; c++) {
        col.fill(0); col[c] = 1;
        ldltSolve(work, 6, col);
        for (let r = 0; r < 6; r++) blockInv[f * 36 + r * 6 + c] = col[r];
      }

      // reduced -= Hgp_f * Hpp_f^-1 * Hpg_f   (Hgp = Hpg^T)
      // rhs    += Hgp_f * Hpp_f^-1 * gp_f
      const P = Hpg.subarray(f * 6 * G, (f + 1) * 6 * G); // 6 x G
      const Bi = blockInv.subarray(f * 36, (f + 1) * 36);
      const tmp = new Float64Array(6 * G); // Hpp^-1 * Hpg
      for (let r = 0; r < 6; r++) {
        for (let c = 0; c < G; c++) {
          let s = 0;
          for (let k = 0; k < 6; k++) s += Bi[r * 6 + k] * P[k * G + c];
          tmp[r * G + c] = s;
        }
      }
      for (let a = 0; a < G; a++) {
        for (let b = 0; b < G; b++) {
          let s = 0;
          for (let k = 0; k < 6; k++) s += P[k * G + a] * tmp[k * G + b];
          reduced[a * G + b] -= s;
        }
        let s = 0;
        for (let r = 0; r < 6; r++) {
          let t = 0;
          for (let k = 0; k < 6; k++) t += Bi[r * 6 + k] * gp[f * 6 + k];
          s += P[r * G + a] * t;
        }
        rhs[a] += s;
      }
    }

    for (let d = 0; d < G; d++) reduced[d * G + d] *= 1 + lambda;
    const dg = new Float64Array(rhs);
    if (!ldlt(reduced, G)) { lambda *= 10; if (lambda > 1e10) break; continue; }
    ldltSolve(reduced, G, dg);

    // Back-substitute the poses: dp_f = Hpp^-1 (-gp_f - Hpg_f dg)
    const dp = new Float64Array(F * 6);
    for (let f = 0; f < F; f++) {
      if (!ok[f]) continue;
      const P = Hpg.subarray(f * 6 * G, (f + 1) * 6 * G);
      const Bi = blockInv.subarray(f * 36, (f + 1) * 36);
      const rhsF = new Float64Array(6);
      for (let r = 0; r < 6; r++) {
        let s = -gp[f * 6 + r];
        for (let c = 0; c < G; c++) s -= P[r * G + c] * dg[c];
        rhsF[r] = s;
      }
      for (let r = 0; r < 6; r++) {
        let s = 0;
        for (let k = 0; k < 6; k++) s += Bi[r * 6 + k] * rhsF[k];
        dp[f * 6 + r] = s;
      }
    }

    // Trial step.
    const saved = snapshot(state);
    applyGlobalStep(state, opt, dg, dp);
    updateGeometry(state);
    const c1 = costGlobal(state, opt, loss);

    if (c1 < cost) {
      cost = c1;
      lambda = Math.max(lambda * 0.4, 1e-9);
    } else {
      restore(state, saved);
      updateGeometry(state);
      lambda *= 8;
      if (lambda > 1e10) break;
    }
  }

  return cost;
}

function accumulateGlobal(
  state: BundleState, opt: BundleOptions, loss: RobustLoss,
  Hgg: Float64Array, gg: Float64Array,
  Hpp: Float64Array, gp: Float64Array, Hpg: Float64Array, G: number,
): number {
  const { mesh, basis, frames, positions, rigidity } = state;
  const S = basis.dim;
  const kDof = intrinsicsDof(opt.intrinsicsMask);

  const cam = v3();
  const rot = v3();
  const uv = new Float64Array(2);
  const Jpose = new Float64Array(12);   // 2x6
  const Jpoint = new Float64Array(6);   // 2x3
  const Jk = new Float64Array(2 * Math.max(kDof, 1));
  const Jshape = new Float64Array(2 * S);
  const r = new Float64Array(2);

  let cost = 0;
  const normals = opt.useSilhouette ? currentNormals(state) : null;

  for (let f = 0; f < frames.length; f++) {
    const frame = frames[f];
    const pose = frame.pose;
    const R = pose.R;
    const Hp = Hpp.subarray(f * 36, (f + 1) * 36);
    const gpf = gp.subarray(f * 6, (f + 1) * 6);
    const Pg = Hpg.subarray(f * 6 * G, (f + 1) * 6 * G);

    for (let i = 0; i < mesh.vertexCount; i++) {
      const sigma = frame.sigmaPx[i];
      if (!(sigma > 0 && sigma < 1e6)) continue;
      const ox = frame.landmarks[i * 2];
      if (Number.isNaN(ox)) continue;
      const oy = frame.landmarks[i * 2 + 1];

      const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
      rot[0] = R[0] * x + R[1] * y + R[2] * z;
      rot[1] = R[3] * x + R[4] * y + R[5] * z;
      rot[2] = R[6] * x + R[7] * y + R[8] * z;
      cam[0] = rot[0] + pose.t[0];
      cam[1] = rot[1] + pose.t[1];
      cam[2] = rot[2] + pose.t[2];
      if (!project(uv, state.intrinsics, cam)) continue;

      // Whitened by the detector's own sigma and by rigidity, so `loss` sees
      // sigmas and one Huber threshold means the same thing everywhere.
      const w = Math.sqrt(rigidity[i]) / sigma;
      r[0] = (uv[0] - ox) * w;
      r[1] = (uv[1] - oy) * w;
      const s = r[0] * r[0] + r[1] * r[1];
      const [rho, drho] = loss.eval(s);
      cost += rho;

      dProjDPose(Jpose, 0, state.intrinsics, cam, rot);
      dProjDModelPoint(Jpoint, 0, state.intrinsics, cam, R);
      for (let a = 0; a < 12; a++) Jpose[a] *= w;

      // Shape jacobian: d(px)/d(model point) * d(model point)/d(coeff)
      for (let k = 0; k < S; k++) {
        const mk = basis.modes[k];
        const mx = mk[i * 3], my = mk[i * 3 + 1], mz = mk[i * 3 + 2];
        Jshape[k] = (Jpoint[0] * mx + Jpoint[1] * my + Jpoint[2] * mz) * w;
        Jshape[S + k] = (Jpoint[3] * mx + Jpoint[4] * my + Jpoint[5] * mz) * w;
      }
      if (kDof > 0) {
        dProjDIntrinsics(Jk, 0, kDof, state.intrinsics, cam, opt.intrinsicsMask);
        for (let a = 0; a < 2 * kDof; a++) Jk[a] *= w;
      }

      // Pose block.
      for (let a = 0; a < 6; a++) {
        gpf[a] += drho * (Jpose[a] * r[0] + Jpose[6 + a] * r[1]);
        for (let b = 0; b <= a; b++) {
          Hp[a * 6 + b] += drho * (Jpose[a] * Jpose[b] + Jpose[6 + a] * Jpose[6 + b]);
        }
      }

      // Global block and the cross term.
      for (let a = 0; a < G; a++) {
        const ja0 = a < S ? Jshape[a] : Jk[a - S];
        const ja1 = a < S ? Jshape[S + a] : Jk[kDof + (a - S)];
        gg[a] += drho * (ja0 * r[0] + ja1 * r[1]);
        for (let b = 0; b <= a; b++) {
          const jb0 = b < S ? Jshape[b] : Jk[b - S];
          const jb1 = b < S ? Jshape[S + b] : Jk[kDof + (b - S)];
          Hgg[a * G + b] += drho * (ja0 * jb0 + ja1 * jb1);
        }
        for (let p = 0; p < 6; p++) {
          Pg[p * G + a] += drho * (Jpose[p] * ja0 + Jpose[6 + p] * ja1);
        }
      }
    }

    if (opt.useSilhouette && frame.silhouette && frame.silhouette.length > 0 && normals) {
      const index = silhouetteIndexFor(state, f, frame);
      if (index) {
        cost += accumulateSilhouette(
          state, opt, loss, frame, normals, index, Hgg, gg, Hp, gpf, Pg, G, S, kDof,
        );
      }
    }
  }

  // Shape prior. Not robustified: it is a prior, not a measurement, and a
  // robust loss on a prior means "stop believing my own regularisation once it
  // disagrees", which is exactly backwards.
  for (let k = 0; k < basis.dim; k++) {
    const w = opt.shapePrior / basis.sigma[k];
    const rr = state.shapeCoeffs[k] * w;
    cost += rr * rr;
    gg[k] += w * rr;
    Hgg[k * G + k] += w * w;
  }

  return cost;
}

/**
 * The silhouette residual: push the model's occluding contour onto the image's.
 *
 * This is the term that recovers nose protrusion, and it is why the protocol
 * has a profile beat. At 80 degrees of yaw the nose is a *shape against the
 * background* — its outline is a direct, high-signal-to-noise measurement of the
 * one quantity a frontal view cannot see. No amount of frontal parallax
 * substitutes for it.
 *
 * Implemented as point-to-nearest-observed-contour, which is the cheap
 * approximation: the exact residual differentiates the contour generator (the
 * set of vertices where the surface normal is perpendicular to the view ray),
 * and moving a vertex changes *which* vertices are on the contour. Ignoring
 * that makes the jacobian correct for the position of a contour vertex and
 * silent about its membership. In practice the membership term matters at the
 * contour's own resolution — sub-pixel — and the approximation is standard.
 *
 * "Sub-pixel" is the textbook argument for the approximation, not a number
 * measured here; the alternative (differentiating the contour generator) was
 * never implemented, so there is nothing to compare against. There is no
 * decision record for it either — an earlier version of this comment cited a
 * `docs/DECISIONS.md` D7 that has never existed in this repository.
 */
function accumulateSilhouette(
  state: BundleState, opt: BundleOptions, loss: RobustLoss,
  frame: BundleFrame, normals: Float64Array, index: SilhouetteIndex,
  Hgg: Float64Array, gg: Float64Array, Hp: Float64Array, gpf: Float64Array,
  Pg: Float64Array, G: number, S: number, kDof: number,
): number {
  const contour = contourVertices(state, frame, normals);
  if (contour.length === 0) return 0;
  const cam = v3(), rot = v3();
  const uv = new Float64Array(2);
  const Jpose = new Float64Array(12);
  const Jpoint = new Float64Array(6);
  const Jk = new Float64Array(2 * Math.max(kDof, 1));
  const Jshape = new Float64Array(2 * S);
  const r = new Float64Array(2);
  const R = frame.pose.R;
  let cost = 0;

  // There used to be a `sigma` here, written `Math.max(1.0, 1.0)` under a
  // comment promising "one buffer pixel converted back to image pixels". It was
  // deleted rather than implemented, and the reason is a measurement, not
  // tidiness:
  //
  //   - The conversion is not available in this file to begin with.
  //     `BundleFrame` carries no raster scale and bundle.ts imports nothing from
  //     `raster.ts`; the silhouette arrives already in image pixels.
  //   - Implementing it would delete the term. The real raster is 192 px wide
  //     (`CAPTURE_DEFAULTS.rasterWidth`) against a 1280 px image, so one buffer
  //     pixel is ~6.7 image pixels and the effective weight falls from 1.0 to
  //     0.15. Measured over 72 enrollments, w=0.15 against w=0: nose RMS
  //     1.763 vs 1.767, |protrusion| 0.836 vs 0.849, |standoff| 0.955 vs 0.974
  //     mm — that is the `no-silhouette` ablation, reproduced. It would erase
  //     the residual the profile beat exists to feed.
  //
  // So the weight is `opt.silhouetteWeight`, full stop, which also makes this
  // path and `costSilhouetteOnly` identical by construction rather than by the
  // coincidence that sigma happened to be 1. They straddle the LM accept test;
  // they have to agree.
  for (const i of contour) {
    const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
    rot[0] = R[0] * x + R[1] * y + R[2] * z;
    rot[1] = R[3] * x + R[4] * y + R[5] * z;
    rot[2] = R[6] * x + R[7] * y + R[8] * z;
    cam[0] = rot[0] + frame.pose.t[0];
    cam[1] = rot[1] + frame.pose.t[1];
    cam[2] = rot[2] + frame.pose.t[2];
    if (!project(uv, state.intrinsics, cam)) continue;

    // Nearest observed contour sample. A model contour point further than
    // `SILHOUETTE_MATCH_PX` from any observed one is not a correspondence, it is
    // a different part of the face — refused outright, which is one of the two
    // places in this tree where a hard threshold is still correct (see
    // `robust.ts`).
    const match = nearestSilhouette(index, uv[0], uv[1]);
    if (!match) continue;

    const w = opt.silhouetteWeight;
    r[0] = (uv[0] - match.x) * w;
    r[1] = (uv[1] - match.y) * w;
    const sq = r[0] * r[0] + r[1] * r[1];
    const [rho, drho] = loss.eval(sq);
    cost += rho;

    dProjDPose(Jpose, 0, state.intrinsics, cam, rot);
    dProjDModelPoint(Jpoint, 0, state.intrinsics, cam, R);
    for (let a = 0; a < 12; a++) Jpose[a] *= w;
    for (let k = 0; k < S; k++) {
      const mk = state.basis.modes[k];
      const mx = mk[i * 3], my = mk[i * 3 + 1], mz = mk[i * 3 + 2];
      Jshape[k] = (Jpoint[0] * mx + Jpoint[1] * my + Jpoint[2] * mz) * w;
      Jshape[S + k] = (Jpoint[3] * mx + Jpoint[4] * my + Jpoint[5] * mz) * w;
    }
    if (kDof > 0) {
      dProjDIntrinsics(Jk, 0, kDof, state.intrinsics, cam, opt.intrinsicsMask);
      for (let a = 0; a < 2 * kDof; a++) Jk[a] *= w;
    }

    for (let a = 0; a < 6; a++) {
      gpf[a] += drho * (Jpose[a] * r[0] + Jpose[6 + a] * r[1]);
      for (let b = 0; b <= a; b++) {
        Hp[a * 6 + b] += drho * (Jpose[a] * Jpose[b] + Jpose[6 + a] * Jpose[6 + b]);
      }
    }
    for (let a = 0; a < G; a++) {
      const ja0 = a < S ? Jshape[a] : Jk[a - S];
      const ja1 = a < S ? Jshape[S + a] : Jk[kDof + (a - S)];
      gg[a] += drho * (ja0 * r[0] + ja1 * r[1]);
      for (let b = 0; b <= a; b++) {
        const jb0 = b < S ? Jshape[b] : Jk[b - S];
        const jb1 = b < S ? Jshape[S + b] : Jk[kDof + (b - S)];
        Hgg[a * G + b] += drho * (ja0 * jb0 + ja1 * jb1);
      }
      for (let p = 0; p < 6; p++) {
        Pg[p * G + a] += drho * (Jpose[p] * ja0 + Jpose[6 + p] * ja1);
      }
    }
  }
  return cost;
}

/**
 * Which mesh vertices are on the occluding contour for this pose.
 *
 * A vertex is on the contour when its own normal is close to perpendicular to
 * the view ray *and* it is on the nose or the face silhouette. The nose
 * restriction is what makes this useful rather than merely expensive: the
 * whole-face silhouette at profile is mostly hair, jaw and ear — none of which
 * this mesh models — so matching against it would drag the solve toward
 * geometry that does not exist.
 */
function contourVertices(
  state: BundleState, frame: BundleFrame, normals: Float64Array,
): number[] {
  const R = frame.pose.R;
  const out: number[] = [];
  const t = frame.pose.t;

  for (const i of state.noseRegion.members) {
    if (state.noseRegion.weight[i] < 0.5) continue;
    const nx = normals[i * 3], ny = normals[i * 3 + 1], nz = normals[i * 3 + 2];
    const cnx = R[0] * nx + R[1] * ny + R[2] * nz;
    const cny = R[3] * nx + R[4] * ny + R[5] * nz;
    const cnz = R[6] * nx + R[7] * ny + R[8] * nz;
    const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
    const vx = R[0] * x + R[1] * y + R[2] * z + t[0];
    const vy = R[3] * x + R[4] * y + R[5] * z + t[1];
    const vz = R[6] * x + R[7] * y + R[8] * z + t[2];
    const l = Math.hypot(vx, vy, vz);
    if (l < 1e-6) continue;
    const dot = (cnx * vx + cny * vy + cnz * vz) / l;
    if (Math.abs(dot) < 0.25) out.push(i);
  }
  return out;
}

function countSilhouette(state: BundleState): number {
  const normals = currentNormals(state);
  let n = 0;
  for (const f of state.frames) {
    if (f.silhouette) n += contourVertices(state, f, normals).length;
  }
  return n;
}

/** Vertex normals for the current geometry, computed once per pass. */
function currentNormals(state: BundleState): Float64Array {
  return computeVertexNormals(state.positions, state.mesh.indices, state.mesh.vertexCount);
}

/**
 * A uniform grid over the observed silhouette samples.
 *
 * The nearest-observed-contour lookup was a linear scan: ~40 model contour
 * points against ~500 observed samples, per frame, per LM iteration, per round —
 * about 34 million distance evaluations for one scan, and comfortably the
 * dominant cost of the whole solve. A grid at the match radius turns each lookup
 * into a nine-cell visit. Same answer, and the harness pins that it is the same
 * answer rather than trusting it.
 */
interface SilhouetteIndex {
  cell: number;
  minX: number;
  minY: number;
  cols: number;
  rows: number;
  buckets: Int32Array[];
  points: Float64Array;
}

const SILHOUETTE_MATCH_PX = 20;

function buildSilhouetteIndex(points: Float64Array): SilhouetteIndex | null {
  if (points.length < 2) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < points.length; i += 2) {
    if (points[i] < minX) minX = points[i];
    if (points[i] > maxX) maxX = points[i];
    if (points[i + 1] < minY) minY = points[i + 1];
    if (points[i + 1] > maxY) maxY = points[i + 1];
  }
  const cell = SILHOUETTE_MATCH_PX;
  const cols = Math.max(1, Math.ceil((maxX - minX) / cell) + 1);
  const rows = Math.max(1, Math.ceil((maxY - minY) / cell) + 1);
  const lists: number[][] = Array.from({ length: cols * rows }, () => []);
  for (let i = 0; i < points.length; i += 2) {
    const cx = Math.min(cols - 1, Math.max(0, Math.floor((points[i] - minX) / cell)));
    const cy = Math.min(rows - 1, Math.max(0, Math.floor((points[i + 1] - minY) / cell)));
    lists[cy * cols + cx].push(i);
  }
  return {
    cell, minX, minY, cols, rows,
    buckets: lists.map((l) => Int32Array.from(l)),
    points,
  };
}

/** Nearest observed sample within `SILHOUETTE_MATCH_PX`, or null. */
function nearestSilhouette(
  index: SilhouetteIndex, x: number, y: number,
): { x: number; y: number; d2: number } | null {
  const cx = Math.floor((x - index.minX) / index.cell);
  const cy = Math.floor((y - index.minY) / index.cell);
  let best = SILHOUETTE_MATCH_PX * SILHOUETTE_MATCH_PX;
  let bx = 0, by = 0, found = false;
  for (let dy = -1; dy <= 1; dy++) {
    const gy = cy + dy;
    if (gy < 0 || gy >= index.rows) continue;
    for (let dx = -1; dx <= 1; dx++) {
      const gx = cx + dx;
      if (gx < 0 || gx >= index.cols) continue;
      for (const i of index.buckets[gy * index.cols + gx]) {
        const ex = index.points[i] - x, ey = index.points[i + 1] - y;
        const d2 = ex * ex + ey * ey;
        if (d2 < best) { best = d2; bx = index.points[i]; by = index.points[i + 1]; found = true; }
      }
    }
  }
  return found ? { x: bx, y: by, d2: best } : null;
}

// -------------------------------------------------------- stage B: the field

/** Counts rounds in which the field stage gave up. Surfaced in `BundleReport`. */
let fieldFactorisationFailures = 0;

/**
 * The free-form nose displacements, with poses, shape and intrinsics fixed.
 *
 * With the normals frozen this is a linear least-squares problem in the field's
 * own values, so it is one accumulation and one dense solve — no LM loop is
 * needed and none is used. The outer round recovers the non-linearity.
 *
 * ## The silhouette belongs here too, and for a while it did not
 *
 * Stage A's `costGlobal` carries the silhouette term; this stage carried it in
 * neither its normal equations nor its line search. That is not a missing
 * refinement — it means the alternation was minimising two different
 * objectives, and stage B's accepted step then *raised* the joint cost on 18 of
 * 108 measured steps (17 of 108 on a second population). With the term in, that
 * count is 0 of 108 on both. The term is a direct function of this stage's own
 * parameters: `contourVertices` iterates `state.noseRegion.members`, and
 * `field.vertices` IS that array — the same object — so every contour vertex
 * has a slot here.
 *
 * No wearer was affected, because both production call sites hard-code
 * `silhouette: null` and only the harness ever supplies one. The harness is,
 * however, exactly where `silhouetteWeight` was swept and where the
 * `no-silhouette` ablation concluded the profile beat was nearly worthless — so
 * that verdict was partly self-inflicted.
 */
function solveField(state: BundleState, opt: BundleOptions, loss: RobustLoss): number {
  const field = state.field;
  const N = field.dim;
  const H = new Float64Array(N * N);
  const g = new Float64Array(N);

  const map = new Int32Array(N);
  for (let s = 0; s < N; s++) map[s] = s;

  const cam = v3();
  const uv = new Float64Array(2);
  const Jpoint = new Float64Array(6);
  const dP = new Float64Array(3);
  const J = new Float64Array(2);
  const r = new Float64Array(2);
  let cost = 0;

  for (const frame of state.frames) {
    const R = frame.pose.R;
    for (let s = 0; s < N; s++) {
      const i = field.vertices[s];
      const sigma = frame.sigmaPx[i];
      if (!(sigma > 0 && sigma < 1e6)) continue;
      const ox = frame.landmarks[i * 2];
      if (Number.isNaN(ox)) continue;
      const oy = frame.landmarks[i * 2 + 1];

      const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + frame.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + frame.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + frame.pose.t[2];
      if (!project(uv, state.intrinsics, cam)) continue;

      const w = Math.sqrt(state.rigidity[i]) / sigma;
      r[0] = (uv[0] - ox) * w;
      r[1] = (uv[1] - oy) * w;
      const sq = r[0] * r[0] + r[1] * r[1];
      const [rho, drho] = loss.eval(sq);
      cost += rho;

      dProjDModelPoint(Jpoint, 0, state.intrinsics, cam, R);
      displacementJacobian(field, s, dP, 0);
      J[0] = (Jpoint[0] * dP[0] + Jpoint[1] * dP[1] + Jpoint[2] * dP[2]) * w;
      J[1] = (Jpoint[3] * dP[0] + Jpoint[4] * dP[1] + Jpoint[5] * dP[2]) * w;

      g[s] += drho * (J[0] * r[0] + J[1] * r[1]);
      H[s * N + s] += drho * (J[0] * J[0] + J[1] * J[1]);
    }
  }

  // The silhouette, into the same system. `H` stays DIAGONAL: a displacement
  // moves its own vertex and nothing else, so a contour residual touches exactly
  // one slot — structurally identical to the landmark term above, and it costs
  // the ~150x150 dense solve nothing. The whole of this change — this block, the
  // matching term in `costField`, and the extra `costField` at the end — is
  // 1-3% on `solveMs`, measured back-to-back against the same build with it
  // removed. It is not a latency question.
  if (opt.useSilhouette) {
    const normals = currentNormals(state);
    for (let f = 0; f < state.frames.length; f++) {
      const frame = state.frames[f];
      if (!frame.silhouette || frame.silhouette.length === 0) continue;
      const index = silhouetteIndexFor(state, f, frame);
      if (!index) continue;
      const R = frame.pose.R;
      for (const i of contourVertices(state, frame, normals)) {
        const s = field.slotOf[i];
        if (s < 0) continue;
        const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
        cam[0] = R[0] * x + R[1] * y + R[2] * z + frame.pose.t[0];
        cam[1] = R[3] * x + R[4] * y + R[5] * z + frame.pose.t[1];
        cam[2] = R[6] * x + R[7] * y + R[8] * z + frame.pose.t[2];
        if (!project(uv, state.intrinsics, cam)) continue;
        const match = nearestSilhouette(index, uv[0], uv[1]);
        if (!match) continue;

        const w = opt.silhouetteWeight;
        r[0] = (uv[0] - match.x) * w;
        r[1] = (uv[1] - match.y) * w;
        const sq = r[0] * r[0] + r[1] * r[1];
        const [rho, drho] = loss.eval(sq);
        cost += rho;

        dProjDModelPoint(Jpoint, 0, state.intrinsics, cam, R);
        displacementJacobian(field, s, dP, 0);
        J[0] = (Jpoint[0] * dP[0] + Jpoint[1] * dP[1] + Jpoint[2] * dP[2]) * w;
        J[1] = (Jpoint[3] * dP[0] + Jpoint[4] * dP[1] + Jpoint[5] * dP[2]) * w;

        g[s] += drho * (J[0] * r[0] + J[1] * r[1]);
        H[s * N + s] += drho * (J[0] * J[0] + J[1] * J[1]);
      }
    }
  }

  cost += accumulateDisplacementPriors(field, scaledPriors(opt), H, g, N, map);

  // A slot no frame observed has only its priors, which are enough to make the
  // system solvable but not enough to make its value meaningful. The Laplacian
  // will fill it from its neighbours, which is the right answer and is why the
  // prior is not optional.
  for (let s = 0; s < N; s++) H[s * N + s] += 1e-6;

  const dx = new Float64Array(N);
  for (let s = 0; s < N; s++) dx[s] = -g[s];
  symmetrizeInPlace(H, N);
  if (!ldlt(H, N)) {
    // Loudly, not silently. A field that cannot be factorised is a field that
    // does not move, and a bundle that quietly skips its own nose stage looks
    // exactly like one that ran it — which is how this went unnoticed for a
    // whole build. See `accumulateDisplacementPriors`.
    fieldFactorisationFailures++;
    console.warn(
      'displacement field: normal equations not positive definite — ' +
      'the nose surface was NOT solved this round',
    );
    return cost;
  }
  ldltSolve(H, N, dx);

  // A single Gauss-Newton step, damped by a line search over three lengths.
  // A full LM here buys nothing measurable — the problem is linear in the
  // frozen normals — but a step that overshoots on a bad round would take the
  // next round's normals with it.
  let bestAlpha = 0, bestCost = cost;
  const base = new Float64Array(field.values);
  for (const alpha of [1, 0.5, 0.25]) {
    for (let s = 0; s < N; s++) field.values[s] = base[s] + alpha * dx[s];
    updateGeometry(state);
    const c = costField(state, opt, loss);
    if (c < bestCost) { bestCost = c; bestAlpha = alpha; break; }
  }
  for (let s = 0; s < N; s++) field.values[s] = base[s] + bestAlpha * dx[s];
  updateGeometry(state);

  // Recomputed rather than returning `bestCost`. When every trial length is
  // rejected — 24 of 216 measured line searches — `bestAlpha` stays 0 and
  // `bestCost` is the *accumulation's* running total, a separately maintained
  // sum that equals `costField` only because both were written to cover the same
  // terms. On this code they do agree: 1.9e-10 absolute, 4.4e-15 relative, which
  // is summation order and nothing else. So this is not a bug fix, it is the
  // removal of a coincidence — the moment a term goes into one of the two and
  // not the other, the reported `costField` stops describing the state actually
  // returned, and it stops silently. That has already happened once, with the
  // silhouette. One extra sweep over the nose vertices per round is the price.
  return costField(state, opt, loss);
}

// -------------------------------------------------------------- cost helpers

function costGlobal(state: BundleState, opt: BundleOptions, loss: RobustLoss): number {
  let cost = costLandmarks(state, loss, false);
  if (opt.useSilhouette) cost += costSilhouetteOnly(state, opt, loss);
  for (let k = 0; k < state.basis.dim; k++) {
    const w = opt.shapePrior / state.basis.sigma[k];
    const rr = state.shapeCoeffs[k] * w;
    cost += rr * rr;
  }
  return cost;
}

/**
 * The joint cost stage B's line search is allowed to see.
 *
 * It has to contain every term stage B's normal equations contain, or the
 * accept test rejects steps the solve believes in and accepts steps it does not.
 * The silhouette is one of those terms — see `solveField`.
 */
function costField(state: BundleState, opt: BundleOptions, loss: RobustLoss): number {
  let cost = costLandmarks(state, loss, true);
  if (opt.useSilhouette) cost += costSilhouetteOnly(state, opt, loss);
  const field = state.field;
  const priors = scaledPriors(opt);
  for (let s = 0; s < field.dim; s++) {
    const a0 = field.neighbourStart[s], a1 = field.neighbourStart[s + 1];
    const deg = a1 - a0;
    if (deg > 0) {
      let rr = field.values[s];
      for (let a = a0; a < a1; a++) rr -= field.values[field.neighbours[a]] / deg;
      const t = rr * priors.smoothness;
      cost += t * t;
    }
    const mm = field.values[s] * priors.magnitude;
    cost += mm * mm;
  }
  return cost;
}

/**
 * The stage-B priors actually used: `DISPLACEMENT_PRIORS` scaled by
 * `fieldPriorScale`, in both the normal equations and the line-search cost —
 * the two have to describe the same objective (see the silhouette lesson in
 * `solveField`). The base weights and their derivation stay in
 * `displacement.ts`; the scale, and the campaign that measured it, live on
 * `BUNDLE_DEFAULTS.fieldPriorScale`.
 */
function scaledPriors(opt: BundleOptions): DisplacementPriors {
  return {
    smoothness: DISPLACEMENT_PRIORS.smoothness * opt.fieldPriorScale,
    magnitude: DISPLACEMENT_PRIORS.magnitude * opt.fieldPriorScale,
  };
}

function costLandmarks(state: BundleState, loss: RobustLoss, noseOnly: boolean): number {
  const cam = v3();
  const uv = new Float64Array(2);
  let cost = 0;
  const list = noseOnly ? state.field.vertices : null;
  const count = list ? list.length : state.mesh.vertexCount;

  for (const frame of state.frames) {
    const R = frame.pose.R;
    for (let n = 0; n < count; n++) {
      const i = list ? list[n] : n;
      const sigma = frame.sigmaPx[i];
      if (!(sigma > 0 && sigma < 1e6)) continue;
      const ox = frame.landmarks[i * 2];
      if (Number.isNaN(ox)) continue;
      const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + frame.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + frame.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + frame.pose.t[2];
      if (!project(uv, state.intrinsics, cam)) continue;
      const w = Math.sqrt(state.rigidity[i]) / sigma;
      const r0 = (uv[0] - ox) * w;
      const r1 = (uv[1] - frame.landmarks[i * 2 + 1]) * w;
      cost += loss.eval(r0 * r0 + r1 * r1)[0];
    }
  }
  return cost;
}

function costSilhouetteOnly(state: BundleState, opt: BundleOptions, loss: RobustLoss): number {
  const cam = v3();
  const uv = new Float64Array(2);
  let cost = 0;
  const normals = currentNormals(state);
  for (let f = 0; f < state.frames.length; f++) {
    const frame = state.frames[f];
    if (!frame.silhouette || frame.silhouette.length === 0) continue;
    const index = silhouetteIndexFor(state, f, frame);
    if (!index) continue;
    const R = frame.pose.R;
    for (const i of contourVertices(state, frame, normals)) {
      const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + frame.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + frame.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + frame.pose.t[2];
      if (!project(uv, state.intrinsics, cam)) continue;
      const match = nearestSilhouette(index, uv[0], uv[1]);
      if (!match) continue;
      const r0 = (uv[0] - match.x) * opt.silhouetteWeight;
      const r1 = (uv[1] - match.y) * opt.silhouetteWeight;
      cost += loss.eval(r0 * r0 + r1 * r1)[0];
    }
  }
  return cost;
}

// ------------------------------------------------------------------ plumbing

interface Snapshot {
  poses: Pose[];
  shape: Float64Array;
  intrinsics: Intrinsics;
}

const snapshot = (state: BundleState): Snapshot => ({
  poses: state.frames.map((f) => poseClone(f.pose)),
  shape: new Float64Array(state.shapeCoeffs),
  intrinsics: { ...state.intrinsics },
});

function restore(state: BundleState, s: Snapshot): void {
  for (let i = 0; i < state.frames.length; i++) {
    state.frames[i].pose.R.set(s.poses[i].R);
    state.frames[i].pose.t.set(s.poses[i].t);
  }
  state.shapeCoeffs.set(s.shape);
  state.intrinsics = { ...s.intrinsics };
}

function applyGlobalStep(
  state: BundleState, opt: BundleOptions, dg: Float64Array, dp: Float64Array,
): void {
  const S = state.basis.dim;
  for (let k = 0; k < S; k++) state.shapeCoeffs[k] += dg[k];
  if (intrinsicsDof(opt.intrinsicsMask) > 0) {
    state.intrinsics = applyIntrinsicsDelta(state.intrinsics, dg, S, opt.intrinsicsMask);
  }
  for (let f = 0; f < state.frames.length; f++) {
    poseOplus(state.frames[f].pose, state.frames[f].pose, dp, f * 6);
  }
}

function symmetrizeInPlace(A: Float64Array, n: number): void {
  for (let i = 0; i < n; i++) for (let j = 0; j < i; j++) A[j * n + i] = A[i * n + j];
}

/** Reprojection statistics in raw pixels — the number a human can judge. */
export function reprojectionStats(state: BundleState): {
  rms: number; p95: number; count: number; chiSquare: number;
} {
  const cam = v3();
  const uv = new Float64Array(2);
  const errors: number[] = [];
  let chiSquare = 0;
  for (const frame of state.frames) {
    const R = frame.pose.R;
    for (let i = 0; i < state.mesh.vertexCount; i++) {
      const sigma = frame.sigmaPx[i];
      if (!(sigma > 0 && sigma < 1e6)) continue;
      if (state.rigidity[i] < 0.5) continue;
      const ox = frame.landmarks[i * 2];
      if (Number.isNaN(ox)) continue;
      const x = state.positions[i * 3], y = state.positions[i * 3 + 1], z = state.positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + frame.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + frame.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + frame.pose.t[2];
      if (!project(uv, state.intrinsics, cam)) continue;
      const e = Math.hypot(uv[0] - ox, uv[1] - frame.landmarks[i * 2 + 1]);
      errors.push(e);
      // The same residual, WHITENED by the sigma the detector claimed for it.
      // Raw pixels answer "how well does the model fit"; this answers "was the
      // detector's own claim about its accuracy true", which is the question the
      // reported millimetres depend on.
      chiSquare += (e * e) / (sigma * sigma);
    }
  }
  if (errors.length === 0) return { rms: NaN, p95: NaN, count: 0, chiSquare: 0 };
  let sum = 0;
  for (const e of errors) sum += e * e;
  errors.sort((a, b) => a - b);
  return {
    rms: Math.sqrt(sum / errors.length),
    p95: errors[Math.min(errors.length - 1, Math.floor(errors.length * 0.95))],
    count: errors.length,
    chiSquare,
  };
}

const nowMs = (): number =>
  (typeof performance !== 'undefined' ? performance.now() : Date.now());
