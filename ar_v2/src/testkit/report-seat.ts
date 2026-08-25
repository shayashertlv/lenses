/**
 * The seat report: where each frame actually comes to rest on each face.
 *
 * This is the direct answer to the complaint that started v2 — *"the interaction
 * of the glasses with the nose is not good at all"* — and it is built around the
 * discipline v1's audit named:
 *
 *   *"A number that can only be zero is not a measurement until you have shown
 *   it can be something else."*
 *
 * v1 shipped a check that "the pads stay on the nose" which measured a residual
 * that was identically zero by construction — the harness was re-deriving the
 * solver's own algebra and comparing it with itself. So every table here ships
 * with a **control**: the same solve against a deliberately wrong nose. If the
 * control does not fail, the measurement is not a measurement.
 *
 * Three controls, and each one falsifies a different claim:
 *
 *   `template-nose`   Seat against the average head instead of this wearer's.
 *                     This is v1's situation exactly. If the pads still bear,
 *                     then scanning the nose bought nothing.
 *   `nominal`         Do not solve at all — hang the pads on the bridge
 *                     landmark, which is v1's final placement. The gap between
 *                     this and the solve is what the contact physics is worth.
 *   `flat-nose`       Seat against a nose with its sidewall flare removed. The
 *                     wedge is the mechanism; without it the frame should not
 *                     find a resting height at all.
 */

import { loadBasis, loadRegions, loadTemplateMesh } from './fixtures.js';
import {
  CAMERA_LADDER, captureSeedFor, generatePopulation, populationSeedFor, synthesizeCapture,
} from './synthetic.js';
import { enroll } from '../enroll/enroll.js';
import { createFaceModel, type FaceModel } from '../core/facemodel.js';
import { standardRegions } from '../core/mesh.js';
import { landmarkHungPose, solveSeat, type SeatResult } from '../fit/contact.js';
import { TEST_FRAMES, parametricFrame, type FrameAsset } from '../fit/frame-asset.js';
import { distribution, fmt, table } from './metrics.js';
import { evaluateBasis } from '../core/shape/basis.js';

export interface SeatRunOptions {
  subjects: number;
  /** Skip the (slow) enrollment and seat against ground truth. Isolates the
   *  contact solver from the reconstruction. */
  useTruth: boolean;
  verbose: boolean;
  /** Campaign seed: one independent noise realisation per distinct value (new
   *  population, and with `useTruth: false` new captures too); the same seed
   *  reproduces the run bit for bit. `undefined` reproduces the historical
   *  no-seed run exactly. See `RunOptions.seed` in report-enroll.ts. */
  seed?: number;
}

export function runSeatReport(options: Partial<SeatRunOptions> = {}): string {
  const opt: SeatRunOptions = { subjects: 6, useTruth: true, verbose: false, ...options };
  const mesh = loadTemplateMesh();
  const basis = loadBasis();
  const regions = loadRegions();
  const population = generatePopulation(mesh, basis, {
    count: opt.subjects, seed: populationSeedFor(opt.seed),
  });

  const out: string[] = [];
  out.push('SEAT — WHERE THE FRAME COMES TO REST');
  out.push('====================================');
  out.push(
    opt.useTruth
      ? 'Seated against ground-truth geometry, to isolate the contact solve.'
      : 'Seated against reconstructed geometry, end to end.',
  );
  out.push('');

  // Model per subject: either the truth, or a real scan.
  const models: FaceModel[] = population.map((subject) => {
    if (opt.useTruth) return truthModel(subject.positions, mesh.vertexCount);
    const geometry = CAMERA_LADDER[0];
    const capture = synthesizeCapture(mesh, subject, geometry, {
      framesPerBeat: 12, seed: captureSeedFor(opt.seed),
    });
    return enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
      irisMm: subject.irisDiameterMm,
    }).model;
  });

  const templateModel = truthModel(mesh.positions, mesh.vertexCount);
  const flatModels = population.map((s) => truthModel(flattenWedge(mesh, basis, s.shapeCoeffs), mesh.vertexCount));

  // ---- main table: per frame, across the population -----------------------
  const rows: (string | number)[][] = [];
  for (const frame of TEST_FRAMES) {
    const descent: number[] = [];
    const bearing: number[] = [];
    const padLoad: number[] = [];
    const panto: number[] = [];
    const roll: number[] = [];
    const clearance: number[] = [];
    const ms: number[] = [];
    let notConverged = 0;

    for (let i = 0; i < population.length; i++) {
      const seat = solveSeat(models[i], mesh, regions, frame);
      descent.push(seat.descentMm);
      bearing.push(Math.abs(seat.padDepthErrorMm));
      padLoad.push(seat.padLoadFraction);
      panto.push(seat.pantoscopicDeg);
      roll.push(Math.abs(seat.rollDeg));
      clearance.push(Math.max(seat.padTiltDeg[0], seat.padTiltDeg[1]));
      ms.push(seat.solveMs);
      if (!seat.converged) notConverged++;
      if (opt.verbose) {
        out.push(`  ${population[i].id}/${frame.id}: ${describe(seat)}`);
      }
    }

    rows.push([
      frame.id,
      frame.padSeparationMm,
      fmt(distribution(descent)),
      distribution(bearing).median.toFixed(2),
      distribution(padLoad).median.toFixed(2),
      distribution(panto).median.toFixed(1),
      distribution(clearance).median.toFixed(1),
      Math.round(distribution(ms).median),
      notConverged,
    ]);
  }

  out.push(table(
    ['frame', 'pad sep', 'descent mm (med/p90/worst)', '|depth err| mm', 'pad load',
      'panto deg', 'pad tilt deg', 'ms', 'unconverged'],
    rows,
  ));
  out.push('');
  out.push('  descent    how far BELOW the nominal placement the frame settles.');
  out.push('             Nominal is v1\'s answer: pads hung on the bridge landmark.');
  out.push('             A wide-padded frame on a narrow nose settles further.');
  out.push('  seat err   RMS spread of the pad samples\' distance to the skin. Zero');
  out.push('             is a pad bedded flush; large is a pad touching on one edge,');
  out.push('             which is what a wearer feels as a pressure point.');
  out.push('  pad load   share of the frame\'s weight carried by the nose rather');
  out.push('             than the ears.');
  out.push('');

  // ---- the wedge, measured ------------------------------------------------
  out.push('THE WEDGE, MEASURED');
  out.push('-------------------');
  out.push('v1 derived that half-width grows ~0.54 mm per mm of descent, and could');
  out.push('not test it — it had no way to vary a frame\'s pad separation alone.');
  out.push('Here it is a sweep. Descent against pad separation, one subject at a time:');
  out.push('');
  const sweepRows: (string | number)[][] = [];
  for (const sep of [12, 14, 16, 18, 20, 22, 24]) {
    const frame = parametricFrame({ id: `sweep-${sep}`, padSeparationMm: sep, padAngleRad: 0.42 });
    const d: number[] = [];
    for (const model of models) d.push(solveSeat(model, mesh, regions, frame).descentMm);
    sweepRows.push([sep, distribution(d).median.toFixed(2), distribution(d).p90.toFixed(2)]);
  }
  out.push(table(['pad separation mm', 'descent median', 'descent p90'], sweepRows));
  const slope = estimateSlope(sweepRows);
  out.push('');
  out.push(`  measured slope: ${slope.toFixed(3)} mm of descent per mm of pad separation`);
  out.push(`  implied wedge half-angle: ${(Math.atan(1 / (2 * Math.max(slope, 1e-6))) * 180 / Math.PI).toFixed(1)} deg`);
  out.push('');

  // ---- controls -----------------------------------------------------------
  out.push('CONTROLS — these must FAIL, or the table above measures nothing');
  out.push('---------------------------------------------------------------');
  const frame = TEST_FRAMES[1]; // 'standard'
  const controlRows: (string | number)[][] = [];

  const measure = (label: string, list: FaceModel[], against: FaceModel[]) => {
    const bearing: number[] = [];
    const penetration: number[] = [];
    const descent: number[] = [];
    for (let i = 0; i < list.length; i++) {
      // Solve the seat against `list[i]`, then measure how that seat sits on the
      // wearer's REAL geometry. This is the honest form of the control: a seat
      // solved against the wrong nose is not wrong because its own solve is
      // unhappy — it is wrong because it does not fit the actual face.
      const seat = solveSeat(list[i], mesh, regions, frame);
      const onTruth = evaluateSeatOn(against[i], mesh, regions, frame, seat);
      bearing.push(Math.abs(onTruth.padDepthErrorMm));
      penetration.push(onTruth.padSeatErrorArticulatedMm);
      descent.push(seat.descentMm);
    }
    controlRows.push([
      label,
      distribution(bearing).median.toFixed(2),
      distribution(penetration).median.toFixed(2),
      distribution(descent).median.toFixed(2),
    ]);
  };

  measure('solved on this face (baseline)', models, models);
  measure('template-nose control', population.map(() => templateModel), models);
  measure('flat-nose control', flatModels, models);

  // Nominal: no solve at all.
  {
    const bearing: number[] = [];
    const penetration: number[] = [];
    for (const model of models) {
      const nominal = solveSeat(model, mesh, regions, frame, {
        maxIterations: 0, initialPose: landmarkHungPose(model, frame),
      });
      bearing.push(Math.abs(nominal.padDepthErrorMm));
      penetration.push(nominal.padSeatErrorArticulatedMm);
    }
    controlRows.push([
      'nominal placement (v1)',
      distribution(bearing).median.toFixed(2),
      distribution(penetration).median.toFixed(2),
      '0.00',
    ]);
  }

  out.push(table(
    ['case', '|depth error| on the real face mm', 'residual after bending mm', 'own descent mm'],
    controlRows,
  ));
  out.push('');
  out.push('  A control passes by being BAD: a seat solved against the wrong nose');
  out.push('  should put a pad in the air or bury it in the skin when it meets the');
  out.push('  real one. If every row looks like the baseline, the seat is not');
  out.push('  reading the scan and the scan was not worth taking.');
  out.push('');
  out.push('  "nominal placement" is the row to read first: a frame hung off the');
  out.push('  bridge landmark with no contact solve at all, which is what v1 shipped.');
  out.push('  It uses landmarkHungPose, NOT the initialisation the solver itself');
  out.push('  starts from — those are two different jobs, and sharing one function');
  out.push('  made this control meaningless for a while (docs/OPEN-QUESTIONS.md Q12).');

  return out.join('\n');
}

// ------------------------------------------------------------------ helpers

function truthModel(positions: Float64Array, vertexCount: number): FaceModel {
  return createFaceModel({
    positions: new Float64Array(positions),
    vertexSigmaMm: new Float64Array(vertexCount).fill(0.1),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0,
    displacementMaxMm: 0,
    intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(vertexCount * 3),
    quality: {},
    pdMm: null,
    pdSigmaMm: null,
    reprojectionRmsPx: 0,
    framesUsed: 0,
    solveMs: 0,
    degraded: false,
    notes: [],
  });
}

/** The same face with the wedge removed — the sidewall made vertical. */
function flattenWedge(
  mesh: ReturnType<typeof loadTemplateMesh>, basis: ReturnType<typeof loadBasis>,
  coeffs: Float64Array,
): Float64Array {
  const k = basis.labels.indexOf('noseSidewallFlare');
  const flat = new Float64Array(coeffs);
  if (k >= 0) flat[k] = -3.5;
  const out = new Float64Array(mesh.vertexCount * 3);
  evaluateBasis(basis, flat, out);
  return out;
}

/**
 * Evaluates a *given* seat transform against a face, without re-solving.
 *
 * This is what makes a control a control. Passing `maxIterations: 0` with an
 * explicit `initialPose` turns `solveSeat` into a pure evaluator, so "solve
 * against the wrong nose, then measure on the right one" cannot quietly become
 * "solve against the right one".
 *
 * With `seat` null it evaluates the nominal placement — v1's answer — which is
 * the fourth control.
 */
function evaluateSeatOn(
  model: FaceModel, mesh: ReturnType<typeof loadTemplateMesh>,
  regions: ReturnType<typeof standardRegions>, frame: FrameAsset,
  seat: SeatResult | null,
): SeatResult {
  return solveSeat(model, mesh, regions, frame, {
    maxIterations: 0,
    ...(seat ? { initialPose: seat.pose } : {}),
  });
}

function describe(seat: SeatResult): string {
  return `descent ${seat.descentMm.toFixed(2)} mm, bearing ` +
    `${seat.pads[0].bearingFraction.toFixed(2)}/${seat.pads[1].bearingFraction.toFixed(2)}, ` +
    `load ${(seat.padLoadFraction * 100).toFixed(0)}%, panto ${seat.pantoscopicDeg.toFixed(1)} deg` +
    (seat.notes.length ? ` — ${seat.notes.join('; ')}` : '');
}

function estimateSlope(rows: (string | number)[][]): number {
  const xs = rows.map((r) => Number(r[0]));
  const ys = rows.map((r) => Number(r[1]));
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) ** 2; }
  return den > 0 ? num / den : 0;
}
