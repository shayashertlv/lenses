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
 * A verdict computed from an iris-scaled model is worth less than one from a
 * card-scaled model, and a verdict about a region the scan barely saw is worth
 * less again. `confidence` is not decoration: the UI shows a verdict with a
 * leading tilde when the number behind it is soft, exactly as v1 learned to do
 * for its width verdict — and unlike v1, the softness is computed from the
 * scan's own covariance rather than from a per-asset flag.
 */

import { clamp } from '../core/linalg.js';
import { LM, type FaceMesh, type Region } from '../core/mesh.js';
import { noseConfidence, type FaceModel } from '../core/facemodel.js';
import { solveSeat, type SeatResult, TARGET_CONTACT_MM } from './contact.js';
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
  cachedSeat?: SeatResult,
): FitAssessment {
  const seat = cachedSeat ?? solveSeat(model, mesh, regions, frame);
  const nose = noseConfidence(model);
  const scaleConfidence = scaleTrust(model);
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
  // **Two different numbers, and for a while one of them did both jobs.**
  // `widthDelta` is the distance from a *deliberately narrow target*, and it is
  // the right thing to grade on. It is the wrong thing to put in a sentence: the
  // prose read "Overhangs your face by about `widthDelta / 2` mm on each side",
  // which carries a fixed `0.05 * templeWidth` bias — 6.9 to 7.8 mm per side —
  // because the target is 10% inside the face by construction. Worse, the SIGN
  // was wrong across most of the adult range: with the catalogue's 138 mm front,
  // `widthDelta` is positive for every face narrower than 153 mm and clears the
  // 4 mm tolerance — so the overhang sentence actually printed — for every face
  // narrower than 149. A 148.6 mm wearer was told "Overhangs by about 2 mm"
  // when the rims stopped 5.3 mm short of their temples on each side. And the
  // good case said "Sits level with the sides of your face" precisely where the
  // frame is ~7 mm inside them, contradicting the paragraph above it.
  //
  // So the grade stays on `widthDelta` and the sentence is built from
  // `overhangPerSide`, which is the real geometry: positive means the rims
  // genuinely stand proud of the widest part of the face.
  const faceWidth = model.measurements.templeWidth;
  const targetWidth = faceWidth * FRAME_TO_FACE_WIDTH;
  const widthDelta = frame.frontWidthMm - targetWidth;
  const overhangPerSide = (frame.frontWidthMm - faceWidth) / 2;
  // Below half a millimetre `toFixed(0)` prints "about 0 mm past", which is a
  // sentence no human wrote. Level is level.
  const wherePerSide = Math.abs(overhangPerSide) < 0.5
    ? 'level with the widest part of your face'
    : overhangPerSide > 0
      ? `about ${overhangPerSide.toFixed(0)} mm past the widest part of your face on each side`
      : `about ${(-overhangPerSide).toFixed(0)} mm inside the widest part of your face on each side`;
  measures.push({
    id: 'width',
    // The trade's own tolerance: a front within about 4 mm of the target width
    // reads as well-proportioned; beyond ~10 mm it looks borrowed.
    grade: gradeBy(Math.abs(widthDelta), 4, 10),
    // Width is the verdict that most needs the scale caveat, because it is a
    // comparison of two absolute lengths and the frame's own may be assumed.
    confidence: scaleConfidence * (frame.dimensionSource === 'assumed' ? 0.3 : 1),
    value: widthDelta,
    unit: 'mm',
  });

  // ---- where it rests ----------------------------------------------------
  const drop = seat.descentMm;
  measures.push({
    id: 'height',
    grade: gradeBy(Math.abs(drop), 3, 8),
    confidence: nose.value,
    value: drop,
    unit: 'mm',
  });

  // ---- how the pads bear -------------------------------------------------
  const tilt = Math.max(seat.padTiltDeg[0], seat.padTiltDeg[1]);
  measures.push({
    id: 'pads',
    grade: seat.padSeatErrorArticulatedMm > 1.0
      ? 'poor'
      : gradeBy(tilt, 10, 25),
    confidence: nose.value,
    value: tilt,
    unit: 'deg',
  });

  // ---- load --------------------------------------------------------------
  measures.push({
    id: 'load',
    grade: seat.padLoadFraction >= 0.5 && seat.padLoadFraction <= 0.95 ? 'good'
      : seat.padLoadFraction < 0.3 ? 'poor' : 'fair',
    confidence: nose.value * 0.8,
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
    confidence: nose.value,
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
    confidence: nose.value * 0.8,
    value: panto,
    unit: 'deg',
  });

  // ---- crookedness -------------------------------------------------------
  const roll = Math.abs(seat.rollDeg);
  measures.push({
    id: 'level',
    grade: gradeBy(roll, 1.0, 2.5),
    confidence: nose.value,
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
  const vertex = (seat.vertexDistanceMm[0] + seat.vertexDistanceMm[1]) / 2 - CORNEAL_APEX_MM;
  measures.push({
    id: 'vertex',
    // 12 to 16 mm is the range prescriptions are written for.
    grade: vertex >= 10 && vertex <= 18 ? 'good' : vertex >= 8 && vertex <= 22 ? 'fair' : 'poor',
    // Vertex carries THREE provenance caveats, not two: the scan's scale, the
    // nose, and the asset's temple reach — the fore-aft input Q16 measured as
    // the highest-leverage number in the tree. See VERTEX_REACH_CONFIDENCE.
    confidence: scaleConfidence * nose.value *
      (frame.dimensionSource === 'assumed' ? VERTEX_REACH_CONFIDENCE : 1),
    value: vertex,
    unit: 'mm',
  });

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

function scaleTrust(model: FaceModel): number {
  if (model.scale.source === 'assumed') return 0;
  // Sigma is a relative standard deviation. 1% is excellent (a card), 5% is the
  // iris's honest figure once population variation is included.
  return clamp(1 - model.scale.sigma / 0.06, 0, 1);
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
 */
export function rankCatalogue(
  model: FaceModel, mesh: FaceMesh, regions: Record<string, Region>, frames: FrameAsset[],
): RankedFrame[] {
  return frames
    .map((frame) => ({ frame, assessment: assessFit(model, mesh, regions, frame) }))
    .sort((a, b) => b.assessment.score - a.assessment.score);
}

export { TARGET_CONTACT_MM, LM };
