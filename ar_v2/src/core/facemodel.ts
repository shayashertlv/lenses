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
 *  - **It can be tested.** `tests/enroll-*.test.ts` compares a recovered model
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

export const FACE_MODEL_VERSION = 2;

/** How the absolute scale was established. Ordered best-first. */
export type ScaleSource = 'card' | 'iris' | 'assumed';

export interface ScaleEstimate {
  source: ScaleSource;
  /** Millimetres per model unit. The model is already in mm, so this is a
   *  correction factor near 1 — but it is kept explicit rather than folded in,
   *  because folding it in loses the ability to say how well it is known. */
  factor: number;
  /** One standard deviation of `factor`, as a fraction. */
  sigma: number;
  /** Free-text provenance for the readout: "ISO 7810 card, 34 frames". */
  note: string;
}

export interface RegionQuality {
  /** Mean number of frames in which a vertex of this region was observed with
   *  usable weight. */
  observations: number;
  /**
   * RMS parallax angle, radians, weighted by observation weight.
   *
   * This is the number v1 could never make large enough, because it penalised
   * the very angle that produces it. Here the protocol *asks* for it, so this
   * field is the direct measurement of whether the scan did its job.
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

  /** Identity coefficients in whichever basis produced this model. */
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

  /** Residual of the bundle at convergence, px RMS. The scan's own self-report. */
  readonly reprojectionRmsPx: number;
  readonly framesUsed: number;
  readonly solveMs: number;
  /** Set when the scan fell back to a single-view initialisation because the
   *  bundle did not converge. Everything still works; the errors are larger and
   *  the UI says so. */
  readonly degraded: boolean;
  readonly notes: string[];
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
  };
}

/**
 * A short content hash of the geometry, for cache keys.
 *
 * FNV-1a over the quantised positions. Quantised to a micrometre first, so that
 * a model reloaded through JSON — which round-trips doubles exactly, but a
 * future format might not — keeps the same id and does not silently invalidate
 * every cached contact solve.
 */
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
  });
}

export function deserializeFaceModel(text: string): FaceModel {
  const raw = JSON.parse(text);
  if (raw.version !== FACE_MODEL_VERSION) {
    throw new Error(
      `face model version ${raw.version}, expected ${FACE_MODEL_VERSION}. ` +
      'Re-scan rather than migrate: a model is four seconds of the wearer\'s time, ' +
      'and a migrated one carries assumptions the new code does not state.',
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
    reprojectionRmsPx: raw.reprojectionRmsPx,
    framesUsed: raw.framesUsed,
    solveMs: raw.solveMs,
    degraded: raw.degraded,
    notes: raw.notes ?? [],
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
 * How much to trust this model's nose, which is the only question the seat
 * actually needs answered.
 *
 * Built from the two things that can make it wrong — too few observations, and
 * too little parallax — rather than from elapsed time. That is the same
 * correction v1 eventually made for its seat confidence (`agreement.js`), moved
 * upstream to where the evidence lives.
 */
export function noseConfidence(model: FaceModel): Confidence {
  const q = model.quality.nose;
  if (!q) return { value: 0, reason: 'no nose coverage recorded' };

  // Parallax: 12 degrees RMS is where triangulated depth error falls below the
  // 1 mm the contact solver needs. Derived in `docs/CONSTANTS.md`.
  const parallax = Math.min(q.parallaxRms / (12 * Math.PI / 180), 1);
  const observed = Math.min(q.observations / 25, 1);
  const sigma = Math.max(0, 1 - q.sigmaMm / 2.5);
  const value = parallax * observed * sigma;

  let reason: string;
  if (parallax < 0.5) reason = 'not enough head turn during the scan';
  else if (observed < 0.5) reason = 'the nose was visible in too few frames';
  else if (sigma < 0.5) reason = 'the observations disagreed';
  else reason = 'measured';

  return { value, reason };
}

/** The landmark indices that exist on the mesh (i.e. not the iris refinement). */
export const MESH_LANDMARK_COUNT = 468;

export function isIrisLandmark(index: number): boolean {
  return index >= MESH_LANDMARK_COUNT;
}

export { LM };
