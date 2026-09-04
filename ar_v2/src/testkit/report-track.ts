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
 * ## The arms — see `ARMS` for the table this paragraph used to duplicate
 *
 * `v2` is **the configuration the app boots**, and as of 2026-09-04 that is
 * true rather than aspirational. Until then every arm ran
 * `createTracker(model, { smooth })` and called `track()` with no `visibility`,
 * against an app that also runs the motion prior, the rigidity map and an
 * ESTIMATED sigma. This header disclosed one of those four in a subordinate
 * clause — "the app also runs the motion prior, which this harness does not" —
 * which is a true sentence that leaves every number under it describing a
 * system nobody ships.
 *
 * What that cost, measured on 5 seeds x 6 subjects x 3 cameras, median total
 * bridge error in mm, adding one thing at a time to the old unsmoothed arm:
 *
 *     configuration            frontal   turned (>40 deg)   all
 *     old harness               0.860         1.467        0.989
 *     + true visibility         0.800         1.071        0.853
 *     + rigidity map            0.794         1.064        0.855
 *     + motion prior            1.168         1.472        1.147
 *     + estimated sigma         1.342         3.425        1.846
 *
 * Three things fall out of that column, and none of them was visible before:
 *
 *  1. **Visibility culling is worth 27% at turned yaw** and the harness had
 *     been throwing it away — `report-occlusion.ts` was even computing the
 *     array and then not passing it.
 *  2. **The motion prior COSTS 34%** on this protocol. It is on by default in
 *     the app. The protocol is a deliberately fast sweep and the cost shrinks
 *     with speed — 9.3% overall at the shipped rate, 4.5% at half, 2.9% at a
 *     quarter — but it never turns into a gain here. `PRIOR_MISS_EMA_RATE`'s
 *     ledger row already records the prior failing at 1-1.5 Hz reversals; this
 *     is the same regime, and the tilted-REST fixture where the prior was shown
 *     to help is a different one.
 *  3. **The uncertainty estimator costs more than everything else combined** —
 *     1.147 to 1.846 mm overall, 1.472 to 3.425 at turned yaw. Feeding the
 *     harness the synthesiser's true sigma was putting an oracle inside the
 *     measurement, and it was worth 2.3x at exactly the angles this report
 *     exists to grade. `v2-true-sigma` keeps that arm so the two are separable.
 *
 * Options now come from `track/profile.ts`, the same function `app/main.ts`
 * calls.
 */

import { loadBasis, loadRegions, loadTemplateMesh } from './fixtures.js';
import {
  CAMERA_LADDER, CAPTURE_DEFAULTS, captureSeedFor, generatePopulation, populationSeedFor,
  protocolBeats, synthesizeCapture,
} from './synthetic.js';
import { enroll } from '../enroll/enroll.js';
import { createFaceModel, landmarkSurface, type FaceModel } from '../core/facemodel.js';
import { LM } from '../core/mesh.js';
import { createTracker, track } from '../track/tracker.js';
import { shippedSigma, shippedTrackerOptions } from '../track/profile.js';
import { createUncertainty } from '../detect/uncertainty.js';
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
  /**
   * Multiplier on the resting head's wander — `CaptureOptions.wanderScale`.
   *
   * The ablation that decides whether the crawl and shimmer lines are a verdict
   * on the filter or a verdict on the stimulus. A lagging estimator's error
   * moves with the true trajectory's acceleration, so this scales the penalty
   * smoothing pays almost directly, and nothing measured set the default of 1.
   */
  wanderScale: number;
}

interface Bucket {
  depth: number[];
  lateral: number[];
  total: number[];
  rotDeg: number[];
  rmsPx: number[];
  lost: number;
  frames: number;
  /**
   * Frame-to-frame change of the bridge ERROR, mm — `|e_t - e_{t-1}|` where
   * `e = got - want`. What a wearer sees as the frame crawling against the
   * face, measured on every consecutive pair inside one beat.
   *
   * **The column this replaces differenced the ESTIMATE** — `|got_t -
   * got_{t-1}|`, with `want` computed four lines above and never subtracted —
   * so it counted the wearer's own motion as jitter. That is only harmless if
   * the head is still, and it is not: `CaptureOptions.wanderScale` drives an
   * AR(1) postural velocity that the hold beats do not freeze — they freeze
   * only the ANGLE — and it moves the true bridge **1.328 mm/frame median
   * across the five campaign seeds** during `centre`, the one beat that holds
   * the angle exactly. (At `wanderScale: 0` that figure is 0.000, which is how
   * the decomposition was checked.)
   * So the old column paid a filter for failing to follow real motion, and its
   * own table said so: `average-head`, whose bridge sits 10-16 mm from truth,
   * scored tied with `v2`. A metric that cannot separate the right answer from
   * one that is a centimetre out is not measuring the tracker.
   */
  crawl: number[];
  /**
   * The same quantity restricted to the beats that genuinely hold a pose, so a
   * verdict cannot be blamed on the wider window.
   *
   * **Derived from the beat table rather than named**, because the retired
   * column named it and got it wrong: it counted the two beats then called
   * `profile-hold-*`, which ramp 2 degrees of yaw, 4 of pitch and 6 of ROLL
   * across ten frames at 80 degrees of yaw. That is a small deliberate sweep,
   * not a hold, and it put a floor of 0.704 mm/frame of true bridge motion
   * into the one window that was supposed to have none. They are now
   * `profile-dwell-*`; `STILL_BEATS` asks the beat table which segments do not
   * move rather than trusting either name, so re-aiming a beat cannot quietly
   * widen this again.
   */
  crawlHeld: number[];
  /**
   * Second difference of the error, `|e_{t+1} - 2e_t + e_{t-1}|`, mm.
   *
   * The high-frequency component. A pure delay against a smooth trajectory
   * contributes `~tau * x'''`, which is small, so a filter cannot inflate this
   * merely by lagging. It exists to answer the obvious objection to `crawl`:
   * that it only rediscovers lag. If smoothing loses here too, it is losing on
   * the axis it was adopted for.
   */
  shimmer: number[];
}

const YAW_BUCKETS = [0, 15, 30, 45, 60, 75, 90];

interface Arm {
  name: string;
  /** One line under the arm's heading, so a pasted table explains itself. */
  what: string;
  /** Track against the template rather than this wearer — v1's situation. */
  template: boolean;
  smooth: boolean;
  motionPrior: boolean;
  /** Feed the synthesiser's true sigma and visibility instead of estimating. */
  trueSigma: boolean;
}

/**
 * The arms, and **`v2` is now the configuration the app actually boots.**
 *
 * Until 2026-09-04 every arm here ran `createTracker(model, { smooth })`: no
 * motion prior, no rigidity map, and `track()` called with no `visibility`,
 * against an app that runs all three. The header used to carry the disclosure
 * as a single clause — "the app also runs the motion prior, which this harness
 * does not" — which is a true sentence that leaves every number below it
 * describing a system nobody ships. Options now come from
 * `track/profile.ts`'s `shippedTrackerOptions`, the same function `app/main.ts`
 * calls, so the two cannot drift apart again without a test going red.
 *
 * Each ablation removes exactly one thing from `v2`, so a column difference has
 * one cause — except `raw`, which removes all three and is the floor the ladder
 * is measured from. `raw` is also the arm every figure published before
 * 2026-09-04 was actually produced on, kept so the old numbers stay comparable
 * and so something in this report is still free of estimator lag: it is the
 * reference `tests/pipeline.test.ts` uses to assert that the crawl column is
 * referenced to truth, which needs an arm that does not lag by construction.
 */
const ARMS: readonly Arm[] = [
  {
    name: 'v2',
    what: 'the shipped configuration: smoothing, motion prior, rigidity, estimated sigma',
    template: false, smooth: true, motionPrior: true, trueSigma: false,
  },
  {
    name: 'v2-true-sigma',
    what: 'v2 fed the TRUE sigma and visibility — the oracle the app does not have',
    template: false, smooth: true, motionPrior: true, trueSigma: true,
  },
  {
    name: 'v2-no-smoothing',
    what: 'v2 with the One Euro off — separates solver error from filter lag',
    template: false, smooth: false, motionPrior: true, trueSigma: false,
  },
  {
    name: 'v2-no-prior',
    what: 'v2 with the constant-velocity prior off — the prior ships ON by default',
    template: false, smooth: true, motionPrior: false, trueSigma: false,
  },
  {
    name: 'raw',
    what: 'the bare solver: no filter, no prior, the true sigma — the floor, and the old harness',
    template: false, smooth: false, motionPrior: false, trueSigma: true,
  },
  {
    name: 'average-head',
    what: 'v2 against the TEMPLATE — the v1 situation, since MediaPipe fits an average head',
    template: true, smooth: true, motionPrior: true, trueSigma: false,
  },
];

/** Beats whose pose does not move: same euler triple at both ends, no distance
 *  ramp. Today that is `centre` alone. */
const STILL_BEATS = new Set(
  protocolBeats(CAPTURE_DEFAULTS)
    .filter((b) => b.from.every((v, i) => v === b.to[i])
      && (b.distanceFrom ?? 1) === (b.distanceTo ?? 1))
    .map((b) => b.name),
);

export function runTrackReport(options: Partial<TrackRunOptions> = {}): string {
  const opt: TrackRunOptions = {
    subjects: 6, useTruth: true, geometries: CAMERA_LADDER.map((g) => g.name),
    wanderScale: 1, ...options,
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

  const results: Record<string, Record<number, Bucket>> = {};
  for (const arm of ARMS) results[arm.name] = Object.fromEntries(
    YAW_BUCKETS.map((y) => [y, emptyBucket()]),
  );

  for (const subject of population) {
    const truth = truthModel(subject.positions, mesh.vertexCount);
    const model: FaceModel = opt.useTruth ? truth : scanOf(subject, mesh, basis, opt.seed);
    const template = truthModel(mesh.positions, mesh.vertexCount);

    for (const geometry of CAMERA_LADDER) {
      if (!opt.geometries.includes(geometry.name)) continue;
      const capture = synthesizeCapture(mesh, subject, geometry, {
        framesPerBeat: 10, seed: captureSeedFor(opt.seed), wanderScale: opt.wanderScale,
      });

      for (const arm of ARMS) {
        const trackModel = arm.template ? template : model;
        // **The app's configuration, from the app's own module.** Not a list
        // retyped here: `app/main.ts` calls the same function, and
        // `tests/app.test.ts` asserts it keeps doing so.
        const state = createTracker(trackModel, shippedTrackerOptions({
          mesh, regions, smooth: arm.smooth, motionPrior: arm.motionPrior,
        }));
        // The app's per-frame uncertainty, too. It is stateful (it caches a
        // depth buffer keyed on the intrinsics), so each arm gets its own.
        const uncertainty = createUncertainty(mesh.vertexCount);
        const detectorSurface = landmarkSurface(trackModel);
        let previousTime = 0;
        let previousRaw: Pose | null = null;
        let previousErr: Float64Array | null = null;
        let previousDelta: Float64Array | null = null;
        let previousBeat = '';

        for (let f = 0; f < capture.frames.length; f++) {
          const frame = capture.frames[f];
          const dt = f === 0 ? 1 / 30 : (f - previousTime) / 30;
          previousTime = f;

          // The synthesiser knows the true sigma and the true visibility. The
          // app never does — it estimates both by rasterising the model at the
          // PREVIOUS pose. Only `true-sigma` gets the oracle, and the gap
          // between it and `v2` is what that estimator costs.
          const { sigmaPx, visibility } = arm.trueSigma
            ? { sigmaPx: frame.sigmaPx, visibility: frame.visibility }
            : shippedSigma({
              state: uncertainty,
              landmarks: frame.landmarks,
              mesh,
              positions: detectorSurface,
              intrinsics: capture.trueIntrinsics,
              previousPose: previousRaw,
            });

          const result = track(state, {
            landmarks: frame.landmarks,
            sigmaPx,
            visibility,
            intrinsics: capture.trueIntrinsics,
            dt,
          });
          // `applyTracked` in `app/main.ts`, exactly: the RAW pose, and it dies
          // when the tracker drops its own — a pose carried across the reset
          // rasterises the first frame back against a head that has moved on.
          previousRaw = result.rawPose ?? (state.lastRaw ? previousRaw : null);

          const bucket = results[arm.name][nearestBucket(Math.abs(frame.trueYaw))];
          bucket.frames++;
          if (!result.tracked || !result.pose) {
            bucket.lost++;
            // A lost frame is a GAP, not a step. Differencing across it would
            // report the motion of however many frames the tracker missed as
            // one frame of crawl. The retired column did exactly that: it
            // `continue`d before touching its previous-frame state.
            previousErr = null;
            previousDelta = null;
            previousBeat = '';
            continue;
          }

          // Where the bridge of the frame lands, against where it should.
          const got = v3(), want = v3();
          applyPose(got, result.pose, trackModel.positions, LM.NOSE_BRIDGE * 3);
          applyPose(want, frame.pose, subject.positions, LM.NOSE_BRIDGE * 3);

          bucket.depth.push(got[2] - want[2]);
          bucket.lateral.push(Math.hypot(got[0] - want[0], got[1] - want[1]));
          bucket.total.push(Math.hypot(got[0] - want[0], got[1] - want[1], got[2] - want[2]));
          bucket.rotDeg.push((rotationAngleBetween(result.pose.R, frame.pose.R) * 180) / Math.PI);
          if (Number.isFinite(result.rmsPx)) bucket.rmsPx.push(result.rmsPx);

          // The benefit side of the filter, differenced against TRUTH.
          //
          // Only against the previous frame of the SAME beat, so a beat
          // boundary is not counted as a step. Reporting the filter's lag
          // without its shimmer reduction is how a filter gets tuned to zero;
          // reporting a shimmer number that cannot tell a good pose from a bad
          // one is worse, because it reads as evidence.
          const ex = got[0] - want[0], ey = got[1] - want[1], ez = got[2] - want[2];
          const held = STILL_BEATS.has(frame.beat);
          if (previousErr && previousBeat === frame.beat) {
            const dx = ex - previousErr[0];
            const dy = ey - previousErr[1];
            const dz = ez - previousErr[2];
            const crawl = Math.hypot(dx, dy, dz);
            bucket.crawl.push(crawl);
            if (held) bucket.crawlHeld.push(crawl);
            if (previousDelta) {
              bucket.shimmer.push(Math.hypot(
                dx - previousDelta[0], dy - previousDelta[1], dz - previousDelta[2],
              ));
            }
            previousDelta = Float64Array.of(dx, dy, dz);
          } else {
            previousDelta = null;
          }
          previousErr = Float64Array.of(ex, ey, ez);
          previousBeat = frame.beat;
        }
      }
    }
  }

  for (const arm of ARMS) {
    out.push(`--- ${arm.name} ---`);
    out.push(`    ${arm.what}`);
    const rows: (string | number)[][] = [];
    for (const yaw of YAW_BUCKETS) {
      const b = results[arm.name][yaw];
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
    const line = (label: string, values: number[]): void => {
      if (!values.length) return;
      const d = distribution(values);
      out.push(`  ${label.padEnd(28)}${d.median.toFixed(3)} mm median, ` +
        `${d.p90.toFixed(3)} p90, ${d.worst.toFixed(3)} worst (${d.n} steps)`);
    };
    line('crawl (all frames)', YAW_BUCKETS.flatMap((y) => results[arm.name][y].crawl));
    line('crawl (still beats)', YAW_BUCKETS.flatMap((y) => results[arm.name][y].crawlHeld));
    line('shimmer (high frequency)', YAW_BUCKETS.flatMap((y) => results[arm.name][y].shimmer));
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
  for (const arm of ARMS) {
    const frontal = distribution(results[arm.name][0].depth).median;
    const turned = [45, 60, 75].map((y) => distribution(results[arm.name][y].depth).median)
      .filter((v) => Number.isFinite(v));
    const worst = turned.length
      ? turned.reduce((a, b) => (Math.abs(b - frontal) > Math.abs(a - frontal) ? b : a))
      : NaN;
    swingRows.push([
      arm.name,
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
  out.push('total error; the three lines under each arm are what that lag buys.');
  out.push('Both belong in the same report — reporting only the cost is how a');
  out.push('filter gets tuned to nothing, and reporting only the benefit is how it');
  out.push('gets tuned until the frame swims.');
  out.push('');
  out.push('All three difference the ERROR, not the estimate:');
  out.push('');
  out.push('  crawl (all frames)   |e_t - e_{t-1}|, every consecutive pair inside');
  out.push('                       one beat. What the wearer sees as the frame');
  out.push('                       moving against the face.');
  out.push('  crawl (still beats)  the same, restricted to the beats whose pose');
  out.push('                       does not move at all, so a verdict cannot be');
  out.push('                       blamed on the wider window. Derived from the');
  out.push('                       beat table: the retired column counted two');
  out.push('                       beats that ramp 6 degrees of roll as holds.');
  out.push('  shimmer              |e_{t+1} - 2e_t + e_{t-1}|. A pure delay against');
  out.push('                       a smooth trajectory barely registers here, so');
  out.push('                       this is the axis the filter was adopted for.');
  out.push('');
  out.push('The column these replace differenced the ESTIMATE against its own');
  out.push('previous frame, so the wearer\'s postural wander — around a millimetre');
  out.push('per frame, hold beats included — was counted as jitter and a lagging');
  out.push('filter won by not following it. See the `crawl` field comment.');
  out.push('');
  out.push('Note that this protocol is a deliberately fast sweep (35 degrees in a');
  out.push('third of a second); ordinary browsing is slower and the lag is');
  out.push('correspondingly smaller. The harness also runs without the motion');
  out.push('prior, which the app switches on by default.');

  return out.join('\n');
}

// ------------------------------------------------------------------ helpers

const emptyBucket = (): Bucket => ({
  depth: [], lateral: [], total: [], rotDeg: [], rmsPx: [], lost: 0, frames: 0,
  crawl: [], crawlHeld: [], shimmer: [],
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
