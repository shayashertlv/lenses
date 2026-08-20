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
 *  1. **Offers a better ruler.** A standard ID card (ISO 7810 ID-1, 85.60 mm
 *     wide, tolerance ±0.12 mm) held at the brow is an object of known size in
 *     the same image. This is what the incumbent does, and it is worth ±1 mm on
 *     PD against the iris's ±2.5 mm.
 *  2. **Reports the uncertainty instead of hiding it.** `ScaleEstimate.sigma`
 *     travels with the number, the UI shows it, and lens-ordering measurements
 *     refuse an iris-only scale.
 *  3. **Never silently substitutes.** If nothing resolved, the source is
 *     `assumed` and everything downstream that claims millimetres says so.
 */

import { type Intrinsics } from '../core/camera.js';
import { LM } from '../core/mesh.js';
import { type Pose } from '../core/linalg.js';
import { percentile, weightedMedian } from '../core/linalg.js';
import type { ScaleEstimate } from '../core/facemodel.js';

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

/** ISO 7810 ID-1: 85.60 x 53.98 mm, tolerance +/- 0.12 mm on the long edge. */
export const ID1_CARD = { widthMm: 85.60, heightMm: 53.98, toleranceMm: 0.12 } as const;

export const PD_PLAUSIBLE_MM: readonly [number, number] = [46, 80];

// ---------------------------------------------------------------- iris path

export interface IrisReading {
  /** Mean projected iris radius in pixels, over both eyes. */
  radiusPx: number;
  /** Camera-space depth of the iris plane, mm, from the solved pose. */
  depthMm: number;
  /** Pupil separation in pixels. */
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
  /** A card measurement, if one was taken. Overrides the iris. */
  card?: CardReading | null;
}

/**
 * The scale ladder. Best available source wins; the loser is still computed and
 * reported, because a disagreement between two rulers is the most informative
 * thing either of them can say.
 */
export function solveScale(input: ScaleInput): {
  estimate: ScaleEstimate; pdMm: number | null; pdSigmaMm: number | null;
  irisFactor: number | null; disagreementPct: number | null;
} {
  const irisMm = input.irisMm ?? IRIS.defaultMm;
  const irisSigma = input.irisSigmaMm ?? IRIS.sigmaMm;

  // Iris: the physical size a projected radius implies at the solved depth.
  // measured = radiusPx * depth / f. The correction factor is the ratio of the
  // assumed true size to the measured one.
  const factors: number[] = [];
  const pds: number[] = [];
  for (const r of input.readings) {
    const measuredDiameterMm = (2 * r.radiusPx * r.depthMm) / input.intrinsics.f;
    if (!(measuredDiameterMm > 1 && measuredDiameterMm < 40)) continue;
    factors.push(irisMm / measuredDiameterMm);
    if (Number.isFinite(r.pdPx)) {
      pds.push((r.pdPx * r.depthMm) / input.intrinsics.f * (irisMm / measuredDiameterMm));
    }
  }

  const irisFactor = factors.length ? weightedMedian(factors) : null;
  // Scatter of the factor across frames, as a relative sigma, floored by the
  // ruler's own uncertainty. Averaging 40 frames does not make an 11.7 mm
  // assumption more true, and a sigma that shrinks with frame count would say
  // it did — which is the "confidence is a clock" mistake, in a new place.
  const scatter = factors.length > 3
    ? (percentile(factors, 0.84) - percentile(factors, 0.16)) / 2 / Math.max(irisFactor ?? 1, 1e-9)
    : 0.05;
  const irisRelSigma = Math.hypot(irisSigma / irisMm, scatter / Math.sqrt(Math.max(factors.length, 1)));

  let pdMm = pds.length ? weightedMedian(pds) : null;
  if (pdMm !== null && !(pdMm >= PD_PLAUSIBLE_MM[0] && pdMm <= PD_PLAUSIBLE_MM[1])) {
    // Outside the human range means the iris landmarks are wrong — a half-closed
    // eye, a specular highlight — not that the wearer is unusual. Refuse rather
    // than report.
    pdMm = null;
  }

  if (input.card) {
    const cardFactor = solveCardFactor(input.card, input.intrinsics);
    if (cardFactor) {
      const disagreement = irisFactor
        ? ((cardFactor.factor - irisFactor) / irisFactor) * 100
        : null;
      return {
        estimate: {
          source: 'card',
          factor: cardFactor.factor,
          sigma: cardFactor.sigma,
          note: `ISO 7810 card, ${input.card.samples} samples`,
        },
        pdMm: pdMm !== null && irisFactor ? (pdMm / irisFactor) * cardFactor.factor : pdMm,
        pdSigmaMm: pdMm !== null ? pdMm * cardFactor.sigma : null,
        irisFactor,
        disagreementPct: disagreement,
      };
    }
  }

  if (irisFactor !== null) {
    return {
      estimate: {
        source: 'iris',
        factor: irisFactor,
        sigma: irisRelSigma,
        note: `iris at ${irisMm.toFixed(2)} mm assumed, ${factors.length} frames`,
      },
      pdMm,
      pdSigmaMm: pdMm !== null ? pdMm * irisRelSigma : null,
      irisFactor,
      disagreementPct: null,
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
    disagreementPct: null,
  };
}

// ----------------------------------------------------------------- card path

/**
 * A card held in frame, as measured by whatever detects it.
 *
 * The detector is deliberately not in this file. Finding a card's four corners
 * in an image is a solved, self-contained vision problem with several good
 * approaches (quad detection on edges; a small segmentation net), and none of
 * them belong inside a scale solver. `docs/OPEN-QUESTIONS.md` Q3 tracks the
 * decision; until it is answered, `card` is null everywhere and the ladder falls
 * through to the iris, honestly labelled.
 */
export interface CardReading {
  /** Long-edge length in pixels, per sample. */
  longEdgePx: number[];
  /** Camera-space depth the card was held at, mm, per sample. */
  depthMm: number[];
  samples: number;
}

function solveCardFactor(
  card: CardReading, intrinsics: Intrinsics,
): { factor: number; sigma: number } | null {
  const factors: number[] = [];
  for (let i = 0; i < card.longEdgePx.length; i++) {
    const measured = (card.longEdgePx[i] * card.depthMm[i]) / intrinsics.f;
    if (!(measured > 20 && measured < 200)) continue;
    factors.push(ID1_CARD.widthMm / measured);
  }
  if (factors.length < 3) return null;
  const factor = weightedMedian(factors);
  const scatter = (percentile(factors, 0.84) - percentile(factors, 0.16)) / 2;
  return {
    factor,
    sigma: Math.hypot(
      ID1_CARD.toleranceMm / ID1_CARD.widthMm,
      scatter / Math.max(factor, 1e-9) / Math.sqrt(factors.length),
    ),
  };
}

/** Applies a scale factor to a geometry in place, and to the poses that go with it. */
export function applyScale(positions: Float64Array, poses: Pose[], factor: number): void {
  if (!(factor > 0) || Math.abs(factor - 1) < 1e-12) return;
  for (let i = 0; i < positions.length; i++) positions[i] *= factor;
  // Scaling the model without scaling the translations moves the face off its
  // own observations: the projection is invariant only when BOTH scale
  // together. Getting this wrong reads as "the scan is fine but the tracker
  // drifts", which is a long afternoon.
  for (const p of poses) {
    p.t[0] *= factor; p.t[1] *= factor; p.t[2] *= factor;
  }
}
