/**
 * Everything a reviewer needs about the current session, as one object.
 *
 * This exists because asking a wearer to paste a snippet into the DevTools
 * console is a bad instrument. Chrome refuses the paste until they type
 * "allow pasting", the snippet has to be kept in sync with the code by hand, and
 * a wearer reasonably hesitates before pasting code they did not write into a
 * console that is warning them it could be used to steal their identity.
 *
 * A button in the app has none of those problems, cannot drift from the code,
 * and — because the contents are shown right there in the panel — asks nobody to
 * trust anything.
 *
 * **No pixels and no landmarks.** The face model's geometry is summarised to a
 * handful of scalars rather than dumped: the point is to diagnose the pipeline,
 * not to move somebody's facial geometry into a chat window. See
 * `docs/PRIVACY.md`.
 */

import type { FaceModel } from '../core/facemodel.js';
import type { SeatResult } from '../fit/contact.js';
import type { FitAssessment } from '../fit/advice.js';
import { achievedTurnDeg, summarise, type ProtocolState } from '../enroll/protocol.js';
import type { FrameLock } from './framelock.js';
import type { Source } from './sources.js';

export interface DiagnosticsInput {
  phase: string;
  fps: number;
  loopDriver: string;
  backend: string;
  workerAvailable: boolean;
  lock: FrameLock;
  source: Source | null;
  protocol: ProtocolState;
  model: FaceModel | null;
  seat: SeatResult | null;
  assessment: FitAssessment | null;
}

const round = (v: number | null | undefined, dp = 2): number | null =>
  (typeof v === 'number' && Number.isFinite(v) ? +v.toFixed(dp) : null);

export function collectDiagnostics(input: DiagnosticsInput): unknown {
  return {
    when: new Date().toISOString(),

    runtime: {
      phase: input.phase,
      fps: round(input.fps, 0),
      loop: input.loopDriver,
      renderer: input.backend,
      enrollmentWorker: input.workerAvailable,
      userAgent: navigator.userAgent,
    },

    camera: {
      kind: input.source?.kind ?? null,
      label: input.source?.label ?? null,
      width: input.source?.width ?? null,
      height: input.source?.height ?? null,
      brightness0to255: round(input.lock.brightness, 1),
      mirrorDelayMs: round(input.lock.mirrorDelayMs, 0),
      framesDropped: input.source?.droppedFrames ?? null,
    },

    scan: {
      done: input.protocol.done,
      skipped: input.protocol.skipped,
      // The first real calibration data for Q13: how far the wearer got, in the
      // scan's own compressed units.
      achievedDeg: Object.fromEntries(
        Object.entries(input.protocol.achieved).map(([k, v]) => [k, round(v as number, 1)]),
      ),
      turnAchievedDeg: round(achievedTurnDeg(input.protocol), 1),
      neutral: input.protocol.neutral && {
        yawDeg: round(input.protocol.neutral.yawDeg, 1),
        pitchDeg: round(input.protocol.neutral.pitchDeg, 1),
        distanceMm: round(input.protocol.neutral.distanceMm, 0),
      },
      summary: summarise(input.protocol),
    },

    model: input.model && {
      degraded: input.model.degraded,
      notes: input.model.notes,
      framesUsed: input.model.framesUsed,
      solveMs: round(input.model.solveMs, 0),
      reprojectionRmsPx: round(input.model.reprojectionRmsPx),
      // THE number: how well the nose was actually measured. About 0.4 mm means
      // the scan worked; about 4 mm means it did not, and everything downstream
      // is the average face in a costume.
      noseSigmaMm: round(input.model.quality?.nose?.sigmaMm, 2),
      noseParallaxDeg: round(((input.model.quality?.nose?.parallaxRms ?? 0) * 180) / Math.PI, 1),
      noseObservations: round(input.model.quality?.nose?.observations, 1),
      fieldRmsMm: round(input.model.displacementRmsMm),
      scale: input.model.scale,
      intrinsicsSolved: input.model.intrinsicsSolved,
      focalPx: round(input.model.intrinsics.f, 0),
      pdMm: round(input.model.pdMm, 1),
      measurementsMm: Object.fromEntries(
        Object.entries(input.model.measurements).map(([k, v]) => [k, round(v as number)]),
      ),
    },

    seat: input.seat && {
      frame: input.assessment?.frameId ?? null,
      descentMm: round(input.seat.descentMm),
      padDepthErrorMm: round(input.seat.padDepthErrorMm),
      padLoadFraction: round(input.seat.padLoadFraction),
      pantoscopicDeg: round(input.seat.pantoscopicDeg, 1),
      rollDeg: round(input.seat.rollDeg, 2),
      padTiltAdviceDeg: input.seat.padTiltAdviceDeg.map((v) => round(v, 1)),
      terminationReason: input.seat.terminationReason,
      notes: input.seat.notes,
    },

    fit: input.assessment && {
      score: input.assessment.score,
      verdicts: input.assessment.verdicts.map((v) => `[${v.grade}] ${v.label}: ${v.detail}`),
      adjustments: input.assessment.adjustments,
      adviceWithheld: input.assessment.adviceWithheld,
    },
  };
}
