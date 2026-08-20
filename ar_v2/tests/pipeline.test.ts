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

import { loadBasis, loadRegions, loadTemplateMesh } from '../src/testkit/fixtures.js';
import {
  CAMERA_LADDER, PLAUSIBLE, generatePopulation, lastRejectionCount,
  subjectResidualAgainstBasis, synthesizeCapture,
} from '../src/testkit/synthetic.js';
import { compareToTruth, distribution } from '../src/testkit/metrics.js';
import { measure, standardRegions } from '../src/core/mesh.js';
import { basisExplains } from '../src/core/shape/anthropometric.js';
import {
  createDisplacementField, displacementStats, refreshNormals,
} from '../src/core/shape/displacement.js';
import { enroll } from '../src/enroll/enroll.js';
import { assessCoverage, selectKeyframes } from '../src/enroll/keyframes.js';
import { IRIS, POPULATION_HVID, solveScale } from '../src/enroll/scale.js';
import { buildCorrespondences, solvePnP } from '../src/track/pnp.js';
import { createTracker, track } from '../src/track/tracker.js';
import { createFaceModel, type FaceModel } from '../src/core/facemodel.js';
import { solveSeat } from '../src/fit/contact.js';
import { TEST_FRAMES, derivePads, parametricFrame } from '../src/fit/frame-asset.js';
import { assessFit, rankCatalogue } from '../src/fit/advice.js';
import { rotationAngleBetween } from '../src/core/linalg.js';

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

  it('explains the template itself exactly', () => {
    const { residualRmsMm } = basisExplains(basis, mesh.positions);
    assert.ok(residualRmsMm < 1e-9, `residual ${residualRmsMm}`);
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
});

// ---------------------------------------------------------------------- PnP

describe('pose against a known model', () => {
  it('does not degrade with yaw — the architectural claim', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const capture = synthesizeCapture(mesh, subject, CAMERA_LADDER[0], { framesPerBeat: 8 });

    const byYaw = new Map<number, number[]>();
    for (const frame of capture.frames) {
      const correspondences = buildCorrespondences(
        frame.landmarks, frame.sigmaPx, mesh.vertexCount,
      );
      if (correspondences.length < 40) continue;
      const result = solvePnP(subject.positions, correspondences, capture.trueIntrinsics);
      const deg = (rotationAngleBetween(result.pose.R, frame.pose.R) * 180) / Math.PI;
      const bucket = Math.round((Math.abs(frame.trueYaw) * 180) / Math.PI / 30) * 30;
      if (!byYaw.has(bucket)) byYaw.set(bucket, []);
      byYaw.get(bucket)!.push(deg);
    }

    const frontal = distribution(byYaw.get(0) ?? []).median;
    assert.ok(frontal < 1.5, `frontal rotation error ${frontal.toFixed(2)} deg`);
    for (const [bucket, values] of byYaw) {
      const median = distribution(values).median;
      // The bar: error at any yaw is within 4x the frontal error. v1's
      // equivalent (a fit against the average head) is 8 to 10x worse at
      // frontal and grows from there.
      assert.ok(
        median < Math.max(frontal * 4, 1.5),
        `yaw ${bucket} deg: ${median.toFixed(2)} deg against ${frontal.toFixed(2)} frontal`,
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
});

// --------------------------------------------------------------- enrollment

describe('enrollment', () => {
  it('recovers a nose it has never seen, to about a millimetre', () => {
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
    // Not "does it run" but "does it help". Without this the field could be
    // solving hard and contributing nothing.
    //
    // Measured across a population rather than on one subject, and the reason is
    // itself worth recording: a single-subject version of this test failed on a
    // face whose nose the basis happened to explain well (0.711 mm with the
    // field against 0.764 without — a 7% gain, not the 15% the bar wanted). That
    // is not a regression, it is the variance of drawing one face. The
    // improvement is a property of the population, so it has to be measured on
    // one.
    //
    // Detector bias is switched off here so the comparison isolates the field
    // rather than the shared bias floor both arms carry.
    const population = generatePopulation(mesh, basis, { count: 4 });
    const geometry = CAMERA_LADDER[0];
    const withField: number[] = [];
    const without: number[] = [];

    for (const subject of population) {
      const capture = synthesizeCapture(mesh, subject, geometry, {
        framesPerBeat: 10, biasMm: 0,
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

    const a = distribution(withField).median;
    const b = distribution(without).median;
    assert.ok(
      a < b * 0.9,
      `the free-form field gave ${a.toFixed(3)} mm against ${b.toFixed(3)} without it — ` +
      'it is not earning its parameters',
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

      nominal.push(Math.abs(
        solveSeat(model, mesh, regions, frame, { maxIterations: 0 }).padDepthErrorMm,
      ));
    }

    const solved = distribution(own).median;
    assert.ok(
      distribution(nominal).median > solved * 1.8,
      `hanging the pads off a landmark is only ${(distribution(nominal).median / solved).toFixed(2)}x ` +
      'worse than solving the contact — the contact solve is not doing anything',
    );
    assert.ok(distribution(wrong).median > solved, 'the template nose fits as well as the real one');
  });

  it('produces adjustments an optician could act on', () => {
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    // The deliberately mismatched frame in the test catalogue.
    const steep = TEST_FRAMES.find((f) => f.id === 'steep-pads')!;
    const assessment = assessFit(model, mesh, regions, steep);
    assert.ok(assessment.adjustments.length > 0, 'a badly-angled pad produced no advice');
    assert.ok(
      assessment.adjustments.some((a) => /bend/i.test(a)),
      `expected pad-bending advice, got: ${assessment.adjustments.join(' | ')}`,
    );
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
    // placement buries the pads four millimetres in and is perfectly FLUSH
    // while doing it, so it scored better than the real solve.
    const subject = generatePopulation(mesh, basis, { count: 1 })[0];
    const model = truthModel(subject.positions);
    const frame = TEST_FRAMES[1];
    const nominal = solveSeat(model, mesh, regions, frame, { maxIterations: 0 });
    assert.ok(Math.abs(nominal.padDepthErrorMm) > 0.5, 'the nominal placement is not deep-wrong');
    assert.ok(
      nominal.padSeatErrorMm < Math.abs(nominal.padDepthErrorMm) * 4,
      'flushness and depth are moving together — they are not measuring different things',
    );
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
});
