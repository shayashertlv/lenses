/**
 * The face model — what a scan produces, and the only thing tracking and
 * fitting are allowed to read about the wearer.
 *
 * This type is the architectural boundary the whole rewrite is about. In v1
 * there was no such object: "who this wearer is" lived in a median window, an
 * information filter, an EMA, a latch and four eased channels, all of them
 * mutating every frame, all of them inside the per-frame path. Nothing could be
 * measured against ground truth because there was nothing to hold still and
 * compare.
 *
 * Here, the scan produces one immutable value. Everything downstream is a pure
 * function of it plus a camera frame. Three consequences worth naming:
 *
 *  - **It can be tested.** `tests/pipeline.test.ts` compares a recovered model
 *    against the synthetic truth that generated the frames, per region, in mm.
 *  - **It can be cached.** A returning wearer skips the scan. A frame's contact
 *    solve is cached against `(modelId, frameId)`.
 *  - **It can be deleted.** It is biometric data and it is a single object with
 *    a single storage key. `docs/PRIVACY.md` is short because this type made it
 *    short.
 *
 * Uncertainty is carried, not dropped. `vertexSigmaMm` is what lets the
 * occlusion feather widen where the scan is weak, lets the UI say which
 * measurements it actually stands behind, and lets the fit verdicts refuse to
 * make claims the scan cannot support. v1 had exactly one uncertainty number
 * that reached the renderer (a constant feather), and the honest version of that
 * sentence is that it had none.
 */

import type { Intrinsics } from './camera.js';
import { LM, measure, type FaceMeasurements } from './mesh.js';
import type { ScanRecord } from '../enroll/protocol.js';

/**
 * The stored format's version, and there is deliberately no migration path.
 *
 * Bumped 2 -> 3 because version 3 changed what the numbers in `quality` MEAN,
 * not merely which of them are present. `parallaxRms` was the obliquity of each
 * single view, `acos(|dz|/|d|)`; it is now the angular dispersion of the views
 * about their own resultant, `2*asin(sqrt((1-R)/2))`. Read a version 2 model
 * under version 3 rules and a still photograph scores 15.9 degrees of
 * "parallax" against a 12 degree threshold — so `noseConfidence` returns 1.0
 * where the honest measurement is about 0.5, and that number is the gate on
 * whether `assessFit` hedges its advice or speaks like an optician. `scan`,
 * `varianceFactor` and the `'pd'` scale source arrived in the same change.
 *
 * A migration cannot be written even in principle: recovering a dispersion from
 * a stored obliquity means answering a different question about views that were
 * never stored. A scan is four seconds of the wearer's time. See
 * `deserializeFaceModel` for what they are told.
 */
export const FACE_MODEL_VERSION = 3;

/**
 * How the absolute scale was established.
 *
 * `pd` is the best rung that exists: the wearer's own prescription figure,
 * applied in `enroll.ts` against the reconstructed surface. `iris` is the
 * shipping default when they supply nothing, and `assumed` says so out loud.
 *
 * `card` is **not** a rung. An ID-1 card ruler was built, measured and removed
 * from the working tree on 2026-08-25, at `f9c9093` — it was never a tracked
 * file, so no commit holds it — and the owner rejected the method outright
 * (`docs/SCALE.md`); no code writes this variant on any wearer path. It
 * survives as the label the testkit stamps on ground-truth models, and as a
 * value a stored v3 model could still carry. Do not read the order as a
 * ranking to restore.
 */
export type ScaleSource = 'card' | 'pd' | 'iris' | 'assumed';

export interface ScaleEstimate {
  source: ScaleSource;
  /** Millimetres per model unit. The model is already in mm, so this is a
   *  correction factor near 1 — but it is kept explicit rather than folded in,
   *  because folding it in loses the ability to say how well it is known. */
  factor: number;
  /**
   * One standard deviation of `factor`, as a fraction.
   *
   * A **population precision**, and it is worth being exact about what that
   * excludes. On the iris rung it is `IRIS.sigmaMm / IRIS.defaultMm` to within
   * a fortieth of a percentage point — `hypot`'s other term, the frame-to-frame
   * scatter over `sqrt(n)`, contributes 0.02 pp — so it is the same number for
   * every wearer and carries nothing about the one in front of the camera. Two
   * things it therefore cannot express:
   *
   *  1. **The bias.** 11.70 mm sits about 2.2% above the mean of the population
   *     the harness generates, so 67% of runs read the wearer LARGE, +2.59%
   *     signed. A symmetric sigma has no way to say "and it is probably one
   *     direction". `docs/SCALE.md` 1.
   *  2. **A ruler that is simply wrong** — a mistyped PD, an iris on a wearer
   *     whose ancestry the pooled constant does not describe. The sigma is
   *     conditional on the ruler being the thing it claims to be.
   *
   * Both are visible only through `disagreementPct`, and only when a second
   * ruler resolved. It is not under-reported: measured on the whole-mesh gauge
   * over 255 runs, |error|/sigma has median 0.65 and p90 1.72, which brackets a
   * well-calibrated one-sigma from both sides.
   */
  sigma: number;
  /** Free-text provenance: "wearer's PD of 63.0 mm, against the solved surface". */
  note: string;
  /**
   * Signed disagreement between this ruler and the one it displaced, in percent
   * of the winning ruler's reading. Positive means **the displaced ruler read
   * the wearer LARGER**, which is the direction the pooled iris errs.
   *
   * Absent — not zero — when only one ruler resolved, which is the shipping
   * default. That absence is the honest state and it is load-bearing: `sigma`
   * above is blind to this wearer's own scale error by construction, so a
   * disagreement between two independent rulers is the **only** signal in the
   * tree that can see it at all. `scaleSigma` in `fit/score.ts` is the consumer.
   *
   * Optional rather than required because 22 sites construct a `ScaleEstimate`
   * literal, and because a model stored by an older build of this same format
   * version comes back without the key. Every reader must tolerate `undefined`
   * as well as `null`. Never `NaN`: `JSON.stringify` writes that as `null`, and
   * a number that changes meaning on the way through disk is the kind of defect
   * `reprojectionRmsPx` already carries a patch for.
   */
  disagreementPct?: number | null;
}

/**
 * How far two rulers are expected to disagree before the gap means something,
 * in percent.
 *
 * `derived`, and it is the one-sigma of the DIFFERENCE of two readings when
 * both rulers are behaving, not a tolerance anybody chose. The only pair that
 * can occur is the wearer's PD against the iris:
 *
 *     iris      IRIS.sigmaMm / IRIS.defaultMm = 0.55 / 11.70  =  4.70%
 *     PD        PD_RULER.opticianSigmaMm / the wearer's own PD
 *                 at 45 mm  0.50 / 45 = 1.11%   (the narrow end of the range)
 *                 at 63 mm  0.50 / 63 = 0.79%   (a typical adult)
 *                 at 85 mm  0.50 / 85 = 0.59%
 *     hypot     4.74% to 4.83% across the whole admissible PD range
 *
 * 4.8 covers it end to end, and the iris term dominates so hard that the PD's
 * own contribution moves the answer by a tenth of a percentage point over a
 * 40 mm span of wearer. Below this, two rulers that differ are two rulers
 * behaving and there is nothing to report; above it, the excess is real and
 * one of them is wrong — which one is not knowable from inside.
 *
 * Sized from the rulers rather than from what a downstream claim can tolerate
 * on purpose. The width verdict wants 1%, which is far tighter than this; a
 * gate at 1% would fire on most honest scans and would be measuring the iris's
 * ordinary scatter rather than a fault.
 */
export const SCALE_DISAGREEMENT_EXPECTED_PCT = 4.8;

export interface RegionQuality {
  /**
   * Summed observation WEIGHT per vertex of this region, averaged over the
   * region's vertices — not a count of frames.
   *
   * Each frame contributes that vertex's visibility, a continuous facing term
   * in [0, 1] gated at 0.05, so a vertex seen in every keyframe of a complete
   * scan still accumulates only about half the frame count: the nose is
   * oblique to the camera through most of the protocol by construction.
   * Measured across 18 synthetic scans (4 subjects + both named extremes x
   * the camera ladder, seed 11), `observations / framesUsed` is 0.407 to 0.518,
   * median 0.469 — narrow, and stable across both subject and geometry.
   *
   * Reading this as "how many frames saw it" is what put a bare 25 in
   * `noseConfidence`; see `NOSE_OBSERVED_FRACTION`.
   */
  observations: number;
  /**
   * Angular dispersion of the view directions, radians, weighted by observation
   * weight: how much the views SPREAD, not where their centre sat.
   *
   * This is the number v1 could never make large enough, because it penalised
   * the very angle that produces it. Here the protocol *asks* for it, so this
   * field is the direct measurement of whether the scan did its job.
   *
   * **Spread and centre are different questions, and conflating them was a real
   * bug in this field.** It used to be computed as `acos(|dz| / |d|)` — how far
   * OFF-AXIS each single view sat — and reported as parallax, so a motionless
   * head in front of a camera below eye level scored 15.9 degrees against a
   * 12 degree threshold from what was effectively a still photograph. As a
   * dispersion it reads about 16 degrees for a wearer who barely moved and 28
   * for one who followed the whole protocol, and it is the same at every camera
   * height — which is what it was failing to be.
   *
   * The obliquity itself was kept for a while alongside, as `obliquityRms`, on
   * the theory that camera placement predicts reconstruction quality. Measured,
   * it correlates **+0.08** with true nose error against the variance factor's
   * +0.61, and every confidence formulation built on it scored worse than the
   * shipped one. It was removed rather than left in the model looking useful:
   * `docs/OPEN-QUESTIONS.md`, "Also refuted along the way".
   */
  parallaxRms: number;
  /** Median per-vertex sigma in this region, mm. */
  sigmaMm: number;
}

export interface FaceModel {
  readonly version: number;
  /** Stable id for caching contact solves. Content hash of the geometry. */
  readonly id: string;
  /** Wall-clock the scan completed, ms since epoch. For staleness policy only. */
  readonly createdAt: number;

  /** The resolved personal mesh, mm, in face space. 3 * vertexCount. */
  readonly positions: Float64Array;
  readonly vertexCount: number;

  /** Per-vertex one-sigma positional uncertainty, mm. */
  readonly vertexSigmaMm: Float64Array;

  /**
   * Identity coefficients in whichever basis produced this model.
   *
   * **These are the solve's PRE-RULER coordinates.** The bundle recovers shape
   * only up to one global scale; `applyScale` fixes that scale afterwards by
   * multiplying `positions`, the poses and the displacement field, and it never
   * touches these. So a coefficient is a gauge coordinate carrying the bundle's
   * own arbitrary size along with the wearer's shape — not a standardised trait
   * score, and not comparable between two models whose rulers disagreed.
   *
   * That is the trap a human reading the stored JSON falls into: values above 3
   * turn up on perfectly clean captures, where read as z-scores they would mean
   * a face outside the population. They are not z-scores.
   *
   * Left unclamped on purpose. A clamp was measured and moves the resolved
   * geometry by at most 0.03 mm, so it buys nothing, and it would relabel a
   * gauge coordinate as a bound on how unusual a face is allowed to be.
   */
  readonly shapeCoeffs: Float64Array;
  readonly basisName: string;

  /** RMS and peak of the free-form nose field, mm. How much of the nose is
   *  measurement rather than basis. */
  readonly displacementRmsMm: number;
  readonly displacementMaxMm: number;

  /** The camera the scan solved for. Reused as the prior for later sessions on
   *  the same device, which is why it is stored with the face rather than
   *  beside it. */
  readonly intrinsics: Intrinsics;
  readonly intrinsicsSolved: boolean;

  readonly scale: ScaleEstimate;

  /**
   * Per-landmark bias of the detector against this mesh, in mm of face space.
   *
   * v1 found the nose-bridge landmark sits off the canonical mesh by a real,
   * measurable amount and estimated it from two faces. Here it is solved per
   * wearer as part of the bundle, which is the only place the information
   * actually is: across 150 views of one face, a *consistent* offset between
   * where the detector puts a landmark and where the surface is cannot be
   * explained by pose, so it separates cleanly from everything else.
   */
  readonly landmarkBiasMm: Float64Array;

  readonly quality: Record<string, RegionQuality>;
  readonly measurements: FaceMeasurements;

  /** Pupillary distance, mm, if the irises resolved. What an optician writes. */
  readonly pdMm: number | null;
  readonly pdSigmaMm: number | null;

  /**
   * Residual of the bundle at convergence, px RMS. The scan's own self-report.
   *
   * NaN when the scan degraded before the bundle ever ran — a real state rather
   * than a fault, because there is no residual when there were no residuals.
   * NaN and not null so that every consumer meets one shape; JSON has no NaN, so
   * `deserializeFaceModel` puts it back on the way in.
   */
  readonly reprojectionRmsPx: number;
  readonly framesUsed: number;
  readonly solveMs: number;
  /** Set when the scan fell back to a single-view initialisation because the
   *  bundle did not converge. Everything still works; the errors are larger and
   *  the UI says so. */
  readonly degraded: boolean;
  readonly notes: string[];
  /**
   * The scan this model came out of, or null for a model built any other way.
   *
   * It rides on the model because the model is the thing that persists. See
   * `ScanRecord` in `enroll/protocol.ts` for why that matters.
   */
  readonly scan: ScanRecord | null;
  /**
   * The bundle's a-posteriori variance factor. 1.0 means the detector's claimed
   * per-landmark sigma was borne out by the residuals; above 1 it was optimistic.
   *
   * On the model rather than only in the solve report because it is the one
   * scan-quality number that measures a claim against evidence, and it has to
   * survive to the point where somebody is deciding whether to trust the fit.
   */
  readonly varianceFactor: number;
}

// -------------------------------------------------------------- construction

export interface FaceModelInit {
  positions: Float64Array;
  vertexSigmaMm: Float64Array;
  shapeCoeffs: Float64Array;
  basisName: string;
  displacementRmsMm: number;
  displacementMaxMm: number;
  intrinsics: Intrinsics;
  intrinsicsSolved: boolean;
  scale: ScaleEstimate;
  landmarkBiasMm: Float64Array;
  quality: Record<string, RegionQuality>;
  pdMm: number | null;
  pdSigmaMm: number | null;
  reprojectionRmsPx: number;
  framesUsed: number;
  solveMs: number;
  degraded: boolean;
  notes: string[];
  createdAt?: number;
  scan?: ScanRecord | null;
  varianceFactor?: number;
}

export function createFaceModel(init: FaceModelInit): FaceModel {
  return {
    version: FACE_MODEL_VERSION,
    id: hashGeometry(init.positions),
    createdAt: init.createdAt ?? Date.now(),
    positions: init.positions,
    vertexCount: init.positions.length / 3,
    vertexSigmaMm: init.vertexSigmaMm,
    shapeCoeffs: init.shapeCoeffs,
    basisName: init.basisName,
    displacementRmsMm: init.displacementRmsMm,
    displacementMaxMm: init.displacementMaxMm,
    intrinsics: init.intrinsics,
    intrinsicsSolved: init.intrinsicsSolved,
    scale: init.scale,
    landmarkBiasMm: init.landmarkBiasMm,
    quality: init.quality,
    measurements: measure(init.positions),
    pdMm: init.pdMm,
    pdSigmaMm: init.pdSigmaMm,
    reprojectionRmsPx: init.reprojectionRmsPx,
    framesUsed: init.framesUsed,
    solveMs: init.solveMs,
    degraded: init.degraded,
    notes: init.notes,
    scan: init.scan ?? null,
    varianceFactor: init.varianceFactor ?? 1,
  };
}

/**
 * The same model with its scan record attached.
 *
 * Needed because the solve runs in a Worker that never sees the protocol: the
 * model comes back across the boundary complete, and the record is bolted on by
 * the side that knows it. A new object rather than a mutation, so a model is
 * still something you can hold and trust.
 */
export function withScanRecord(model: FaceModel, scan: ScanRecord | null): FaceModel {
  return { ...model, scan };
}

/**
 * A short content hash of the geometry, for cache keys.
 *
 * FNV-1a over the quantised positions. Quantised to a micrometre first, so that
 * a model reloaded through JSON — which round-trips doubles exactly, but a
 * future format might not — keeps the same id and does not silently invalidate
 * every cached contact solve.
 */
/**
 * The same face, in the convention the DETECTOR speaks.
 *
 * `model.positions` is skin: `enroll.ts` subtracts `landmarkBiasMm` from what
 * the bundle solved, because a pad bears on skin rather than on a landmark
 * convention. Everything that compares the model against the detector's own
 * output wants the other one — the surface the detector would report — and by
 * `detector-bias.ts`'s own definition that is `positions + landmarkBiasMm`.
 *
 * Zero today, so this is a copy. It is not decoration: measured on the template
 * with the harness's own 0.6 mm normal-offset bias, solving detector landmarks
 * against the SKIN surface costs **2.04 mm of pose error at frontal** against
 * 0.089 mm here — 23x — while the reprojection rms moves 0.71 to 0.94 px, a
 * fiftieth of the way to `maxRmsPx`. Nothing would have refused a frame; the
 * glasses would simply have sat 2 mm out for the life of the enrollment.
 */
export function landmarkSurface(model: FaceModel): Float64Array {
  const out = new Float64Array(model.positions);
  const bias = model.landmarkBiasMm;
  // **Throws rather than falling back**, because the fallback IS the defect
  // this function exists to remove: skipping the add silently restores the
  // shipped-until-2026-09-02 behaviour of solving detector landmarks against
  // skin, which no gate can see (0.71 -> 0.94 px of reprojection at a 2.04 mm
  // pose error). `deserializeFaceModel` refuses any other format version and
  // `serializeFaceModel` always writes the full array, so this is unreachable
  // today — which is exactly when a silent branch is cheapest to remove.
  if (!bias || bias.length !== out.length) {
    throw new Error(
      `the model carries ${bias ? bias.length : 'no'} landmark-bias values for `
      + `${out.length} coordinates — the detector surface cannot be built, and `
      + 'guessing zero would silently reinstate a 2 mm pose error',
    );
  }
  for (let i = 0; i < out.length; i++) out[i] += bias[i];
  return out;
}

export function hashGeometry(positions: Float64Array): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < positions.length; i++) {
    const q = Math.round(positions[i] * 1000) | 0;
    for (let b = 0; b < 4; b++) {
      h ^= (q >>> (b * 8)) & 0xff;
      h = Math.imul(h, 0x01000193) >>> 0;
    }
  }
  return h.toString(16).padStart(8, '0');
}

// ------------------------------------------------------------ serialisation

/**
 * JSON-safe form. Float arrays become plain arrays of numbers rounded to a
 * micrometre — three orders of magnitude finer than anything this pipeline can
 * measure, and it halves the stored size against full double precision.
 */
export function serializeFaceModel(model: FaceModel): string {
  const round = (a: Float64Array, dp = 3) =>
    Array.from(a, (v) => +v.toFixed(dp));
  return JSON.stringify({
    version: model.version,
    id: model.id,
    createdAt: model.createdAt,
    positions: round(model.positions),
    vertexSigmaMm: round(model.vertexSigmaMm, 3),
    shapeCoeffs: round(model.shapeCoeffs, 6),
    basisName: model.basisName,
    displacementRmsMm: model.displacementRmsMm,
    displacementMaxMm: model.displacementMaxMm,
    intrinsics: model.intrinsics,
    intrinsicsSolved: model.intrinsicsSolved,
    scale: model.scale,
    landmarkBiasMm: round(model.landmarkBiasMm),
    quality: model.quality,
    pdMm: model.pdMm,
    pdSigmaMm: model.pdSigmaMm,
    reprojectionRmsPx: model.reprojectionRmsPx,
    framesUsed: model.framesUsed,
    solveMs: model.solveMs,
    degraded: model.degraded,
    notes: model.notes,
    scan: model.scan,
    varianceFactor: model.varianceFactor,
  });
}

export function deserializeFaceModel(text: string): FaceModel {
  const raw = JSON.parse(text);
  if (raw.version !== FACE_MODEL_VERSION) {
    // Say WHY, because this string is what a wearer's console and a diagnostics
    // dump show when their saved scan disappears. "Could not be read" reads as a
    // fault in the file, the camera or them; a format change is none of those.
    throw new Error(
      `the saved scan is in the version ${raw.version} format and this build ` +
      `reads version ${FACE_MODEL_VERSION} — the scan format changed, so a new ` +
      'scan is needed. Nothing is wrong with the stored measurements; they just ' +
      'mean something different now. Re-scan rather than migrate: a model is ' +
      'four seconds of the wearer\'s time, and a migrated one carries ' +
      'assumptions the new code does not state.',
    );
  }
  const positions = Float64Array.from(raw.positions);
  return {
    version: raw.version,
    id: raw.id,
    createdAt: raw.createdAt,
    positions,
    vertexCount: positions.length / 3,
    vertexSigmaMm: Float64Array.from(raw.vertexSigmaMm),
    shapeCoeffs: Float64Array.from(raw.shapeCoeffs),
    basisName: raw.basisName,
    displacementRmsMm: raw.displacementRmsMm,
    displacementMaxMm: raw.displacementMaxMm,
    intrinsics: raw.intrinsics,
    intrinsicsSolved: raw.intrinsicsSolved,
    scale: raw.scale,
    landmarkBiasMm: Float64Array.from(raw.landmarkBiasMm),
    quality: raw.quality,
    measurements: measure(positions),
    pdMm: raw.pdMm,
    pdSigmaMm: raw.pdSigmaMm,
    // `JSON.stringify` writes NaN as `null`, and the degraded path genuinely
    // produces NaN here — which is every worker-run scan that fell back. Without
    // this the field holds `null` behind a `number` declaration and the first
    // `.toFixed` on it throws, one page reload after the scan that caused it.
    reprojectionRmsPx: raw.reprojectionRmsPx ?? NaN,
    framesUsed: raw.framesUsed,
    solveMs: raw.solveMs,
    degraded: raw.degraded,
    notes: raw.notes ?? [],
    // Null for a model stored before scan records existed. It survives a page
    // reload, which is the whole reason it is here.
    scan: raw.scan ?? null,
    varianceFactor: raw.varianceFactor ?? 1,
  };
}

// ---------------------------------------------------------------- reporting

export interface Confidence {
  /** 0..1, and it is a real posterior weight rather than a progress bar. */
  value: number;
  /** Why it is what it is, for the readout. */
  reason: string;
}

/**
 * Typical variance factor for a scan whose detector behaved as advertised.
 *
 * `measured`, and replicated 2026-08-22 across 5 seeds x 14 subjects x 3
 * camera geometries (210 enrollments, pooled-iris config at the pre-campaign
 * keyframes=48 defaults — not re-measured at 24): per-seed medians 1.75 to
 * 1.91, across-seed median of medians 1.90, pooled median 1.86. The detector's
 * own sigma claim is routinely optimistic, and this is the baseline that means
 * "normal", not 1.0.
 *
 * The full band is 1.44 to 8.33 with a pooled p90 of 5.52, and the upper tail
 * is entirely the phone-lap geometry (per-geometry medians: eye-level 1.63,
 * laptop-lid 1.83, phone-lap 5.10 — the sigma claim is roughly 3x optimistic
 * there). The previous value here, 1.6 with a quoted band of 1.44-1.75, was
 * one unseeded draw and described only the eye-level geometry. At 1.9 the
 * agreement term below reads 1.0 for a median eye-level or laptop-lid scan
 * and about 0.37 for a median phone-lap scan — which is the ordering the
 * confidence exists to report, since phone-lap is also where true nose error
 * is largest.
 */
const TYPICAL_VARIANCE_FACTOR = 1.9;

/**
 * The share of a scan's keyframes a well-observed nose actually accumulates as
 * observation weight — the denominator `noseConfidence`'s `observed` term is
 * measured against.
 *
 * `measured`: across 18 synthetic scans (4 subjects plus both named extremes,
 * each at the three camera geometries, seed 11) `observations / framesUsed`
 * ran 0.407 to 0.518, median 0.469. Set at 0.40, just under the measured
 * minimum, so every healthy scan clamps the term to 1 and it bites only on a
 * nose that genuinely went unobserved. The margin is deliberate: the synthetic
 * protocol reaches yaw angles Q13 says a real neck does not, so a real scan
 * should be expected to sit below the synthetic band without that being a
 * fault.
 *
 * ## Why this is a FRACTION OF `framesUsed` and not a constant
 *
 * It used to be a bare `25`, and the term it feeds is multiplicative, so the
 * bare version silently made confidence a function of the keyframe budget.
 * `KEYFRAME_DEFAULTS.count` dropped 48 -> 24 on millimetre metrics that said
 * nothing about this term, and the ceiling of `observed` went with it: at
 * `/25` the eighteen scans above score 0.391 to 0.497, so **not one of them
 * could reach even half of the term's range**, every wearer-facing number was
 * marked approximate by `SOFT_VERDICT`, and half fell under
 * `ADVICE_CONFIDENCE` and were told the nose was visible in too few frames.
 * Their noses were measured exactly as well as before.
 *
 * Normalising by `framesUsed` makes the term invariant to the keyframe budget,
 * which is the property it needed and never had — a scan is now judged on the
 * fraction of its own frames that saw the nose, not on how many frames the
 * selector happened to keep this month.
 */
export const NOSE_OBSERVED_FRACTION = 0.40;

/**
 * How much to trust this model's nose, which is the only question the seat
 * actually needs answered.
 *
 * Built from the two things that can make it wrong — too few observations, and
 * too little parallax — rather than from elapsed time. That is the same
 * correction v1 eventually made for its seat confidence (`agreement.js`), moved
 * upstream to where the evidence lives.
 *
 * ## Why `sigmaMm` is not one of the terms any more
 *
 * It used to be: `value = parallax * observed * (1 - sigmaMm / 2.5)`. That term
 * looked like the most rigorous of the three and was the least informative of
 * any of them. `sigmaMm` is the formal covariance of the bundle — a
 * **conditional precision**, the spread of the solution given the shape basis
 * and the solved poses. It cannot see the error the basis itself introduces, so
 * it does not measure accuracy, and measurement bears that out:
 *
 *     correlation between sigmaMm and true nose RMS error:  -0.09  (n = 18)
 *
 * Not weak — absent, and slightly the wrong way round. The worst camera geometry
 * in the ladder (a phone in the lap) produced the *largest* true error, 1.79 mm,
 * together with the *smallest* formal sigma, 0.096 mm, because an extreme
 * viewing angle reads to the covariance as an abundance of information. A term
 * built on it was making confidence go up as the reconstruction got worse.
 *
 * The third term is now the variance factor, which measures a claim against
 * evidence — whether the residuals were the size the detector said they would
 * be. It is not a substitute for an accuracy estimate. **This build does not
 * have one**, and saying so is better than deriving false comfort from a number
 * that has none of the property it appears to have.
 */
export function noseConfidence(model: FaceModel): Confidence {
  const q = model.quality.nose;
  if (!q) return { value: 0, reason: 'no nose coverage recorded' };

  // Parallax: 12 degrees RMS is where triangulated depth error falls below the
  // 1 mm the contact solver needs. Derived in `docs/CONSTANTS.md`. This is now a
  // genuine spread between view directions — it reads about 16 degrees for a
  // wearer who barely moved and 28 for one who followed the whole protocol, and
  // it is the same at every camera height, which is what it was failing to be.
  const parallax = Math.min(q.parallaxRms / (12 * Math.PI / 180), 1);
  // Against the fraction of THIS scan's keyframes a good nose earns, not
  // against a bare frame count — see `NOSE_OBSERVED_FRACTION` for what the
  // bare version did when the keyframe budget moved underneath it.
  const observed = Math.min(
    q.observations / (NOSE_OBSERVED_FRACTION * Math.max(model.framesUsed, 1)), 1,
  );
  // Did the detector's residuals come out the size it claimed? At or below the
  // typical factor this is 1; at twice typical it halves.
  const agreement = Math.min(1, TYPICAL_VARIANCE_FACTOR / Math.max(model.varianceFactor, 1e-6));
  const value = parallax * observed * agreement;

  // **The reason names the term that BINDS, not the first one to trip.**
  //
  // The old form tested the three in a fixed order and reported the first
  // below 0.5, so whenever more than one was low the wearer was told about
  // whichever happened to be checked first. On the phone-lap geometry that is
  // reliably wrong: `agreement` runs 0.33-0.47 there against `observed`'s
  // 0.41-0.47, so the binding fault is landmark noise and the sentence blamed
  // the head turn. The wearer then re-scans, which cannot help, because the
  // thing that was actually wrong was the camera.
  const terms: readonly (readonly [number, string])[] = [
    [parallax, 'not enough head turn during the scan'],
    [observed, 'the nose was visible in too few frames'],
    [agreement, 'the landmarks were noisier than the detector reported'],
  ];
  let worst = terms[0];
  for (const term of terms) if (term[0] < worst[0]) worst = term;
  const reason = worst[0] < 0.5 ? worst[1] : 'measured';

  return { value, reason };
}

/** The landmark indices that exist on the mesh (i.e. not the iris refinement). */
export const MESH_LANDMARK_COUNT = 468;

export function isIrisLandmark(index: number): boolean {
  return index >= MESH_LANDMARK_COUNT;
}

export { LM };
