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

export interface Verdict {
  id: string;
  label: string;
  grade: Grade;
  /** One sentence, addressed to the wearer. */
  detail: string;
  /** 0..1. Below `SOFT_VERDICT` the UI marks the number as approximate. */
  confidence: number;
  /** The measured quantity, for the readout. */
  value: number | null;
  unit: string;
}

export const SOFT_VERDICT = 0.6;

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

export interface FitAssessment {
  frameId: string;
  seat: SeatResult;
  verdicts: Verdict[];
  /** 0..100. A single number for ranking a catalogue. */
  score: number;
  /** What an optician would do, in their own language. Empty when nothing needs
   *  doing, which is itself worth saying — and empty when the scan is not good
   *  enough to advise on, in which case `adviceWithheld` says so. */
  adjustments: string[];
  /** Set when advice was suppressed for lack of a good enough scan. */
  adviceWithheld: string | null;
}

export function assessFit(
  model: FaceModel, mesh: FaceMesh, regions: Record<string, Region>, frame: FrameAsset,
  cachedSeat?: SeatResult,
): FitAssessment {
  const seat = cachedSeat ?? solveSeat(model, mesh, regions, frame);
  const nose = noseConfidence(model);
  const scaleConfidence = scaleTrust(model);
  const verdicts: Verdict[] = [];

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
  const faceWidth = model.measurements.templeWidth;
  const targetWidth = faceWidth * FRAME_TO_FACE_WIDTH;
  const widthDelta = frame.frontWidthMm - targetWidth;
  verdicts.push({
    id: 'width',
    label: 'Width',
    // The trade's own tolerance: a front within about 4 mm of the face's own
    // width reads as well-proportioned; beyond ~10 mm it looks borrowed.
    grade: gradeBy(Math.abs(widthDelta), 4, 10),
    detail: Math.abs(widthDelta) <= 4
      ? 'Sits level with the sides of your face.'
      : widthDelta > 0
        ? `Overhangs your face by about ${(widthDelta / 2).toFixed(0)} mm on each side.`
        : `Narrower than your face by about ${(-widthDelta / 2).toFixed(0)} mm on each side.`,
    // Width is the verdict that most needs the scale caveat, because it is a
    // comparison of two absolute lengths and the frame's own may be assumed.
    confidence: scaleConfidence * (frame.dimensionSource === 'assumed' ? 0.3 : 1),
    value: widthDelta,
    unit: 'mm',
  });

  // ---- where it rests ----------------------------------------------------
  const drop = seat.descentMm;
  verdicts.push({
    id: 'height',
    label: 'Where it rests',
    grade: gradeBy(Math.abs(drop), 3, 8),
    detail: Math.abs(drop) <= 3
      ? 'Rests where it should on your nose.'
      : drop > 0
        ? `Slides about ${drop.toFixed(0)} mm down your nose — the pads are wider than your bridge.`
        : `Perches about ${(-drop).toFixed(0)} mm high — the pads are narrower than your bridge.`,
    confidence: nose.value,
    value: drop,
    unit: 'mm',
  });

  // ---- how the pads bear -------------------------------------------------
  const tilt = Math.max(seat.padTiltAdviceDeg[0], seat.padTiltAdviceDeg[1]);
  verdicts.push({
    id: 'pads',
    label: 'Pad contact',
    grade: seat.padSeatErrorArticulatedMm > 1.0
      ? 'poor'
      : gradeBy(tilt, 10, 25),
    detail: seat.padSeatErrorArticulatedMm > 1.0
      ? 'The pads cannot follow the curve of your nose even if bent — this frame\'s bridge is the wrong shape for you.'
      : tilt <= 10
        ? 'The pads lie flat against your nose.'
        : `The pads meet your nose at about ${tilt.toFixed(0)} degrees off — an optician would bend them flat in seconds.`,
    confidence: nose.value,
    value: tilt,
    unit: 'deg',
  });

  // ---- load --------------------------------------------------------------
  verdicts.push({
    id: 'load',
    label: 'Weight distribution',
    grade: seat.padLoadFraction >= 0.5 && seat.padLoadFraction <= 0.95 ? 'good'
      : seat.padLoadFraction < 0.3 ? 'poor' : 'fair',
    detail: seat.padLoadFraction < 0.3
      ? 'Almost all the weight lands on your ears — the pads are too far apart to hold it.'
      : seat.padLoadFraction > 0.95
        ? 'Your nose is carrying essentially all the weight; heavier frames in this shape will mark.'
        : 'The weight is shared sensibly between your nose and your ears.',
    confidence: nose.value * 0.8,
    value: seat.padLoadFraction * 100,
    unit: '%',
  });

  // ---- crookedness -------------------------------------------------------
  const roll = Math.abs(seat.rollDeg);
  verdicts.push({
    id: 'level',
    label: 'Sits level',
    grade: gradeBy(roll, 1.0, 2.5),
    detail: roll <= 1.0
      ? 'Sits level.'
      : `Will sit about ${roll.toFixed(1)} degrees off level — your nose is not quite symmetrical, ` +
        'which is completely normal and is why opticians adjust pads individually.',
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
  verdicts.push({
    id: 'vertex',
    label: 'Lens distance',
    // 12 to 16 mm is the range prescriptions are written for.
    grade: vertex >= 10 && vertex <= 18 ? 'good' : vertex >= 8 && vertex <= 22 ? 'fair' : 'poor',
    detail: `Lenses will sit about ${vertex.toFixed(0)} mm from your eyes` +
      (vertex > 18 ? ' — further than most prescriptions assume, which matters above about 4 dioptres.'
        : vertex < 10 ? ' — close enough that your lashes may touch.' : '.'),
    confidence: scaleConfidence * nose.value,
    value: vertex,
    unit: 'mm',
  });

  // ---- fouling -----------------------------------------------------------
  if (seat.worstClearanceMm > 0.8) {
    verdicts.push({
      id: 'clearance',
      label: 'Clearance',
      grade: seat.worstClearanceMm > 2 ? 'poor' : 'fair',
      detail: `The rims press into your face by about ${seat.worstClearanceMm.toFixed(1)} mm — ` +
        'they will touch your brow or cheeks.',
      confidence: nose.value,
      value: seat.worstClearanceMm,
      unit: 'mm',
    });
  }

  // ---- the honest caveat -------------------------------------------------
  if (model.degraded || model.scale.source === 'assumed') {
    verdicts.push({
      id: 'caveat',
      label: 'About these numbers',
      grade: 'unknown',
      detail: model.scale.source === 'assumed'
        ? 'No absolute measurement was possible, so these are proportions rather than millimetres.'
        : `The scan was incomplete (${model.notes.join('; ')}). A re-scan will sharpen these.`,
      confidence: 0,
      value: null,
      unit: '',
    });
  }

  return {
    frameId: frame.id,
    seat,
    verdicts,
    score: scoreOf(verdicts),
    // Advice is gated on the scan, not on the seat.
    //
    // The verdicts carry their own confidence and the UI marks the soft ones,
    // but an *instruction* has no equivalent hedge — "narrow the pads by 59 mm"
    // reads as a measurement whatever tilde is next to it. Caught by running the
    // app against a still photograph, which cannot turn its head, so the nose
    // was never triangulated (sigma 4.6 mm against the 0.4 mm a real scan gets)
    // and the seat came out 44 mm down the nose. Every verdict correctly said
    // "poor" and the caveat correctly said "re-scan" — and underneath them sat
    // three confident instructions to an optician.
    adjustments: nose.value >= ADVICE_CONFIDENCE && !model.degraded
      ? adjustmentsFor(seat, frame)
      : [],
    adviceWithheld: nose.value < ADVICE_CONFIDENCE || model.degraded
      ? `Not enough of your nose was measured to advise on adjustments — ${nose.reason}.`
      : null,
  };
}

/**
 * How well the nose must be known before the system will tell somebody to bend
 * something, 0..1.
 *
 * Higher than `SOFT_VERDICT`, on purpose. A hedged verdict is still useful
 * information; a hedged instruction is just a wrong instruction with a
 * disclaimer.
 */
export const ADVICE_CONFIDENCE = 0.45;

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
  width: 2.0,
  level: 1.2,
  load: 1.0,
  vertex: 0.8,
  clearance: 1.5,
};

function scoreOf(verdicts: Verdict[]): number {
  let num = 0, den = 0;
  for (const v of verdicts) {
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

// -------------------------------------------------------------- adjustments

/**
 * Instructions in an optician's own language.
 *
 * These are the output that makes the whole exercise more than a mirror, and
 * they are only possible because the geometry is metric and the contact is
 * solved. Deliberately conservative: nothing here suggests an adjustment the
 * measurement cannot support, and each line names the size of the change.
 */
function adjustmentsFor(seat: SeatResult, frame: FrameAsset): string[] {
  const out: string[] = [];

  const tiltR = seat.padTiltAdviceDeg[0];
  const tiltL = seat.padTiltAdviceDeg[1];
  if (Math.max(tiltR, tiltL) > 8) {
    out.push(
      Math.abs(tiltR - tiltL) > 5
        ? `Bend the pad arms to flatten the pads: right ${tiltR.toFixed(0)} deg, left ${tiltL.toFixed(0)} deg.`
        : `Bend both pad arms to flatten the pads by about ${((tiltR + tiltL) / 2).toFixed(0)} deg.`,
    );
  }

  if (seat.descentMm > 3) {
    // Descent per mm of pad separation is measured, not assumed — see the wedge
    // sweep in `testkit/report-seat.ts`. The inverse gives the correction.
    const narrowBy = seat.descentMm / WEDGE_SLOPE_MM_PER_MM;
    out.push(
      `Narrow the pads by about ${narrowBy.toFixed(0)} mm to lift the frame ` +
      `${seat.descentMm.toFixed(0)} mm to where it should sit.`,
    );
  } else if (seat.descentMm < -3) {
    const widenBy = -seat.descentMm / WEDGE_SLOPE_MM_PER_MM;
    out.push(`Widen the pads by about ${widenBy.toFixed(0)} mm to let the frame settle.`);
  }

  if (Math.abs(seat.rollDeg) > 1.5) {
    const side = seat.rollDeg > 0 ? 'right' : 'left';
    out.push(`Raise the ${side} pad slightly to level the frame.`);
  }

  if (seat.padDepthErrorMm < -1.5) {
    out.push('The pads are set too deep for this bridge — bring them forward.');
  }

  if (seat.pantoscopicDeg < 4) {
    out.push(
      `Pantoscopic tilt is ${seat.pantoscopicDeg.toFixed(0)} deg; most prescriptions ` +
      'assume 8 to 10. Bend the temples down at the hinge.',
    );
  }

  return out;
}

/**
 * Millimetres of descent per millimetre of pad separation.
 *
 * **Measured, not derived.** The synthetic seat sweep (`report-seat.ts`) gives
 * 0.74 across the population. The purely geometric prediction from the
 * template's own sidewall angle is about 1.8, and the difference is real and
 * physical: the temple arms take load as the frame descends, so it does not
 * slide as far as the wedge alone would allow. v1 quoted the geometric figure
 * (0.54 mm of half-width per mm of descent, i.e. ~1.85 mm of separation per mm)
 * and had no way to check it.
 */
export const WEDGE_SLOPE_MM_PER_MM = 0.74;

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
