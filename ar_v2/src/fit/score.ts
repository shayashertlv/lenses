/**
 * Turning a solved seat into things a person can act on.
 *
 * This is the layer v1 did not have and could not have had. Its verdicts were
 * comparisons of one estimate against another — its own audit says nine of
 * eleven catalogue frames declared `widthSource: 'assumed'`, so "is this frame
 * the right width for you" was an estimate against a placeholder. With a metric
 * face and metric frame geometry, the same questions have real answers, and
 * some new ones become askable:
 *
 *   - Where will this frame actually sit, and is that where it should?
 *   - Are the pads bearing flush, and if not, by how many degrees are they off?
 *   - Will it slide down? Will it foul the brow? Will it sit crooked?
 *   - Of the whole catalogue, which frames fit *this* face?
 *
 * ## Every verdict carries its own uncertainty
 *
 * A verdict computed from an iris-scaled model is worth less than one scaled by
 * the wearer's own PD, and a verdict about a region the scan barely saw is worth
 * less again. `confidence` is not decoration: the UI shows a verdict with a
 * leading tilde when the number behind it is soft, exactly as v1 learned to do
 * for its width verdict — and unlike v1, the softness is computed from the
 * scan's own covariance rather than from a per-asset flag.
 */

import { clamp } from '../core/linalg.js';
import { LM, type FaceMesh, type Region } from '../core/mesh.js';
import {
  SCALE_DISAGREEMENT_EXPECTED_PCT, noseConfidence, type FaceModel,
} from '../core/facemodel.js';
import {
  PAD_CURVATURE_LIMIT_MM, solveSeat, type SeatResult, TARGET_CONTACT_MM,
} from './contact.js';
import type { FrameAsset } from './frame-asset.js';

export type Grade = 'good' | 'fair' | 'poor' | 'unknown';

export interface FitMeasure {
  /** Stable key, and the weight `scoreOf` looks up. */
  id: string;
  grade: Grade;
  /** 0..1. How much the measurement behind this grade can be trusted. */
  confidence: number;
  /** The measured quantity itself. */
  value: number | null;
  unit: string;
  /**
   * When set, `value` is a DIFFERENCE against this named frame rather than a
   * distance from an absolute target, and it must be read and rendered that
   * way. "4 mm wider than the Navigator" and "4 mm wider than you need" are
   * different sentences and only one of them is exact.
   *
   * This file has made the other version of this mistake once already — see the
   * width block, where one number carried two meanings for a while and printed
   * an overhang with the wrong sign across most of the adult range.
   */
  relativeTo?: string;
}

/**
 * A well-fitting front, as a fraction of the mesh's own temple-landmark span.
 *
 * 0.90, from the trade's rule that a frame should sit inside the widest point of
 * the face rather than level with it, applied to this mesh's landmarks: the
 * template spans 155 mm and normal adult fronts run 135 to 142 mm.
 * `derived`, and it is a ratio rather than a length so it transfers across
 * faces.
 */
export const FRAME_TO_FACE_WIDTH = 0.90;

/**
 * How far the corneal apex sits forward of the plane through the eye corners, mm.
 *
 * Published corneal apex to canthal plane is 11 to 13 mm in adults. `published`.
 */
export const CORNEAL_APEX_MM = 12;

/**
 * How much of the vertex verdict's confidence survives an ASSUMED temple reach.
 *
 * The seat's fore-aft answer is only as good as the ear-rest position it was
 * solved against, and on every parametric frame that position is
 * `FrameSpec.templeReachMm`'s shared 95 mm default, not a measurement. Q16
 * measured the leverage under the shipped wall hook (2026-08-22, 5 seeds x
 * 8 subjects x 5 frames, cross-seed medians): ±5 mm of reach moves the corneal
 * vertex 8.7 -> 16.7 mm — 0.80 mm of vertex per mm of reach, enough on its own
 * to carry the verdict across the entire 12-16 mm band. No other verdict input
 * has that reach (`descentMm` moves too, -0.05 -> 9.33 mm, but its verdict
 * already carries the nose confidence that dominates it).
 *
 * The FACTOR is `stated`; the sensitivity behind it is measured. 0.5 halves
 * the verdict's weight in the score's confidence-shrunk average without
 * pretending the number is worthless — it is still centred on the geometry the
 * asset actually declares. Keyed on `dimensionSource === 'assumed'` exactly as
 * the width verdict's caveat is, because that is the provenance flag the asset
 * pipeline carries. Exported now that its `docs/CONSTANTS.md` row exists —
 * `check-constants.mjs` requires a ledger row for every export, and holding
 * the export back was this docstring's own condition until the row landed.
 */
export const VERTEX_REACH_CONFIDENCE = 0.5;

export interface FitAssessment {
  frameId: string;
  seat: SeatResult;
  /** One graded measurement per criterion. */
  measures: FitMeasure[];
  /** 0..100. A single number for ranking a catalogue. */
  score: number;
}

export function assessFit(
  model: FaceModel, mesh: FaceMesh, regions: Record<string, Region>, frame: FrameAsset,
  cachedSeat?: SeatResult, reference?: FrameAsset,
): FitAssessment {
  const seat = cachedSeat ?? solveSeat(model, mesh, regions, frame);
  const nose = noseConfidence(model);
  const measures: FitMeasure[] = [];

  // ---- width -------------------------------------------------------------
  //
  // Not `frontWidth - templeWidth`. Landmarks 127 and 356 are the widest points
  // of the mesh's own silhouette at brow height, and a well-fitting frame is
  // narrower than that — it sits inside the face's widest point, not level with
  // it. The template measures 155 mm across those landmarks and a
  // normal-fitting adult frame is 135 to 142 mm, so the target ratio is about
  // 0.90. Comparing the two spans directly reported every frame as 29 mm too
  // narrow, which is the shape of error that comes from treating two
  // differently-defined measurements as the same one — the exact trap v1's own
  // bound-transfer rule was written to avoid, applied here in the other
  // direction.
  //
  // **`widthDelta` is a distance from a target, not a distance from the face,
  // and one number did both jobs here once.** The prose read "Overhangs your
  // face by about `widthDelta / 2` mm on each side", which carries a fixed
  // `0.05 * templeWidth` bias — 6.9 to 7.8 mm per side — because the target is
  // 10% inside the face by construction. The SIGN was wrong across most of the
  // adult range: with a 138 mm front, `widthDelta` is positive for every face
  // narrower than 153 mm, so a 148.6 mm wearer was told "Overhangs by about
  // 2 mm" while the rims stopped 5.3 mm short of their temples on each side.
  //
  // The prose verdicts were removed from the UI on 2026-08-25, and the
  // `overhangPerSide` figure that fixed the sentence went unused for a day
  // afterwards — computed every call, read by nothing. It is deleted with this
  // change; the trap it documents is kept, because the next person to put a
  // width sentence in front of a wearer needs it. The real geometry is
  // `(frontWidthMm - templeWidth) / 2`, positive when the rims genuinely stand
  // proud of the widest part of the face — NOT `widthDelta / 2`.
  //
  // **Against a reference frame, this verdict is EXACT.** Scale is a common
  // factor, so it cancels out of a difference between two frames and survives
  // only in an absolute:
  //
  //     widthDelta(A) - widthDelta(B) = W_A - W_B          exact, no scale term
  //     widthDelta(A)                 = W_A - 0.90 x F     carries the whole error
  //
  // Both frames' widths are known to the millimetre. "This pair is 4 mm wider
  // than that one on you" costs nothing; "this pair is 4 mm too wide for you"
  // costs 1.365 mm per point of scale against a 4 mm band, which the iris rung
  // cannot supply and no prop-free ruler can. So when the wearer is looking at
  // a frame, the comparison against THAT frame is offered instead, and it is
  // the only width signal on an iris scan that carries any confidence at all.
  //
  // It answers a different question, and the difference is the point: the
  // absolute form asks "does this fit you", which needs a ruler; the reference
  // form asks "how does this differ from the one in front of you", which does
  // not. Ranking against a reference is a SIMILARITY ordering and does not
  // require the reference to fit — which matters, because the wearer has not
  // said that it does.
  const faceWidth = model.measurements.templeWidth;
  const targetWidth = faceWidth * FRAME_TO_FACE_WIDTH;
  const widthDelta = reference
    ? frame.frontWidthMm - reference.frontWidthMm
    : frame.frontWidthMm - targetWidth;
  measures.push({
    id: 'width',
    // The trade's own tolerance: a front within about 4 mm of the target width
    // reads as well-proportioned; beyond ~10 mm it looks borrowed. The same
    // tolerance applies to a difference from a reference — 4 mm of front width
    // is 4 mm of front width either way.
    grade: gradeBy(Math.abs(widthDelta), 4, 10),
    // Against a reference the scale caveat comes OFF, because the scale is not
    // in the number. What does not come off is the asset provenance, and it now
    // applies to BOTH frames: a difference between two widths is worth what the
    // worse-known of the two is worth.
    //
    // Absolute, it needs the scale caveat more than anything else here and by a
    // wide margin — one point of scale moves it by a third of the whole good
    // band, so on the shipping iris rung a single sigma consumes the band 1.6
    // times over and the verdict shrinks to neutral. That is the measurement,
    // not a regression. `SCALE_SENSITIVITY`.
    confidence: (reference ? 1 : scaleCaveat('width', model))
      * (frame.dimensionSource === 'assumed' ? 0.3 : 1)
      * (reference && reference.dimensionSource === 'assumed' ? 0.3 : 1),
    value: widthDelta,
    unit: 'mm',
    ...(reference ? { relativeTo: reference.name } : {}),
  });

  // ---- where it rests ----------------------------------------------------
  const drop = seat.descentMm;
  measures.push({
    id: 'height',
    grade: gradeBy(Math.abs(drop), 3, 8),
    // Carried no scale caveat at all until it was measured. The frame is a
    // fixed metric object and the face is not, so a rescaled wedge catches it
    // at a different height.
    confidence: nose.value * scaleCaveat('height', model),
    value: drop,
    unit: 'mm',
  });

  // ---- how the pads bear -------------------------------------------------
  //
  // **The same bar `solveSeat` refuses at, and it used to be a different one.**
  // This graded on a bare `1.0` while `solveSeat` fires its "this frame does not
  // suit this face" note at `PAD_CURVATURE_LIMIT_MM = 0.9`, so a residual in
  // (0.9, 1.0] had the seat saying the frame is unwearable and the wearer-facing
  // verdict calling the pads 'good'. Measured over 29 faces x 15 frames (435
  // pairs): 19 land in that band, 13 of them change grade, and 7 of the 13 are
  // navigator — the one asset with author-declared pads, so it is not a
  // synthetic-frame artefact. The worst case in the report's own realisation is
  // `crystal-lenses` on S02: residual 0.9080, tilt 8.4 deg, seat note firing,
  // graded 'good'.
  const tilt = Math.max(seat.padTiltDeg[0], seat.padTiltDeg[1]);
  measures.push({
    id: 'pads',
    grade: seat.padSeatErrorArticulatedMm > PAD_CURVATURE_LIMIT_MM
      ? 'poor'
      : gradeBy(tilt, 10, 25),
    confidence: nose.value * scaleCaveat('pads', model),
    value: tilt,
    unit: 'deg',
  });

  // ---- load --------------------------------------------------------------
  measures.push({
    id: 'load',
    grade: seat.padLoadFraction >= 0.5 && seat.padLoadFraction <= 0.95 ? 'good'
      : seat.padLoadFraction < 0.3 ? 'poor' : 'fair',
    confidence: nose.value * 0.8 * scaleCaveat('load', model),
    value: seat.padLoadFraction * 100,
    unit: '%',
  });

  // ---- pad depth ---------------------------------------------------------
  //
  // A pad buried in the skin is a pressure point, and until now it could not
  // reach a grade. `padDepthErrorMm` appeared exactly once in this file — inside
  // an adjustment string — and had no key in `WEIGHTS`, so a real wearer whose
  // pads bury 1.9 mm was told *"Rests where it should on your nose"* and scored
  // 81. The seat had the number the whole time; nothing carried it to the wearer
  // except prose they had to read past.
  //
  // Signed, and the two signs are different faults: negative buries the pad into
  // the skin, positive leaves it hovering with the frame resting on something
  // else. Graded on magnitude, described by sign.
  const depth = seat.padDepthErrorMm;
  measures.push({
    id: 'depth',
    grade: gradeBy(Math.abs(depth), 1.0, 3.0),
    confidence: nose.value * scaleCaveat('depth', model),
    value: depth,
    unit: 'mm',
  });

  // ---- pantoscopic tilt --------------------------------------------------
  //
  // This was escaping the verdict list entirely. 'Sits level' below grades
  // `|rollDeg|` — rotation in the frontal plane — so a frame with no
  // pantoscopic tilt at all still graded good and said "Sits level", while the
  // adjustment text underneath told the wearer to bend the temples down. Two
  // different axes, and only one of them was being reported.
  //
  // 8 to 12 degrees is what a prescription assumes. Below about 4 the lens is
  // optically wrong for a downward gaze; much above 15 the frame looks tipped
  // and the lower rim reaches the cheek.
  const panto = seat.pantoscopicDeg;
  measures.push({
    id: 'panto',
    grade: panto >= 6 && panto <= 14 ? 'good' : panto >= 3 && panto <= 18 ? 'fair' : 'poor',
    confidence: nose.value * 0.8 * scaleCaveat('panto', model),
    value: panto,
    unit: 'deg',
  });

  // ---- crookedness -------------------------------------------------------
  const roll = Math.abs(seat.rollDeg);
  measures.push({
    id: 'level',
    grade: gradeBy(roll, 1.0, 2.5),
    confidence: nose.value * scaleCaveat('level', model),
    value: roll,
    unit: 'deg',
  });

  // ---- optics ------------------------------------------------------------
  //
  // Vertex distance is measured from the back of the lens to the CORNEA, not to
  // the eye-corner plane the mesh gives. The corneal apex sits about 12 mm
  // forward of the line between the canthi, so the raw seat figure overstates it
  // by that much — which put every frame at 22 to 28 mm and graded them all
  // poor.
  //
  // **And it is withheld outright when the lens centres were not measured.**
  // `frame-asset.ts` and `frame-from-mesh.ts` have both promised this since the
  // derived fallback was written, and neither had a reader: `lensSource` was set
  // and stored and consulted only to build a note string. Where an asset names
  // no lens part, its centres are the extent centre of the frontmost slice of
  // the WHOLE mesh — hinge and forward-temple geometry included — and
  // `contact.ts` computes `vertexDistanceMm` straight off that z. The offset is
  // of the order of the entire 8-to-22 band this grade is read against, so the
  // number is not soft, it is meaningless, and softening it would still leave a
  // wrong millimetre figure on the wearer's screen.
  //
  // `'unknown'` scores exactly neutral in `scoreOf` whatever the confidence, and
  // `ui.ts` renders no row for a null value, so this verdict leaves the ranking
  // and the readout without pretending either way. Two shipped assets take this
  // path — `meshy` and `crystal-parts`, which name no lens — and before the gate
  // existed they showed the wearer a lens distance of -1.42 mm and -1.33 mm,
  // graded 'poor'.
  //
  // **This contains the verdict, not the number.** The same `lensCentres` are
  // still the frame's centre of mass in `contact.ts`'s `comOf`, still set the
  // depth of the clearance ring, and still size the rim in `frame-layout.ts`, so
  // a wrong centre still moves the seat and every verdict downstream of it. That
  // is the derivation's own accuracy, not this gate's job, and it is open.
  //
  // Only `'derived'` is withheld. A parametric frame reports `'constructed'`:
  // its centres are placed by its own spec, so they are exact rather than
  // estimated, and the seat and scale tests are written against those frames
  // precisely because the geometry is known.
  if (frame.lensSource === 'derived') {
    measures.push({ id: 'vertex', grade: 'unknown', confidence: 0, value: null, unit: 'mm' });
  } else {
    const vertex = (seat.vertexDistanceMm[0] + seat.vertexDistanceMm[1]) / 2 - CORNEAL_APEX_MM;
    measures.push({
      id: 'vertex',
      // 12 to 16 mm is the range prescriptions are written for.
      grade: vertex >= 10 && vertex <= 18 ? 'good' : vertex >= 8 && vertex <= 22 ? 'fair' : 'poor',
      // Vertex carries THREE provenance caveats: the scan's scale, the nose, and
      // the asset's temple reach — the fore-aft input Q16 measured as the
      // highest-leverage number in the tree. See VERTEX_REACH_CONFIDENCE.
      //
      // The scale one used to be the same flat multiply width carried, and that
      // was backwards. Measured, vertex is the LEAST scale-sensitive claim in the
      // tree — one point of scale moves it by a fortieth of a millimetre against
      // a band eight millimetres wide — so it now keeps almost all of its
      // confidence where width keeps almost none. What actually threatens this
      // verdict is the reach, and that is the caveat beside it.
      confidence: scaleCaveat('vertex', model) * nose.value *
        (frame.dimensionSource === 'assumed' ? VERTEX_REACH_CONFIDENCE : 1),
      value: vertex,
      unit: 'mm',
    });
  }

  // ---- fouling -----------------------------------------------------------
  if (seat.worstClearanceMm > 0.8) {
    measures.push({
      id: 'clearance',
      grade: seat.worstClearanceMm > 2 ? 'poor' : 'fair',
      confidence: nose.value,
      value: seat.worstClearanceMm,
      unit: 'mm',
    });
  }

  // ---- the honest caveat -------------------------------------------------
  if (model.degraded || model.scale.source === 'assumed') {
    measures.push({
      id: 'caveat',
      grade: 'unknown',
      confidence: 0,
      value: null,
      unit: '',
    });
  }

  return {
    frameId: frame.id,
    seat,
    measures,
    score: scoreOf(measures),
  };
}



// ------------------------------------------------------------------ scoring

const GRADE_POINTS: Record<Grade, number> = { good: 1, fair: 0.55, poor: 0.1, unknown: 0.5 };

/**
 * A single number for ranking a catalogue.
 *
 * Weighted by *what a wearer notices*, which is not the same as what is easy to
 * measure. Where a frame rests and whether the pads bear are the two things a
 * wearer feels within a minute of putting it on; lens distance is invisible
 * until the prescription is wrong.
 *
 * Verdicts with low confidence are pulled toward neutral rather than dropped,
 * so a soft measurement cannot make a frame look good OR bad by being uncertain.
 */
const WEIGHTS: Record<string, number> = {
  height: 3.0,
  pads: 3.0,
  // As heavy as `pads`: a pad buried in the skin is the thing a wearer actually
  // feels, and it was worth nothing at all until it had a verdict to attach to.
  depth: 3.0,
  width: 2.0,
  level: 1.2,
  // Lighter than the rest because it is the most adjustable fault on the list —
  // an optician fixes it by bending the temples, without touching the frame.
  panto: 1.0,
  load: 1.0,
  vertex: 0.8,
  clearance: 1.5,
};

function scoreOf(measures: FitMeasure[]): number {
  let num = 0, den = 0;
  for (const v of measures) {
    const w = WEIGHTS[v.id];
    if (!w) continue;
    const points = GRADE_POINTS[v.grade];
    const c = clamp(v.confidence, 0, 1);
    // Shrink toward neutral in proportion to how little we know.
    num += w * (points * c + 0.5 * (1 - c));
    den += w;
  }
  return den > 0 ? Math.round((num / den) * 100) : 50;
}

const gradeBy = (value: number, goodBelow: number, poorAbove: number): Grade =>
  (value <= goodBelow ? 'good' : value >= poorAbove ? 'poor' : 'fair');

/**
 * The effective one-sigma of this scan's scale, as a fraction.
 *
 * `ScaleEstimate.sigma` alone is **blind to the wearer in front of the camera**.
 * On the iris rung it is `IRIS.sigmaMm / IRIS.defaultMm` to within a fortieth of
 * a percentage point — the frame-to-frame scatter contributes 0.02 pp — so it is
 * the same number for everybody, and a wearer whose true HVID is 11.10 mm
 * carries 5.4% of error at exactly the confidence of one the 11.70 mm assumption
 * fits. That is a population precision doing duty as an individual one, and no
 * amount of vision work changes it: 95.7% of the iris path's error is the HVID
 * assumption itself.
 *
 * The one thing that can see the individual is a **second ruler**. When the
 * wearer supplies their own PD, `enroll` records the signed gap against the
 * ruler it displaced, and anything past what two behaving rulers explain between
 * them is real, unmodelled, and belongs here. Which of the two is wrong is not
 * knowable from inside — so the excess is priced as error rather than used to
 * pick a winner.
 *
 * This is also the only defence against a **mistyped PD**, and it is a needed
 * one. `sigma` on the PD rung is `opticianSigmaMm / knownPdMm`, so a wearer who
 * types a LARGER wrong number prints a SMALLER sigma: measured over 10
 * (seed, subject) pairs, a PD typed 10% high gives a 10.00% scale error at
 * sigma 0.714%, against 0% error at 0.786% when it is right — a wrong scale
 * carried at *higher* confidence than a correct one. At that mistype the iris
 * disagrees by about 10% against a 4.8% expectation, the excess enters here, and
 * the confidence goes to nearly nothing.
 */
function scaleSigma(model: FaceModel): number {
  const gap = model.scale.disagreementPct;
  if (gap == null || !Number.isFinite(gap)) return model.scale.sigma;
  const excess = Math.max(0, Math.abs(gap) / 100 - SCALE_DISAGREEMENT_EXPECTED_PCT / 100);
  return Math.hypot(model.scale.sigma, excess);
}

/**
 * What one percent of scale error does to each verdict, in the verdict's own
 * unit, against the width of the band it is graded against.
 *
 * **The scale caveat used to be a flat multiply on two verdicts, and it was on
 * the wrong two.** `width` and `vertex` carried it and nothing else did — and
 * measured, `vertex` is the LEAST scale-sensitive claim in the tree while
 * `height`, `depth`, `panto` and `load` all move and carried none. A verdict's
 * exposure to scale is not a property of whether somebody remembered it; it is
 * the sensitivity of its own quantity against its own tolerance, and that is a
 * measurement. So it is measured, and the caveat is proportional to it.
 *
 * `perPct` is |d value| per 1% of scale, from the ±1% pair on ground-truth
 * geometry with the factor imposed — the gauge alone, no enrolment. `band` is
 * the width of the good band the verdict is graded inside, so `perPct / band`
 * is the fraction of the verdict's whole tolerance one point of scale consumes.
 *
 * Measured 2026-08-25, 5 campaign seeds x 12 subjects x 15 frames (5 pad
 * geometries x front widths 132/140/148 mm), ground-truth geometry with the
 * factor imposed so this is the gauge alone and no enrolment noise enters.
 * Median of per-seed medians, with the per-seed spread:
 *
 *     measure   per 1%    band    of band   per-seed medians
 *     width     1.365 mm    4 mm   34.1%    1.380 1.391 1.365 1.354 1.340
 *     height    0.249 mm    3 mm    8.3%    0.249 0.268 0.237 0.262 0.135
 *     depth     0.056 mm    1 mm    5.6%    0.058 0.057 0.047 0.056 0.050
 *     panto     0.183 deg   4 deg   4.6%    0.229 0.187 0.163 0.183 0.128
 *     pads      0.223 deg  10 deg   2.2%    0.228 0.211 0.223 0.181 0.224
 *     load      0.482 pp   22.5 pp  2.1%    0.679 0.482 0.415 0.661 0.443
 *     vertex    0.034 mm    4 mm    0.8%    0.060 0.033 0.029 0.034 0.048
 *     level     0.003 deg   1 deg   0.3%    0.002 0.002 0.007 0.003 0.007
 *
 * **Width and vertex differ by a factor of forty and used to carry the same
 * caveat.** At the iris rung's 4.7% one sigma the width verdict's entire
 * tolerance is consumed 1.6 times over, while vertex loses 4% of its
 * confidence — and vertex was the one the tree hedged.
 *
 * Two of these reproduce independent earlier measurements to three digits,
 * which is the only cross-check available: `docs/SCALE.md` 2 has width at
 * 1.37 mm and vertex at 0.035 mm per 1%, from a different frame set. `height`
 * does NOT reproduce — 0.249 here against SCALE.md's 0.19 — and the frame set is
 * why: these fifteen frames span 132 to 148 mm of front width and a wider front
 * puts the ear rest further out, so the seat moves more.
 *
 * Two things this table is not:
 *
 *  - It is not a per-frame figure. The seat is a contact equilibrium, so its
 *    sensitivity depends on where a particular frame catches a particular
 *    sidewall, and the spread across frames is far wider than the spread across
 *    seeds. A few percent of face/frame pairs *jump* between catching the
 *    sidewall and sliding, and for those the movement is several times the
 *    median. These are population figures and the tail is worse.
 *  - It is not a list of everything scale touches. Anything absent from it is
 *    exactly scale-invariant and gets no caveat at all: the face's own
 *    projection, tracking, PnP, and every face-only ratio and angle. That
 *    invariance is real to machine precision and the product should know it.
 */
const SCALE_SENSITIVITY: Record<string, { perPct: number; band: number }> = {
  width: { perPct: 1.365, band: 4 },
  height: { perPct: 0.249, band: 3 },
  depth: { perPct: 0.056, band: 1.0 },
  panto: { perPct: 0.183, band: 4 },
  pads: { perPct: 0.223, band: 10 },
  // Percentage points: the verdict's value is `padLoadFraction * 100` and its
  // good band is 50 to 95, so the half-width is 22.5 pp rather than the 20 the
  // first pass of this measurement used.
  load: { perPct: 0.482, band: 22.5 },
  vertex: { perPct: 0.034, band: 4 },
  // Kept although it rounds to nothing — 0.3% of its band per point of scale.
  // Leaving it out would say the roll of a settled frame is EXACTLY invariant,
  // and it is not: the seat is a contact equilibrium, so a rescaled wedge
  // catches the frame at a slightly different attitude. Nothing in this list is
  // exactly invariant; the things that are (the face's own projection, its
  // ratios, its angles, tracking, PnP) are not verdicts and never appear here.
  level: { perPct: 0.0026, band: 1.0 },
};

/**
 * How much of a verdict survives the scan's scale uncertainty, 0..1.
 *
 * One sigma of scale consumes `perPct * sigmaPct` of the verdict's own
 * tolerance band; what is left of the band is what is left of the verdict. A
 * verdict whose band is entirely consumed is not *wrong*, it is uninformative,
 * and `scoreOf` shrinks it toward neutral rather than dropping it — so an
 * uncertain measurement cannot make a frame look good OR bad by being uncertain.
 */
function scaleCaveat(id: string, model: FaceModel): number {
  const s = SCALE_SENSITIVITY[id];
  // Not in the table: exactly invariant, and free.
  if (!s) return 1;
  // Not a 5% ruler — no ruler. A categorical statement rather than a wide one.
  if (model.scale.source === 'assumed') return 0;
  return clamp(1 - (s.perPct * scaleSigma(model) * 100) / s.band, 0, 1);
}

// ---------------------------------------------------------------- catalogue

export interface RankedFrame {
  frame: FrameAsset;
  assessment: FitAssessment;
}

/**
 * Rank a catalogue for this wearer.
 *
 * The feature that a sticker-based try-on cannot copy: sorting by whether the
 * frame will actually sit right, rather than by style. Every entry carries its
 * own seat solve, so "why is this ranked here" is answerable.
 *
 * ## `reference` — and what it does and does not fix
 *
 * Pass the frame the wearer is looking at and the width verdict becomes a
 * comparison against it, which is EXACT: scale is a common factor and cancels
 * out of a difference between two frames. Measured, 5 seeds x 12 subjects x 15
 * frames (5 pad geometries x front widths 132/140/148 mm), ground-truth
 * geometry with the factor imposed, counting how often the TOP-RANKED frame
 * changes, median-of-seeds:
 *
 *     scale error     +-1%    +-2.5%
 *     absolute        16.7%   41.7% / 50.0%
 *     reference       16.7%   25.0% / 25.0%
 *     width alone, absolute    8.3%    25.0%
 *     width alone, reference   0.0%     0.0%   <- exactly invariant, 0/60 cells
 *
 * **The width channel is fixed completely and the ranking is not, and the
 * reason is a result that goes against the plan this was built from.**
 * `docs/SCALE.md` 5 and `docs/NEXT-SESSION.md` 3B both attribute the ranking's
 * scale sensitivity to this file's fixed metric target, `FRAME_TO_FACE_WIDTH`.
 * It is not that. Measured on the five parametric TEST_FRAMES — the catalogue
 * those documents' own numbers were taken on — dropping the width measure
 * ENTIRELY changes the top-ranked-frame count not at all:
 *
 *     parametric catalogue, top frame changes /60
 *     shipping weights   16 / 10 / 7 / 17   (x0.975 / x0.99 / x1.01 / x1.025)
 *     width dropped      16 / 10 / 7 / 17   identical, cell for cell
 *     width alone         0 /  0 / 0 /  0
 *
 * because every one of those five frames defaults to `frontWidthMm` 138, so the
 * width verdict is byte-identical across the catalogue and orders nothing. What
 * moves the ranking is the SEAT, and the seat is a contact equilibrium: a
 * fixed-size frame lands somewhere else on a wedge that is 1% bigger, and two
 * frames land at two different somewhere-elses. That difference does not
 * cancel, so no reference frame can remove it.
 *
 * `NEXT-SESSION.md`'s gate for this change — "materially fewer than 6/50 and
 * 16/50" — is therefore **met at +-2.5% and not met at +-1%**, and cannot be
 * met at +-1% by this mechanism. What remains is the seat's own scale
 * sensitivity, and the tail of it is the frames that JUMP between catching the
 * sidewall and sliding.
 *
 * It is still worth having, and after the scale caveat became proportional it
 * is worth more than it was: on an iris-scaled scan the absolute width verdict
 * carries essentially zero confidence, so width contributes nothing to the
 * ordering at all. The reference form is what gives it back.
 */
export function rankCatalogue(
  model: FaceModel, mesh: FaceMesh, regions: Record<string, Region>, frames: FrameAsset[],
  reference?: FrameAsset,
): RankedFrame[] {
  return frames
    .map((frame) => ({
      frame, assessment: assessFit(model, mesh, regions, frame, undefined, reference),
    }))
    .sort((a, b) => b.assessment.score - a.assessment.score);
}

export { TARGET_CONTACT_MM, LM };
