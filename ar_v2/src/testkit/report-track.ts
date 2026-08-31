/**
 * The tracking report: what happens past 40 degrees of yaw.
 *
 * This is the direct answer to the first complaint that motivated v2 — *"the
 * glasses are being pushed forward for no reason at >40 degrees"* — and the
 * whole table is built around making that number visible for the first time.
 *
 * v1 could never measure it, and the reason is instructive. Its own stage-14
 * note says the honest thing: *"the only behaviour a wearer ever reported by
 * name was the only behaviour with no general instrument"*. It could not build
 * one because it had no ground truth: the wearer's real nose was unknown, so
 * "where should the frame be" had no answer to compare against. Synthetic
 * subjects have an answer by construction.
 *
 * ## What is measured
 *
 * Not pose error in the abstract. **Where the bridge of the frame lands**, in
 * millimetres, against where it should land — because that is the quantity a
 * wearer sees. Decomposed into:
 *
 *   `depth`     signed error along the view axis. Negative is toward the camera,
 *               which is the "pushed forward" the complaint names.
 *   `lateral`   error across the face.
 *   `total`     the distance a wearer would actually perceive.
 *
 * ## The three arms
 *
 *   `v2`              pose solved against this wearer's own geometry — ground
 *                     truth at the default `useTruth: true`, the scan when it is
 *                     false — with the One Euro ON, which is **the app's shipped
 *                     smoothing** (`app/main.ts` boots `smooth: true`). The app
 *                     also runs the motion prior, which this harness does not.
 *   `average-head`    the same solve against the template — v1's situation, since
 *                     MediaPipe's transformation matrix is a rigid fit of the
 *                     average head to the landmarks.
 *   `v2-no-smoothing` v2 with the filter off, to separate solver error from
 *                     filter lag.
 */

import { loadBasis, loadRegions, loadTemplateMesh } from './fixtures.js';
import {
  CAMERA_LADDER, captureSeedFor, generatePopulation, populationSeedFor, synthesizeCapture,
} from './synthetic.js';
import { enroll } from '../enroll/enroll.js';
import { createFaceModel, type FaceModel } from '../core/facemodel.js';
import { LM } from '../core/mesh.js';
import { createTracker, track } from '../track/tracker.js';
import { rotationAngleBetween, type Pose, v3 } from '../core/linalg.js';
import { distribution, table } from './metrics.js';

export interface TrackRunOptions {
  subjects: number;
  /** Use ground-truth geometry rather than a scan, to isolate the tracker. */
  useTruth: boolean;
  geometries: string[];
  /** Campaign seed: one independent noise realisation per distinct value (new
   *  population and new captures — including the scan when `useTruth: false`);
   *  the same seed reproduces the run bit for bit. `undefined` reproduces the
   *  historical no-seed run exactly. See `RunOptions.seed` in report-enroll.ts. */
  seed?: number;
}

interface Bucket {
  depth: number[];
  lateral: number[];
  total: number[];
  rotDeg: number[];
  rmsPx: number[];
  lost: number;
  frames: number;
  /** Frame-to-frame movement of the bridge during a HELD pose, mm. The jitter a
   *  wearer sees as shimmer. */
  jitter: number[];
}

const YAW_BUCKETS = [0, 15, 30, 45, 60, 75, 90];

export function runTrackReport(options: Partial<TrackRunOptions> = {}): string {
  const opt: TrackRunOptions = {
    subjects: 6, useTruth: true, geometries: CAMERA_LADDER.map((g) => g.name), ...options,
  };
  const mesh = loadTemplateMesh();
  const basis = loadBasis();
  const regions = loadRegions();
  const population = generatePopulation(mesh, basis, {
    count: opt.subjects, seed: populationSeedFor(opt.seed),
  });

  const out: string[] = [];
  out.push('TRACKING — WHAT HAPPENS PAST 40 DEGREES OF YAW');
  out.push('==============================================');
  out.push(
    opt.useTruth
      ? 'Tracked against ground-truth geometry, to isolate the tracker from the scan.'
      : 'Tracked against scanned geometry, end to end.',
  );
  out.push(`${population.length} subjects x ${opt.geometries.length} camera geometries`);
  out.push('');

  const arms = ['v2', 'average-head', 'v2-no-smoothing'] as const;
  const results: Record<string, Record<number, Bucket>> = {};
  for (const arm of arms) results[arm] = Object.fromEntries(
    YAW_BUCKETS.map((y) => [y, emptyBucket()]),
  );

  for (const subject of population) {
    const truth = truthModel(subject.positions, mesh.vertexCount);
    const model: FaceModel = opt.useTruth ? truth : scanOf(subject, mesh, basis, opt.seed);
    const template = truthModel(mesh.positions, mesh.vertexCount);

    for (const geometry of CAMERA_LADDER) {
      if (!opt.geometries.includes(geometry.name)) continue;
      const capture = synthesizeCapture(mesh, subject, geometry, {
        framesPerBeat: 10, seed: captureSeedFor(opt.seed),
      });

      for (const arm of arms) {
        const trackModel = arm === 'average-head' ? template : model;
        const state = createTracker(trackModel, { smooth: arm !== 'v2-no-smoothing' });
        let previousTime = 0;
        let previousBridge: Float64Array | null = null;
        let previousBeat = '';

        for (let f = 0; f < capture.frames.length; f++) {
          const frame = capture.frames[f];
          const dt = f === 0 ? 1 / 30 : (f - previousTime) / 30;
          previousTime = f;

          const result = track(state, {
            landmarks: frame.landmarks,
            sigmaPx: frame.sigmaPx,
            intrinsics: capture.trueIntrinsics,
            dt,
          });

          const bucket = results[arm][nearestBucket(Math.abs(frame.trueYaw))];
          bucket.frames++;
          if (!result.tracked || !result.pose) { bucket.lost++; continue; }

          // Where the bridge of the frame lands, against where it should.
          const got = v3(), want = v3();
          applyPose(got, result.pose, trackModel.positions, LM.NOSE_BRIDGE * 3);
          applyPose(want, frame.pose, subject.positions, LM.NOSE_BRIDGE * 3);

          bucket.depth.push(got[2] - want[2]);
          bucket.lateral.push(Math.hypot(got[0] - want[0], got[1] - want[1]));
          bucket.total.push(Math.hypot(got[0] - want[0], got[1] - want[1], got[2] - want[2]));
          bucket.rotDeg.push((rotationAngleBetween(result.pose.R, frame.pose.R) * 180) / Math.PI);
          if (Number.isFinite(result.rmsPx)) bucket.rmsPx.push(result.rmsPx);

          // Jitter: only during a beat that holds a pose, and only against the
          // previous frame of the SAME beat, so a beat boundary is not counted
          // as a jump. This is the benefit side of the filter — reporting its
          // lag without its jitter reduction would make smoothing look purely
          // costly, which is how a filter gets tuned to zero.
          const held = frame.beat === 'centre' || frame.beat.startsWith('profile-hold');
          if (held && previousBridge && previousBeat === frame.beat) {
            bucket.jitter.push(Math.hypot(
              got[0] - previousBridge[0], got[1] - previousBridge[1], got[2] - previousBridge[2],
            ));
          }
          previousBridge = new Float64Array(got);
          previousBeat = frame.beat;
        }
      }
    }
  }

  for (const arm of arms) {
    out.push(`--- ${arm} ---`);
    const rows: (string | number)[][] = [];
    for (const yaw of YAW_BUCKETS) {
      const b = results[arm][yaw];
      if (b.frames === 0) continue;
      const d = distribution(b.depth);
      rows.push([
        `${yaw}`,
        b.frames,
        d.median.toFixed(2),
        distribution(b.total).median.toFixed(2),
        distribution(b.total).p90.toFixed(2),
        distribution(b.rotDeg).median.toFixed(2),
        distribution(b.rmsPx).median.toFixed(2),
        b.lost,
      ]);
    }
    out.push(table(
      ['yaw deg', 'frames', 'depth err mm', 'total err mm', 'p90 mm', 'rot err deg', 'rms px', 'lost'],
      rows,
    ));
    const jitter = YAW_BUCKETS.flatMap((y) => results[arm][y].jitter);
    if (jitter.length) {
      const j = distribution(jitter);
      out.push(`  jitter while holding a pose: ${j.median.toFixed(3)} mm median, ` +
        `${j.p90.toFixed(3)} p90, ${j.worst.toFixed(3)} worst (${j.n} frames)`);
    }
    out.push('');
  }

  // The headline: how much the depth error MOVES between frontal and profile.
  out.push('THE COMPLAINT, QUANTIFIED');
  out.push('-------------------------');
  out.push('"The glasses are pushed forward at >40 degrees" is a statement about how');
  out.push('much the depth error CHANGES with yaw — a constant offset would be');
  out.push('invisible, because the wearer never sees the frame anywhere else.');
  out.push('');
  const swingRows: (string | number)[][] = [];
  for (const arm of arms) {
    const frontal = distribution(results[arm][0].depth).median;
    const turned = [45, 60, 75].map((y) => distribution(results[arm][y].depth).median)
      .filter((v) => Number.isFinite(v));
    const worst = turned.length
      ? turned.reduce((a, b) => (Math.abs(b - frontal) > Math.abs(a - frontal) ? b : a))
      : NaN;
    swingRows.push([
      arm,
      frontal.toFixed(2),
      Number.isFinite(worst) ? worst.toFixed(2) : '-',
      Number.isFinite(worst) ? Math.abs(worst - frontal).toFixed(2) : '-',
    ]);
  }
  out.push(table(
    ['arm', 'depth err at 0 deg', 'depth err when turned', 'swing mm'],
    swingRows,
  ));
  out.push('');
  out.push('  swing is the number a wearer reports as "it pushes forward when I turn".');
  out.push('');
  out.push('WHAT THE FILTER COSTS AND BUYS');
  out.push('------------------------------');
  out.push('The lag is visible above as the gap between v2 and v2-no-smoothing on');
  out.push('total error; the jitter line under each arm is what that lag buys. Both');
  out.push('numbers belong in the same report — reporting only the cost is how a');
  out.push('filter gets tuned to nothing, and reporting only the benefit is how it');
  out.push('gets tuned until the frame swims. Note that this protocol is a');
  out.push('deliberately fast sweep (35 degrees in a third of a second); ordinary');
  out.push('browsing is slower and the lag is correspondingly smaller.');

  return out.join('\n');
}

// ------------------------------------------------------------------ helpers

const emptyBucket = (): Bucket => ({
  depth: [], lateral: [], total: [], rotDeg: [], rmsPx: [], lost: 0, frames: 0, jitter: [],
});

function nearestBucket(yawRad: number): number {
  const deg = (yawRad * 180) / Math.PI;
  let best = YAW_BUCKETS[0];
  for (const b of YAW_BUCKETS) if (Math.abs(b - deg) < Math.abs(best - deg)) best = b;
  return best;
}

function applyPose(out: Float64Array, pose: Pose, p: ArrayLike<number>, o: number): void {
  const x = p[o], y = p[o + 1], z = p[o + 2];
  const R = pose.R;
  out[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
  out[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
  out[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
}

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
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
}

function scanOf(
  subject: ReturnType<typeof generatePopulation>[number],
  mesh: ReturnType<typeof loadTemplateMesh>,
  basis: ReturnType<typeof loadBasis>,
  seed: number | undefined,
): FaceModel {
  const geometry = CAMERA_LADDER[0];
  const capture = synthesizeCapture(mesh, subject, geometry, {
    framesPerBeat: 12, seed: captureSeedFor(seed),
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
}

export { loadRegions };
