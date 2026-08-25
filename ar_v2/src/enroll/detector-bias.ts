/**
 * The landmark detector's own offset from the skin — a calibration asset, not a
 * per-wearer estimate.
 *
 * ## Why this cannot be solved during a scan
 *
 * The bundle sees only what the detector reports. If the detector consistently
 * places the nose-bridge landmark 0.8 mm in front of the actual bridge, then
 * across all 150 views of one face, "the detector is biased" and "this bridge is
 * 0.8 mm more prominent" produce **identical observations**. No amount of
 * parallax separates them, because the bias rides on the surface point and
 * transforms with it.
 *
 * The information simply is not in the capture. It is in a comparison between
 * the detector's output and a *ground-truth scan of the same face*, and that is
 * a measurement someone makes once, in a lab, against a population — not
 * something a wearer's four seconds can supply.
 *
 * v1 got this half right and half wrong: it correctly identified that the
 * nose-bridge landmark sits off the canonical mesh by a real, measurable
 * amount, and then estimated the size of that offset **from two faces** and
 * shipped it. Its own audit flags this (`n=2 for the detector bias`). Two faces
 * is not a population; it is an anecdote with a standard deviation.
 *
 * ## What this file does instead
 *
 * Holds a slot. The default is zero, which is honest: no bias correction is
 * being applied, so the reconstruction is the surface *as the detector sees it*,
 * self-consistently. Tracking is unaffected either way, because tracking matches
 * the same detector against the same surface. Only the **contact solve** cares,
 * because a pad bears on skin rather than on a landmark convention — and a
 * sub-millimetre bias there is worth about a third of a millimetre of seat
 * height on a typical wedge.
 *
 * `measureDetectorBias` is the function that fills it, and it needs ground-truth
 * geometry. The synthetic harness has that by construction, which is how the
 * estimator itself is tested; the real calibration needs scanned heads. See
 * `docs/OPEN-QUESTIONS.md` Q2.
 */

import type { FaceMesh } from '../core/mesh.js';
import { mat3FromQuat } from '../core/linalg.js';

export interface DetectorBias {
  /** Per-vertex offset in face space, mm, 3 per vertex. Subtracted from the
   *  reconstruction to recover skin from landmark convention. */
  offsetMm: Float64Array;
  /** One-sigma of each offset, mm — how well the calibration itself is known. */
  sigmaMm: Float64Array;
  /** What produced this: 'none', or a description of the calibration set. */
  source: string;
  /** How many distinct faces the calibration averaged. `0` for the zero bias,
   *  and the number a reviewer should look at first for any other. */
  faces: number;
}

let cached: DetectorBias | null = null;

/**
 * The shipped calibration. Zero until a real one exists.
 *
 * Returning a real object rather than `null` is deliberate: an optional
 * correction is a correction somebody forgets to apply, and a zero one costs a
 * subtraction of zero.
 */
export function detectorBias(vertexCount = 468): DetectorBias {
  if (cached && cached.offsetMm.length === vertexCount * 3) return cached;
  cached = {
    offsetMm: new Float64Array(vertexCount * 3),
    sigmaMm: new Float64Array(vertexCount * 3),
    source: 'none — no ground-truth calibration has been measured',
    faces: 0,
  };
  return cached;
}

export function loadDetectorBias(json: unknown): DetectorBias {
  const raw = json as {
    offsetMm: number[]; sigmaMm: number[]; source: string; faces: number;
  };
  if (!raw || !Array.isArray(raw.offsetMm)) {
    throw new Error('detector bias: expected { offsetMm, sigmaMm, source, faces }');
  }
  if (!(raw.faces >= 5)) {
    // Not a hard refusal — a small calibration is still better than none — but
    // it is exactly the number v1 shipped at n=2, so it is said out loud.
    console.warn(
      `detector bias calibrated on only ${raw.faces} faces; ` +
      'treat the correction as indicative, not measured',
    );
  }
  return {
    offsetMm: Float64Array.from(raw.offsetMm),
    sigmaMm: Float64Array.from(raw.sigmaMm),
    source: raw.source,
    faces: raw.faces,
  };
}

/**
 * Measures the bias from paired (reconstruction, ground truth) geometries.
 *
 * Each pair contributes `reconstructed - truth` per vertex, after a rigid
 * alignment that removes the gauge freedom — otherwise a global offset between
 * the two would be recorded as a per-landmark bias, which is the mistake that
 * makes a calibration worse than none.
 *
 * The alignment is Procrustes without scale: scale is exactly the quantity the
 * ruler establishes, and absorbing it here would hide a scale error in the bias
 * and hide the bias in the scale.
 */
export function measureDetectorBias(
  pairs: { reconstructed: Float64Array; truth: Float64Array }[],
  mesh: FaceMesh,
  source: string,
): DetectorBias {
  const V = mesh.vertexCount;
  const sum = new Float64Array(V * 3);
  const sumSq = new Float64Array(V * 3);

  for (const pair of pairs) {
    const aligned = rigidAlign(pair.reconstructed, pair.truth, V);
    for (let i = 0; i < V * 3; i++) {
      const d = aligned[i] - pair.truth[i];
      sum[i] += d;
      sumSq[i] += d * d;
    }
  }

  const n = Math.max(pairs.length, 1);
  const offsetMm = new Float64Array(V * 3);
  const sigmaMm = new Float64Array(V * 3);
  for (let i = 0; i < V * 3; i++) {
    offsetMm[i] = sum[i] / n;
    const variance = Math.max(sumSq[i] / n - offsetMm[i] * offsetMm[i], 0);
    // Standard error of the mean, not the population sd: what is wanted is how
    // well the CALIBRATION is known, which improves with more faces.
    sigmaMm[i] = Math.sqrt(variance / n);
  }

  return { offsetMm, sigmaMm, source, faces: pairs.length };
}

/**
 * Procrustes alignment without scale: finds R, t minimising |R a + t - b|.
 *
 * Horn's quaternion method (JOSA A, 1987): build a 4x4 symmetric matrix from the
 * cross-covariance and take the eigenvector of its largest eigenvalue, which is
 * the optimal rotation's quaternion.
 *
 * Chosen over the two obvious alternatives for reasons that were both measured
 * here rather than assumed:
 *
 *  - **Not SVD**: there is no SVD in this tree and adding one for a 3x3 is
 *    disproportionate.
 *  - **Not the polar-decomposition Newton iteration** (`X <- (X + X^-T)/2`),
 *    which is what this function used first and which was wrong in a way worth
 *    recording. That iteration converges quadratically *near the answer* but its
 *    early steps only halve the scale, and the cross-covariance of 468 vertices
 *    in millimetres has entries around 1e6 — so it needs about twenty iterations
 *    just to reach O(1), and at the twelve it was given it returned a matrix
 *    with a scale factor of several hundred still in it. The symptom was
 *    spectacular and completely misleading: per-region errors of 4000 mm sitting
 *    next to per-*measurement* errors of 0.9 mm, i.e. the reconstruction was
 *    fine and only its grader was broken. Horn's method has no scale sensitivity
 *    at all: the eigenvector is invariant to scaling the matrix.
 *
 * Reflections are refused rather than returned. A proper rotation is what a
 * comparison between two versions of the same mesh must produce; an improper
 * one means the inputs do not correspond, and silently returning it would grade
 * a mirrored reconstruction as excellent.
 */
export function rigidAlign(a: Float64Array, b: Float64Array, vertexCount: number): Float64Array {
  const ca = [0, 0, 0], cb = [0, 0, 0];
  for (let i = 0; i < vertexCount; i++) {
    for (let c = 0; c < 3; c++) { ca[c] += a[i * 3 + c]; cb[c] += b[i * 3 + c]; }
  }
  for (let c = 0; c < 3; c++) { ca[c] /= vertexCount; cb[c] /= vertexCount; }

  // S[r][c] = sum a_r b_c
  const S = new Float64Array(9);
  for (let i = 0; i < vertexCount; i++) {
    const ax = a[i * 3] - ca[0], ay = a[i * 3 + 1] - ca[1], az = a[i * 3 + 2] - ca[2];
    const bx = b[i * 3] - cb[0], by = b[i * 3 + 1] - cb[1], bz = b[i * 3 + 2] - cb[2];
    S[0] += ax * bx; S[1] += ax * by; S[2] += ax * bz;
    S[3] += ay * bx; S[4] += ay * by; S[5] += ay * bz;
    S[6] += az * bx; S[7] += az * by; S[8] += az * bz;
  }

  const R = new Float64Array(9);
  hornRotation(R, S);

  const out = new Float64Array(a.length);
  for (let i = 0; i < vertexCount; i++) {
    const ax = a[i * 3] - ca[0], ay = a[i * 3 + 1] - ca[1], az = a[i * 3 + 2] - ca[2];
    out[i * 3] = R[0] * ax + R[1] * ay + R[2] * az + cb[0];
    out[i * 3 + 1] = R[3] * ax + R[4] * ay + R[5] * az + cb[1];
    out[i * 3 + 2] = R[6] * ax + R[7] * ay + R[8] * az + cb[2];
  }
  return out;
}

/**
 * The rotation taking `a` onto `b`, from their cross-covariance `S`.
 *
 * The eigenvector is found by shifted power iteration: `N + cI` with `c` a
 * Gershgorin bound is positive definite, so the dominant eigenvector of the
 * shifted matrix is the one wanted, and power iteration on a 4x4 costs nothing.
 * Fifty iterations is far past convergence for any non-degenerate input; a
 * degenerate one (all points collinear) falls back to the identity, which is the
 * correct answer to an unanswerable question.
 */
export function hornRotation(out: Float64Array, S: Float64Array): void {
  const sxx = S[0], sxy = S[1], sxz = S[2];
  const syx = S[3], syy = S[4], syz = S[5];
  const szx = S[6], szy = S[7], szz = S[8];

  const N = [
    [sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
    [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
    [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
    [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz],
  ];

  // Gershgorin shift so every eigenvalue is positive and the largest stays
  // largest.
  let shift = 0;
  for (let r = 0; r < 4; r++) {
    let row = 0;
    for (let c = 0; c < 4; c++) row += Math.abs(N[r][c]);
    shift = Math.max(shift, row);
  }
  for (let r = 0; r < 4; r++) N[r][r] += shift;

  let q = [1, 0, 0, 0];
  for (let it = 0; it < 50; it++) {
    const next = [0, 0, 0, 0];
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let c = 0; c < 4; c++) s += N[r][c] * q[c];
      next[r] = s;
    }
    const len = Math.hypot(next[0], next[1], next[2], next[3]);
    if (!(len > 1e-300)) break;
    let delta = 0;
    for (let r = 0; r < 4; r++) {
      const v = next[r] / len;
      delta = Math.max(delta, Math.abs(v - q[r]));
      q[r] = v;
    }
    if (delta < 1e-15) break;
  }

  // Horn's ordering is (w, x, y, z); `mat3FromQuat` wants (x, y, z, w).
  const w = q[0], x = q[1], y = q[2], z = q[3];
  const n = Math.hypot(w, x, y, z);
  if (!(n > 1e-12)) { out.set([1, 0, 0, 0, 1, 0, 0, 0, 1]); return; }
  mat3FromQuat(out, [x / n, y / n, z / n, w / n]);
}
