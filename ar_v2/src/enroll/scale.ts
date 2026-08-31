/**
 * Absolute scale — the one thing a single camera genuinely cannot see, and the
 * place v1 has a real, ethnicity-correlated bug.
 *
 * ## The ambiguity
 *
 * A head 5% larger at 5% more distance projects to identical pixels. Nothing in
 * the bundle can break that: it recovers shape and pose *up to one global
 * scale*, anchored only softly by the shape prior pulling toward the template's
 * size. Something outside the projection has to supply a real length.
 *
 * ## The bug in v1, stated plainly
 *
 * `IRIS_DIAMETER_CM = 1.17` — 11.7 mm — is a **white-adult mean**. Published
 * horizontal-visible-iris-diameter means run about 11.10 mm (Japanese),
 * 11.26 mm (Chinese) and 11.75 mm (white adults), with a within-group SD near
 * 0.45 mm. So the constant is not "±0.5 mm of noise" as v1's prose has it: a
 * large part of that spread is **bias**, correlated with ancestry.
 *
 * On a wearer whose true iris is 11.10 mm, a ruler of 11.70 mm reads every
 * length 5.4% long — a 62 mm pupillary distance comes out at 65.3 mm. That is
 * three times Fittingbox's published tolerance for the same measurement, it is
 * systematic rather than noisy so no amount of averaging removes it, and it
 * lands hardest on exactly the populations a mostly-Western test set does not
 * contain.
 *
 * v2 does three things about it:
 *
 *  1. **Offers a better ruler that is not a prop.** The wearer's own PD, from
 *     their prescription — a propagated 0.79% (a pupilometer's 0.5 mm over a
 *     63 mm adult PD) against the iris's propagated 4.70%, which is the figure
 *     that clears the 1.5% the tightest downstream claim needs. Both numbers
 *     are ruler sigmas rather than measured end-to-end scale errors, and the
 *     iris's measured error is larger than its sigma on the temple-width gauge;
 *     `docs/SCALE.md` 4 states the 0.79% without that qualification and should
 *     not be read as a measurement of the PD rung. Applied in `enroll.ts`
 *     against the reconstructed surface, not here. (An ID-1 card was tried and
 *     is gone: `f9c9093` deleted it, and the owner has rejected the method.)
 *  2. **Reports the uncertainty instead of hiding it.** `ScaleEstimate.sigma`
 *     travels with the number, the UI shows it, and lens-ordering measurements
 *     refuse an iris-only scale. What a symmetric sigma cannot report is a
 *     one-sided bias or a ruler that is simply wrong, and for those there is
 *     `ScaleEstimate.disagreementPct` — the gap between two rulers, which is
 *     the only signal in the tree that sees an individual wearer's scale error
 *     rather than the population's.
 *  3. **Never silently substitutes.** If nothing resolved, the source is
 *     `assumed` and everything downstream that claims millimetres says so.
 */

import { type Intrinsics } from '../core/camera.js';
import { LM } from '../core/mesh.js';
import { type Pose } from '../core/linalg.js';
import { percentile, weightedMedian } from '../core/linalg.js';
import type { ScaleEstimate } from '../core/facemodel.js';

// There is no card module. `enroll/card.ts` was deleted in f9c9093 and the
// method is rejected; this file and `enroll.ts` hold the whole ladder.

/**
 * Population mean horizontal visible iris diameter, mm, and its within-group SD.
 *
 * The default is the pooled adult figure this project inherits from MediaPipe's
 * iris work. `POPULATION_HVID` exists so that a deployment which *knows* its
 * audience, or a wearer who volunteers it, can do better — but the default must
 * never pretend to a precision it does not have, hence `sigmaMm` covering the
 * between-group spread rather than only the within-group one.
 */
export const IRIS = {
  defaultMm: 11.7,
  /**
   * One sigma of the default, mm.
   *
   * 0.55 rather than v1's implied 0.5: the within-group SD is ~0.45, and the
   * between-group spread of means (11.10 to 11.75) contributes roughly another
   * 0.27 in quadrature when the wearer's group is unknown. Treating only the
   * within-group figure as the uncertainty is what makes the bias invisible.
   */
  sigmaMm: 0.55,
} as const;

export const POPULATION_HVID: Record<string, { meanMm: number; sigmaMm: number }> = {
  pooled: { meanMm: 11.7, sigmaMm: 0.55 },
  japanese: { meanMm: 11.10, sigmaMm: 0.45 },
  chinese: { meanMm: 11.26, sigmaMm: 0.45 },
  white: { meanMm: 11.75, sigmaMm: 0.45 },
};

/**
 * The span a pupillary distance has to fall inside to be accepted as a ruler
 * **or** reported as a measurement — one range, because these used to be two.
 *
 * The acceptance gate in `enroll` was `[45, 85]` (matching the app's `set-pd`
 * handler and its stored-value reader) and the readout gate here was
 * `[46, 80]`. That gap is not academic: the PD correction rescales the whole
 * geometry so that `interpupillarySpan` EQUALS the wearer's figure by
 * construction, so on `[45, 46) u (80, 85]` a scan was resized by the wearer's
 * own typed number, every millimetre downstream with it, and then refused to
 * print that number back — with the note *"the measured eye span is outside
 * the human range, so something in the eye landmarks or the scale is wrong"*,
 * about a number the wearer had typed. Measured against `dist/` at the time:
 * 45.0 gave `source: 'pd'`, `factor: 1.0166`, `pdMm: null`; 46.0 gave the same
 * source and `pdMm: 46.000`.
 *
 * 45 to 85 is the range the app already promises in its own error message, and
 * the readout has no business being narrower than the ruler. Both ends stay
 * wider than the adult range on purpose: the job is to refuse a *measurement
 * failure* — a half-closed eye, a specular highlight — not to police anybody's
 * face.
 */
export const PD_PLAUSIBLE_MM: readonly [number, number] = [45, 85];

// ---------------------------------------------------------------- iris path

export interface IrisReading {
  /** Mean projected iris radius in pixels, over both eyes. */
  radiusPx: number;
  /** Camera-space depth of the iris plane, mm, from the solved pose. */
  depthMm: number;
  /**
   * Pupil separation in pixels.
   *
   * Reported, and deliberately not used as a ruler. It is an IMAGE length, so it
   * foreshortens with yaw exactly as the mean iris radius does not — see the
   * note in `solveScale` for the measured cost of forgetting that.
   */
  pdPx: number;
}

/**
 * Reads one frame's irises.
 *
 * The *mean* radius over all four contour points per eye, not a nominated
 * horizontal pair. MediaPipe's contour ordering is undocumented and has no
 * reason to stay fixed, and — more importantly — the mean radius of a projected
 * circle is nearly invariant to the viewing angle, while a nominated horizontal
 * diameter foreshortens with yaw. That invariance is the entire reason the iris
 * is usable as a ruler at all, and it is destroyed by picking two points.
 *
 * Returns null rather than a guess when the iris is too small to measure. Six
 * pixels of diameter is where quantisation exceeds the signal.
 *
 * **There is deliberately no yaw gate here, and that was tested rather than
 * assumed.** This paragraph used to end "or when the head is turned far enough
 * that the disc is no longer facing the camera. See `IRIS_MAX_YAW_DEG`" — a
 * constant that exists nowhere in this repository, sitting in a second,
 * orphaned docstring directly above the one that says the gate was measured and
 * refused.
 *
 * A review flagged this function for accepting a reading at any head angle: no
 * yaw gate, no visibility gate, no eye-openness gate, while measured keyframe
 * yaw runs to a p90 of 72 degrees and a max of 86. The concern is sound in
 * principle — the ruler rests on the iris disc facing the camera, and what
 * really holds it there is not the mean-radius argument in the header above but
 * the eye counter-rotating in its orbit to keep fixation, which runs out at the
 * edge of the oculomotor range around 50 degrees.
 *
 * Modelling the iris faithfully in the harness (as a real disc that turns with
 * the head once fixation runs out, rather than a circle drawn in image space)
 * makes the effect visible for the first time: the reading is flat to within
 * 2.5% out to 60 degrees, then −5.6% at 60–70, −7.6% at 70–80 and −10.4% at
 * 80–90, while iris visibility falls from 0.69 to 0.13.
 *
 * So a gate at 55 degrees was written. Measured against ground truth over ten
 * subjects, it made the scale **worse**:
 *
 *     gate at 55 deg   mean |scale error| 0.68%   median 0.62%   worst 0.90%
 *     no gate          mean |scale error| 0.47%   median 0.36%   worst 0.74%
 *
 * `solveScale` takes a median over every keyframe, and a median is already
 * robust to a biased minority; throwing away a third of the sample costs more
 * stability than the biased tail costs accuracy. The gate was removed.
 *
 * Recorded at this length because the next reviewer will notice the same missing
 * gate and reach for the same fix.
 *
 * **Re-measured 2026-08-31, because the harness that decided it was flattering
 * the iris.** `testkit/synthetic.ts` wrote occluded iris contour points as exact
 * projections of the subject's own disc with the tightest sigma in the frame,
 * exempt from the occlusion model it applies to every other landmark — so the
 * far iris at profile, which is the whole case a yaw gate exists for, was
 * bit-true. That is fixed: an occluded iris is now pulled toward the detector's
 * prior, a population-mean disc on the average face, and its sigma inflates the
 * same way the mesh loop's does.
 *
 * The verdict holds. Same probe on both harnesses, median over the campaign
 * seeds of |implied iris diameter / truth - 1|:
 *
 *     exempt harness   ungated 0.213%   gated at 55 deg 0.202%   gate wins 3/5
 *     honest harness   ungated 0.260%   gated at 55 deg 0.231%   gate wins 3/5
 *
 * 3 of 5 is under this tree's 4-of-5 adoption rule, so the gate stays out on
 * evidence that now survives the harness it was taken on — and read those four
 * digits against the instrument's own floor. `radius()` above averages
 * |contour - centre|, a NORM, so independent noise on the centre and the rim
 * biases the radius upward by about `sigma^2 / (2R)`: +0.29% at eye-level
 * geometry, where the iris projects to only ~7 px of radius, falling to +0.04%
 * on the phone-lap rung where it projects to ~18 px. Every figure in the table
 * sits at or below that floor and the 0.029 pp between the two arms is a tenth
 * of it. The floor is common to both harnesses and both arms, so it cannot flip
 * the verdict; what it means is that the gate is not measurably better rather
 * than measurably worse. The honest iris costs
 * the ruler about a fifth of its accuracy (0.213% to 0.260%) and the shipped
 * enrolment about 4% of its scale error (1.79% to 1.86%) — the harness was
 * kinder than reality, but not by enough to have changed this decision.
 */
export function readIris(
  landmarks: Float64Array, pose: Pose, positions: Float64Array,
): IrisReading | null {
  const radius = (centre: number, contour: readonly number[]): number => {
    const cx = landmarks[centre * 2], cy = landmarks[centre * 2 + 1];
    if (Number.isNaN(cx)) return NaN;
    let sum = 0, n = 0;
    for (const i of contour) {
      const x = landmarks[i * 2], y = landmarks[i * 2 + 1];
      if (Number.isNaN(x)) continue;
      sum += Math.hypot(x - cx, y - cy);
      n++;
    }
    return n ? sum / n : NaN;
  };

  const rr = radius(LM.IRIS_R_CENTRE, LM.IRIS_R_CONTOUR);
  const rl = radius(LM.IRIS_L_CENTRE, LM.IRIS_L_CONTOUR);
  const radii = [rr, rl].filter((r) => Number.isFinite(r) && r > 0);
  if (radii.length === 0) return null;
  const radiusPx = radii.reduce((a, b) => a + b, 0) / radii.length;
  if (radiusPx < 3) return null;

  // Depth of the iris plane: the inner eye corners' own camera depth, from the
  // solved pose and the solved geometry. This is the step that makes the whole
  // thing self-consistent — the ruler is read at the depth the bundle says the
  // eyes are at, not at an assumed distance.
  const depthOf = (i: number): number => {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    const R = pose.R;
    return R[6] * x + R[7] * y + R[8] * z + pose.t[2];
  };
  const depthMm = (depthOf(LM.EYE_INNER_R) + depthOf(LM.EYE_INNER_L)) / 2;
  if (!(depthMm > 1)) return null;

  const rx = landmarks[LM.IRIS_R_CENTRE * 2], ry = landmarks[LM.IRIS_R_CENTRE * 2 + 1];
  const lx = landmarks[LM.IRIS_L_CENTRE * 2], ly = landmarks[LM.IRIS_L_CENTRE * 2 + 1];
  const pdPx = Number.isNaN(rx) || Number.isNaN(lx) ? NaN : Math.hypot(lx - rx, ly - ry);

  return { radiusPx, depthMm, pdPx };
}

export interface ScaleInput {
  readings: IrisReading[];
  intrinsics: Intrinsics;
  /** Assumed true iris diameter and its sigma. */
  irisMm?: number;
  irisSigmaMm?: number;
}

/**
 * The wearer's own PD as a ruler.
 *
 * The iris is the ruler of last resort: it works on anybody, needs no props and
 * no cooperation, and it is *assumed* rather than measured — 11.70 mm pooled,
 * with a one-sigma of 0.55 mm covering both the within-group spread and the gap
 * between population means. That is 4.7%, and measured across a synthetic
 * population the shipping iris path produces a worst-case scale error of
 * **14.5%** — this line said 10% until five seeds were run, and 10% turns out
 * to have been one draw; the worst cell runs 10.28 to 14.50% across the seeds.
 * A real wearer's five scans disagreed with each other by up to 1.9%, which is
 * the ruler's *precision*; its accuracy is unmeasurable without a second one.
 *
 * A pupillary distance from a spectacle prescription is that second ruler, and
 * it is nearly free: it was measured on this wearer by an optician with a
 * pupilometer, most people who wear glasses have it written down, and it costs
 * a text field rather than a computer-vision subsystem.
 *
 * **There is no rung above this one.** An ID-1 card was built, measured and
 * deleted (`f9c9093`), and the method is rejected. Measured since, across 5
 * seeds: 95.7% of the iris path's error is the population HVID assumption,
 * perfect vision would buy 0.11-1.47 percentage points of a 14.5% worst case,
 * and every other physically admissible prop-free signal is dead — autofocus on
 * depth of field (+-47% of Z), WebXR on reach, the corneal glint on the same
 * ancestry-correlated constant this file refuses, rolling shutter on physics.
 * Device motion reaches 2.65% only in simulation and carries a structural bias:
 * the head recoils against the arm that thrusts the phone. `docs/SCALE.md`.
 *
 * **It is applied in `enroll`, against the reconstructed 3-D geometry, and not
 * here.** The obvious place is this file, next to the iris, using the `pdPx`
 * that `readIris` already returns — and that is wrong. `pdPx` is an image
 * distance between two pupils, so it foreshortens with yaw exactly as the iris
 * radius does not; this file's own header says so: *"the mean radius of a
 * projected circle is nearly invariant to the viewing angle, while a nominated
 * horizontal diameter foreshortens"*. Measured, the image-space version made the
 * scale worse than the assumption it replaced — 4.39% median error against
 * 6.35% — while reporting a confident 0.93% sigma. Wrong and confident is the
 * one combination worth going out of the way to avoid.
 */
export const PD_RULER = {
  /** A pupilometer in an optician's hands, one sigma, mm. */
  opticianSigmaMm: 0.5,
} as const;

/**
 * The scale ladder. Best available source wins; the loser is still computed and
 * reported, because a disagreement between two rulers is the most informative
 * thing either of them can say.
 */
export function solveScale(input: ScaleInput): {
  estimate: ScaleEstimate;
  /** Always null out of this function. `enroll` fills them in from the scaled
   *  3-D surface; the fields stay on the shape so there is one place a PD is
   *  carried rather than two. See the note in the body. */
  pdMm: number | null; pdSigmaMm: number | null;
  irisFactor: number | null;
} {
  const irisMm = input.irisMm ?? IRIS.defaultMm;
  const irisSigma = input.irisSigmaMm ?? IRIS.sigmaMm;

  // Iris: the physical size a projected radius implies at the solved depth.
  // measured = radiusPx * depth / f. The correction factor is the ratio of the
  // assumed true size to the measured one.
  const factors: number[] = [];
  for (const r of input.readings) {
    const measuredDiameterMm = (2 * r.radiusPx * r.depthMm) / input.intrinsics.f;
    if (!(measuredDiameterMm > 1 && measuredDiameterMm < 40)) continue;
    factors.push(irisMm / measuredDiameterMm);
  }

  const irisFactor = factors.length ? weightedMedian(factors) : null;
  // Scatter of the factor across frames, as a relative sigma, floored by the
  // ruler's own uncertainty. Averaging 40 frames does not make an 11.7 mm
  // assumption more true, and a sigma that shrinks with frame count would say
  // it did — which is the "confidence is a clock" mistake, in a new place.
  //
  // **What this number is, and what it is not — because a doc got it wrong.**
  //
  // It prints 4.72% and the first term is 0.55/11.70 = 4.7009%, a floor `hypot`
  // can only add to; the scatter term contributes 0.02 pp at the shipped
  // keyframe count. So the printed sigma is the HVID constant's population
  // one-sigma and nothing else, and no amount of vision work moves it.
  //
  // `docs/SCALE.md` 2 and `docs/NEXT-SESSION.md` 3A both say it is under-reported
  // — "prints 4.72% while its p90 implies 5.72% and its median implies 7.68%".
  // Re-checked against the probe those came from, the claim does not survive
  // this tree's own adoption rule:
  //
  //   - The pair was computed POOLED over 150 rows. Per seed, the median route
  //     implies 4.02/8.58/5.28/8.41/5.28 and the p90 route 7.73/4.30/5.11/5.32/
  //     5.88 — 7.68% reproduces on 2 of 5 seeds and 5.72% on 2 of 5, and the
  //     SIGN of the gap between the two routes flips across seeds. Median of
  //     seeds, the two routes agree: 5.28% and 5.32%.
  //   - 20% of those rows are not draws. `generatePopulation(count: N)` appends
  //     two named extremes with irises hard-coded at 11.10 and 11.90 mm,
  //     identical in every seed, so 15 rows sit at exactly +5.41% — on top of
  //     the measured median, contributing nothing to the tail.
  //   - It is scored on TEMPLE WIDTH (`testkit/metrics.ts`'s `scaleErrorPct`),
  //     the span furthest from where the iris is read, where the pipeline
  //     residual is sd 2.94% against 0.89% on the eye span. Raising this
  //     constant to 7.68% would bake the bundle's temple-region shape recovery
  //     into a ruler's uncertainty.
  //   - On the whole-mesh gauge over 255 runs the same printed 4.72% has
  //     |error|/sigma median 0.65 and p90 1.72 — implying 4.52% and 4.96%, i.e.
  //     bracketing it. A separate adversarial pass measured the fraction of runs
  //     exceeding the claimed one-sigma at 0.300 against a Gaussian's 0.317.
  //
  // So it is left alone. What is genuinely missing from it is not size but
  // SHAPE: the error is one-sided (+2.59% signed, 67% of wearers read large,
  // because 11.70 sits ~2.2% above the generated population's mean) and a
  // symmetric sigma cannot say so. That is reported through
  // `ScaleEstimate.disagreementPct` instead, which is signed — see the field.
  const scatter = factors.length > 3
    ? (percentile(factors, 0.84) - percentile(factors, 0.16)) / 2 / Math.max(irisFactor ?? 1, 1e-9)
    : 0.05;
  const irisRelSigma = Math.hypot(irisSigma / irisMm, scatter / Math.sqrt(Math.max(factors.length, 1)));

  // **No pupillary distance comes out of this function, and that is the fix to a
  // real one.**
  //
  // It used to take the median of `pdPx * depth / f * (irisMm / measuredDiameter)`
  // over the frames, which looks like a triangulation and is not one. Substitute
  // `measuredDiameter = 2 * radiusPx * depth / f` and both the depth and the
  // focal length cancel *exactly*, leaving `irisMm * pdPx / (2 * radiusPx)` — a
  // ratio of two IMAGE lengths and a constant. The mean iris radius is nearly
  // invariant to viewing angle; a pupil separation is not, so the quotient
  // foreshortens with yaw, and the median is taken over a scan that spends most
  // of its frames deliberately asking the wearer to turn.
  //
  // Every subject read low. Mean error -3.93 mm even with the true iris diameter
  // supplied; worst case a 62.5 mm PD reported as 56.9, beside a printed sigma
  // of about 2.7 mm that does not cover it. The harm is not cosmetic: the readout
  // is the number a wearer copies into `set-pd`, at which point a foreshortened
  // guess becomes the ruler for everything else.
  //
  // The PD is measured in `enroll` instead, off the scaled 3-D surface, where the
  // bundle has already divided head angle out: +0.78 mm mean with the wearer's
  // true iris supplied as the ruler, against -3.93 here, and the residual is the
  // scale's rather than the projection's. `PD_PLAUSIBLE_MM` still refuses an
  // implausible span; it is applied there, at the point where the number is made.

  if (irisFactor !== null) {
    return {
      estimate: {
        source: 'iris',
        factor: irisFactor,
        sigma: irisRelSigma,
        note: `iris at ${irisMm.toFixed(2)} mm assumed, ${factors.length} frames`,
      },
      pdMm: null,
      pdSigmaMm: null,
      irisFactor,
    };
  }

  return {
    estimate: {
      source: 'assumed',
      factor: 1,
      // The template's own size against the adult population's: face width CV
      // is about 5%, so an unmeasured face is one sigma of 5% wrong at best.
      sigma: 0.05,
      note: 'no ruler resolved — sizes are the template\'s, not this wearer\'s',
    },
    pdMm: null,
    pdSigmaMm: null,
    irisFactor: null,
  };
}

/** Applies a scale factor to a geometry in place, and to the poses that go with it. */
export function applyScale(
  positions: Float64Array, poses: Pose[], factor: number,
  field?: { values: Float64Array } | null,
): void {
  if (!(factor > 0) || Math.abs(factor - 1) < 1e-12) return;
  for (let i = 0; i < positions.length; i++) positions[i] *= factor;
  // Scaling the model without scaling the translations moves the face off its
  // own observations: the projection is invariant only when BOTH scale
  // together. Getting this wrong reads as "the scan is fine but the tracker
  // drifts", which is a long afternoon.
  for (const p of poses) {
    p.t[0] *= factor; p.t[1] *= factor; p.t[2] *= factor;
  }
  // The displacement field is a length too, and it was the one thing here that
  // did not scale. It is measured along vertex normals in the bundle's own
  // arbitrary units, so leaving it behind meant `displacementRmsMm` was in
  // pre-scale units while carrying an `Mm` suffix — a real wearer's dump read
  // 0.74 when the field was actually 1.09 mm, understating it by the gauge
  // factor. Everything with length units scales together, and this function is
  // where that invariant lives.
  if (field) {
    for (let i = 0; i < field.values.length; i++) field.values[i] *= factor;
  }
}
