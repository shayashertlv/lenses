/**
 * The foundations: manifold arithmetic, the camera model, and every analytic
 * jacobian in the tree against central differences.
 *
 * The jacobian tests are the load-bearing ones. Hand-written derivatives are
 * what make the enrollment bundle fast enough to run while a wearer is still
 * looking at the guide dot, and a wrong one does not crash — it converges
 * slowly to a slightly wrong answer, which is indistinguishable from noisy data.
 * Checking them numerically is what makes writing them by hand a safe decision
 * rather than a brave one.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';

import {
  type Pose,
  eulerYXZ, expSO3, invertSymmetric, ldlt, ldltSolve, logSO3, m3, m3transpose,
  mat3FromEulerYXZ, m3mul, orthonormalize, poseClone, poseIdentity, poseOplus,
  rotationAngleBetween, smoothstep, solveSymmetric, v3, vlen, weightedMedian, mad, percentile,
} from '../src/core/linalg.js';
import { PNP_DEFAULTS, buildCorrespondences, pixelGateScale, refinePnP } from '../src/track/pnp.js';
import {
  dProjDIntrinsics, dProjDModelPoint, dProjDPoint, dProjDPose, intrinsicsFromFov,
  pointAtDepth, project, rayThrough, scaleIntrinsics, verticalFovDeg, type Intrinsics,
} from '../src/core/camera.js';
import { hornRotation, rigidAlign } from '../src/enroll/detector-bias.js';
import { createRng } from '../src/testkit/random.js';
import {
  createFaceModel, deserializeFaceModel, landmarkSurface, noseConfidence, serializeFaceModel,
} from '../src/core/facemodel.js';
import type { ScanRecord } from '../src/enroll/protocol.js';
import { applyScale } from '../src/enroll/scale.js';
import { loadBasis, loadRegions, loadTemplateMesh } from '../src/testkit/fixtures.js';
import { generatePopulation, populationSeedFor } from '../src/testkit/synthetic.js';
import {
  SEAT_DEFAULTS, SKIN, accumulate, energyTerms, nominalPose, solveSeat,
} from '../src/fit/contact.js';
import { emptyClosestPoint, buildMeshDistance } from '../src/core/meshdist.js';
import { parametricFrame } from '../src/fit/frame-asset.js';
import { createDepthBuffer, rasterize } from '../src/core/raster.js';
import {
  createUncertainty, estimateSigma, UNCERTAINTY_DEFAULTS,
} from '../src/detect/uncertainty.js';
import {
  LM, computeVertexNormals, measure, silhouetteStrips, standardRegions,
  trackingRigidity, type SilhouetteStrip,
} from '../src/core/mesh.js';
import { basisJacobian, evaluateBasis } from '../src/core/shape/basis.js';
import {
  applyDisplacement, createDisplacementField, displacementJacobian, refreshNormals,
} from '../src/core/shape/displacement.js';
import { barron, huber, robustScale } from '../src/core/robust.js';
import { occluderBiasedMatrix, poseToGLMatrix, poseToUnflippedMatrix } from '../src/render/convert.js';
import { FACE_TO_CAMERA_FLIP } from '../src/core/camera.js';
import {
  BARRON_ALPHA_HIGH, BARRON_ALPHA_LOW, BARRON_VIS_HI, BARRON_VIS_LO,
  LATCH_DRIFT_MM, LATCH_ENTER_FRAMES, LATCH_FADE_FRAMES, LATCH_VEL_WINDOW,
  TRACKER_DEFAULTS, VF_CAL_MIN_VIS, VIS_CULL_HI, VIS_CULL_LO, createTracker, track,
} from '../src/track/tracker.js';
import { collectDiagnostics } from '../src/app/diagnostics.js';
import { createProtocol } from '../src/enroll/protocol.js';
import {
  ADAPTIVE_NOISE_SCALE_MAX, ADAPTIVE_SIGMA_FLOOR_PX, OneEuro, PoseSmoother,
  ROTATION_SMOOTHING, TRANSLATION_SMOOTHING, noiseScaleFromSigma,
} from '../src/track/smoothing.js';
import { poseRotationFromHeadEuler } from '../src/core/camera.js';

const K: Intrinsics = intrinsicsFromFov(1280, 720, 63);

describe('SO(3)', () => {
  it('exp and log are inverses across the full range', () => {
    const rng = createRng(1);
    for (let i = 0; i < 400; i++) {
      const axis = v3(rng.normal(), rng.normal(), rng.normal());
      const len = vlen(axis);
      if (len < 1e-9) continue;
      // Angles from machine-epsilon-small to just under pi, because both ends
      // have their own branch and both branches have been wrong in this file.
      const angle = rng.next() * (Math.PI - 1e-4);
      const w = v3(axis[0] / len * angle, axis[1] / len * angle, axis[2] / len * angle);
      const R = m3();
      expSO3(R, w);
      const back = v3();
      logSO3(back, R);
      for (let c = 0; c < 3; c++) assert.ok(Math.abs(back[c] - w[c]) < 1e-9, `component ${c}`);
    }
  });

  it('handles the tiny-angle branch without dividing by zero', () => {
    for (const angle of [0, 1e-12, 1e-9, 1e-7, 1e-6]) {
      const R = m3();
      expSO3(R, v3(angle, 0, 0));
      for (const value of R) assert.ok(Number.isFinite(value));
      const back = v3();
      logSO3(back, R);
      assert.ok(Math.abs(back[0] - angle) < 1e-12);
    }
  });

  it('handles rotations near pi', () => {
    // The near-pi branch matters here: a wearer turning to full profile and back
    // passes through large rotations relative to a keyframe.
    for (const angle of [Math.PI - 1e-5, Math.PI - 1e-7]) {
      for (const axis of [v3(1, 0, 0), v3(0, 1, 0), v3(0, 0, 1), v3(0.6, 0.8, 0)]) {
        const w = v3(axis[0] * angle, axis[1] * angle, axis[2] * angle);
        const R = m3();
        expSO3(R, w);
        const back = v3();
        logSO3(back, R);
        const Rback = m3();
        expSO3(Rback, back);
        assert.ok(rotationAngleBetween(R, Rback) < 1e-6);
      }
    }
  });

  it('eulerYXZ inverts mat3FromEulerYXZ', () => {
    const rng = createRng(7);
    for (let i = 0; i < 200; i++) {
      const yaw = rng.range(-1.5, 1.5);
      const pitch = rng.range(-1.0, 1.0);
      const roll = rng.range(-1.0, 1.0);
      const R = m3();
      mat3FromEulerYXZ(R, yaw, pitch, roll);
      const e = eulerYXZ(R);
      assert.ok(Math.abs(e.yaw - yaw) < 1e-9, 'yaw');
      assert.ok(Math.abs(e.pitch - pitch) < 1e-9, 'pitch');
      assert.ok(Math.abs(e.roll - roll) < 1e-9, 'roll');
    }
  });

  it('orthonormalize repairs a drifted rotation', () => {
    const R = m3();
    expSO3(R, v3(0.3, -0.2, 0.7));
    for (let i = 0; i < 9; i++) R[i] += (i % 3 === 0 ? 1 : -1) * 1e-3;
    orthonormalize(R, R);
    const RtR = m3();
    const Rt = Float64Array.of(R[0], R[3], R[6], R[1], R[4], R[7], R[2], R[5], R[8]);
    m3mul(RtR, Rt, R);
    for (let i = 0; i < 9; i++) {
      const want = i % 4 === 0 ? 1 : 0;
      assert.ok(Math.abs(RtR[i] - want) < 1e-12, `R^T R element ${i}`);
    }
  });
});

describe('dense linear algebra', () => {
  it('LDL^T solves a symmetric positive-definite system', () => {
    const rng = createRng(11);
    for (const n of [1, 2, 3, 6, 12]) {
      const A = new Float64Array(n * n);
      // A = M^T M + nI is SPD by construction.
      const M = Float64Array.from({ length: n * n }, () => rng.normal());
      for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
          let s = 0;
          for (let k = 0; k < n; k++) s += M[k * n + i] * M[k * n + j];
          A[i * n + j] = s + (i === j ? n : 0);
        }
      }
      const x = Float64Array.from({ length: n }, () => rng.normal());
      const b = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) b[i] += A[i * n + j] * x[j];
      }
      const work = new Float64Array(A);
      const rhs = new Float64Array(b);
      assert.ok(solveSymmetric(work, n, rhs));
      for (let i = 0; i < n; i++) assert.ok(Math.abs(rhs[i] - x[i]) < 1e-8, `n=${n} x[${i}]`);
    }
  });

  it('LDL^T refuses a non-positive-definite matrix rather than returning nonsense', () => {
    // This is exactly the failure mode that silently disabled the displacement
    // field: a truncated J^T J is not PSD, `ldlt` returned false, and the caller
    // took an early return that produced no visible symptom.
    const A = Float64Array.of(1, 2, 2, 1); // eigenvalues 3 and -1
    assert.equal(ldlt(A, 2), false);
  });

  it('invertSymmetric round-trips', () => {
    const A = Float64Array.of(4, 1, 0, 1, 3, 1, 0, 1, 2);
    const inv = new Float64Array(A);
    assert.ok(invertSymmetric(inv, 3));
    for (let i = 0; i < 3; i++) {
      for (let j = 0; j < 3; j++) {
        let s = 0;
        for (let k = 0; k < 3; k++) s += A[i * 3 + k] * inv[k * 3 + j];
        assert.ok(Math.abs(s - (i === j ? 1 : 0)) < 1e-12);
      }
    }
  });
});

describe('camera model', () => {
  it('project and rayThrough are inverses', () => {
    const rng = createRng(13);
    for (let i = 0; i < 300; i++) {
      const X = v3(rng.range(-200, 200), rng.range(-200, 200), rng.range(200, 900));
      const uv = new Float64Array(2);
      assert.ok(project(uv, K, X));
      const back = v3();
      pointAtDepth(back, K, uv[0], uv[1], X[2]);
      assert.ok(Math.hypot(back[0] - X[0], back[1] - X[1], back[2] - X[2]) < 1e-9);
    }
  });

  it('undistortion inverts distortion', () => {
    const distorted: Intrinsics = { ...K, k1: -0.18 };
    const rng = createRng(17);
    for (let i = 0; i < 200; i++) {
      const X = v3(rng.range(-150, 150), rng.range(-150, 150), rng.range(300, 800));
      const uv = new Float64Array(2);
      assert.ok(project(uv, distorted, X));
      const ray = v3();
      rayThrough(ray, distorted, uv[0], uv[1]);
      // The ray must point at X.
      const l = vlen(X);
      assert.ok(Math.abs(ray[0] - X[0] / l) < 1e-6);
      assert.ok(Math.abs(ray[1] - X[1] / l) < 1e-6);
      assert.ok(Math.abs(ray[2] - X[2] / l) < 1e-6);
    }
  });

  it('field of view round-trips', () => {
    for (const fov of [40, 55, 63, 78]) {
      const k = intrinsicsFromFov(1920, 1080, fov);
      assert.ok(Math.abs(verticalFovDeg(k) - fov) < 1e-9);
    }
  });

  it('carries a solved camera across a mode change without inventing a field of view', () => {
    // A driver changes modes by CROPPING and downscaling, never by squashing,
    // so exactly one axis's field of view survives and it is the one with the
    // larger pixel ratio. `f = k.f * width / k.width` - which this was until
    // it acquired its first caller - is right only when the aspect survives:
    // it hands a 63-degree 1280x720 record a 78.50-degree vertical field of
    // view at 640x480, and costs 115 mm of solved depth on that transfer.
    const hfov = (k: Intrinsics) => (2 * Math.atan(k.width / 2 / k.f) * 180) / Math.PI;
    const k = intrinsicsFromFov(1280, 720, 63);

    // Same aspect: both fields of view survive, and cx/cy stay central.
    const half = scaleIntrinsics(k, 640, 360);
    assert.ok(Math.abs(verticalFovDeg(half) - 63) < 1e-9);
    assert.ok(Math.abs(hfov(half) - hfov(k)) < 1e-9);
    assert.ok(Math.abs(half.cx - 320) < 1e-9 && Math.abs(half.cy - 180) < 1e-9);

    // 16:9 -> 4:3 is a HORIZONTAL crop: the vertical fov survives, the
    // horizontal one shrinks.
    const crop43 = scaleIntrinsics(k, 640, 480);
    assert.ok(Math.abs(verticalFovDeg(crop43) - 63) < 1e-9,
      `a 4:3 mode was given a ${verticalFovDeg(crop43).toFixed(2)} deg vertical fov - `
      + 'the record was stretched, not cropped');
    assert.ok(hfov(crop43) < hfov(k) - 1e-9,
      'a side crop did not narrow the horizontal field of view');
    assert.ok(Math.abs(crop43.cx - 320) < 1e-9 && Math.abs(crop43.cy - 240) < 1e-9,
      'the principal point left the centre of a symmetric crop');

    // 4:3 -> 16:9 is a VERTICAL crop: the horizontal fov survives.
    const k43 = intrinsicsFromFov(640, 480, 63);
    const crop169 = scaleIntrinsics(k43, 1280, 720);
    assert.ok(Math.abs(hfov(crop169) - hfov(k43)) < 1e-9,
      `a 16:9 mode was given a ${hfov(crop169).toFixed(2)} deg horizontal fov against `
      + `${hfov(k43).toFixed(2)} - the record was stretched, not cropped`);
    assert.ok(verticalFovDeg(crop169) < verticalFovDeg(k43) - 1e-9,
      'a top-and-bottom crop did not narrow the vertical field of view');

    // **And the two assertions above cannot both describe one camera.** They
    // are the same transfer in opposite directions, so composing them must be
    // the identity - and it is not. The premise reads the DESTINATION as the
    // crop every time, which for a given sensor is true of at most one of the
    // two directions: under a native-16:9 sensor the truth is 0.667 down and
    // 1.5 up, under a native-4:3 one 0.5 down and 2.0 up, and `max` gives
    // 0.667 and 2.0 - exact in one cell of each column, 33% high in the other.
    //
    // Pinned rather than fixed because `min`, "the smaller mode is a crop of
    // the larger" and its inverse each score two of those four as well. The
    // choice is a bet on the sensor's native aspect, which the record does not
    // carry. See the header and `docs/OPEN-QUESTIONS.md` Q8.
    const roundTrip = scaleIntrinsics(scaleIntrinsics(k, 640, 480), 1280, 720);
    assert.ok(Math.abs(roundTrip.f / k.f - 4 / 3) < 1e-12,
      `720 -> 480 -> 720 multiplied f by ${(roundTrip.f / k.f).toFixed(6)}, not 4/3. If it is now `
      + '1 the transfer has become inverse-consistent and the header, this comment and Q8 all '
      + 'need rewriting; if it is anything else the rule moved and nothing measured it');
    assert.ok(Math.abs(verticalFovDeg(roundTrip) - 49.37) < 0.01,
      `the round trip returned a ${verticalFovDeg(roundTrip).toFixed(2)} deg camera against the `
      + '49.37 measured - the mirror of the 78.50 the rule this one replaced produced one rung down');

    // **And the boundary of the defect, which is the half worth knowing before
    // touching the rule.** All of the disagreement above lives in the
    // aspect-CHANGING rungs. Where the aspect is unchanged `sx === sy`, every
    // candidate rule returns the same number, the transfer is exact, and the
    // round trip IS the identity. So this is not "scaleIntrinsics does not
    // compose" — it is "scaleIntrinsics is a bet exactly when the aspect
    // changes", and `intrinsicsForSource` logs which of the two it just did.
    const sameAspect = scaleIntrinsics(scaleIntrinsics(k, 640, 360), 1280, 720);
    assert.equal(sameAspect.f, k.f,
      `a 16:9 -> 16:9 round trip moved f from ${k.f} to ${sameAspect.f}. It must be exact: the `
      + 'ambiguity this test documents is entirely about aspect CHANGES, and if a same-aspect '
      + 'transfer has stopped composing then the defect is bigger than the header says');
    assert.equal(verticalFovDeg(sameAspect).toFixed(4), verticalFovDeg(k).toFixed(4),
      'a same-aspect round trip changed the field of view');
  });
});

describe('analytic jacobians match central differences', () => {
  const EPS = 1e-6;

  // Every test below that composes a chain rule through `dProjDPoint` sweeps k1
  // as well as the geometry, and the reason is that `k1 = 0` is DEGENERATE: the
  // function takes an early return whose (xn, yn) block is exactly `f/z * I`,
  // off-diagonals exactly zero and diagonals exactly equal. All four of the
  // distorted branch's terms — duxn, duyn, dvxn, dvyn — are dead code from an
  // undistorted test's point of view, so a corruption of that block passes in
  // silence. Measured: injecting a swap of `duxn` and `dvyn` moves the composed
  // result by exactly 0.00e+0 at k1 = 0 and by 2.27e-3 at k1 = -0.15, and is
  // caught by 1 of the 3 tests without this sweep and 3 of 3 with it.
  //
  // The limit that remains, written down because it is permanent: a TRANSPOSE
  // of the 2x2 distortion block is undetectable at ANY k1, because `duyn` and
  // `dvxn` in src/core/camera.ts are assigned the identical expression
  // `k.f * (2 * k.k1 * xn * yn)`. The block is symmetric by construction, so
  // swapping those two is a no-op that no difference test can see. Sweeping k1
  // buys detection of ASYMMETRIC corruptions and nothing more.
  const DISTORTIONS: Intrinsics[] = [K, { ...K, k1: -0.15 }];

  it('d(projection) / d(camera point)', () => {
    const rng = createRng(23);
    for (const k of DISTORTIONS) {
      for (let i = 0; i < 100; i++) {
        const X = v3(rng.range(-120, 120), rng.range(-120, 120), rng.range(300, 800));
        const J = new Float64Array(6);
        dProjDPoint(J, 0, k, X);
        for (let c = 0; c < 3; c++) {
          const plus = new Float64Array(X); plus[c] += EPS;
          const minus = new Float64Array(X); minus[c] -= EPS;
          const a = new Float64Array(2); const b = new Float64Array(2);
          project(a, k, plus); project(b, k, minus);
          for (let r = 0; r < 2; r++) {
            const numeric = (a[r] - b[r]) / (2 * EPS);
            assert.ok(
              Math.abs(numeric - J[r * 3 + c]) < 1e-4 * Math.max(1, Math.abs(numeric)),
              `k1=${k.k1} row ${r} col ${c}: analytic ${J[r * 3 + c]} numeric ${numeric}`,
            );
          }
        }
      }
    }
  });

  it('d(projection) / d(pose increment)', () => {
    // The k1 loop sits OUTSIDE the seed so each pass sees identical geometry and
    // only the distortion differs. The tolerance is unchanged: 2e-3 relative
    // passes at k1 = -0.15 untouched. A step-size sweep puts the true worst
    // relative error over these 60 samples at 3.5e-8 (EPS 1e-4), 5.3e-9 (1e-5),
    // 6.5e-8 (1e-6) and 6.4e-7 (1e-7), at both k1 values — so the bar is four
    // orders above the numerics and is not hiding anything.
    for (const KK of DISTORTIONS) {
      const rng = createRng(29);
      for (let i = 0; i < 60; i++) {
        const pose = poseIdentity();
        expSO3(pose.R, v3(rng.range(-1, 1), rng.range(-1.2, 1.2), rng.range(-0.5, 0.5)));
        pose.t.set([rng.range(-40, 40), rng.range(-40, 40), rng.range(350, 700)]);
        const Xm = v3(rng.range(-70, 70), rng.range(-90, 90), rng.range(-40, 60));

        const rot = v3();
        const cam = v3();
        const apply = (p: typeof pose) => {
          rot[0] = p.R[0] * Xm[0] + p.R[1] * Xm[1] + p.R[2] * Xm[2];
          rot[1] = p.R[3] * Xm[0] + p.R[4] * Xm[1] + p.R[5] * Xm[2];
          rot[2] = p.R[6] * Xm[0] + p.R[7] * Xm[1] + p.R[8] * Xm[2];
          cam[0] = rot[0] + p.t[0]; cam[1] = rot[1] + p.t[1]; cam[2] = rot[2] + p.t[2];
        };
        apply(pose);
        if (!(cam[2] > 50)) continue;
        const J = new Float64Array(12);
        dProjDPose(J, 0, KK, cam, rot);

        for (let c = 0; c < 6; c++) {
          const delta = new Float64Array(6);
          delta[c] = EPS;
          const plus = poseIdentity(); poseOplus(plus, pose, delta, 0);
          delta[c] = -EPS;
          const minus = poseIdentity(); poseOplus(minus, pose, delta, 0);
          const a = new Float64Array(2), b = new Float64Array(2);
          apply(plus); project(a, KK, cam);
          apply(minus); project(b, KK, cam);
          for (let r = 0; r < 2; r++) {
            const numeric = (a[r] - b[r]) / (2 * EPS);
            assert.ok(
              Math.abs(numeric - J[r * 6 + c]) < 2e-3 * Math.max(1, Math.abs(numeric)),
              `k1=${KK.k1} row ${r} col ${c}: analytic ${J[r * 6 + c]} numeric ${numeric}`,
            );
          }
        }
      }
    }
  });

  it('d(projection) / d(model point) through the pose rotation', () => {
    for (const KK of DISTORTIONS) {
      const rng = createRng(31);
      for (let i = 0; i < 80; i++) {
        const R = m3();
        expSO3(R, v3(rng.range(-1, 1), rng.range(-1, 1), rng.range(-1, 1)));
        const t = v3(rng.range(-30, 30), rng.range(-30, 30), rng.range(350, 700));
        const Xm = v3(rng.range(-70, 70), rng.range(-90, 90), rng.range(-40, 60));
        const cam = v3(
          R[0] * Xm[0] + R[1] * Xm[1] + R[2] * Xm[2] + t[0],
          R[3] * Xm[0] + R[4] * Xm[1] + R[5] * Xm[2] + t[1],
          R[6] * Xm[0] + R[7] * Xm[1] + R[8] * Xm[2] + t[2],
        );
        if (!(cam[2] > 50)) continue;
        const J = new Float64Array(6);
        dProjDModelPoint(J, 0, KK, cam, R);
        for (let c = 0; c < 3; c++) {
          const shift = (s: number) => {
            const X = new Float64Array(Xm); X[c] += s;
            const p = v3(
              R[0] * X[0] + R[1] * X[1] + R[2] * X[2] + t[0],
              R[3] * X[0] + R[4] * X[1] + R[5] * X[2] + t[1],
              R[6] * X[0] + R[7] * X[1] + R[8] * X[2] + t[2],
            );
            const uv = new Float64Array(2); project(uv, KK, p); return uv;
          };
          const a = shift(EPS), b = shift(-EPS);
          for (let r = 0; r < 2; r++) {
            const numeric = (a[r] - b[r]) / (2 * EPS);
            assert.ok(
              Math.abs(numeric - J[r * 3 + c]) < 1e-4 * Math.max(1, Math.abs(numeric)),
              `k1=${KK.k1} row ${r} col ${c}: analytic ${J[r * 3 + c]} numeric ${numeric}`,
            );
          }
        }
      }
    }
  });

  it('the hoisted scratch buffers survive one jacobian being taken mid-flight', () => {
    // `dProjDPose` and `dProjDModelPoint` each write a 2x3 point jacobian into a
    // module-level buffer, and the two buffers are deliberately SEPARATE.
    //
    // Calling them one after another cannot see that, and a version of this test
    // that did so was a check that could not fail: each call refills its own
    // buffer completely before reading it, so merging the two into one passes a
    // sequential test perfectly. The hazard is NESTING — the outer call has
    // filled its buffer and not yet consumed it when the inner call runs.
    //
    // Nothing in the tree nests them today, which is exactly why the separation
    // is otherwise unobservable, so the nesting here is constructed: `Prot` is
    // read by `dProjDPose` AFTER `dProjDPoint` has filled the buffer and BEFORE
    // the loop that consumes it, so a getter on it lands in the one window where
    // a shared buffer would corrupt the result.
    const pose = poseIdentity();
    expSO3(pose.R, v3(0.3, -0.7, 0.15));
    pose.t.set([12, -8, 520]);
    const Xm = v3(30, -45, 22);
    const rot = v3(
      pose.R[0] * Xm[0] + pose.R[1] * Xm[1] + pose.R[2] * Xm[2],
      pose.R[3] * Xm[0] + pose.R[4] * Xm[1] + pose.R[5] * Xm[2],
      pose.R[6] * Xm[0] + pose.R[7] * Xm[1] + pose.R[8] * Xm[2],
    );
    const cam = v3(rot[0] + pose.t[0], rot[1] + pose.t[1], rot[2] + pose.t[2]);

    const clean = new Float64Array(12);
    dProjDPose(clean, 0, K, cam, rot);

    // Deliberately different intrinsics AND a different point, so anything
    // shared would be visibly clobbered rather than happen to coincide.
    let nested = 0;
    const interleaved: ArrayLike<number> = {
      length: 3,
      get 0() {
        nested++;
        dProjDModelPoint(
          new Float64Array(6), 0, { ...K, f: K.f * 1.7, k1: -0.2 }, v3(-90, 60, 410), pose.R,
        );
        return rot[0];
      },
      1: rot[1],
      2: rot[2],
    } as unknown as ArrayLike<number>;

    const during = new Float64Array(12);
    dProjDPose(during, 0, K, cam, interleaved);
    assert.equal(nested, 1, 'the nested call never fired — the fixture is not testing anything');
    assert.deepEqual(
      during, clean,
      'a model-point jacobian taken while a pose jacobian was mid-flight changed the ' +
      'pose jacobian — the two scratch buffers have been merged into one',
    );
  });

  it('the skew scratch keeps its diagonal at exactly zero, forever', () => {
    // `skewScratch`'s S[0], S[4], S[8] are written once at construction and are
    // zero for every input. If anyone ever writes a non-zero into them the
    // rotation block of EVERY pose jacobian silently gains a spurious term —
    // and with a non-zero Prot on the previous call there is also residue to
    // leave behind. Calling with Prot = 0 makes the whole skew matrix zero, so
    // both faults show up here as a non-zero rotation block.
    const cam = v3(12, -8, 520);
    dProjDPose(new Float64Array(12), 0, K, cam, v3(1, 2, 3));
    const J2 = new Float64Array(12);
    dProjDPose(J2, 0, K, cam, v3(0, 0, 0));
    // Exactness is the point — do not soften these to a tolerance.
    for (const i of [0, 1, 2, 6, 7, 8]) {
      assert.equal(J2[i], 0, `rotation entry ${i} came back ${J2[i]} for a zero Prot`);
    }
  });

  it('d(vertex) / d(shape coefficient)', () => {
    // `basisJacobian` had no difference test anywhere, while camera.ts's header
    // claimed the net covered "every analytic jacobian in the tree". It is
    // linear, so the numeric answer is exact and the bar can be tight.
    const basis = loadBasis();
    const rng = createRng(59);
    const coeffs = new Float64Array(basis.dim);
    for (let k = 0; k < basis.dim; k++) coeffs[k] = rng.range(-1.5, 1.5);
    const a = new Float64Array(basis.vertexCount * 3);
    const b = new Float64Array(basis.vertexCount * 3);
    const J = new Float64Array(3);
    const H = 1e-4;

    for (let trial = 0; trial < 60; trial++) {
      const v = Math.floor(rng.next() * basis.vertexCount);
      const k = Math.floor(rng.next() * basis.dim);
      basisJacobian(basis, v, k, J, 0);

      const plus = new Float64Array(coeffs); plus[k] += H;
      const minus = new Float64Array(coeffs); minus[k] -= H;
      evaluateBasis(basis, plus, a);
      evaluateBasis(basis, minus, b);
      for (let c = 0; c < 3; c++) {
        const numeric = (a[v * 3 + c] - b[v * 3 + c]) / (2 * H);
        assert.ok(
          Math.abs(numeric - J[c]) < 1e-8 * Math.max(1, Math.abs(numeric)),
          `vertex ${v} mode ${basis.labels[k]} axis ${c}: analytic ${J[c]} numeric ${numeric}`,
        );
      }
    }
  });

  it('d(vertex) / d(free-form field value)', () => {
    // Same gap, for `displacementJacobian`. The weight factor is the part worth
    // differencing: the field's own `applyDisplacement` multiplies by the region
    // feather, so a jacobian that forgot it would be right in the interior of
    // the nose and wrong at exactly the rim where the field is most constrained.
    const mesh = loadTemplateMesh();
    const regions = standardRegions(mesh);
    const field = createDisplacementField(mesh, regions.nose);
    refreshNormals(field, mesh, mesh.positions);

    const rng = createRng(61);
    for (let s = 0; s < field.dim; s++) field.values[s] = rng.range(-1.2, 1.2);

    const J = new Float64Array(3);
    const H = 1e-4;
    let feathered = 0;
    for (let trial = 0; trial < 60; trial++) {
      const slot = Math.floor(rng.next() * field.dim);
      if (field.weight[slot] > 0.05 && field.weight[slot] < 0.95) feathered++;
      displacementJacobian(field, slot, J, 0);

      const v = field.vertices[slot] * 3;
      const shift = (d: number) => {
        const saved = field.values[slot];
        field.values[slot] = saved + d;
        const p = new Float64Array(mesh.positions);
        applyDisplacement(field, p);
        field.values[slot] = saved;
        return p;
      };
      const a = shift(H), b = shift(-H);
      for (let c = 0; c < 3; c++) {
        const numeric = (a[v + c] - b[v + c]) / (2 * H);
        assert.ok(
          Math.abs(numeric - J[c]) < 1e-8 * Math.max(1, Math.abs(numeric)),
          `slot ${slot} axis ${c}: analytic ${J[c]} numeric ${numeric}`,
        );
      }
    }
    // Otherwise the weight factor was never exercised and this only tested the
    // normal. Measured: 11 of the 60 draws land in the feather band.
    assert.ok(feathered >= 3, `only ${feathered} of 60 draws had a partial region weight`);
  });

  it('d(seated point) / d(pose increment), against the increment poseOplus applies', () => {
    // `contact.ts`'s `pointJacobian` is the gradient the whole seat solve is
    // built on, and it had no difference test either. It is module-private, so
    // this instantiates the SHIPPED function out of the compiled build rather
    // than restating its formula here — a test that re-derives the derivative it
    // is checking is a check that cannot fail.
    //
    // What it really pins is the CONVENTION: the increment must be the one
    // `poseOplus` applies (`R <- exp(w) R`, `t <- t + u`, rotation acting on the
    // rotated point alone). Get that wrong and the seat solver steps in a
    // slightly wrong direction, converges anyway, and lands somewhere plausible.
    const source = readFileSync(new URL('../src/fit/contact.js', import.meta.url), 'utf8');
    const start = source.indexOf('function pointJacobian(');
    assert.ok(start >= 0, 'pointJacobian has been renamed or removed from fit/contact');
    let depth = 0, end = start;
    for (let i = source.indexOf('{', start); i < source.length; i++) {
      if (source[i] === '{') depth++;
      else if (source[i] === '}' && --depth === 0) { end = i + 1; break; }
    }
    const pointJacobian = new Function(
      `${source.slice(start, end)}; return pointJacobian;`,
    )() as (J: Float64Array, rot: ArrayLike<number>) => void;

    const rng = createRng(67);
    const J = new Float64Array(18);
    const EPS_P = 1e-6;
    for (let trial = 0; trial < 40; trial++) {
      const pose = poseIdentity();
      expSO3(pose.R, v3(rng.range(-1, 1), rng.range(-1, 1), rng.range(-1, 1)));
      pose.t.set([rng.range(-40, 40), rng.range(-40, 40), rng.range(-40, 40)]);
      const local = v3(rng.range(-25, 25), rng.range(-25, 25), rng.range(-25, 25));

      const rotOf = (p: typeof pose) => v3(
        p.R[0] * local[0] + p.R[1] * local[1] + p.R[2] * local[2],
        p.R[3] * local[0] + p.R[4] * local[1] + p.R[5] * local[2],
        p.R[6] * local[0] + p.R[7] * local[1] + p.R[8] * local[2],
      );
      const worldOf = (p: typeof pose) => {
        const r = rotOf(p);
        return v3(r[0] + p.t[0], r[1] + p.t[1], r[2] + p.t[2]);
      };
      pointJacobian(J, rotOf(pose));

      for (let c = 0; c < 6; c++) {
        const delta = new Float64Array(6);
        delta[c] = EPS_P;
        const plus = poseIdentity(); poseOplus(plus, pose, delta, 0);
        delta[c] = -EPS_P;
        const minus = poseIdentity(); poseOplus(minus, pose, delta, 0);
        const a = worldOf(plus), b = worldOf(minus);
        for (let r = 0; r < 3; r++) {
          const numeric = (a[r] - b[r]) / (2 * EPS_P);
          assert.ok(
            Math.abs(numeric - J[r * 6 + c]) < 1e-4 * Math.max(1, Math.abs(numeric)),
            `row ${r} col ${c}: analytic ${J[r * 6 + c]} numeric ${numeric}`,
          );
        }
      }
    }
  });

  it('d(projection) / d(intrinsics)', () => {
    const rng = createRng(37);
    const mask = { f: true, pp: true, k1: true };
    const dof = 4;
    for (let i = 0; i < 60; i++) {
      const k: Intrinsics = { ...K, k1: rng.range(-0.2, 0.2) };
      const X = v3(rng.range(-120, 120), rng.range(-120, 120), rng.range(300, 800));
      const J = new Float64Array(2 * dof);
      dProjDIntrinsics(J, 0, dof, k, X, mask);
      const names = ['f', 'cx', 'cy', 'k1'] as const;
      for (let c = 0; c < dof; c++) {
        const shift = (s: number): Float64Array => {
          const kk: Intrinsics = { ...k };
          (kk as any)[names[c]] += s;
          const uv = new Float64Array(2); project(uv, kk, X); return uv;
        };
        const a = shift(EPS), b = shift(-EPS);
        for (let r = 0; r < 2; r++) {
          const numeric = (a[r] - b[r]) / (2 * EPS);
          assert.ok(
            Math.abs(numeric - J[r * dof + c]) < 1e-4 * Math.max(1, Math.abs(numeric)),
            `${names[c]} row ${r}: analytic ${J[r * dof + c]} numeric ${numeric}`,
          );
        }
      }
    }
  });
});

describe('rigid alignment', () => {
  it('recovers a known rotation and translation exactly', () => {
    const rng = createRng(41);
    const n = 200;
    const a = new Float64Array(n * 3);
    for (let i = 0; i < n * 3; i++) a[i] = rng.range(-80, 80);

    const R = m3();
    expSO3(R, v3(0.3, -0.9, 0.4));
    const t = v3(12, -30, 55);
    const b = new Float64Array(n * 3);
    for (let i = 0; i < n; i++) {
      const x = a[i * 3], y = a[i * 3 + 1], z = a[i * 3 + 2];
      b[i * 3] = R[0] * x + R[1] * y + R[2] * z + t[0];
      b[i * 3 + 1] = R[3] * x + R[4] * y + R[5] * z + t[1];
      b[i * 3 + 2] = R[6] * x + R[7] * y + R[8] * z + t[2];
    }

    const aligned = rigidAlign(a, b, n);
    for (let i = 0; i < n * 3; i++) {
      assert.ok(Math.abs(aligned[i] - b[i]) < 1e-8, `element ${i}`);
    }
  });

  it('is invariant to the scale of its input — the bug that reported 4000 mm errors', () => {
    // Horn's method takes the eigenvector of a matrix, so scaling the point set
    // cannot change the answer. The polar-Newton version this replaced needed
    // ~20 iterations to shed a 1e6 scale factor and was given 12.
    const rng = createRng(43);
    const n = 120;
    const base = new Float64Array(n * 3);
    for (let i = 0; i < n * 3; i++) base[i] = rng.range(-1, 1);
    const R = m3();
    expSO3(R, v3(0.5, 0.2, -0.7));

    let previous: Float64Array | null = null;
    for (const scale of [1, 100, 10000]) {
      const a = new Float64Array(n * 3);
      for (let i = 0; i < n * 3; i++) a[i] = base[i] * scale;
      const b = new Float64Array(n * 3);
      for (let i = 0; i < n; i++) {
        const x = a[i * 3], y = a[i * 3 + 1], z = a[i * 3 + 2];
        b[i * 3] = R[0] * x + R[1] * y + R[2] * z;
        b[i * 3 + 1] = R[3] * x + R[4] * y + R[5] * z;
        b[i * 3 + 2] = R[6] * x + R[7] * y + R[8] * z;
      }
      const aligned = rigidAlign(a, b, n);
      let worst = 0;
      for (let i = 0; i < n * 3; i++) worst = Math.max(worst, Math.abs(aligned[i] - b[i]));
      // Relative to the scale, the error must stay at machine precision.
      assert.ok(worst / scale < 1e-9, `scale ${scale}: worst ${worst}`);
      previous = aligned;
    }
    assert.ok(previous);
  });

  it('hornRotation returns a proper rotation', () => {
    const rng = createRng(47);
    const S = Float64Array.from({ length: 9 }, () => rng.normal());
    const R = new Float64Array(9);
    hornRotation(R, S);
    const det =
      R[0] * (R[4] * R[8] - R[5] * R[7])
      - R[1] * (R[3] * R[8] - R[5] * R[6])
      + R[2] * (R[3] * R[7] - R[4] * R[6]);
    assert.ok(Math.abs(det - 1) < 1e-9, `det ${det}`);
  });
});

describe('statistics helpers', () => {
  it('weighted median with equal weights reproduces the plain median exactly', () => {
    const rng = createRng(53);
    for (let n = 1; n < 12; n++) {
      const values = Array.from({ length: n }, () => rng.range(-10, 10));
      const weights = new Array(n).fill(1);
      assert.equal(weightedMedian(values, weights), weightedMedian(values));
    }
  });

  it('weighted median cannot be dragged past the mass by a long tail', () => {
    // The property the anchor window depended on in v1 and the agreement law
    // depends on here: however many low-weight samples pile up in a tail, they
    // cannot move the estimate past the weight of the trustworthy ones.
    const values = [10, 10, 10, 10, 1000, 1000, 1000, 1000, 1000];
    const weights = [5, 5, 5, 5, 0.01, 0.01, 0.01, 0.01, 0.01];
    assert.equal(weightedMedian(values, weights), 10);
  });

  it('mad and percentile agree with hand-computed values', () => {
    assert.equal(mad([1, 2, 3, 4, 100]), 1);
    assert.equal(percentile([0, 10], 0.5), 5);
    assert.equal(percentile([0, 10], 0), 0);
    assert.equal(percentile([0, 10], 1), 10);
  });
});

describe('the face model survives being put away and taken out again', () => {
  const model = (scan: ScanRecord | null) => createFaceModel({
    positions: Float64Array.from({ length: 9 }, (_, i) => i * 1.5),
    vertexSigmaMm: new Float64Array(3).fill(0.2),
    shapeCoeffs: Float64Array.of(0.5, -0.25),
    basisName: 'test',
    displacementRmsMm: 0.4, displacementMaxMm: 1.1,
    intrinsics: K,
    intrinsicsSolved: true,
    scale: { source: 'iris', factor: 1.2, sigma: 0.05, note: 'test' },
    landmarkBiasMm: new Float64Array(9),
    quality: { nose: { observations: 31, parallaxRms: 0.41, sigmaMm: 0.15 } },
    pdMm: 61.8, pdSigmaMm: 1.2,
    reprojectionRmsPx: 4.6, framesUsed: 48, solveMs: 2142,
    degraded: false, notes: ['a note'],
    scan,
  });

  it('carries the scan that produced it across a page reload', () => {
    // The model outlives the protocol: it goes to localStorage and comes back on
    // the next load straight into `wear`, while the protocol is rebuilt empty.
    // A real wearer's diagnostics therefore reported "In progress — 0 of 7 done"
    // beside a model built from 48 frames, and the one number needed to
    // calibrate the yaw compression was gone before they could send it.
    const record: ScanRecord = {
      done: ['centre', 'turn-right', 'turn-left'],
      skipped: ['lean-back'],
      achieved: { 'turn-right': 34.2, 'turn-left': 31.8 },
      turnAchievedDeg: 33.0,
      neutral: { yawDeg: 1.2, pitchDeg: -3.4, distanceMm: 520 },
      finished: true,
      summary: '3 of 7 done, 1 skipped',
    };
    const back = deserializeFaceModel(serializeFaceModel(model(record)));
    assert.deepEqual(back.scan, record);
  });

  it('reads a model stored before scan records existed', () => {
    const text = serializeFaceModel(model(null));
    const raw = JSON.parse(text);
    delete raw.scan;
    assert.equal(deserializeFaceModel(JSON.stringify(raw)).scan, null);
  });

  it('round-trips the numbers a fit actually depends on', () => {
    const back = deserializeFaceModel(serializeFaceModel(model(null)));
    assert.equal(back.framesUsed, 48);
    assert.equal(back.quality!.nose!.observations, 31);
    assert.equal(back.scale.factor, 1.2);
    assert.equal(back.pdMm, 61.8);
    assert.equal(back.intrinsicsSolved, true);
    // Derived on the way in rather than stored, so it has to come back too.
    assert.equal(back.vertexCount, 3);
    assert.ok(back.measurements, 'measurements were not recomputed on load');
  });

  it('brings a degraded scan\'s missing residual back as NaN rather than null', () => {
    // `enroll`'s degraded path genuinely produces NaN here — there is no
    // reprojection residual when there were no residuals — and JSON has no NaN,
    // so `JSON.stringify` writes `null`. Without the coercion on the way back in
    // the field holds `null` behind a `number` declaration, and the first
    // `.toFixed()` on it throws: one page reload after the scan that caused it,
    // on the reload path rather than the scan path, which is the hardest place
    // to connect a crash back to its cause.
    const degraded = createFaceModel({
      ...model(null), reprojectionRmsPx: NaN, degraded: true, notes: [],
    });
    const back = deserializeFaceModel(serializeFaceModel(degraded));
    // NOT assert.equal against NaN, which compares equal to nothing.
    assert.ok(
      Number.isNaN(back.reprojectionRmsPx),
      `reprojectionRmsPx came back as ${back.reprojectionRmsPx}`,
    );
    assert.doesNotThrow(() => back.reprojectionRmsPx.toFixed(2));
  });

  it('refuses a model from the previous format, and says it was the format', () => {
    // This string is what a wearer's console shows when their saved scan
    // disappears, so it has to name the cause. "Could not be read" reads as a
    // fault in their file, their camera, or them; a format change is none of
    // those, and version 3 changed what the quality numbers MEAN rather than
    // what they are called — so reading a version 2 model would be worse than
    // refusing it.
    const raw = JSON.parse(serializeFaceModel(model(null)));
    raw.version = 2;
    assert.throws(
      () => deserializeFaceModel(JSON.stringify(raw)),
      (e: Error) => {
        assert.match(e.message, /version 2/);
        assert.match(e.message, /version 3/);
        assert.match(e.message, /scan format changed/);
        return true;
      },
    );
  });

  it('carries no obliquity in the region quality', () => {
    // `obliquityRms` — how far off-axis the camera sat — was kept alongside the
    // parallax on the theory that camera placement predicts reconstruction
    // error. Measured against true nose error it correlates +0.08, against the
    // variance factor's +0.61. It is deleted, and this is a KEY-SET assertion
    // rather than an absence check so that adding any new quality field is a
    // deliberate decision that has to come back and update this line.
    const back = deserializeFaceModel(serializeFaceModel(model(null)));
    assert.deepEqual(
      Object.keys(back.quality.nose).sort(),
      ['observations', 'parallaxRms', 'sigmaMm'],
    );
  });
});

describe('the gauge scales everything with length units, or none of it', () => {
  it('scales the displacement field along with the positions and the poses', () => {
    const positions = Float64Array.of(1, 2, 3, 4, 5, 6);
    const pose = poseIdentity();
    pose.t.set([10, 20, 30]);
    const field = { values: Float64Array.of(0.5, -1.5, 2.0) };

    applyScale(positions, [pose], 2, field);

    assert.deepEqual(Array.from(positions), [2, 4, 6, 8, 10, 12]);
    assert.deepEqual(Array.from(pose.t), [20, 40, 60]);
    // The field was the one thing that did not scale. It is measured along
    // vertex normals in the bundle's own arbitrary units, so leaving it behind
    // meant `displacementRmsMm` carried an `Mm` suffix while holding a pre-scale
    // number — a real wearer's dump read 0.74 for a field that was 1.09 mm.
    assert.deepEqual(Array.from(field.values), [1, -3, 4]);
  });

  it('leaves a unit gauge alone entirely', () => {
    const positions = Float64Array.of(1, 2, 3);
    const field = { values: Float64Array.of(0.5) };
    applyScale(positions, [], 1, field);
    assert.deepEqual(Array.from(positions), [1, 2, 3]);
    assert.deepEqual(Array.from(field.values), [0.5]);
  });
});

describe('the shape basis has the modes its name claims', () => {
  it('ships twenty modes, and every one of them moves the surface', () => {
    const basis = loadBasis();
    // The name is a promise. `browRidge` was identically zero for the whole life
    // of this file — its fade-out ramp was scaled by the chin-to-forehead span
    // rather than the eye-to-forehead one, which put the ramp's lower bound
    // above the highest vertex in the mesh, so every vertex clamped and the
    // field came out `1 - 1`. The zero mode was pruned and the basis shipped 19
    // under the name `anthropometric-20`, silently.
    assert.equal(basis.dim, 20, `basis '${basis.name}' has ${basis.dim} modes`);
    assert.equal(basis.labels.length, 20);

    for (let k = 0; k < basis.dim; k++) {
      let peak = 0;
      const m = basis.modes[k];
      for (let v = 0; v < basis.vertexCount; v++) {
        peak = Math.max(peak, Math.hypot(m[v * 3], m[v * 3 + 1], m[v * 3 + 2]));
      }
      assert.ok(
        peak > 0.05,
        `mode '${basis.labels[k]}' peaks at ${peak.toFixed(4)} mm — it does nothing`,
      );
    }
  });

  it('normalises a coefficient to peak DISPLACEMENT, not to the span it is named after', () => {
    // Finding 27, as the guarantee it actually is. A coefficient of 1.0 means
    // one declared SD of PEAK VERTEX DISPLACEMENT. It does NOT mean one SD of
    // the `FaceMeasurements` span the mode is named after, and the two differ by
    // a factor that depends on how many ends of the span move.
    //
    // Do not add "and every mode lands at 1.0x": twelve of the twenty name no
    // span that `FaceMeasurements` reports, and ten of the remaining eight are
    // two-sided or offset. The two cases below are the two behaviours.
    const basis = loadBasis();
    const mesh = loadTemplateMesh();
    const m0 = measure(mesh.positions);
    const pos = new Float64Array(mesh.vertexCount * 3);
    const coeffs = new Float64Array(basis.dim);
    const spanMoveOf = (label: string, field: keyof typeof m0) => {
      const k = basis.labels.indexOf(label);
      assert.ok(k >= 0, `no ${label} mode`);
      coeffs.fill(0);
      coeffs[k] = 1;
      evaluateBasis(basis, coeffs, pos);
      return Math.abs(measure(pos)[field] - m0[field]);
    };

    // Two-sided. `faceWidth` pushes BOTH temples outward, so the span between
    // them moves by twice the peak displacement. Declared SD is
    // CV.faceWidth (0.05) x templeWidth = 7.743 mm; measured span move 15.486.
    const twoSided = spanMoveOf('faceWidth', 'templeWidth');
    const twoSidedSd = 0.05 * m0.templeWidth;
    assert.ok(
      twoSided / twoSidedSd > 1.8,
      `faceWidth moved templeWidth by ${(twoSided / twoSidedSd).toFixed(2)} declared SD — ` +
      'a two-sided mode must move its span by about 2x, because the normalisation ' +
      'is on peak displacement and both temples travel',
    );

    // One-sided. `cheekProminence` pushes the cheeks out along z only, so the
    // depth it is named after moves by the displacement itself. Declared SD is
    // CV.cheekProminence (0.10) x |nasalRootDepth| = 1.478 mm; measured 1.468.
    const oneSided = spanMoveOf('cheekProminence', 'cheekDepth');
    const oneSidedSd = 0.10 * Math.abs(m0.nasalRootDepth);
    assert.ok(
      Math.abs(oneSided / oneSidedSd - 1) < 0.10,
      `cheekProminence moved cheekDepth by ${(oneSided / oneSidedSd).toFixed(3)} declared SD, ` +
      'expected ~1.0 for a one-sided prominence field',
    );
  });

  it('puts the brow ridge above the eyes and not on the chin', () => {
    // A mode can be non-zero and still be in the wrong place, which is the
    // failure the peak check above would not catch.
    const basis = loadBasis();
    const k = basis.labels.indexOf('browRidge');
    assert.ok(k >= 0, 'no browRidge mode');
    const m = basis.modes[k];

    const eyeY = (basis.mean[LM.EYE_OUTER_R * 3 + 1] + basis.mean[LM.EYE_OUTER_L * 3 + 1]) / 2;
    let above = 0;
    let below = 0;
    for (let v = 0; v < basis.vertexCount; v++) {
      const e = m[v * 3] ** 2 + m[v * 3 + 1] ** 2 + m[v * 3 + 2] ** 2;
      if (basis.mean[v * 3 + 1] >= eyeY) above += e; else below += e;
    }
    assert.ok(
      above > below * 4,
      `browRidge puts ${(100 * below / (above + below)).toFixed(0)}% of its energy ` +
      'below the eye line',
    );
  });
});

describe('the sigma the detector reports has to be in the landmarks own pixels', () => {
  const mesh = loadTemplateMesh();
  const flatInput = (landmarks: Float64Array, pixelScale?: number) => ({
    landmarks,
    mesh,
    positions: mesh.positions,
    intrinsics: intrinsicsFromFov(1280, 720, 63),
    pose: null,
    pixelScale,
  });

  it('scales the floor with the landmarks, or the bundle trusts them four times over', () => {
    // `floorPx` is calibrated at the DETECTION resolution — 640 px on the long
    // side. The app scales the detector's output up to source pixels before
    // calling, so at a 1280-wide capture the landmarks arrive twice as large as
    // the floor describing them. Sigma was half what it should be; a covariance
    // goes as sigma squared, so the bundle believed those landmarks FOUR times
    // more than it should have.
    //
    // Real fingerprint: a wearer's a-posteriori variance factor measured ~3.5,
    // where the synthetic harness reports ~1.6 — and the harness never runs this
    // function, it feeds `sigmaPx` from its own noise model. No test could see
    // it, because until now there was no test of this function at all.
    const detectPx = new Float64Array(mesh.vertexCount * 2).fill(100);
    const sourcePx = Float64Array.from(detectPx, (v) => v * 2);

    const atDetect = estimateSigma(
      createUncertainty(mesh.vertexCount), flatInput(detectPx, 1),
    ).sigmaPx;
    const atSource = estimateSigma(
      createUncertainty(mesh.vertexCount), flatInput(sourcePx, 2),
    ).sigmaPx;

    assert.ok(Math.abs(atDetect[0] - UNCERTAINTY_DEFAULTS.floorPx) < 1e-9,
      `detect-resolution floor came out ${atDetect[0]}`);
    assert.ok(Math.abs(atSource[0] - UNCERTAINTY_DEFAULTS.floorPx * 2) < 1e-9,
      `source-resolution floor came out ${atSource[0]}, expected ${UNCERTAINTY_DEFAULTS.floorPx * 2}`);
  });

  it('defaults to a scale of one, so a caller in detect pixels is unaffected', () => {
    const landmarks = new Float64Array(mesh.vertexCount * 2).fill(100);
    const sigma = estimateSigma(
      createUncertainty(mesh.vertexCount), flatInput(landmarks),
    ).sigmaPx;
    assert.ok(Math.abs(sigma[0] - UNCERTAINTY_DEFAULTS.floorPx) < 1e-9);
  });

  it('reports a landmark the detector could not place as infinitely uncertain', () => {
    const landmarks = new Float64Array(mesh.vertexCount * 2).fill(100);
    landmarks[0] = NaN;
    const sigma = estimateSigma(
      createUncertainty(mesh.vertexCount), flatInput(landmarks, 2),
    ).sigmaPx;
    assert.equal(sigma[0], Infinity);
    assert.ok(Number.isFinite(sigma[1]), 'one missing landmark condemned the rest');
  });
});

describe("Barron's loss family — one shape parameter, checked like every jacobian here", () => {
  const SCALE = 2.5;

  it('drho is the derivative of rho, for every alpha the schedule can reach', () => {
    // The tree's standard for an analytic JACOBIAN: central differences. Not
    // every analytic derivative gets it — `huber`'s rho', the loss all three
    // solvers actually run, has no difference test at all. See README's
    // jacobian-coverage bullet for the standing list.
    // A wrong drho does not crash — it converges slowly to a slightly wrong
    // answer, which is indistinguishable from noisy data.
    for (const alpha of [2, 1.5, 1, 0.5, 0.001, 0, -0.001, -0.5, -1, -2]) {
      const loss = barron(alpha, SCALE);
      for (const s of [1e-4, 0.01, 1, 9, 100, 1e4]) {
        const h = Math.max(1e-7, s * 1e-6);
        const [rPlus] = loss.eval(s + h);
        const [rMinus] = loss.eval(s - h);
        const numeric = (rPlus - rMinus) / (2 * h);
        const [, analytic] = loss.eval(s);
        assert.ok(Math.abs(analytic - numeric) <= 1e-5 * Math.max(1, Math.abs(numeric)),
          `alpha=${alpha} s=${s}: drho ${analytic} against central difference ${numeric}`);
      }
    }
  });

  it('every alpha agrees with Huber near zero — one threshold still means one thing', () => {
    // rho -> s and drho -> 1 as s -> 0 for the whole family. Without this the
    // scale constant would mean something different for each landmark, which
    // is precisely the property the tree refuses to give up.
    for (const alpha of [2, 1, 0, -2]) {
      const [rho, drho] = barron(alpha, SCALE).eval(1e-6);
      assert.ok(Math.abs(rho / 1e-6 - 1) < 1e-4, `alpha=${alpha}: rho/s = ${rho / 1e-6}`);
      assert.ok(Math.abs(drho - 1) < 1e-4, `alpha=${alpha}: drho = ${drho}`);
    }
  });

  it('the sweep through alpha = 0 is smooth, where the naive closed form loses every digit', () => {
    // The schedule runs from 1 to -2 and therefore passes THROUGH zero, where
    // the closed form divides by alpha and pow(x, a/2) - 1 is a difference of
    // nearly equal numbers. expm1 is what makes the general branch survive it;
    // the alpha === 0 branch exists for the division, not for accuracy.
    const at = (a: number) => barron(a, SCALE).eval(30);
    const [rhoZero, drhoZero] = at(0);
    for (const eps of [1e-3, 1e-6, 1e-9, 1e-12]) {
      for (const a of [eps, -eps]) {
        const [rho, drho] = at(a);
        assert.ok(Number.isFinite(rho) && Number.isFinite(drho),
          `alpha=${a} produced a non-finite loss (${rho}, ${drho})`);
        assert.ok(Math.abs(rho - rhoZero) < 0.02 * Math.max(1, Math.abs(rhoZero)),
          `alpha=${a}: rho ${rho} against the analytic limit ${rhoZero}`);
        assert.ok(Math.abs(drho - drhoZero) < 0.02,
          `alpha=${a}: drho ${drho} against the analytic limit ${drhoZero}`);
      }
    }
  });

  it('alpha 1 saturates like Huber and alpha -2 redescends — the whole point of the rank', () => {
    // Influence is drho * r. Huber's is constant past its threshold: a
    // hallucinated landmark pulls just as hard at 100 sigma as at 3. A
    // redescending kernel lets it go. These are the shipped-scale numbers the
    // ledger row quotes.
    const infl = (loss: ReturnType<typeof barron>, r: number) => loss.eval(r * r)[1] * r;
    const h = huber(SCALE);
    const hufl = (r: number) => h.eval(r * r)[1] * r;
    assert.ok(Math.abs(hufl(100) - SCALE) < 1e-9,
      `fixture sanity: Huber's influence at 100 sigma is ${hufl(100)}, expected the constant ${SCALE}`);
    const a1 = barron(1, SCALE);
    const am2 = barron(-2, SCALE);
    // alpha 1 is Huber-like: influence still saturating near the threshold.
    assert.ok(infl(a1, 100) > 0.9 * SCALE,
      `alpha=1 influence at 100 sigma is ${infl(a1, 100)} — it should saturate like Huber, not redescend`);
    // alpha -2 is gone by 100 sigma, and monotonically going.
    assert.ok(infl(am2, 100) < 0.01,
      `alpha=-2 influence at 100 sigma is ${infl(am2, 100)} — it is not redescending`);
    assert.ok(infl(am2, 30) < infl(am2, 10) && infl(am2, 10) < infl(am2, 3),
      'alpha=-2 influence must fall monotonically once past the scale');
    // And the two must genuinely differ, or scheduling between them buys nothing.
    assert.ok(infl(a1, 100) > 100 * infl(am2, 100),
      `the schedule's endpoints are not distinguishable at 100 sigma: ${infl(a1, 100)} vs ${infl(am2, 100)}`);
  });
});

describe('the robust scale estimate', () => {
  const rng = createRng(97);
  const SIGMA = 2.0;
  const N = 20001;
  const signed: number[] = [];
  for (let i = 0; i < N; i++) signed.push(SIGMA * rng.normal());
  const median = (v: number[]) => {
    const s = [...v].sort((a, b) => a - b);
    return s[s.length >> 1];
  };

  it('recovers sigma from SIGNED residuals', () => {
    // The analytic answer is exactly sigma: 1.4826 * MAD is a consistent
    // estimator of the standard deviation for a Gaussian, and the deviation is
    // taken about the sample's own median, which sits near zero only while the
    // residuals still carry their sign. Measured on this 20001-sample draw:
    // 2.022, i.e. 1.011 sigma.
    const got = robustScale(signed);
    assert.ok(
      Math.abs(got / SIGMA - 1) < 0.05,
      `robustScale returned ${got.toFixed(4)} for sigma ${SIGMA}`,
    );
  });

  it('reads 0.59 sigma when handed absolute residuals — which is why the parameter was renamed', () => {
    // The defect, as a measurement. The parameter used to be called
    // `absResiduals`, and a caller who honoured that name got 0.59 sigma back
    // and no complaint from anything. The analytic value is 0.5916 sigma: the
    // median of a half-normal is 0.67449 sigma, the MAD about it is 0.3990
    // sigma, times 1.4826. Measured here: 0.601 sigma.
    const got = robustScale(signed.map(Math.abs)) / SIGMA;
    assert.ok(
      got > 0.55 && got < 0.65,
      `absolute residuals gave ${got.toFixed(4)} sigma, expected ~0.59 — the old ` +
      'parameter name `absResiduals` described an input this function cannot use',
    );
    assert.ok(
      Math.abs(got - 1) > 0.20,
      `absolute residuals gave ${got.toFixed(4)} sigma, which is close enough to 1 that ` +
      'the signed/unsigned distinction no longer costs anything — recheck the estimator',
    );
  });

  it('the one-line shortcut overestimates by 1.75x on the norms this tree actually produces', () => {
    // This test exists to stop `1.4826 * median(|r|)` being reintroduced as an
    // obvious simplification. See the DO NOT paragraph in src/core/robust.ts.
    // The identity holds for a scalar Gaussian, and the only absolute-residual
    // arrays this tree produces are 2-D `Math.hypot` norms — Rayleigh, not
    // half-normal. The analytic factor is 1.4826 * sqrt(2 ln 2) = 1.7456;
    // measured on 20001 draws, 1.742.
    const norms: number[] = [];
    for (let i = 0; i < N; i++) {
      norms.push(Math.hypot(SIGMA * rng.normal(), SIGMA * rng.normal()));
    }
    const shortcut = 1.4826 * median(norms);
    assert.ok(
      Math.abs(shortcut / (1.7456 * SIGMA) - 1) < 0.05,
      `the shortcut gave ${shortcut.toFixed(4)}, expected ~${(1.7456 * SIGMA).toFixed(4)}`,
    );
    assert.ok(
      shortcut > 1.3 * SIGMA,
      `the shortcut gave ${(shortcut / SIGMA).toFixed(3)} sigma — if this ever falls to 1, ` +
      'the residual arrays have stopped being 2-D norms and robust.ts needs rereading',
    );
  });
});

describe('a seat under a posed head is written without a flip', () => {
  /** Column-major 4x4 multiply, C = A*B. Spelled out so no library convention
   *  is assumed — the whole bug this pins was a convention applied twice. */
  const mul = (A: ArrayLike<number>, B: ArrayLike<number>) => {
    const C = new Float64Array(16);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += A[k * 4 + r] * B[c * 4 + k];
        C[c * 4 + r] = s;
      }
    }
    return C;
  };

  // A frontal head 400 mm from the camera. A frontal head IS the flip, per
  // core/camera.ts — that is what FACE_TO_CAMERA_FLIP means.
  const head = { R: Float64Array.from(FACE_TO_CAMERA_FLIP), t: Float64Array.of(0, 0, 400) };
  // And a seat placing the right lens centre, in face-space millimetres.
  const seat = {
    R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
    t: Float64Array.of(-31.74, 20.13, 60.34),
  };

  it('composes the head and the seat to the place the lens actually is', () => {
    // There is no test anywhere else in this tree that composes two nodes'
    // matrices, which is exactly why a double flip could ship on a live path —
    // every `fitFrame` ran it. Elements 12, 13, 14 are the translation.
    const headM = poseToGLMatrix(new Float32Array(16), head);
    const right = mul(headM, poseToUnflippedMatrix(new Float32Array(16), seat));
    assert.ok(Math.abs(right[12] - (-31.74)) < 1e-3, `x ${right[12]}`);
    assert.ok(Math.abs(right[13] - 20.13) < 1e-3, `y ${right[13]}`);
    assert.ok(Math.abs(right[14] - (-339.66)) < 1e-3, `z ${right[14]}`);

    // And the wrong answer is pinned by name rather than merely absent. Applying
    // the flip at both levels puts the lens at (-31.74, -20.13, -460.34):
    // below and behind the head, 127.22 mm from where it belongs.
    const wrong = mul(headM, poseToGLMatrix(new Float32Array(16), seat));
    const off = Math.hypot(
      right[12] - wrong[12], right[13] - wrong[13], right[14] - wrong[14],
    );
    assert.ok(
      off > 127 && off < 128,
      `the double flip moved the lens ${off.toFixed(2)} mm — it is a 127 mm error, ` +
      'not a rounding one',
    );

    // Note for whoever tightens this: with an IDENTITY seat rotation, F.S.F
    // produces the same translation as F.S, so translation alone cannot
    // distinguish those two. Give the seat a non-identity rotation and assert on
    // the rotation block if that distinction ever needs pinning too.
  });

  it('the occluder bias moves the surface toward the camera, and only along the axis', () => {
    // Stage 2's sign pin. The bias convention is the stage-0 instrument's —
    // NEGATIVE toward the camera — and GL's camera looks down -Z, so "toward"
    // is +z: two negations that cancel, which is exactly the kind of algebra
    // that ships inverted. A frontal head at z = -400 (GL) biased by -0.5 mm
    // must land at -399.5: half a millimetre NEARER the camera. If this test
    // fails after an edit to `occluderBiasedMatrix`, the occluder is hiding
    // LESS than the sweep it was tuned by says it should, and the X-ray rate
    // the constant's docstring promises is no longer the measured one.
    const headM = poseToGLMatrix(new Float32Array(16), head);
    const biased = occluderBiasedMatrix(new Float32Array(16), headM, -0.5);
    assert.ok(Math.abs(biased[14] - (headM[14] + 0.5)) < 1e-6,
      `z ${biased[14]} vs head ${headM[14]} — biased occluder is not nearer the camera`);
    assert.equal(biased[12], headM[12], 'bias leaked into x');
    assert.equal(biased[13], headM[13], 'bias leaked into y');
    for (let i = 0; i < 12; i++) {
      assert.equal(biased[i], headM[i], `bias touched rotation cell ${i}`);
    }
    // And zero bias is exactly the head matrix — the knob has an off position.
    const zero = occluderBiasedMatrix(new Float32Array(16), headM, 0);
    for (let i = 0; i < 16; i++) assert.equal(zero[i], headM[i]);
  });

  it('has no vertex-position flip to reintroduce', () => {
    // A source assertion, and it belongs beside the test above because the two
    // are one rule. `positionsToGL` had zero callers and its own docstring
    // pointed the first caller at the occluder and the frame geometry — both
    // face-space, both already in agreement with GL — so it would have been
    // wrong the first time anybody used it.
    const src = readFileSync(new URL('../../src/render/convert.ts', import.meta.url), 'utf8');
    assert.ok(
      !/export function positionsToGL/.test(src),
      'positionsToGL applies a flip to face-space vertices; face space already agrees with GL',
    );
  });
});

// ------------------------------------------------- the tracker's own contract

describe('the tracker rides out a faceless frame, and main.ts depends on it', () => {
  const mesh = loadTemplateMesh();
  const intrinsics = intrinsicsFromFov(1280, 720, 63);

  /** The template as a FaceModel — the geometry the tracker solves against. */
  const templateModel = () => createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
    shapeCoeffs: new Float64Array(0),
    basisName: 'template',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics,
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'template' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: {},
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });

  /** One frame of the template seen head-on at half a metre. */
  const goodFrame = () => {
    const pose = poseIdentity();
    pose.R.set(FACE_TO_CAMERA_FLIP);
    pose.t.set([0, 0, 500]);
    const landmarks = new Float64Array(mesh.vertexCount * 2);
    const cam = v3();
    const uv = new Float64Array(2);
    for (let i = 0; i < mesh.vertexCount; i++) {
      const x = mesh.positions[i * 3], y = mesh.positions[i * 3 + 1], z = mesh.positions[i * 3 + 2];
      cam[0] = pose.R[0] * x + pose.R[1] * y + pose.R[2] * z + pose.t[0];
      cam[1] = pose.R[3] * x + pose.R[4] * y + pose.R[5] * z + pose.t[1];
      cam[2] = pose.R[6] * x + pose.R[7] * y + pose.R[8] * z + pose.t[2];
      project(uv, intrinsics, cam);
      landmarks[i * 2] = uv[0];
      landmarks[i * 2 + 1] = uv[1];
    }
    return { landmarks, sigmaPx: new Float64Array(mesh.vertexCount).fill(1.0) };
  };

  it('holds the pose for exactly holdFrames faceless frames and then drops it', () => {
    // The contract main.ts's `onDetection` now depends on for real. It was
    // asserted at the unit level while production never called `track()` on a
    // faceless frame at all — so weakening `miss()` would silently lose the
    // ride-out again, and no test would say so.
    const state = createTracker(templateModel());
    const good = goodFrame();
    assert.equal(
      track(state, { ...good, intrinsics, dt: 1 / 60 }).tracked, true,
      'the tracker could not solve a noise-free frontal frame',
    );

    // Read the bound from the constant, not from a literal, so the test tracks
    // the tuning rather than pinning it.
    for (let i = 1; i <= TRACKER_DEFAULTS.holdFrames; i++) {
      const r = track(state, { landmarks: null, sigmaPx: null, intrinsics, dt: 1 / 60 });
      assert.equal(r.tracked, true, `faceless frame ${i} was not ridden out`);
      assert.equal(r.held, true, `faceless frame ${i} did not report itself as held`);
      assert.ok(r.pose, `faceless frame ${i} returned no pose to render with`);
      assert.equal(r.rawPose, null, `faceless frame ${i} invented a raw solve`);
    }
    const past = track(state, { landmarks: null, sigmaPx: null, intrinsics, dt: 1 / 60 });
    assert.equal(past.tracked, false, 'the ride-out never ends');
    assert.equal(past.pose, null, 'a dropped frame still handed back a pose');
  });

  it('clears lastRaw once the face has been gone longer than the reset window', () => {
    // main.ts now mirrors `app.lastPose` off exactly this field, so a change to
    // WHEN it clears silently changes which pose `estimateSigma` rasterises
    // visibility against — and a stale warm start is worse than none.
    const state = createTracker(templateModel());
    track(state, { ...goodFrame(), intrinsics, dt: 1 / 60 });
    assert.ok(state.lastRaw, 'a solved frame did not leave a warm start behind');

    // One step past lostSecondsBeforeReset (0.5 s).
    track(state, { landmarks: null, sigmaPx: null, intrinsics, dt: 0.6 });
    assert.equal(
      state.lastRaw, null,
      'the warm start survived longer than the reset window — a velocity carried ' +
      'across half a second describes a movement that is over',
    );
  });
});

describe('angular measurements are reported in degrees, under their own key', () => {
  const mesh = loadTemplateMesh();

  it('keeps sidewallAngle out of the millimetre group and converts it', () => {
    // `sidewallAngle` is RADIANS in `FaceMeasurements`, and it used to be dumped
    // inside `measurementsMm` with everything else: `0.27` under a millimetre
    // heading reads as a quarter-millimetre nose wall rather than the 15.7-degree
    // wedge it is, and that is the kind of unit slip that survives review
    // because the number is plausible read either way.
    const model = createFaceModel({
      positions: new Float64Array(mesh.positions),
      vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
      shapeCoeffs: new Float64Array(0),
      basisName: 'template',
      displacementRmsMm: 0, displacementMaxMm: 0,
      intrinsics: K,
      intrinsicsSolved: true,
      scale: { source: 'card', factor: 1, sigma: 0.001, note: 'template' },
      landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
      quality: {},
      pdMm: null, pdSigmaMm: null,
      reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
    });

    const d = collectDiagnostics({
      phase: 'wear', fps: 60, loopDriver: 'raf', backend: 'test',
      workerAvailable: true, solvedOn: 'worker',
      lock: { brightness: 120, mirrorDelayMs: 30 } as never,
      source: null, protocol: createProtocol(),
      model, seat: null, assessment: null,
      steady: 'on', tracker: null, motionPrior: true, marchOval: true, recentTrack: [],
      recentDetectMs: [],
    }) as {
      runtime: { enrollmentSolvedOn: string };
      model: {
        measurementsMm: Record<string, number>;
        measurementsDeg: Record<string, number>;
      };
    };

    assert.equal('sidewallAngle' in d.model.measurementsMm, false,
      'a radian is being printed in the millimetre column again');
    // The canonical template measures 0.2743 rad = 15.72 deg, rounded to 1 dp.
    assert.ok(
      Math.abs(d.model.measurementsDeg.sidewallAngle - 15.7) < 0.2,
      `sidewallAngle reported as ${d.model.measurementsDeg.sidewallAngle}`,
    );

    // Completeness, so that adding an angular key to core/mesh.ts and forgetting
    // diagnostics.ts's ANGULAR_MEASUREMENTS list is a FAILURE rather than a
    // measurement that quietly vanishes from the dump.
    assert.deepEqual(
      new Set([
        ...Object.keys(d.model.measurementsMm),
        ...Object.keys(d.model.measurementsDeg),
      ]),
      new Set(Object.keys(model.measurements)),
    );

    // While we are here: this key is what tells a reviewer whether a dump's
    // numbers came from the worker or from an inline fallback, which is a
    // different fault from having no worker at all.
    assert.equal(d.runtime.enrollmentSolvedOn, 'worker');
  });
});

describe('the seat gradient matches central differences in the smooth regime', () => {
  // `accumulate` is the Gauss-Newton accumulation the seat steps on and
  // `energyTerms` is the energy the accept test judges those steps by. If the
  // two drift apart the solve starts rejecting steps it believes in — the
  // silhouette bug's shape, in the contact solver — and nothing crashes.
  //
  // The differencing happens in the SMOOTH regime on purpose: the pose is
  // pushed forward until every pad sample is clear of the face (contact === 0)
  // while the hook is engaged (ear > 0) and no rim fouls (clearance === 0), so
  // the active energy is hook + ear + gravity + priors — all C1. In the
  // CONTACT regime a one-sided term's second derivative is discontinuous
  // wherever a sample enters or leaves the surface, and the numeric-vs-
  // Gauss-Newton gap there (up to ~20%) is pre-existing behaviour of the
  // unchanged wall path, not a defect this test should convert into a bar.
  //
  // Both hook stiffnesses because the Q15 knob must not bend the gradient:
  // the wall (0.8) and the derived cantilever (0.11) share every line of
  // accumulation except the stiffness scalar. Both temple reaches because the
  // reach moves which terms are active at a given pose.
  //
  // Measured on this build: worst relative error 3.83e-9, worst near-zero
  // absolute 1.42e-8 — the 1e-6 / 1e-7 bars have about three orders of margin.
  const mesh = loadTemplateMesh();
  const basis = loadBasis();
  const regions = loadRegions();
  const subject = generatePopulation(mesh, basis, { count: 1, seed: populationSeedFor(11) })[0];

  const model = createFaceModel({
    positions: new Float64Array(subject.positions),
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

  // The same distance fields `solveSeat` builds: nose+cheeks for contact, the
  // whole face for clearance.
  const contactVerts = Uint32Array.from(new Set([...regions.nose.members, ...regions.cheeks.members]));
  const distance = buildMeshDistance(mesh, model.positions, contactVerts);
  const clearance = buildMeshDistance(mesh, model.positions);
  const totalOf = (t: ReturnType<typeof energyTerms>) =>
    t.contact + t.gravity + t.ear + t.clearance + t.prior;

  it('at both hook stiffnesses and both temple reaches', () => {
    for (const reach of [95, 90]) {
      const frame = parametricFrame({
        id: 'standard', padSeparationMm: 17, padAngleRad: 0.67, massG: 24,
        templeReachMm: reach,
      });
      const prior = nominalPose(model, frame);
      for (const hook of [undefined, SKIN.hookCantileverNPerMm]) {
        const opt = { ...SEAT_DEFAULTS, hookStiffnessNPerMm: hook };

        // Find the smooth pose: down 3 mm (engages the ear support), then
        // forward in 2 mm steps until every pad is strictly HOVERING. The old
        // condition here was `t.contact === 0`, which the pad-approach spring
        // abolished by design — a hovering pad now carries the weak approach
        // energy, so the smooth regime is "every pad at least half a
        // millimetre clear of the kink", and this test now covers the
        // approach branch's gradient as well as the ear and prior terms.
        const cp = emptyClosestPoint();
        const allPadsHovering = (pp: ReturnType<typeof poseClone>): boolean => {
          const n = frame.padSamples.length / 3;
          for (let i = 0; i < n; i++) {
            const sx = frame.padSamples[i * 3], sy = frame.padSamples[i * 3 + 1],
              sz = frame.padSamples[i * 3 + 2];
            const R = pp.R, t = pp.t;
            const wx = R[0] * sx + R[1] * sy + R[2] * sz + t[0];
            const wy = R[3] * sx + R[4] * sy + R[5] * sz + t[1];
            const wz = R[6] * sx + R[7] * sy + R[8] * sz + t[2];
            if (!distance.query(wx, wy, wz, cp)) return false;
            if (!(cp.distance > 0.5)) return false;
          }
          return true;
        };
        let pose = null;
        for (let fwd = 8; fwd <= 30 && !pose; fwd += 2) {
          const p = poseClone(prior);
          p.t[1] -= 3;
          p.t[2] = prior.t[2] + fwd;
          const t = energyTerms(model, frame, p, distance, clearance, prior, opt);
          if (allPadsHovering(p) && t.ear > 0 && t.clearance === 0) pose = p;
        }
        assert.ok(pose, `no smooth pose found at reach ${reach}, hook ${hook ?? 'wall'}`);

        const H = new Float64Array(36);
        const g = new Float64Array(6);
        accumulate(model, frame, pose!, distance, clearance, prior, opt, H, g);

        const EPS = 1e-6;
        for (let axis = 0; axis < 6; axis++) {
          const dp = new Float64Array(6);
          const pp = poseClone(pose!);
          const pm = poseClone(pose!);
          dp[axis] = EPS;
          poseOplus(pp, pp, dp, 0);
          dp[axis] = -EPS;
          poseOplus(pm, pm, dp, 0);
          const numeric = (
            totalOf(energyTerms(model, frame, pp, distance, clearance, prior, opt))
            - totalOf(energyTerms(model, frame, pm, distance, clearance, prior, opt))
          ) / (2 * EPS);
          const scale = Math.max(Math.abs(g[axis]), Math.abs(numeric));
          const label = `reach ${reach}, hook ${hook ?? 'wall'}, axis ${axis}`;
          if (scale >= 1e-6) {
            assert.ok(
              Math.abs(g[axis] - numeric) / scale < 1e-6,
              `${label}: analytic ${g[axis]} against numeric ${numeric} — the accumulation ` +
              'and the energy have drifted apart, so the accept test is judging steps by ' +
              'a different objective than the one that proposed them',
            );
          } else {
            // The symmetric axes (x-translation, and rotation about the axes a
            // symmetric pose has no torque about) are exact zeros; hold them to
            // an absolute bar instead of a meaningless relative one.
            assert.ok(
              Math.abs(g[axis] - numeric) < 1e-7,
              `${label}: near-zero axis reads analytic ${g[axis]} against numeric ${numeric}`,
            );
          }
        }
      }
    }
  });

  it('and in the CONTACT regime, at the pose solveSeat actually returns', () => {
    // **The sibling above cannot see the branch this one exists for.** It
    // searches for a pose where every pad HOVERS, and `addOneSided` returns at
    // its first line under exactly that condition — measured, 0 of 18 pad
    // samples reach it in all four of that test's configurations. So the
    // penetrating-contact row, which is most of the rows on every seat the app
    // has ever solved, had no gradient coverage at all, and shipped built from
    // the interpolated vertex normal instead of the distance gradient. The
    // suite stayed green through the whole of it.
    //
    // The assertion below therefore leads with a check that the branch is
    // ENTERED. A gradient test that silently stops exercising its subject is
    // the failure this file is meant to be immune to.
    for (const frame of [
      parametricFrame({ id: 'standard', padSeparationMm: 17, padAngleRad: 0.67, massG: 24 }),
      parametricFrame({ id: 'wide-pads', padSeparationMm: 22, padAngleRad: 0.67, massG: 28 }),
    ]) {
      const prior = nominalPose(model, frame);
      const opt = SEAT_DEFAULTS;
      const seat = solveSeat(model, mesh, regions, frame, {});

      // Prove the branch is live at this pose before grading its gradient.
      const cp = emptyClosestPoint();
      let penetrating = 0;
      const n = frame.padSamples.length / 3;
      for (let i = 0; i < n; i++) {
        const sx = frame.padSamples[i * 3], sy = frame.padSamples[i * 3 + 1],
          sz = frame.padSamples[i * 3 + 2];
        const R = seat.pose.R, t = seat.pose.t;
        const wx = R[0] * sx + R[1] * sy + R[2] * sz + t[0];
        const wy = R[3] * sx + R[4] * sy + R[5] * sz + t[1];
        const wz = R[6] * sx + R[7] * sy + R[8] * sz + t[2];
        if (distance.query(wx, wy, wz, cp) && cp.distance < 0) penetrating++;
      }
      assert.ok(
        penetrating > 0,
        `${frame.id}: no pad sample penetrates at the settled pose, so this test is `
        + 'measuring the hovering branch again — the thing it was written to stop',
      );

      const H = new Float64Array(36);
      const g = new Float64Array(6);
      accumulate(model, frame, seat.pose, distance, null, prior, opt, H, g);

      // The angle between the analytic and numeric gradients, which is the
      // quantity that matters: a solver steps along a DIRECTION, and a step
      // built from one vector while the accept test judges by another is how
      // `solveSeat` came to report `converged: true` at a pose where the true
      // gradient was still 0.19-0.27. Measured on this build across 30
      // (subject, frame) pairs: worst 1.98 degrees, median under 0.1.
      const EPS = 1e-5;
      const numeric = new Float64Array(6);
      for (let axis = 0; axis < 6; axis++) {
        const dp = new Float64Array(6);
        const pp = poseClone(seat.pose);
        const pm = poseClone(seat.pose);
        dp[axis] = EPS;
        poseOplus(pp, pp, dp, 0);
        dp[axis] = -EPS;
        poseOplus(pm, pm, dp, 0);
        numeric[axis] = (
          totalOf(energyTerms(model, frame, pp, distance, null, prior, opt))
          - totalOf(energyTerms(model, frame, pm, distance, null, prior, opt))
        ) / (2 * EPS);
      }
      let dot = 0, na = 0, nn = 0;
      for (let a = 0; a < 6; a++) {
        dot += g[a] * numeric[a]; na += g[a] * g[a]; nn += numeric[a] * numeric[a];
      }
      const degrees = (Math.acos(Math.max(-1, Math.min(1, dot / Math.sqrt(na * nn)))) * 180)
        / Math.PI;
      assert.ok(
        degrees < 5,
        `${frame.id}: analytic and numeric gradients differ by ${degrees.toFixed(2)} degrees `
        + `over ${penetrating} penetrating samples — the step and the accept test disagree`,
      );
      // And the magnitudes, so a gradient that points the right way but is
      // scaled wrong cannot pass on direction alone.
      const ratio = Math.sqrt(nn) / Math.sqrt(na);
      assert.ok(
        ratio > 0.95 && ratio < 1.05,
        `${frame.id}: |numeric|/|analytic| = ${ratio.toFixed(4)}`,
      );
    }
  });
});

describe('the pose smoother settles without ringing, at the pose level', () => {
  // **The step-response test in this file drives a bare `OneEuro` on an
  // absolute value — the TRANSLATION data path — and exits its loop at the
  // first 90% crossing.** Overshoot begins at that crossing, so the one
  // instrument pointed at the filter was structurally unable to see it, and
  // rotation shipped as a second-order underdamped loop: 25% overshoot on a
  // step, 7.2% on an ordinary 36 deg/s turn, ringing for about a second, with
  // the whole excursion generated by the filter over an exact input.
  //
  // This runs the WHOLE response through `PoseSmoother`, which is what the app
  // uses, and holds both channels to the same shape.
  const rotY = (deg: number): Float64Array => {
    const r = (deg * Math.PI) / 180, c = Math.cos(r), sn = Math.sin(r);
    return Float64Array.of(c, 0, sn, 0, 1, 0, -sn, 0, c);
  };
  const yawOf = (R: Float64Array): number => (Math.atan2(R[2], R[8]) * 180) / Math.PI;
  const want = (i: number, target: number, ramp: number): number => (ramp === 0
    ? (i < 14 ? 0 : target)
    : Math.min(target, (target * Math.max(0, i - 13)) / ramp));

  it('overshoots a rotation step by no more than 5% and stops ringing inside 0.8 s', () => {
    for (const [target, ramp, label] of [
      [12, 0, 'a 12 degree step'],
      [30, 0, 'a 30 degree step'],
      [12, 10, 'a 12 degree turn at 36 deg/s'],
      [30, 15, 'a 30 degree turn at 60 deg/s'],
    ] as const) {
      const smoother = new PoseSmoother();
      const trace: number[] = [];
      for (let i = 0; i < 70; i++) {
        trace.push(yawOf(smoother.filter(
          { R: rotY(want(i, target, ramp)), t: Float64Array.of(0, 0, 500) }, 1 / 30, 1,
        ).R));
      }
      const peak = Math.max(...trace);
      const overshoot = ((peak - target) / target) * 100;
      assert.ok(
        overshoot <= 5,
        `${label}: overshot ${overshoot.toFixed(1)}% — the rotation loop is ringing again`,
      );
      let settled = 0;
      for (let i = 0; i < trace.length; i++) {
        if (Math.abs(trace[i] - want(i, target, ramp)) > 0.05) settled = i;
      }
      assert.ok(
        settled <= 14 + 24,
        `${label}: still moving at frame ${settled}, 0.8 s after a head that has stopped`,
      );
    }
  });

  it('and translation over the same step never overshoots at all', () => {
    // The control. Translation low-passes an absolute value, so it is first
    // order and monotone by construction; if this ever fails, the two channels
    // have been made to share a structure and the rotation fix went the wrong
    // way.
    const smoother = new PoseSmoother();
    let peak = -Infinity;
    for (let i = 0; i < 40; i++) {
      const z = i < 14 ? 500 : 512;
      peak = Math.max(peak, smoother.filter(
        { R: rotY(0), t: Float64Array.of(0, 0, z) }, 1 / 30, 1,
      ).t[2]);
    }
    assert.ok(peak <= 512 + 1e-9, `translation overshot to ${peak.toFixed(4)} mm`);
  });
});

describe('nose confidence is graded against the scan it actually took', () => {
  // The `observed` term used to divide by a bare 25 while its numerator is a
  // visibility-weighted sum whose ceiling is about half of `framesUsed`. When
  // the keyframe budget dropped 48 -> 24 the term could no longer exceed ~0.5,
  // so no clean scan cleared `SOFT_VERDICT` and half were refused advice with
  // a sentence blaming the wearer.
  const modelWith = (observations: number, framesUsed: number, varianceFactor = 1.0) =>
    createFaceModel({
      positions: new Float64Array(9),
      vertexSigmaMm: new Float64Array(3).fill(0.2),
      shapeCoeffs: new Float64Array(0),
      basisName: 'test',
      displacementRmsMm: 0,
      displacementMaxMm: 0,
      intrinsics: { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
      intrinsicsSolved: true,
      scale: { source: 'card', factor: 1, sigma: 0.001, note: 'test' },
      landmarkBiasMm: new Float64Array(9),
      quality: { nose: { observations, parallaxRms: 0.5, sigmaMm: 0.3 } },
      pdMm: null,
      pdSigmaMm: null,
      reprojectionRmsPx: 0,
      framesUsed,
      solveMs: 0,
      degraded: false,
      notes: [],
      varianceFactor,
    });

  it('is invariant to the keyframe budget — the same scan quality scores the same', () => {
    // The defect in one line: halve the frames AND the observations, which is
    // what a smaller keyframe budget does to the same face, and the answer
    // must not move. Under the bare 25 it halved.
    const many = noseConfidence(modelWith(11.3, 24)).value;
    const few = noseConfidence(modelWith(22.6, 48)).value;
    assert.ok(
      Math.abs(many - few) < 1e-9,
      `the same scan quality scored ${many} at 24 keyframes and ${few} at 48`,
    );
  });

  it('clears on a healthy scan and falls when the nose goes unobserved', () => {
    // Measured across 18 synthetic scans (seed 11): observations/framesUsed
    // runs 0.407-0.518. A scan in that band must read a full 1; scaling the
    // nose visibility down must drop it, monotonically, through the advice
    // gate. Both directions, because a gate that can only pass is not a gate.
    const healthy = noseConfidence(modelWith(0.47 * 24, 24)).value;
    assert.equal(healthy, 1, `a healthy scan scored ${healthy}`);
    const half = noseConfidence(modelWith(0.47 * 24 * 0.5, 24)).value;
    const quarter = noseConfidence(modelWith(0.47 * 24 * 0.25, 24)).value;
    assert.ok(half < healthy && quarter < half, `not monotone: ${healthy} ${half} ${quarter}`);
    // 0.45 was the advice gate before the optician layer was removed; it
    // survives as the bar because it is the confidence at which this project
    // stopped trusting a nose, and that judgement did not change with the
    // prose. The confidence now feeds `scoreOf`, which shrinks a low-confidence
    // criterion toward neutral rather than letting it condemn a frame.
    assert.ok(quarter < 0.45, `a quarter-visible nose scored ${quarter}`);
  });

  it('names the term that binds, not the first one to trip', () => {
    // The phone-lap case: `observed` and `agreement` are both low and
    // `agreement` is lower. The old form tested in a fixed order and blamed
    // the frame count, so the wearer re-scanned — which cannot help, because
    // what was wrong was the camera.
    // Both low, agreement lower: observed = 4.32/(0.40*24) = 0.45, agreement
    // = 1.9/5.43 = 0.35. The first draft of this test set observations high
    // enough that `observed` CLAMPED to 1, so only one term was ever below
    // the threshold and the two orderings could not disagree — a test for an
    // ordering bug that could not see the ordering. It survived its own
    // sabotage, which is how that was found.
    const both = noseConfidence(modelWith(4.32, 24, 5.43));
    assert.match(
      both.reason, /noisier than the detector reported/,
      `named "${both.reason}" when the variance factor was the binding term`,
    );
    const sparse = noseConfidence(modelWith(0.10 * 24, 24, 1.0));
    assert.match(
      sparse.reason, /visible in too few frames/,
      `named "${sparse.reason}" when the observation count was the binding term`,
    );
  });
});

// ------------------------------------------- uncertainty-adaptive smoothing

/** The template as the tracker's FaceModel, for the smoothing tests below. */
const smoothingTestModel = () => {
  const mesh = loadTemplateMesh();
  return createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.2),
    shapeCoeffs: new Float64Array(0),
    basisName: 'template',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: K,
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'template' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: {},
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
};

/** Template landmarks at a pose, with seeded pixel noise. */
const noisyLandmarksAt = (
  yawDeg: number, drift: number, rng: ReturnType<typeof createRng>, noisePx: number,
): Float64Array => {
  const mesh = loadTemplateMesh();
  const pose = poseIdentity();
  poseRotationFromHeadEuler(pose.R, (yawDeg * Math.PI) / 180, 0, 0);
  pose.t.set([drift, 0, 500]);
  const landmarks = new Float64Array(mesh.vertexCount * 2);
  const cam = v3();
  const uv = new Float64Array(2);
  for (let i = 0; i < mesh.vertexCount; i++) {
    const x = mesh.positions[i * 3], y = mesh.positions[i * 3 + 1], z = mesh.positions[i * 3 + 2];
    cam[0] = pose.R[0] * x + pose.R[1] * y + pose.R[2] * z + pose.t[0];
    cam[1] = pose.R[3] * x + pose.R[4] * y + pose.R[5] * z + pose.t[1];
    cam[2] = pose.R[6] * x + pose.R[7] * y + pose.R[8] * z + pose.t[2];
    project(uv, K, cam);
    landmarks[i * 2] = uv[0] + rng.normal() * noisePx;
    landmarks[i * 2 + 1] = uv[1] + rng.normal() * noisePx;
  }
  return landmarks;
};

describe('adaptive smoothing leaves smooth:false and smooth:true bit-identical', () => {
  const mesh = loadTemplateMesh();

  /** A tracked sequence with real motion and noise, so the filter has work. */
  const sequence = (() => {
    const rng = createRng(0xadaf7);
    const frames: Float64Array[] = [];
    for (let f = 0; f < 40; f++) frames.push(noisyLandmarksAt((20 * f) / 40, f * 0.2, rng, 0.7));
    return frames;
  })();

  it('smooth:false emits the raw solve, bit for bit, and reports noiseScale 1', () => {
    const state = createTracker(smoothingTestModel(), { smooth: false });
    for (const landmarks of sequence) {
      const r = track(state, {
        landmarks, sigmaPx: new Float64Array(mesh.vertexCount).fill(1.0), intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked && r.pose && r.rawPose, 'the fixture sequence lost tracking');
      for (let k = 0; k < 9; k++) assert.equal(r.pose!.R[k], r.rawPose!.R[k], `R[${k}]`);
      for (let k = 0; k < 3; k++) assert.equal(r.pose!.t[k], r.rawPose!.t[k], `t[${k}]`);
      assert.equal(r.noiseScale, 1);
    }
  });

  it('smooth:true is exactly the default PoseSmoother over the raw solves', () => {
    // The raw solve stream is independent of smoothing — the warm start is the
    // RAW pose — so the pre-adaptive `true` behaviour is reproducible from a
    // `false` run: the default PoseSmoother applied per frame with no noise
    // scale. Bitwise inequality here means the `true` path changed.
    const rawState = createTracker(smoothingTestModel(), { smooth: false });
    const trueState = createTracker(smoothingTestModel(), { smooth: true });
    const reference = new PoseSmoother();
    for (const landmarks of sequence) {
      const sigmaPx = new Float64Array(mesh.vertexCount).fill(1.0);
      const raw = track(rawState, { landmarks, sigmaPx, intrinsics: K, dt: 1 / 30 });
      const smoothed = track(trueState, { landmarks, sigmaPx, intrinsics: K, dt: 1 / 30 });
      const want = reference.filter(raw.rawPose!, 1 / 30);
      for (let k = 0; k < 9; k++) assert.equal(smoothed.pose!.R[k], want.R[k], `R[${k}]`);
      for (let k = 0; k < 3; k++) assert.equal(smoothed.pose!.t[k], want.t[k], `t[${k}]`);
      assert.equal(smoothed.noiseScale, 1);
    }
  });

  it('the OneEuro arithmetic is unchanged: golden values', () => {
    // A pure-integer-arithmetic input sequence, so the inputs are exact
    // doubles on any engine. Equality is EXACT: a formula tweak that moves
    // the fifteenth digit is still a formula tweak. First captured from the
    // tree at the commit before `noiseScale` existed; RECAPTURED 2026-08-23
    // when derivativeCutoffHz moved 1 -> 5 (a measured SETTINGS change — the
    // sweep is on the ledger row — under the identical formula: the old and
    // new goldens were produced by the same filter code, so the recapture
    // keeps pinning formula drift while the settings history lives in git).
    // (The filter's internals use Math.exp; if this ever fails by a uniform
    // last-digit drift after an engine upgrade, that is where to look.)
    const seq: number[] = [];
    for (let k = 0; k < 48; k++) seq.push(((k * 7919) % 101) / 10 - 5);
    const golden: Record<string, number[]> = {
      translation: [-5, -3.0929322910285544, 0.3008970371218429, -0.9802963091080481, -0.08708539605352572, -2.345070724348036, -1.7503480279836228, 0.8700301717798395, -0.6096343673390037, 0.21816985625429863, -2.0608739733105037, -1.4715741002612768, 1.1474015576216174, -0.3202330310419428, 0.5068234958867954, -1.762204317566792, -1.1760136983041292, 1.4392796821823015, -0.02036596381945177, 0.8029850573538162, -1.459127544273738, -0.8772960477870159, 1.7329372186554233, 0.28053334171094013, 1.0998350582000125, -1.1557107504846824, -0.578324054430927, 2.0267484935637525, 0.5814886127327339, 1.396735321401895, -0.8522947935819971, -0.2793307242479339, 2.3205915042049705, 0.8824295381270701, 1.6936408747884673, -0.548903015248281, 0.019668055463982648, -2.38764507536434, -1.8576678047659985, 0.7089224290119742, -0.7238013967846335, 0.08224786092821479, -2.1494455222295814, -1.5857215519823595, 1.0035572962200316, -0.4169688819735031, 0.3852169467438834, -1.841517483860577],
      rotation: [-5, -0.9677522832894248, 3.15334652144084, -2.666700906831614, 1.1980426576633523, -4.614588106040046, -0.7500764090033729, 3.4414963866886956, -2.3861653090313077, 1.484819725628216, -4.31657034153054, -0.4580684946926654, 3.740680531986702, -2.085764685874869, 1.7830795413622584, -4.016170587757827, -0.16080525406609603, 4.040292169084718, -1.7848830447181703, 2.0816819325987455, -3.7157040557799164, 0.13655320743616128, 4.339913759132372, -1.4839814693352897, 2.380273074710183, -3.4152345994878672, 0.4338507527120945, 4.639533295723451, -1.1830728906701644, 2.6788424041636634, -3.114764038244972, 0.7310805772593851, 4.939150395672902, -0.8821576340306052, 2.9773891535709134, -2.814292445428696, 1.0282400571830608, -4.719936777661973, -0.8906759411165921, 3.337423141437403, -2.4821110038421454, 1.3743242990658056, -4.413808116847096, -0.5762723095951903, 3.63819560761774, -2.179985612978673, 1.6738050485018316, -4.113169952031287],
    };
    for (const [name, settings] of [
      ['translation', TRANSLATION_SMOOTHING], ['rotation', ROTATION_SMOOTHING],
    ] as const) {
      const filter = new OneEuro(settings);
      seq.forEach((x, i) => {
        assert.equal(filter.filter(x, 1 / 30), golden[name][i], `${name}[${i}]`);
      });
    }
  });

  it('the speed estimate opens fast enough — the breakout half of the delay', () => {
    // The wearer localized the tracking delay to the first instant after
    // stillness; half of that instant was the 1 Hz derivative cutoff taking
    // four frames to notice a step. At the shipped tuning a clean step must
    // reach 90% within three frames on both channels (measured: two).
    for (const [name, settings, step] of [
      ['translation', TRANSLATION_SMOOTHING, 15],
      ['rotation', ROTATION_SMOOTHING, 0.175],
    ] as const) {
      const filter = new OneEuro(settings);
      for (let i = 0; i < 30; i++) filter.filter(0, 1 / 30);
      let frames = 0;
      let y = 0;
      while (frames < 10 && y < 0.9 * step) { y = filter.filter(step, 1 / 30); frames++; }
      assert.ok(frames <= 3,
        `${name} took ${frames} frames to reach 90% of a step — the onset lag is back`);
    }
  });
});

describe('adaptive smoothing rides the sigma stream', () => {
  const mesh = loadTemplateMesh();

  it('smooths MORE when the claimed sigma is higher — monotone over two levels', () => {
    // One landmark stream (a held pose, pure detector noise), three runs that
    // differ only in the sigma the tracker is TOLD. The claimed levels are
    // both loose enough that the PnP inlier gate admits every landmark, so the
    // raw solves are identical across runs and any jitter difference is the
    // filter's. Higher claimed sigma must mean a stiller bridge.
    const frames: Float64Array[] = [];
    const rng = createRng(0xadaf8);
    for (let f = 0; f < 50; f++) frames.push(noisyLandmarksAt(10, 0, rng, 0.7));

    const bridgeJitter = (smooth: boolean | 'adaptive', sigmaValue: number): number => {
      const model = smoothingTestModel();
      const state = createTracker(model, { smooth });
      const deltas: number[] = [];
      let prev: Float64Array | null = null;
      for (const landmarks of frames) {
        const r = track(state, {
          landmarks, sigmaPx: new Float64Array(mesh.vertexCount).fill(sigmaValue),
          intrinsics: K, dt: 1 / 30,
        });
        assert.ok(r.pose, 'the held fixture lost tracking');
        const p = r.pose!;
        const o = LM.NOSE_BRIDGE * 3;
        const x = model.positions[o], y = model.positions[o + 1], z = model.positions[o + 2];
        const b = Float64Array.of(
          p.R[0] * x + p.R[1] * y + p.R[2] * z + p.t[0],
          p.R[3] * x + p.R[4] * y + p.R[5] * z + p.t[1],
          p.R[6] * x + p.R[7] * y + p.R[8] * z + p.t[2],
        );
        if (prev) deltas.push(Math.hypot(b[0] - prev[0], b[1] - prev[1], b[2] - prev[2]));
        prev = b;
      }
      return percentile(deltas, 0.5);
    };

    const fixed = bridgeJitter(true, 4.2);          // sigma-blind
    const atScale2 = bridgeJitter('adaptive', 1.4); // noise scale 2
    const atScale6 = bridgeJitter('adaptive', 4.2); // noise scale 6
    assert.ok(
      atScale6 < atScale2,
      `sigma 4.2 px jittered ${atScale6.toFixed(4)} mm against sigma 1.4 px's ` +
      `${atScale2.toFixed(4)} — the filter is not riding the sigma stream`,
    );
    assert.ok(
      atScale2 < fixed,
      `adaptive at noise scale 2 (${atScale2.toFixed(4)} mm) did not smooth more ` +
      `than the fixed tuning (${fixed.toFixed(4)} mm)`,
    );
  });

  it('reports the scale it ran at, and caps it', () => {
    // The cap, hit from both sides, plus the neutral cases. Exact equality is
    // right here: the clamp arms return the constant or 1 itself.
    const fill = (v: number) => new Float64Array(200).fill(v);
    assert.equal(noiseScaleFromSigma(fill(ADAPTIVE_SIGMA_FLOOR_PX * 100)), ADAPTIVE_NOISE_SCALE_MAX);
    assert.equal(noiseScaleFromSigma(fill(ADAPTIVE_SIGMA_FLOOR_PX * 5000)), ADAPTIVE_NOISE_SCALE_MAX);
    // Below the floor is a calibration accident, not a licence to chase noise.
    assert.equal(noiseScaleFromSigma(fill(ADAPTIVE_SIGMA_FLOOR_PX / 10)), 1);
    // Absent landmarks (sigma Infinity) are ignored, not averaged in.
    assert.equal(noiseScaleFromSigma(Float64Array.of(Infinity, 0.7, Infinity, 0.7)), 1);
    // No finite sigma at all: neutral, never NaN.
    assert.equal(noiseScaleFromSigma(fill(Infinity)), 1);

    // And the tracker reports the scale on its result: capped under a
    // pathological stream, ~1 under a clean one.
    const state = createTracker(smoothingTestModel(), { smooth: 'adaptive' });
    const rng = createRng(0xadaf9);
    const clean = track(state, {
      landmarks: noisyLandmarksAt(0, 0, rng, 0.7),
      sigmaPx: new Float64Array(mesh.vertexCount).fill(ADAPTIVE_SIGMA_FLOOR_PX),
      intrinsics: K, dt: 1 / 30,
    });
    assert.equal(clean.noiseScale, 1);
    const noisy = track(state, {
      landmarks: noisyLandmarksAt(0, 0, rng, 0.7),
      sigmaPx: new Float64Array(mesh.vertexCount).fill(11),   // finite, under maxSigmaPx
      intrinsics: K, dt: 1 / 30,
    });
    assert.equal(noisy.noiseScale, ADAPTIVE_NOISE_SCALE_MAX);
  });
});

describe("the stillness latch v2 — velocity-gated rest, crossfaded exits", () => {
  // A synthetic tracking session against the template: a projected frontal
  // face plus per-frame pixel jitter, then the three motions that killed
  // latch v1 on the first real wearer's face: a genuine step turn (must
  // release and land on the live pose), a slow deliberate slide (must NOT
  // latch — v1's displacement band read its sub-millimetre frame steps as
  // rest and stuttered), and sub-velocity creep (must re-anchor as a glide,
  // never as v1's 2.2 mm pop).
  const mesh = loadTemplateMesh();
  const model = createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.3),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
  const K = { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };

  const frameAt = (yawDeg: number, txMm: number, jitterPx: number, seed: number) => {
    const th = (yawDeg * Math.PI) / 180;
    const c = Math.cos(th), sn = Math.sin(th);
    const pose = {
      R: Float64Array.of(c, 0, sn, 0, -1, 0, sn, 0, -c),
      t: Float64Array.of(txMm, 0, 520),
    };
    const landmarks = new Float64Array(mesh.vertexCount * 2);
    let st = seed >>> 0 || 1;
    const rnd = () => {
      st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0;
      return st / 4294967296 - 0.5;
    };
    for (let v = 0; v < mesh.vertexCount; v++) {
      const X = mesh.positions[v * 3], Y = mesh.positions[v * 3 + 1], Z = mesh.positions[v * 3 + 2];
      const cx = pose.R[0] * X + pose.R[1] * Y + pose.R[2] * Z + pose.t[0];
      const cy = pose.R[3] * X + pose.R[4] * Y + pose.R[5] * Z + pose.t[1];
      const cz = pose.R[6] * X + pose.R[7] * Y + pose.R[8] * Z + pose.t[2];
      landmarks[v * 2] = K.cx + (K.f * cx) / cz + rnd() * 2 * jitterPx;
      landmarks[v * 2 + 1] = K.cy + (K.f * cy) / cz + rnd() * 2 * jitterPx;
    }
    return landmarks;
  };
  const sigma = new Float64Array(mesh.vertexCount).fill(1.4);
  // The earliest a v2 latch can engage: the velocity window must fill
  // (LATCH_VEL_WINDOW+1 raw poses) and then stay quiet for the enter count.
  const SETTLE = LATCH_VEL_WINDOW + 1 + LATCH_ENTER_FRAMES + 1;

  it('the basin audit keeps its amortised rate across reacquisitions', () => {
    // `TrackerState.framesTracked` is SESSION-cumulative: incremented once, at
    // one site, and cleared by nothing — not by `miss()`'s reset block and not
    // by `adoptAuditPose`. Its comment said "frames since the last full
    // acquisition", which is false, and the obvious repair — resetting the
    // counter to match the comment — is BACKWARDS.
    //
    // Measured both ways on the same sessions: a per-acquisition counter never
    // reaches `basinAuditInterval` in a session that reacquires more often than
    // the period, so the audit stops running exactly where a wrong basin is most
    // likely. 13 audits over 428 tracked frames becomes 1.
    //
    // Nothing in this tree referenced the field before today — no test, no
    // report, no doc — which is how a comment and its code drifted apart with no
    // gate noticing. So what is pinned is the RATE, which is the property the
    // audit option's own docstring claims, and neither semantics can now drift
    // in silence.
    const state = createTracker(model, { smooth: true });
    let tracked = 0;
    for (let f = 0; f < 900; f++) {
      // 20 dark frames in every 37: long enough to pass the 0.5 s
      // `lostSecondsBeforeReset` and force a genuine cold reacquisition.
      const dark = f % 37 >= 17;
      const r = track(state, dark
        ? { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 }
        : { landmarks: frameAt(0, 0, 0.8, 500 + f), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      if (r.tracked && r.rawPose) tracked++;
    }
    // The precondition, so this cannot pass by never losing the face.
    assert.ok(state.acquisitions > 10,
      `only ${state.acquisitions} acquisitions — the fixture never lost the face, so it `
      + 'cannot tell a cumulative counter from a per-acquisition one');
    assert.ok(
      state.basinAuditsRun >= tracked / (2 * TRACKER_DEFAULTS.basinAuditInterval),
      `${state.basinAuditsRun} audits over ${tracked} tracked frames against a bar of `
      + `${(tracked / (2 * TRACKER_DEFAULTS.basinAuditInterval)).toFixed(1)} — the audit `
      + 'cadence is counting something that resets',
    );
  });

  it('freezes exactly at rest once the velocity window says rest', () => {
    const state = createTracker(model, { smooth: 'locked' });
    const poses: Float64Array[] = [];
    let lastLatched = false;
    let latchedSeen = 0;
    for (let i = 0; i < 30; i++) {
      const r = track(state, { landmarks: frameAt(0, 0, 0.8, 100 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      assert.ok(r.tracked, `frame ${i} lost: ${r.reason}`);
      // The readout's NaN contract: no velocity until the window fills, a
      // number every frame after. The diagnostics instrument rides on this.
      if (i < LATCH_VEL_WINDOW) {
        assert.ok(Number.isNaN(r.velMmS), `frame ${i} reported a velocity before the window filled`);
      } else {
        assert.ok(Number.isFinite(r.velMmS) && Number.isFinite(r.velDegS),
          `frame ${i} lost the velocity readout after the window filled`);
      }
      poses.push(Float64Array.from(r.pose!.t));
      if (r.latched) latchedSeen++;
      lastLatched = r.latched;
    }
    // The session-cumulative held-frames counter must agree with what the
    // results reported — it is the number that survives the diagnostics
    // ring's 10 s horizon, so it may not drift from the truth it summarises.
    assert.equal(state.latchedFrames, latchedSeen,
      `latchedFrames says ${state.latchedFrames}, the results said ${latchedSeen}`);
    assert.ok(lastLatched, 'a second of genuine rest and the latch never engaged');
    // After the settle window, emitted poses must be BIT-IDENTICAL.
    const ref = poses[SETTLE];
    let frozen = 0;
    for (let i = SETTLE; i < 30; i++) {
      if (poses[i][0] === ref[0] && poses[i][1] === ref[1] && poses[i][2] === ref[2]) frozen++;
    }
    assert.ok(frozen >= 30 - SETTLE - 2,
      `only ${frozen} resting frames were bit-frozen — the latch is not latching`);
    assert.ok(state.latchEngages >= 1, 'the engage counter missed its own event');
  });

  it('releases on a real turn through the crossfade and lands on the live pose', () => {
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 20; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 100 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched after 20 resting frames');

    // A 12-degree step turn. The release must be immediate but the exit must
    // be a RAMP: v1 cut straight to raw and the accumulated innovation landed
    // as a visible snap — the "choppy" half of the wearer's report.
    const rs: ReturnType<typeof track>[] = [];
    for (let k = 0; k <= LATCH_FADE_FRAMES + 1; k++) {
      const r = track(state, { landmarks: frameAt(12, 0, 0.8, 500 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      assert.ok(r.tracked, `turn frame ${k} refused: ${r.reason}`);
      rs.push(r);
    }
    assert.ok(!rs[0].latched, 'the latch held through a 12-degree turn');
    assert.ok(rs[0].fading, 'the release was a cut, not a crossfade');
    // euler is in RADIANS — the first draft of this assertion compared it
    // against a degree-sized bar and concluded the latch was stuck while the
    // tracker was following a 12-degree turn flawlessly at 0.65 px of rms.
    const yawOf = (r: (typeof rs)[number]) => Math.abs((r.euler!.yaw * 180) / Math.PI);
    assert.ok(yawOf(rs[LATCH_FADE_FRAMES + 1]) > 8,
      `after the fade the tracker reads ${yawOf(rs[LATCH_FADE_FRAMES + 1]).toFixed(1)} deg ` +
      'of a 12 deg turn — the crossfade never handed over to the live pose');
    // The release frame pays out only the first fade fraction. v1 emitted the
    // whole accumulated innovation right here — anchor to raw in one frame —
    // and that cut was the "choppy" half of the wearer's report. (A snap turn
    // may still move fast on LATER frames: that is the live One Euro doing
    // its job, and the slide/creep tests bound the steps where motion is
    // actually slow.)
    assert.ok(yawOf(rs[0]) < 4,
      `the release frame emitted ${yawOf(rs[0]).toFixed(1)} deg of a 12 deg innovation — ` +
      'the exit is a cut again, not a crossfade');
    // A 12-degree step reads ~36 deg/s in the window — this release belongs
    // to the VELOCITY exit, not the drift guard, and the counters must say
    // so: a build whose velocity release went dead would still pass every
    // motion test through the drift guard alone, with two ledger rows
    // (LATCH_EXIT_VEL_*) governing nothing.
    assert.equal(state.latchReleases, 1,
      'the velocity release never fired on a fast turn — the exit thresholds are dead code');
    assert.equal(state.latchReanchors, 0,
      'the drift guard claimed a release the velocity exit owns');
  });

  it("slow deliberate motion must NOT latch — the 'stuck' defect, pinned", () => {
    // 25 mm/s is a slow, deliberate slide: 0.83 mm per frame, which v1's
    // 1.2 mm displacement enter band read as rest. v1 then froze, accumulated
    // 2.2 mm of innovation, snapped, re-froze — the stutter the wearer
    // reported. v2's velocity gate reads 25 mm/s against an 8.5 mm/s enter
    // threshold and must keep the latch out for the whole slide.
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 20; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 900 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched at rest before the slide');
    let prev: Float64Array | null = null;
    let maxStep = 0;
    let latchedMidMotion = 0;
    let final: Float64Array | null = null;
    for (let k = 1; k <= 60; k++) {
      const r = track(state, {
        landmarks: frameAt(0, (25 * k) / 30, 0.8, 2000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked, `slide frame ${k} refused: ${r.reason}`);
      const t = Float64Array.from(r.pose!.t);
      // The breakout transient (release + fade) is allowed; a re-engage after
      // it is the defect.
      if (k > 12 && r.latched) latchedMidMotion++;
      // Once the window is pure slide, the readout must SAY 25 mm/s — the
      // diagnostics paste that re-derives the thresholds rides on these
      // being real units, and a per-frame-instead-of-per-second regression
      // would corrupt the instrument while every gate still worked.
      if (k > LATCH_VEL_WINDOW + 2) {
        assert.ok(r.velMmS > 20 && r.velMmS < 30,
          `window reads ${r.velMmS.toFixed(1)} mm/s during a 25 mm/s slide — the readout's units drifted`);
        assert.ok(r.velDegS < 1, `phantom rotation ${r.velDegS.toFixed(2)} deg/s during a pure slide`);
      }
      if (prev) maxStep = Math.max(maxStep, Math.hypot(t[0] - prev[0], t[1] - prev[1], t[2] - prev[2]));
      prev = t;
      final = t;
    }
    assert.equal(latchedMidMotion, 0,
      're-latched mid-slide — slow deliberate motion is being read as rest again');
    assert.ok(maxStep < 1.6,
      `a ${maxStep.toFixed(2)} mm frame step during a 0.83 mm/frame slide — the choppiness is back`);
    // And the pose must actually BE where the head is: bounded steps alone
    // would also pass a build that froze and fell ever further behind.
    assert.ok(Math.abs(final![0] - 50) < 3,
      `after a 50 mm slide the emitted pose sits at ${final![0].toFixed(1)} mm — bounded steps, unbounded lag`);
  });

  it('sub-velocity creep is pursued by the leaky anchor — one spell, no snaps', () => {
    // 4 mm/s sits under the enter gate: the velocity gate calls it
    // stillness, so the anchor pursues it at sub-enter speed one deadband
    // behind instead of freezing until the drift guard snaps — the "small
    // breathe every couple of seconds" the first calibrated field session
    // reported, retired. One latch spell for the whole creep, zero
    // re-anchor events, and every frame step bounded by the enter rate —
    // stillness-speed, invisible by the gate's own definition.
    const state = createTracker(model, { smooth: 'locked' });
    let prev: Float64Array | null = null;
    let maxStep = 0;
    let final: Float64Array | null = null;
    for (let k = 0; k <= 120; k++) {
      const r = track(state, {
        landmarks: frameAt(0, (4 * k) / 30, 0.8, 3000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked, `creep frame ${k} refused: ${r.reason}`);
      const t = Float64Array.from(r.pose!.t);
      if (prev && k > 2) maxStep = Math.max(maxStep, Math.hypot(t[0] - prev[0], t[1] - prev[1], t[2] - prev[2]));
      prev = t;
      final = t;
    }
    // The pursuit must also arrive: 120 frames of 4 mm/s is 16 mm of truth,
    // and the anchor trails by about one deadband, no more.
    assert.ok(Math.abs(final![0] - 16) < 2,
      `after 16 mm of creep the emitted pose sits at ${final![0].toFixed(1)} mm — the anchor is hoarding motion`);
    assert.equal(state.latchReanchors, 0,
      `${state.latchReanchors} drift re-anchors during pursued creep — the breathe is back`);
    assert.equal(state.latchEngages, 1,
      `${state.latchEngages} engages — the pursuit dropped the latch it should have carried`);
    assert.ok(maxStep < 0.45,
      `a ${maxStep.toFixed(2)} mm frame step during pursued creep — ` +
      'pursuit is supposed to move at stillness-speed');
  });

  it("a slow head turn must NOT latch — the rotational half of 'stuck', pinned", () => {
    // 5 deg/s of yaw with essentially zero translation: the most natural
    // resting motion there is, and the axis the gate's deg/s channel exists
    // for. A deg-blind gate reads ~0 mm/s here, latches mid-turn, and
    // stutters through drift-release cycles — v1's defect, rotated. The
    // review that demanded this test proved the whole suite passed with
    // velDegS deleted from both gates.
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 20; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 5000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched at rest before the turn');
    let latchedMidTurn = 0;
    let prevYaw: number | null = null;
    let maxYawStep = 0;
    for (let k = 1; k <= 60; k++) {
      const r = track(state, {
        landmarks: frameAt((5 * k) / 30, 0, 0.8, 6000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked, `turn frame ${k} refused: ${r.reason}`);
      const yaw = (r.euler!.yaw * 180) / Math.PI;
      if (k > 12 && r.latched) latchedMidTurn++;
      if (prevYaw !== null) maxYawStep = Math.max(maxYawStep, Math.abs(yaw - prevYaw));
      prevYaw = yaw;
    }
    assert.equal(latchedMidTurn, 0,
      're-latched during a 5 deg/s head turn — the deg/s channel is not gating');
    assert.ok(maxYawStep < 0.8,
      `a ${maxYawStep.toFixed(2)} deg frame step during a 0.17 deg/frame turn — rotational chop`);
  });

  it('the enter/exit hysteresis holds — mid-band motion must not re-latch', () => {
    // 11 mm/s sits between enter (8.5) and exit (15): fast enough that rest
    // must never be claimed, slow enough that the velocity release alone
    // never fires. A collapsed hysteresis (enter raised to exit) reads the
    // band as quiet, latches mid-motion, and drift-cycles — the boundary
    // chatter the two thresholds exist to prevent, invisible to the other
    // fixtures because none of them moves inside the band.
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 20; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 7000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched at rest before the slide');
    let latchedMidMotion = 0;
    for (let k = 1; k <= 60; k++) {
      const r = track(state, {
        landmarks: frameAt(0, (11 * k) / 30, 0.8, 8000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked, `mid-band frame ${k} refused: ${r.reason}`);
      if (k > 12 && r.latched) latchedMidMotion++;
    }
    assert.equal(latchedMidMotion, 0,
      'mid-band motion re-latched — the enter threshold has crept up to the exit');
  });

  it('the pursuit does not hold real motion in custody', () => {
    // 9.5 mm/s — just over the enter gate. The pursuit must NOT run here:
    // an ungated pursuit absorbs 8.5 of the 9.5 and keeps the latch holding
    // real motion for over a second, trailing the head the whole way —
    // the "stuck" defect reborn at pursuit speed. Gated, the guard trips
    // within a dozen frames and the latch stays out (9.5 > enter).
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 20; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 13000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched at rest before the slide');
    let heldLate = 0;
    for (let k = 1; k <= 90; k++) {
      const r = track(state, {
        landmarks: frameAt(0, (9.5 * k) / 30, 0.8, 14000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked, `custody frame ${k} refused: ${r.reason}`);
      if (k > 20 && r.latched) heldLate++;
    }
    assert.equal(heldLate, 0,
      `still latched ${heldLate} frames deep into a 9.5 mm/s slide — the pursuit is holding motion in custody`);
  });

  it('a stall does not dilute the velocity window into a false rest', () => {
    // One consumed frame carrying a full second (tab switch, GC pause,
    // detector jank) puts a gap nothing observed inside the window. Averaged
    // into the span, a 30 mm/s pan right after the stall reads a few mm/s —
    // quiet enough to latch mid-pan. The ring must die at the stall instead:
    // the same "that movement is over" judgement the miss path makes, at the
    // same threshold.
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 15; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 9000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched at rest before the stall');
    let latchedMidPan = 0;
    for (let k = 1; k <= 30; k++) {
      // k=1 is the resume frame, carrying the stalled second; the pan is
      // already underway when the stream returns. While pre-stall rest
      // entries still anchor the window, the pan's displacement divides by
      // the stall-dominated span and reads ~6-8 mm/s — under the enter
      // threshold, counting toward a latch of a head moving at 30 mm/s.
      const dt = k === 1 ? 1.0 : 1 / 30;
      const r = track(state, { landmarks: frameAt(0, (30 * k) / 30, 0.8, 9500 + k), sigmaPx: sigma, intrinsics: K, dt });
      assert.ok(r.tracked, `post-stall frame ${k} refused: ${r.reason}`);
      // The breakout transient (drift release + fade) owns the first frames;
      // claiming rest after it is the defect.
      if (k >= 8 && r.latched) latchedMidPan++;
    }
    assert.equal(latchedMidPan, 0,
      'latched mid-pan after a stall — the stalled second diluted the window into a false rest');
  });

  it('the quiet streak does not straddle a dropout', () => {
    // "Quiet for LATCH_ENTER_FRAMES" means consecutive OBSERVED frames. A
    // streak banked before a blink used to survive it and let the latch
    // engage one frame after recovery, on a window nobody watched.
    const state = createTracker(model, { smooth: 'locked' });
    // Ring full at frame 10; quiet reaches 2 by frame 11 — one short of
    // engaging.
    for (let i = 0; i < 12; i++) {
      const r = track(state, { landmarks: frameAt(0, 0, 0.8, 10000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      assert.ok(!r.latched, `latched at frame ${i}, before the enter count could complete`);
    }
    for (let i = 0; i < 3; i++) {
      track(state, { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 });
    }
    // Recovery: the count starts over. Frames 1 and 2 must not be latched;
    // by the fourth observed frame stillness has re-earned the latch.
    const r1 = track(state, { landmarks: frameAt(0, 0, 0.8, 10100), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    const r2 = track(state, { landmarks: frameAt(0, 0, 0.8, 10101), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    assert.ok(!r1.latched && !r2.latched,
      'the latch engaged on a quiet streak banked before the dropout');
    track(state, { landmarks: frameAt(0, 0, 0.8, 10102), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    const r4 = track(state, { landmarks: frameAt(0, 0, 0.8, 10103), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    assert.ok(r4.latched, 'stillness after the dropout never re-earned the latch');
  });

  it('a dropout the tracker WATCHED and one it slept through end in the same place', () => {
    // **Hermeticity, and it is the whole argument for crediting the gap.**
    //
    // Two descriptions of one wall clock. Arm A: the tracker is called on N
    // faceless frames and then a good one at dt = 1/30. Arm B: the tracker is
    // not called at all during the gap, then the same good frame at
    // dt = (N+1)/30. Nothing physical distinguishes them, so the emitted pose
    // must not either.
    //
    // The motion prior, the stall reset and the velocity clock all credited
    // `gapSeconds` already. The One Euro filter did not, so it was told one
    // frame had passed when up to fourteen had — and the pose it emitted on
    // recovery differed between the two arms by up to 2.88 mm.
    //
    // RED: drop `+ gapSeconds` from the smoother's call in `track()`.
    // 4 mm per frame = 120 mm/s at 30 fps. The head has to MOVE through the
    // gap for the filter's clock to matter at all: the first version of this
    // test slid it 0 mm and passed under sabotage, which is the same defect it
    // was written to catch, one level up. A still head is measurably immune —
    // total emitted travel over the eight frames after a 12-frame dropout is
    // 0.326 mm shipped against 0.344 credited.
    const slide = (i: number) => frameAt(0, i * 4, 0.0, 20000 + i);
    for (const gap of [1, 5, 12]) {
      const a = createTracker(model, { smooth: true });
      const b = createTracker(model, { smooth: true });
      // Both arms see the same lead-in, frame for frame.
      for (let i = 0; i < 8; i++) {
        const input = { landmarks: slide(i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 };
        track(a, input);
        track(b, input);
      }
      // A watches the gap go dark; B is simply not called.
      for (let i = 0; i < gap; i++) {
        track(a, { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 });
      }
      const ra = track(a, { landmarks: slide(8 + gap), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      const rb = track(b, { landmarks: slide(8 + gap), sigmaPx: sigma, intrinsics: K, dt: (gap + 1) / 30 });
      assert.ok(ra.pose && rb.pose, 'a recovery frame was refused');
      const apart = Math.hypot(
        ra.pose.t[0] - rb.pose.t[0], ra.pose.t[1] - rb.pose.t[1], ra.pose.t[2] - rb.pose.t[2],
      );
      assert.ok(apart < 1e-9,
        `after a ${gap}-frame gap the watched and unwatched arms land ${apart.toFixed(4)} mm `
        + 'apart. They describe the same wall clock, so the filter was told the wrong dt.');
    }
  });

  it('refused solves do not count as acquisitions', () => {
    // 'acquisitions' means times the tracker ACQUIRED — a solve the rms gate
    // then refused acquired nothing. The old counting sat at the solve, so a
    // fresh tracker staring at garbage (someone walks through the frame
    // before the wearer sits down) banked one phantom acquisition per
    // refused frame into the diagnostics; the same before-the-gate shape
    // inflated the counter once per frame through any hand-over-face spell
    // whose cold retry beat the stale warm start.
    const state = createTracker(model, { smooth: false });
    for (let i = 0; i < 10; i++) {
      // 200 px of scatter: enough correspondences survive the sigma gate,
      // but they do not describe this face — rms fails, every frame refused.
      const r = track(state, { landmarks: frameAt(0, 0, 200, 12000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      assert.ok(!r.tracked, `garbage frame ${i} was accepted at 200 px of scatter`);
    }
    assert.equal(state.acquisitions, 0,
      `${state.acquisitions} acquisitions from 10 refused frames — the counter sits before the gate again`);
    const clean = track(state, { landmarks: frameAt(0, 0, 0.8, 11000), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    assert.ok(clean.tracked, `the clean frame was refused: ${clean.reason}`);
    assert.equal(state.acquisitions, 1, 'the real acquisition went uncounted');
  });

  it('a stall while latched drops the anchor — the gap is not paid out as a swoop', () => {
    // A latch held across a two-second tab-switch used to keep its stale
    // anchor: the release fired on the gap displacement, booked it as drift
    // creep, and the crossfade swept the glasses two-thirds of the way back
    // across the gap — an unbounded-magnitude swoop the miss path was
    // explicitly designed to prevent. The stall branch now makes the miss
    // path's whole judgement: the anchor died with the motion it described.
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 20; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 15000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.ok(state.latchedPose, 'precondition: latched before the stall');
    const reanchorsBefore = state.latchReanchors;
    const r = track(state, { landmarks: frameAt(0, 60, 0.8, 16000), sigmaPx: sigma, intrinsics: K, dt: 2.0 });
    assert.ok(r.tracked, `the stall frame was refused: ${r.reason}`);
    assert.ok(!r.latched && !r.fading, 'the stale anchor survived a two-second stall');
    assert.equal(state.latchReanchors, reanchorsBefore,
      'a tab-switch discontinuity was booked as drift creep');
    assert.ok(Math.abs(r.pose!.t[0] - 60) < 5,
      `the stall frame emitted ${r.pose!.t[0].toFixed(1)} mm of a 60 mm gap — ` +
      'the crossfade is paying the gap out as a swoop');
  });

  it('a gap split across misses and a slow recovery frame still resets the window', () => {
    // 0.43 s of misses plus a 0.40 s recovery dt is 0.83 s of darkness —
    // over the reset span, but each half under its own old check, and the
    // review mechanized ~1.0 s of unobserved motion slipping through the
    // seam. The stall judgement now reads the COMBINED gap: the recovery
    // frame must start the window over, reporting no velocity at all.
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 15; i++) {
      track(state, { landmarks: frameAt(0, 0, 0.8, 17000 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    for (let i = 0; i < 13; i++) {
      track(state, { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 });
    }
    const r = track(state, { landmarks: frameAt(0, 0, 0.8, 18000), sigmaPx: sigma, intrinsics: K, dt: 0.4 });
    assert.ok(r.tracked, `the recovery frame was refused: ${r.reason}`);
    assert.ok(Number.isNaN(r.velMmS),
      `the recovery frame reports ${r.velMmS?.toFixed?.(1)} mm/s across 0.83 s of darkness — ` +
      'the split gap evaded the stall judgement again');
  });

  it('a brief dropout does not corrupt the velocity clock', () => {
    // miss() banks dropped-frame time in lostSeconds and the recovery frame
    // credits it into the velocity window's clock. Without the credit, the
    // window straddles the gap with a foreshortened span and a 6 mm/s drift
    // reads ~11 mm/s for the next ten frames — past the 8.5 enter threshold,
    // so the latch wrongly refuses (or releases over) a speed it owns.
    const state = createTracker(model, { smooth: 'locked' });
    const txAt = (k: number) => (6 * k) / 30;
    for (let k = 0; k < 15; k++) {
      track(state, { landmarks: frameAt(0, txAt(k), 0.8, 4000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    // Eight faceless frames — under the 0.5 s reset — while the head drifts on.
    for (let k = 15; k < 23; k++) {
      track(state, { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 });
    }
    let maxV = 0;
    for (let k = 23; k < 31; k++) {
      const r = track(state, { landmarks: frameAt(0, txAt(k), 0.8, 4000 + k), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      assert.ok(r.tracked, `recovery frame ${k} refused: ${r.reason}`);
      if (Number.isFinite(r.velMmS)) maxV = Math.max(maxV, r.velMmS);
    }
    assert.ok(maxV > 0, 'no velocity reading while the window straddled the gap');
    assert.ok(maxV < 8.5,
      `a 6 mm/s drift read ${maxV.toFixed(1)} mm/s across a dropout — ` +
      "the gap's time went missing from the velocity clock");
  });

  it("'locked' at rest is stiller than 'true', which is stiller than raw — the ordering that is the point", () => {
    const spread = (mode: false | true | 'locked') => {
      const state = createTracker(model, { smooth: mode });
      let prev: Float64Array | null = null;
      let sum = 0, n = 0;
      for (let i = 0; i < 36; i++) {
        const r = track(state, { landmarks: frameAt(0, 0, 0.8, 300 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
        const t = Float64Array.from(r.pose!.t);
        if (prev && i > SETTLE) { sum += Math.hypot(t[0] - prev[0], t[1] - prev[1], t[2] - prev[2]); n++; }
        prev = t;
      }
      return sum / n;
    };
    const raw = spread(false), fixed = spread(true), locked = spread('locked');
    assert.ok(locked <= fixed * 0.25,
      `locked crawl ${locked.toFixed(4)} vs fixed ${fixed.toFixed(4)} — the latch buys nothing`);
    assert.ok(locked < 1e-9, `locked crawl ${locked.toFixed(6)} mm — should be exactly zero at rest`);
    assert.ok(fixed <= raw, 'fixed smoothing failed to smooth');
  });
});

describe("the latch gates calibrate to the session's rest floor", () => {
  // The defect these tests exist for was filed by the first real face: its
  // at-rest rotational wander (windowed p50 0.87 deg/s, p90 2.14) sat above
  // both fixed thresholds, and the latch chattered on genuine stillness —
  // 34.7% latched, seven engage/release cycles in nine seconds. No fixed
  // constants survive contact with a second camera, so the gates learn each
  // session's rest floor from latched frames, clamped to [prior, prior*cap].
  // These tests run the MECHANISM across noise regimes the shipped priors
  // never saw — which is the only way one development face can test what a
  // general user gets: the fixture is the population, the face is one draw.
  const mesh = loadTemplateMesh();
  const model = createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.3),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
  const K = { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };
  const sigma = new Float64Array(mesh.vertexCount).fill(1.4);

  // A session generator with CORRELATED rest wander: iid jitter solves to
  // ~0.02 mm of pose noise and can never chatter a gate, so these fixtures
  // add what real detectors have — a common-mode AR(1) image offset and a
  // common-mode AR(1) roll about the landmark centroid, which the solver
  // reads as slow head wander. rollSigmaDeg scales the regime.
  const wanderSession = (seedBase: number, rollSigmaDeg: number) => {
    let st = seedBase >>> 0 || 1;
    const rnd = () => {
      st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0;
      return st / 4294967296;
    };
    const gauss = () => {
      let u1 = 0;
      while (u1 === 0) u1 = rnd();
      return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * rnd());
    };
    const rho = Math.exp(-(1 / 30) / 0.3);
    const rollStep = rollSigmaDeg * Math.sqrt(1 - rho * rho);
    const offStep = 0.8 * Math.sqrt(1 - rho * rho);
    let phi = 0, ox = 0, oy = 0;
    return (yawDeg: number) => {
      phi = rho * phi + rollStep * gauss();
      ox = rho * ox + offStep * gauss();
      oy = rho * oy + offStep * gauss();
      const th = (yawDeg * Math.PI) / 180;
      const c = Math.cos(th), sn = Math.sin(th);
      const pose = {
        R: Float64Array.of(c, 0, sn, 0, -1, 0, sn, 0, -c),
        t: Float64Array.of(0, 0, 520),
      };
      const lm = new Float64Array(mesh.vertexCount * 2);
      let mx = 0, my = 0;
      for (let v = 0; v < mesh.vertexCount; v++) {
        const X = mesh.positions[v * 3], Y = mesh.positions[v * 3 + 1], Z = mesh.positions[v * 3 + 2];
        const cx = pose.R[0] * X + pose.R[1] * Y + pose.R[2] * Z + pose.t[0];
        const cy = pose.R[3] * X + pose.R[4] * Y + pose.R[5] * Z + pose.t[1];
        const cz = pose.R[6] * X + pose.R[7] * Y + pose.R[8] * Z + pose.t[2];
        lm[v * 2] = K.cx + (K.f * cx) / cz;
        lm[v * 2 + 1] = K.cy + (K.f * cy) / cz;
        mx += lm[v * 2]; my += lm[v * 2 + 1];
      }
      mx /= mesh.vertexCount; my /= mesh.vertexCount;
      const cr = Math.cos((phi * Math.PI) / 180), sr = Math.sin((phi * Math.PI) / 180);
      for (let v = 0; v < mesh.vertexCount; v++) {
        const dx = lm[v * 2] - mx, dy = lm[v * 2 + 1] - my;
        lm[v * 2] = mx + cr * dx - sr * dy + ox + gauss() * 0.5;
        lm[v * 2 + 1] = my + sr * dx + cr * dy + oy + gauss() * 0.5;
      }
      return lm;
    };
  };
  const step = (state: ReturnType<typeof createTracker>, lm: Float64Array) =>
    track(state, { landmarks: lm, sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });

  it('chatter on a noisy-rest regime heals itself within seconds', () => {
    // A regime ~2x the shipped rotation prior — the first wearer's face,
    // roughly. On FIXED gates this chatters indefinitely; the calibrator
    // must learn the floor and settle into a solid latch.
    const frame = wanderSession(0xfeed1, 0.5);
    const state = createTracker(model, { smooth: 'locked' });
    const velDegAll: number[] = [];
    // Settling half: 15 s. Record the regime's own velocity so the fixture
    // proves it WOULD chatter on the priors.
    for (let i = 0; i < 450; i++) {
      const r = step(state, frame(0));
      assert.ok(r.tracked, `frame ${i} lost: ${r.reason}`);
      if (Number.isFinite(r.velDegS)) velDegAll.push(r.velDegS);
    }
    const p50 = percentile(velDegAll, 0.5);
    assert.ok(p50 > 0.8,
      `fixture regime reads ${p50.toFixed(2)} deg/s at rest — too quiet to chatter fixed gates, the fixture is not earning its name`);
    // Measured half: 5 s. The latch must now HOLD: mostly latched, and
    // velocity releases (the chatter signature) essentially gone — drift
    // re-anchors remain allowed, they are the designed glide.
    const releasesBefore = state.latchReleases;
    let latchedFrames = 0;
    for (let i = 0; i < 150; i++) {
      const r = step(state, frame(0));
      if (r.latched) latchedFrames++;
    }
    // The bar sits in the measured discrimination gap, not on a hope: this
    // fixture scores 103/150 calibrated against 14/150 with the gates
    // frozen at their priors (drift re-anchor glides own the remaining
    // unlatched frames — they are designed behavior, ~8 frames per cycle).
    assert.ok(latchedFrames >= 75,
      `${latchedFrames}/150 frames latched after 15 s of calibration — the gates never learned this session's rest`);
    assert.ok(state.latchReleases - releasesBefore <= 1,
      `${state.latchReleases - releasesBefore} velocity releases in 5 s of calibrated rest — still chattering`);
    assert.ok(state.latchEnterDegS > 0.8,
      'the rotation gate never moved off its prior in a regime that demanded it');
  });

  it('the cap binds — noise beyond it may not claim rest', () => {
    // A regime whose learned floor wants to exceed prior*cap. The clamp is
    // what keeps deliberate slow motion followable in ANY regime: an
    // uncapped gate here would rise past 5 deg/s and read a real head turn
    // as stillness.
    const frame = wanderSession(0xfeed2, 0.9);
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 600; i++) {
      const r = step(state, frame(0));
      assert.ok(r.tracked, `frame ${i} lost: ${r.reason}`);
    }
    assert.ok(state.floorDeg !== null, 'the floor never bootstrapped in the cap regime');
    assert.equal(state.latchEnterDegS, 0.8 * 6,
      `rotation gate reads ${state.latchEnterDegS.toFixed(2)} deg/s — the cap is not binding`);
  });

  it('gates relax back toward the priors when the regime quiets', () => {
    // Noisy light, then good light: the learned floor must not be a
    // ratchet-up-only — the latched samples quiet down and the EMA follows
    // them home to the prior clamp.
    const noisy = wanderSession(0xfeed3, 0.5);
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 450; i++) step(state, noisy(0));
    assert.ok(state.latchEnterDegS > 1.6,
      `precondition: gate only reached ${state.latchEnterDegS.toFixed(2)} deg/s in the noisy phase`);
    const quiet = wanderSession(0xfeed4, 0.02);
    for (let i = 0; i < 600; i++) step(state, quiet(0));
    assert.ok(state.latchEnterDegS < 0.8 * 1.3,
      `gate still reads ${state.latchEnterDegS.toFixed(2)} deg/s after 20 s of quiet — the floor does not relax`);
  });

  it('a calibrated session still follows real motion', () => {
    // After learning a noisy floor, a slow deliberate 5 deg/s turn — above
    // any gate the cap permits — must be FOLLOWED: the outcome-level "not
    // stuck" statement, robust to transient flicker at the boundary.
    const frame = wanderSession(0xfeed5, 0.5);
    const state = createTracker(model, { smooth: 'locked' });
    for (let i = 0; i < 450; i++) step(state, frame(0));
    let lastYaw = 0;
    for (let k = 1; k <= 60; k++) {
      const r = step(state, frame((5 * k) / 30));
      assert.ok(r.tracked, `turn frame ${k} refused: ${r.reason}`);
      lastYaw = Math.abs((r.euler!.yaw * 180) / Math.PI);
    }
    assert.ok(Math.abs(lastYaw - 10) < 2.5,
      `after a 10 deg slow turn the tracker reads ${lastYaw.toFixed(1)} deg — the learned gates are holding real motion hostage`);
  });
});

describe('gaze may not move the glasses', () => {
  // MediaPipe deforms the eye region with GAZE — lids and contours follow
  // the pupil — so a perfectly still wearer could steer the pose solve with
  // their eyes: the first real wearer reported the glasses "heavily
  // affected" by iris movement against a motionless face. The fix is
  // disenfranchisement: `trackingRigidity` zeroes the eye region's vote in
  // the solve. The fixture displaces exactly those vertices, coherently,
  // the way a gaze shift does.
  const mesh = loadTemplateMesh();
  const regions = standardRegions(mesh);
  const model = createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.3),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
  const K = { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };
  const sigma = new Float64Array(mesh.vertexCount).fill(1.4);
  // The lid rings (standard MediaPipe topology, corners included) and the
  // orbital surround the eyes region sweeps up around them. The deformation
  // profile below moves the rings fully and the surround at a third — the
  // lids follow the pupil, the orbit mostly does not.
  const rings = new Set([
    33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
    263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466,
  ]);
  const surround = Array.from(regions.eyes.members).filter((i) => !rings.has(i));

  const gazeFrame = (gazePx: number, seed: number) => {
    const lm = new Float64Array(mesh.vertexCount * 2);
    let st = seed >>> 0 || 1;
    const rnd = () => {
      st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0;
      return st / 4294967296 - 0.5;
    };
    for (let v = 0; v < mesh.vertexCount; v++) {
      const X = mesh.positions[v * 3], Y = mesh.positions[v * 3 + 1], Z = mesh.positions[v * 3 + 2];
      const cz = -Z + 520;
      lm[v * 2] = K.cx + (K.f * X) / cz + rnd() * 1.6;
      lm[v * 2 + 1] = K.cy + (K.f * -Y) / cz + rnd() * 1.6;
    }
    // The gaze: lids follow the pupil fully, the orbital surround drags.
    for (const i of rings) lm[i * 2] += gazePx;
    for (const i of surround) lm[i * 2] += gazePx / 3;
    return lm;
  };

  it('an eye-region shift leaves the solved pose still', () => {
    const meanPose = (rig: Float64Array | null, gazePx: number, seedBase: number) => {
      const state = createTracker(model, { smooth: false, rigidity: rig });
      const acc = [0, 0, 0];
      for (let i = 0; i < 12; i++) {
        const r = track(state, {
          landmarks: gazeFrame(gazePx, seedBase + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30,
        });
        assert.ok(r.tracked, `gaze frame ${i} refused: ${r.reason}`);
        acc[0] += r.rawPose!.t[0]; acc[1] += r.rawPose!.t[1]; acc[2] += r.rawPose!.t[2];
      }
      return acc.map((v) => v / 12);
    };
    const shift = (rig: Float64Array | null) => {
      const a = meanPose(rig, 0, 20000);
      const b = meanPose(rig, 3, 21000);
      return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    };
    const rigidity = trackingRigidity(mesh, regions);
    const unprotected = shift(null);
    const protectedShift = shift(rigidity);
    // Fixture sanity first: if the naive solve barely moves, this test can
    // discriminate nothing and must say so rather than pass vacuously.
    // (Measured: unprotected 0.366 mm, tiered map 0.120 — the residual is
    // the surround's half vote, and it sits far inside the latch's 1.1 mm
    // frozen deadband, so at rest gaze moves the emitted pose not at all.)
    assert.ok(unprotected > 0.2,
      `fixture sanity: the unprotected solve moved only ${unprotected.toFixed(3)} mm — ` +
      'the gaze fixture is too weak to convict anything');
    assert.ok(protectedShift < unprotected * 0.45,
      `a pure gaze shift still moves the pose ${protectedShift.toFixed(3)} mm ` +
      `(unprotected: ${unprotected.toFixed(3)}) — the lid rings are still voting`);
    assert.ok(protectedShift < 0.18,
      `gaze residual ${protectedShift.toFixed(3)} mm — outside the perceptual budget ` +
      'the latch deadband gives it');
  });
});

describe("the tilt pass — the solve knows how much it knows", () => {
  // The field failure this exists for: 11 seconds of perfectly still rest at
  // 25 deg of yaw produced ZERO latched frames, because the far half-face is
  // hallucinated (MediaPipe draws it under-rotated toward its frontal
  // prior), the solve got ~2x noisier, and absolute gates tuned frontal
  // could never pass. The fixture reproduces the mechanism: far-side
  // landmarks rendered at 0.55x the true yaw, blended by hiddenness, sigma
  // inflated quadratically, visibility = 1 - hiddenness, common-mode wander
  // on top for the rest scenarios.
  const mesh = loadTemplateMesh();
  const regions = standardRegions(mesh);
  const rigidity = trackingRigidity(mesh, regions);
  const K = { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };
  const model = createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.3),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: K, intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });

  const poseAt = (yawDeg: number) => {
    const th = (yawDeg * Math.PI) / 180;
    const c = Math.cos(th), sn = Math.sin(th);
    return { R: Float64Array.of(c, 0, sn, 0, -1, 0, sn, 0, -c), t: Float64Array.of(0, 0, 520) };
  };
  const projectAll = (pose: { R: Float64Array; t: Float64Array }) => {
    const out = new Float64Array(mesh.vertexCount * 2);
    for (let v = 0; v < mesh.vertexCount; v++) {
      const X = mesh.positions[v * 3], Y = mesh.positions[v * 3 + 1], Z = mesh.positions[v * 3 + 2];
      const cx = pose.R[0] * X + pose.R[1] * Y + pose.R[2] * Z + pose.t[0];
      const cy = pose.R[3] * X + pose.R[4] * Y + pose.R[5] * Z + pose.t[1];
      const cz = pose.R[6] * X + pose.R[7] * Y + pose.R[8] * Z + pose.t[2];
      out[v * 2] = K.cx + (K.f * cx) / cz;
      out[v * 2 + 1] = K.cy + (K.f * cy) / cz;
    }
    return out;
  };
  /**
   * Which strip vertex is on the occluding contour under this pose — the same
   * perpendicularity test `marchStrip` runs, at describe scope because two
   * tests need it and a second copy could drift from the first.
   */
  const marchTruth = (pose: Pose, strip: SilhouetteStrip) => {
    let best = strip.landmark, bestDot = Infinity;
    for (let k = 0; k < strip.candidates.length; k++) {
      const v = strip.candidates[k];
      const nx = strip.normals[k * 3], ny = strip.normals[k * 3 + 1], nz = strip.normals[k * 3 + 2];
      const ncx = pose.R[0] * nx + pose.R[1] * ny + pose.R[2] * nz;
      const ncy = pose.R[3] * nx + pose.R[4] * ny + pose.R[5] * nz;
      const ncz = pose.R[6] * nx + pose.R[7] * ny + pose.R[8] * nz;
      const x = mesh.positions[v * 3], y = mesh.positions[v * 3 + 1], z = mesh.positions[v * 3 + 2];
      const cx = pose.R[0] * x + pose.R[1] * y + pose.R[2] * z + pose.t[0];
      const cy = pose.R[3] * x + pose.R[4] * y + pose.R[5] * z + pose.t[1];
      const cz = pose.R[6] * x + pose.R[7] * y + pose.R[8] * z + pose.t[2];
      const len = Math.hypot(cx, cy, cz) || 1;
      const d = Math.abs((ncx * cx + ncy * cy + ncz * cz) / len);
      if (d < bestDot) { bestDot = d; best = v; }
    }
    return best;
  };

  const makeSession = (seedBase: number, wanderRollDeg: number, sigmaScale = 1) => {
    let st = seedBase >>> 0 || 1;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
    const gauss = () => {
      let u1 = 0; while (u1 === 0) u1 = rnd();
      return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * rnd());
    };
    const rho = Math.exp(-(1 / 30) / 0.3);
    const rollStep = wanderRollDeg * Math.sqrt(1 - rho * rho);
    const offStep = 0.8 * Math.sqrt(1 - rho * rho);
    let phi = 0, ox = 0, oy = 0;
    return (yawDeg: number) => {
      phi = rho * phi + rollStep * gauss();
      ox = rho * ox + offStep * gauss();
      oy = rho * oy + offStep * gauss();
      const trueP = projectAll(poseAt(yawDeg));
      const hallP = projectAll(poseAt(yawDeg * 0.55));
      const lm = new Float64Array(mesh.vertexCount * 2);
      const sig = new Float64Array(mesh.vertexCount);
      const vis = new Float64Array(mesh.vertexCount);
      const s = Math.sin((yawDeg * Math.PI) / 180);
      let mx = 0, my = 0;
      // Hallucinated landmarks do not just sit in the wrong place — they
      // WANDER, and they wander TOGETHER (one prior invents them all), which
      // is why the solve cannot average the noise away: a per-vertex iid
      // wobble was measured to vanish under the root-N averaging. And the
      // claimed sigma under-states them — quadratic-in-hiddenness at HALF
      // the real inflation — because that dishonesty is precisely the real
      // estimator's documented failure, and an honest sigma stream was
      // measured to defuse this fixture entirely.
      const fx = gauss() * 1.5, fy = gauss() * 1.5;
      for (let v = 0; v < mesh.vertexCount; v++) {
        const lateral = mesh.positions[v * 3] / 80;
        const h = Math.min(1, Math.max(0, -lateral * s * 2.5));
        lm[v * 2] = (1 - h) * trueP[v * 2] + h * hallP[v * 2] + gauss() * 0.5 + fx * h;
        lm[v * 2 + 1] = (1 - h) * trueP[v * 2 + 1] + h * hallP[v * 2 + 1] + gauss() * 0.5 + fy * h;
        sig[v] = 1.4 * (1 + 2 * h * h) * sigmaScale;
        vis[v] = 1 - h;
        mx += lm[v * 2]; my += lm[v * 2 + 1];
      }
      mx /= mesh.vertexCount; my /= mesh.vertexCount;
      const cr = Math.cos((phi * Math.PI) / 180), sr = Math.sin((phi * Math.PI) / 180);
      for (let v = 0; v < mesh.vertexCount; v++) {
        const dx = lm[v * 2] - mx, dy = lm[v * 2 + 1] - my;
        lm[v * 2] = mx + cr * dx - sr * dy + ox;
        lm[v * 2 + 1] = my + sr * dx + cr * dy + oy;
      }
      return { lm, sig, vis };
    };
  };

  it('tilted rest latches — the field deadlock, reproduced and dissolved', () => {
    const state = createTracker(model, { smooth: 'locked', rigidity });
    const frame = makeSession(0xdef1, 0.5);
    let latched = 0;
    let firstEngage = -1;
    for (let i = 0; i < 600; i++) {
      const { lm, sig, vis } = frame(25);
      const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30 });
      assert.ok(r.tracked, `tilted frame ${i} lost: ${r.reason}`);
      if (firstEngage < 0 && state.latchEngages > 0) firstEngage = i;
      if (i >= 450 && r.latched) latched++;
    }
    // Outcome assertions only: WHICH mechanism opened the gates is pinned by
    // the deterministic lift test below — a first draft pinned it here by
    // the gate's value at one second, and the review proved that seed-lucky
    // (4 of 10 equivalent streams read below the bar).
    assert.ok(firstEngage >= 0 && firstEngage <= 150,
      `first latch engage at frame ${firstEngage} — the gates did not open when the regime demanded it`);
    assert.ok(latched >= 105,
      `${latched}/150 tilted-rest frames latched — the field deadlock is back (it measured 0)`);
  });

  it('the gate lift is instant — before any calibration could reach', () => {
    // Fifteen frames of loudly, HONESTLY noisy landmarks: 6 px injected,
    // 6 px claimed, so the calibrated covariance reads large and stable in
    // every stream — no wander corridor, no seed luck. The calibrator
    // provably cannot be the cause at this horizon: its floor is null until
    // frames latch, and even a flicker's two or three samples move the
    // learned gate by under a tenth of the prior at LATCH_FLOOR_RATE. A gate
    // well above the 0.8 prior by frame 15 is the covariance lift, uniquely.
    const state = createTracker(model, { smooth: 'locked', rigidity });
    let st = 0x11f7;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
    const truthP = projectAll(poseAt(0));
    const sigBig = new Float64Array(mesh.vertexCount).fill(6);
    for (let i = 0; i < 15; i++) {
      const lm = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + rnd() * 12;
      const r = track(state, { landmarks: lm, sigmaPx: sigBig, intrinsics: K, dt: 1 / 30 });
      assert.ok(r.tracked, `noisy frame ${i} refused: ${r.reason}`);
    }
    assert.ok(state.latchEnterDegS > 2,
      `after 15 honestly-noisy frames the rotation gate reads ${state.latchEnterDegS.toFixed(2)} deg/s — ` +
      'the covariance lift is not lifting');
  });

  it('the covariance grows with the regime and stays proportionate to the truth', () => {
    const results: { sigma: number; sigmaDeg: number; jitter: number }[] = [];
    for (const yaw of [0, 25, 40]) {
      const state = createTracker(model, { smooth: false, rigidity });
      const frame = makeSession(0xabc0 + yaw, 0);
      let sSum = 0, sDegSum = 0, jit = 0, n = 0, jn = 0;
      let prev: Float64Array | null = null;
      for (let i = 0; i < 50; i++) {
        const { lm, sig, vis } = frame(yaw);
        const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30 });
        if (!r.tracked) continue;
        if (Number.isFinite(r.sigmaMm)) { sSum += r.sigmaMm; sDegSum += r.sigmaDeg; n++; }
        const t = Float64Array.from(r.rawPose!.t);
        if (prev) { jit += Math.hypot(t[0] - prev[0], t[1] - prev[1], t[2] - prev[2]); jn++; }
        prev = t;
      }
      results.push({ sigma: sSum / n, sigmaDeg: sDegSum / n, jitter: jit / jn });
    }
    // Physical-range bands per channel: a copy-paste slip that reads the
    // rotation block as millimetres lands three orders of magnitude away
    // (radians-squared traces), and the review proved exactly that slip
    // passed the whole suite before these bands existed.
    for (const r of results) {
      assert.ok(r.sigma > 0.02 && r.sigma < 5,
        `sigmaMm ${r.sigma.toFixed(4)} outside any physical pose noise — reading the wrong covariance block?`);
      assert.ok(r.sigmaDeg > 0.02 && r.sigmaDeg < 5,
        `sigmaDeg ${r.sigmaDeg.toFixed(4)} outside any physical pose noise — reading the wrong covariance block?`);
    }
    assert.ok(results[2].sigma > results[0].sigma * 1.4,
      `predicted sigma barely moved (${results[0].sigma.toFixed(3)} -> ${results[2].sigma.toFixed(3)} mm) ` +
      'across a regime whose real noise doubles — the covariance is regime-blind');
    // The load-bearing property is not an absolute constant of
    // proportionality — the fixture's claimed-sigma dishonesty varies with
    // yaw, as a real estimator's does, and at high yaw the covariance also
    // carries hallucination BIAS that frame-to-frame jitter cannot see. What
    // the gate lift needs is that the prediction never GROWS SLOWER than the
    // truth: an under-tracking covariance would leave the latch deadlocked
    // exactly where it matters.
    const sigmaGrowth = results[2].sigma / results[0].sigma;
    const jitterGrowth = results[2].jitter / results[0].jitter;
    assert.ok(sigmaGrowth >= jitterGrowth * 0.5,
      `predicted sigma grew ${sigmaGrowth.toFixed(2)}x while the real jitter grew ` +
      `${jitterGrowth.toFixed(2)}x — the covariance under-tracks the regime`);
  });

  it('a mis-scaled sigma stream cannot fool the calibration', () => {
    // Same landmarks, same injected noise — but the CLAIMED sigma tripled.
    // The a-posteriori variance factor must eat the lie: H^-1 grows 9x, the
    // whitened residuals shrink 9x, and the predicted pose sigma stands
    // still. Without the factor, the covariance is a statement about claims.
    const measure = (sigmaScale: number) => {
      const state = createTracker(model, { smooth: false, rigidity });
      const frame = makeSession(0xbeef, 0, sigmaScale);
      let sSum = 0, n = 0;
      for (let i = 0; i < 40; i++) {
        const { lm, sig, vis } = frame(25);
        const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30 });
        if (r.tracked && Number.isFinite(r.sigmaMm)) { sSum += r.sigmaMm; n++; }
      }
      return sSum / n;
    };
    const honest = measure(1);
    const inflated = measure(3);
    assert.ok(inflated < honest * 1.6 && inflated > honest / 1.6,
      `claimed-sigma x3 moved the predicted pose sigma ${honest.toFixed(3)} -> ${inflated.toFixed(3)} mm — ` +
      'the variance-factor calibration is not doing its job');
  });

  it('deliberate sigma inflation cannot read as miscalibration', () => {
    // The review measured the pooled variance factor understating pose sigma
    // 18-28% at mid-yaw: landmarks whose sigma is DELIBERATELY inflated (the
    // occlusion bias guard, the rigidity taper) have whitened residuals that
    // are small BY CONSTRUCTION, and pooling them drags chi2/dof under the
    // honest population's value. The discrimination: 200 landmarks claiming
    // 7x sigma at visibility 0.5 carry ~3% of the solve's weight, so the
    // predicted pose sigma against the IDENTICAL stream with those landmarks
    // simply absent may differ by a few percent at most — any bigger shift
    // is the calibration estimate being polluted by claims that were never
    // noise estimates. The pooled estimator provably reads ~0.7x here; the
    // dist sabotages (useCal forced false in residualStats, the tracker's
    // eligibility mask forced all-ones) each restore it and fail this band.
    const run = (subsetPresent: boolean) => {
      const state = createTracker(model, { smooth: false, rigidity });
      let st = 0x5eed;
      const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
      const truthP = projectAll(poseAt(0));
      const subset = new Set<number>();
      for (let i = 0; i < mesh.vertexCount && subset.size < 200; i++) {
        if (rigidity[i] >= 0.999) subset.add(i);
      }
      let sSum = 0, n = 0;
      for (let f = 0; f < 15; f++) {
        const lm = new Float64Array(mesh.vertexCount * 2);
        const sig = new Float64Array(mesh.vertexCount);
        const vis = new Float64Array(mesh.vertexCount).fill(1);
        for (let v = 0; v < mesh.vertexCount; v++) {
          // Draw unconditionally so both arms consume the same sequence:
          // uniform +/-2.6 px is sd 1.5 — the honest claim below, exactly.
          lm[v * 2] = truthP[v * 2] + rnd() * 5.2;
          lm[v * 2 + 1] = truthP[v * 2 + 1] + rnd() * 5.2;
          sig[v] = 1.5;
          if (subset.has(v)) {
            sig[v] = 1.5 * 7;
            vis[v] = 0.5;
            if (!subsetPresent) { lm[v * 2] = NaN; lm[v * 2 + 1] = NaN; }
          }
        }
        const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30 });
        assert.ok(r.tracked, `frame ${f} refused: ${r.reason}`);
        if (Number.isFinite(r.sigmaMm)) { sSum += r.sigmaMm; n++; }
      }
      assert.ok(n >= 10, `only ${n} frames carried a covariance`);
      return sSum / n;
    };
    const withInflated = run(true);
    const without = run(false);
    const ratio = withInflated / without;
    assert.ok(ratio > 0.9 && ratio < 1.1,
      `200 deliberately-inflated landmarks moved the predicted sigma by ${ratio.toFixed(3)}x ` +
      `(${withInflated.toFixed(3)} vs ${without.toFixed(3)} mm) — ` +
      'the variance factor is reading bias guards as miscalibration again');
  });

  it('a frame with no calibration-eligible landmarks falls back to the pooled estimate', () => {
    // Visibility 0.5 everywhere: every sigma fails the eligibility cut, and
    // the estimator must fall back to the pooled population rather than
    // divide nothing by nothing — the sabotage that forces the calibrated
    // branch unconditionally returns a zero variance factor here, and a
    // confident zero sigma is exactly the wrong failure.
    const state = createTracker(model, { smooth: false, rigidity });
    let st = 0xfa11;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
    const truthP = projectAll(poseAt(0));
    let sSum = 0, n = 0;
    for (let f = 0; f < 10; f++) {
      const lm = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + rnd() * 5.2;
      const sig = new Float64Array(mesh.vertexCount).fill(1.5);
      const vis = new Float64Array(mesh.vertexCount).fill(0.5);
      const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30 });
      assert.ok(r.tracked, `frame ${f} refused: ${r.reason}`);
      if (Number.isFinite(r.sigmaMm)) { sSum += r.sigmaMm; n++; }
    }
    const sigma = sSum / n;
    assert.ok(sigma > 0.02 && sigma < 5,
      `with zero eligible landmarks the predicted sigma reads ${sigma.toFixed(4)} mm — ` +
      'the pooled fallback is not engaging');
  });

  it('the production visibility keeps the frontal solve intact', () => {
    // The cull thresholds were sized on the fixture's 1-hiddenness units;
    // production visibility is a facing COSINE from the raster, and at
    // frontal the face-oval rim reads near zero facing — permanently culled.
    // The review measured the closed production loop: 436 -> ~386
    // correspondences at frontal, accuracy within noise (0.148 vs 0.145 mm).
    // This pins that loop — the real estimator's own output into track() —
    // so a ramp-constant or facing-formula drift cannot silently reshape the
    // frontal solve again.
    const unc = createUncertainty(mesh.vertexCount);
    const state = createTracker(model, { smooth: false, rigidity });
    const truth = poseAt(0);
    const truthP = projectAll(truth);
    let st = 0xcafe;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
    let corr = 0, n = 0;
    for (let i = 0; i < 10; i++) {
      const lm = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + rnd() * 1;
      const est = estimateSigma(unc, {
        landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
        intrinsics: K, pose: i === 0 ? null : truth,
      });
      const r = track(state, {
        landmarks: lm, sigmaPx: est.sigmaPx, visibility: i === 0 ? null : est.visibility,
        intrinsics: K, dt: 1 / 30,
      });
      assert.ok(r.tracked, `closed-loop frame ${i} refused: ${r.reason}`);
      if (i > 0) { corr += r.correspondences; n++; }
    }
    const mean = corr / n;
    assert.ok(mean > 355 && mean < 415,
      `frontal closed-loop solve carries ${mean.toFixed(0)} correspondences — ` +
      'the visibility cull moved against the measured 386-of-436 baseline');
  });

  it('the motion prior joins the normal equations, not just the gradient', () => {
    // The review's sharpest finding: an implementation that adds the prior to
    // the GRADIENT and the cost but forgets the HESSIAN still converges to the
    // correct MAP pose — LM tolerates a wrong metric — so it passes every
    // pose-based assertion while shipping a covariance that omits the prior's
    // information entirely. Two assertions, because neither alone can tell
    // the difference:
    //   (a) stationarity, on a frame whose prediction DISAGREES with the
    //       measurements by several sigma, so the pin cannot pass by both
    //       terms being zero (with the vacuity guard asserting exactly that);
    //   (b) the covariance must CONTRACT by the share the solve reports.
    // The g-only sabotage passes (a) and fails (b); an H-only one fails (a).
    const truth = poseAt(0);
    const truthP = projectAll(truth);
    let st = 0x9a1d;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
    const lm = new Float64Array(mesh.vertexCount * 2);
    for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + rnd() * 1.2;
    const sig = new Float64Array(mesh.vertexCount).fill(1.4);
    const cs = buildCorrespondences(lm, sig, mesh.vertexCount, undefined, 12);

    // A prediction deliberately 4 mm and 1.5 degrees off the measurements.
    const predicted = poseClone(truth);
    predicted.t[0] += 4;
    m3mul(predicted.R, expSO3(m3(), v3(0, (1.5 * Math.PI) / 180, 0)), truth.R);
    orthonormalize(predicted.R, predicted.R);
    // Information: 0.5 deg and 1.5 mm one-sigma, diagonal.
    const information = new Float64Array(36);
    const sr = (0.5 * Math.PI) / 180, sm = 1.5;
    for (let i = 0; i < 3; i++) information[i * 6 + i] = 1 / (sr * sr);
    for (let i = 3; i < 6; i++) information[i * 6 + i] = 1 / (sm * sm);

    const withPrior = refinePnP(model.positions, cs, K, predicted,
      { wantCovariance: true, prior: { pose: predicted, information } });
    const without = refinePnP(model.positions, cs, K, truth, { wantCovariance: true });
    assert.ok(withPrior.covariance && without.covariance, 'both solves must carry a covariance');

    // (b) is measured on its OWN fixture, deliberately: a prior that
    // disagrees with the measurements drags the pose off the measurement
    // optimum, which inflates the residuals, which moves the variance factor
    // — so a covariance comparison across THAT pair confounds the prior's
    // information with its pull. Centring a strong prior exactly on the
    // prior-less solution isolates the one variable: identical residuals,
    // identical variance factor, and the only difference in the covariance
    // is the information the prior added to the Hessian.
    const centred = new Float64Array(36);
    const csr = (0.02 * Math.PI) / 180, csm = 0.05;
    for (let i = 0; i < 3; i++) centred[i * 6 + i] = 1 / (csr * csr);
    for (let i = 3; i < 6; i++) centred[i * 6 + i] = 1 / (csm * csm);
    const atOptimum = refinePnP(model.positions, cs, K, without.pose,
      { wantCovariance: true, prior: { pose: without.pose, information: centred } });
    assert.ok(atOptimum.covariance, 'the centred-prior solve must carry a covariance');

    // (a) stationarity of the FUSED objective at the returned pose.
    const rp = new Float64Array(6);
    logSO3(rp, m3mul(m3(), withPrior.pose.R, m3transpose(m3(), predicted.R)));
    rp[3] = withPrior.pose.t[0] - predicted.t[0];
    rp[4] = withPrior.pose.t[1] - predicted.t[1];
    rp[5] = withPrior.pose.t[2] - predicted.t[2];
    let priorGrad = 0;
    for (let a = 0; a < 6; a++) {
      let g = 0;
      for (let b = 0; b < 6; b++) g += information[a * 6 + b] * rp[b];
      priorGrad += g * g;
    }
    priorGrad = Math.sqrt(priorGrad);
    // The vacuity guard: the prior's own gradient contribution must be LARGE,
    // or "the total gradient is small" would be a statement about nothing.
    assert.ok(priorGrad > 50,
      `the prior's gradient at the solution is only ${priorGrad.toFixed(1)} — ` +
      'the fixture does not disagree with the measurements, so it convicts nothing');

    // The measurement gradient at the returned pose, accumulated exactly as
    // the solver does. The FUSED gradient must vanish — that, and only that,
    // is the statement "the converged solve is the MAP estimate". Without
    // this the block above is a vacuity guard with nothing behind it, which
    // is what the prior-in-Hessian-but-not-gradient sabotage walked through.
    const gMeas = new Float64Array(6);
    {
      const Jm = new Float64Array(12);
      const camv = v3(), rotv = v3(), uvv = new Float64Array(2);
      const R = withPrior.pose.R;
      for (const c of cs) {
        const i = c.vertex;
        const x = model.positions[i * 3], y = model.positions[i * 3 + 1], z = model.positions[i * 3 + 2];
        rotv[0] = R[0] * x + R[1] * y + R[2] * z;
        rotv[1] = R[3] * x + R[4] * y + R[5] * z;
        rotv[2] = R[6] * x + R[7] * y + R[8] * z;
        camv[0] = rotv[0] + withPrior.pose.t[0];
        camv[1] = rotv[1] + withPrior.pose.t[1];
        camv[2] = rotv[2] + withPrior.pose.t[2];
        if (!project(uvv, K, camv)) continue;
        const wgt = 1 / c.sigmaPx;
        const r0 = (uvv[0] - c.u) * wgt, r1 = (uvv[1] - c.v) * wgt;
        const [, drho] = PNP_DEFAULTS.loss.eval(r0 * r0 + r1 * r1);
        dProjDPose(Jm, 0, K, camv, rotv);
        for (let a = 0; a < 12; a++) Jm[a] *= wgt;
        for (let a = 0; a < 6; a++) gMeas[a] += drho * (Jm[a] * r0 + Jm[6 + a] * r1);
      }
    }
    let fusedGrad = 0;
    for (let a = 0; a < 6; a++) {
      let g = gMeas[a];
      for (let b = 0; b < 6; b++) g += information[a * 6 + b] * rp[b];
      fusedGrad += g * g;
    }
    fusedGrad = Math.sqrt(fusedGrad);
    assert.ok(fusedGrad < priorGrad * 0.02,
      `the fused gradient at the returned pose is ${fusedGrad.toFixed(2)} against the prior's own ` +
      `${priorGrad.toFixed(1)} — the solve did not converge to the MAP point, so the prior is ` +
      'not being balanced against the measurements');

    // (b) the returned covariance must BE the fused one: C^-1/VF minus the
    // measurement normal matrix must equal the prior's information.
    //
    // Checked by reconstruction rather than by a predicted contraction ratio.
    // A first draft asserted the sigma falls by sqrt(1 - trace share) and was
    // wrong by 4x — a trace ratio cannot predict the contraction of a COUPLED,
    // ill-conditioned Hessian, because the inverse's diagonal is dominated by
    // the worst-determined direction and an isotropic prior regularises that
    // direction hardest. (The measured contraction was far LARGER than the
    // share suggested, which is the prior working, not failing.) The identity
    // below has no such model in it.
    const sigmaMmOf = (C: Float64Array) => Math.sqrt(Math.max(0, (C[21] + C[28] + C[35]) / 3));
    assert.ok(Number.isFinite(atOptimum.priorShareMm) && atOptimum.priorShareMm > 0.05,
      `priorShareMm reads ${atOptimum.priorShareMm} — the prior is not in the normal equations at all`);
    assert.ok(!Number.isFinite(without.priorShareMm),
      'a prior-less solve must report NaN share, not a number');

    const Hmeas = Float64Array.from(without.covariance!);
    assert.ok(invertSymmetric(Hmeas, 6), 'the prior-less covariance must invert');
    for (let i = 0; i < 36; i++) Hmeas[i] *= without.varianceFactor;
    const expected = Float64Array.from(Hmeas);
    for (let i = 0; i < 36; i++) expected[i] += centred[i];
    assert.ok(invertSymmetric(expected, 6), 'the fused normal matrix must invert');
    for (let i = 0; i < 36; i++) expected[i] *= atOptimum.varianceFactor;

    const got = sigmaMmOf(atOptimum.covariance!);
    const want = sigmaMmOf(expected);
    const measOnly = sigmaMmOf(without.covariance!);
    assert.ok(got < measOnly * 0.9,
      `the prior did not contract the covariance at all (${measOnly.toFixed(5)} -> ${got.toFixed(5)} mm)`);
    assert.ok(got < want * 1.05 && got > want * 0.95,
      `fused sigma ${got.toFixed(5)} mm against the reconstructed fusion's ${want.toFixed(5)} — ` +
      'the returned covariance is not VF * (H_meas + prior)^-1, so the prior reached ' +
      'the gradient but not the Hessian');
  });

  it('the motion prior steadies the tilted solve without lagging the head', () => {
    // The rank's whole claim, as an outcome pin on the two quantities the
    // wearer feels: frame-to-frame jitter at a 40-degree tilt (where the
    // solve is weakest and the residual complaint lives) must fall
    // materially, and a real head turn through that band must not lag.
    // Both arms run the IDENTICAL noise realisation (one seed, two trackers)
    // and are compared only on frames both of them tracked — the sweep's own
    // instrument lesson, after an unpaired version manufactured a phantom
    // 15-23% accuracy cost out of mismatched frame sets.
    const measure = (priorOn: boolean, yawOf: (i: number) => number) => {
      const state = createTracker(model, { smooth: false, rigidity, motionPrior: priorOn });
      const frame = makeSession(0x51ee, 0.4);
      const out: { t: Float64Array | null }[] = [];
      for (let i = 0; i < 120; i++) {
        const { lm, sig, vis } = frame(yawOf(i));
        const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30 });
        out.push({ t: r.tracked && r.rawPose ? Float64Array.from(r.rawPose.t) : null });
      }
      return out;
    };
    const jitterOf = (a: { t: Float64Array | null }[], b: { t: Float64Array | null }[]) => {
      const steps: number[] = [];
      let prev: Float64Array | null = null;
      for (let i = 0; i < a.length; i++) {
        if (!a[i].t || !b[i].t) { prev = null; continue; }
        if (prev && i > 15) {
          steps.push(Math.hypot(a[i].t![0] - prev[0], a[i].t![1] - prev[1], a[i].t![2] - prev[2]));
        }
        prev = a[i].t;
      }
      steps.sort((x, y) => x - y);
      return steps[steps.length >> 1];
    };
    const still = () => 40;
    const on = measure(true, still);
    const off = measure(false, still);
    const jOn = jitterOf(on, off);
    const jOff = jitterOf(off, on);
    assert.ok(jOn < jOff * 0.8,
      `tilted-rest jitter ${jOff.toFixed(4)} -> ${jOn.toFixed(4)} mm with the prior — ` +
      'measured -40% median across 5 seeds; a reading this weak means the prior is not carrying');

    // And the head actually moving through the same band: the prior may not
    // cost accuracy there. (The sweep measures the lag itself at 0.35 frames;
    // this pins the accuracy half, which is the part a unit test can hold
    // without a sub-frame correlation instrument.)
    const turning = (i: number) => 40 + 15 * Math.sin((2 * Math.PI * 1.0 * i) / 30);
    const onT = measure(true, turning);
    const offT = measure(false, turning);
    let errOn = 0, errOff = 0, n = 0;
    for (let i = 0; i < onT.length; i++) {
      if (!onT[i].t || !offT[i].t) continue;
      const truth = poseAt(turning(i));
      errOn += Math.hypot(onT[i].t![0] - truth.t[0], onT[i].t![1] - truth.t[1], onT[i].t![2] - truth.t[2]);
      errOff += Math.hypot(offT[i].t![0] - truth.t[0], offT[i].t![1] - truth.t[1], offT[i].t![2] - truth.t[2]);
      n++;
    }
    assert.ok(n > 60, `only ${n} frames tracked in both arms — the turn fixture lost the face`);
    assert.ok(errOn < (errOff / n) * n * 1.05,
      `turning through the tilt band costs ${((errOn / errOff - 1) * 100).toFixed(1)}% accuracy with the prior — ` +
      'the constant-velocity prior is dragging a real head turn');
  });

  it('the motion prior does not fit across darkness it never watched', () => {
    // A sub-reset gap (under lostSecondsBeforeReset) leaves the velocity ring
    // INTACT and straddling the gap, so the frame after recovery would fit a
    // constant velocity across half a second nobody observed — and a head
    // that reversed inside the gap gets a confident pull the wrong way. The
    // window's acceleration functional prices a long lever arm (measured 3.3x
    // for a 0.4 s gap), but that pricing assumes constant acceleration, which
    // a reversal is not, so the window is TRIMMED to the contiguous tail.
    // Pinned on the observable: no prior share until the ring has refilled
    // with frames whose motion something actually watched.
    const state = createTracker(model, { smooth: false, rigidity, motionPrior: true });
    const frame = makeSession(0x6a9, 0.3);
    const shares: number[] = [];
    for (let i = 0; i < 40; i++) {
      const { lm, sig, vis } = frame(0);
      // Frame 20 arrives 0.4 s late: a stall INSIDE the reset threshold, so
      // nothing else in the tracker clears the ring.
      const dt = i === 20 ? 0.4 : 1 / 30;
      const r = track(state, { landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt });
      shares.push(r.priorShareRot);
    }
    // The fixture-sanity precondition: the prior must be genuinely running
    // before the gap, or nothing below convicts anything.
    assert.ok(Number.isFinite(shares[19]) && shares[19] > 1e-3,
      `the prior was not carrying before the gap (share ${shares[19]}) — the fixture proves nothing`);
    // The late frame itself: the prior extrapolates 0.4 s, and the window's
    // acceleration functional prices that lever arm, so the prior goes INERT
    // rather than absent — a continuous degradation, not a cliff. Two orders
    // of magnitude is the measured collapse; the bar is one.
    assert.ok(shares[20] < shares[19] / 10,
      `the frame that arrived 0.4 s late kept ${(shares[20] / shares[19]).toFixed(3)} of its ` +
      'resting prior strength — the acceleration functional is not pricing the extrapolation');
    // The frame AFTER recovery is the one the first design missed and the
    // review caught: its fit window still straddles the gap, and the two-point
    // form would have claimed MORE confidence there than in steady state. The
    // window trim must refuse it outright.
    assert.ok(!Number.isFinite(shares[21]),
      `the frame after the gap carried a prior (share ${shares[21]}) — its fit window ` +
      'still straddles darkness nobody watched');
    assert.ok(Number.isFinite(shares[25]) && shares[25] > 1e-3,
      `the prior never came back after the gap (share ${shares[25]}) — the window trim is not refilling`);
  });

  it('the redescending schedule reaches the solve, and only the solve that carries the prior', () => {
    // Two claims in one fixture, because they are the same mechanism seen
    // from both sides. The schedule must actually change the weight a
    // poorly-seen landmark carries (or the rank does nothing), and it must
    // NOT reach the cold retry or the basin audit — both of those exist to
    // escape a bad basin, and a non-convex kernel is how you create one.
    // They reuse this very correspondence array, so the guard cannot be
    // "we pass a different array"; it is the solve-level `redescending`
    // switch, and this pins that switch.
    const truth = poseAt(0);
    const truthP = projectAll(truth);
    let st = 0xb00b;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
    const lm = new Float64Array(mesh.vertexCount * 2);
    for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + rnd() * 1.2;
    const sig = new Float64Array(mesh.vertexCount).fill(1.4);
    const cs = buildCorrespondences(lm, sig, mesh.vertexCount, undefined, 12);
    // Displace ONE landmark far enough to be an obvious outlier, and give it
    // a low visibility so the schedule puts it at the redescending end.
    const victim = cs[Math.floor(cs.length / 2)];
    victim.u += 40;
    victim.lossAlpha = BARRON_ALPHA_LOW;

    const withSchedule = refinePnP(model.positions, cs, K, truth,
      { wantCovariance: true, redescending: true });
    const withoutSchedule = refinePnP(model.positions, cs, K, truth, { wantCovariance: true });

    // The outlier's pull differs between the two — the fixture-sanity
    // precondition, without which everything below is vacuous.
    const pull = (r: ReturnType<typeof refinePnP>) => Math.hypot(
      r.pose.t[0] - truth.t[0], r.pose.t[1] - truth.t[1], r.pose.t[2] - truth.t[2],
    );
    assert.ok(pull(withoutSchedule) > 1e-4,
      `fixture sanity: the outlier moved the Huber solve only ${pull(withoutSchedule)} mm — nothing to reject`);
    assert.ok(pull(withSchedule) < pull(withoutSchedule),
      `the redescending solve was pulled ${pull(withSchedule)} mm against Huber's ` +
      `${pull(withoutSchedule)} — the schedule is not reaching the solver`);

    // And the guard: the SAME stamped array solved without the switch must
    // be bit-identical to one whose alphas were never set at all.
    const stripped = cs.map((c) => ({ ...c, lossAlpha: undefined }));
    const asHuber = refinePnP(model.positions, stripped, K, truth, { wantCovariance: true });
    assert.equal(withoutSchedule.pose.t[0], asHuber.pose.t[0],
      'a stamped correspondence array changed a solve that did not ask for the schedule — ' +
      'the cold retry and the basin audit run through exactly this path');
    assert.equal(withoutSchedule.rmsPx, asHuber.rmsPx, 'stamped alphas leaked into the reported rms');
    assert.equal(withoutSchedule.inliers, asHuber.inliers, 'stamped alphas leaked into the inlier count');
  });

  it('the redescending schedule stays off by default — the measurement said so', () => {
    // Not caution: a verdict. The schedule makes the partially-hallucinated
    // case it was built for WORSE (11.365 -> 12.484 mm at 40 degrees, paired,
    // 5 seeds), because a redescending estimator assumes the bad points are a
    // minority it can shed and the hallucinated far side is a coherent
    // majority within its band. This pins the default so the verdict cannot
    // be undone by an edit that looks like a tidy-up; the ledger row carries
    // the numbers.
    assert.equal(TRACKER_DEFAULTS.redescending, false,
      'redescending is on by default — it measured WORSE on its own target case');
    assert.equal(PNP_DEFAULTS.redescending, false,
      'the solver honours per-point kernels by default — every cold path would inherit them');
  });

  it('redescending earns its keep against a MINORITY of bad landmarks', () => {
    // The regime the machinery is kept for, and the reason it is not deleted:
    // when the wrong points really are a minority — an occluding hand rather
    // than a hallucinated half-face — the schedule beats Huber. Measured 32%
    // at frontal across 5 seeds; this pins the mechanism on one deterministic
    // seed with a margin well inside that.
    const run = (redescending: boolean) => {
      const unc = createUncertainty(mesh.vertexCount);
      const state = createTracker(model, {
        smooth: false, rigidity, motionPrior: true, redescending,
      });
      const truth = poseAt(0);
      const truthP = projectAll(truth);
      let st = 0x0cc1;
      const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
      const gauss = () => { let u = 0; while (u === 0) u = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
      // A coherent patch of landmarks thrown off together — a hand, not noise.
      const order = [...Array(mesh.vertexCount).keys()]
        .sort((a, b) => mesh.positions[a * 3] - mesh.positions[b * 3]);
      const hidden = new Set(order.slice(0, Math.round(mesh.vertexCount * 0.15)));
      let prev: Pose | null = null;
      const errs: number[] = [];
      const ok: boolean[] = [];
      for (let i = 0; i < 60; i++) {
        const lm = new Float64Array(mesh.vertexCount * 2);
        const ox = gauss() * 3, oy = gauss() * 3;
        for (let v = 0; v < mesh.vertexCount; v++) {
          lm[v * 2] = truthP[v * 2] + gauss() * 0.5;
          lm[v * 2 + 1] = truthP[v * 2 + 1] + gauss() * 0.5;
          if (hidden.has(v) && i >= 10) { lm[v * 2] += 25 + ox; lm[v * 2 + 1] += 18 + oy; }
        }
        const est = estimateSigma(unc, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: K, pose: prev,
        });
        const r = track(state, {
          landmarks: lm, sigmaPx: est.sigmaPx, visibility: est.visibility, intrinsics: K, dt: 1 / 30,
        });
        ok.push(!!(r.tracked && r.rawPose));
        if (r.tracked && r.rawPose) {
          prev = r.rawPose;
          errs.push(Math.hypot(
            r.rawPose.t[0] - truth.t[0], r.rawPose.t[1] - truth.t[1], r.rawPose.t[2] - truth.t[2],
          ));
        } else errs.push(NaN);
      }
      return { errs, ok };
    };
    const off = run(false);
    const on = run(true);
    // Compared only on frames BOTH arms tracked — an unpaired version of this
    // comparison already manufactured a phantom 15-23% effect elsewhere in
    // this campaign out of nothing but mismatched frame sets.
    const both = off.ok.map((v, i) => v && on.ok[i]);
    const med = (e: number[]) => {
      const s = e.filter((v, i) => both[i] && Number.isFinite(v)).sort((a, b) => a - b);
      return s[s.length >> 1];
    };
    const eOff = med(off.errs);
    const eOn = med(on.errs);
    assert.ok(both.filter(Boolean).length > 40,
      `only ${both.filter(Boolean).length} frames tracked in both arms — the occlusion fixture lost the face`);
    assert.ok(eOff > 0.5,
      `fixture sanity: the occluder moved the Huber solve only ${eOff.toFixed(3)} mm — nothing to improve on`);
    assert.ok(eOn < eOff * 0.9,
      `with a minority of displaced landmarks the schedule scored ${eOn.toFixed(3)} mm against Huber's ` +
      `${eOff.toFixed(3)} — the one regime it is kept for is not working`);
  });

  it('the variance factor is calibrated only where the schedule is flat', () => {
    // A hard invariant, not a tuning preference: every landmark the variance
    // factor calibrates from must sit at the schedule's fixed upper end, or
    // the factor rank 4's motion prior consumes would drift with the
    // schedule instead of describing the sigma stream. Asserted rather than
    // written down, because a future sweep of the band would otherwise break
    // it silently.
    assert.ok(BARRON_VIS_HI <= VF_CAL_MIN_VIS,
      `BARRON_VIS_HI ${BARRON_VIS_HI} is above VF_CAL_MIN_VIS ${VF_CAL_MIN_VIS} — ` +
      'the variance factor would be estimated from landmarks whose kernel the schedule is still varying');
    // ...and that the invariant has teeth: a landmark at the calibration cut
    // must genuinely be at the flat end.
    const alphaAt = (v: number) => BARRON_ALPHA_HIGH + (BARRON_ALPHA_LOW - BARRON_ALPHA_HIGH)
      * (1 - smoothstep(BARRON_VIS_LO, BARRON_VIS_HI, v));
    assert.ok(Math.abs(alphaAt(VF_CAL_MIN_VIS) - BARRON_ALPHA_HIGH) < 1e-9,
      `a landmark at the calibration cut carries alpha ${alphaAt(VF_CAL_MIN_VIS)}, not ${BARRON_ALPHA_HIGH}`);
    assert.ok(alphaAt(BARRON_VIS_LO) < BARRON_ALPHA_LOW + 1e-9,
      'the schedule does not reach its redescending end at the bottom of the band');
  });

  it('a deep turn is not mistaken for a stranger walking in', () => {
    // The wearer's report, as a test: "the glasses disappear around the 50
    // degrees mark". The reprojection gate means "these landmarks do not
    // describe this face", and it was averaging every residual flat — so the
    // far half-face MediaPipe invents, which the estimator has already muted
    // to nearly zero weight, dragged the mean past the threshold and an
    // ordinary turn was refused as a stranger. Past holdFrames refusals the
    // frame is hidden, which is what the wearer sees.
    //
    // Both halves are pinned together, because fixing one by breaking the
    // other is exactly what the first attempt did (restricting the mean to
    // inliers left it with no support on a garbage frame, and the suite's
    // existing pin caught it accepting 200 px of scatter).
    const unc = createUncertainty(mesh.vertexCount);
    const state = createTracker(model, { smooth: false, rigidity, motionPrior: true });
    let st = 0x5017;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
    const gauss = () => { let u = 0; while (u === 0) u = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
    let prev: Pose | null = null;
    let refused = 0;
    let held = 0;
    let last: { lm: Float64Array; sigmaPx: Float64Array; visibility: Float64Array;
      pose: Pose | null; rmsPx: number } | null = null;
    for (let i = 0; i < 60; i++) {
      // Turn out to 60 degrees and hold, with the far side invented — the
      // regime the wearer met.
      const yaw = Math.min(60, i * 2);
      const here = poseAt(yaw);
      const truthP = projectAll(here);
      const hallP = projectAll(poseAt(yaw * 0.55));
      const s = Math.sin((yaw * Math.PI) / 180);
      const lm = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount; v++) {
        const h = Math.min(1, Math.max(0, -(mesh.positions[v * 3] / 80) * s * 2.5));
        lm[v * 2] = (1 - h) * truthP[v * 2] + h * hallP[v * 2] + gauss() * 0.5;
        lm[v * 2 + 1] = (1 - h) * truthP[v * 2 + 1] + h * hallP[v * 2 + 1] + gauss() * 0.5;
      }
      const est = estimateSigma(unc, {
        landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
        intrinsics: K, pose: prev,
      });
      const r = track(state, {
        landmarks: lm, sigmaPx: est.sigmaPx, visibility: est.visibility, intrinsics: K, dt: 1 / 30,
      });
      if (r.rawPose) prev = r.rawPose;
      if (i >= 25) { held++; if (!r.rawPose) refused++; }
      last = {
        lm, sigmaPx: est.sigmaPx, visibility: est.visibility,
        pose: r.rawPose ?? prev, rmsPx: r.rmsPx,
      };
    }
    assert.equal(refused, 0,
      `${refused} of ${held} frames at a held 60-degree turn were refused as "not this face" — ` +
      'the gate is averaging residuals the solve already muted');

    // The outcome above is necessary but not sufficient: a fixture that never
    // reaches the threshold would pass it while the statistic stayed wrong,
    // and the flat-mean sabotage proved exactly that. So pin the STATISTIC.
    // Rebuild this frame's correspondences the way track() does and compute
    // both means at the pose it returned: the weighted one the gate now uses
    // must sit materially below the flat one it used to, because the far side
    // is muted. Measured on the production loop: 8.67 against 11.70 at 55
    // degrees, a ratio of 0.74.
    {
      const eff = new Float64Array(mesh.vertexCount);
      for (let v = 0; v < mesh.vertexCount; v++) {
        eff[v] = rigidity[v] * smoothstep(VIS_CULL_LO, VIS_CULL_HI, last!.visibility[v]);
      }
      const cs = buildCorrespondences(last!.lm, last!.sigmaPx, mesh.vertexCount, eff, 12);
      const uvv = new Float64Array(2), camv = v3();
      const R = last!.pose!.R;
      let flatSum = 0, flatN = 0;
      for (const c of cs) {
        const i2 = c.vertex;
        const x = model.positions[i2 * 3], y = model.positions[i2 * 3 + 1], z = model.positions[i2 * 3 + 2];
        camv[0] = R[0] * x + R[1] * y + R[2] * z + last!.pose!.t[0];
        camv[1] = R[3] * x + R[4] * y + R[5] * z + last!.pose!.t[1];
        camv[2] = R[6] * x + R[7] * y + R[8] * z + last!.pose!.t[2];
        if (!project(uvv, K, camv)) continue;
        const e = Math.hypot(uvv[0] - c.u, uvv[1] - c.v);
        flatSum += e * e; flatN++;
      }
      const flat = Math.sqrt(flatSum / flatN);
      // The SHIPPED statistic, not one this test recomputes: an earlier draft
      // derived both means locally, which made the assertion a statement
      // about the fixture that no sabotage of the implementation could fail.
      const shipped = last!.rmsPx;
      assert.ok(flat > 3,
        `fixture sanity: the flat mean is only ${flat.toFixed(2)} px at a deep turn — ` +
        'the invented far side is not producing the residuals this fix is about');
      assert.ok(shipped < flat * 0.92,
        `the solver reported ${shipped.toFixed(2)} px against a flat mean of ${flat.toFixed(2)} — ` +
        'residuals are not being weighted by how much the solve listened to them');
    }

    // The same gate must still do its actual job. A frame of pure scatter has
    // no trustworthy landmarks at all, and the weighted mean keeps every
    // residual in the sum precisely so it can still say how far off it is.
    const fresh = createTracker(model, { smooth: false, rigidity });
    const truthP = projectAll(poseAt(0));
    let accepted = 0;
    for (let i = 0; i < 6; i++) {
      const lm = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + gauss() * 200;
      const sig = new Float64Array(mesh.vertexCount).fill(1.4);
      const r = track(fresh, { landmarks: lm, sigmaPx: sig, intrinsics: K, dt: 1 / 30 });
      if (r.rawPose) accepted++;
    }
    assert.equal(accepted, 0,
      `${accepted} of 6 garbage frames were accepted — widening the gate for deep turns ` +
      'has cost it the job it exists for');

    // **A SECOND FACE, which is the case scatter cannot stand in for.** The
    // landed-code review found this: scatter is ONE population, and the
    // failure needs two. A weighted mean over a mixture is the geometric
    // mean of its parts, so it is dominated by whichever face the solve
    // fitted and reported 10.6-13.0 px — accepted — while the pose was up to
    // 57 mm wrong. Cold, because that is when the solve is free to land on
    // the wrong person; warm, the prior holds it on the wearer.
    for (const [frac, iyaw, itx, itz] of [
      [0.45, 10, 30, 470], [0.45, 15, 35, 460], [0.60, 12, 30, 470],
    ] as const) {
      const cold = createTracker(model, { smooth: false, rigidity, motionPrior: true });
      const uncCold = createUncertainty(mesh.vertexCount);
      const mine = projectAll(poseAt(0));
      const theirs = projectAll({
        R: poseAt(iyaw).R, t: Float64Array.of(itx, 0, itz),
      });
      let took = 0;
      for (let i = 0; i < 10; i++) {
        const lm = new Float64Array(mesh.vertexCount * 2);
        for (let v = 0; v < mesh.vertexCount; v++) {
          const src = rnd() < frac ? theirs : mine;
          lm[v * 2] = src[v * 2] + gauss() * 0.5;
          lm[v * 2 + 1] = src[v * 2 + 1] + gauss() * 0.5;
        }
        const e2 = estimateSigma(uncCold, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: K, pose: null,
        });
        const r2 = track(cold, {
          landmarks: lm, sigmaPx: e2.sigmaPx, visibility: e2.visibility, intrinsics: K, dt: 1 / 30,
        });
        if (r2.rawPose) took++;
      }
      assert.equal(took, 0,
        `${took} of 10 frames were accepted with a second face taking ${(frac * 100).toFixed(0)}% ` +
        'of the landmarks — the gate is measuring how well it fits SOMEBODY, not whether it is the wearer');
    }
  });

  it('the pixel gates are sized for the camera in front of them, not the one they were measured on', () => {
    // `GROSS_ERROR_PX` (40) and `maxRmsPx` (14) are distances in IMAGE pixels,
    // and every number behind them was measured at 63 degrees on 1280x720.
    // A reprojection error for a fixed physical mistake scales with the focal
    // length, so at 640x360 — half the focal length — the stranger's landmarks
    // land INSIDE a bar sized for a camera twice as sharp. Measured before the
    // scale existed: 20 of 20 of these frames accepted, against 0 of 20 at the
    // reference geometry. `app/main.ts` documents `getUserMedia` renegotiating
    // to 640x480 when another application holds the camera, and `core/camera.ts`
    // records 78.5 degrees measured on a real laptop lid — which is f 441 at a
    // perfectly ordinary 1280x720 and leaked 1 of 20.
    //
    // The deep-turn arm is in the same test deliberately. Tightening a gate is
    // only a fix if the frames it must NOT refuse still pass, and scaling the
    // other way (f 1423, the phone-lap rung) was measured to take that arm from
    // 30 of 30 to 0 of 30 — which is why `pixelGateScale` clamps at 1.
    const smallK = intrinsicsFromFov(640, 360, 63);
    assert.ok(pixelGateScale(smallK) < 0.51 && pixelGateScale(smallK) > 0.49,
      `the 640x360 gate scale is ${pixelGateScale(smallK)}, not the half this test is about`);
    assert.equal(pixelGateScale(K), 1,
      'the reference geometry must scale by exactly 1, or every measured number moves');

    const smallModel = createFaceModel({
      positions: new Float64Array(mesh.positions),
      vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.3),
      shapeCoeffs: new Float64Array(0),
      basisName: 'ground-truth',
      displacementRmsMm: 0, displacementMaxMm: 0,
      intrinsics: smallK, intrinsicsSolved: true,
      scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
      landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
      quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
      pdMm: null, pdSigmaMm: null,
      reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
    });
    const projectSmall = (pose: { R: Float64Array; t: Float64Array }) => {
      const out = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount; v++) {
        const X = mesh.positions[v * 3], Y = mesh.positions[v * 3 + 1], Z = mesh.positions[v * 3 + 2];
        const cx = pose.R[0] * X + pose.R[1] * Y + pose.R[2] * Z + pose.t[0];
        const cy = pose.R[3] * X + pose.R[4] * Y + pose.R[5] * Z + pose.t[1];
        const cz = pose.R[6] * X + pose.R[7] * Y + pose.R[8] * Z + pose.t[2];
        out[v * 2] = smallK.cx + (smallK.f * cx) / cz;
        out[v * 2 + 1] = smallK.cy + (smallK.f * cy) / cz;
      }
      return out;
    };
    let ss = 0x51f3;
    const sr = () => { ss ^= ss << 13; ss ^= ss >>> 17; ss ^= ss << 5; ss >>>= 0; return ss / 4294967296; };
    const sg = () => { let u = 0; while (u === 0) u = sr(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * sr()); };

    // A SECOND FACE, on the small camera. Every frame must be refused.
    for (const [frac, iyaw, itx, itz] of [
      [0.45, 10, 30, 470], [0.60, 12, 30, 470],
    ] as const) {
      const cold = createTracker(smallModel, { smooth: false, rigidity, motionPrior: true });
      const unc = createUncertainty(mesh.vertexCount);
      const mine = projectSmall(poseAt(0));
      const theirs = projectSmall({ R: poseAt(iyaw).R, t: Float64Array.of(itx, 0, itz) });
      let took = 0;
      for (let i = 0; i < 10; i++) {
        const lm = new Float64Array(mesh.vertexCount * 2);
        for (let v = 0; v < mesh.vertexCount; v++) {
          const src = sr() < frac ? theirs : mine;
          lm[v * 2] = src[v * 2] + sg() * 0.5;
          lm[v * 2 + 1] = src[v * 2 + 1] + sg() * 0.5;
        }
        const e = estimateSigma(unc, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: smallK, pose: null,
        });
        if (track(cold, {
          landmarks: lm, sigmaPx: e.sigmaPx, visibility: e.visibility, intrinsics: smallK, dt: 1 / 30,
        }).rawPose) took++;
      }
      assert.equal(took, 0,
        `${took} of 10 frames were accepted at 640x360 with a second face taking `
        + `${(frac * 100).toFixed(0)}% of the landmarks — the pixel gates are still sized `
        + 'for a 1280x720 camera, so a lower-resolution or wider-angle one gets no gate at all');
    }

    // A DEEP TURN on the same camera, which must still be accepted.
    for (const yaw of [55, 70, 80]) {
      const tr = createTracker(smallModel, { smooth: false, rigidity, motionPrior: true });
      const unc = createUncertainty(mesh.vertexCount);
      const truth = projectSmall(poseAt(yaw));
      const hall = projectSmall(poseAt(yaw * 0.55));
      let ok = 0;
      for (let i = 0; i < 10; i++) {
        const lm = new Float64Array(mesh.vertexCount * 2);
        for (let v = 0; v < mesh.vertexCount; v++) {
          const far = mesh.positions[v * 3] < 0;
          lm[v * 2] = (far ? hall[v * 2] : truth[v * 2]) + sg() * 0.5;
          lm[v * 2 + 1] = (far ? hall[v * 2 + 1] : truth[v * 2 + 1]) + sg() * 0.5;
        }
        const e = estimateSigma(unc, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: smallK, pose: null,
        });
        if (track(tr, {
          landmarks: lm, sigmaPx: e.sigmaPx, visibility: e.visibility, intrinsics: smallK, dt: 1 / 30,
        }).rawPose) ok++;
      }
      assert.equal(ok, 10,
        `only ${ok} of 10 frames at ${yaw} degrees of yaw were accepted on a 640x360 camera — `
        + 'the gate scale has bought the second-face case by refusing legitimate deep turns');
    }
  });

  it('the sigma cull cannot hide a second face, and expression is not one', () => {
    // **`gross` was diluted by the cull, one step before the weighting it says
    // it cannot be diluted by.** `buildCorrespondences` drops a landmark whose
    // sigma passes `maxSigmaPx`, and `grossFraction` was counted over what came
    // back — so declaring a landmark uncertain removed it from the statistic
    // that exists to notice it is somewhere else. Measured on the fixture above
    // at 60%: 468 landmarks produced, 49 surviving, 0.102 over the survivors
    // against 0.355 over the frame, accepted at 11.7 mm from the INTRUDER's
    // truth and 47.7 mm from the wearer's.
    //
    // Both directions are pinned here because the obvious repair — counting a
    // culled landmark's ABSENCE as gross — refuses the wearer for talking. Lip
    // landmarks are outside the rigidity map and fully visible at a frontal
    // pose, sustained speech inflates them past the cull, and charging that as
    // "elsewhere" refused 22 of 35 frames on a still head. So a culled landmark
    // counts only when it is actually gross: projected at the solved pose and
    // more than GROSS_ERROR_PX away. A lip moves tens of pixels; a second face's
    // landmarks are fifty off.
    let rs = 0x2f19;
    const rr = () => { rs ^= rs << 13; rs ^= rs >>> 17; rs ^= rs << 5; rs >>>= 0; return rs / 4294967296; };
    const gauss = () => { let u = 0; while (u === 0) u = rr(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rr()); };
    const band = [...Array(mesh.vertexCount).keys()]
      .sort((a, b) => model.positions[a * 3 + 1] - model.positions[b * 3 + 1])
      .slice(0, 71);

    const drive = (frames: Float64Array[]) => {
      const tr = createTracker(model, { smooth: false, rigidity, motionPrior: true });
      const u = createUncertainty(mesh.vertexCount);
      let prev: Pose | null = null;
      let refused = 0;
      let culled = 0;
      for (const lm of frames) {
        const est = estimateSigma(u, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: K, pose: prev,
        });
        for (let v = 0; v < mesh.vertexCount; v++) if (est.sigmaPx[v] > 12) culled++;
        const r = track(tr, {
          landmarks: lm, sigmaPx: est.sigmaPx, visibility: est.visibility,
          intrinsics: K, dt: 1 / 30,
        });
        if (r.rawPose) prev = r.rawPose; else refused++;
      }
      return { refused, culled };
    };

    // TALKING. A still frontal head with the lower face moving every frame. The
    // motion is real and the landmarks DO get culled — the fixture asserts that,
    // or it would pass while testing nothing — but none of it is fifty pixels
    // from where the model puts it, so none of it is another face.
    const frontal = projectAll(poseAt(0));
    const talking: Float64Array[] = [];
    for (let f = 0; f < 30; f++) {
      const lm = new Float64Array(mesh.vertexCount * 2);
      for (let v = 0; v < mesh.vertexCount; v++) {
        lm[v * 2] = frontal[v * 2] + gauss() * 0.5;
        lm[v * 2 + 1] = frontal[v * 2 + 1] + gauss() * 0.5
          + (band.includes(v) ? (f % 2 ? 22 : -22) : 0);
      }
      talking.push(lm);
    }
    const spoke = drive(talking);
    assert.ok(spoke.culled > 0,
      'fixture sanity: the talking fixture culled no landmarks, so it cannot show '
      + 'whether a culled landmark is charged as a second face');
    assert.equal(spoke.refused, 0,
      `${spoke.refused} of ${talking.length} frames were refused on a still frontal face whose `
      + 'lower face was moving. Expression is being charged as "elsewhere" — past holdFrames '
      + 'that hides the glasses, which is the wearer-visible bug this gate already caused once.');
  });

  it('no strip may span the midline — a landmark cannot march across the face', () => {
    // A strip is "which vertex is on the contour at this height", and a row
    // through the midline spans both cheeks: the perpendicularity test could
    // pick the FAR one and march a landmark clean across the face. That is
    // how a 103 mm "slide" appeared beside an honest 34.6 mm mean when the
    // strips were first measured. The forehead crown and the point of the
    // chin get no strip at all — their contour slides vertically if at all,
    // so the fixed correspondence is the right one for them.
    const strips = silhouetteStrips(mesh);
    for (const s of strips) {
      let mn = Infinity, mx = -Infinity;
      for (const v of s.candidates) {
        mn = Math.min(mn, mesh.positions[v * 3]);
        mx = Math.max(mx, mesh.positions[v * 3]);
      }
      assert.ok(!(mn < -5 && mx > 5),
        `landmark ${s.landmark} (x = ${mesh.positions[s.landmark * 3].toFixed(1)}) has a strip ` +
        `spanning x ${mn.toFixed(1)} to ${mx.toFixed(1)} — it can march to the other side of the face`);
      // And every candidate must be on the landmark's own side.
      const side = Math.sign(mesh.positions[s.landmark * 3]);
      for (const v of s.candidates) {
        assert.equal(Math.sign(mesh.positions[v * 3]) || side, side,
          `landmark ${s.landmark} has a candidate at x = ${mesh.positions[v * 3].toFixed(1)} across the midline`);
      }
    }
    // The exclusion must be narrow: it removes the two midline landmarks and
    // nothing else, or the fix has quietly disarmed the rank.
    assert.ok(strips.length >= 32,
      `only ${strips.length} strips survive the midline cut — it is excluding more than the crown and the chin`);
  });

  it('landmark marching follows the contour the oval landmarks actually mark', () => {
    // The one BIAS fix in the tracker, and it needs a fixture nothing else
    // here provides. Every other fixture blends vertices toward a wrong-yaw
    // projection, which models the far-side INVENTION but has no sliding
    // silhouette — so it cannot state rank 6's premise, let alone test it.
    //
    // Here the oval landmarks are placed on the mesh's REAL contour for the
    // pose (the strip vertex whose normal is most perpendicular to the view
    // ray) and everything else is exact. That is the defect stated precisely:
    // the landmark is honest, and the fixed correspondence it is matched to
    // is what is wrong. Measured on the template, the true contour vertex has
    // moved for 26 of 36 oval landmarks by 40 degrees, 34.6 mm on average.
    const strips = silhouetteStrips(mesh);
    assert.ok(strips.length > 30, `only ${strips.length} oval strips were built`);

    // Fixture-sanity precondition: the contour must genuinely slide, or the
    // comparison below is a statement about nothing.
    const truth = poseAt(45);
    let slid = 0;
    for (const strip of strips) if (marchTruth(truth, strip) !== marchTruth(poseAt(0), strip)) slid++;
    assert.ok(slid > 15,
      `the contour vertex moved for only ${slid} of ${strips.length} oval landmarks at 45 degrees — ` +
      'the fixture does not exhibit the bias this fix exists for');

    const run = (marching: boolean, seed: number) => {
      const state = createTracker(model, {
        smooth: false, rigidity, motionPrior: true,
        ovalStrips: marching ? strips : null,
      });
      const unc = createUncertainty(mesh.vertexCount);
      let st = seed;
      const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
      const gauss = () => { let u = 0; while (u === 0) u = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
      let prev: Pose | null = null;
      const errs: number[] = [];
      for (let i = 0; i < 60; i++) {
        // Acquire at frontal, THEN turn — what a wearer does, and what the
        // marching needs: it rematches under the pose we already believe, so
        // a cold first frame at 45 degrees has no prediction to march under.
        // (Both arms are refused by the rms gate in that case, identically —
        // the bias is in the fixed correspondence either way, so marching is
        // never WORSE than today; it simply cannot rescue frame one. Worth
        // knowing: re-acquisition mid-turn lands on the unmarched path.)
        const here = poseAt(Math.min(45, i * 3));
        const truthP = projectAll(here);
        const lm = new Float64Array(mesh.vertexCount * 2);
        for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + gauss() * 0.5;
        // The oval landmarks sit on the CONTOUR, not on their own vertex.
        for (const strip of strips) {
          const sv = marchTruth(here, strip);
          lm[strip.landmark * 2] = truthP[sv * 2] + gauss() * 0.5;
          lm[strip.landmark * 2 + 1] = truthP[sv * 2 + 1] + gauss() * 0.5;
        }
        const est = estimateSigma(unc, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: K, pose: prev,
        });
        const r = track(state, {
          landmarks: lm, sigmaPx: est.sigmaPx, visibility: est.visibility, intrinsics: K, dt: 1 / 30,
        });
        if (!r.tracked || !r.rawPose) continue;
        prev = r.rawPose;
        // Measure only on the settled tail, at the held 45-degree pose.
        if (i >= 25) {
          errs.push(Math.hypot(
            r.rawPose.t[0] - here.t[0], r.rawPose.t[1] - here.t[1], r.rawPose.t[2] - here.t[2],
          ));
        }
      }
      errs.sort((a, b) => a - b);
      return { median: errs.length ? errs[errs.length >> 1] : NaN, frames: errs.length };
    };
    // Median of five seeds, not one draw. A single-seed version of this pin
    // read 0.361 against 0.400 and would have convicted the mechanism on a
    // coin flip; the five-seed medians below separate by a factor of five.
    // This tree has been burned by one draw before, in both directions.
    const SEEDS = [0x51de, 0x11, 0x23, 0x37, 0x53];
    const mid = (xs: number[]) => { const t = [...xs].sort((p, q) => p - q); return t[t.length >> 1]; };
    const runs = SEEDS.map((seed) => ({ fixed: run(false, seed), marched: run(true, seed) }));
    const a = {
      median: mid(runs.map((r) => r.fixed.median).filter(Number.isFinite)),
      frames: mid(runs.map((r) => r.fixed.frames)),
    };
    const b = {
      median: mid(runs.map((r) => r.marched.median).filter(Number.isFinite)),
      frames: mid(runs.map((r) => r.marched.frames)),
    };
    // An earlier draft of this pin compared how many frames each arm KEPT,
    // because the fixed-correspondence arm was losing the face outright at
    // 45 degrees. That turned out to be measuring a different defect — the
    // reprojection gate averaging residuals flat, so the invented far side
    // dragged an ordinary turn past the "not this face" threshold. With that
    // fixed (see residualStats) both arms hold the pose, and the honest
    // comparison is the one this rank is actually about: how far the solve
    // lands from the truth when the contour has slid underneath it.
    assert.ok(a.frames > 25 && b.frames > 25,
      `only ${a.frames}/${b.frames} settled frames tracked — the turn fixture lost the face`);
    assert.ok(a.median > 0.2,
      `fixture sanity: the fixed-vertex solve is already at ${a.median.toFixed(3)} mm — no bias to remove`);
    assert.ok(b.median < a.median * 0.6,
      `marching scored ${b.median.toFixed(3)} mm against the fixed correspondence's ${a.median.toFixed(3)} — ` +
      'measured -74% at 40 degrees and -81% at 55 with the production cull in the loop, so a result ' +
      'this weak means the march is not finding the contour');
  });

  it('the strips that remap at FRONTAL are the ones carrying the marching gain', () => {
    // **The march is not a no-op at frontal, and the strips where it is not are
    // the ones the rank cannot do without.** A strip is a HORIZONTAL row of
    // candidates, and on the top and bottom arcs of the oval the rim runs
    // horizontally too — so the row lies ALONG the contour rather than across
    // it, and the most edge-on vertex in it is a more lateral neighbour. At an
    // exact frontal pose 10 of the 34 strips therefore remap, by 11.7 to
    // 20.0 mm: 338->297, 297->332, 377->400, 400->378, 378->379, their four
    // mirrors, and 67->103. It is the same regime `silhouetteStrips`' midline
    // cut already exempts 10 and 152 for, reaching further round the ring.
    //
    // The obvious repair is to exempt them, and this test exists because that
    // repair is a trap. Measured over twelve seeds, in this fixture's shape but
    // NOT by the code below — the campaign probe swept thresholds and seed
    // counts the suite has no business spending; what runs here is the five-seed
    // pin of the same trade:
    //
    //     yaw     off     all 34    without those ten
    //       0    0.096     0.095          0.096
    //      25    0.142     0.097          0.142       <- the whole gain
    //      40    0.521     0.103          0.125
    //      55    1.009     0.165          0.223
    //
    // At 25 degrees those ten carry ALL of it — removing them lands on the
    // unmarched median exactly — and at 40 and 55 about a fifth. What they buy
    // at frontal is nothing either way: twelve seeds put marched at 0.095
    // against unmarched 0.096, inside a per-seed spread of 0.074 to 0.128.
    //
    // **And every number above assumes the detector's oval landmarks follow the
    // contour**, which is what this fixture builds and what marching exists
    // for. Against the opposite premise — each oval landmark at its own fixed
    // vertex — marching is a cost rather than a gain (0.101 → 0.120 at 25
    // degrees, 0.120 → 0.213 at 40, twelve seeds). That regime is not pinned
    // here because it is an argument about the DETECTOR, not about this code;
    // `docs/CONSTANTS.md` carries it, and it is why the flag ships off.
    //
    // 25 degrees rather than the 45 the test above uses, deliberately: that is
    // where the difference between the arms is total instead of partial, and
    // it is a pose an ordinary wearer holds.
    const strips = silhouetteStrips(mesh);
    const frontal = poseAt(0);
    const remapping = strips.filter((s) => marchTruth(frontal, s) !== s.landmark);
    const steady = strips.filter((s) => marchTruth(frontal, s) === s.landmark);
    assert.ok(remapping.length > 0,
      'no strip remaps at an exact frontal pose — the arcs this test is about are gone, and '
      + 'the `silhouetteStrips` ledger row plus the comment at the march call site both '
      + 'describe a tree that no longer exists');
    assert.equal(remapping.length + steady.length, strips.length, 'strip partition lost one');

    const run = (used: SilhouetteStrip[] | null, seed: number) => {
      const state = createTracker(model, {
        smooth: false, rigidity, motionPrior: true, ovalStrips: used,
      });
      const unc = createUncertainty(mesh.vertexCount);
      let st = seed;
      const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
      const gauss = () => { let u = 0; while (u === 0) u = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
      let prev: Pose | null = null;
      const errs: number[] = [];
      for (let i = 0; i < 60; i++) {
        const here = poseAt(Math.min(25, i * 3));
        const truthP = projectAll(here);
        const lm = new Float64Array(mesh.vertexCount * 2);
        for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = truthP[v] + gauss() * 0.5;
        // Every oval landmark sits on the TRUE contour in every arm. The
        // fixture must not change with the arm, or it measures itself.
        for (const strip of strips) {
          const sv = marchTruth(here, strip);
          lm[strip.landmark * 2] = truthP[sv * 2] + gauss() * 0.5;
          lm[strip.landmark * 2 + 1] = truthP[sv * 2 + 1] + gauss() * 0.5;
        }
        const est = estimateSigma(unc, {
          landmarks: lm, mesh, positions: new Float64Array(mesh.positions),
          intrinsics: K, pose: prev,
        });
        const r = track(state, {
          landmarks: lm, sigmaPx: est.sigmaPx, visibility: est.visibility, intrinsics: K, dt: 1 / 30,
        });
        if (!r.tracked || !r.rawPose) continue;
        prev = r.rawPose;
        if (i >= 25) {
          errs.push(Math.hypot(
            r.rawPose.t[0] - here.t[0], r.rawPose.t[1] - here.t[1], r.rawPose.t[2] - here.t[2],
          ));
        }
      }
      errs.sort((a, b) => a - b);
      return errs.length ? errs[errs.length >> 1] : NaN;
    };
    const SEEDS = [0x51de, 0x11, 0x23, 0x37, 0x53];
    const mid = (xs: number[]) => {
      const t = xs.filter(Number.isFinite).sort((p, q) => p - q);
      return t.length ? t[t.length >> 1] : NaN;
    };
    const off = mid(SEEDS.map((s) => run(null, s)));
    const every = mid(SEEDS.map((s) => run(strips, s)));
    const without = mid(SEEDS.map((s) => run(steady, s)));

    assert.ok(off > every * 1.25,
      `fixture sanity: marching scored ${every.toFixed(3)} mm against the unmarched `
      + `${off.toFixed(3)} at 25 degrees — measured 0.097 against 0.142, so a result this `
      + 'weak means the fixture has stopped exhibiting the bias the rank exists for');
    assert.ok(without > every * 1.25,
      `dropping the ${remapping.length} strips that remap at frontal scored `
      + `${without.toFixed(3)} mm against ${every.toFixed(3)} with all of them — 0.142 against `
      + '0.097 at the twelve seeds the campaign swept, and the same ordering at the five here, '
      + 'i.e. the entire gain at this yaw. If this now passes '
      + 'cheaply, the exemption is no longer paying for itself and the ledger row is out of date; '
      + 'if you got here by exempting the flat arcs to make the frontal march a no-op, that is '
      + 'the trade this test exists to make visible — and the frontal cost it buys is 0.095 '
      + 'against 0.096, inside the seed noise.');
  });

  it('the tracker solves against the surface the DETECTOR reports, not the skin one', () => {
    // `enroll.ts` subtracts `detectorBias().offsetMm` from what the bundle
    // solved, so `model.positions` is SKIN — which is right, because a pad
    // bears on skin rather than on a landmark convention. The detector reports
    // the other surface. Until 2026-09-02 the tracker matched raw detector
    // landmarks against the skin one, and `detector-bias.ts` asserted that
    // "Tracking is unaffected either way" — true only while the bias is zero,
    // which it is until Q2's calibration exists.
    //
    // Measured here, with the 0.6 mm normal offset Q2's own harness injects:
    // solving against skin costs 2.04 mm of pose error at frontal against
    // 0.089 with the offset added back, while the reprojection rms moves 0.71
    // to 0.94 px — a fiftieth of the way to `maxRmsPx`, so nothing refuses a
    // frame and the glasses simply sit 2 mm out for the life of the scan.
    //
    // Both arms are here because the pin is worthless without the control: a
    // zero bias makes them the same run, and this test would then be green
    // whatever the tracker did.
    const V = mesh.vertexCount;
    const skin = new Float64Array(mesh.positions);
    const normals = computeVertexNormals(skin, mesh.indices, V);
    const bias = new Float64Array(V * 3);
    const detectorSurface = new Float64Array(V * 3);
    for (let v = 0; v < V; v++) {
      for (let c = 0; c < 3; c++) {
        bias[v * 3 + c] = normals[v * 3 + c] * 0.6;
        detectorSurface[v * 3 + c] = skin[v * 3 + c] + bias[v * 3 + c];
      }
    }

    const modelWith = (b: Float64Array) => createFaceModel({
      positions: new Float64Array(skin),
      vertexSigmaMm: new Float64Array(V).fill(0.3),
      shapeCoeffs: new Float64Array(0), basisName: 'ground-truth',
      displacementRmsMm: 0, displacementMaxMm: 0,
      intrinsics: K, intrinsicsSolved: true,
      scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
      landmarkBiasMm: b,
      quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
      pdMm: null, pdSigmaMm: null,
      reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
    });
    const projectDetector = (pose: Pose) => {
      const out = new Float64Array(V * 2);
      for (let v = 0; v < V; v++) {
        const X = detectorSurface[v * 3], Y = detectorSurface[v * 3 + 1], Z = detectorSurface[v * 3 + 2];
        const cx = pose.R[0] * X + pose.R[1] * Y + pose.R[2] * Z + pose.t[0];
        const cy = pose.R[3] * X + pose.R[4] * Y + pose.R[5] * Z + pose.t[1];
        const cz = pose.R[6] * X + pose.R[7] * Y + pose.R[8] * Z + pose.t[2];
        out[v * 2] = K.cx + (K.f * cx) / cz;
        out[v * 2 + 1] = K.cy + (K.f * cy) / cz;
      }
      return out;
    };
    const run = (b: Float64Array, seed: number) => {
      const state = createTracker(modelWith(b), { smooth: false, rigidity, motionPrior: true });
      const unc = createUncertainty(V);
      let st = seed;
      const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
      const gauss = () => { let u = 0; while (u === 0) u = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
      const here = poseAt(0);
      const truthP = projectDetector(here);
      let prev: Pose | null = null;
      const errs: number[] = [];
      for (let i = 0; i < 40; i++) {
        const lm = new Float64Array(V * 2);
        for (let j = 0; j < V * 2; j++) lm[j] = truthP[j] + gauss() * 0.5;
        const est = estimateSigma(unc, {
          landmarks: lm, mesh, positions: new Float64Array(skin), intrinsics: K, pose: prev,
        });
        const r = track(state, {
          landmarks: lm, sigmaPx: est.sigmaPx, visibility: est.visibility, intrinsics: K, dt: 1 / 30,
        });
        if (!r.tracked || !r.rawPose) continue;
        prev = r.rawPose;
        if (i >= 20) {
          errs.push(Math.hypot(
            r.rawPose.t[0] - here.t[0], r.rawPose.t[1] - here.t[1], r.rawPose.t[2] - here.t[2],
          ));
        }
      }
      errs.sort((a, b2) => a - b2);
      return errs.length ? errs[errs.length >> 1] : NaN;
    };
    const SEEDS = [0x51de, 0x11, 0x23, 0x37, 0x53];
    const mid = (xs: number[]) => {
      const t = xs.filter(Number.isFinite).sort((p, q) => p - q);
      return t.length ? t[t.length >> 1] : NaN;
    };
    // The control: the model does not declare the bias, so the tracker cannot
    // compensate. This is what shipped, and it must be badly wrong or the
    // assertion below is measuring nothing.
    const blind = mid(SEEDS.map((s) => run(new Float64Array(V * 3), s)));
    const told = mid(SEEDS.map((s) => run(bias, s)));

    assert.ok(blind > 1.0,
      `the control landed ${blind.toFixed(3)} mm from truth against the 2.04 measured — a `
      + '0.6 mm detector bias is supposed to be plainly visible in the pose here, and if it is '
      + 'not, this fixture has stopped exhibiting the defect and the assertion below is free');
    // **And a bias it cannot use is refused, not ignored.** Falling back to
    // zero on a malformed array would silently restore the 2 mm defect above,
    // through the one branch no gate can see. Unreachable today —
    // `deserializeFaceModel` rejects any other format version and
    // `serializeFaceModel` always writes the full array — which is exactly when
    // a silent fallback is cheapest to refuse.
    assert.throws(
      () => landmarkSurface({ ...modelWith(bias), landmarkBiasMm: new Float64Array(3) }),
      /landmark-bias/,
      'landmarkSurface accepted a bias of the wrong length — it must throw rather than treat '
      + 'it as zero, because zero is the behaviour this whole test exists to forbid',
    );

    assert.ok(told < 0.5,
      `declaring the bias left the solve ${told.toFixed(3)} mm out against the 0.089 measured, `
      + `with the blind arm at ${blind.toFixed(3)} — the tracker is still matching detector `
      + 'landmarks against the SKIN surface. It must use `landmarkSurface(model)`: '
      + '`model.positions` is skin by `enroll.ts`, and every comparison inside `track` is '
      + 'against the detector.');
  });

  it('the visible half owns the solve', () => {
    // At 40 deg the hallucinated far side pulls the pose toward the frontal
    // prior — a translation bias the cull removes and mere inflation cannot.
    const bias = (useVis: boolean) => {
      const state = createTracker(model, { smooth: false, rigidity });
      const truth = poseAt(40);
      const frame = makeSession(0xabc0 + 40, 0);
      let terr = 0, corr = 0, n = 0;
      for (let i = 0; i < 50; i++) {
        const { lm, sig, vis } = frame(40);
        const r = track(state, {
          landmarks: lm, sigmaPx: sig, visibility: useVis ? vis : null, intrinsics: K, dt: 1 / 30,
        });
        if (!r.tracked) continue;
        terr += Math.hypot(
          r.rawPose!.t[0] - truth.t[0], r.rawPose!.t[1] - truth.t[1], r.rawPose!.t[2] - truth.t[2],
        );
        corr += r.correspondences; n++;
      }
      return { err: terr / n, corr: corr / n };
    };
    const withCull = bias(true);
    const without = bias(false);
    assert.ok(withCull.corr < without.corr - 20,
      `culling removed only ${(without.corr - withCull.corr).toFixed(0)} correspondences at 40 deg — the ramp is not engaging`);
    assert.ok(withCull.err < without.err - 0.3,
      `translation bias ${without.err.toFixed(2)} -> ${withCull.err.toFixed(2)} mm — ` +
      'culling the hallucinated half bought nothing');
  });

  /*
   * The motion prior's stand-aside gate, on the channel that had none.
   *
   * Until 2026-08-31 the prior's honesty grade came only from the rotation
   * residual, and the resulting miss scaled BOTH channels' process noise. So
   * the lean-in/lean-back beat of an actual try-on — the wearer moving toward
   * the camera to look at the frames, orientation steady — could contradict
   * the constant-velocity prediction by millimetres every reversal without the
   * gate reading anything but rest. These two fixtures pin the mechanism and
   * the outcome: that the gate SEES a translation reversal, and that the beat
   * is no longer a multiple-x regression against solving without a prior.
   */
  const leanFrames = (
    priorOn: boolean, seed: number, dzOf: (i: number) => number, yawOf: (i: number) => number,
  ) => {
    const state = createTracker(model, { smooth: false, rigidity, motionPrior: priorOn });
    let st = seed >>> 0 || 1;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
    const gauss = () => { let u = 0; while (u === 0) u = rnd();
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
    const out: { truth: { R: Float64Array; t: Float64Array };
      t: Float64Array | null; R: Float64Array | null;
      missRot: number | null; missMm: number | null }[] = [];
    for (let i = 0; i < 200; i++) {
      const truth = { R: poseAt(yawOf(i)).R, t: Float64Array.of(0, 0, 520 + dzOf(i)) };
      const p = projectAll(truth);
      const lm = new Float64Array(mesh.vertexCount * 2);
      const sig = new Float64Array(mesh.vertexCount).fill(0.7);
      const vis = new Float64Array(mesh.vertexCount).fill(1);
      for (let v = 0; v < mesh.vertexCount * 2; v++) lm[v] = p[v] + gauss() * 0.7;
      const r = track(state, {
        landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30,
      });
      out.push({
        truth,
        t: r.tracked && r.rawPose ? Float64Array.from(r.rawPose.t) : null,
        R: r.tracked && r.rawPose ? Float64Array.from(r.rawPose.R) : null,
        missRot: state.priorMissLast, missMm: state.priorMissTransLast,
      });
    }
    return out;
  };
  const still = () => 40;
  const flat = () => 0;
  const lean = (i: number) => 25 * Math.sin((2 * Math.PI * 1.0 * i) / 30);

  it('the prior grades the translation it predicted, not just the rotation', () => {
    // The mechanism, stated as the thing that was missing. A 1 Hz lean at a
    // steady orientation must move the TRANSLATION grade well above its
    // resting level while leaving the ROTATION grade where it was — which is
    // exactly the case the old gate could not see, because it had only the
    // second of those two numbers.
    const restRun = leanFrames(true, 0x51ee, flat, still);
    const leanRun = leanFrames(true, 0x51ee, lean, still);
    const after = <T>(a: T[]) => a.slice(60);
    const medOf = (a: number[]) => {
      const v = [...a].sort((x, y) => x - y);
      return v.length ? v[v.length >> 1] : NaN;
    };
    const grab = (rows: typeof restRun, k: 'missRot' | 'missMm') =>
      medOf(after(rows).map((r) => r[k]).filter((v): v is number => v !== null));

    const restRot = grab(restRun, 'missRot'), restMm = grab(restRun, 'missMm');
    const leanRot = grab(leanRun, 'missRot'), leanMm = grab(leanRun, 'missMm');
    assert.ok(Number.isFinite(restMm),
      'the translation grade is never computed — the prior has no honesty signal for it');
    // The precondition that makes the rest of it mean anything: with the head
    // still, both grades sit near one and the prior runs at full strength.
    assert.ok(restMm < 2 && restRot < 2,
      `a still head already reads missRot ${restRot.toFixed(2)} / missMm ${restMm.toFixed(2)} — ` +
      'the gate is standing aside at rest and the resting win is being paid for');
    // The lean must trip the translation grade. The bar is relative as well as
    // absolute because the gate is SELF-LIMITING: standing aside shrinks the
    // very prediction error it grades, so the reading settles well below what
    // an ungated prior would show (4.2 measured before the stand-aside squares,
    // ~2.1 after). What has to hold is that it clears the `max(..., 1)` floor
    // and separates cleanly from rest.
    assert.ok(leanMm > 1.5 && leanMm > restMm * 2,
      `a 1 Hz lean reads a translation grade of ${leanMm.toFixed(2)} against a resting ` +
      `${restMm.toFixed(2)} — the prior is being contradicted by millimetres and does not know it`);
    // ...and must NOT be visible in the rotation grade, which is why grading
    // rotation alone could never have caught it.
    assert.ok(leanRot < restRot * 1.5,
      `the rotation grade moved ${restRot.toFixed(2)} -> ${leanRot.toFixed(2)} on a pure ` +
      'translation reversal, so this fixture does not isolate what it claims to');
  });

  it('and a lean beat is no longer a multiple-x regression against no prior at all', () => {
    // The outcome the wearer feels. Paired arms on the IDENTICAL noise
    // realisation, compared only on frames both tracked. Before the
    // per-channel gate this measured 4.5x at 0.5 Hz and 10.6x at 1 Hz; the bar
    // is 2x, which the defect fails by a wide margin and which does not pin
    // the exact strength of the stand-aside.
    const rms = (rows: ReturnType<typeof leanFrames>, other: ReturnType<typeof leanFrames>) => {
      let s = 0, n = 0;
      for (let i = 40; i < rows.length; i++) {
        if (!rows[i].t || !other[i].t) continue;
        const t = rows[i].truth.t, g = rows[i].t!;
        s += (g[0] - t[0]) ** 2 + (g[1] - t[1]) ** 2 + (g[2] - t[2]) ** 2; n++;
      }
      return n ? Math.sqrt(s / n) : NaN;
    };
    const on = leanFrames(true, 0x6a9, lean, still);
    const off = leanFrames(false, 0x6a9, lean, still);
    assert.ok(rms(on, off) < rms(off, on) * 2,
      `a 1 Hz lean costs ${(rms(on, off) / rms(off, on)).toFixed(2)}x the translation error ` +
      'with the prior on — the stand-aside is not reaching the translation channel');

    // And the resting win it exists for must survive: the same arms, still.
    const restOn = leanFrames(true, 0x6a9, flat, still);
    const restOff = leanFrames(false, 0x6a9, flat, still);
    assert.ok(rms(restOn, restOff) < rms(restOff, restOn) * 0.85,
      `the prior no longer steadies a still head (${rms(restOff, restOn).toFixed(3)} -> ` +
      `${rms(restOn, restOff).toFixed(3)} mm) — the gate is standing aside when it should not`);
  });

  it('and the ROTATION half of the same gate is held by something too', () => {
    // Added with the translation gate, for a reason worth stating: until then
    // the rotation half was held by NOTHING. Deleting its `* missRot` outright
    // passed all 337 tests and all four gates, while costing 4-11x the rotation
    // error on a head shake — which is the 7.1x-19x regression the
    // `PRIOR_MISS_EMA_RATE` ledger row exists to record. A decision settled in
    // prose only is a decision the next edit can undo silently, and the fixture
    // for the new channel had no business being better guarded than the old one.
    const shake = (i: number) => 40 + 10 * Math.sin((2 * Math.PI * 1.0 * i) / 30);
    const rmsRot = (rows: ReturnType<typeof leanFrames>, other: ReturnType<typeof leanFrames>) => {
      let s = 0, n = 0;
      for (let i = 40; i < rows.length; i++) {
        if (!rows[i].t || !other[i].t) continue;
        n++;
      }
      // Angle between the solved and the true rotation, degrees RMS. The pose
      // is recovered from the same rows the translation metric uses, so the
      // two arms are compared on exactly the frames both of them tracked.
      s = 0;
      for (let i = 40; i < rows.length; i++) {
        if (!rows[i].t || !other[i].t) continue;
        const P = rows[i].R!, T = rows[i].truth.R;
        let tr = 0;
        for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) tr += P[r * 3 + c] * T[r * 3 + c];
        const ang = Math.acos(Math.max(-1, Math.min(1, (tr - 1) / 2)));
        s += ang * ang;
      }
      return n ? (Math.sqrt(s / n) * 180) / Math.PI : NaN;
    };
    const on = leanFrames(true, 0x51ee, flat, shake);
    const off = leanFrames(false, 0x51ee, flat, shake);
    assert.ok(Number.isFinite(rmsRot(off, on)) && rmsRot(off, on) > 0,
      'the shake fixture produced no rotation error at all — it proves nothing');
    assert.ok(rmsRot(on, off) < rmsRot(off, on) * 2,
      `a 1 Hz head shake costs ${(rmsRot(on, off) / rmsRot(off, on)).toFixed(2)}x the rotation ` +
      'error with the prior on — the rotation stand-aside is not firing');
  });

  it('the prior\'s share COLLAPSES on the channel it is getting wrong, which is what bounds the closed loop', () => {
    // **The grade is a closed loop and this is the property that makes it
    // survivable.** `priorMissLast` compares the prediction to `result.pose` —
    // the MAP posterior into which that same prior was fused — so the residual
    // is shrunk by the prior's own pull, and the shrink grows with the prior's
    // information share. The tree refuses this construction elsewhere in the
    // same file: "Never the pose being solved for, which would close a loop
    // around the estimate."
    //
    // It is bounded because the share is not a free parameter. Violating the
    // prediction raises `miss`, `miss` inflates Q, Q weakens the prior, and the
    // share falls — so the loop is tightest exactly where the prediction is
    // being MET and loosest where it is being contradicted, which is the
    // opposite of what the defect report predicted. Measured through the real
    // tracker with sigma and visibility from the real `estimateSigma`, so the
    // far half of a turned head is muted the way it is in the app:
    //
    //     regime                 shareRot   under-read   missRot
    //     frontal hold            0.129        1.99        0.64
    //     70 deg hold             0.189        2.28        0.74
    //     frontal, hard shake     0.009        1.07        5.72
    //     70 deg, hard shake      0.017        1.12        5.78
    //
    // The under-read is 2.3x at a hold — where `miss` sits UNDER its floor of 1
    // and the whole reading is discarded — and 1.1x at the corner the
    // `MOTION_PRIOR_ACCEL_MM_S2` ledger row flags untested, where the gate is
    // reading 5.8 and firing hard.
    //
    // Grading honestly instead (a second, prior-less solve of the same frame,
    // measured) costs **+6.2 to +6.6% emitted jitter** at rest and on a shake —
    // against the prior's own reason for existing, a 21.8% rest-jitter win —
    // and buys 8.9-11.5% on a RAW lean error that the smoother absorbs: on the
    // emitted pose the lean gets 0.2-0.4% WORSE. That is the same trade the
    // squared stand-aside was rejected for a few lines above, and it is
    // rejected here for the same reason.
    //
    // RED: delete `* missRot` from qRot in `buildPrior`. The share stops
    // collapsing and the loop stops being bounded.
    const shareOf = (yawOf: (i: number) => number) => {
      const state = createTracker(model, { smooth: false, rigidity, motionPrior: true });
      let st = 0x51ee;
      const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296; };
      const gauss = () => { let u = 0; while (u === 0) u = rnd();
        return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
      const sig = new Float64Array(mesh.vertexCount).fill(0.7);
      const vis = new Float64Array(mesh.vertexCount).fill(1);
      const shares: number[] = [];
      for (let i = 0; i < 160; i++) {
        const truth = { R: poseAt(yawOf(i)).R, t: Float64Array.of(0, 0, 520) };
        const p = projectAll(truth);
        const lm = new Float64Array(mesh.vertexCount * 2);
        for (let v = 0; v < lm.length; v++) lm[v] = p[v] + gauss() * 0.7;
        const r = track(state, {
          landmarks: lm, sigmaPx: sig, visibility: vis, intrinsics: K, dt: 1 / 30,
        });
        if (i >= 40 && Number.isFinite(r.priorShareRot)) shares.push(r.priorShareRot);
      }
      shares.sort((a, b) => a - b);
      return shares.length ? shares[shares.length >> 1] : NaN;
    };

    const held = shareOf(() => 60);
    const shaken = shareOf((i) => 60 + 12 * Math.sin((2 * Math.PI * 1.5 * i) / 30));
    assert.ok(held > 0.03,
      `the prior carries only ${held.toFixed(4)} of a HELD solve — the fixture is not exercising `
      + 'the prior at all, so the collapse below would prove nothing');
    assert.ok(shaken < held / 4,
      `a hard shake leaves the prior ${shaken.toFixed(4)} of the solve against ${held.toFixed(4)} `
      + `at a hold (${(held / shaken).toFixed(1)}x, needs 4x). The share is what bounds the closed `
      + 'loop in the grade above; if it stops collapsing under violation, the under-read stops '
      + 'being small exactly where it matters and the grade needs rebuilding.');
  });
});

describe('the basin audit — wired through the real path, guarded against flapping', () => {
  // The defect it exists for CANNOT be reproduced here, and that is recorded
  // rather than faked: the first real wearer's screen recording caught the
  // warm-started solver holding +12-15 degrees of phantom roll for ten
  // seconds with the rms gate happy throughout, after a fast slide across the
  // frame. Sixty synthetic attempts to manufacture that basin (teleports, 26-80
  // sparse correspondences, yaw 40-78) produced zero: iid landmark jitter
  // always lets LM out, so the trap needs the real detector's CORRELATED
  // errors. What CAN be pinned is the machinery: the audit must run cold
  // solves through the real track() path on its schedule; near-equal wins
  // must die in the adoption deadband (each one it lets through resets the
  // smoother — the ~1/s pop of the wearer's "choppy" report); with the
  // deadband off, adoption must flow — counter, memory reset, crossfade —
  // and at the shipped ratio warm-and-cold agreement must adopt NOTHING,
  // the guard that stops basin-flapping.
  const mesh = loadTemplateMesh();
  const model = createFaceModel({
    positions: new Float64Array(mesh.positions),
    vertexSigmaMm: new Float64Array(mesh.vertexCount).fill(0.3),
    shapeCoeffs: new Float64Array(0),
    basisName: 'ground-truth',
    displacementRmsMm: 0, displacementMaxMm: 0,
    intrinsics: { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 },
    intrinsicsSolved: true,
    scale: { source: 'card', factor: 1, sigma: 0.001, note: 'ground truth' },
    landmarkBiasMm: new Float64Array(mesh.vertexCount * 3),
    quality: { nose: { observations: 30, parallaxRms: 0.3, sigmaMm: 0.3 } },
    pdMm: null, pdSigmaMm: null,
    reprojectionRmsPx: 0, framesUsed: 0, solveMs: 0, degraded: false, notes: [],
  });
  const K = { f: 832, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };
  const frame = (jitterPx: number, seed: number) => {
    const lm = new Float64Array(mesh.vertexCount * 2);
    let st = seed >>> 0 || 1;
    const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; st >>>= 0; return st / 4294967296 - 0.5; };
    for (let v = 0; v < mesh.vertexCount; v++) {
      const X = mesh.positions[v * 3], Y = mesh.positions[v * 3 + 1], Z = mesh.positions[v * 3 + 2];
      const cz = -Z + 520;
      lm[v * 2] = K.cx + (K.f * X) / cz + rnd() * 2 * jitterPx;
      lm[v * 2 + 1] = K.cy + (K.f * -Y) / cz + rnd() * 2 * jitterPx;
    }
    return lm;
  };
  const sigma = new Float64Array(mesh.vertexCount).fill(1.4);

  it('adopts nothing at the shipped ratio when warm and cold agree', () => {
    const state = createTracker(model, { basinAuditInterval: 1 });
    for (let i = 0; i < 40; i++) {
      track(state, { landmarks: frame(0.8, 40 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.equal(state.basinEscapes, 0,
      `${state.basinEscapes} adoptions between two solves of the SAME basin — ` +
      'the rescue ratio is letting noise flap the pose between equals');
    assert.ok(state.basinAuditsRun >= 30, 'the audit is not even running');
  });

  it('the deadband skips near-equal wins — the ~1/s smoother-reset pop, pinned', () => {
    // basinRescueRatio 1.5 accepts a cold solve merely comparable to the warm
    // one, which on real correlated noise is what the shipped ratio was doing
    // about once a second. Each of those adoptions reset the smoother for a
    // pose difference under a millimetre — all cost, no rescue. The deadband
    // must eat every one of them.
    const state = createTracker(model, { basinAuditInterval: 10, basinRescueRatio: 1.5 });
    for (let i = 0; i < 41; i++) {
      track(state, { landmarks: frame(0.8, 90 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
    }
    assert.equal(state.basinEscapes, 0,
      `${state.basinEscapes} same-basin adoptions went through the deadband — ` +
      'every one of these resets the smoother, and that is the pop the wearer reported');
    assert.ok(state.basinAdoptionsSkipped >= 3,
      `${state.basinAdoptionsSkipped} deadband skips over 40 frames at interval 10 — ` +
      'the audit is not reaching the deadband at all');
  });

  it('adopts through the real path, crossfade armed, when the deadband is off', () => {
    // Deadband zeroed: every rms-comparable cold solve adopts, exercising the
    // exact code a genuine rescue takes — the counter, the memory reset, and
    // the crossfade that replaced the hard cut.
    const state = createTracker(model, {
      basinAuditInterval: 10, basinRescueRatio: 1.5, basinAdoptMinMm: 0, basinAdoptMinDeg: 0,
    });
    let sawFade = false;
    let adoptionsWithOffset = 0;
    for (let i = 0; i < 41; i++) {
      const before = state.basinEscapes;
      const r = track(state, { landmarks: frame(0.8, 90 + i), sigmaPx: sigma, intrinsics: K, dt: 1 / 30 });
      if (r.fading) sawFade = true;
      // On the adoption frame itself the fade must have CAPTURED its offset —
      // a countdown with no offset is a fade that pays out nothing, which is
      // the pop wearing the flag. (The offset cannot be pinned by its visible
      // effect here: same-basin adoptions differ by microns, which is exactly
      // why the flag alone proved breakage-blind.)
      if (state.basinEscapes > before && state.fadeOffset !== null) adoptionsWithOffset++;
    }
    assert.ok(state.basinEscapes >= 3,
      `${state.basinEscapes} audits adopted over 40 frames at interval 10 — the audit is not running`);
    assert.ok(state.basinEscapes <= 5, 'the audit is firing off its schedule');
    assert.ok(sawFade, 'an adoption landed without arming the crossfade — the pop is back');
    assert.equal(adoptionsWithOffset, state.basinEscapes,
      'an adoption armed the fade countdown without capturing the offset it pays out');
  });
});
