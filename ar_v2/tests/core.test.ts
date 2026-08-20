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

import {
  eulerYXZ, expSO3, invertSymmetric, ldlt, ldltSolve, logSO3, m3,
  mat3FromEulerYXZ, m3mul, orthonormalize, poseIdentity, poseOplus,
  rotationAngleBetween, solveSymmetric, v3, vlen, weightedMedian, mad, percentile,
} from '../src/core/linalg.js';
import {
  dProjDIntrinsics, dProjDModelPoint, dProjDPoint, dProjDPose, intrinsicsFromFov,
  pointAtDepth, project, rayThrough, verticalFovDeg, type Intrinsics,
} from '../src/core/camera.js';
import { hornRotation, rigidAlign } from '../src/enroll/detector-bias.js';
import { createRng } from '../src/testkit/random.js';

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
});

describe('analytic jacobians match central differences', () => {
  const EPS = 1e-6;

  it('d(projection) / d(camera point)', () => {
    const rng = createRng(23);
    for (const k of [K, { ...K, k1: -0.15 }]) {
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
      dProjDPose(J, 0, K, cam, rot);

      for (let c = 0; c < 6; c++) {
        const delta = new Float64Array(6);
        delta[c] = EPS;
        const plus = poseIdentity(); poseOplus(plus, pose, delta, 0);
        delta[c] = -EPS;
        const minus = poseIdentity(); poseOplus(minus, pose, delta, 0);
        const a = new Float64Array(2), b = new Float64Array(2);
        apply(plus); project(a, K, cam);
        apply(minus); project(b, K, cam);
        for (let r = 0; r < 2; r++) {
          const numeric = (a[r] - b[r]) / (2 * EPS);
          assert.ok(
            Math.abs(numeric - J[r * 6 + c]) < 2e-3 * Math.max(1, Math.abs(numeric)),
            `row ${r} col ${c}: analytic ${J[r * 6 + c]} numeric ${numeric}`,
          );
        }
      }
    }
  });

  it('d(projection) / d(model point) through the pose rotation', () => {
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
      dProjDModelPoint(J, 0, K, cam, R);
      for (let c = 0; c < 3; c++) {
        const shift = (s: number) => {
          const X = new Float64Array(Xm); X[c] += s;
          const p = v3(
            R[0] * X[0] + R[1] * X[1] + R[2] * X[2] + t[0],
            R[3] * X[0] + R[4] * X[1] + R[5] * X[2] + t[1],
            R[6] * X[0] + R[7] * X[1] + R[8] * X[2] + t[2],
          );
          const uv = new Float64Array(2); project(uv, K, p); return uv;
        };
        const a = shift(EPS), b = shift(-EPS);
        for (let r = 0; r < 2; r++) {
          const numeric = (a[r] - b[r]) / (2 * EPS);
          assert.ok(Math.abs(numeric - J[r * 3 + c]) < 1e-4 * Math.max(1, Math.abs(numeric)));
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
