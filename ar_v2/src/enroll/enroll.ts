/**
 * The scan, end to end: frames in, one `FaceModel` out.
 *
 * The order matters and each step exists for a reason the previous step cannot
 * cover:
 *
 *   1. **Initialise poses.** Sequentially, each frame from its predecessor,
 *      because consecutive frames are close and POSIT-from-scratch on a profile
 *      view is the one place a coarse initialiser genuinely struggles. Frame one
 *      is near-frontal by protocol, which is the easy case.
 *   2. **Select keyframes.** Span the poses actually visited, not the frame rate.
 *   3. **Assess coverage.** Before spending the solve. A scan missing its turn
 *      beat cannot produce a nose, and telling the wearer that in two seconds is
 *      better than producing a confident average one.
 *   4. **Bundle.** Poses, shape, intrinsics, and the free-form nose field.
 *   5. **Scale.** The bundle is scale-free; the ruler sets the size.
 *   6. **Uncertainty.** Per vertex, from the observations that actually
 *      constrained it — which is what lets everything downstream know what it
 *      is standing on.
 *
 * Step 6 is the one with no equivalent in v1 at all, and it is what makes the
 * rest of the system honest. A vertex seen in forty frames across sixty degrees
 * of parallax and a vertex seen twice head-on are both "in the mesh"; only one
 * of them is a measurement.
 */

import {
  type Intrinsics, MEDIAPIPE_ASSUMED_VERTICAL_FOV, dProjDModelPoint,
  intrinsicsFromFov, project, viewAngleAt,
} from '../core/camera.js';
import {
  type Pose, invertSymmetric, poseClone, poseIdentity, v3, weightedMedian,
} from '../core/linalg.js';
import {
  type FaceMesh, type Region, standardRegions,
} from '../core/mesh.js';
import { type ShapeBasis } from '../core/shape/basis.js';
import {
  createDisplacementField, displacementStats,
} from '../core/shape/displacement.js';
import {
  type FaceModel, type RegionQuality, createFaceModel,
} from '../core/facemodel.js';
import {
  type BundleFrame, type BundleOptions, type BundleReport,
  createBundleState, landmarkRigidity, runBundle,
} from './bundle.js';
import {
  type CoverageVerdict, type KeyframeOptions, assessCoverage, selectKeyframes,
} from './keyframes.js';
import { applyScale, readIris, solveScale, type CardReading } from './scale.js';
import { buildCorrespondences, solvePnP } from '../track/pnp.js';
import { detectorBias, type DetectorBias } from './detector-bias.js';

export interface EnrollInput {
  mesh: FaceMesh;
  basis: ShapeBasis;
  /** Raw frames in capture order. Poses are ignored on input and solved here. */
  frames: Omit<BundleFrame, 'pose'>[];
  /** Starting camera. If unknown, pass the image size and let the bundle solve. */
  intrinsics?: Intrinsics;
  imageWidth: number;
  imageHeight: number;
  card?: CardReading | null;
  /** Overrides the pooled iris assumption when the wearer volunteers it. */
  irisMm?: number;
  irisSigmaMm?: number;
  bias?: DetectorBias;
  keyframes?: Partial<KeyframeOptions>;
  bundle?: Partial<BundleOptions>;
  trace?: (message: string) => void;
}

export interface EnrollResult {
  model: FaceModel;
  coverage: CoverageVerdict;
  bundle: BundleReport;
  /** The solved poses of the keyframes, for the harness and for debugging. */
  keyframePoses: Pose[];
  keyframeIndices: number[];
}

export function enroll(input: EnrollInput): EnrollResult {
  const started = nowMs();
  const { mesh, basis } = input;
  const notes: string[] = [];
  const regions = standardRegions(mesh);
  const rigidity = landmarkRigidity(mesh, regions.nose);

  const intrinsics0 = input.intrinsics ?? intrinsicsFromFov(
    input.imageWidth, input.imageHeight, MEDIAPIPE_ASSUMED_VERTICAL_FOV,
  );

  // ---- 1. initialise poses ------------------------------------------------
  const frames: BundleFrame[] = [];
  let previous: Pose | undefined;
  let failedInits = 0;
  for (const raw of input.frames) {
    const correspondences = buildCorrespondences(
      raw.landmarks, raw.sigmaPx, mesh.vertexCount, rigidity,
    );
    if (correspondences.length < 30) { failedInits++; continue; }
    const result = solvePnP(mesh.positions, correspondences, intrinsics0, previous);
    if (!(result.pose.t[2] > 50 && result.pose.t[2] < 5000)) { failedInits++; previous = undefined; continue; }
    previous = poseClone(result.pose);
    frames.push({ ...raw, pose: result.pose });
  }
  if (failedInits) notes.push(`${failedInits} frames failed pose initialisation`);
  input.trace?.(`initialised ${frames.length} poses (${failedInits} failed)`);

  if (frames.length < 8) {
    return degraded(input, intrinsics0, regions, notes, started,
      'too few usable frames to solve a scan');
  }

  // ---- 2/3. keyframes and coverage ---------------------------------------
  const selection = selectKeyframes(frames, input.keyframes);
  const coverage = assessCoverage(selection, frames);
  input.trace?.(
    `keyframes ${selection.frames.length}/${frames.length}, ` +
    `yaw span ${selection.yawSpanDeg.toFixed(0)}deg, ` +
    `pitch ${selection.pitchSpanDeg.toFixed(0)}deg, ` +
    `distance ${selection.distanceSpanPct.toFixed(0)}%`,
  );
  if (!coverage.sufficient) notes.push(`coverage incomplete: ${coverage.missing.join(', ')}`);
  if (!coverage.hasProfile) {
    notes.push('no profile view — nose protrusion is inferred, not observed');
  }

  // ---- 4. the bundle ------------------------------------------------------
  const field = createDisplacementField(mesh, regions.nose);
  const state = createBundleState(
    mesh, basis, field, regions.nose, selection.frames, intrinsics0,
  );
  const bundleOptions: Partial<BundleOptions> = {
    ...input.bundle,
    intrinsicsMask: input.bundle?.intrinsicsMask ?? {
      // Only solve the focal length when the lean beat actually happened.
      // Solving it from a fixed-distance capture is not a hard failure, it is a
      // soft one: it converges, absorbs a millimetre of head size, and nothing
      // reports it. Refusing is the honest option.
      f: coverage.canSolveIntrinsics,
      pp: false,
      k1: false,
    },
    trace: input.trace,
  };
  if (!coverage.canSolveIntrinsics) {
    notes.push('camera field of view assumed, not solved (no lean in the scan)');
  }
  const report = runBundle(state, bundleOptions);
  input.trace?.(
    `bundle: ${report.reprojectionRmsPx.toFixed(3)} px rms in ${report.ms.toFixed(0)} ms, ` +
    `f ${report.focalPx.toFixed(1)} (${report.focalMovedPct >= 0 ? '+' : ''}${report.focalMovedPct.toFixed(1)}%)`,
  );

  // ---- 5. scale -----------------------------------------------------------
  const readings = [];
  for (const frame of state.frames) {
    const r = readIris(frame.landmarks, frame.pose, state.positions);
    if (r) readings.push(r);
  }
  const scale = solveScale({
    readings,
    intrinsics: state.intrinsics,
    irisMm: input.irisMm,
    irisSigmaMm: input.irisSigmaMm,
    card: input.card ?? null,
  });
  applyScale(state.positions, state.frames.map((f) => f.pose), scale.estimate.factor);
  if (scale.estimate.source === 'assumed') {
    notes.push('no absolute ruler resolved — millimetre readouts are unavailable');
  }
  if (scale.disagreementPct !== null && Math.abs(scale.disagreementPct) > 4) {
    notes.push(
      `card and iris disagree by ${scale.disagreementPct.toFixed(1)}% — ` +
      'the card is used; the gap is the iris assumption being wrong for this wearer',
    );
  }
  input.trace?.(
    `scale: ${scale.estimate.source} x${scale.estimate.factor.toFixed(4)} ` +
    `+/-${(scale.estimate.sigma * 100).toFixed(1)}%` +
    (scale.pdMm ? `, PD ${scale.pdMm.toFixed(1)} mm` : ''),
  );

  // ---- 6. uncertainty and quality ----------------------------------------
  const { sigma, observations, parallax } = perVertexUncertainty(state.positions, state.frames, state.intrinsics, mesh);
  const quality: Record<string, RegionQuality> = {};
  for (const [name, region] of Object.entries(regions)) {
    quality[name] = summarise(region, sigma, observations, parallax);
  }

  // The detector's own offset from the skin, applied as a correction to the
  // reconstructed surface. Zero by default — see the module for why this cannot
  // be solved per wearer.
  const bias = input.bias ?? detectorBias();
  const corrected = new Float64Array(state.positions);
  for (let i = 0; i < corrected.length; i++) corrected[i] -= bias.offsetMm[i] ?? 0;

  const stats = displacementStats(field);
  const model = createFaceModel({
    positions: corrected,
    vertexSigmaMm: sigma,
    shapeCoeffs: new Float64Array(state.shapeCoeffs),
    basisName: basis.name,
    displacementRmsMm: stats.rmsMm,
    displacementMaxMm: stats.maxMm,
    intrinsics: state.intrinsics,
    intrinsicsSolved: coverage.canSolveIntrinsics,
    scale: scale.estimate,
    landmarkBiasMm: bias.offsetMm,
    quality,
    pdMm: scale.pdMm,
    pdSigmaMm: scale.pdSigmaMm,
    reprojectionRmsPx: report.reprojectionRmsPx,
    framesUsed: state.frames.length,
    solveMs: nowMs() - started,
    degraded: !coverage.sufficient || !report.converged,
    notes,
  });

  return {
    model,
    coverage,
    bundle: report,
    keyframePoses: state.frames.map((f) => poseClone(f.pose)),
    keyframeIndices: selection.indices,
  };
}

// -------------------------------------------------------------- uncertainty

/**
 * Per-vertex positional uncertainty, in millimetres, from the views that
 * actually saw it.
 *
 * For each vertex, accumulate the 3x3 information matrix of its own
 * observations: `A = sum_f J_f^T J_f / sigma_f^2` with `J` the 2x3 jacobian of
 * the projection with respect to the model point. Then the one-sigma positional
 * uncertainty is `sqrt(trace(A^-1) / 3)`.
 *
 * The shape of `A` is the whole story of monocular reconstruction, and it says
 * out loud what v1 spent a `zConf` accumulator discovering the hard way: a
 * vertex seen only head-on has a huge null direction along the view ray, so
 * `A^-1` blows up in depth however many frames it was seen in. Only parallax
 * fills it. That is why `parallax` is returned alongside — the two together tell
 * a wearer not just how well their nose is known but *why*.
 */
export function perVertexUncertainty(
  positions: Float64Array, frames: BundleFrame[], intrinsics: Intrinsics, mesh: FaceMesh,
): { sigma: Float64Array; observations: Float64Array; parallax: Float64Array } {
  const V = mesh.vertexCount;
  const A = new Float64Array(V * 9);
  const observations = new Float64Array(V);
  const parallaxSum = new Float64Array(V);
  const parallaxWeight = new Float64Array(V);

  const cam = v3();
  const uv = new Float64Array(2);
  const J = new Float64Array(6);

  for (const frame of frames) {
    const R = frame.pose.R;
    for (let i = 0; i < V; i++) {
      const s = frame.sigmaPx[i];
      if (!(s > 0 && s < 1e6)) continue;
      if (Number.isNaN(frame.landmarks[i * 2])) continue;
      const w = frame.visibility[i];
      if (!(w > 0.05)) continue;

      const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
      cam[0] = R[0] * x + R[1] * y + R[2] * z + frame.pose.t[0];
      cam[1] = R[3] * x + R[4] * y + R[5] * z + frame.pose.t[1];
      cam[2] = R[6] * x + R[7] * y + R[8] * z + frame.pose.t[2];
      if (!project(uv, intrinsics, cam)) continue;

      dProjDModelPoint(J, 0, intrinsics, cam, R);
      const iw = w / (s * s);
      for (let a = 0; a < 3; a++) {
        for (let b = 0; b < 3; b++) {
          A[i * 9 + a * 3 + b] += iw * (J[a] * J[b] + J[3 + a] * J[3 + b]);
        }
      }
      observations[i] += w;
      const angle = viewAngleAt(frame.pose, positions.subarray(i * 3, i * 3 + 3));
      parallaxSum[i] += w * angle * angle;
      parallaxWeight[i] += w;
    }
  }

  const sigma = new Float64Array(V);
  const parallax = new Float64Array(V);
  const block = new Float64Array(9);
  for (let i = 0; i < V; i++) {
    parallax[i] = parallaxWeight[i] > 0 ? Math.sqrt(parallaxSum[i] / parallaxWeight[i]) : 0;
    block.set(A.subarray(i * 9, i * 9 + 9));
    // A floor on the information, equivalent to a prior that the vertex is
    // within ~8 mm of where the basis put it. Without it an unobserved vertex
    // reports infinite uncertainty and every median over a region becomes
    // Infinity — which is technically true and useless.
    for (let d = 0; d < 3; d++) block[d * 3 + d] += 1 / (8 * 8);
    if (!invertSymmetric(block, 3)) { sigma[i] = 8; continue; }
    const trace = block[0] + block[4] + block[8];
    sigma[i] = Math.sqrt(Math.max(trace, 0) / 3);
  }

  return { sigma, observations, parallax };
}

function summarise(
  region: Region, sigma: Float64Array, observations: Float64Array, parallax: Float64Array,
): RegionQuality {
  const sigmas: number[] = [];
  let obs = 0, par = 0, w = 0;
  for (const i of region.members) {
    const rw = region.weight[i];
    if (rw < 0.5) continue;
    sigmas.push(sigma[i]);
    obs += observations[i];
    par += parallax[i] * parallax[i];
    w++;
  }
  return {
    observations: w ? obs / w : 0,
    parallaxRms: w ? Math.sqrt(par / w) : 0,
    sigmaMm: sigmas.length ? weightedMedian(sigmas) : 8,
  };
}

// ------------------------------------------------------------------ failure

/**
 * What a scan returns when it could not run.
 *
 * The template, honestly labelled. This is v1's *normal* operating state and
 * v2's failure state, which is the whole difference between the two systems in
 * one object: everything downstream still works, the numbers are still
 * produced, and every one of them is marked `degraded` with `scale.source` of
 * `assumed`, so nothing in the UI can claim a millimetre it does not have.
 */
function degraded(
  input: EnrollInput, intrinsics: Intrinsics, regions: Record<string, Region>,
  notes: string[], started: number, why: string,
): EnrollResult {
  notes.push(why);
  const V = input.mesh.vertexCount;
  const quality: Record<string, RegionQuality> = {};
  for (const name of Object.keys(regions)) {
    quality[name] = { observations: 0, parallaxRms: 0, sigmaMm: 8 };
  }
  const model = createFaceModel({
    positions: new Float64Array(input.mesh.positions),
    vertexSigmaMm: new Float64Array(V).fill(8),
    shapeCoeffs: new Float64Array(input.basis.dim),
    basisName: input.basis.name,
    displacementRmsMm: 0,
    displacementMaxMm: 0,
    intrinsics,
    intrinsicsSolved: false,
    scale: { source: 'assumed', factor: 1, sigma: 0.05, note: why },
    landmarkBiasMm: new Float64Array(V * 3),
    quality,
    pdMm: null,
    pdSigmaMm: null,
    reprojectionRmsPx: NaN,
    framesUsed: 0,
    solveMs: nowMs() - started,
    degraded: true,
    notes,
  });
  return {
    model,
    coverage: {
      sufficient: false, canSolveIntrinsics: false, hasProfile: false,
      missing: ['turn', 'nod', 'lean', 'profile'],
      advice: 'The scan could not find your face for long enough. Try again in better light.',
    },
    bundle: {
      rounds: 0, reprojectionRmsPx: NaN, reprojectionP95Px: NaN, residualsUsed: 0,
      silhouetteResiduals: 0, focalPx: intrinsics.f, focalMovedPct: 0, ms: 0,
      perRound: [], converged: false, fieldFailures: 0, fieldRmsMm: 0,
    },
    keyframePoses: [],
    keyframeIndices: [],
  };
}

const nowMs = (): number =>
  (typeof performance !== 'undefined' ? performance.now() : Date.now());
