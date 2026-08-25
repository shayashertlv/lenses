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
import type { FaceMeasurements } from '../core/mesh.js';
import { percentile } from '../core/linalg.js';
import type { SeatResult } from '../fit/contact.js';
import type { FitAssessment } from '../fit/score.js';
import { achievedTurnDeg, summarise, type ProtocolState } from '../enroll/protocol.js';
import { LATCH_EXIT_RATIO, type TrackerState } from '../track/tracker.js';
import type { FrameLock } from './framelock.js';
import type { Source } from './sources.js';

export interface DiagnosticsInput {
  phase: string;
  fps: number;
  loopDriver: string;
  backend: string;
  /** Whether a live enrollment worker exists right now. */
  workerAvailable: boolean;
  /** Where the last solve actually ran; null before the first one. */
  solvedOn: 'worker' | 'main' | null;
  lock: FrameLock;
  source: Source | null;
  protocol: ProtocolState;
  model: FaceModel | null;
  seat: SeatResult | null;
  assessment: FitAssessment | null;
  /** The Steady button's live mode, in its own words. */
  steady: 'off' | 'on' | 'adaptive' | 'locked';
  /** The live tracker, read for its latch/audit counters only. */
  tracker: TrackerState | null;
  /** Whether the rank-4 constant-velocity MAP prior is fused into the solve
   *  (`?prior=off` turns it off) — the A/B arm this paste describes. */
  motionPrior: boolean;
  /** Whether rank 6's oval landmark marching is on (off by default;
   *  `?march=on` enables). */
  marchOval: boolean;
  /** Rolling per-frame readouts from the last ~10 s of real solves. */
  recentTrack: {
    velMmS: number; velDegS: number; sigmaMm: number; sigmaDeg: number;
    noiseVelMmS: number; noiseVelDegS: number;
    priorShareRot: number; priorShareMm: number; varianceFactor: number;
    latched: boolean; fading: boolean; lagMm: number;
  }[];
  /** Detector inference times over the same window, ms. */
  recentDetectMs: number[];
}

const round = (v: number | null | undefined, dp = 2): number | null =>
  (typeof v === 'number' && Number.isFinite(v) ? +v.toFixed(dp) : null);

/**
 * The `FaceMeasurements` keys that are ANGLES, in radians.
 *
 * They used to be reported inside `measurementsMm` along with everything else,
 * so a dump showed `sidewallAngle: 0.27` under a millimetre heading — which
 * reads as a quarter-millimetre nose wall rather than as the 15.7-degree wedge
 * it is on the template, and is the kind of unit slip that survives review
 * because the number is plausible read either way. Angles get their own key and
 * are converted at this reporting boundary only: `FaceMeasurements` stays in
 * radians because everything downstream of it does trigonometry.
 *
 * Listed explicitly rather than sniffed from the name, so that adding an angular
 * measurement to `core/mesh.ts` and forgetting this line is a missing key rather
 * than a wrong unit.
 */
const ANGULAR_MEASUREMENTS: readonly (keyof FaceMeasurements)[] = ['sidewallAngle'];

const DEG = 180 / Math.PI;

export function collectDiagnostics(input: DiagnosticsInput): unknown {
  return {
    when: new Date().toISOString(),

    runtime: {
      phase: input.phase,
      fps: round(input.fps, 0),
      loop: input.loopDriver,
      renderer: input.backend,
      enrollmentWorker: input.workerAvailable,
      // `enrollmentWorker` is a prediction about the next solve; this is a
      // record of the last one. 'main' beside a `true` above means the worker is
      // alive and the solve fell back anyway, which is a different fault from
      // having no worker at all and used to be unreportable.
      enrollmentSolvedOn: input.solvedOn,
      userAgent: navigator.userAgent,
    },

    camera: {
      kind: input.source?.kind ?? null,
      label: input.source?.label ?? null,
      width: input.source?.width ?? null,
      height: input.source?.height ?? null,
      brightness0to255: round(input.lock.brightness, 1),
      mirrorDelayMs: round(input.lock.mirrorDelayMs, 0),
      // The detector runs synchronously inside the frame callback, so this is
      // a lower bound on how much of `mirrorDelayMs` is inference rather than
      // anything the tracker did. Split out because a delay report that
      // cannot separate the two cannot be acted on.
      detectMs: (() => {
        const v = input.recentDetectMs.filter((x) => Number.isFinite(x));
        return v.length
          ? { p50: round(percentile(v, 0.5), 1), p90: round(percentile(v, 0.9), 1), max: round(Math.max(...v), 1) }
          : null;
      })(),
      framesDropped: input.source?.droppedFrames ?? null,
    },

    // The scan that produced the CURRENT MODEL, falling back to the live
    // protocol only while a scan is actually running.
    //
    // Reading the live protocol unconditionally is what made this block useless:
    // a stored model restores straight into `wear` with a freshly-created
    // protocol beside it, so a real wearer's dump reported "0 of 7 done" next to
    // a model built from 48 frames — and the one number needed to calibrate the
    // yaw compression (Q13) was gone before they could send it.
    scan: (() => {
      const r = input.model?.scan;
      const source = r ? 'the scan that built this model' : 'live (no scan recorded)';
      const done = r ? r.done : input.protocol.done;
      const skipped = r ? r.skipped : input.protocol.skipped;
      const achieved = r ? r.achieved : input.protocol.achieved;
      const neutral = r ? r.neutral : input.protocol.neutral;
      return {
        source,
        done,
        skipped,
        achievedDeg: Object.fromEntries(
          Object.entries(achieved).map(([k, v]) => [k, round(v as number, 1)]),
        ),
        turnAchievedDeg: round(r ? r.turnAchievedDeg : achievedTurnDeg(input.protocol), 1),
        neutral: neutral && {
          yawDeg: round(neutral.yawDeg, 1),
          pitchDeg: round(neutral.pitchDeg, 1),
          distanceMm: round(neutral.distanceMm, 0),
        },
        summary: r ? r.summary : summarise(input.protocol),
      };
    })(),

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
        Object.entries(input.model.measurements)
          .filter(([k]) => !ANGULAR_MEASUREMENTS.includes(k as keyof FaceMeasurements))
          .map(([k, v]) => [k, round(v as number)]),
      ),
      measurementsDeg: Object.fromEntries(
        Object.entries(input.model.measurements)
          .filter(([k]) => ANGULAR_MEASUREMENTS.includes(k as keyof FaceMeasurements))
          .map(([k, v]) => [k, round((v as number) * DEG, 1)]),
      ),
    },

    // The stillness-latch instrument. `velRecent` is the windowed velocity of
    // the wearer's OWN raw pose stream over the last ~10 s — the exact signal
    // the latch gates on — so one paste says where their face sits against
    // LATCH_ENTER/EXIT and whether the thresholds fit them or need to move.
    tracking: (() => {
      const t = input.tracker;
      const ring = input.recentTrack;
      const finite = (
        k: 'velMmS' | 'velDegS' | 'lagMm' | 'sigmaMm' | 'sigmaDeg' | 'noiseVelMmS'
        | 'noiseVelDegS' | 'priorShareRot' | 'priorShareMm' | 'varianceFactor',
      ) => ring.map((r) => r[k]).filter((v) => Number.isFinite(v));
      const dist = (vals: number[]) => (vals.length ? {
        p50: round(percentile(vals, 0.5)),
        p90: round(percentile(vals, 0.9)),
        max: round(Math.max(...vals)),
      } : null);
      const pct = (k: 'latched' | 'fading') =>
        (ring.length ? round((100 * ring.filter((r) => r[k]).length) / ring.length, 1) : null);
      return {
        steady: input.steady,
        // Which A/B arm this paste is. Ambiguity here has cost a session
        // before — a "jitteriness is back" report turned out to be the
        // Steady cycle overshooting to 'off' with no code changed.
        motionPrior: input.motionPrior,
        marchOval: input.marchOval,
        latch: t && {
          engages: t.latchEngages,
          releasesVelocity: t.latchReleases,
          reanchorsDrift: t.latchReanchors,
          // Session-cumulative, so the paste keeps its answer even after
          // the interesting seconds roll out of the 10 s `recent` ring:
          // latchedFrames / engages is the average latch spell in frames.
          latchedFrames: t.latchedFrames,
        },
        // The gates the latch is actually running: per-session learned rest
        // floors where the session has taught them, shipped priors where it
        // has not. `learned` false with noisy rest means the latch has not
        // held long enough to sample it yet.
        gates: t && {
          enterMmS: round(t.latchEnterMmS),
          enterDegS: round(t.latchEnterDegS),
          exitMmS: round(t.latchEnterMmS * LATCH_EXIT_RATIO),
          exitDegS: round(t.latchEnterDegS * LATCH_EXIT_RATIO),
          learned: { mm: t.floorMm !== null, deg: t.floorDeg !== null },
          // The floor estimators themselves — the session's cumulative rest
          // record (mean and absolute deviation of latched-frame windowed
          // velocities, per channel). Unlike `recent`, these cannot roll
          // over: they ARE what this session learned rest looks like.
          restFloor: {
            mmS: t.floorMm && { m: round(t.floorMm.m), d: round(t.floorMm.d) },
            degS: t.floorDeg && { m: round(t.floorDeg.m), d: round(t.floorDeg.d) },
          },
        },
        basinAudit: t && {
          run: t.basinAuditsRun,
          adopted: t.basinEscapes,
          skippedDeadband: t.basinAdoptionsSkipped,
        },
        acquisitions: t?.acquisitions ?? null,
        framesTracked: t?.framesTracked ?? null,
        recent: ring.length ? {
          frames: ring.length,
          velMmS: dist(finite('velMmS')),
          velDegS: dist(finite('velDegS')),
          // The solve's own per-frame uncertainty — what the gates lift
          // against. Compare its p50 across a frontal paste and a tilted
          // paste: the ratio IS the regime shift, measured.
          sigmaMm: dist(finite('sigmaMm')),
          sigmaDeg: dist(finite('sigmaDeg')),
          // The gate lift's denominators: vel / noiseVel from the SAME
          // frames is what re-derives LATCH_GATE_SNR from a real session.
          noiseVelMmS: dist(finite('noiseVelMmS')),
          noiseVelDegS: dist(finite('noiseVelDegS')),
          // The motion prior's information share of the solve, per block.
          // This is the certainty gate made visible: near zero on a clean
          // frontal frame (the landmarks own the pose), a substantial
          // fraction at tilt or under occlusion (the prior carries what the
          // landmarks no longer can). Absent when the prior did not run.
          priorShareRot: dist(finite('priorShareRot')),
          priorShareMm: dist(finite('priorShareMm')),
          // The a-posteriori variance factor: how mis-scaled this session's
          // sigma claims actually are, measured over the landmarks whose
          // sigma was never deliberately inflated. Around 1 means the
          // detector's noise model fits this camera and light.
          varianceFactor: dist(finite('varianceFactor')),
          lagMm: dist(finite('lagMm')),
          latchedPct: pct('latched'),
          fadingPct: pct('fading'),
        } : null,
      };
    })(),

    seat: input.seat && {
      frame: input.assessment?.frameId ?? null,
      descentMm: round(input.seat.descentMm),
      padDepthErrorMm: round(input.seat.padDepthErrorMm),
      padLoadFraction: round(input.seat.padLoadFraction),
      padOverClosure: round(input.seat.padOverClosure),
      pantoscopicDeg: round(input.seat.pantoscopicDeg, 1),
      rollDeg: round(input.seat.rollDeg, 2),
      padTiltDeg: input.seat.padTiltDeg.map((v) => round(v, 1)),
      terminationReason: input.seat.terminationReason,
      notes: input.seat.notes,
    },

    fit: input.assessment && {
      score: input.assessment.score,
      measures: input.assessment.measures.map(
        (m) => `${m.id} ${m.grade} ${m.value === null ? '-' : m.value.toFixed(2)}${m.unit}`
          + ` @${m.confidence.toFixed(2)}`,
      ),
    },
  };
}
