/**
 * The pipeline, end to end, against synthetic ground truth.
 *
 * Every threshold in this file is a **regression bar**, not an aspiration: it is
 * set from a measured run with headroom, so a change that makes the system worse
 * fails here rather than in a report nobody re-read. Where a bar is loose, it
 * says so and says why.
 *
 * Several tests exist only because something was silently broken and nothing
 * caught it. Those are marked. They are the most valuable tests in the file:
 *
 *   - the displacement field must actually move (it did not, for a whole build)
 *   - the basis must NOT be able to explain a synthetic nose (or the enrollment
 *     tests measure the basis rather than the scan)
 *   - a seat solved against the wrong nose must fit the right one badly (v1's
 *     "a number that can only be zero is not a measurement")
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { loadBasis, loadRegions, loadTemplateMesh } from '../src/testkit/fixtures.js';
import {
  CAMERA_LADDER, PLAUSIBLE, assertDistinctNoiseStreams, captureSeedFor,
  generatePopulation, lastRejectionCount, populationSeedFor,
  subjectResidualAgainstBasis, synthesizeCapture,
} from '../src/testkit/synthetic.js';
import { compareToTruth, distribution, table } from '../src/testkit/metrics.js';
import { measure, standardRegions, trackingRigidity } from '../src/core/mesh.js';
import { basisExplains } from '../src/core/shape/anthropometric.js';
import { evaluateBasis } from '../src/core/shape/basis.js';
import { createRng } from '../src/testkit/random.js';
import {
  createDisplacementField, displacementStats, refreshNormals,
} from '../src/core/shape/displacement.js';
import { enroll } from '../src/enroll/enroll.js';
import { createBundleState, landmarkRigidity, runBundle } from '../src/enroll/bundle.js';
import { assessCoverage, selectKeyframes } from '../src/enroll/keyframes.js';
import {
  IRIS, PD_PLAUSIBLE_MM, PD_RULER, POPULATION_HVID, solveScale,
} from '../src/enroll/scale.js';
import { buildCorrespondences, posit, refinePnP, solvePnP } from '../src/track/pnp.js';
import { createTracker, track } from '../src/track/tracker.js';
import {
  createFaceModel, type FaceModel, type ScaleEstimate,
} from '../src/core/facemodel.js';
import {
  PAD_CURVATURE_LIMIT_MM, SEAT_DEFAULTS, SKIN, energyTerms, landmarkHungPose, solveSeat,
} from '../src/fit/contact.js';
import { perVertexUncertainty } from '../src/enroll/enroll.js';
import { LM } from '../src/core/mesh.js';
import { noseConfidence } from '../src/core/facemodel.js';
import { buildMeshDistance, emptyClosestPoint } from '../src/core/meshdist.js';
import {
  intrinsicsFromFov, pointAtDepth, poseRotationFromHeadEuler, project,
  type Intrinsics,
} from '../src/core/camera.js';
import { createDepthBuffer, rasterize } from '../src/core/raster.js';
import { poseClone, poseIdentity, type Pose } from '../src/core/linalg.js';
import {
  GRAVITY_N_PER_G, TEST_FRAMES, derivePads, parametricFrame, type FrameAsset,
  type FrameSpec,
} from '../src/fit/frame-asset.js';
import { readGlb } from '../src/fit/mesh-io.js';
import { assessFit, rankCatalogue } from '../src/fit/score.js';
import {
  fitOccluderArm, flipsAt, frameSampleSet, ladderPose, occlusionCell,
  occlusionYawLadder, scaleSamples, transformSamples,
} from '../src/testkit/report-occlusion.js';
import { rotationAngleBetween } from '../src/core/linalg.js';
import { rigidAlign } from '../src/enroll/detector-bias.js';

const mesh = loadTemplateMesh();
const basis = loadBasis();
const regions = loadRegions();

const truthModel = (positions: Float64Array): FaceModel => createFaceModel({
  positions: new Float64Array(positions),
  vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
  shapeCoeffs: new Float64Array(0),
  basisName: 'ground-truth',
  displacementRmsMm: 0, displacementMaxMm: 0,
  intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
  intrinsicsSolved: true,
  scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
  landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
  quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
  pdMm: null, pdSigmaMm: null,
  reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
});

// ---------------------------------------------------------------- template

describe('the template and its regions', () => {
  it('parses to 468 vertices in millimetres', () => {
    assert.equal(mesh.vertexCount, 468);
    assert.equal(mesh.triangleCount, 898);
    const m = measure(mesh.positions);
    // Sanity in real units: an adult face is ~155 mm across these landmarks,
    // not 15.5 (centimetres left unconverted) or 1550 (converted twice).
    assert.ok(m.templeWidth > 140 && m.templeWidth < 170, `templeWidth ${m.templeWidth}`);
    assert.ok(m.noseWidth > 18 && m.noseWidth < 30, `noseWidth ${m.noseWidth}`);
    assert.ok(m.nasalRootDepth > 8 && m.nasalRootDepth < 22, `nasalRootDepth ${m.nasalRootDepth}`);
  });

  it('every named region has enough vertices to be usable', () => {
    // A template swap that emptied a region would otherwise show up as a solver
    // that quietly stops constraining part of the face.
    const minimums: Record<string, number> = {
      nose: 60, padStrip: 12, bridge: 8, eyes: 40, temples: 8, cheeks: 8,
    };
    for (const [name, floor] of Object.entries(minimums)) {
      const region = regions[name];
      assert.ok(region, `region ${name} is missing`);
      assert.ok(
        region.members.length >= floor,
        `region ${name} has ${region.members.length} vertices, wanted >= ${floor}`,
      );
    }
  });

  it('the nose region is feathered, not binary', () => {
    // A hard boundary lets the free-form field put a step in the surface exactly
    // at the edge of what it may move, and a step under a nose pad is a crease.
    const weights = Array.from(regions.nose.members, (i) => regions.nose.weight[i]);
    const partial = weights.filter((w) => w > 0.05 && w < 0.95).length;
    assert.ok(partial >= 10, `only ${partial} vertices in the feather band`);
  });
});

// -------------------------------------------------------------------- basis

describe('the eye corners the tracker silences are the eye corners it names', () => {
  it('trackingRigidity zeroes every LM.EYE_* corner', () => {
    // `LID_RING_R`/`LID_RING_L` in `core/mesh.ts` carried a comment saying their
    // corner indices "are cross-checked against `LM.EYE_*` above, so a template
    // swap that renumbers them breaks loudly in the tests rather than silently
    // here". Nothing anywhere performed that check. This is it.
    //
    // The property that matters is behavioural, so it is asserted through
    // `trackingRigidity` rather than by exporting a private array: the eye
    // region may not vote on the pose, because MediaPipe deforms it with gaze
    // and a wearer's eyes must not steer their glasses. If a template renumbered
    // the corners, `LM.EYE_*` would move, the lid rings would not contain them,
    // and the gaze-driven landmarks would be back in the solve at full weight.
    const r = trackingRigidity(mesh, regions);
    for (const [name, i] of [
      ['EYE_OUTER_R', LM.EYE_OUTER_R], ['EYE_OUTER_L', LM.EYE_OUTER_L],
      ['EYE_INNER_R', LM.EYE_INNER_R], ['EYE_INNER_L', LM.EYE_INNER_L],
    ] as const) {
      assert.equal(r[i], 0,
        `${name} (vertex ${i}) has rigidity ${r[i]} — it is not on a lid ring, so a `
        + 'gaze-driven landmark is voting on the pose');
    }

    // And the check has to be able to fail, so: a landmark that is NOT an eye
    // corner must not be silenced. Without this the assertion above would pass
    // on `fill(0)`.
    assert.ok(r[LM.SUBNASALE] > 0.9,
      `the subnasale has rigidity ${r[LM.SUBNASALE]} — everything is being silenced, `
      + 'so the assertions above cannot fail');
  });
});

describe('the shape basis', () => {
  it('has interpretable, independent modes at plausible magnitudes', () => {
    assert.ok(basis.dim >= 15, `only ${basis.dim} modes survived orthogonalisation`);
    for (const label of ['noseWidth', 'noseBridgeDepth', 'noseSidewallFlare', 'noseDeviation']) {
      assert.ok(basis.labels.includes(label), `missing the ${label} mode`);
    }
    for (let k = 0; k < basis.dim; k++) {
      let peak = 0;
      const mode = basis.modes[k];
      for (let i = 0; i < mesh.vertexCount; i++) {
        peak = Math.max(peak, Math.hypot(mode[i * 3], mode[i * 3 + 1], mode[i * 3 + 2]));
      }
      // One standard deviation of any single facial trait is millimetres, not
      // centimetres. A mode peaking at 40 mm means a reference span was wrong —
      // which happened, when the depth modes were scaled off a head-depth span
      // instead of the nasal root depth.
      assert.ok(peak > 0.5 && peak < 20, `mode ${basis.labels[k]} peaks at ${peak.toFixed(1)} mm`);
    }
  });

  it('modes are mutually orthogonal', () => {
    for (let a = 0; a < basis.dim; a++) {
      for (let b = a + 1; b < basis.dim; b++) {
        let dot = 0, na = 0, nb = 0;
        for (let i = 0; i < basis.modes[a].length; i++) {
          dot += basis.modes[a][i] * basis.modes[b][i];
          na += basis.modes[a][i] ** 2;
          nb += basis.modes[b][i] ** 2;
        }
        const cosine = dot / Math.sqrt(na * nb);
        assert.ok(Math.abs(cosine) < 1e-9, `${basis.labels[a]} vs ${basis.labels[b]}: ${cosine}`);
      }
    }
  });

  it('CANNOT explain a synthetic nose — the falsifiability guard', () => {
    // If this ever passes trivially, every enrollment threshold below is
    // measuring the basis rather than the reconstruction, and the whole suite
    // becomes v1's "the pads stay on the nose" check: a residual that is zero by
    // construction.
    const population = generatePopulation(mesh, basis, { count: 8 });
    const residuals = population.map((s) => subjectResidualAgainstBasis(basis, s).residualRmsMm);
    const worstCase = Math.max(...residuals);
    const median = distribution(residuals).median;
    assert.ok(
      median > 0.15,
      `the basis explains the synthetic noses too well (median residual ${median.toFixed(3)} mm) — ` +
      'the enrollment tests would be measuring nothing',
    );
    assert.ok(worstCase > 0.3, `worst residual only ${worstCase.toFixed(3)} mm`);
  });

  it('has the template itself as its mean, so a zero delta is a zero residual', () => {
    // Retitled from "explains the template itself exactly", which is not what
    // this can see. `basis.mean` is byte-identical to `mesh.positions`, so the
    // delta handed to `basisExplains` is all zeros and the residual is 0 no
    // matter what the modes contain — verified by zeroing them, randomising
    // them, and scaling them by 1000, all of which leave this line green.
    //
    // It is kept because it does pin something real (mean equals template, and
    // `basisExplains` returns zero on a zero delta rather than something else)
    // and it is the only upper-bound check on `basisExplains`. The test below is
    // the one that exercises the modes.
    const { residualRmsMm } = basisExplains(basis, mesh.positions);
    assert.ok(residualRmsMm < 1e-9, `residual ${residualRmsMm}`);
  });

  it('recovers the coefficients it was given, which the mean-residual check cannot see', () => {
    // Build a target FROM the basis at known coefficients and ask the basis to
    // explain it back. Now the delta is non-zero, the least-squares fit has
    // something to do, and a corrupted mode changes the answer.
    const rng = createRng(811);
    const want = new Float64Array(basis.dim);
    for (let k = 0; k < basis.dim; k++) want[k] = rng.range(-1.2, 1.2);
    const target = new Float64Array(mesh.vertexCount * 3);
    evaluateBasis(basis, want, target);

    const got = basisExplains(basis, target);
    // The modes are orthonormalised, so a target built from them is inside the
    // span exactly and the fit is exact to machine precision, not approximately.
    assert.ok(
      got.residualRmsMm < 1e-9,
      `a shape built from the basis left ${got.residualRmsMm.toFixed(6)} mm of residual — ` +
      'either the modes are no longer orthogonal or the fit is not solving',
    );
    for (let k = 0; k < basis.dim; k++) {
      assert.ok(
        Math.abs(got.coeffs[k] - want[k]) < 1e-6,
        `mode ${basis.labels[k]}: asked ${want[k].toFixed(4)}, recovered ${got.coeffs[k].toFixed(4)}`,
      );
    }
  });
});

// --------------------------------------------------------------- population

describe('the synthetic population', () => {
  it('stays inside the human range', () => {
    const population = generatePopulation(mesh, basis, { count: 15 });
    for (const subject of population) {
      const m = subject.measurements;
      assert.ok(
        m.templeWidth >= PLAUSIBLE.templeWidthMm[0] && m.templeWidth <= PLAUSIBLE.templeWidthMm[1],
        `${subject.id} temple width ${m.templeWidth.toFixed(0)} mm is not a human face`,
      );
      assert.ok(m.noseWidth >= PLAUSIBLE.noseWidthMm[0] && m.noseWidth <= PLAUSIBLE.noseWidthMm[1]);
    }
    // A rejection rate near 1 would mean the basis is mostly generating
    // impossible faces and the population is no longer a fair sample.
    assert.ok(lastRejectionCount < population.length * 3, `${lastRejectionCount} rejections`);
  });

  it('spans a real spread of noses and iris diameters', () => {
    const population = generatePopulation(mesh, basis, { count: 15 });
    const noses = population.map((s) => s.measurements.noseWidth);
    const irises = population.map((s) => s.irisDiameterMm);
    assert.ok(Math.max(...noses) - Math.min(...noses) > 8, 'nose widths are too similar');
    // The iris spread is the whole point of the scale finding: a fixed 11.7 mm
    // constant has to be wrong for some of these subjects or the bias is
    // untestable.
    assert.ok(Math.max(...irises) - Math.min(...irises) > 0.8, 'iris diameters are too similar');
  });

  it('gives every (subject, camera) cell of the sweep its own noise stream', () => {
    // The salt used to be `subject.id.length * 31 + geometry.name.length`, and
    // every generated id is `S00`, `S01`, ... — exactly three characters — while
    // `eye-level` and `phone-lap` are both nine. The default 17 x 3 grid
    // therefore held six distinct noise streams, and every scalar accuracy
    // number in the tree was common-mode across the cells that shared one.
    const population = generatePopulation(mesh, basis, { count: 6 });
    const captures: ReturnType<typeof synthesizeCapture>[] = [];
    for (const s of population) {
      for (const g of CAMERA_LADDER) {
        captures.push(synthesizeCapture(mesh, s, g, { framesPerBeat: 4 }));
      }
    }
    assert.equal(captures.length, population.length * CAMERA_LADDER.length);
    assert.doesNotThrow(() => assertDistinctNoiseStreams(captures));

    // And the instrument can fail, which is this tree's house rule. Exact
    // equality is deliberate: two captures drawn off one stream agree bit for
    // bit through the final frame, so any non-zero difference already proves the
    // streams are distinct and a tolerance would only weaken it.
    assert.throws(
      () => assertDistinctNoiseStreams([captures[0], captures[0]]),
      /same\s+noise stream/,
    );
  });

  it('threads a campaign seed through both noise domains, and no seed means the historical run', () => {
    // The replication campaign's whole contract in one test. `populationSeedFor`
    // and `captureSeedFor` are the ONLY sanctioned spelling of the fold from
    // one campaign seed into the two independent noise domains (who the
    // subjects are, what the detector saw of them). Three properties carry
    // every published sweep:
    //
    //   1. the same campaign seed reproduces the same figure, byte for byte;
    //   2. different seeds are different realisations;
    //   3. NO seed is the historical default run — `undefined` must pass
    //      through as "the 0x5eed / 0xc0ffee defaults", never as seed 0, or
    //      every unseeded caller silently lands on a different (and shared)
    //      stream. That exact clobber is how `{ seed: maybeSeed }` once put
    //      every caller on createRng(0).
    const geometry = CAMERA_LADDER[0];
    const samePos = (a: Float64Array, b: Float64Array) =>
      a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
    const sameCapture = (a: ReturnType<typeof synthesizeCapture>, b: typeof a) =>
      a.frames.length === b.frames.length
      && a.frames.every((f, i) => samePos(f.landmarks, b.frames[i].landmarks));

    // Population domain.
    const p1 = generatePopulation(mesh, basis, { count: 2, seed: populationSeedFor(7) });
    const p2 = generatePopulation(mesh, basis, { count: 2, seed: populationSeedFor(7) });
    const p3 = generatePopulation(mesh, basis, { count: 2, seed: populationSeedFor(8) });
    const p0 = generatePopulation(mesh, basis, { count: 2 });
    const pu = generatePopulation(mesh, basis, { count: 2, seed: undefined });
    assert.ok(p1.every((s, i) => samePos(s.positions, p2[i].positions)), 'seed 7 did not replicate');
    assert.ok(!p1.every((s, i) => samePos(s.positions, p3[i].positions)), 'seeds 7 and 8 drew the same faces');
    assert.ok(p0.every((s, i) => samePos(s.positions, pu[i].positions)),
      'an explicitly-undefined seed drew different faces from the default — the undefined guard is gone');
    assert.ok(!p0.every((s, i) => samePos(s.positions, p1[i].positions)),
      'campaign seed 7 reproduced the HISTORICAL population — the fold is the identity');

    // Capture domain, on one fixed subject.
    const subject = p0[0];
    const c1 = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 4, seed: captureSeedFor(7) });
    const c2 = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 4, seed: captureSeedFor(7) });
    const c3 = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 4, seed: captureSeedFor(8) });
    const c0 = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 4 });
    const cu = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 4, seed: undefined });
    assert.ok(sameCapture(c1, c2), 'capture seed 7 did not replicate');
    assert.ok(!sameCapture(c1, c3), 'capture seeds 7 and 8 drew the same noise');
    assert.ok(sameCapture(c0, cu), 'undefined capture seed is not the default');
    assert.ok(!sameCapture(c0, c1), 'campaign capture seed 7 reproduced the historical run');

    // The degenerate seed is refused at the fold, not just in acrossSeeds:
    // 0xffffffff aliases the unseeded run through the fork mix.
    assert.throws(() => populationSeedFor(0xffffffff), /0xffffffff|campaign seed/);
    assert.throws(() => captureSeedFor(0xffffffff), /0xffffffff|campaign seed/);
    assert.equal(populationSeedFor(undefined), undefined);
    assert.equal(captureSeedFor(undefined), undefined);
  });

  it('keeps nose detail in the named extremes that the basis cannot explain', () => {
    // `broad-low` and `narrow-high` are appended to every population and carry
    // the enrollment tests built on them. Reseeding `namedExtreme` redrew their
    // noise field and their basis residual fell from about 0.52 mm rms to about
    // 0.20 — one new random draw, not a trend, but nothing stops the NEXT reseed
    // drawing a residual of zero, at which point those two subjects would
    // silently stop being able to falsify anything.
    //
    // Currently measured: broad-low 0.203 mm rms, narrow-high 0.207. The 0.10
    // bar leaves room for a reseed without letting the residual reach zero.
    const population = generatePopulation(mesh, basis, { count: 4 });
    for (const id of ['broad-low', 'narrow-high']) {
      const s = population.find((p) => p.id === id);
      assert.ok(s, `no ${id} subject`);
      const r = subjectResidualAgainstBasis(basis, s!);
      assert.ok(
        r.residualRmsMm > 0.10,
        `${id} residual ${r.residualRmsMm.toFixed(3)} mm rms — the basis has absorbed its ` +
        'nose and it can no longer falsify anything',
      );
    }
  });
});

// ---------------------------------------------------------------------- PnP

describe('pose against a known model', () => {
  it('does not degrade with yaw — the architectural claim', () => {
    // Across the POPULATION and the whole camera ladder, not one subject.
    //
    // The single-subject version of this test failed at 1.52 degrees against a
    // 1.50 bar after an unrelated fix, on a bucket holding a handful of frames.
    // That is not a regression, it is the variance of one draw — the same lesson
    // the free-form field test learned. A claim about the architecture has to be
    // measured on a population.
    const population = generatePopulation(mesh, basis, { count: 5 });
    const byYaw = new Map<number, number[]>();

    for (const subject of population) {
      for (const geometry of CAMERA_LADDER) {
        const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 5 });
        for (const frame of capture.frames) {
          const correspondences = buildCorrespondences(
            frame.landmarks, frame.sigmaPx, mesh.vertexCount,
          );
          if (correspondences.length < 40) continue;
          const result = solvePnP(subject.positions, correspondences, capture.trueIntrinsics);
          const deg = (rotationAngleBetween(result.pose.R, frame.pose.R) * 180) / Math.PI;
          const bucket = Math.round((Math.abs(frame.trueYaw) * 180) / Math.PI / 15) * 15;
          if (!byYaw.has(bucket)) byYaw.set(bucket, []);
          byYaw.get(bucket)!.push(deg);
        }
      }
    }

    const frontal = distribution(byYaw.get(0) ?? []).median;
    assert.ok(frontal < 0.8, `frontal rotation error ${frontal.toFixed(2)} deg`);

    // Measured: 0.41 deg median at frontal, rising to 0.91 at 45 degrees and
    // falling back to 0.56 at 90 — a worst-bucket ratio of 2.2. The bars carry
    // headroom over that. v1's equivalent (fitting the average head) runs 2.3 to
    // 4.2 degrees and puts the bridge 17 to 30 mm from where it belongs.
    for (const [bucket, values] of byYaw) {
      if (values.length < 15) continue; // too few frames to have a median worth reading
      const median = distribution(values).median;
      assert.ok(
        median < 1.5,
        `yaw ${bucket} deg: ${median.toFixed(2)} deg median over ${values.length} frames`,
      );
      assert.ok(
        median < frontal * 3.5,
        `yaw ${bucket} deg is ${(median / frontal).toFixed(2)}x the frontal error`,
      );
    }
  });

  it('reprojection error is BLIND to fitting the wrong face', () => {
    // Not a control — a warning, and one of the more important facts in the
    // tree. Fitting the average head to a real face's landmarks produces a
    // reprojection residual barely worse than fitting the real geometry,
    // because the six pose parameters absorb the shape error. The residual
    // looks healthy while the frame is centimetres from where it belongs.
    //
    // This is exactly why v1's harness could not catch its own central problem:
    // every check it had was a self-consistency check, and self-consistency is
    // preserved by the failure. The next test measures the thing that is not.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const capture = synthesizeCapture(mesh, subject, CAMERA_LADDER[0], { framesPerBeat: 6 });

    const known: number[] = [];
    const average: number[] = [];
    for (const frame of capture.frames) {
      const correspondences = buildCorrespondences(
        frame.landmarks, frame.sigmaPx, mesh.vertexCount,
      );
      if (correspondences.length < 40) continue;
      known.push(solvePnP(subject.positions, correspondences, capture.trueIntrinsics).rmsPx);
      average.push(solvePnP(mesh.positions, correspondences, capture.trueIntrinsics).rmsPx);
    }
    const ratio = distribution(average).median / distribution(known).median;
    assert.ok(
      ratio < 1.3,
      `reprojection was ${ratio.toFixed(2)}x worse against the average head — if this ever ` +
      'becomes a large number, reprojection has become a usable discriminator and the ' +
      'comment above needs rewriting',
    );
  });

  it('but 3D placement is not — the average head puts the bridge centimetres away', () => {
    // The control that reprojection cannot provide. If this ever fails, the
    // personal model is doing nothing and the scan is not worth taking.
    const population = generatePopulation(mesh, basis, { count: 4 });
    const knownErr: number[] = [];
    const averageErr: number[] = [];

    const place = (pose: { R: Float64Array; t: Float64Array }, p: Float64Array, i: number) => {
      const x = p[i * 3], y = p[i * 3 + 1], z = p[i * 3 + 2];
      return [
        pose.R[0] * x + pose.R[1] * y + pose.R[2] * z + pose.t[0],
        pose.R[3] * x + pose.R[4] * y + pose.R[5] * z + pose.t[1],
        pose.R[6] * x + pose.R[7] * y + pose.R[8] * z + pose.t[2],
      ];
    };

    for (const subject of population) {
      const capture = synthesizeCapture(mesh, subject, CAMERA_LADDER[0], { framesPerBeat: 5 });
      for (const frame of capture.frames) {
        const correspondences = buildCorrespondences(
          frame.landmarks, frame.sigmaPx, mesh.vertexCount,
        );
        if (correspondences.length < 40) continue;
        const bridge = 6; // LM.NOSE_BRIDGE
        const want = place(frame.pose, subject.positions, bridge);

        const a = solvePnP(subject.positions, correspondences, capture.trueIntrinsics);
        const gotA = place(a.pose, subject.positions, bridge);
        knownErr.push(Math.hypot(gotA[0] - want[0], gotA[1] - want[1], gotA[2] - want[2]));

        const b = solvePnP(mesh.positions, correspondences, capture.trueIntrinsics);
        const gotB = place(b.pose, mesh.positions, bridge);
        averageErr.push(Math.hypot(gotB[0] - want[0], gotB[1] - want[1], gotB[2] - want[2]));
      }
    }

    const known = distribution(knownErr).median;
    const average = distribution(averageErr).median;
    assert.ok(known < 3, `known-model bridge error ${known.toFixed(2)} mm`);
    assert.ok(
      average > known * 3,
      `the average head put the bridge ${average.toFixed(2)} mm out against ` +
      `${known.toFixed(2)} for the scanned one — expected at least 3x`,
    );
  });

  it('is unbiased when the reference point is off the principal point', () => {
    // `posit` had NO direct test at all, which is exactly why a 74-degree median
    // rotation error passed the whole suite: every other PnP test goes through
    // `solvePnP`, and `refinePnP` absorbs the bias completely — identical output
    // to four decimals — so nothing that calls `solvePnP` can ever catch it.
    //
    // The bias lives in the `- x_0` / `- y_0` terms of posit's normal equations.
    // Removing them costs 73.6 degrees of median rotation error and 158.5 at the
    // worst over the 99 poses below.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const Kp = intrinsicsFromFov(1280, 720, 63);
    const idx: number[] = [];
    for (let i = 0; i < mesh.vertexCount; i += 7) idx.push(i);
    const ref = idx[0];

    const sweep = (offX: number, offY: number) => {
      const errors: number[] = [];
      for (let yaw = -80; yaw <= 80; yaw += 5) {
        for (const pitch of [-15, 0, 15]) {
          const pose = poseIdentity();
          poseRotationFromHeadEuler(pose.R, (yaw * Math.PI) / 180, (pitch * Math.PI) / 180, 0);
          const p = subject.positions;
          const R = pose.R;
          const rr = [
            R[0] * p[ref * 3] + R[1] * p[ref * 3 + 1] + R[2] * p[ref * 3 + 2],
            R[3] * p[ref * 3] + R[4] * p[ref * 3 + 1] + R[5] * p[ref * 3 + 2],
            R[6] * p[ref * 3] + R[7] * p[ref * 3 + 1] + R[8] * p[ref * 3 + 2],
          ];
          // The load-bearing line: the offsets put the reference vertex OFF the
          // optical axis, which is what makes x_0 and y_0 non-zero.
          pose.t.set([-rr[0] + offX, -rr[1] + offY, -rr[2] + 500]);

          const cs: { vertex: number; u: number; v: number; sigmaPx: number }[] = [];
          const uv = new Float64Array(2);
          for (const i of idx) {
            const cam = Float64Array.of(
              R[0] * p[i * 3] + R[1] * p[i * 3 + 1] + R[2] * p[i * 3 + 2] + pose.t[0],
              R[3] * p[i * 3] + R[4] * p[i * 3 + 1] + R[5] * p[i * 3 + 2] + pose.t[1],
              R[6] * p[i * 3] + R[7] * p[i * 3 + 1] + R[8] * p[i * 3 + 2] + pose.t[2],
            );
            if (!project(uv, Kp, cam)) continue;
            cs.push({ vertex: i, u: uv[0], v: uv[1], sigmaPx: 1 });
          }
          // posit uses correspondences[0] as its reference, so the order matters.
          if (cs.length < 20 || cs[0].vertex !== ref) continue;
          errors.push((rotationAngleBetween(posit(subject.positions, cs, Kp).R, pose.R) * 180) / Math.PI);
        }
      }
      return errors;
    };

    // These are exact, noise-free projections of the exact model, so the correct
    // answer is exactly zero. Measured: 99 poses, worst 4.1e-5 degrees.
    const offAxis = sweep(60, 45);
    assert.equal(offAxis.length, 99, `only ${offAxis.length} poses were usable`);
    assert.ok(
      Math.max(...offAxis) < 1e-3,
      `worst off-axis rotation error ${Math.max(...offAxis).toExponential(2)} deg`,
    );

    // THE CONTROL, and it must stay labelled as one: with the reference
    // projecting exactly onto the principal point, this half passes EVEN WITH
    // THE BUG — measured 0.0000 degrees both ways — because the omitted term is
    // x_0 and is identically zero there. Simplifying the test down to this half
    // would silently delete all of its power.
    const onAxis = sweep(0, 0);
    assert.ok(
      Math.max(...onAxis) < 1e-3,
      `worst on-axis rotation error ${Math.max(...onAxis).toExponential(2)} deg`,
    );
  });

  it('is settled after two accepted steps when it is warm-started', () => {
    // The claim in `src/track/pnp.ts`'s module header, kept as a measurement
    // rather than a sentence. It also guards against someone "fixing" the header
    // by tightening `stepRad`/`stepMm`, or loosening them and quietly paying for
    // it in accuracy.
    const rotGap: number[] = [];
    const trGap: number[] = [];
    const iterations: number[] = [];
    for (const subject of generatePopulation(mesh, basis, { count: 4 })) {
      for (const geometry of CAMERA_LADDER) {
        const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 22 });
        let prev: Pose | null = null;
        for (const frame of capture.frames) {
          const cs = buildCorrespondences(frame.landmarks, frame.sigmaPx, mesh.vertexCount);
          if (cs.length < 40) continue;
          if (!prev) {
            prev = solvePnP(subject.positions, cs, capture.trueIntrinsics).pose;
            continue;
          }
          const full = refinePnP(subject.positions, cs, capture.trueIntrinsics, prev);
          const two = refinePnP(subject.positions, cs, capture.trueIntrinsics, prev, {
            maxIterations: 2,
          });
          rotGap.push((rotationAngleBetween(full.pose.R, two.pose.R) * 180) / Math.PI);
          trGap.push(Math.hypot(
            full.pose.t[0] - two.pose.t[0],
            full.pose.t[1] - two.pose.t[1],
            full.pose.t[2] - two.pose.t[2],
          ));
          iterations.push(full.iterations);
          prev = full.pose;
        }
      }
    }
    // Measured over 5,526 warm-started frames: 0.026 deg and 0.071 mm on the
    // median, 0.120 deg and 0.541 mm at p95.
    assert.ok(rotGap.length > 3000, `only ${rotGap.length} warm-started frames`);
    assert.ok(
      distribution(rotGap).median < 0.10,
      `stopping at two steps costs ${distribution(rotGap).median.toFixed(3)} deg on the median`,
    );
    assert.ok(
      distribution(trGap).median < 0.25,
      `stopping at two steps costs ${distribution(trGap).median.toFixed(3)} mm on the median`,
    );
    // The other half, which keeps the header honest about the LOOP rather than
    // about the answer. Measured median 7, mean 8.0.
    assert.ok(
      distribution(iterations).median >= 4,
      `the warm-started solve now converges in ${distribution(iterations).median} iterations — ` +
      'if that is two, the header\'s paragraph about stepRad/stepMm sitting six orders ' +
      'below the noise floor is false and has to be rewritten',
    );
  });
});

// --------------------------------------------------------------- enrollment

describe('enrollment', () => {
  it('recovers a nose it has never seen, to about a millimetre', () => {
    // `framesPerBeat` is back at 12. It sat at 14 for one day, dodging a
    // selection/coverage seam: at the keyframes=24 default, farthest-point
    // sampling could collapse a sparse capture's pitch span below the coverage
    // threshold and degrade a scan that contained the nod. `selectKeyframes`
    // now guarantees the six per-axis extreme frames, so the selection's span
    // equals the capture's by construction — the repair is pinned by its own
    // test below ("the selection cannot lose a nod the wearer performed").
    // Measured here at fpb 12 with the repaired selector: nose median 0.83 mm,
    // worst 1.06, all subjects clean.
    const population = generatePopulation(mesh, basis, { count: 4 });
    const noseErrors: number[] = [];
    const protrusionErrors: number[] = [];

    for (const subject of population) {
      const geometry = CAMERA_LADDER[0];
      const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
      const result = enroll({
        mesh, basis,
        frames: capture.frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
        imageWidth: geometry.width, imageHeight: geometry.height,
        // The subject's true iris, so this test measures the RECONSTRUCTION and
        // not the population bias of a fixed iris constant — which has its own
        // test below.
        irisMm: subject.irisDiameterMm,
      });
      assert.ok(!result.model.degraded, `scan degraded: ${result.model.notes.join('; ')}`);

      // A nose is NOT visible in every frame it appears in — at 35 degrees of
      // yaw the far sidewall is behind the nose — so its observation count must
      // come out strictly below the frame count. This holds for any real
      // visibility and fails only when something hands the bundle a confident
      // `fill(1)`, which is exactly what the app did: it destructured `sigmaPx`
      // out of `estimateSigma` and threw the visibility away. The fingerprint
      // reached a real wearer's diagnostics as `noseObservations` equal to
      // `framesUsed`, 48 and 48. Every test here passed the synthesizer's true
      // visibility, so nothing caught it.
      const q = result.model.quality?.nose;
      assert.ok(
        q !== undefined && q.observations < result.model.framesUsed,
        `nose observations ${q?.observations} against ${result.model.framesUsed} frames — ` +
        'every landmark was counted as fully visible, so visibility is being discarded',
      );

      const comparison = compareToTruth(result.model, subject, regions, mesh);
      noseErrors.push(comparison.perRegion.nose.rmsMm);
      protrusionErrors.push(Math.abs(comparison.measurementError.noseProtrusion));
    }

    // Measured median is ~0.7 mm with the realistic detector bias in place.
    // The bar is set with headroom; tighten it when the detector calibration
    // (docs/OPEN-QUESTIONS.md Q2) lands and the bias floor comes out.
    const nose = distribution(noseErrors);
    assert.ok(nose.median < 1.6, `nose surface error ${nose.median.toFixed(2)} mm`);
    assert.ok(nose.worst < 3.0, `worst nose error ${nose.worst.toFixed(2)} mm`);
    assert.ok(
      distribution(protrusionErrors).median < 1.6,
      `protrusion error ${distribution(protrusionErrors).median.toFixed(2)} mm`,
    );
  });

  it('the collapse guard actually fires, and only on collapse', () => {
    // `BundleReport.converged` was initialised `true` and cleared on exactly one
    // condition: `reprojectionStats` returning a non-finite rms, which happens
    // only when NOT ONE landmark in any frame projected in front of the camera.
    // `degraded` rests on it. Measured over 96 enrolments (60 healthy, 36
    // deliberately stressed with noise x2 to x14, 3 and 6 mm of detector bias,
    // no profile, no turn, `fieldPriorScale` 0, `shapePrior` 0, all intrinsics
    // unlocked) it never once went false.
    //
    // It is not being turned into a convergence test, because there is none to
    // be had: the bundle always converges, and every candidate — the lambda
    // ceiling, step acceptance, a residual plateau — either never fires or
    // fires on 92% of healthy scans. What this pins is that the flag does what
    // it now SAYS it does.
    //
    // **And it has to reach the guard to do that.** Driving `enroll` with more
    // and more landmark noise does NOT: measured on this fixture, at 10 px it
    // returns `converged: true` beside a nose 15 mm from truth, and from 14 px
    // upward it returns `rounds: 0` — the pose-initialisation gate rejects the
    // scan and `enroll` takes its degraded early return, so `runBundle` is
    // never called at all and the `false` comes from the stub. A test that
    // stopped there would be asserting on a line it never executed. So the
    // guard is exercised directly.
    const geom = CAMERA_LADDER[0];
    const subject1 = generatePopulation(mesh, basis, { count: 1 })[0];
    const cap = synthesizeCapture(mesh, subject1, geom, { framesPerBeat: 8 });
    const frames = () => cap.frames.map((f) => ({
      landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
      silhouette: f.silhouette, pose: poseClone(f.pose), beat: f.beat,
    }));
    const regionsC = standardRegions(mesh);

    // Healthy: the guard must NOT fire.
    const healthy = runBundle(createBundleState(
      mesh, basis, createDisplacementField(mesh, regionsC.nose), regionsC.nose,
      frames(), intrinsicsFromFov(geom.width, geom.height, 63),
    ), { rounds: 1 });
    assert.ok(healthy.converged, 'a healthy solve reported itself collapsed');
    assert.ok(Number.isFinite(healthy.reprojectionRmsPx));

    // Catastrophe: every pose behind the camera, so `project` refuses every
    // landmark and `reprojectionStats` has nothing to average.
    const behind = frames();
    for (const f of behind) f.pose.t[2] = -500;
    const dead = runBundle(createBundleState(
      mesh, basis, createDisplacementField(mesh, regionsC.nose), regionsC.nose,
      behind, intrinsicsFromFov(geom.width, geom.height, 63),
    ), { rounds: 1 });
    assert.ok(!dead.converged,
      'not one landmark projected in front of the camera and the solve still '
      + 'reported itself converged');

    // And the gap this leaves, asserted rather than described, so that anyone
    // who later reads `converged` as an accuracy statement is contradicted by a
    // test: a scan can be undegraded, converged, and 15 mm wrong.
    const noisy = enroll({
      mesh, basis,
      frames: synthesizeCapture(mesh, subject1, geom, { framesPerBeat: 8, noisePx: 10 })
        .frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
      imageWidth: geom.width, imageHeight: geom.height,
    });
    assert.ok(noisy.bundle.converged && !noisy.model.degraded,
      'ten pixels of landmark noise now degrades the scan — if that is a real '
      + 'improvement, this assertion is the thing to update, and the comment above '
      + 'it about what `converged` cannot see');
    assert.ok(noisy.bundle.reprojectionRmsPx > 20,
      `the noisy fixture came back at ${noisy.bundle.reprojectionRmsPx.toFixed(1)} px rms, `
      + 'so it is no longer the bad-evidence case this is about');
  });

  it('a scan that got no silhouette says so out loud', () => {
    // `useSilhouette` defaults true and every silhouette path in `bundle.ts`
    // then `continue`s on `!frame.silhouette`, so a caller who supplies none
    // gets the `no-silhouette` ablation and is told nothing. Production did
    // exactly that on 100% of real frames — 0 of 141 and 0 of 165 on the two
    // scans in `docs/REAL-FACE.md` — and the only field that could show it,
    // `BundleReport.silhouetteResiduals`, had no consumers at all.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const geometry = CAMERA_LADDER[0];
    const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 8 });
    const run = (silhouette: (f: { silhouette: Float64Array }) => Float64Array | null) => enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: silhouette(f), beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
    });

    // Exactly what `collectFrame` and `enroll.worker.ts` used to hand the bundle.
    const withoutIt = run(() => null);
    assert.equal(withoutIt.bundle.silhouetteResiduals, 0);
    assert.ok(withoutIt.model.notes.some((n) => /silhouette/.test(n)),
      'the contour term was skipped on every frame and the scan did not say so');

    // And the note must not fire when the term did run, or it is noise.
    const withIt = run((f) => f.silhouette);
    assert.ok(withIt.bundle.silhouetteResiduals > 0,
      'the harness supplied a silhouette and the bundle used none of it');
    assert.ok(!withIt.model.notes.some((n) => /silhouette/.test(n)),
      `a scan WITH a silhouette was told it had none: ${withIt.model.notes.join('; ')}`);
  });

  it('rigidity is a sigma multiplier, and the nose boost does not move the stats gate', () => {
    // `landmarkRigidity`'s docstring claimed 0..1 for a function that returns up
    // to 1.8 — while the SAME docstring, twenty-two lines lower, said the nose
    // boost was deliberate. The stale half was the range.
    //
    // This is a documentation defect and the "over-weighting" it implied is not
    // one: measured as a paired ablation over 72 enrolments (3 seeds x 8
    // subjects x 3 geometries, boost on against clamped to 1), nose RMS moves
    // -0.011 mm on the paired median with 40 of 72 improving and every other
    // accuracy metric is a coin flip. What IS worth pinning is the trap.
    const rig = landmarkRigidity(mesh, standardRegions(mesh).nose);
    const max = Math.max(...rig);
    assert.ok(max > 1.5 && max <= 1.8,
      `rigidity peaks at ${max.toFixed(3)} — the nose boost has been normalised away, `
      + 'which is not the same as clamped: see below');

    // **Clamping is harmless; SCALING is not.** Every consumer divides the
    // claimed sigma by `sqrt(r)` or multiplies the residual by it, so there is
    // nothing to normalise against — but `reprojectionStats` also GATES on
    // `rigidity < 0.5`, and dividing the array through by 1.8 pushes 8 vertices
    // under that gate: `residualsUsed` drops 7416 -> 7224 and `varianceFactor`
    // moves with it. Clamping to 1 moves neither, because no vertex crosses 0.5
    // either way.
    const gated = (a: Float64Array) => a.reduce((n, v) => n + (v >= 0.5 ? 1 : 0), 0);
    const clamped = Float64Array.from(rig, (v) => Math.min(v, 1));
    assert.equal(gated(rig), gated(clamped),
      'the nose boost now lifts vertices across reprojectionStats\' 0.5 gate, so '
      + 'residualsUsed and varianceFactor depend on it');
    const scaled = Float64Array.from(rig, (v) => v / 1.8);
    assert.ok(gated(scaled) < gated(rig),
      'dividing rigidity through by its own maximum no longer changes what '
      + 'reprojectionStats counts — this test has stopped describing the trap it names');
  });

  it('the free-form field actually moves — regression for a silent failure', () => {
    // The field was inert for an entire build: a truncated Laplacian prior made
    // the normal equations non-positive-definite, `ldlt` returned false, and
    // `solveField` took its early return. Nothing visible changed. The ablation
    // "with field" vs "without field" showed no difference because there was no
    // difference.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const geometry = CAMERA_LADDER[0];
    const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 10 });
    const result = enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
    });
    assert.equal(result.bundle.fieldFailures, 0, 'the nose field failed to factorise');
    assert.ok(
      result.model.displacementRmsMm > 0.15,
      `the field moved ${result.model.displacementRmsMm.toFixed(3)} mm rms — ` +
      'a real face is never exactly the basis shape, so this being near zero means the stage did not run',
    );
  });

  it('the field earns its place', () => {
    // Not "does it run" but "does it help" — and the answer is now a SETTLED,
    // replicated measurement rather than this test's one draw. The 2026-08-22
    // displacement-field campaign (5 seeds x 14 subjects, eye-level, shipped
    // and clean configs) adopted `fieldPriorScale` 8: field-on beats field-off
    // on median nose RMS in >= 4/5 seeds in BOTH configs at every scale swept
    // (x1..x8), with x8 the measured-best qualifying cell — clean
    // median-of-seeds 0.668 mm on against 0.884 off (ratio 0.756), pad strip
    // better outright. The full record lives at `BUNDLE_DEFAULTS.fieldPriorScale`.
    //
    // This test pins that verdict on ONE deterministic realisation, and the
    // draw is chosen deliberately, with the alternatives measured and recorded:
    //
    //   count 6, campaign seed 1 (this fixture):   0.702 on / 1.002 off = 0.70
    //   count 6, campaign seeds 2 / 3:             0.66 / 0.88
    //   count 6, the historical unseeded draw:     0.766 / 0.735 = 1.04 — LOSES
    //   count 12, the historical unseeded draw:    0.860 / 0.934 = 0.92
    //
    // The unseeded draw is a genuine losing realisation of the median (while
    // still fixing the tail: its two worst field-off subjects, 1.55 and 1.29 mm,
    // come back to 0.97 and 0.86 with the field on). The campaign already
    // recorded that roughly one realisation in five shows a deficit under the
    // shipped config, so a single-draw test on an arbitrary draw would be
    // re-measuring seed variance, not the verdict. Seeded, the test asks the
    // settled question — "does the shipped x8 field help on a known-fair draw"
    // — and fails only when the FIELD changes, not when the dice do.
    //
    // Detector bias is off and the true iris supplied so the comparison
    // isolates the field rather than the shared bias floor both arms carry.
    const population = generatePopulation(mesh, basis, {
      count: 6, seed: populationSeedFor(1),
    });
    const geometry = CAMERA_LADDER[0];
    const withField: number[] = [];
    const without: number[] = [];

    for (const subject of population) {
      const capture = synthesizeCapture(mesh, subject, geometry, {
        framesPerBeat: 12, biasMm: 0, seed: captureSeedFor(1),
      });
      const frames = capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      }));
      const common = {
        mesh, basis, frames,
        imageWidth: geometry.width, imageHeight: geometry.height,
        irisMm: subject.irisDiameterMm,
      };
      withField.push(
        compareToTruth(enroll({ ...common }).model, subject, regions, mesh).perRegion.nose.rmsMm,
      );
      without.push(
        compareToTruth(
          enroll({ ...common, bundle: { solveField: false } }).model, subject, regions, mesh,
        ).perRegion.nose.rmsMm,
      );
    }

    // Measured on this fixture: 0.7024 with the field, 1.0023 without —
    // ratio 0.70 against the 0.9 bar, so the bar has 0.2 of headroom while
    // still failing an inert field outright (an inert field measures ~1.0).
    const a = distribution(withField).median;
    const b = distribution(without).median;
    assert.ok(
      a < b * 0.9,
      `the free-form field gave ${a.toFixed(3)} mm against ${b.toFixed(3)} without it — ` +
      'it is not earning its parameters. If this moved because the field itself changed, ' +
      're-run the fieldPriorScale campaign (BUNDLE_DEFAULTS.fieldPriorScale) before touching the bar.',
    );
  });

  it('reports incomplete coverage rather than inventing a nose', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const geometry = CAMERA_LADDER[0];
    // A capture with no turn at all: only the centre beat.
    const capture = synthesizeCapture(mesh, subject, geometry, {
      framesPerBeat: 10, includeProfile: false, includeLean: false,
    });
    const centreOnly = capture.frames.filter((f) => f.beat === 'centre');
    const result = enroll({
      mesh, basis,
      frames: centreOnly.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
    });
    assert.ok(result.model.degraded, 'a turn-less scan reported itself as complete');
    assert.ok(result.coverage.missing.includes('turn'));
    assert.ok(!result.coverage.canSolveIntrinsics);
    assert.ok(result.model.notes.length > 0);
  });

  it('keyframe selection spans the poses rather than the frame rate', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const geometry = CAMERA_LADDER[0];
    const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 20 });
    const frames = capture.frames.map((f) => ({
      landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
      silhouette: f.silhouette, beat: f.beat, pose: f.pose,
    }));
    const selection = selectKeyframes(frames, { count: 40 });
    assert.equal(selection.frames.length, 40);
    assert.ok(selection.yawSpanDeg > 90, `yaw span ${selection.yawSpanDeg.toFixed(0)} deg`);
    const verdict = assessCoverage(selection, frames);
    assert.ok(verdict.sufficient);
    assert.ok(verdict.hasProfile);
  });

  it('the selection cannot lose a nod the wearer performed — the k24 defect, repaired', () => {
    // RECORDED 2026-08-22, REPAIRED 2026-08-23. The keyframes=24 adoption
    // measured millimetre metrics and never looked at the `degraded` flag. On
    // the historical unseeded draw, subject S01's 12-frames-per-beat capture
    // CONTAINED the nod, but farthest-point sampling at 24 keyframes spent the
    // budget on yaw and lean, the selection's pitch span collapsed to 8.9
    // degrees against the 12-degree threshold, and a compliant wearer was told
    // to "Nod once" — advice about a thing they had already done.
    //
    // The repair is in `selectKeyframes`: after the frontal anchors it
    // guarantees the six per-axis extreme frames, so the selection's span
    // equals the capture's span on every axis BY CONSTRUCTION, at any count
    // that fits them. Two assertions pin it:
    //
    // 1. The wearer-facing outcome: the S01/fpb-12 draw that used to degrade
    //    must enroll clean.
    // 2. The structural guarantee itself: spans at count 24 must equal spans
    //    at count 48 exactly — true only while the extremes are guaranteed,
    //    false the day someone deletes that block, regardless of draw.
    const geometry = CAMERA_LADDER[0];
    const s01 = generatePopulation(mesh, basis, { count: 4 })[1];
    assert.equal(s01.id, 'S01');

    const capture = synthesizeCapture(mesh, s01, geometry, { framesPerBeat: 12 });
    assert.ok(
      capture.frames.some((f) => f.beat === 'pitch-down'),
      'the capture no longer contains the nod, so this fixture measures nothing',
    );

    const sparse = enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
    });
    assert.ok(
      !sparse.model.degraded && !sparse.coverage.missing.includes('nod'),
      `the repaired selector lost the nod again on the S01/fpb-12 draw ` +
      `(${sparse.model.notes.join('; ')}) — check the per-axis extremes block ` +
      'in selectKeyframes before touching this test',
    );

    // The structural half, on posed frames (the harness's true poses stand in
    // for the PnP init, exactly as the spans test above uses them). Equality is
    // exact: both selections contain the same six extreme frames, so max minus
    // min is the same subtraction of the same numbers.
    //
    // Count 16, not 24, and that choice is measured: with the guarantee
    // deleted from the compiled dist, farthest-point on TRUE poses happens to
    // keep the extremes at 24 on every subject of this draw (the original
    // defect lived on the noisier PnP-estimated poses), so a 24-vs-48
    // comparison could not fail — the exact "check that cannot fail" this
    // repo bans. At 16 the broken selector loses 11.4-14.1 degrees of pitch
    // span on EVERY subject at both 12 and 20 frames per beat; the guarantee
    // makes 16 and 48 exactly equal. Verified both ways 2026-08-23.
    const posed = capture.frames.map((f) => ({
      landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
      silhouette: f.silhouette, beat: f.beat, pose: f.pose,
    }));
    const k16 = selectKeyframes(posed, { count: 16 });
    const k48 = selectKeyframes(posed, { count: 48 });
    for (const axis of ['yawSpanDeg', 'pitchSpanDeg', 'distanceSpanPct'] as const) {
      assert.equal(
        k16[axis], k48[axis],
        `${axis}: ${k16[axis]} at 16 keyframes vs ${k48[axis]} at 48 — the ` +
        'per-axis extremes guarantee in selectKeyframes is gone, and a sparse ' +
        'draw can once again lose a motion the wearer performed',
      );
    }
  });

  it('makes one face\'s scalar error a distribution, not a number', () => {
    // The reason finding 15 mattered. The scalar measurement errors —
    // `noseProtrusion`, `bridgeStandoff` — are NOISE-DOMINATED, so a single
    // capture draw of one subject says almost nothing about the pipeline. This
    // is the falsifiability instrument for every future "we improved protrusion
    // by 0.3 mm" claim: if the claim is smaller than this span, it is a draw.
    //
    // Only the capture SEED varies, so the face is byte-identical across the ten
    // draws and nothing here is measuring the population.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const geometry = CAMERA_LADDER[0];
    const protrusion: number[] = [];
    for (let d = 0; d < 10; d++) {
      const capture = synthesizeCapture(mesh, subject, geometry, {
        framesPerBeat: 14, seed: 0xc0ffee + d * 7919,
      });
      const model = enroll({
        mesh, basis,
        frames: capture.frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
        imageWidth: geometry.width, imageHeight: geometry.height,
        irisMm: subject.irisDiameterMm,
      }).model;
      protrusion.push(Math.abs(
        compareToTruth(model, subject, regions, mesh).measurementError.noseProtrusion,
      ));
    }
    const span = Math.max(...protrusion) - Math.min(...protrusion);
    // Measured 2026-08-22 at the campaign defaults (keyframes 24,
    // fieldPriorScale 8): the ten draws run 0.060 to 0.529 mm — span 0.47, an
    // 8.7-fold ratio. The pre-campaign defaults measured 0.075 to 1.388 (span
    // 1.31, 18-fold); the shrink is the pipeline getting steadier, and the
    // bars moved down WITH the measurement because the thing this test guards
    // is not the size of the spread but its existence: byte-shared noise
    // streams (the `subject.id.length` salting defect) make the span collapse
    // toward zero and the ratio toward 1, and 0.25 / 4x still fail that state
    // outright while giving the measured draw about 2x of headroom.
    assert.ok(
      span > 0.25,
      `ten independent capture draws of ONE face span only ${span.toFixed(2)} mm of ` +
      'protrusion error — if this collapses, the capture noise has stopped varying and ' +
      'every scalar in the reports is one frozen draw',
    );
    assert.ok(
      Math.max(...protrusion) / Math.max(Math.min(...protrusion), 1e-9) > 4,
      `the best and worst draw differ by only ${(Math.max(...protrusion) / Math.min(...protrusion)).toFixed(1)}x`,
    );
  });
});

// -------------------------------------------------------------------- scale

describe('the metric ruler', () => {
  it('the fixed iris constant is biased against a real population', () => {
    // The finding, as a test. v1's `IRIS_DIAMETER_CM = 1.17` is a white-adult
    // mean; published group means run from 11.10 mm. Using it on a wearer whose
    // iris is genuinely 11.10 mm reads every length 5.4% long, and that error is
    // systematic, so no amount of averaging removes it.
    const japanese = POPULATION_HVID.japanese.meanMm;
    const biasPct = ((IRIS.defaultMm - japanese) / japanese) * 100;
    assert.ok(biasPct > 4, `expected a >4% bias, got ${biasPct.toFixed(1)}%`);

    // And it must show up as a real error on a real subject.
    const population = generatePopulation(mesh, basis, { count: 6 });
    const smallIris = population.reduce((a, b) => (a.irisDiameterMm < b.irisDiameterMm ? a : b));
    const geometry = CAMERA_LADDER[0];
    const capture = synthesizeCapture(mesh, smallIris, geometry, { framesPerBeat: 10 });
    const frames = capture.frames.map((f) => ({
      landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
      silhouette: f.silhouette, beat: f.beat,
    }));
    const common = { mesh, basis, frames, imageWidth: geometry.width, imageHeight: geometry.height };

    const withDefault = enroll({ ...common });
    const withTruth = enroll({ ...common, irisMm: smallIris.irisDiameterMm });
    const a = Math.abs(compareToTruth(withDefault.model, smallIris, regions, mesh).scaleErrorPct);
    const b = Math.abs(compareToTruth(withTruth.model, smallIris, regions, mesh).scaleErrorPct);
    assert.ok(
      a > b + 1,
      `the fixed iris constant cost only ${(a - b).toFixed(2)}% of scale on a ` +
      `${smallIris.irisDiameterMm.toFixed(2)} mm iris — expected more than 1%`,
    );
  });

  it('refuses to invent a scale when nothing resolved', () => {
    const result = solveScale({
      readings: [],
      intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    });
    assert.equal(result.estimate.source, 'assumed');
    assert.equal(result.pdMm, null);
    assert.ok(result.estimate.note.includes('no ruler'));
  });
});

// ------------------------------------------------------------------ tracking

describe('tracking', () => {
  it('holds the frame on the face through a full turn', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    const capture = synthesizeCapture(mesh, subject, CAMERA_LADDER[1], { framesPerBeat: 8 });
    const state = createTracker(model);

    let tracked = 0, total = 0;
    const errors: number[] = [];
    for (const frame of capture.frames) {
      const result = track(state, {
        landmarks: frame.landmarks, sigmaPx: frame.sigmaPx,
        intrinsics: capture.trueIntrinsics, dt: 1 / 30,
      });
      total++;
      if (!result.tracked || !result.pose) continue;
      tracked++;
      errors.push((rotationAngleBetween(result.pose.R, frame.pose.R) * 180) / Math.PI);
    }

    assert.ok(tracked / total > 0.9, `tracked only ${((tracked / total) * 100).toFixed(0)}% of frames`);
    assert.ok(distribution(errors).median < 1.5, `median rotation error ${distribution(errors).median.toFixed(2)} deg`);
  });

  it('holds the previous pose through a brief dropout rather than flickering', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    const capture = synthesizeCapture(mesh, subject, CAMERA_LADDER[0], { framesPerBeat: 6 });
    const state = createTracker(model);

    const first = capture.frames[3];
    track(state, {
      landmarks: first.landmarks, sigmaPx: first.sigmaPx,
      intrinsics: capture.trueIntrinsics, dt: 1 / 30,
    });

    for (let i = 0; i < state.options.holdFrames; i++) {
      const held = track(state, {
        landmarks: null, sigmaPx: null, intrinsics: capture.trueIntrinsics, dt: 1 / 30,
      });
      assert.ok(held.tracked, `frame ${i} of the dropout was not held`);
      assert.ok(held.held);
    }
    const past = track(state, {
      landmarks: null, sigmaPx: null, intrinsics: capture.trueIntrinsics, dt: 1 / 30,
    });
    assert.ok(!past.tracked, 'the hold never ends');
  });
});

describe('the silhouette reaches the free-form field, not just the globals', () => {
  // `iterationsGlobal: 0` is the load-bearing option in this block: it freezes
  // stage A entirely, so any dependence of the field on `silhouetteWeight` can
  // only have arrived through stage B. (`solveGlobal` returns Infinity in that
  // configuration; nothing here reads its cost.)
  const regionsB = standardRegions(mesh);
  const subject = generatePopulation(mesh, basis, { count: 1 })[0];
  const geometry = CAMERA_LADDER[0];
  const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 6 });
  const KB = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
  // `BundleFrame` needs a pose and `runBundle` MUTATES it, so every run gets its
  // own copy or the second run starts from where the first finished.
  const framesFor = () => capture.frames.map((f) => ({
    landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
    silhouette: f.silhouette, pose: poseClone(f.pose), beat: f.beat,
  }));
  const fieldOnlyAt = (silhouetteWeight: number) => {
    const field = createDisplacementField(mesh, regionsB.nose);
    const state = createBundleState(mesh, basis, field, regionsB.nose, framesFor(), { ...KB });
    return runBundle(state, { rounds: 1, iterationsGlobal: 0, silhouetteWeight }).fieldRmsGauge;
  };

  it('changes the field it solves when the silhouette weight changes', () => {
    // Before the fix `silhouetteWeight` had literally zero effect on stage B:
    // the alternation minimised two different objectives, and stage B's accepted
    // step RAISED the joint cost on 18 of 108 measured steps. The two arms came
    // back bit-identical — 3.3923 and 3.3923.
    //
    // Measured on this fixture now: 3.3923 mm at w=0 and 2.7333 at w=1, a gap of
    // 0.659 mm, so the 0.2 bar is wide.
    const rms0 = fieldOnlyAt(0);
    const rms1 = fieldOnlyAt(1);
    assert.ok(
      Math.abs(rms0 - rms1) > 0.2,
      `the field came out ${rms0.toFixed(4)} mm at silhouetteWeight 0 and ` +
      `${rms1.toFixed(4)} at 1 — stage B cannot see the silhouette`,
    );
  });

  it('weights the silhouette the same way in the accumulation and in the line search', () => {
    // `accumulateSilhouette` and `costSilhouetteOnly` straddle the LM accept
    // test: if the accumulation weights a residual differently from the cost
    // that judges the resulting step, the solve accepts steps it does not
    // believe in. They used to agree only by the coincidence that a `sigma`
    // variable evaluated to `Math.max(1.0, 1.0)`.
    //
    // Both are module-private, so this pins the property behaviourally: with
    // stage A frozen, the field's residual has to fall monotonically as the
    // weight rises, because more weight on a residual the solver can actually
    // see is more of that residual explained. A weighting mismatch breaks the
    // monotonicity, because the accept test starts rejecting good steps.
    // Measured: 3.3923 / 2.7333 / 2.5014 at w = 0, 1, 2.
    //
    // If a future change makes it reasonable to export the two functions for
    // testing, the far better version of this test is direct: build one
    // BundleState, call each on the same state with the same `opt` and `loss`,
    // and assert the two costs are equal to 1e-9. Take that route if the door
    // opens.
    const rms = [0, 1, 2].map(fieldOnlyAt);
    for (let i = 1; i < rms.length; i++) {
      assert.ok(
        rms[i] <= rms[i - 1],
        `field residual rose from ${rms[i - 1].toFixed(4)} to ${rms[i].toFixed(4)} mm when the ` +
        'accept test is rejecting steps the accumulation believes in — the silhouette weight ' +
        'went up and the two weightings have drifted apart',
      );
    }
  });

  it('fieldPriorScale reaches both sides of stage B\'s accept test', () => {
    // The lesson the silhouette bug taught, applied to the new knob: a weight
    // that scales the normal equations but not the line-search cost (or vice
    // versa) silently re-splits the objective, the accept test starts rejecting
    // steps the accumulation believes in, and the knob goes dead without a
    // single assertion noticing. `fieldPriorScale` multiplies BOTH — see
    // `fieldPriors` in bundle.ts — so turning it from the shipped 8 down to 1
    // must visibly change the field the same capture solves.
    //
    // Measured on this fixture (full default enroll, framesPerBeat 10):
    // fieldRmsGauge 1.2463 at x8 against 1.4446 at x1 — a 0.198 gap against
    // the 0.05 bar. **In the solve's own gauge, not millimetres**: the bundle
    // finishes before `solveScale`, and the scale factor applied afterwards runs
    // 1.099 to 2.732. Comparing two solves of the SAME capture is exactly the
    // use that gauge is valid for. Both solves must also actually factorise: a
    // prior scale that made the system indefinite would "change the field" by
    // killing it.
    const cap = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 10 });
    const solveAt = (fieldPriorScale: number) => enroll({
      mesh, basis,
      frames: cap.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
      bundle: { fieldPriorScale },
    }).bundle;
    const shipped = solveAt(8);
    const base = solveAt(1);
    assert.equal(shipped.fieldFailures, 0, 'the field failed to factorise at the shipped x8');
    assert.equal(base.fieldFailures, 0, 'the field failed to factorise at x1');
    assert.ok(shipped.fieldRmsGauge > 0 && base.fieldRmsGauge > 0, 'a field of zero did not run');
    assert.ok(
      Math.abs(shipped.fieldRmsGauge - base.fieldRmsGauge) > 0.05,
      `fieldRmsGauge ${shipped.fieldRmsGauge.toFixed(4)} at x8 against `
      + `${base.fieldRmsGauge.toFixed(4)} at x1 — the prior scale no longer reaches the `
      + 'solve, so it is scaling only one side of the accept test (or none of it)',
    );
  });

  it('the bundle reports the field in its own gauge and the model reports it in mm', () => {
    // `BundleReport.fieldRmsGauge` was `fieldRmsMm` and it was not millimetres.
    // `runBundle` finishes at step 4 of `enroll`; `applyScale` runs at steps 5
    // and 6, and `enroll` recomputes `displacementStats` afterwards for
    // `FaceModel.displacementRmsMm`. So the two differ by exactly the scale
    // factor — verified to full precision in all 18 cells of a 4-subject x
    // 3-geometry sweep — and the gauge tracks the camera's UNSOLVED focal
    // length rather than the wearer: the same face on two cameras reported
    // 0.6965 and 0.4758 for fields that were really 0.9721 and 0.9994 mm.
    const cap = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 10 });
    const r = enroll({
      mesh, basis,
      frames: cap.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
    });
    // The falsifiability guard for the assertion below, and it is not
    // decoration: at a scale factor near 1 the two gauges are numerically the
    // same and this test could not tell them apart. Measured range 1.099-2.732.
    assert.ok(Math.abs(r.model.scale.factor - 1) > 0.05,
      `the scale factor is ${r.model.scale.factor.toFixed(4)} on this fixture, so this test `
      + 'could not distinguish a gauge figure from a millimetre one');
    assert.ok(
      Math.abs(r.bundle.fieldRmsGauge * r.model.scale.factor - r.model.displacementRmsMm) < 1e-9,
      `the bundle reports ${r.bundle.fieldRmsGauge} and the model ${r.model.displacementRmsMm} `
      + `at a scale factor of ${r.model.scale.factor} — the two field magnitudes no longer `
      + 'differ by exactly the scale factor, so one of them has changed gauge and the '
      + 'docstring is now lying about which',
    );
  });
});

// ---------------------------------------------------------------------- fit

describe('the seat', () => {
  it('slides further down the wedge as the pads get wider — monotonically', () => {
    // v1 derived this relationship and could not test it, because it had no way
    // to vary a frame's pad separation independently of everything else.
    const population = generatePopulation(mesh, basis, { count: 4 });
    const models = population.map((s) => truthModel(s.positions));
    const separations = [12, 15, 18, 21, 24];
    const medians = separations.map((sep) => {
      const frame = parametricFrame({ id: `t${sep}`, padSeparationMm: sep, padAngleRad: 0.67 });
      return distribution(models.map((m) => solveSeat(m, mesh, regions, frame).descentMm)).median;
    });
    for (let i = 1; i < medians.length; i++) {
      assert.ok(
        medians[i] > medians[i - 1] - 0.3,
        `descent went backwards from ${separations[i - 1]} to ${separations[i]} mm: ` +
        `${medians[i - 1].toFixed(2)} -> ${medians[i].toFixed(2)}`,
      );
    }
    const slope = (medians[medians.length - 1] - medians[0]) / (separations[separations.length - 1] - separations[0]);
    assert.ok(slope > 0.3 && slope < 2.0, `wedge slope ${slope.toFixed(3)} mm per mm`);
  });

  it('the pad lift it REPORTS is the vertical force the contact energy exerts', () => {
    // `describeSeat` projected each contact force onto `cp.n` — a
    // barycentrically interpolated VERTEX normal — while `accumulate` builds
    // its contact row from `u = (p - cp)/|p - cp|`, the gradient of the
    // penetration depth. Those sit 9.2 degrees apart at the median over 1,416
    // bearing samples and 48.2 at the worst, so the wearer-facing "% on the
    // nose" described different physics from the one that was solved.
    //
    // Mind the SIGN, because the obvious substitution is wrong: with
    // `E = k*d^2/2` the force is `-k*d*u`, so it is MINUS u. Measured, `+u_y`
    // drives `padLoadFraction` to 0.0% on all 145 catalogue pairs, because
    // `cp.n` and `+u` sit 170.8 degrees apart.
    //
    // The assertion is an exact identity rather than a bar. At a pose where
    // EVERY pad sample penetrates, the approach spring contributes nothing, so
    // the reported pad lift must equal `-dE_contact/dt_y` to numerical
    // precision. `Terms.contact` is the pad term alone — ears are `.ear`,
    // clearance is `.clearance` — so no options gymnastics are needed.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    const frame = TEST_FRAMES[1];

    // Push the frame 2.15 mm back into the nose so all 18 pad samples bear.
    const settled = solveSeat(model, mesh, regions, frame);
    const t = new Float64Array(settled.pose.t);
    t[2] -= 2.15;
    const pose = { R: new Float64Array(settled.pose.R), t };
    const rep = solveSeat(model, mesh, regions, frame, { maxIterations: 0, initialPose: pose });

    // The precondition, so a later change to the frame or the mesh cannot
    // silently turn this identity into an inequality.
    assert.equal(rep.pads[0].bearingFraction, 1, 'not every left pad sample penetrates');
    assert.equal(rep.pads[1].bearingFraction, 1, 'not every right pad sample penetrates');

    const reportedLiftN = rep.padOverClosure * frame.massG * GRAVITY_N_PER_G;

    const contactVerts = (() => {
      const s = new Set<number>();
      for (const v of regions.nose.members) s.add(v);
      for (const v of regions.cheeks.members) s.add(v);
      return Uint32Array.from([...s].sort((a, b) => a - b));
    })();
    const distance = buildMeshDistance(mesh, model.positions, contactVerts);
    const E = (dy: number) => {
      const tt = new Float64Array(pose.t);
      tt[1] += dy;
      return energyTerms(model, frame, { R: pose.R, t: tt }, distance, null,
        { R: pose.R, t: pose.t }, SEAT_DEFAULTS).contact;
    };
    const h = 1e-4;
    const fy = -(E(h) - E(-h)) / (2 * h);

    assert.ok(Math.abs(fy) > 1e-6, 'the contact energy has no vertical gradient here');
    assert.ok(Math.abs(reportedLiftN - fy) / Math.abs(fy) < 1e-6,
      `the reported pad lift (${reportedLiftN.toExponential(8)} N) is not the vertical `
      + `force the contact energy exerts (${fy.toExponential(8)} N), a relative `
      + `difference of ${(Math.abs(reportedLiftN - fy) / Math.abs(fy)).toFixed(4)}. `
      + 'The report is describing a direction the solve never used.');
  });

  it('a seat solved against the wrong nose fits the right one badly — the control', () => {
    // v1's lesson: a number that can only be zero is not a measurement. If this
    // test ever passes trivially, the seat is not reading the scan.
    const population = generatePopulation(mesh, basis, { count: 5 });
    const frame = TEST_FRAMES[1];
    const template = truthModel(mesh.positions);

    const own: number[] = [];
    const wrong: number[] = [];
    const nominal: number[] = [];

    for (const subject of population) {
      const model = truthModel(subject.positions);
      const good = solveSeat(model, mesh, regions, frame);
      own.push(Math.abs(good.padDepthErrorMm));

      const bad = solveSeat(template, mesh, regions, frame);
      wrong.push(Math.abs(
        solveSeat(model, mesh, regions, frame, { maxIterations: 0, initialPose: bad.pose })
          .padDepthErrorMm,
      ));

      // v1's ACTUAL placement — pads hung on the bridge landmark — not the
      // solver's own initialisation, which seats them on the sidewall and had
      // therefore already been given half the answer.
      nominal.push(Math.abs(
        solveSeat(model, mesh, regions, frame, {
          maxIterations: 0,
          initialPose: landmarkHungPose(model, frame),
        }).padDepthErrorMm,
      ));
    }

    const solved = distribution(own).median;
    // 3x, against a measured 4.7x. The bar was 1.8x while this control shared
    // nominalPose with the solver's own initialisation — which meant it started
    // with the pads already on the sidewall, scored 1.55x, and was reporting
    // that the entire contact solve was worth about fifty percent. It is worth
    // more than fourfold; the control was just holding the answer.
    assert.ok(
      distribution(nominal).median > solved * 3,
      `hanging the pads off a landmark is only ${(distribution(nominal).median / solved).toFixed(2)}x ` +
      'worse than solving the contact — the contact solve is not doing anything',
    );
    assert.ok(distribution(wrong).median > solved, 'the template nose fits as well as the real one');
  });

  it('grades a deliberately mismatched frame worse than a matched one', () => {
    // The optician-instruction test that used to sit here went with the prose.
    // What survives is the thing a try-on actually needs: the numeric grading
    // has to be able to tell a bad pad angle from a good one, because that is
    // what the catalogue ranking is built on.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    const steep = TEST_FRAMES.find((f) => f.id === 'steep-pads')!;
    const standard = TEST_FRAMES.find((f) => f.id === 'standard')!;
    const bad = assessFit(model, mesh, regions, steep);
    const good = assessFit(model, mesh, regions, standard);
    assert.ok(
      bad.score < good.score,
      `the mismatched frame scored ${bad.score} against the matched frame's ${good.score}`,
    );
    assert.ok(
      bad.measures.some((m) => m.grade === 'poor' || m.grade === 'fair'),
      'a badly-angled pad produced no criterion worse than good',
    );
  });

  it('a residual the seat refuses cannot grade better than poor', () => {
    // Two bars for one decision. `solveSeat` fires "this frame does not suit
    // this face" at `PAD_CURVATURE_LIMIT_MM = 0.9`; the wearer-facing verdict
    // graded on a bare `1.0`. In the band between them the seat called a frame
    // unwearable and the score called its pads 'good' — measured on the report's
    // own realisation, `crystal-lenses` on S02 at residual 0.9080 and tilt
    // 8.4 deg did exactly that.
    //
    // Neither bar had a test. That is the finding, not a reassurance: no test
    // asserted on the `pads` grade at all and no report imports `score.ts`, so a
    // wearer-facing verdict with two contradicting bars had nothing of its own.
    //
    // The fixture is derived FROM the constant, deliberately: it stays honest if
    // 0.9 is later re-derived, which it should be — `PAD_CURVATURE_LIMIT_MM`'s
    // ledger derivation table reproduces on no seed I tried.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    const frame = TEST_FRAMES[1];
    const real = solveSeat(model, mesh, regions, frame);

    // Mid-band: past the bar the SEAT refuses at, under the bar the SCORE used.
    const mid = (PAD_CURVATURE_LIMIT_MM + 1.0) / 2;
    assert.ok(mid > PAD_CURVATURE_LIMIT_MM && mid < 1.0,
      `the two bars have converged, so this fixture no longer sits between them`);
    const seat = {
      ...real,
      padSeatErrorArticulatedMm: mid,
      // Zero tilt, so the ONLY thing that can make this grade poor is the
      // curvature residual. Without this the test could pass on the tilt.
      padTiltDeg: [0, 0] as [number, number],
    };
    const pads = assessFit(model, mesh, regions, frame, seat)
      .measures.find((m) => m.id === 'pads')!;
    assert.equal(pads.grade, 'poor',
      `the seat says this frame does not suit this face at ${mid.toFixed(3)} mm and the `
      + `verdict says the pads are '${pads.grade}'. Two bars for one decision.`);

    // And the other side, so the bar is not simply "everything is poor".
    const under = { ...seat, padSeatErrorArticulatedMm: PAD_CURVATURE_LIMIT_MM - 0.05 };
    const ok = assessFit(model, mesh, regions, frame, under)
      .measures.find((m) => m.id === 'pads')!;
    assert.equal(ok.grade, 'good',
      `a residual inside the seat's own limit graded '${ok.grade}' with zero tilt`);
  });

  it('ranks the catalogue differently for different faces', () => {
    // If the ranking is the same for everyone it is a style list, not a fit.
    const population = generatePopulation(mesh, basis, { count: 6 });
    const orders = population.map((s) =>
      rankCatalogue(truthModel(s.positions), mesh, regions, TEST_FRAMES)
        .map((r) => r.frame.id).join(','));
    assert.ok(new Set(orders).size > 1, `every face got the same ranking: ${orders[0]}`);
  });

  it('the depth error and the flushness error are different numbers', () => {
    // Conflating them made the control table meaningless: a landmark-hung
    // placement buries the pads millimetres in and is comparatively FLUSH while
    // doing it, so it scored better than the real solve.
    //
    // The bound used to be `padSeatErrorMm < 4 * |padDepthErrorMm|`, which is a
    // check that cannot fail: it passes for total conflation, since |d| < 4|d|
    // is true for every d. It survived only because `maxIterations: 0` forces a
    // deep-wrong placement and the bound scaled with the wrongness.
    //
    // What separates the two forms is the RATIO, and it separates them
    // arithmetically rather than empirically. `padSeatErrorMm` is the RMS of the
    // pad distances about their own MEAN; the reverted form is the RMS about
    // ZERO, which is `sqrt(mean^2 + spread^2)` — never smaller than |mean|, i.e.
    // never a ratio below 1, for any face and any frame. Measured on the shipped
    // form over 8 faces x 5 frames: ratio 0.204 to 1.360, median 0.406.
    //
    // The fixture is `landmarkHungPose` — v1's actual placement — rather than
    // the solver's own initialisation, which is the placement the paragraph
    // above is about.
    const population = generatePopulation(mesh, basis, { count: 6 });
    const ratios: number[] = [];
    const depths: number[] = [];
    for (const subject of population) {
      const model = truthModel(subject.positions);
      for (const frame of TEST_FRAMES) {
        const hung = solveSeat(model, mesh, regions, frame, {
          maxIterations: 0, initialPose: landmarkHungPose(model, frame),
        });
        depths.push(Math.abs(hung.padDepthErrorMm));
        ratios.push(hung.padSeatErrorMm / Math.abs(hung.padDepthErrorMm));
      }
    }
    // The placement really is deep-wrong, or there is nothing to be flush about.
    // Measured minimum over the population: 2.56 mm.
    assert.ok(
      Math.min(...depths) > 2.0,
      `the shallowest landmark-hung placement was only ${Math.min(...depths).toFixed(2)} mm ` +
      'deep-wrong — the fixture no longer poses the question',
    );
    const median = distribution(ratios).median;
    assert.ok(
      median < 0.7,
      `flushness is ${median.toFixed(3)} of depth on the median — at 1.0 or above they ` +
      'are the same measurement, which is what an RMS about zero would give',
    );
  });

  it('reads the same pad residual whatever order the samples arrived in', () => {
    // Finding 06. The old detrend regressed the depth field on the sample INDEX,
    // so it read a different answer for the same pad depending on emission
    // order — a property of the asset's serialisation, not of the fit. The plane
    // fit that replaced it is exactly order-invariant.
    //
    // This fails loudly against any index-based, grid-assuming or "dominant
    // mode" detrend. Measured on the shipped code the worst difference over 14
    // faces is 2.05e-15 mm; under the old index detrend it was 0.1 to 1 mm.
    const base = TEST_FRAMES.find((f) => f.id === 'standard')!;
    const n = base.padSamples.length / 3;
    // A fixed permutation, so the test is deterministic and the failure
    // reproducible.
    const perm = [...Array(n).keys()].sort((a, b) => ((a * 7) % n) - ((b * 7) % n));
    const ps = new Float64Array(n * 3);
    const pn = new Float64Array(n * 3);
    const sd = new Int8Array(n);
    perm.forEach((src, dst) => {
      for (let k = 0; k < 3; k++) {
        ps[dst * 3 + k] = base.padSamples[src * 3 + k];
        pn[dst * 3 + k] = base.padNormals[src * 3 + k];
      }
      sd[dst] = base.padSide[src];
    });
    const shuffled: FrameAsset = { ...base, padSamples: ps, padNormals: pn, padSide: sd };

    let worst = 0;
    for (const subject of generatePopulation(mesh, basis, { count: 12 })) {
      const model = truthModel(subject.positions);
      const a = solveSeat(model, mesh, regions, base);
      const b = solveSeat(model, mesh, regions, shuffled);
      worst = Math.max(worst, Math.abs(a.padSeatErrorArticulatedMm - b.padSeatErrorArticulatedMm));
    }
    assert.ok(
      worst < 1e-9,
      `permuting the pad samples moved the articulated residual by ${worst.toExponential(2)} mm — ` +
      'the detrend is reading the sample order again',
    );
  });

  it('does not call the frame whose defect is tilt the one that cannot be worn', () => {
    // Finding 06's wearer-facing consequence. `padTiltDeg` is a fault an
    // optician fixes with a pair of pliers; `padSeatErrorArticulatedMm` is what
    // is left AFTER that adjustment, and is therefore the number that says "this
    // frame is not for you". They must not leak into each other.
    //
    // The old estimator made `steep-pads` — whose deliberate catalogue defect is
    // a 0.20 rad pad angle against a 0.67 rad sidewall, i.e. purely tilt — the
    // WORST frame on the unfixable metric, at 1.513 mm against the field's ~0.5.
    // That is the exact inversion of the distinction.
    //
    // Measured on the shipped code with 14 subjects:
    //   tilt medians     narrow 20.1  standard 20.3  wide 21.4  heavy 20.9  steep 31.0
    //   residual medians narrow 0.732 standard 0.494 wide 0.398 heavy 0.382 steep 0.472
    const tilt: Record<string, number[]> = {};
    const residual: Record<string, number[]> = {};
    for (const frame of TEST_FRAMES) { tilt[frame.id] = []; residual[frame.id] = []; }
    for (const subject of generatePopulation(mesh, basis, { count: 12 })) {
      const model = truthModel(subject.positions);
      for (const frame of TEST_FRAMES) {
        const seat = solveSeat(model, mesh, regions, frame);
        tilt[frame.id].push(Math.max(...seat.padTiltDeg));
        residual[frame.id].push(seat.padSeatErrorArticulatedMm);
      }
    }
    const medTilt = (id: string) => distribution(tilt[id]).median;
    const medRes = (id: string) => distribution(residual[id]).median;

    // (a) steep-pads is the worst on the FIXABLE metric, which is the whole
    // point of its being in the catalogue.
    for (const frame of TEST_FRAMES) {
      if (frame.id === 'steep-pads') continue;
      assert.ok(
        medTilt('steep-pads') > medTilt(frame.id),
        `steep-pads tilts ${medTilt('steep-pads').toFixed(1)} deg against ${frame.id}'s ` +
        `${medTilt(frame.id).toFixed(1)} — the tilt readout can no longer see a badly angled pad`,
      );
    }
    // (b) and it is NOT the worst on the unfixable one.
    assert.ok(
      medRes('narrow-pads') > medRes('steep-pads'),
      `steep-pads leaves ${medRes('steep-pads').toFixed(3)} mm of unfixable residual against ` +
      `narrow-pads' ${medRes('narrow-pads').toFixed(3)} — a pure tilt defect is being ` +
      'reported as a frame that cannot be worn',
    );
  });

  it('never reaches the clearance term on a real frame, and does reach it on an absurd one', () => {
    // Finding 33. `SKIN.clearanceStiffnessNPerMm`'s docstring asserts that this
    // term never engages for any catalogue frame, and that reaching it needs
    // lenses BEHIND the pad contact. Adding rim geometry, changing
    // `clearanceSamples` or moving the lens plane would make that documented
    // claim silently false; this makes it fail instead. It also stops the term
    // being quietly deleted as unused, which a term nothing can reach invites.
    const faces = generatePopulation(mesh, basis, { count: 12 })
      .map((s) => truthModel(s.positions));
    for (const model of faces) {
      for (const frame of TEST_FRAMES) {
        const seat = solveSeat(model, mesh, regions, frame);
        assert.equal(
          seat.worstClearanceMm, 0,
          `${frame.id} engaged the clearance term at ${seat.worstClearanceMm} mm — the ` +
          'docstring on SKIN.clearanceStiffnessNPerMm says no catalogue frame can',
        );
      }
    }
    // Lenses ten millimetres BEHIND the pads: a frame nobody could make, which
    // is the point — a term nothing can reach is a term nobody can review.
    // Measured: 1.365 mm maximum, engaging on 3 of the 14 faces, so the
    // assertion is on the MAX across faces and not per face.
    const inside = parametricFrame({
      id: 'lenses-inside-the-head', padSeparationMm: 17, padAngleRad: 0.67,
      lensAheadOfPadsMm: -10,
    });
    const worst = Math.max(
      ...faces.map((m) => solveSeat(m, mesh, regions, inside).worstClearanceMm),
    );
    assert.ok(
      worst > 0,
      'even lenses behind the pad contact did not engage the clearance term — it is ' +
      'now unreachable, and a term nobody can reach is a term nobody can review',
    );
  });
});

describe('deriving pad contact from a real mesh, and refusing everything else', () => {
  // **The battery this describe exists for.** The previous derivation returned
  // `ok: true` on all eleven catalogue assets, on all eleven mirrored, on a
  // Z-flipped frame, on a sphere, a plate, a cylinder and the human face mesh
  // — zero refusals across 231 configurations — while its docstring said "It
  // reports when it failed". Its only test fed it a three-vertex triangle,
  // which trips the is-this-a-mesh guard and nothing else.
  //
  // So every refusal below is exercised against a real asset rather than a
  // constructed toy, and the positive case is graded against geometry the
  // asset's own author declared.
  const glbPath = (name: string) => resolve(
    dirname(fileURLToPath(import.meta.url)), '../../assets/glasses', name,
  );
  const load = (name: string) => readGlb(new Uint8Array(readFileSync(glbPath(name))));
  const truth = JSON.parse(readFileSync(glbPath('ground-truth.json'), 'utf8')) as {
    measured: Record<string, {
      padSeparationMm: number; padAngleRad: number; padConeAngleDeg: number;
    }>;
  };

  /**
   * Same geometry, one axis negated — and the winding reversed with it.
   *
   * Negating one axis IS a mirror, so it flips triangle winding and leaves the
   * mesh inside out. Re-winding is what makes the result an upside-down (or
   * back-to-front) FRAME rather than an inverted one, which is the difference
   * between the two refusals below.
   */
  const rewind = (ix: Uint32Array): Uint32Array => {
    const out = Uint32Array.from(ix);
    for (let t = 0; t + 2 < out.length; t += 3) {
      const keep = out[t + 1];
      out[t + 1] = out[t + 2];
      out[t + 2] = keep;
    }
    return out;
  };
  const flip = (p: Float64Array, axis: 0 | 1 | 2): Float64Array => {
    const out = Float64Array.from(p);
    for (let i = axis; i < out.length; i += 3) out[i] = -out[i];
    return out;
  };

  /** A closed convex surface: outward normals everywhere, nothing facing the midline. */
  const sphere = (radius: number, rings = 24): { p: Float64Array; ix: Uint32Array } => {
    const pos: number[] = [];
    for (let i = 0; i <= rings; i++) {
      const phi = (i / rings) * Math.PI;
      for (let j = 0; j <= rings; j++) {
        const th = (j / rings) * 2 * Math.PI;
        pos.push(radius * Math.sin(phi) * Math.cos(th), radius * Math.cos(phi), radius * Math.sin(phi) * Math.sin(th));
      }
    }
    const ix: number[] = [];
    for (let i = 0; i < rings; i++) {
      for (let j = 0; j < rings; j++) {
        const a = i * (rings + 1) + j;
        ix.push(a, a + rings + 1, a + 1, a + 1, a + rings + 1, a + rings + 2);
      }
    }
    return { p: Float64Array.from(pos), ix: Uint32Array.from(ix) };
  };

  it('recovers navigator\'s authored pads to under a millimetre', () => {
    const asset = load('navigator.glb');
    const got = derivePads(asset.positions, asset.indices);
    assert.ok(got.ok, `refused a frame with authored nose pads: ${got.reason}`);
    const want = truth.measured['navigator.glb'];
    const err = Math.abs(got.padSeparationMm - want.padSeparationMm);
    assert.ok(
      err < 1.0,
      `pad separation ${got.padSeparationMm.toFixed(2)} against the asset's own `
      + `${want.padSeparationMm} mm — ${err.toFixed(2)} mm out`,
    );
  });

  it('padAngleRad is the angle parametricFrame inverts, on a real pad too', () => {
    // `padAngleRad` was TWO angles under one name. `parametricFrame` consumes it
    // as a yaw about the vertical — `n = (-side*cos a, 0, -sin a)`, so `ny` is
    // identically zero — and `SKIN`'s `atan(0.60 / 0.76) = 0.67` was derived
    // that way, with the template sidewall's `+0.24` vertical component present
    // in the data and deliberately absent from the number. Both MEASURING sites,
    // added later, read it as a cone angle from the x axis instead.
    //
    // (a) The round trip: the definition must recover what parametricFrame built.
    for (const a of [0.20, 0.42, 0.67, 0.80]) {
      const f = parametricFrame({ id: 'rt', padSeparationMm: 17, padAngleRad: a });
      for (let i = 0; i < f.padNormals.length / 3; i++) {
        const nx = f.padNormals[i * 3], nz = f.padNormals[i * 3 + 2];
        assert.ok(Math.abs(Math.atan2(Math.abs(nz), Math.abs(nx)) - a) < 1e-12,
          `parametric pad ${i} came back at ${Math.atan2(Math.abs(nz), Math.abs(nx))} for ${a}`);
      }
    }

    // (b) **THE DISCRIMINATOR, and (a) is worthless without it.** A parametric
    // pad has `ny == 0`, so a cone angle and a yaw agree on it EXACTLY and the
    // round trip above passes under either definition — which is precisely how
    // the collision survived. navigator's authored pads lean down as well as in,
    // and there the two differ by 3.8 degrees.
    const asset = load('navigator.glb');
    const d = derivePads(asset.positions, asset.indices);
    assert.ok(d.ok, `refused a frame with authored nose pads: ${d.reason}`);
    let maxAbsNy = 0;
    for (let i = 0; i < d.padNormals.length / 3; i++) {
      maxAbsNy = Math.max(maxAbsNy, Math.abs(d.padNormals[i * 3 + 1]));
    }
    assert.ok(maxAbsNy > 0.2,
      `this asset's pad normals peak at |ny| = ${maxAbsNy.toFixed(3)}, so a cone angle and `
      + 'a yaw agree on it and it cannot discriminate the two definitions — pick another '
      + 'asset before trusting the assertion below');

    // **A yaw is invariant to a vertical stretch of the frame and a cone angle
    // is not**, and that is what actually separates them here. Scaling y by s
    // sends a normal to `(nx, ny/s, nz)` renormalised, which leaves `nz/nx`
    // — and therefore the yaw — alone, while changing `ny` and therefore the
    // cone. No ground truth needed, and no dependence on how biased this
    // derivation is, which is the trap: comparing against the authored yaw
    // CANNOT discriminate, because `derivePads` over-reads by 10.4 degrees and
    // its cone reading is only 12.4 degrees out — both inside any tolerance
    // loose enough to pass at all.
    //
    // Measured on navigator over a 4x stretch (y x0.5 against y x2), on the
    // area-weighted aggregate this function actually returns: the yaw moves
    // **1.40 degrees** and the cone moves **4.90**. The residual movement in the
    // yaw is the inward-facing gate admitting slightly different faces as the
    // normals renormalise, not the definition. The 2.5-degree bar sits with a
    // 1.79x margin below it and a 1.96x margin above -- deliberately balanced,
    // because a bar pushed to either end of that gap is a knife edge.
    const stretched = (s: number) => {
      const p = new Float64Array(asset.positions);
      for (let i = 1; i < p.length; i += 3) p[i] *= s;
      const got = derivePads(p, asset.indices);
      assert.ok(got.ok, `derivePads refused navigator stretched by ${s}: ${got.reason}`);
      return got.padAngleRad;
    };
    const spreadDeg = Math.abs(stretched(2) - stretched(0.5)) * 180 / Math.PI;
    assert.ok(spreadDeg < 2.5,
      `padAngleRad moved ${spreadDeg.toFixed(2)} degrees when the frame was stretched `
      + 'vertically. A yaw about the vertical axis cannot do that; a cone angle from '
      + 'the x axis does. This is measuring the wrong quantity.');

    // (c) And it is still in the neighbourhood of the asset's own figure — a
    // loose bar, because this derivation over-reads the yaw by 10.4 degrees and
    // that is a separate open item, not this one.
    const want = truth.measured['navigator.glb'];
    assert.ok(Math.abs(d.padAngleRad - want.padAngleRad) < 0.25,
      `derivePads reads ${d.padAngleRad.toFixed(4)} rad against the asset's own `
      + `${want.padAngleRad}`);

    // (d) ground-truth.json must keep the two apart. If they ever become equal
    // there, the file has stopped distinguishing them and (b)'s subject is gone.
    const cone = (want.padConeAngleDeg * Math.PI) / 180;
    assert.ok(cone - want.padAngleRad > 0.05,
      `the authored yaw and cone angles now agree to ${(cone - want.padAngleRad).toFixed(4)} rad `
      + '— ground-truth.json has stopped distinguishing them');
  });

  it('gives the same answer mirrored — a frame is a frame either way round', () => {
    // NOT a refusal: glasses are near-symmetric, so mirroring is a geometry the
    // derivation must simply be invariant to. It is here because the previous
    // version's `ok: true` on mirrored input was read as evidence it worked.
    const asset = load('navigator.glb');
    // A mirror reverses triangle WINDING, and winding is what decides which
    // way a face normal points. Mirroring the positions alone leaves the mesh
    // inside-out — every normal inverted — which is broken geometry rather
    // than a mirrored frame, and this test asserted the wrong thing until it
    // failed and said so. A modeller's mirror re-winds; so does this one.
    const rewound = rewind(asset.indices);
    const direct = derivePads(asset.positions, asset.indices);
    const mirrored = derivePads(flip(asset.positions, 0), rewound);
    assert.ok(direct.ok && mirrored.ok, 'a mirrored frame is still a frame');
    assert.ok(
      Math.abs(direct.padSeparationMm - mirrored.padSeparationMm) < 0.05,
      `mirroring moved the separation ${direct.padSeparationMm} -> ${mirrored.padSeparationMm}`,
    );
    // And the inside-out version — mirrored positions, original winding — must
    // NOT quietly succeed, because every normal in it points into the solid.
    const insideOut = derivePads(flip(asset.positions, 0), asset.indices);
    assert.ok(!insideOut.ok, `derived pads from an inside-out mesh: ${insideOut.reason}`);
  });

  it('refuses a frame that is upside down', () => {
    // The case the old version was structurally blind to: it never read Y at
    // all, so this returned byte-identical pads.
    const asset = load('navigator.glb');
    const got = derivePads(flip(asset.positions, 1), rewind(asset.indices));
    assert.ok(!got.ok, `derived pads from an upside-down frame: ${got.reason}`);
    assert.match(got.reason, /upside down/);
  });

  it('refuses a frame that is back to front', () => {
    const asset = load('navigator.glb');
    const got = derivePads(flip(asset.positions, 2), rewind(asset.indices));
    assert.ok(!got.ok, `derived pads from a back-to-front frame: ${got.reason}`);
  });

  it('refuses things that are not frames at all', () => {
    const s60 = sphere(30);
    const cases: [string, Float64Array, Uint32Array][] = [
      ['a sphere', s60.p, s60.ix],
      // A flat plate: normals are +/-Z, nothing leans toward the midline.
      ['a flat plate', Float64Array.from([
        -70, -20, 0, 70, -20, 0, 70, 20, 0, -70, -20, 0, 70, 20, 0, -70, 20, 0,
      ]), Uint32Array.from([0, 1, 2, 3, 4, 5])],
      ['the human face', loadTemplateMesh().positions, loadTemplateMesh().indices],
    ];
    for (const [what, p, ix] of cases) {
      const got = derivePads(p, ix);
      assert.ok(!got.ok, `derived nose pads from ${what}: ${got.reason}`);
    }
  });

  it('reports what it refused on, in words that name the defect', () => {
    // A refusal whose reason is generic is a refusal nobody can act on.
    const asset = load('navigator.glb');
    assert.match(derivePads(flip(asset.positions, 1), rewind(asset.indices)).reason, /upside down/);
    assert.match(derivePads(flip(asset.positions, 0), asset.indices).reason, /inside out/);
    assert.match(derivePads(Float64Array.from([0, 0, 0]), Uint32Array.from([0, 0, 0])).reason, /not a mesh/);
  });
});

describe('frame assets', () => {
  it('parametric pads land where they were asked to', () => {
    for (const sep of [13, 17, 22]) {
      const frame = parametricFrame({ id: 's', padSeparationMm: sep, padAngleRad: 0.6 });
      let right = 0, rn = 0, left = 0, ln = 0;
      for (let i = 0; i < frame.padSamples.length / 3; i++) {
        if (frame.padSide[i] < 0) { right += frame.padSamples[i * 3]; rn++; }
        else { left += frame.padSamples[i * 3]; ln++; }
      }
      const measured = Math.abs(left / ln - right / rn);
      assert.ok(Math.abs(measured - sep) < 0.5, `asked for ${sep} mm, got ${measured.toFixed(2)}`);
    }
  });

  it('centres a parametric pad cloud on the origin — which is why a rigid setback did nothing', () => {
    // Finding 34. `parametricFrame` re-centres on the pad centroid, and that is
    // the origin convention the whole contact solve assumes. It is also the
    // reason `padSetbackMm` was inert: a field that offsets every pad sample
    // rigidly is removed again by the re-centring before anything reads it. Any
    // future field of that shape would be equally dead, so this pins the
    // invariant that makes it true rather than the one dead field.
    //
    // Measured on the shipped code: (-2.2e-15, -1.1e-16, 0) mm for 'standard',
    // and 2.22e-15 mm is the worst component over every cloud below.
    //
    // What this can and cannot catch, checked by injection rather than assumed.
    // Today's generation is already symmetric, so deleting the re-centring on
    // its own changes nothing and this stays green — the re-centring is a no-op
    // until there is an offset to remove. What it DOES catch is the pair: adding
    // a rigid fore-aft offset back into the sample loop (a `padSetbackMm`-shaped
    // field) while the re-centring is gone. Verified both ways: offset with the
    // re-centring intact passes, offset without it fails.
    const clouds: FrameAsset[] = [
      ...TEST_FRAMES,
      parametricFrame({
        id: 'small', padSeparationMm: 13, padHeightMm: 9, padWidthMm: 5,
        padAngleRad: 0.5, samplesPerPad: 9,
      }),
      parametricFrame({
        id: 'large', padSeparationMm: 21, padHeightMm: 14, padWidthMm: 8,
        padAngleRad: 0.8, samplesPerPad: 25,
      }),
    ];
    for (const frame of clouds) {
      const count = frame.padSamples.length / 3;
      const mean = [0, 0, 0];
      for (let i = 0; i < count; i++) {
        for (let k = 0; k < 3; k++) mean[k] += frame.padSamples[i * 3 + k] / count;
      }
      for (let k = 0; k < 3; k++) {
        assert.ok(
          Math.abs(mean[k]) < 1e-9,
          `${frame.id} pad cloud centroid axis ${k} is ${mean[k].toExponential(2)} mm — the ` +
          'contact solve assumes the pads are centred on the frame origin',
        );
      }
    }
  });

  it('pad derivation refuses a mesh with no pads rather than inventing them', () => {
    // The failure mode that let nine of eleven v1 assets declare dimensions they
    // did not have.
    const flat = Float64Array.from([
      -50, 0, 0, 50, 0, 0, 0, 50, 0,
    ]);
    const indices = Uint32Array.from([0, 1, 2]);
    const result = derivePads(flat, indices);
    assert.equal(result.ok, false);
    assert.ok(result.reason.length > 10);
  });

  it('templeReachMm defaults to the old inline literal, bit for bit', () => {
    // Q16 gave the highest-leverage number in the tree a spec field. The field
    // must be a pure rename of the 95 that was hardcoded: an explicit 95 and an
    // omitted field have to build byte-identical geometry, or every historical
    // measurement in this repository silently stops describing the catalogue.
    // Measured: max |difference| = 0 across earRests and padSamples.
    const spec = { id: 'reach-identity', padSeparationMm: 17, padAngleRad: 0.67, massG: 24 };
    const explicit = parametricFrame({ ...spec, templeReachMm: 95 });
    const defaulted = parametricFrame(spec);
    assert.deepEqual(Array.from(explicit.padSamples), Array.from(defaulted.padSamples));
    for (let s = 0; s < 2; s++) {
      assert.deepEqual(Array.from(explicit.earRests[s]), Array.from(defaulted.earRests[s]));
      assert.deepEqual(Array.from(explicit.hinges[s]), Array.from(defaulted.hinges[s]));
    }
  });

  it('templeReachMm moves the seat the way the Q16 sweep measured', () => {
    // The leverage that motivated the field: ±5 mm of reach walks the corneal
    // vertex across the entire 12-16 mm prescription band. Re-measured on this
    // fixture (10 subjects at populationSeedFor(11) x the 5 catalogue specs,
    // shipped wall hook, seated against ground truth; medians over the 50
    // pairs per arm):
    //
    //     reach    corneal vertex    descent      hook force / weight
    //      90          8.69           -0.74             1.94
    //      95         13.03            3.04             1.14
    //     100         16.69            9.05             0.87
    //
    // Only the ORDERINGS are asserted — the medians are one seed's numbers.
    // Longer reach: the ear rest sits further back, the frame hangs further
    // forward and lower (vertex and descent up), and the hook has less forward
    // push left to resist (force down).
    const SPECS: FrameSpec[] = [
      { id: 'narrow-pads', padSeparationMm: 13, padAngleRad: 0.67, massG: 20 },
      { id: 'standard', padSeparationMm: 17, padAngleRad: 0.67, massG: 24 },
      { id: 'wide-pads', padSeparationMm: 22, padAngleRad: 0.67, massG: 28 },
      { id: 'heavy-acetate', padSeparationMm: 19, padAngleRad: 0.67, massG: 42, bridgeType: 'saddle' },
      { id: 'steep-pads', padSeparationMm: 17, padAngleRad: 0.20, massG: 24 },
    ];
    const models = generatePopulation(mesh, basis, { count: 8, seed: populationSeedFor(11) })
      .map((subject) => truthModel(subject.positions));
    const arm = (reach: number) => {
      const vertex: number[] = [];
      const descent: number[] = [];
      const hookOverWeight: number[] = [];
      for (const model of models) {
        for (const spec of SPECS) {
          const frame = parametricFrame({ ...spec, templeReachMm: reach });
          const s = solveSeat(model, mesh, regions, frame);
          // The corneal trap: SeatResult.vertexDistanceMm is to the eye-corner
          // plane; subtract the 12 mm corneal offset before reading it against
          // the 12-16 band.
          vertex.push((s.vertexDistanceMm[0] + s.vertexDistanceMm[1]) / 2 - 12);
          descent.push(s.descentMm);
          hookOverWeight.push(s.hookForceN / (frame.massG * GRAVITY_N_PER_G));
        }
      }
      return {
        vertex: distribution(vertex).median,
        descent: distribution(descent).median,
        hookOverWeight: distribution(hookOverWeight).median,
      };
    };
    const at90 = arm(90), at95 = arm(95), at100 = arm(100);
    assert.ok(
      at90.vertex < at95.vertex && at95.vertex < at100.vertex,
      `corneal vertex medians ${at90.vertex.toFixed(2)} / ${at95.vertex.toFixed(2)} / ` +
      `${at100.vertex.toFixed(2)} mm at reach 90/95/100 — reach no longer moves the fore-aft seat`,
    );
    assert.ok(
      at90.descent < at95.descent && at95.descent < at100.descent,
      `descent medians ${at90.descent.toFixed(2)} / ${at95.descent.toFixed(2)} / ` +
      `${at100.descent.toFixed(2)} mm — a longer reach must let the frame ride lower`,
    );
    assert.ok(
      at90.hookOverWeight > at95.hookOverWeight && at95.hookOverWeight > at100.hookOverWeight,
      `hook/weight medians ${at90.hookOverWeight.toFixed(2)} / ${at95.hookOverWeight.toFixed(2)} / ` +
      `${at100.hookOverWeight.toFixed(2)} — a longer reach must unload the hook`,
    );
  });

  it('templeReachMm refuses garbage where the mistake is', () => {
    // A non-positive reach puts the ear rests level with or ahead of the pads:
    // the one-sided ear and hook terms silently never engage and the seat
    // reports a plausible answer for a frame that cannot exist.
    const spec = { id: 'reach-guard', padSeparationMm: 17, padAngleRad: 0.67 };
    for (const bad of [NaN, 0, -5]) {
      assert.throws(
        () => parametricFrame({ ...spec, templeReachMm: bad }),
        /templeReachMm/,
        `templeReachMm ${bad} was accepted`,
      );
    }
  });
});

describe('the quality numbers have to measure quality', () => {
  /** N frames from the SAME camera position — no head movement whatsoever. */
  function stillFrames(count: number, pitchDeg: number) {
    const positions = new Float64Array(mesh.positions);
    const frames = [];
    for (let f = 0; f < count; f++) {
      const pose = poseIdentity();
      poseRotationFromHeadEuler(pose.R, 0, (pitchDeg * Math.PI) / 180, 0);
      pose.t.set([0, 0, 520]);
      const landmarks = new Float64Array(mesh.vertexCount * 2);
      const K = { f: 1061, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };
      const cam = new Float64Array(3);
      const uv = new Float64Array(2);
      for (let i = 0; i < mesh.vertexCount; i++) {
        const R = pose.R;
        const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
        cam[0] = R[0] * x + R[1] * y + R[2] * z + pose.t[0];
        cam[1] = R[3] * x + R[4] * y + R[5] * z + pose.t[1];
        cam[2] = R[6] * x + R[7] * y + R[8] * z + pose.t[2];
        project(uv, K, cam);
        landmarks[i * 2] = uv[0];
        landmarks[i * 2 + 1] = uv[1];
      }
      frames.push({
        pose,
        landmarks,
        sigmaPx: new Float64Array(mesh.vertexCount).fill(0.7),
        visibility: new Float64Array(mesh.vertexCount).fill(1),
        silhouette: null,
        beat: 'centre',
      });
    }
    return { frames, positions, K: { f: 1061, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 } };
  }

  it('a head that never moved has no parallax, however low the camera sits', () => {
    // The whole bug in one assertion. `parallax` used to be the angle between
    // each view ray and the model's +Z axis — a property of ONE view — so a
    // still photograph taken from below eye level reported 15.9 degrees against
    // a 12 degree threshold, and the term was pinned at 1.0 on every laptop and
    // phone. The wearer-facing consequence: "not enough head turn during the
    // scan" was unreachable code.
    for (const pitchDeg of [0, -10, -20, -30]) {
      const { frames, positions, K } = stillFrames(12, pitchDeg);
      const { parallax } = perVertexUncertainty(positions, frames, K, mesh);
      const noseParallaxDeg = (parallax[LM.NOSE_BRIDGE] * 180) / Math.PI;
      assert.ok(
        noseParallaxDeg < 1.0,
        `camera pitched ${pitchDeg} deg with a motionless head reports ` +
        `${noseParallaxDeg.toFixed(1)} deg of parallax — it is measuring the camera, not the turn`,
      );
    }
  });

  it('confidence falls when the reconstruction is actually worse', () => {
    // Measured across the camera ladder: true nose error runs 1.03 mm at eye
    // level to 1.79 mm with a phone in the lap, and confidence has to follow it
    // down. It did not before — the third term was the formal covariance, whose
    // correlation with the true error is -0.09, and whose value is SMALLEST for
    // the worst geometry because an extreme viewing angle reads as an abundance
    // of information.
    const results = CAMERA_LADDER.map((geometry) => {
      const subject = generatePopulation(mesh, basis, { count: 1 })[0];
      const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
      const model = enroll({
        mesh, basis,
        frames: capture.frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
        imageWidth: geometry.width, imageHeight: geometry.height,
        irisMm: subject.irisDiameterMm,
      }).model;
      return {
        name: geometry.name,
        confidence: noseConfidence(model).value,
        varianceFactor: model.varianceFactor,
        error: compareToTruth(model, subject, regions, mesh).perRegion.nose.rmsMm,
      };
    });

    const best = results.reduce((a, b) => (a.error < b.error ? a : b));
    const worst = results.reduce((a, b) => (a.error > b.error ? a : b));
    assert.ok(
      worst.confidence < best.confidence,
      `${worst.name} reconstructs at ${worst.error.toFixed(2)} mm with confidence ` +
      `${worst.confidence.toFixed(2)}, while ${best.name} reconstructs at ` +
      `${best.error.toFixed(2)} mm with ${best.confidence.toFixed(2)} — ` +
      'confidence is not tracking the reconstruction',
    );

    // And the agreement term's raw input has to read the geometry the recentred
    // TYPICAL_VARIANCE_FACTOR was calibrated on: phone-lap's detector-sigma
    // claim is the optimistic one (campaign per-geometry medians 5.10 against
    // eye-level's 1.63 over 210 pooled runs; this draw measures 5.85 against
    // 1.74). Only the ORDERING is asserted — the phone-lap band runs 1.44 to
    // 8.33 across draws, so a per-draw floor above ~1.4 would be a coin toss.
    const byName = Object.fromEntries(results.map((r) => [r.name, r]));
    assert.ok(
      byName['phone-lap'].varianceFactor > byName['eye-level'].varianceFactor,
      `variance factor phone-lap ${byName['phone-lap'].varianceFactor.toFixed(2)} against ` +
      `eye-level ${byName['eye-level'].varianceFactor.toFixed(2)} — the agreement term has ` +
      'stopped seeing the geometry whose sigma claim is actually optimistic',
    );
  });

  it('a scan whose landmarks were noisier than claimed says so', () => {
    // The variance factor is the one scan-quality number that measures a claim
    // against evidence. It has to come back above 1 when the detector was
    // optimistic, which — measured on this harness — it always is.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const capture = synthesizeCapture(mesh, subject, CAMERA_LADDER[0], { framesPerBeat: 12 });
    const result = enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: CAMERA_LADDER[0].width, imageHeight: CAMERA_LADDER[0].height,
      irisMm: subject.irisDiameterMm,
    });
    assert.ok(
      result.bundle.varianceFactor > 1.05,
      `variance factor ${result.bundle.varianceFactor.toFixed(2)} — either the detector ` +
      'model became exactly right, or the residuals stopped being whitened',
    );
    assert.equal(result.model.varianceFactor, result.bundle.varianceFactor);
  });
});

describe('the configuration that actually ships', () => {
  it('is measured, and it is worse than the one the other tests use', () => {
    // Every other enrollment test passes `irisMm: subject.irisDiameterMm` — the
    // wearer's TRUE iris. `src/app/main.ts` never sets `irisMm` at all, so every
    // real scan falls through to the pooled `IRIS.defaultMm` of 11.7 mm. The
    // path that ships had no test, and the bar guarding it was only ever applied
    // to a configuration nobody runs.
    const geometry = CAMERA_LADDER[0];
    const run = (useTrueIris: boolean) => {
      const nose: number[] = [];
      const scaleErr: number[] = [];
      for (const subject of generatePopulation(mesh, basis, { count: 8 })) {
        const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
        const model = enroll({
          mesh, basis,
          frames: capture.frames.map((f) => ({
            landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
            silhouette: f.silhouette, beat: f.beat,
          })),
          imageWidth: geometry.width, imageHeight: geometry.height,
          ...(useTrueIris ? { irisMm: subject.irisDiameterMm } : {}),
        }).model;
        nose.push(compareToTruth(model, subject, regions, mesh).perRegion.nose.rmsMm);
        scaleErr.push(Math.abs(
          model.measurements.outerEyeSpan / measure(subject.positions).outerEyeSpan - 1,
        ) * 100);
      }
      return { nose: distribution(nose), scale: distribution(scaleErr) };
    };

    const shipping = run(false);
    const oracle = run(true);

    // Measured: true iris gives nose median 1.04 / worst 1.52 and scale error
    // median 0.39% / worst 1.31%. Shipping gives nose median 1.47 / worst 3.38
    // and scale error median 2.71% / worst 10.08%. The bars are set from those,
    // with headroom, and they are DIFFERENT bars on purpose — pretending one
    // number covers both configurations is how this went unnoticed.
    assert.ok(
      shipping.nose.median < 2.0,
      `shipping nose error median ${shipping.nose.median.toFixed(2)} mm`,
    );
    assert.ok(
      shipping.nose.worst < 4.0,
      `shipping nose error worst ${shipping.nose.worst.toFixed(2)} mm`,
    );
    assert.ok(
      oracle.nose.worst < shipping.nose.worst,
      'the true iris is no better than the assumed one — the ruler is not being used',
    );
  });

  it('reports an uncertainty that actually covers the error it makes', () => {
    // The pooled iris is a guess, and the honest part is `IRIS.sigmaMm` = 0.55
    // on 11.7 mm — 4.7% at one sigma, covering both the within-group SD and the
    // spread between population means. If the scale error the assumption causes
    // routinely exceeded what the model reports as its own uncertainty, the
    // wearer would be told millimetres nobody should act on.
    const geometry = CAMERA_LADDER[0];
    let covered = 0;
    let n = 0;
    for (const subject of generatePopulation(mesh, basis, { count: 10 })) {
      const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
      const model = enroll({
        mesh, basis,
        frames: capture.frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
        imageWidth: geometry.width, imageHeight: geometry.height,
      }).model;
      const err = Math.abs(
        model.measurements.outerEyeSpan / measure(subject.positions).outerEyeSpan - 1,
      );
      // scale.sigma is a relative one-sigma on the gauge.
      if (err <= 2.5 * model.scale.sigma) covered++;
      n++;
    }
    assert.ok(
      covered >= Math.ceil(n * 0.8),
      `only ${covered}/${n} scans had their scale error inside 2.5 sigma of the ` +
      'uncertainty they reported — the iris assumption is understating itself',
    );
  });
});

describe('the wearer own PD as a ruler', () => {
  const geometry = CAMERA_LADDER[0];
  const truePd = (p: Float64Array) => {
    const mid = (a: number, b: number, c: number) => (p[a * 3 + c] + p[b * 3 + c]) / 2;
    return Math.hypot(
      mid(133, 33, 0) - mid(362, 263, 0),
      mid(133, 33, 1) - mid(362, 263, 1),
      mid(133, 33, 2) - mid(362, 263, 2),
    );
  };
  const run = (subject: ReturnType<typeof generatePopulation>[number], knownPdMm: number | null) => {
    const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
    return enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: geometry.width, imageHeight: geometry.height,
      knownPdMm,
    }).model;
  };
  const scaleErrPct = (model: FaceModel, subject: ReturnType<typeof generatePopulation>[number]) =>
    Math.abs(model.measurements.outerEyeSpan / measure(subject.positions).outerEyeSpan - 1) * 100;

  it('promises one range, and it is the one the app tells the wearer', () => {
    // **The fixture cannot come from the constant.** The first version of the
    // test below swept `PD_PLAUSIBLE_MM[0]` and `[1]`, which moves with the
    // constant — so narrowing it back to [46, 80], or opening it to [1, 500],
    // both passed. A range is only checkable against something outside itself,
    // and the something is the promise the app makes in its own words:
    // `set-pd` refuses with "outside the human range (45 to 85)", and
    // `readStoredPd` drops a stored value on the same rule. Both now read this
    // constant, so this assertion is what stops all three drifting together.
    assert.deepEqual([PD_PLAUSIBLE_MM[0], PD_PLAUSIBLE_MM[1]], [45, 85],
      'the PD range moved. It is quoted verbatim to the wearer by `set-pd` and '
      + 'used by `readStoredPd`, `enroll`s correction and `enroll`s readout — '
      + 'move it here and every one of those moves, or none of them should.');

    // And no site may go back to spelling it out. This is the defect itself:
    // the correction gated on bare literals while the readout gated on the
    // constant, so a wearer who typed 45 had the scan resized by their number
    // and was then told the eye landmarks were wrong. Compiled text, because
    // that is what runs.
    const enrollJs = readFileSync(new URL('../src/enroll/enroll.js', import.meta.url), 'utf8');
    assert.doesNotMatch(enrollJs, /knownPdMm\s*>=\s*4[0-9]/,
      'the PD correction is gating on a literal again, beside a readout that '
      + 'gates on PD_PLAUSIBLE_MM — that is two ranges for one decision');
    const mainJs = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    assert.doesNotMatch(mainJs, /raw\s*>=\s*4[0-9]\s*&&\s*raw\s*<=\s*[0-9]/,
      'the app is gating a typed PD on a literal again');
  });

  it('a PD it accepted as a ruler is a PD it will report', () => {
    // These were TWO ranges. The correction gated on a bare [45, 85] (matching
    // the app's `set-pd` handler and its stored-value reader) and the readout
    // on `PD_PLAUSIBLE_MM` = [46, 80]. Since the correction rescales the whole
    // geometry so `interpupillarySpan` EQUALS the wearer's figure by
    // construction, a PD of 45.0 or 82.0 was taken as the absolute ruler, every
    // millimetre downstream resized by it, and then refused as a readout with
    // "something in the eye landmarks or the scale is wrong" — about a number
    // the wearer had typed.
    //
    // The ENDPOINTS are the fixture, and they are also the second half of this
    // finding: the correction's round trip lands the recomputed span up to
    // 1.4e-14 mm from the typed figure, so at an inclusive boundary the verdict
    // flipped per FACE. Measured over four subjects at seed 11, an exactly
    // typed 45.0 gave 44.999999999999993 (refused), 45.000000000000000
    // (reported) and 45.000000000000007 (reported). Several subjects, so this
    // cannot pass by drawing a lucky one.
    const population = generatePopulation(mesh, basis, { count: 2 });
    for (const subject of population) {
      for (const pd of [PD_PLAUSIBLE_MM[0], 61.8, PD_PLAUSIBLE_MM[1]]) {
        const model = run(subject, pd);
        assert.equal(model.scale.source, 'pd', `${pd} mm was not taken as the ruler`);
        assert.ok(model.pdMm !== null && Math.abs(model.pdMm - pd) < 1e-6,
          `the scan was resized by ${pd} mm and then reported `
          + `${model.pdMm === null ? 'nothing' : `${model.pdMm} mm`}`);
        assert.ok(!model.notes.some((n) => /eye landmarks or the scale is wrong/.test(n)),
          `a scan resized by a typed ${pd} mm blamed its own eye landmarks`);
      }
      // And the refusal still refuses, just outside. Not a range that accepts
      // everything: that would pass the assertions above just as well.
      for (const pd of [PD_PLAUSIBLE_MM[0] - 0.1, PD_PLAUSIBLE_MM[1] + 0.1]) {
        const model = run(subject, pd);
        assert.notEqual(model.scale.source, 'pd',
          `${pd} mm is outside the human range and was used as the ruler anyway`);
        assert.ok(model.notes.some((n) => /was not used/.test(n)),
          `${pd} mm was ignored without saying so`);
      }
    }
  });

  /** The same enrollment at a second camera geometry, for the PD readout. */
  const enrollAt = (
    subject: ReturnType<typeof generatePopulation>[number],
    extra: Record<string, unknown>,
  ) => {
    const g = CAMERA_LADDER[1];
    const capture = synthesizeCapture(mesh, subject, g, { framesPerBeat: 12 });
    return enroll({
      mesh, basis,
      frames: capture.frames.map((f) => ({
        landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
        silhouette: f.silhouette, beat: f.beat,
      })),
      imageWidth: g.width, imageHeight: g.height,
      ...extra,
    }).model;
  };


  it('beats the pooled iris by roughly tenfold', () => {
    // The iris is assumed — 11.70 mm pooled with a 4.7% sigma — and produces a
    // median scale error of about 4.4%. A PD from a prescription was measured on
    // this wearer with a pupilometer.
    const population = generatePopulation(mesh, basis, { count: 6 });
    const iris = population.map((s) => scaleErrPct(run(s, null), s));
    const pd = population.map((s) => scaleErrPct(run(s, truePd(s.positions)), s));
    assert.ok(
      distribution(pd).median < distribution(iris).median / 3,
      `PD gives ${distribution(pd).median.toFixed(2)}% against the iris's ` +
      `${distribution(iris).median.toFixed(2)}% — the ruler is not being used`,
    );
  });

  it('is applied against the solved surface, not the image', () => {
    // The trap this walked into once: `readIris` returns `pdPx`, an image-space
    // pupil separation, and using it foreshortens with yaw — the exact property
    // the iris was chosen to avoid. That version measured WORSE than the
    // assumption it replaced (6.35% against 4.39%) while reporting a confident
    // 0.93% sigma. If this test regresses, check where the span is taken from.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = run(subject, truePd(subject.positions));
    assert.equal(model.scale.source, 'pd');
    assert.ok(
      scaleErrPct(model, subject) < 2.0,
      `scale error ${scaleErrPct(model, subject).toFixed(2)}% with an exact PD`,
    );
  });

  it('reports an uncertainty it can stand behind', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = run(subject, truePd(subject.positions));
    // Sub-1%, where the iris path reports 4.7% — and it has to be honest, not
    // merely small.
    assert.ok(model.scale.sigma < 0.015, `sigma ${(model.scale.sigma * 100).toFixed(2)}%`);
    assert.ok(
      scaleErrPct(model, subject) < 2.5 * model.scale.sigma * 100,
      `${scaleErrPct(model, subject).toFixed(2)}% error against a ` +
      `${(model.scale.sigma * 100).toFixed(2)}% claimed sigma`,
    );
  });

  it('refuses a figure outside the human range, and says it refused', () => {
    // Refusing is half of it. `enroll` is a library entry point as well as the
    // app's — a replayed capture or a harness can hand it a PD that the app's
    // own `set-pd` handler would have rejected before storing — and this branch
    // used to fall through with no note at all, so the scan came back on the
    // iris while the caller believed it had set the ruler. `scale.ts`'s "never
    // silently substitutes" covers a ruler that was OFFERED as much as one that
    // was missing.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    for (const typo of [6.3, 630, 12]) {
      const model = run(subject, typo);
      assert.equal(
        model.scale.source, 'iris',
        `a PD of ${typo} mm was accepted — a wearer typing centimetres would ` +
        'silently get a face an order of magnitude wrong',
      );
      assert.ok(
        model.notes.some((n) => n.includes('was not used')),
        `a PD of ${typo} mm was dropped in silence: ${model.notes.join(' | ')}`,
      );
    }
  });

  it('measures the pupillary distance off the surface, not off the image', () => {
    // `readIris` also returns `pdPx`, an image-space pupil separation, and
    // taking the PD from it collapses to `irisMm * pdPx / (2 * radiusPx)` — a
    // ratio of two image measurements that FORESHORTENS with yaw, which is the
    // exact property the iris was chosen to avoid. The model's PD now comes from
    // `interpupillarySpan` on the scaled 3-D geometry.
    //
    // Measured at CAMERA_LADDER[1] over 8 subjects: with an exact iris ruler the
    // mean error is +0.20 mm. The old image-space path read LOW by a mean of
    // 3.93 mm, so a NEGATIVE mean here is the regression — the sign is the
    // assertion, and the size is the sanity check on it.
    const population = generatePopulation(mesh, basis, { count: 6 });
    const exactErrors: number[] = [];
    for (const subject of population) {
      const t = truePd(subject.positions);
      // An exact ruler, so this measures where the span is TAKEN FROM rather
      // than the population bias of a pooled iris constant.
      const exact = enrollAt(subject, { irisMm: subject.irisDiameterMm, irisSigmaMm: 0.05 });
      assert.ok(
        exact.pdMm !== null && Number.isFinite(exact.pdMm),
        `${subject.id}: no PD came back at all`,
      );
      exactErrors.push(exact.pdMm! - t);

      // And on the shipping path — the pooled 11.7 mm iris — the printed sigma
      // has to cover the error it makes. This is the property the old version
      // lacked: it printed about 2.7 mm against a 3.9 mm bias, which is an
      // interval that excludes the truth.
      const pooled = enrollAt(subject, {});
      assert.ok(
        Math.abs(pooled.pdMm! - t) < 2 * pooled.pdSigmaMm!,
        `${subject.id}: PD off by ${(pooled.pdMm! - t).toFixed(2)} mm against a claimed ` +
        `${pooled.pdSigmaMm!.toFixed(2)} mm sigma`,
      );
    }
    const mean = exactErrors.reduce((a, b) => a + b, 0) / exactErrors.length;
    assert.ok(
      mean > 0,
      `the exact-ruler PD reads ${mean.toFixed(2)} mm LOW on the mean — a negative bias is ` +
      'the fingerprint of the image-space path, which foreshortens with yaw',
    );
    assert.ok(mean < 1.5, `exact-ruler PD bias ${mean.toFixed(2)} mm`);
  });

  it('gives a wearer their own PD back unchanged', () => {
    // The readout is computed ONCE, after every `applyScale`, so the known-PD
    // path reproduces the wearer's own figure rather than reporting a separately
    // derived number beside it. The correction sets the span equal to the
    // supplied PD, so this is exact by construction and the bar can be 1e-6.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const t = truePd(subject.positions);
    const model = enrollAt(subject, { knownPdMm: t });

    assert.equal(model.scale.source, 'pd');
    assert.ok(
      Math.abs(model.pdMm! - t) < 1e-6,
      `asked for ${t.toFixed(4)} mm and the model reports ${model.pdMm!.toFixed(4)}`,
    );
    // `pdSigmaMm = pdMm * (sigmaMm / knownPdMm)` collapses to the optician's own
    // sigma, and reporting anything else would be claiming to know their
    // prescription better than the pupilometer that produced it.
    assert.ok(
      Math.abs(model.pdSigmaMm! - PD_RULER.opticianSigmaMm) < 1e-6,
      `pdSigmaMm ${model.pdSigmaMm} against PD_RULER.opticianSigmaMm ` +
      `${PD_RULER.opticianSigmaMm}`,
    );
    assert.ok(
      model.notes.some((n) => n.includes('scale set from your PD')),
      `the wearer's own ruler went unmentioned: ${model.notes.join(' | ')}`,
    );
    // That note is gated on `scale.estimate.source === 'card'` now, so it cannot
    // appear on a path where no card was ever read.
    assert.ok(
      model.notes.every((n) => !n.includes('card and iris disagree')),
      `a card disagreement was reported on a scan with no card: ${model.notes.join(' | ')}`,
    );
  });

  // ---- the second ruler ---------------------------------------------------
  //
  // `ScaleEstimate.sigma` is a POPULATION precision: on the iris rung it is
  // 0.55/11.70 to within a fortieth of a point, the same number for every
  // wearer, so nothing downstream can tell a wearer the 11.70 mm assumption
  // fits from one whose true HVID is 11.10 and who therefore carries 5.4% at
  // identical printed confidence. Two rulers disagreeing is the only signal in
  // the tree that sees the individual, and the gap is free — `enroll` already
  // computes it to move the geometry.
  //
  // These four tests construct the gap EXACTLY rather than hoping a population
  // produces one. `model.pdMm` on an iris-only run is the very span the PD
  // correction divides into, and `enroll` is deterministic on identical frames,
  // so dividing that span by 1.05 and handing it back as the wearer's PD makes
  // the disagreement 5.000% by construction.

  // One subject, and the iris rung's reading of it, solved once. `enroll` is
  // deterministic on identical frames and `generatePopulation` is seeded, so
  // every test below divides into the same span and gets an exact gap.
  let cached: { subject: ReturnType<typeof generatePopulation>[number]; span: number } | null = null;
  const irisRun = () => {
    if (!cached) {
      const subject = generatePopulation(mesh, basis, { count: 1 })[0];
      const model = run(subject, null);
      assert.equal(model.scale.source, 'iris', 'the iris rung did not resolve');
      assert.ok(model.pdMm !== null, 'the iris run reported no PD to disagree with');
      assert.ok(
        model.scale.disagreementPct === null || model.scale.disagreementPct === undefined,
        `an iris-only scan claims a disagreement of ${model.scale.disagreementPct} — ` +
        'absent is the honest state, and a zero would read as "two rulers checked ' +
        'and agreed", which is the one thing an iris-only scan has NOT established',
      );
      cached = { subject, span: model.pdMm! };
    }
    return cached;
  };

  it('records how far the two rulers disagree, signed the way the bias runs', () => {
    const { subject, span } = irisRun();

    // The wearer's real pupils are 5% CLOSER than the iris made them, so the
    // iris read them LARGE — and the documented bias is exactly that direction
    // (+2.59% signed, 67% of wearers read large, because 11.70 sits ~2.2% above
    // the population mean). Positive therefore has to mean "read large", or the
    // sign carries the opposite of what every doc about it says.
    const large = run(subject, span / 1.05);
    assert.equal(large.scale.source, 'pd');
    assert.ok(
      large.scale.disagreementPct !== null && large.scale.disagreementPct !== undefined,
      'two rulers resolved and the disagreement came back absent',
    );
    assert.ok(
      Math.abs(large.scale.disagreementPct! - 5) < 0.01,
      `constructed a 5.000% gap and the model reports ` +
      `${large.scale.disagreementPct!.toFixed(4)}%`,
    );

    // And the other way. Without this the sign is untested: a magnitude-only
    // implementation, or one with the reciprocal inverted, passes the line above.
    const small = run(subject, span * 1.05);
    assert.ok(
      small.scale.disagreementPct! < 0,
      `the iris read the wearer SMALL and the disagreement came back ` +
      `${small.scale.disagreementPct!.toFixed(3)}% — the sign is inverted`,
    );
    assert.ok(Math.abs(small.scale.disagreementPct! + 4.7619) < 0.01);
  });

  it('says a large gap out loud, and stays quiet when the rulers agree', () => {
    // Both directions, because a note that always fires and a note that never
    // fires each pass one half of this on their own.
    const { subject, span } = irisRun();
    const said = (m: FaceModel) => m.notes.some((n) => n.includes('disagree by'));

    // 10%, comfortably past the 4.8% two behaving rulers explain between them.
    const far = run(subject, span / 1.10);
    assert.ok(
      said(far),
      `a 10% ruler disagreement went unmentioned: ${far.notes.join(' | ')}`,
    );
    assert.ok(
      far.notes.some((n) => n.includes('one eye')),
      'the note does not name the likeliest cause — a monocular PD typed whole',
    );

    // The wearer's PD agrees with the iris to a fraction of a percent.
    const near = run(subject, span * 1.001);
    assert.ok(
      !said(near),
      `two rulers 0.1% apart were reported as disagreeing: ${near.notes.join(' | ')}`,
    );
    // ... and the ordinary note still fires, so "quiet" is not "silent".
    assert.ok(near.notes.some((n) => n.includes('scale set from your PD')));
  });

  it('says which WAY the displaced ruler was out', () => {
    // The note read `the iris scale was +3.0% out`, built from `correction - 1`.
    // A leading plus in front of "out" reads as "the iris was 3% too big", and
    // a positive `correction - 1` means the opposite: the scan had to be made
    // BIGGER because the iris had read the wearer small.
    const { subject, span } = irisRun();
    const large = run(subject, span / 1.05);
    assert.ok(
      large.notes.some((n) => n.includes('read you 5.0% large')),
      `the direction is missing or inverted: ${large.notes.join(' | ')}`,
    );
    const small = run(subject, span * 1.05);
    assert.ok(
      small.notes.some((n) => n.includes('read you 4.8% small')),
      `the direction is missing or inverted: ${small.notes.join(' | ')}`,
    );
  });
});

describe('ranking next to a frame, where the scale cancels', () => {
  // Scale is a common factor. It cancels out of a DIFFERENCE between two frames
  // and survives only in an absolute:
  //
  //     widthDelta(A) - widthDelta(B) = W_A - W_B          exact
  //     widthDelta(A)                 = W_A - 0.90 x F     carries the error
  //
  // Both frames' widths are known to the millimetre, so the first sentence
  // costs nothing and the second costs 1.365 mm per point of scale against a
  // 4 mm band. These tests assert the exactness rather than a tolerance,
  // because it IS exact — the face term is algebraically absent, not small.

  const spec = (id: string, frontWidthMm: number) => parametricFrame({
    id, frontWidthMm, padSeparationMm: 17, padAngleRad: 0.67, massG: 24,
  });
  const wide = spec('w148', 148);
  const pivot = spec('w140', 140);

  const subject = generatePopulation(mesh, basis, { count: 1 })[0];
  const scaled = (factor: number) => {
    const p = new Float64Array(subject.positions.length);
    for (let i = 0; i < p.length; i++) p[i] = subject.positions[i] * factor;
    return truthModel(p);
  };
  const widthOf = (factor: number, reference?: typeof pivot) =>
    assessFit(scaled(factor), mesh, regions, wide, undefined, reference)
      .measures.find((m) => m.id === 'width')!;

  it('the absolute width verdict moves with the scan scale', () => {
    // The control. Without this the test below could pass on a verdict that
    // never moves for any reason at all.
    const moved = Math.abs(widthOf(1.025).value! - widthOf(1.0).value!);
    assert.ok(
      moved > 3,
      `2.5% of scale moved the absolute width verdict by only ${moved.toFixed(2)} mm — ` +
      'expected about 3.5 mm, since the target is 0.90 of a ~155 mm face span',
    );
  });

  it('the same verdict against a reference does not move at all', () => {
    const a = widthOf(1.0, pivot);
    const b = widthOf(1.025, pivot);
    // Exactly equal, not nearly: the face term is algebraically absent.
    assert.equal(
      a.value, b.value,
      `${a.value} vs ${b.value} — the reference form still carries a face term`,
    );
    assert.equal(a.value, 148 - 140, 'the difference is not the difference of the two widths');
    assert.equal(a.grade, b.grade);
  });

  it('says what it is relative to, so the number cannot be read as an absolute', () => {
    // "4 mm wider than the Navigator" and "4 mm wider than you need" are
    // different sentences and only one of them is exact. This file has already
    // shipped one number carrying two meanings; the label travels with it now.
    assert.equal(widthOf(1.0, pivot).relativeTo, pivot.name);
    assert.equal(widthOf(1.0).relativeTo, undefined);
  });

  it('drops the scale caveat and keeps the asset caveat', () => {
    // Scale is not in the number, so the scale caveat comes off. What does not
    // come off is where the two WIDTHS came from, and it now applies to both
    // frames: a difference between two widths is worth what the worse-known of
    // the two is worth. Both parametric frames declare `assumed`, so this reads
    // 0.3 x 0.3 — nearly worthless, and correctly so. It becomes 1.0 the day
    // the catalogue's widths are measured rather than placed.
    const irisModel = createFaceModel({
      positions: new Float64Array(subject.positions),
      vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
      shapeCoeffs: new Float64Array(0),
      basisName: 'ground-truth',
      displacementRmsMm: 0, displacementMaxMm: 0,
      intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
      intrinsicsSolved: true,
      scale: { source: 'iris', factor: 1, sigma: 0.047, note: 'pooled iris' },
      landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
      quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
      pdMm: null, pdSigmaMm: null,
      reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
    });
    const w = (reference?: typeof pivot) =>
      assessFit(irisModel, mesh, regions, wide, undefined, reference)
        .measures.find((m) => m.id === 'width')!.confidence;
    // On a 4.7% ruler the absolute verdict's whole 4 mm band is consumed 1.6
    // times over, so it is worth exactly nothing and contributes nothing to the
    // ordering. The reference form is what gives it back.
    assert.equal(w(), 0, `the absolute width verdict kept ${w()} on a 4.7% ruler`);
    assert.ok(w(pivot) > 0, 'the reference form is worth nothing either — the scale ' +
      'caveat is still being applied to a number the scale is not in');
    assert.ok(
      Math.abs(w(pivot) - 0.09) < 1e-9,
      `both frames declare an assumed width, so the comparison should be worth ` +
      `0.3 x 0.3 = 0.09, and it is worth ${w(pivot)}`,
    );
  });

  it('reorders the catalogue, and the reference is not privileged into first place', () => {
    // A similarity ordering does not endorse the reference. It ranks high on
    // width by construction — it is zero millimetres from itself — but the seat
    // carries three times width's weight, so it does not simply win.
    const frames = [spec('w126', 126), spec('w134', 134), pivot, spec('w146', 146), wide];
    const ranked = rankCatalogue(truthModel(subject.positions), mesh, regions, frames, pivot);
    assert.equal(ranked.length, frames.length);
    assert.ok(
      ranked.some((r) => r.frame.id !== pivot.id),
      'the ranking collapsed to the reference alone',
    );
    for (const r of ranked) {
      assert.equal(
        r.assessment.measures.find((m) => m.id === 'width')!.relativeTo, pivot.name,
        `${r.frame.id} was ranked without the reference the others were ranked against`,
      );
    }
  });
});

describe('the scale caveat sits where scale actually moves the verdict', () => {
  // The caveat used to be a flat multiply on exactly two verdicts — `width` and
  // `vertex` — and measured, it was on the wrong two. Per 1% of scale: width
  // moves 1.37 mm against a 4 mm band, and vertex moves 0.035 mm against a 4 mm
  // band. That is a factor of forty, and they carried the SAME caveat, while
  // `height`, `depth`, `panto` and `load` all move and carried none at all.
  //
  // These are behavioural rather than textual: `dist/` keeps comments, so
  // grepping the compiled source for an English word is a check that cannot
  // fail. They instantiate the compiled function and compare what it returns.

  const subject = generatePopulation(mesh, basis, { count: 1 })[0];
  const frame = TEST_FRAMES[1];

  /** The same face and the same frame, differing only in the scale estimate. */
  const at = (scale: ScaleEstimate) => {
    const model = createFaceModel({
      positions: new Float64Array(subject.positions),
      vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
      shapeCoeffs: new Float64Array(0),
      basisName: 'ground-truth',
      displacementRmsMm: 0, displacementMaxMm: 0,
      intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
      intrinsicsSolved: true,
      scale,
      landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
      quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
      pdMm: null, pdSigmaMm: null,
      reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
    });
    const out = new Map<string, number>();
    for (const m of assessFit(model, mesh, regions, frame).measures) out.set(m.id, m.confidence);
    return out;
  };

  // An exact ruler against the shipping iris rung. The POSITIONS are identical,
  // so every graded value is identical too and only the confidences can move.
  const exact = at({ source: 'pd', factor: 1, sigma: 0.001, note: 'exact' });
  const iris = at({ source: 'iris', factor: 1, sigma: 0.047, note: 'pooled iris' });

  it('withholds the vertex verdict when the lens centres were only estimated', () => {
    // Finding 5. `frame-asset.ts` and `frame-from-mesh.ts` had both promised,
    // since the derived fallback was written, that `score.ts` "withholds the
    // vertex-distance ... grade when it is not 'measured'". Nothing did:
    // `lensSource` was set, stored, and read only to build a note string, so the
    // two shipped assets that name no lens part — `meshy` and `crystal-parts` —
    // were graded on a lens plane taken from the extent centre of the frontmost
    // slice of the WHOLE mesh, hinge and forward-temple geometry included, and
    // the wearer was shown the millimetres that came off it.
    //
    // The three cases are one field apart, so they are asserted one field apart:
    // nothing else about the frame or the face moves between them.
    const scaleAt: ScaleEstimate = { source: 'pd', factor: 1, sigma: 0.001, note: 'exact' };
    const m = createFaceModel({
      positions: new Float64Array(subject.positions),
      vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
      shapeCoeffs: new Float64Array(0),
      basisName: 'ground-truth',
      displacementRmsMm: 0, displacementMaxMm: 0,
      intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
      intrinsicsSolved: true,
      scale: scaleAt,
      landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
      quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
      pdMm: null, pdSigmaMm: null,
      reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
    });
    const vertexOf = (lensSource: FrameAsset['lensSource']) =>
      assessFit(m, mesh, regions, { ...frame, lensSource }).measures.find((v) => v.id === 'vertex')!;

    // Estimated off a scan: withheld. `'unknown'` scores exactly neutral in
    // `scoreOf` whatever the confidence, and `ui.ts` renders no row for a null
    // value, so neither the ranking nor the readout carries it.
    const derived = vertexOf('derived');
    assert.equal(derived.grade, 'unknown', 'a derived lens centre still produced a grade');
    assert.equal(derived.value, null, 'a derived lens centre still put millimetres on the screen');
    assert.equal(derived.confidence, 0);

    // Measured off named lens parts: graded, with a number.
    const measured = vertexOf('measured');
    assert.notEqual(measured.grade, 'unknown', 'a measured lens centre lost its grade');
    assert.ok(Number.isFinite(measured.value), 'a measured lens centre lost its value');

    // Placed by a parametric spec: NOT withheld. This is the case the first cut
    // of the gate got wrong — `parametricFrame` also reported `'derived'`, so
    // gating on `!== 'measured'` silently took the vertex verdict away from
    // every frame the seat and scale tests are written against. Those centres
    // are exact by construction; only the scan-derived ones are estimates.
    const constructed = vertexOf('constructed');
    assert.notEqual(constructed.grade, 'unknown',
      'a constructed lens centre was withheld — the gate cannot tell a placed centre from a guessed one');
    assert.equal(constructed.value, measured.value,
      'the same geometry graded differently under two labels');

    // And the point of withholding: the untrusted number stops reaching the
    // VERDICT. Asserted on the vertex measure and not on the score, because the
    // score is not insensitive to `lensCentres` and cannot be — the same field
    // is the frame's centre of mass in `comOf`, the depth of the clearance
    // ring, and the rim size in `frame-layout.ts`, all of which stay graded.
    // An earlier version of this test asserted the SCORE was unmoved by a 10 mm
    // shift; it passed at this fixture and this magnitude by grade-band luck and
    // broke on 55 of 120 (face, frame) pairs, which is a test that cannot tell a
    // working gate from a gate whose input still steers the ranking.
    const shifted = (dz: number): FrameAsset['lensCentres'] => [
      Float64Array.of(frame.lensCentres[0][0], frame.lensCentres[0][1], frame.lensCentres[0][2] + dz),
      Float64Array.of(frame.lensCentres[1][0], frame.lensCentres[1][1], frame.lensCentres[1][2] + dz),
    ];
    const vertexAt = (lensSource: FrameAsset['lensSource'], dz: number) =>
      assessFit(m, mesh, regions, { ...frame, lensSource, lensCentres: shifted(dz) })
        .measures.find((v) => v.id === 'vertex')!;

    // Both directions, at the magnitude the frontmost-slice fallback errs by.
    for (const dz of [-10, 10]) {
      const v = vertexAt('derived', dz);
      assert.equal(v.grade, 'unknown', `a derived centre shifted ${dz} mm produced a grade`);
      assert.equal(v.value, null, `a derived centre shifted ${dz} mm produced a value`);
    }
    // The control: the same shifts must move the measured verdict, or this test
    // cannot see the gate work.
    assert.notEqual(vertexAt('measured', -10).value, vertexAt('measured', 10).value,
      'a 20 mm swing in a MEASURED lens centre moved the vertex verdict nowhere');
  });

  it('leaves the least scale-sensitive verdict almost untouched', () => {
    // 0.035 mm per point of scale against a 4 mm band: a 4.7% ruler consumes
    // 4% of the tolerance. Vertex's real exposure is the temple reach, and that
    // caveat is a separate one beside it.
    const kept = iris.get('vertex')! / exact.get('vertex')!;
    assert.ok(
      kept > 0.9,
      `the iris rung cost the vertex verdict ${((1 - kept) * 100).toFixed(0)}% of its ` +
      'confidence — vertex is the least scale-sensitive claim measured and should keep it',
    );
  });

  it('takes the width verdict apart, because one sigma eats the whole band', () => {
    // 1.37 mm per point against a 4 mm band: 4.7% consumes it 1.6 times over.
    // The verdict is not WRONG, it is uninformative, and `scoreOf` shrinks it
    // toward neutral rather than dropping it — so an uncertain measurement
    // cannot make a frame look good or bad by being uncertain.
    const kept = iris.get('width')! / exact.get('width')!;
    assert.ok(
      kept < 0.2,
      `the width verdict kept ${(kept * 100).toFixed(0)}% of its confidence on a 4.7% ` +
      'ruler, where a single sigma is 6.4 mm against a 4 mm band',
    );
    // The pair is the whole point. A flat multiply — the old behaviour — makes
    // these two identical, and this line is what turns that red.
    assert.ok(
      iris.get('vertex')! / exact.get('vertex')! > 4 * kept,
      'width and vertex lost the same fraction of their confidence, which is the ' +
      'flat multiply this replaced: they differ by a factor of forty in sensitivity',
    );
  });

  it('gives the seat verdicts the caveat they never carried', () => {
    // `height`, `depth`, `panto` and `load` all move under scale — the frame is
    // a fixed metric object and the face is not, so a rescaled wedge catches it
    // somewhere else — and every one of them read `nose.value` alone.
    for (const id of ['height', 'depth', 'panto', 'load']) {
      assert.ok(
        iris.get(id)! < exact.get(id)! * 0.999,
        `${id} costs nothing on a 4.7% ruler (${exact.get(id)!.toFixed(4)} -> ` +
        `${iris.get(id)!.toFixed(4)}) — it moves under scale and carries no caveat`,
      );
    }
  });

  it('charges each verdict in proportion to what scale actually does to it', () => {
    // The whole design in one assertion. Nothing in the verdict list is exactly
    // scale-invariant — the seat is a contact equilibrium, so even the roll of
    // a settled frame moves a little — so the claim is not "some pay nothing",
    // it is that what each pays is ORDERED by its measured sensitivity. A flat
    // multiply, a hand-picked subset, or a table with two entries transposed
    // all turn this red.
    //
    // Measured fraction of the good band consumed per 1% of scale:
    //   width 34.1%, height 8.3%, depth 5.6%, panto 4.6%, vertex 0.8%, level 0.3%
    const order = ['width', 'height', 'depth', 'panto', 'vertex', 'level'];
    const kept = order.map((id) => iris.get(id)! / exact.get(id)!);
    for (let i = 1; i < order.length; i++) {
      assert.ok(
        kept[i] > kept[i - 1],
        `${order[i]} kept ${(kept[i] * 100).toFixed(1)}% of its confidence and the more ` +
        `scale-sensitive ${order[i - 1]} kept ${(kept[i - 1] * 100).toFixed(1)}% — the ` +
        'caveat is not ordered by the sensitivity it claims to be built from',
      );
    }
    // `pads` and `load` sit between panto and vertex at 2.2% and 2.1% of band,
    // close enough that their order is inside the per-seed spread, so they are
    // bracketed rather than sequenced.
    for (const id of ['pads', 'load']) {
      const k = iris.get(id)! / exact.get(id)!;
      assert.ok(
        k > kept[order.indexOf('panto')] && k < kept[order.indexOf('vertex')],
        `${id} kept ${(k * 100).toFixed(1)}%, outside the panto-to-vertex bracket its ` +
        'measured sensitivity puts it in',
      );
    }
  });

  it('a second ruler that disagrees is what makes any of this see the wearer', () => {
    // The point of the whole cluster. `sigma` on the iris rung is the same
    // number for everybody, so it cannot tell a wearer the 11.70 mm assumption
    // fits from one carrying 5.4%. A disagreement between two rulers can.
    const pd = { source: 'pd' as const, factor: 1, sigma: 0.008, note: 'pd' };
    const alone = at(pd);
    const agreeing = at({ ...pd, disagreementPct: 3 });
    const fighting = at({ ...pd, disagreementPct: 20 });

    // 3% is well inside what two behaving rulers explain between them (4.8%),
    // so it is not evidence of anything and must cost nothing. Without this the
    // implementation could simply penalise any disagreement at all.
    for (const id of ['width', 'height', 'vertex']) {
      assert.equal(
        agreeing.get(id), alone.get(id),
        `${id} was penalised for two rulers agreeing to within their own sigmas`,
      );
    }

    // 20% is four times what they explain. One of them is wrong, this cannot
    // say which, and the excess is priced as error rather than used to pick a
    // winner. This is also the only defence against a mistyped PD, where the
    // printed sigma moves the WRONG WAY: a PD typed 10% high prints a smaller
    // sigma than a correct one.
    for (const id of ['width', 'height']) {
      assert.ok(
        fighting.get(id)! < alone.get(id)! * 0.5,
        `${id} kept ${((fighting.get(id)! / alone.get(id)!) * 100).toFixed(0)}% of its ` +
        'confidence while the two rulers disagreed by 20% — the gap is not being read',
      );
    }
  });
});

describe('the seat is pinned by the cheek, so the cheek has to be reported', () => {
  it('names cheek depth among the measurements', () => {
    // A real wearer's two scans, half an hour apart, moved padDepthErrorMm by
    // 2.68 mm and vertex distance by 5 mm while every REPORTED measurement moved
    // about 1%. Reconstructing their face from those measurements accounted for
    // 22% of it. The missing input was cheek depth, which nothing measured.
    const m = measure(mesh.positions);
    assert.ok(Number.isFinite(m.cheekDepth), 'cheekDepth is not measured');
    // Measured across 40 synthetic faces: 54.2 to 69.6 mm, median 61.3, template
    // 61.9. The CHEEK landmark is lateral, so this span is most of the way round
    // the side of the head rather than a shallow facial contour — a fact worth
    // pinning, because a "cheek depth" that came back at 15 mm would mean the
    // landmark had moved and the whole hook chain with it.
    assert.ok(
      m.cheekDepth > 40 && m.cheekDepth < 85,
      `cheekDepth ${m.cheekDepth.toFixed(1)} mm is outside the measured 54-70 band`,
    );
  });

  it('no longer lets the cheek decide where the lenses sit — the anchor repair', () => {
    // This test used to assert the OPPOSITE: that a 9 mm cheek shift moves the
    // frame fore-aft, because `earRestPoints` derived the ear's depth from
    // cheek.z - 17. That mechanism was the recorded Q16 risk, and on the first
    // real wearer it missed by enough to bury the pads 1.9 mm and park the
    // lenses 5 mm from the eyes — with the Q15 hook-stiffness experiment barely
    // moving it, the fingerprint of a wrongly PLACED wall rather than a wrongly
    // stiff one. The repair anchors the ear on the outer canthus (the scan's
    // best landmark, ~0.6 mm on a real face) plus the published
    // ectocanthion-tragion offset (EYE_TO_TRAGION_Z_MM), so the same cheek
    // shift must now move almost nothing, and a canthus-depth shift must move
    // the frame instead. Both directions asserted; break either and Q16 has
    // been re-opened.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const frame = TEST_FRAMES.find((f) => f.id === 'standard')!;
    const seatShifting = (members: Uint32Array | number[], weight: Float64Array | null, dz: number) => {
      const p = new Float64Array(subject.positions);
      for (const i of members) {
        if (!weight || weight[i] >= 0.5) p[i * 3 + 2] += dz;
      }
      return solveSeat(truthModel(p), mesh, regions, frame);
    };

    const cheekBack = seatShifting(regions.cheeks.members, regions.cheeks.weight, -6);
    const cheekForward = seatShifting(regions.cheeks.members, regions.cheeks.weight, 3);
    const cheekLever = Math.abs(cheekForward.pose.t[2] - cheekBack.pose.t[2]);
    assert.ok(
      cheekLever < 0.75,
      `a 9 mm cheek shift still moves the frame ${cheekLever.toFixed(2)} mm fore-aft — ` +
      'the ear anchor is reading the cheek again',
    );

    const canthusBack = seatShifting([LM.EYE_OUTER_R, LM.EYE_OUTER_L], null, -4);
    const canthusForward = seatShifting([LM.EYE_OUTER_R, LM.EYE_OUTER_L], null, 4);
    const canthusLever = canthusForward.pose.t[2] - canthusBack.pose.t[2];
    assert.ok(
      canthusLever > 2,
      `an 8 mm canthus-depth shift moved the frame only ${canthusLever.toFixed(2)} mm — ` +
      'the new anchor is not live, and nothing pins fore-aft at all',
    );
  });
});

describe('the gates that run before the tests do', () => {
  // Both scripts resolve `src`, `dist` and `docs/CONSTANTS.md` relative to the
  // CURRENT WORKING DIRECTORY, so each case gets a throwaway tree and runs the
  // real script as a subprocess against it. Nothing here touches the repo's own
  // dist/, which other work builds into.
  const scriptsDir = fileURLToPath(new URL('../../scripts/', import.meta.url));

  /** Runs a gate script with `cwd` set to a fixture tree. */
  const run = (dir: string, script: string): { status: number; out: string } => {
    // spawnSync rather than execFileSync: these scripts say the important things
    // on STDERR — including the loud skip — and execFileSync hands back stdout
    // only on the success path, so a green run would look silent either way.
    const r = spawnSync(process.execPath, [join(dir, 'scripts', script)], {
      cwd: dir, encoding: 'utf8',
    });
    return { status: r.status ?? -1, out: `${r.stdout ?? ''}${r.stderr ?? ''}` };
  };

  const fixture = (script: string): string => {
    const dir = mkdtempSync(join(tmpdir(), 'gate-'));
    mkdirSync(join(dir, 'scripts'), { recursive: true });
    copyFileSync(join(scriptsDir, script), join(dir, 'scripts', script));
    return dir;
  };

  it('fails the isolation boundary on a module that cannot load in Node', () => {
    // The exact failure the gate was blind to: `enroll.worker.ts` set
    // `self.onmessage` at top level, matched none of the source-text patterns,
    // and the gate printed "isolation boundary intact" and exited 0. The import
    // pass is what catches a module whose SOURCE looks headless and whose
    // BEHAVIOUR is not.
    const dir = fixture('check-isolation.mjs');
    try {
      mkdirSync(join(dir, 'src/core'), { recursive: true });
      mkdirSync(join(dir, 'dist/src/core'), { recursive: true });
      writeFileSync(join(dir, 'src/core/b.ts'), 'export const B = 1;\n');
      // A built module that throws at load while its source trips no pattern —
      // the name is assembled at run time so no grep could see it.
      writeFileSync(join(dir, 'dist/src/core/b.js'),
        'const g = globalThis;\n'
        + 'if (typeof g["Screen" + "Orientation"] === "undefined") {\n'
        + '  throw new ReferenceError("needs a browser");\n'
        + '}\n'
        + 'export const B = 1;\n');

      const r = run(dir, 'check-isolation.mjs');
      assert.notEqual(r.status, 0, `the gate passed a module that cannot load:\n${r.out}`);
      assert.match(r.out, /does not load in Node/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('says out loud when it skipped the import pass', () => {
    // Silence is the bug this whole pass exists to prevent, so the exit code is
    // not enough: a green run that quietly checked half of what it claims to is
    // worse than a red one.
    const dir = fixture('check-isolation.mjs');
    try {
      mkdirSync(join(dir, 'src/core'), { recursive: true });
      writeFileSync(join(dir, 'src/core/b.ts'), 'export const B = 1;\n');
      const r = run(dir, 'check-isolation.mjs');
      assert.equal(r.status, 0, `a tree with no dist/ should still pass:\n${r.out}`);
      assert.match(r.out, /SKIPPED the import pass/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('strips the clock out of a report without touching a data column', () => {
    // `check-reports.mjs`'s canary hashes a regenerated report, and every report
    // carries a per-solve `ms` column whose value is the machine rather than the
    // code — so the clock has to come out before hashing or the gate reports a
    // drift on every different laptop.
    //
    // The FIRST version of that strip was `^(\S.*?)(\s+)\d+(\s+\d+)\s*$`:
    // blank the second-from-last integer on any row that ends in two integers.
    // It works on both real tables and it is exactly the blunt instrument this
    // gate exists to prevent — a real data column that happened to sit in that
    // position would be silently blanked, and the canary would lose the ability
    // to see it change while still reporting itself green.
    //
    // So the strip reads the table's own underline for the column spans and the
    // header above it for which span is the clock. This asserts both halves:
    // the clock goes, and a same-shaped table with no `ms` header is untouched.
    const gate = join(scriptsDir, 'check-reports.mjs');
    return import(pathToFileURL(gate).href).then(({ stripTimings }) => {
      const withClock = [
        'frame          pad sep   ms  unconverged',
        '-------------  -------  ---  -----------',
        'narrow-pads         13  135            0',
        'standard            17  106            1',
        '',
      ].join('\n');
      const stripped = stripTimings(withClock).split('\n');
      assert.ok(!/135/.test(stripped[2]), `the clock survived: ${stripped[2]}`);
      assert.ok(!/106/.test(stripped[3]), `the clock survived: ${stripped[3]}`);

      // **The WIDTH is part of the clock.** Both fixture values above are three
      // digits, so for its whole life this test could not see that the strip
      // blanked digit-for-digit and left the column's width behind. The column
      // is right-aligned, so a 99 ms solve hashed as ` --` and a 114 ms one as
      // `---`: the seat canary took FOUR distinct values in ten consecutive
      // runs of one unchanged build (11f0e9f2, dab9f30e, dd50cac2, 99203b68),
      // one of which was the committed stamp. `npm test` failed the reports
      // gate at random on an unchanged tree and would have passed it at random
      // on a changed one.
      // Right-aligned in the span the underline declares, exactly as the
      // generator writes it -- so this is a faster machine, not a wider table.
      const faster = withClock.replace('  135  ', '   99  ').replace('  106  ', '    8  ');
      assert.notEqual(faster, withClock, 'the fast-machine fixture did not substitute');
      assert.equal(stripTimings(faster), stripTimings(withClock),
        'two runs of the same code on machines of different speed hash differently — '
        + 'the strip is erasing the clock\'s value and keeping its width');
      // **And the WIDTH of the column is the clock as well.** The two cases
      // above both hold the column at three characters wide, which is the shape
      // of fixture that let the previous repair look complete: it removed the
      // jitter WITHIN a width and left the jitter BETWEEN widths.
      //
      // `table()` sizes every column to its widest cell and the `ms` header is
      // two characters, so a run whose slowest solve reads 99 emits a TWO-dash
      // column and one that reads 101 emits three — a different underline, a
      // different pad on every row, a different hash, on identical code. The
      // narrow case was worse than a wrong width: the separator was matched
      // with `-{3,}`, so a two-dash run failed the line test outright and the
      // raw milliseconds went into the canary.
      //
      // These fixtures are built with the generators' own `table()`, so the
      // widths are the ones a report would really carry rather than ones this
      // test chose.
      const clocked = (a: number, b: number) =>
        `${table(['frame', 'pad sep', 'ms', 'unconverged'],
          [['narrow-pads', 13, a, 0], ['standard', 17, b, 1]])}\n`;

      const threeWide = clocked(135, 106);
      const twoWide = clocked(96, 82);
      assert.equal(twoWide.split('\n')[1].split(/\s+/)[2], '--',
        'the two-digit fixture did not produce a two-wide column');
      assert.ok(!/\b(96|82)\b/.test(stripTimings(twoWide)),
        'a two-wide clock column defeated the separator match and the raw '
        + 'milliseconds were hashed');
      assert.equal(stripTimings(twoWide), stripTimings(threeWide),
        'the same code on a faster machine hashes differently — the strip is '
        + "erasing the clock's value and keeping its column WIDTH");

      // The boundary, which is what makes it a coin flip rather than a
      // per-machine constant: one solve either side of 100 on the same laptop.
      assert.equal(stripTimings(clocked(99, 82)), stripTimings(clocked(101, 82)),
        'a single solve crossing 100 ms changed the hash');
      assert.equal(stripTimings(clocked(999, 82)), stripTimings(clocked(1001, 82)),
        'a single solve crossing 1000 ms changed the hash');

      // Everything else on the row is intact, including the trailing integer the
      // old positional rule sat next to. Asserted by content rather than by
      // spacing: whether the clock column is cut out or blanked to a fixed width
      // is the strip's business, and a test that pins the spacing would refuse a
      // different correct repair.
      assert.match(stripped[2], /^narrow-pads\s+13\s+(-+\s+)?0\s*$/, `row rewritten: ${stripped[2]}`);
      assert.match(stripped[3], /^standard\s+17\s+(-+\s+)?1\s*$/, `row rewritten: ${stripped[3]}`);

      // The same shape with no `ms` header. Under the positional rule the 135
      // and 106 here would be blanked too, and a real measurement would stop
      // being watched.
      const noClock = withClock.replace('   ms  unconverged', '  hits  unconverged')
        .replace('  ---  -----------', '  ----  -----------');
      assert.equal(
        stripTimings(noClock), noClock,
        'a table with no clock column was rewritten anyway — the strip is ' +
        'positional again, and the canary has stopped watching a real column',
      );
    });
  });

  it('strips the clock out of table shapes no report emits today', () => {
    // The strip finds the clock by reading a table's underline for column spans
    // and the header above it for which span is the clock, so its correctness
    // is a property of table GEOMETRY. Every generator emits one clock column
    // per table, blank-line separated, with no literal duration in a cell — so
    // the cases below are all latent. Each of them fails in the same direction
    // when it goes wrong: the column is not found, the raw milliseconds are
    // hashed, and a build-blocking gate goes back to being machine-dependent.
    //
    // Measured across all four generators at both `{seed:11, subjects:1}` and
    // their defaults when these were written: narrowest separator run 3, no
    // one-character columns, no adjacent tables, no dash-placeholder rows.
    const gate = join(scriptsDir, 'check-reports.mjs');
    return import(pathToFileURL(gate).href).then(({ stripTimings }) => {
      const T = (headers: string[], rows: (string | number)[][]) =>
        `${table(headers, rows)}\n`;
      // Two runs of the same code at clock readings a decade apart. Nothing
      // below may distinguish them.
      const same = (build: (ms: number) => string, what: string) => {
        assert.equal(stripTimings(build(99)), stripTimings(build(1010)), what);
        assert.ok(!/\b(99|1010)\b/.test(stripTimings(build(99))),
          `${what} — and the raw clock reached the hash`);
      };

      // A table carrying TWO clock columns. The strip used to keep a single
      // span per separator, so the earlier column survived with its value and
      // its width.
      same((ms) => T(['frame', 'ms', 'pad sep', 'ms'],
        [['narrow-pads', ms, 13, ms], ['standard', ms, 17, ms]]),
      'a table with two clock columns kept the first one');

      // A renamed header. `ms` -> `solve ms` is an ordinary edit, and this
      // gate's own preamble records a column being renamed in one of these
      // very tables.
      same((ms) => T(['frame', 'pad sep', 'solve ms'],
        [['narrow-pads', 13, ms], ['standard', 17, ms]]),
      'a clock column headed `solve ms` was not recognised');

      // ...but `rms` is not a clock, and its column is a real measurement.
      const rms = T(['frame', 'rms', 'unconverged'], [['narrow-pads', 135, 0]]);
      assert.equal(stripTimings(rms), rms,
        'an `rms` column was mistaken for a clock and deleted from the hash');

      // A literal duration inside a cell. The free-text rule rewrites it to
      // `- ms`, which SHORTENS the line — so if that ran before the column
      // spans were taken, every offset on the table moved out from under them.
      same((ms) => T(['stage', 'budget', 'ms'],
        [['solve', '45 ms', ms], ['pose', '50 ms', ms]]),
      'a literal duration in a cell desynchronised the column spans');

      // Two tables with no blank line between them. The second table's header
      // is a row of the first as far as a single-pass strip is concerned.
      same((ms) => `${T(['frame', 'ms', 'x'], [['n', ms, 1]]).trimEnd()}\n`
        + T(['other', 'ms', 'y'], [['m', ms, 2]]),
      'a table butted against the one above it was corrupted');

      // A data row of dash placeholders looks exactly like an underline. It
      // must not be taken for one — and it must not be able to stop the strip
      // for the rest of the table either.
      same((ms) => T(['frame', 'ms', 'x'],
        [['n', ms, 1], ['-', '-', '-'], ['s', ms, 2]]),
      'a dash-placeholder row was read as an underline');

      // A one-character column. Requiring every run to be two dashes or more
      // would exclude the whole table and hash its clock raw — the mirror of
      // the `-{3,}` defect that started this.
      same((ms) => T(['f', 'ms', 'x'], [['n', ms, 1], ['s', ms, 2]]),
      'a table with a one-character column was skipped entirely');
    });
  });

  it('refuses a constants ledger with two rows for one constant', () => {
    // Finding 36: two contradictory `SKIN.hookStiffnessNPerMm` rows, invisible
    // to the old checker. Two rows for one constant means at least one is stale,
    // and a reviewer reading either has no way to know which.
    const dir = fixture('check-constants.mjs');
    try {
      mkdirSync(join(dir, 'docs'), { recursive: true });
      mkdirSync(join(dir, 'src'), { recursive: true });
      writeFileSync(join(dir, 'docs/CONSTANTS.md'),
        '# ledger\n\n| constant | value | class | why |\n| --- | --- | --- | --- |\n'
        + '| `ALPHA` | 1 | `stated` | first. |\n'
        + '| `ALPHA` | 2 | `stated` | second, stale. |\n');
      writeFileSync(join(dir, 'src/a.ts'), 'export const ALPHA = 1;\n');
      const r = run(dir, 'check-constants.mjs');
      assert.notEqual(r.status, 0, `two rows for one constant passed:\n${r.out}`);
      assert.match(r.out, /has 2 rows/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('refuses a provenance class nobody defined', () => {
    // An unrecognised token used to count as a silent zero, so a row could
    // declare any provenance it liked and the ledger's own summary would omit
    // it without saying so.
    const dir = fixture('check-constants.mjs');
    try {
      mkdirSync(join(dir, 'docs'), { recursive: true });
      mkdirSync(join(dir, 'src'), { recursive: true });
      writeFileSync(join(dir, 'docs/CONSTANTS.md'),
        '# ledger\n\n| constant | value | class | why |\n| --- | --- | --- | --- |\n'
        + '| `ALPHA` | 1 | `guessed` | somebody made this up. |\n');
      writeFileSync(join(dir, 'src/a.ts'), 'export const ALPHA = 1;\n');
      const r = run(dir, 'check-constants.mjs');
      assert.notEqual(r.status, 0, `an unknown class passed:\n${r.out}`);
      assert.match(r.out, /has class 'guessed'/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('counts an `assumed` constant instead of dropping it', () => {
    // Finding 37: the temple-reach row's `assumed` class matched no pattern, so
    // the highest-leverage number in the tree was absent from the very count the
    // ledger exists to produce.
    const dir = fixture('check-constants.mjs');
    try {
      mkdirSync(join(dir, 'docs'), { recursive: true });
      mkdirSync(join(dir, 'src'), { recursive: true });
      writeFileSync(join(dir, 'docs/CONSTANTS.md'),
        '# ledger\n\n| constant | value | class | why |\n| --- | --- | --- | --- |\n'
        + '| `ALPHA` | 1 | `stated` | chosen. |\n'
        + '| `BETA` | 2 | `assumed` | assumed, and named as such. |\n');
      writeFileSync(join(dir, 'src/a.ts'), 'export const ALPHA = 1;\nexport const BETA = 2;\n');
      const r = run(dir, 'check-constants.mjs');
      assert.equal(r.status, 0, `a well-formed ledger failed:\n${r.out}`);
      assert.match(r.out, /1 assumed/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

// ------------------------------------------------------------- the occlusion instrument

describe('the occlusion instrument', () => {
  // Stage 0 of the occlusion plan: before any renderer exists, occlusion
  // quality is a measured quantity (src/testkit/report-occlusion.ts). These
  // four tests pin the instrument itself — the coordinate path, the reason the
  // scan exists, the bias sign convention, and the band's non-vacuousness —
  // because an instrument nobody has falsified is a number generator.

  const standardFrame = TEST_FRAMES[1];

  /** The seated frame cloud on a given model, in that model's space. */
  const seatedSamples = (model: FaceModel): Float64Array => {
    const seat = solveSeat(model, mesh, regions, standardFrame);
    return transformSamples(seat.pose, frameSampleSet(standardFrame));
  };

  it('truth against itself measures EXACTLY zero — the coordinate-path pin', () => {
    // If this fails, nothing else in the instrument means anything: the same
    // geometry at the same pose through the truth path and the occluder path
    // must produce identical masks, an identical band, zero boundary distance
    // at every contour pixel, and zero flips in both directions. Any nonzero
    // here is a disagreement between the two code paths — a second silhouette
    // convention, a raster scale mismatch, an epsilon applied to one side.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const samples = seatedSamples(truthModel(subject.positions));

    for (const geometry of CAMERA_LADDER) {
      const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
      for (const yawDeg of [0, 30, 60]) {
        const pose = ladderPose(geometry, yawDeg);
        const arm = { positions: subject.positions, pose };
        const cell = occlusionCell(mesh, arm, arm, { points: samples, pose }, k);
        const where = `${geometry.name} at yaw ${yawDeg}`;

        assert.ok(cell.bandTruthCount > 0, `${where}: empty glasses band — the pin is vacuous`);
        const worst = cell.boundaryPx.length ? Math.max(...cell.boundaryPx) : NaN;
        assert.equal(
          worst, 0,
          `${where}: truth-vs-truth boundary error is ${worst} px, not exactly 0 — ` +
          'the truth path and the occluder path have diverged; diff how the two ' +
          'arms are rasterised and how their contours are extracted before ' +
          'trusting any other number from this instrument',
        );
        assert.equal(cell.meanOffsetMm, 0, `${where}: truth-vs-truth mask XOR is not empty`);
        const f = flipsAt(cell, 0);
        assert.ok(f.contested > 0, `${where}: nothing contested — the frame never meets the face`);
        assert.equal(
          f.xray + f.forgiven, 0,
          `${where}: ${f.xray} x-ray + ${f.forgiven} forgiven flips against itself — ` +
          'the hidden classifier treats the two buffers differently (an epsilon ' +
          'or bias is being applied to one side only)',
        );

        // The orientation observable, which a conjugated rotation cannot fake:
        // in CV convention +Y is DOWN, and glasses sit above the face centroid,
        // so the band centre must have the SMALLER image y. If this fires, the
        // FACE_TO_CAMERA_FLIP convention broke somewhere between the pose
        // builder and the raster — every mask is upside down.
        assert.ok(
          cell.bandCentrePxY < cell.faceCentroidPxY,
          `${where}: glasses band (y ${cell.bandCentrePxY.toFixed(0)}) sits BELOW the ` +
          `face centroid (y ${cell.faceCentroidPxY.toFixed(0)}) — the face/camera flip ` +
          'is wrong and the masks are upside down; check ladderPose against ' +
          'poseRotationFromHeadEuler before anything else',
        );
      }
    }

    // And the zero can be something else — the rule this tree's harness lives
    // by. The same instrument pointed at a different surface must move.
    const k = intrinsicsFromFov(CAMERA_LADDER[0].width, CAMERA_LADDER[0].height, CAMERA_LADDER[0].fovDeg);
    const pose = ladderPose(CAMERA_LADDER[0], 30);
    const templateArm = fitOccluderArm(mesh.positions, subject.positions, mesh.vertexCount, pose, k);
    const cell = occlusionCell(
      mesh, { positions: subject.positions, pose }, templateArm, { points: samples, pose }, k,
    );
    assert.ok(
      cell.meanOffsetMm > 0.1,
      `template-vs-truth offset is ${cell.meanOffsetMm.toFixed(3)} mm — the instrument ` +
      'cannot distinguish the average head from this subject, so its zeros are ' +
      'not measurements',
    );
  });

  it('the template as occluder is measurably worse than the scan at 30 degrees and beyond', () => {
    // The reason scan-once occlusion exists. Measured (reports/occlusion.txt,
    // metric D): the template occluder adds 1.3-2.3 mm of banded boundary
    // offset over the scan at yaw 30-45, across all three camera geometries
    // and five seeds (per-seed range 0.87-2.27). The bar is 0.35 mm — well
    // under every measured realisation, and far above zero.
    const geometry = CAMERA_LADDER[0];
    const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
    const diffs: number[] = [];

    for (const subject of generatePopulation(mesh, basis, { count: 2 })) {
      const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
      const scan = enroll({
        mesh, basis,
        frames: capture.frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
        imageWidth: geometry.width, imageHeight: geometry.height,
        irisMm: subject.irisDiameterMm,
      }).model;
      const samples = seatedSamples(scan);

      for (const yawDeg of [30, 45]) {
        const pose = ladderPose(geometry, yawDeg);
        const truthArm = { positions: subject.positions, pose };
        const scanArm = fitOccluderArm(scan.positions, subject.positions, mesh.vertexCount, pose, k);
        const armSamples = { points: scaleSamples(samples, scanArm.gauge), pose: scanArm.pose };
        const scanCell = occlusionCell(mesh, truthArm, scanArm, armSamples, k, { biasesMm: [0] });
        const templateArm = fitOccluderArm(mesh.positions, subject.positions, mesh.vertexCount, pose, k);
        const templateCell = occlusionCell(mesh, truthArm, templateArm, armSamples, k, { biasesMm: [0] });
        diffs.push(templateCell.meanOffsetMm - scanCell.meanOffsetMm);
      }
    }

    const med = distribution(diffs).median;
    assert.ok(
      med > 0.35,
      `template occluder is only ${med.toFixed(3)} mm worse than the scan in the ` +
      `glasses band at 30-45 degrees (per-cell: ${diffs.map((d) => d.toFixed(2)).join(', ')}) — ` +
      'either the scan stopped reconstructing the contour (compare metric A in ' +
      'reports/occlusion.txt against its checked-in copy) or the instrument is ' +
      'no longer reading the occluder it was handed. If the scan genuinely got ' +
      'this close to the template, scan-once occlusion has lost its argument ' +
      'and stage 1 should hear about it before a renderer is built on it.',
    );
  });

  it('pushing the occluder away from the camera trades X-ray for forgiveness, monotonically — the sign pin', () => {
    // The bias sweep's sign convention, pinned against the truth itself so no
    // scan error can muddy it: positive bias moves the occluder AWAY (depths
    // grow), the face hides less, X-ray can only rise and forgiveness can only
    // fall. If this fails, the renderer would read the bias table backwards
    // and push the occluder the wrong way — check the sign applied to the
    // depth comparison in the hidden classifier.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const samples = seatedSamples(truthModel(subject.positions));
    const biases = [-1.5, -1, -0.5, 0, 0.5, 1, 1.5];
    const xray = new Array(biases.length).fill(0);
    const forgiven = new Array(biases.length).fill(0);

    for (const geometry of CAMERA_LADDER.slice(0, 2)) {
      const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
      for (const yawDeg of [0, 30, 60]) {
        const pose = ladderPose(geometry, yawDeg);
        const arm = { positions: subject.positions, pose };
        const cell = occlusionCell(mesh, arm, arm, { points: samples, pose }, k, { biasesMm: biases });
        biases.forEach((b, i) => {
          xray[i] += flipsAt(cell, b).xray;
          forgiven[i] += flipsAt(cell, b).forgiven;
        });
      }
    }

    for (let i = 1; i < biases.length; i++) {
      assert.ok(
        xray[i] >= xray[i - 1],
        `x-ray fell from ${xray[i - 1]} to ${xray[i]} as the bias moved away from the ` +
        `camera (${biases[i - 1]} -> ${biases[i]} mm) — the bias sign is inverted in the ` +
        'hidden classifier',
      );
      assert.ok(
        forgiven[i] <= forgiven[i - 1],
        `forgiven hides rose from ${forgiven[i - 1]} to ${forgiven[i]} as the bias moved ` +
        `away from the camera (${biases[i - 1]} -> ${biases[i]} mm) — the bias sign is ` +
        'inverted in the hidden classifier',
      );
    }
    // Both directions must actually engage, or the monotonicity above is a
    // comparison of zeros.
    assert.ok(
      xray[biases.length - 1] > 0,
      'no x-ray at +1.5 mm — the away-from-camera side of the sweep never engaged',
    );
    assert.ok(
      forgiven[0] > 0,
      'no forgiven hides at -1.5 mm — the toward-camera side of the sweep never engaged',
    );
  });

  it('the glasses band is non-empty at every yaw bucket the report sweeps', () => {
    // A vacuously-empty band is a check that cannot fail: every boundary
    // metric would be a median over nothing and every flip count a fraction
    // of zero. The floor of 10 contour pixels keeps a two-pixel sliver from
    // counting as a band.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const samples = seatedSamples(truthModel(subject.positions));

    for (const geometry of CAMERA_LADDER) {
      const k = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
      for (const yawDeg of occlusionYawLadder) {
        const pose = ladderPose(geometry, yawDeg);
        const arm = { positions: subject.positions, pose };
        const cell = occlusionCell(mesh, arm, arm, { points: samples, pose }, k, { biasesMm: [0] });
        assert.ok(
          cell.bandTruthCount >= 10 && cell.bandOccluderCount >= 10,
          `${geometry.name} at yaw ${yawDeg}: band has ${cell.bandTruthCount} truth / ` +
          `${cell.bandOccluderCount} occluder contour pixels — the projected frame ` +
          'samples are missing the face (check the seat transform and the band ' +
          'padding before trusting any occlusion number at this pose)',
        );
      }
    }
  });
});

// ------------------------------------------------- the eye-corner reference

describe('the eye-corner reference plane (Q24)', () => {
  it('carries no depth bias under the default configuration — the settled claim', () => {
    // Q24 (docs/OPEN-QUESTIONS.md, settled 2026-08-23): the "+2.28 mm
    // eye-corner depth bias" recorded during the Q18 settlement does not exist
    // in any frame a seat can feel. Re-measured on this tree over 17 subjects
    // x campaign seeds {11, 23, 37} (eye-level, shipped config), the four
    // corner vertices' signed depth error after the tree's standard whole-mesh
    // rigid alignment is +0.003 mm median, 0.206 mm median absolute (per-seed
    // medians +0.128 / +0.003 / -0.045) — and it does not follow
    // `gazeAmplitudeMm` (+0.049 at gaze 0). The one definition that reproduces
    // the recorded one-signed signature is the UNREGISTERED comparison of the
    // two canonical frames (-5.1 mm median, per-seed -2.5 to -8.2): the
    // solve's gauge, which `vertexDistanceMm` subtracts out by construction
    // because both of its ends ride the same frame. So nothing was "repaired";
    // this bar pins the measured fact instead.
    //
    // Fixture draw measured for the bars: per-subject signed dz -0.459 to
    // +0.299 mm, median -0.124, worst |.| 0.459 (n=8). The bars carry headroom
    // over those digits but sit far below the 2.28 the question was filed
    // about, so a genuine corner-depth bias of that size fails both, loudly.
    // Breakage-verified in a private build, each bar separately: +2.3 mm
    // injected into the corners' z after the bundle fails the median bar at
    // 2.144; the same injection on one subject alone fails the worst bar at
    // 2.568.
    const geometry = CAMERA_LADDER[0];
    const corners = [LM.EYE_OUTER_R, LM.EYE_OUTER_L, LM.EYE_INNER_R, LM.EYE_INNER_L];
    const dzs: number[] = [];
    for (const subject of generatePopulation(mesh, basis, { count: 6 })) {
      const capture = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 12 });
      const model = enroll({
        mesh, basis,
        frames: capture.frames.map((f) => ({
          landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
          silhouette: f.silhouette, beat: f.beat,
        })),
        imageWidth: geometry.width, imageHeight: geometry.height,
      }).model;
      assert.ok(!model.degraded, `scan degraded: ${model.notes.join('; ')}`);
      const aligned = rigidAlign(model.positions, subject.positions, mesh.vertexCount);
      let dz = 0;
      for (const c of corners) dz += aligned[c * 3 + 2] - subject.positions[c * 3 + 2];
      dzs.push(dz / corners.length);
    }
    const d = distribution(dzs);
    assert.ok(
      Math.abs(d.median) < 0.75,
      `eye-corner depth bias ${d.median.toFixed(3)} mm median (fixture record: -0.124) — ` +
      'the reference plane of every vertexDistanceMm has moved. If this reads near +2.3, ' +
      'the Q24 gauge artefact has become real geometry and the question reopens',
    );
    assert.ok(
      d.worst < 1.5,
      `worst per-subject eye-corner depth error ${d.worst.toFixed(3)} mm ` +
      '(fixture record: 0.459) — one subject\'s corner plane came out badly, which the ' +
      'Q24 settlement population never showed',
    );
  });
});
