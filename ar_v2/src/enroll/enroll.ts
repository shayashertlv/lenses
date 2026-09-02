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
  intrinsicsFromFov, project,
} from '../core/camera.js';
import {
  type Pose, invertSymmetric, poseClone, poseIdentity, v3, weightedMedian,
} from '../core/linalg.js';
import {
  LM, type FaceMesh, type Region, standardRegions,
} from '../core/mesh.js';
import { type ShapeBasis } from '../core/shape/basis.js';
import {
  createDisplacementField, displacementStats,
} from '../core/shape/displacement.js';
import {
  type FaceModel, type RegionQuality, SCALE_DISAGREEMENT_EXPECTED_PCT, createFaceModel,
} from '../core/facemodel.js';
import {
  type BundleFrame, type BundleOptions, type BundleReport,
  createBundleState, landmarkRigidity, runBundle,
} from './bundle.js';
import {
  type CoverageVerdict, type KeyframeOptions, assessCoverage, selectKeyframes,
} from './keyframes.js';
import {
  PD_PLAUSIBLE_MM, PD_RULER, applyScale, readIris, solveScale,
} from './scale.js';
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
  /** Overrides the pooled iris assumption when the wearer volunteers it. */
  irisMm?: number;
  irisSigmaMm?: number;
  /** The wearer's own pupillary distance, mm. A better ruler than the pooled
   *  iris because it was measured on them. See `PD_RULER` in `scale.ts`. */
  knownPdMm?: number | null;
  /** One sigma on that figure, mm. Defaults to `PD_RULER.opticianSigmaMm`. */
  knownPdSigmaMm?: number;
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
  // **The term was asked for and nothing arrived.**
  //
  // `useSilhouette` defaults true, and all five silhouette paths in `bundle.ts`
  // enter and then `continue` on `!frame.silhouette`. So a caller that hands
  // every frame `silhouette: null` gets the `no-silhouette` ablation while
  // believing it got the contour term, and `BundleReport.silhouetteResiduals`
  // - which is the only place that shows - has no consumers in `src/` or
  // `tests/`. That is exactly what production did on 100% of real frames
  // (0 of 141 and 0 of 165 on the two real scans in `docs/REAL-FACE.md`), and
  // nothing said so for the life of the feature.
  //
  // It is not a cosmetic gap. Measured over 5 seeds x 10 subjects x 3 camera
  // geometries, supplying the contour is worth 0.287 mm off the |standoff| p90
  // and 0.412 mm off its worst, winning on 5 seeds of 5 - and `docs/CONSTANTS.md`
  // publishes `silhouetteWeight = 1.0` as `measured` from a sweep that ran
  // entirely on a term production never executed.
  if (bundleOptions.useSilhouette !== false && report.silhouetteResiduals === 0) {
    notes.push('no silhouette was supplied — the profile contour term was skipped');
  }
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
  });
  applyScale(state.positions, state.frames.map((f) => f.pose), scale.estimate.factor, field);

  // The wearer's own PD, applied against the RECONSTRUCTED geometry.
  //
  // Not in `solveScale` next to the iris, and not from `readIris`'s `pdPx`: an
  // image-space pupil separation foreshortens with yaw, which is the exact
  // property the iris was chosen to avoid. Measured, that version made the scale
  // worse than the assumption it replaced. Here the span is taken between the
  // eye-corner midpoints of the solved 3-D surface, where head angle has already
  // been divided out by the bundle.
  if (input.knownPdMm != null) {
    const span = interpupillarySpan(state.positions);
    // The SAME constant the readout at the bottom of this function gates on.
    // Two literals here and `PD_PLAUSIBLE_MM` there is what let a scan adopt a
    // wearer's PD as its ruler and then deny having it.
    if (span > 1 && input.knownPdMm >= PD_PLAUSIBLE_MM[0]
      && input.knownPdMm <= PD_PLAUSIBLE_MM[1]) {
      const correction = input.knownPdMm / span;
      applyScale(state.positions, state.frames.map((f) => f.pose), correction, field);
      const sigmaMm = input.knownPdSigmaMm ?? PD_RULER.opticianSigmaMm;
      // Name the ruler the correction is measured AGAINST. It is the iris only
      // when an iris actually resolved; with no ruler at all the scan was
      // carrying the template's size, and calling that "the iris assumption"
      // reports a measurement that never happened.
      const displaced = scale.estimate.source;

      // **The second ruler, which is the only thing in this tree that can see a
      // scale error at all.**
      //
      // `ScaleEstimate.sigma` is a population precision — on the iris rung it is
      // 0.55/11.70 to within 0.02 pp, identical for every wearer — so nothing
      // downstream can distinguish a wearer the 11.70 mm assumption fits from
      // one whose true HVID is 11.10 and who therefore carries 5.4% at exactly
      // the same printed confidence. Two rulers disagreeing is a DIRECT
      // observation of this wearer's own error, and it is free: the gap is
      // `correction`, which this function already computes to move the geometry.
      //
      // Signed so the direction survives, and signed the way the documented
      // bias is: POSITIVE means the displaced ruler read the wearer LARGER.
      // `span` is what the displaced ruler made of the pupils and `knownPdMm` is
      // what a pupilometer made of them, so that is `span / knownPd - 1`, i.e.
      // `1 / correction - 1`.
      //
      // It is deliberately not folded into `sigma`. The gap is one-sided and a
      // sigma is two-sided, and averaging a known direction into a symmetric
      // interval destroys the only information the second ruler supplied.
      // Rounded at construction rather than at the readout, because this field
      // is dumped verbatim into `diagnostics.ts`'s paste and into the stored
      // model. A thousandth of a percentage point is four orders below the
      // 4.8% the gap is compared against; what it buys is that a wearer's
      // diagnostics say -4.762 rather than -4.761904761904767.
      const disagreementPct = displaced === 'assumed'
        ? null
        : Math.round((1 / correction - 1) * 100_000) / 1000;

      notes.push(
        `scale set from your PD of ${input.knownPdMm.toFixed(1)} mm ` +
        (displaced === 'assumed'
          ? `(it resized the scan by ${((correction - 1) * 100).toFixed(1)}%, which had no ruler before)`
          // Say which WAY, not just how far. The old wording was
          // `the iris scale was +3.0% out`, and a leading plus in front of "out"
          // reads as "the iris was 3% too big" — the opposite of what a positive
          // `correction - 1` means, which is that the scan had to be made BIGGER
          // because the iris had read the wearer small.
          : `(the ${displaced} ruler read you ` +
            `${Math.abs(disagreementPct!).toFixed(1)}% ` +
            `${disagreementPct! > 0 ? 'large' : 'small'})`),
      );
      // Above the gap two well-behaved rulers explain by themselves, one of them
      // is wrong and this cannot say which. `scaleSigma` prices that; the wearer
      // is told, because a PD typed one digit out is the likeliest cause and it
      // is the one thing they can check.
      if (disagreementPct !== null
        && Math.abs(disagreementPct) > SCALE_DISAGREEMENT_EXPECTED_PCT) {
        notes.push(
          `your PD and the ${displaced} ruler disagree by ` +
          `${Math.abs(disagreementPct).toFixed(1)}%, further apart than the two ` +
          'can normally be — one of them is wrong. Check the PD is the ' +
          'distance between both pupils and not one eye\'s half of it',
        );
      }
      scale.estimate = {
        source: 'pd',
        factor: scale.estimate.factor * correction,
        // **This sigma moves the wrong way when the ruler is wrong, and no
        // sigma can fix that.** It is `opticianSigmaMm / knownPdMm`, which is
        // the correct relative precision of a pupilometer reading — but the
        // wearer TYPES this number, and a larger mistyped PD therefore prints a
        // SMALLER sigma. Measured over 10 (seed, subject) pairs: a PD typed 10%
        // high gives a 10.00% scale error at sigma 0.714% and width confidence
        // 0.881, against 0% error at 0.786% and 0.869 when it is right — a
        // wrong scale carried at HIGHER confidence than a correct one.
        //
        // Not patched with an invented recall term. A mistyped ruler is a
        // blunder, not a Gaussian, and inflating every honest wearer's sigma to
        // cover it would be the "wrong and confident" trade taken in the other
        // direction. `disagreementPct` above is the defence, and it is a real
        // one: at a 10% mistype the iris disagrees by about 10% against a 4.8%
        // expectation, and `scaleSigma` takes the confidence to near zero.
        // What has no defence is a mistyped PD on a scan where NO iris
        // resolved — said out loud in the note below.
        sigma: sigmaMm / input.knownPdMm,
        note: `wearer's PD of ${input.knownPdMm.toFixed(1)} mm, against the solved surface`
          + (disagreementPct === null ? ', unchecked — no second ruler resolved' : ''),
        disagreementPct,
      };
    } else {
      // A PD was supplied and silently ignored. The app's own `set-pd` handler
      // refuses the same range before storing, so this branch is reached by the
      // library entry point — a replayed capture, a harness, a caller that is
      // not the UI — and there it used to fall through with no note at all, so
      // the scan came back on the iris while the caller believed it had set the
      // ruler. `scale.ts`'s "never silently substitutes" applies to a ruler that
      // was OFFERED as much as to one that was missing.
      notes.push(
        `the PD supplied (${input.knownPdMm.toFixed(1)} mm) was not used — ` +
        (span > 1
          ? `it is outside the ${PD_PLAUSIBLE_MM[0]} to ${PD_PLAUSIBLE_MM[1]} mm human range`
          : 'the solved eye span is degenerate, so there was nothing to correct'),
      );
    }
  }

  // The PD readout, measured on the scaled 3-D surface and never from the image.
  //
  // `solveScale` used to derive it from `readIris`'s `pdPx`, and that expression
  // collapses to a ratio of two IMAGE lengths with the depth and the focal length
  // cancelling exactly — so it foreshortened with yaw and every subject read low,
  // by a mean of 3.93 mm, behind a printed sigma of about 2.7 that did not cover
  // it. The long version of why is in `solveScale`. Off the solved surface, with
  // the wearer's true iris diameter supplied as the ruler, the same measurement
  // reads +0.78 mm mean and 1.62 mm worst over the synthetic population; on the
  // pooled iris it reads +1.87 mm mean, which is the scale error and nothing
  // else, beside a sigma of about 3 mm that is the right size for it.
  //
  // That sigma is the SCALE's, and it is not an accuracy: it cannot see the
  // eye-corner midpoint's own bias as a stand-in for a pupil, which is unmeasured
  // on a real face (`interpupillarySpan` below, and `docs/OPEN-QUESTIONS.md`
  // Q17). Where the wearer supplied their own PD this reproduces their figure and
  // their sigma exactly, by construction — the correction above set it.
  if (scale.estimate.source !== 'assumed') {
    const span = interpupillarySpan(state.positions);
    if (span >= PD_PLAUSIBLE_MM[0] - PD_ROUNDTRIP_MM
      && span <= PD_PLAUSIBLE_MM[1] + PD_ROUNDTRIP_MM) {
      scale.pdMm = span;
      scale.pdSigmaMm = span * scale.estimate.sigma;
    } else {
      // Outside the human range is a failure of the eye landmarks or of the ruler
      // — a half-closed eye, a specular highlight, a scale that came out badly —
      // not a wearer who is unusual. Refuse rather than report, and say so:
      // a missing PD with no explanation is the kind of silence that gets read as
      // a bug in the readout.
      notes.push(
        'pupillary distance not reported — the measured eye span is outside the ' +
        'human range, so something in the eye landmarks or the scale is wrong',
      );
    }
  }

  if (scale.estimate.source === 'assumed') {
    notes.push('no absolute ruler resolved — millimetre readouts are unavailable');
  }
  input.trace?.(
    `scale: ${scale.estimate.source} x${scale.estimate.factor.toFixed(4)} ` +
    `+/-${(scale.estimate.sigma * 100).toFixed(1)}%` +
    (scale.pdMm ? `, PD ${scale.pdMm.toFixed(1)} mm` : ''),
  );

  // ---- 6. uncertainty and quality ----------------------------------------
  const { sigma, observations, parallax } = perVertexUncertainty(state.positions, state.frames, state.intrinsics, mesh);

  // Rescale the formal covariance by the a-posteriori variance factor — the
  // standard photogrammetric correction for a stochastic model that turned out
  // to be wrong. Without it these millimetres scale linearly with whatever the
  // detector layer asserted its own accuracy to be, and nothing ever checked.
  //
  // It is a correction to a PRECISION, not a conversion into an accuracy: see
  // `noseConfidence` for why the corrected number still must not be read as
  // "how wrong the nose is".
  const sigmaScale = Math.sqrt(Math.max(report.varianceFactor, 0) || 1);
  for (let i = 0; i < sigma.length; i++) sigma[i] *= sigmaScale;
  const quality: Record<string, RegionQuality> = {};
  for (const [name, region] of Object.entries(regions)) {
    quality[name] = summarise(region, sigma, observations, parallax);
  }

  // The detector's own offset from the skin, applied as a correction to the
  // reconstructed surface. Zero by default — see the module for why this cannot
  // be solved per wearer.
  //
  // **This is the line that splits the two conventions, so it is worth saying
  // which side each consumer is on.** What leaves here is SKIN, which is what
  // the contact solve, the occluder and the edge snap want — they all touch the
  // wearer. Everything that compares the model against the DETECTOR's own
  // output wants the other surface, and takes it from `landmarkSurface(model)`:
  // the tracker's correspondences and the oval strips. The offset is kept on
  // the model as `landmarkBiasMm` for exactly that.
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
    varianceFactor: report.varianceFactor,
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

/**
 * The span the wearer's PD is compared against: eye-corner midpoint to
 * eye-corner midpoint, in the solved surface.
 *
 * A proxy for pupil centres, and an imperfect one — the medial canthus sits
 * closer to the nose than the visual axis does, so this runs slightly wide of a
 * true interpupillary distance by an amount nobody here has measured on a real
 * face. In the synthetic harness the iris centres are placed on exactly these
 * midpoints, so the proxy is exact by construction and the harness cannot see
 * that bias at all. `docs/OPEN-QUESTIONS.md` Q17.
 */
/**
 * How far the span may sit outside `PD_PLAUSIBLE_MM` and still be reported.
 *
 * Not slack in the plausibility rule — it is the width of a float. The PD
 * correction above rescales the geometry by `knownPdMm / span`, so the span
 * afterwards is the wearer's own figure *recomputed through a `Math.hypot` of
 * three scaled coordinates*, and that round trip lands up to 1.4e-14 mm away
 * from it. At an INCLUSIVE boundary that is enough to flip the verdict, and it
 * flips per face rather than per number: measured over four subjects at seed
 * 11, typing exactly 45.0 gave spans of 44.999999999999993 (refused),
 * 45.000000000000000 (reported) and 45.000000000000007 (reported), and typing
 * exactly 85.0 gave 84.999999999999986 (reported) and 85.000000000000014
 * (refused). 45 and 85 are precisely the two numbers the app's own "outside the
 * human range (45 to 85)" message invites a wearer to type.
 *
 * A nanometre is fourteen orders of magnitude above that round trip and six
 * below the 0.1 mm the readout prints, so it cannot admit anything a person
 * would call out of range.
 */
const PD_ROUNDTRIP_MM = 1e-9;

function interpupillarySpan(positions: Float64Array): number {
  const mid = (a: number, b: number, c: number) =>
    (positions[a * 3 + c] + positions[b * 3 + c]) / 2;
  return Math.hypot(
    mid(LM.EYE_INNER_R, LM.EYE_OUTER_R, 0) - mid(LM.EYE_INNER_L, LM.EYE_OUTER_L, 0),
    mid(LM.EYE_INNER_R, LM.EYE_OUTER_R, 1) - mid(LM.EYE_INNER_L, LM.EYE_OUTER_L, 1),
    mid(LM.EYE_INNER_R, LM.EYE_OUTER_R, 2) - mid(LM.EYE_INNER_L, LM.EYE_OUTER_L, 2),
  );
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
): {
  sigma: Float64Array; observations: Float64Array; parallax: Float64Array;
} {
  const V = mesh.vertexCount;
  const A = new Float64Array(V * 9);
  const observations = new Float64Array(V);
  // Resultant of the unit view directions, per vertex, in FACE space. Parallax
  // is how much those directions SPREAD, and a spread is only visible in the
  // resultant's length — see below.
  //
  // The other quantity that used to be accumulated here — the RMS obliquity of
  // each single view against the face's own +Z, which is what `parallaxRms` was
  // wrongly computing before the two were separated — is gone. Kept for a while
  // as `quality.obliquityRms` on the theory that camera placement predicts
  // reconstruction quality, it measured +0.08 correlation with true nose error
  // against the variance factor's +0.61 and never reached a consumer.
  const viewSum = new Float64Array(V * 3);
  const viewWeight = new Float64Array(V);

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

      // The direction this vertex is being viewed from, as a unit vector in the
      // face's own frame. `cam` is already the vertex in camera space, so the
      // direction back to the camera is `-cam`, rotated into face space by R^T.
      const vx = -(R[0] * cam[0] + R[3] * cam[1] + R[6] * cam[2]);
      const vy = -(R[1] * cam[0] + R[4] * cam[1] + R[7] * cam[2]);
      const vz = -(R[2] * cam[0] + R[5] * cam[1] + R[8] * cam[2]);
      const vl = Math.hypot(vx, vy, vz);
      if (vl > 1e-9) {
        viewSum[i * 3] += (w * vx) / vl;
        viewSum[i * 3 + 1] += (w * vy) / vl;
        viewSum[i * 3 + 2] += (w * vz) / vl;
        viewWeight[i] += w;
      }
    }
  }

  const sigma = new Float64Array(V);
  const parallax = new Float64Array(V);
  const block = new Float64Array(9);
  for (let i = 0; i < V; i++) {
    // **Spread between views, not obliquity of each view.**
    //
    // This was `acos(|dz| / |d|)` per frame — the angle between the view ray and
    // the model's own +Z axis — squared and averaged. That is a property of one
    // view, not a relationship between views, so a completely motionless head in
    // front of a camera sitting below eye level produced a large constant
    // "parallax": measured, 15.9 degrees against a 12 degree threshold, from a
    // still photograph. The term was pinned at 1.0 on every laptop and phone,
    // and the one repair the model could suggest to a wearer — that they had not
    // turned their head enough — was unreachable code.
    //
    // The honest quantity is the angular dispersion of the view directions about
    // their own mean. For unit vectors that is carried entirely by the length of
    // their resultant: R = 1 means every view came from the same direction and
    // there is no parallax at all, however oblique those views were.
    // `2*asin(sqrt((1-R)/2))` is the angle whose chord equals the RMS chord, and
    // it agrees with the RMS angle to first order.
    if (viewWeight[i] > 0) {
      const rx = viewSum[i * 3] / viewWeight[i];
      const ry = viewSum[i * 3 + 1] / viewWeight[i];
      const rz = viewSum[i * 3 + 2] / viewWeight[i];
      const r = Math.min(1, Math.hypot(rx, ry, rz));
      parallax[i] = 2 * Math.asin(Math.min(1, Math.sqrt((1 - r) / 2)));
    } else {
      parallax[i] = 0;
    }
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
  region: Region, sigma: Float64Array, observations: Float64Array,
  parallax: Float64Array,
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
      perRound: [], converged: false, fieldFailures: 0, fieldRmsGauge: 0,
      varianceFactor: 1,
    },
    keyframePoses: [],
    keyframeIndices: [],
  };
}

const nowMs = (): number =>
  (typeof performance !== 'undefined' ? performance.now() : Date.now());
