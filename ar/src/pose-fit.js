/**
 * CANDIDATE B — the rigid-subset pose refit (anchoring-v3).
 *
 * Spec: ar/docs/nose-v3/v3-rethink.txt §B.1–B.4, promoted to primary by the
 * 2026-08-17 telemetry attribution run (spec.md Stage-6/R0 notes): with every
 * estimator frozen, the MediaPipe matrix ALONE walked the drawn frame 3.49 px
 * RMS during deliberate eye circles on a held-still head — above the 2 px
 * psychophysical bar — because MediaPipe's Procrustes fit weights the
 * gaze-coupled nose vertices highest. The matrix itself carries gaze; no
 * amount of downstream conditioning can remove what the carrier brings in.
 *
 * So this module re-derives the carrying transform each frame: a weighted
 * Huber least-squares fit of the person model's OWN converged geometry
 * (`person.est`, this wearer's face, not the canonical average) against this
 * frame's observed landmark rays, over `RIGID_SUBSET` — the measured
 * gaze-immune, expression-resistant vertices. Both live symptoms are attacked
 * at the estimator: gaze-coupled vertices are excluded from the fit entirely,
 * and extreme-pose noise is averaged over ~20 personally-converged stable
 * points instead of riding MediaPipe's canonical-basis compromise (the R0
 * rigidMiss finding: the canonical fit redistributes its misfit by up to
 * 13 px at −18° yaw on this wearer).
 *
 * What it deliberately does NOT do:
 *
 *  - **Scale is not solved.** A head does not change size, scale is the
 *    twitchiest channel of the decomposition (it absorbs the depth
 *    ambiguity), and the smoother's scale channel already knows all of that.
 *    The refit poses `scale × est` and leaves the scale channel's input —
 *    MediaPipe's own — untouched.
 *  - **It never runs free of the MediaPipe matrix.** The person model was
 *    accumulated in matrix-defined face space; a refit whose frame walked
 *    away from that gauge would drag the model with it through a feedback
 *    loop (pose fits model, observations carried by pose feed model). The
 *    solved pose is CLAMPED to ≤ CLAMP_ROT / ≤ CLAMP_POS of the matrix, and
 *    every clamp engagement is counted — a clamp that engages at rest is the
 *    gauge-drift alarm (B.4: kill if > 1% of rest frames). Note the counter
 *    counts RAIL PRESSURE, not drift by itself: a correctly capped clamp
 *    re-engages every frame the solve leans on the rail, so the replay's
 *    asserting form of B.4 is the attribution — live-arm engagements at
 *    converged rest must not exceed the frozen-model control arm's, where
 *    feedback is structurally impossible (the 2026-08-17 landing
 *    verification, findings F1/F3/F4).
 *  - **It has no gate.** Confidence is a continuous blend: `wSolve` rises
 *    with the subset's accumulated information and falls with degeneracy,
 *    and the composed pose is `blend(matrix, solved, wSolve)`. A cold session
 *    (W = 0 everywhere) has wSolve = 0 by arithmetic and rides the matrix
 *    bit-for-bit — frame one is untouched, which is the invariant every
 *    stage re-pins. Few visible subset points (extreme yaw, a hand) → the
 *    facing weights collapse → wSolve → 0 → today's behaviour, continuously.
 *
 * Cost: |S| = 21 points × 2 Gauss–Newton iterations of ~150 flops, two 6×6
 * Cholesky solves and one inverse-power λ_min — well under 0.01 ms against
 * the replay's ~5–7 ms frame mean, hard-gated at +0.1 ms (B.4).
 */

import * as THREE from 'three';
import { RIGID_SUBSET } from './canonical-face.js';
import { W_MAX, W_PIN_FULL } from './person.js';
import { FACING_HOLD, FACING_TRUST } from './occluder.js';

/**
 * The Huber corner, in pixels — spec B.1's own number. Inside it a residual
 * is believed quadratically; past it, influence grows no further, so one
 * landmark the detector hallucinated (or a mouth mid-word moving the
 * philtrum) buys a bounded vote instead of dragging the pose. 2 px is the
 * psychophysical detection threshold the whole v3 effort is stated in — a
 * residual small enough to be invisible is exactly the regime where
 * quadratic (efficient) weighting is safe.
 */
export const HUBER_PX = 2;

/**
 * Gauss–Newton iterations per frame — spec B.1. Two, warm-started, because
 * the answer moves by fractions of a degree between frames: the first
 * iteration lands within the linearisation's validity, the second polishes.
 * More iterations measured nothing on the fixture but cost wall time.
 */
export const GN_ITERATIONS = 2;

/**
 * The gauge clamp — spec B.1/B.4: how far the solved pose may sit from the
 * MediaPipe matrix, in radians (3°) and centimetres. Sized from the quantity
 * the refit exists to remove: the matrix's measured pose-systematic error on
 * this wearer runs low-single-digit millimetres and sub-degree to ~1° of
 * redistribution (R0 rigidMiss, stage-4 wander spans) — a correction of 3°/
 * 1 cm covers every honest fix several times over, while bounding the
 * gauge-drift feedback loop absolutely. Engagements are counted, never
 * hidden; the B.4 kill criterion reads the counter.
 */
export const CLAMP_ROT_RAD = 3 * (Math.PI / 180);
export const CLAMP_POS_CM = 1.0;

/** Hermite smoothstep — the same shape every ramp in this pipeline uses. */
const smoothstep = (edge0, edge1, x) => {
  const t = Math.min(Math.max((x - edge0) / (edge1 - edge0), 0), 1);
  return t * t * (3 - 2 * t);
};

const solvedQuaternion = new THREE.Quaternion();
const solvedPosition = new THREE.Vector3();
const relQuaternion = new THREE.Quaternion();
const stepQuaternion = new THREE.Quaternion();
const stepAxis = new THREE.Vector3();
const scratchMatrix = new THREE.Matrix3();

/**
 * Area-weighted vertex normals for the canonical mesh, computed once — the
 * facing test needs the direction each subset vertex's surface looks, and the
 * canonical normal rotated by the pose is accurate to well under the ramp's
 * own width for a near-rigid face.
 */
function vertexNormals(face) {
  const normals = new Float32Array(face.vertexCount * 3);
  const { positions, indices } = face;
  for (let t = 0; t < indices.length; t += 3) {
    const a = indices[t] * 3;
    const b = indices[t + 1] * 3;
    const c = indices[t + 2] * 3;
    const abx = positions[b] - positions[a];
    const aby = positions[b + 1] - positions[a + 1];
    const abz = positions[b + 2] - positions[a + 2];
    const acx = positions[c] - positions[a];
    const acy = positions[c + 1] - positions[a + 1];
    const acz = positions[c + 2] - positions[a + 2];
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    for (const at of [a, b, c]) {
      normals[at] += nx;
      normals[at + 1] += ny;
      normals[at + 2] += nz;
    }
  }
  for (let i = 0; i < face.vertexCount; i++) {
    const at = i * 3;
    const len = Math.hypot(normals[at], normals[at + 1], normals[at + 2]) || 1;
    normals[at] /= len;
    normals[at + 1] /= len;
    normals[at + 2] /= len;
  }
  return normals;
}

/**
 * Cholesky-decomposes a symmetric 6×6 (lower triangle, in place into `L`).
 * Returns false when the matrix is not positive definite — the caller treats
 * that as "no solvable pose this frame" and falls back to the matrix whole.
 */
function cholesky6(H, L) {
  for (let i = 0; i < 6; i++) {
    for (let j = 0; j <= i; j++) {
      let sum = H[i * 6 + j];
      for (let k = 0; k < j; k++) sum -= L[i * 6 + k] * L[j * 6 + k];
      if (i === j) {
        if (!(sum > 1e-12)) return false;
        L[i * 6 + i] = Math.sqrt(sum);
      } else {
        L[i * 6 + j] = sum / L[j * 6 + j];
      }
    }
  }
  return true;
}

/** Solves L Lᵀ x = b in place using a finished `cholesky6` factor. */
function choleskySolve6(L, b, x) {
  for (let i = 0; i < 6; i++) {
    let sum = b[i];
    for (let k = 0; k < i; k++) sum -= L[i * 6 + k] * x[k];
    x[i] = sum / L[i * 6 + i];
  }
  for (let i = 5; i >= 0; i--) {
    let sum = x[i];
    for (let k = i + 1; k < 6; k++) sum -= L[k * 6 + i] * x[k];
    x[i] = sum / L[i * 6 + i];
  }
  return x;
}

/**
 * The smallest eigenvalue of a positive-definite 6×6, by inverse power
 * iteration on the Cholesky factor — eight solves, no allocation. This is
 * spec B.1's λ_min(JᵀJ): the information along the pose's least-constrained
 * direction, the degeneracy detector the confidence blend reads.
 */
function lambdaMin6(L, v, w) {
  for (let i = 0; i < 6; i++) v[i] = 1 / Math.sqrt(6);
  let lambda = 0;
  for (let iter = 0; iter < 8; iter++) {
    choleskySolve6(L, v, w);
    let norm = 0;
    for (let i = 0; i < 6; i++) norm += w[i] * w[i];
    norm = Math.sqrt(norm);
    if (!(norm > 1e-30)) return 0;
    for (let i = 0; i < 6; i++) v[i] = w[i] / norm;
    lambda = 1 / norm; // ‖H⁻¹v‖ → 1/λ_min as v converges
  }
  return lambda;
}

/**
 * Builds the refit for one canonical topology. All state is preallocated;
 * `solve` allocates nothing per frame.
 */
export function createPoseFit(face) {
  const subset = RIGID_SUBSET;
  const n = subset.length;
  const normals = vertexNormals(face);

  return {
    /** Last frame's solved divergence from the matrix — the warm start.
     * Stored RELATIVE (object-frame rotation, camera-frame offset) so it is
     * bounded by the clamp by construction and can never go stale across a
     * dropout: applied to THIS frame's matrix it always lands inside the
     * clamp region, and "no delta yet" is exactly "start from the matrix",
     * which is spec B.1's reset behaviour verbatim. */
    warmRot: new THREE.Quaternion(),
    warmPos: new THREE.Vector3(),
    hasWarm: false,

    /** Monotone counter — the replay differences it per segment (B.4). */
    clampEngagements: 0,

    // --- preallocated scratch ---
    px: new Float64Array(n), // subset camera-space points at the current pose
    py: new Float64Array(n),
    pz: new Float64Array(n),
    ox: new Float64Array(n), // scaled object points (scale × person.est)
    oy: new Float64Array(n),
    oz: new Float64Array(n),
    ux: new Float64Array(n), // observed pixels
    uy: new Float64Array(n),
    w: new Float64Array(n), // per-point weight (maturity × facing × r(pitch))
    H: new Float64Array(36),
    L: new Float64Array(36),
    g: new Float64Array(6),
    d: new Float64Array(6),
    ev: new Float64Array(6),
    ew: new Float64Array(6),

    /** Forgets the warm start — an identity change is a new gauge. */
    reset() {
      this.hasWarm = false;
      // The clamp counter is NOT cleared: it is a session-monotone event
      // count the replay differences, like the seat's.
    },

    /**
     * One frame's refit. Reads the person model, the observed landmarks and
     * the MediaPipe pose; writes the blended pose into `outPosition` /
     * `outQuaternion` and the instrumentation into `readout`. Returns the
     * readout. With nothing to stand on (no person model, W = 0, Cholesky
     * failure) wSolve is 0 and the outputs are the MediaPipe pose COPIED
     * bit-for-bit — the cold fallback is exact, not approximate.
     */
    solve({
      person, landmarks, mpPosition, mpQuaternion, scale,
      camera, width, height, pitchTrust = 1,
      outPosition, outQuaternion, readout,
    }) {
      const r = readout;
      r.wSolve = 0;
      r.gnIters = 0;
      r.residualPx = null;
      r.clampEngagedFrame = false;
      r.subsetVisible = 0;
      r.massFrac = 0;
      r.iso = null;
      r.clampEngagements = this.clampEngagements;

      outPosition.copy(mpPosition);
      outQuaternion.copy(mpQuaternion);

      if (!person || !landmarks || landmarks.length < face.vertexCount) return r;

      // --- the working pose: matrix ∘ last frame's bounded divergence ---
      if (this.hasWarm) {
        solvedQuaternion.copy(mpQuaternion).multiply(this.warmRot);
        solvedPosition.copy(mpPosition).add(this.warmPos);
      } else {
        solvedQuaternion.copy(mpQuaternion);
        solvedPosition.copy(mpPosition);
      }

      // Projection constants — the same pinhole `carryLandmarks` unprojects
      // through, run forward: ndc.x = P00·x/(−z), px = (ndc.x·0.5+0.5)·width.
      const pm = camera.projectionMatrix.elements;
      const ax = 0.5 * width * pm[0];
      const ay = 0.5 * height * pm[5];
      const cx0 = 0.5 * width;
      const cy0 = 0.5 * height;

      // --- per-point constants: object points, observations, weights ---
      // Weights are taken ONCE per frame at the warm-start pose (the pose
      // moves ≤ the clamp inside the solve — nothing a facing ramp can see):
      //
      //   w_i = (W_i / W_MAX) · facing_i · r(pitch)     — spec B.1
      //
      //  - W_i/W_MAX: the person model's own accumulated-information fraction
      //    for this vertex, against its own forgetting cap.
      //  - facing_i: the occluder's measured facing ramp (FACING_HOLD →
      //    FACING_TRUST on the normal-vs-ray dot) — the pipeline's facing
      //    law, HALF of its observation law: facing cannot detect a vertex
      //    hidden BEHIND other geometry (occluder.js states this about its
      //    own ramp), and at 30–45° yaw the far cheek/jaw/temple members are
      //    exactly that — camera-facing, landmark hallucinated, entering at
      //    full carried weight bounded only by Huber (landing verification,
      //    finding F5). Left as-is measurement-first: the candidate
      //    refinement (a visBehind-style hold) must be differenced against
      //    yaw-segment engagements in shadow mode before any weight changes
      //    — see the landing note's open items.
      //  - r(pitch): the POSE_TRUST pitch ramp, passed by the caller — the
      //    codebase's measured pitch-degradation curve. It enters UNIFORMLY,
      //    so the argmin never changes; only the blend's confidence mass
      //    falls with pitch. Its presence was measured BOTH ways on the real
      //    fixture (the landing note): without it the blend trusted the
      //    refit harder at deep pitch and the pitch-hold RMS worsened 4.60 →
      //    5.12 px — at 30–49° the subset's landmarks are noisy enough
      //    (solve residual ~4.3 px) that the matrix-plus-estimators carry
      //    the hold better, exactly the degradation the curve measured.
      const { est, W } = person;
      const qx = solvedQuaternion.x;
      const qy = solvedQuaternion.y;
      const qz = solvedQuaternion.z;
      const qw = solvedQuaternion.w;
      // Rotation matrix from the quaternion, once.
      scratchMatrix.set(
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw),
        2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw),
        2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy),
      );
      const m = scratchMatrix.elements; // column-major

      let mass = 0;
      let visible = 0;
      for (let k = 0; k < n; k++) {
        const v = subset[k];
        const at = v * 3;
        const sx = scale * est[at];
        const sy = scale * est[at + 1];
        const sz = scale * est[at + 2];
        this.ox[k] = sx;
        this.oy[k] = sy;
        this.oz[k] = sz;
        const pxc = m[0] * sx + m[3] * sy + m[6] * sz + solvedPosition.x;
        const pyc = m[1] * sx + m[4] * sy + m[7] * sz + solvedPosition.y;
        const pzc = m[2] * sx + m[5] * sy + m[8] * sz + solvedPosition.z;
        this.px[k] = pxc;
        this.py[k] = pyc;
        this.pz[k] = pzc;

        const lm = landmarks[v];
        this.ux[k] = lm.x * width;
        this.uy[k] = lm.y * height;

        // Facing at the warm-start pose: canonical normal through the pose's
        // rotation, against the view ray — the occluder's exact convention
        // (its dot is taken in face space against the face-space camera;
        // this is the same dot rotated into camera space).
        const nx = m[0] * normals[at] + m[3] * normals[at + 1] + m[6] * normals[at + 2];
        const ny = m[1] * normals[at] + m[4] * normals[at + 1] + m[7] * normals[at + 2];
        const nz = m[2] * normals[at] + m[5] * normals[at + 1] + m[8] * normals[at + 2];
        const plen = Math.hypot(pxc, pyc, pzc) || 1;
        const dot = -(nx * pxc + ny * pyc + nz * pzc) / plen;
        const t = Math.min(Math.max((dot - FACING_HOLD) / (FACING_TRUST - FACING_HOLD), 0), 1);
        const facing = t * t * (3 - 2 * t);

        const wi = Math.min(Math.max(W[v] / W_MAX, 0), 1) * facing * pitchTrust;
        this.w[k] = wi;
        mass += wi;
        if (facing > 0) visible++;
      }
      r.subsetVisible = visible;
      r.massFrac = mass / n;

      if (!(mass > 0)) return r;

      // --- Gauss–Newton, 2 iterations on se(3) about the subset centroid ---
      //
      // The twist is parametrised about the weighted CENTROID of the current
      // camera-space points, with the rotation generators scaled by the
      // subset's RMS radius about it. Both choices are conditioning, not
      // physics: about the camera origin a milliradian of rotation IS a
      // half-millimetre of translation (the lever is the 40–60 cm viewing
      // distance) and the normal matrix is degenerate by construction;
      // about the centroid the blocks decouple, and with the radius scaled
      // out both carry the same px/cm units — which is what makes λ_min
      // comparable across poses and distances.
      let iso = null;
      for (let iter = 0; iter < GN_ITERATIONS; iter++) {
        // Centroid and radius of the current points.
        let cxw = 0;
        let cyw = 0;
        let czw = 0;
        for (let k = 0; k < n; k++) {
          cxw += this.w[k] * this.px[k];
          cyw += this.w[k] * this.py[k];
          czw += this.w[k] * this.pz[k];
        }
        cxw /= mass;
        cyw /= mass;
        czw /= mass;
        let r2sum = 0;
        for (let k = 0; k < n; k++) {
          const dx = this.px[k] - cxw;
          const dy = this.py[k] - cyw;
          const dz = this.pz[k] - czw;
          r2sum += this.w[k] * (dx * dx + dy * dy + dz * dz);
        }
        const radius = Math.sqrt(r2sum / mass) || 1;

        this.H.fill(0);
        this.g.fill(0);
        const jrow = this.d; // reuse as a 6-wide row scratch

        for (let k = 0; k < n; k++) {
          const wi = this.w[k];
          if (!(wi > 0)) continue;
          const x = this.px[k];
          const y = this.py[k];
          const z = this.pz[k];
          if (!(z < -1e-6)) continue; // behind the camera — degenerate frame
          const iz = -1 / z;

          // Predicted pixel and residual.
          const rx = (cx0 + ax * x * iz) - this.ux[k];
          const ry = (cy0 - ay * y * iz) - this.uy[k];
          const rnorm = Math.hypot(rx, ry);
          const huber = rnorm <= HUBER_PX ? 1 : HUBER_PX / rnorm;
          const ww = wi * huber;

          // ∂pixel/∂point (2×3).
          const jxx = ax * iz;
          const jxz = ax * x * iz * iz;
          const jyy = -ay * iz;
          const jyz = -ay * y * iz * iz;

          // Twist columns: rotation e_k × (p − c), radius-normalised; then
          // translation. Row 1 (pixel x), row 2 (pixel y) share the columns.
          const qxr = (x - cxw) / radius;
          const qyr = (y - cyw) / radius;
          const qzr = (z - czw) / radius;
          // d p/dω'_x = (0, −qz, qy) etc., through ∂pixel/∂point:
          jrow[0] = jxz * qyr; // x-row, ω_x: jxx·0 + jxz·qy
          jrow[1] = jxx * qzr + jxz * (-qxr); // ω_y: (qz, 0, −qx)
          jrow[2] = jxx * (-qyr); // ω_z: (−qy, qx, 0) — z-component 0
          jrow[3] = jxx;
          jrow[4] = 0;
          jrow[5] = jxz;
          for (let i = 0; i < 6; i++) {
            const ji = jrow[i];
            if (ji === 0) continue;
            for (let j = 0; j <= i; j++) this.H[i * 6 + j] += ww * ji * jrow[j];
            this.g[i] += ww * ji * rx;
          }

          jrow[0] = jyy * (-qzr) + jyz * qyr; // y-row, ω_x: (0,−qz,qy)
          jrow[1] = jyz * (-qxr); // ω_y: (qz,0,−qx)
          jrow[2] = jyy * qxr; // ω_z: (−qy,qx,0)
          jrow[3] = 0;
          jrow[4] = jyy;
          jrow[5] = jyz;
          for (let i = 0; i < 6; i++) {
            const ji = jrow[i];
            if (ji === 0) continue;
            for (let j = 0; j <= i; j++) this.H[i * 6 + j] += ww * ji * jrow[j];
            this.g[i] += ww * ji * ry;
          }
        }

        // Mirror the lower triangle up.
        for (let i = 0; i < 6; i++) {
          for (let j = i + 1; j < 6; j++) this.H[i * 6 + j] = this.H[j * 6 + i];
        }

        if (!cholesky6(this.H, this.L)) return r; // degenerate → matrix whole

        // λ_min on the FIRST iteration's information — the blend reads the
        // geometry the step was solved under.
        if (iter === 0) {
          let tr = 0;
          for (let i = 0; i < 6; i++) tr += this.H[i * 6 + i];
          const lmin = lambdaMin6(this.L, this.ev, this.ew);
          iso = tr > 0 ? (6 * lmin) / tr : 0;
        }

        choleskySolve6(this.L, this.g, this.d);

        // Apply δ = −(H⁻¹ g): rotation about the centroid, then translation.
        const wxr = -this.d[0] / radius;
        const wyr = -this.d[1] / radius;
        const wzr = -this.d[2] / radius;
        const angle = Math.hypot(wxr, wyr, wzr);
        if (angle > 1e-12) {
          stepAxis.set(wxr / angle, wyr / angle, wzr / angle);
          stepQuaternion.setFromAxisAngle(stepAxis, angle);
          // p' = Rδ(p − c) + c + ν ⇒ R ← Rδ·R, t ← Rδ·(t − c) + c + ν.
          solvedQuaternion.premultiply(stepQuaternion).normalize();
          solvedPosition.sub(stepAxis.set(cxw, cyw, czw));
          solvedPosition.applyQuaternion(stepQuaternion);
          solvedPosition.add(stepAxis.set(cxw, cyw, czw));
        }
        solvedPosition.x -= this.d[3];
        solvedPosition.y -= this.d[4];
        solvedPosition.z -= this.d[5];
        r.gnIters = iter + 1;

        // Refresh the point set for the next iteration / final residual.
        const q2x = solvedQuaternion.x;
        const q2y = solvedQuaternion.y;
        const q2z = solvedQuaternion.z;
        const q2w = solvedQuaternion.w;
        scratchMatrix.set(
          1 - 2 * (q2y * q2y + q2z * q2z), 2 * (q2x * q2y - q2z * q2w), 2 * (q2x * q2z + q2y * q2w),
          2 * (q2x * q2y + q2z * q2w), 1 - 2 * (q2x * q2x + q2z * q2z), 2 * (q2y * q2z - q2x * q2w),
          2 * (q2x * q2z - q2y * q2w), 2 * (q2y * q2z + q2x * q2w), 1 - 2 * (q2x * q2x + q2y * q2y),
        );
        const m2 = scratchMatrix.elements;
        for (let k = 0; k < n; k++) {
          const sx = this.ox[k];
          const sy = this.oy[k];
          const sz = this.oz[k];
          this.px[k] = m2[0] * sx + m2[3] * sy + m2[6] * sz + solvedPosition.x;
          this.py[k] = m2[1] * sx + m2[4] * sy + m2[7] * sz + solvedPosition.y;
          this.pz[k] = m2[2] * sx + m2[5] * sy + m2[8] * sz + solvedPosition.z;
        }
      }

      // Final weighted RMS residual, for the readout and the replay column.
      let rsum = 0;
      let rn = 0;
      for (let k = 0; k < n; k++) {
        const wi = this.w[k];
        if (!(wi > 0) || !(this.pz[k] < -1e-6)) continue;
        const iz = -1 / this.pz[k];
        const rx = (cx0 + ax * this.px[k] * iz) - this.ux[k];
        const ry = (cy0 - ay * this.py[k] * iz) - this.uy[k];
        rsum += wi * (rx * rx + ry * ry);
        rn += wi;
      }
      r.residualPx = rn > 0 ? Math.sqrt(rsum / rn) : null;
      r.iso = iso;

      // --- the gauge clamp (B.1): bounded divergence from the matrix ---
      relQuaternion.copy(mpQuaternion).invert().multiply(solvedQuaternion);
      const relDot = Math.min(Math.abs(relQuaternion.w), 1);
      const relAngle = 2 * Math.acos(relDot);
      let engaged = false;
      if (relAngle > CLAMP_ROT_RAD) {
        // CAP, don't zero: slerp FROM the solved rotation TOWARD the matrix
        // by (1 − cap/angle), which leaves exactly CLAMP_ROT_RAD of the
        // divergence in place. The tempting mirror form —
        // `copy(mpQuaternion).slerp(solvedQuaternion, cap/angle)` — is a
        // self-aliasing bug: after the copy, `this` IS the slerp target, so
        // the interpolation runs from the matrix toward the matrix and the
        // clamp silently becomes a snap to the matrix (2026-08-17 landing
        // verification, finding F1: every engaged frame lost its whole
        // solved rotation AND its warm rotation, a ~wSolve·3° pre-smoother
        // pose snap on each rail crossing — measured as the fit reading
        // NOISIER than production at the cold ramp). The argument
        // quaternion here is never `this`, so no aliasing is possible.
        solvedQuaternion.slerp(mpQuaternion, 1 - CLAMP_ROT_RAD / relAngle);
        engaged = true;
      }
      const dxp = solvedPosition.x - mpPosition.x;
      const dyp = solvedPosition.y - mpPosition.y;
      const dzp = solvedPosition.z - mpPosition.z;
      const dist = Math.hypot(dxp, dyp, dzp);
      if (dist > CLAMP_POS_CM) {
        const kk = CLAMP_POS_CM / dist;
        solvedPosition.set(mpPosition.x + dxp * kk, mpPosition.y + dyp * kk,
          mpPosition.z + dzp * kk);
        engaged = true;
      }
      if (engaged) this.clampEngagements++;
      r.clampEngagedFrame = engaged;
      r.clampEngagements = this.clampEngagements;

      // --- the warm start for next frame: the bounded divergence itself ---
      this.warmRot.copy(mpQuaternion).invert().multiply(solvedQuaternion);
      this.warmPos.set(solvedPosition.x - mpPosition.x,
        solvedPosition.y - mpPosition.y, solvedPosition.z - mpPosition.z);
      this.hasWarm = true;

      // --- the confidence blend (B.1): wSolve = smooth(Σw_i, λ_min) ---
      //
      // The mass term is the subset's information against the codebase's own
      // "earned the pin" bar: Σw_i normalised by |S| vertices at pin-full
      // maturity (W_PIN_FULL of the model's W_MAX cap, the exact κ constant
      // the pin fusion trusts a person estimate at — spec B.2: the refit
      // replaces the PIN's carrier, so it earns the frame on the pin's own
      // constant). Saturating clamp, smoothed through the pipeline's own
      // smoothstep so the cold end engages with zero derivative: a cold
      // session is 0 by arithmetic, ~2–4 s of decent frontal observation is
      // full mass, and a thinning subset (extreme yaw, occlusion) fades it
      // continuously. Every factor is an existing constant (W_MAX,
      // W_PIN_FULL, |S|, the facing ramp) — no free threshold, per the v3
      // kill rule.
      //
      // λ_min enters as measured isotropy, 6λ_min/tr(H) — dimensionless
      // because the twist is radius-normalised above — against the measured
      // frontal reference ISO_REF, clamped to 1: a frame whose geometry is
      // as good as converged-frontal blends on its mass alone; a degenerate
      // frame (λ_min → 0) fades out linearly. This is what keeps a
      // near-collinear visible subset — the one configuration whose solved
      // pose can wander inside the gauge — from owning the frame.
      const isoTerm = iso === null ? 0
        : Math.min(Math.max(iso / ISO_REF, 0), 1);
      const pinFullMass = Math.min(mass / (n * (W_PIN_FULL / W_MAX)), 1);
      r.wSolve = smoothstep(0, 1, pinFullMass) * isoTerm;

      if (r.wSolve > 0) {
        outPosition.copy(mpPosition).lerp(solvedPosition, r.wSolve);
        outQuaternion.copy(mpQuaternion).slerp(solvedQuaternion, r.wSolve);
      }
      return r;
    },
  };
}

/**
 * The measured frontal isotropy reference (see the blend note above): the
 * fixture's converged frontal segments measure 6λ_min/tr(H) at 0.0797 (still)
 * / 0.0761 (eye-circles) — the weakest pose direction of a near-planar facial
 * subset (the pitch-vs-height ambiguity) genuinely carries ~8% of the mean
 * information at frontal, and that measured level IS full confidence. Poses
 * whose geometry is thinner (extreme yaw collapsing the subset toward a
 * line) fade the blend linearly below it. Pinned from the telemetry replay's
 * poseFitIsoMean on the real capture — a measured number with its
 * measurement in the landing note, not a tunable.
 *
 * KNOWN DOMAIN LIMIT (landing verification 2026-08-17, finding F2): this pin
 * is a ONE-POINT sample along viewing distance. λ_min rides the perspective
 * observability of the weak direction, which the radius normalisation does
 * not remove — measured on the real solver against the real mesh, converged
 * frontal iso runs ~1/z² (0.0846 at 35 cm, 0.0502 at 45, 0.0278 at 60,
 * 0.0176 at 75 — iso·z² constant within ~4%). A wearer sitting at 60–75 cm
 * therefore gets isoTerm 0.35–0.22 at PERFECT frontal convergence: the fit
 * quietly fades toward the matrix with distance. The failure direction is
 * conservative (less fit, never more), which is why it ships; but the spec'd
 * λ_min term is a DEGENERACY detector and distance is not degeneracy.
 * Deferred rather than patched because the honest fix — normalising the
 * measured 1/z² law out, or pinning ISO_REF as a curve vs |z| — must itself
 * be gated on a capture that exercises distance, and the pinned fixture is
 * single-distance: any constant chosen today could not pass its own gate
 * (the v3 kill rule). The next capture session should include a
 * near/mid/far still segment; until then the meaning of wSolve readings on
 * other wearers includes their distance, not only their convergence.
 */
export const ISO_REF = 0.08;

export const POSE_FIT_CONSTANTS = {
  HUBER_PX, GN_ITERATIONS, CLAMP_ROT_RAD, CLAMP_POS_CM, ISO_REF,
  SUBSET_SIZE: RIGID_SUBSET.length,
};
