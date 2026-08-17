/**
 * The person model — the accumulating face-space estimator that turns "the scan"
 * into a real scan.
 *
 * Everything else in this pipeline measures the wearer per frame and carries the
 * answer through medians and filters. What none of that can do is *converge*: a
 * median over 31 samples is as good on day one as it will ever be, and the one
 * axis a single camera cannot see — depth — stays borrowed from the average head
 * forever, corrected only by the affine depth fit, whose input is the noisiest
 * thing MediaPipe reports.
 *
 * This module is the missing timescale (T2 in the design: seconds-to-minutes).
 * Per vertex it runs a tiny anisotropic recursive estimator — an information
 * filter with a 3x3 symmetric matrix `A` and a vector `b` — fed the ray-pinned
 * observations the occluder already computes every frame. The anisotropy is the
 * whole idea: transverse to the view ray the observation is a true measurement
 * (the ray pinning puts the vertex exactly on its landmark), while along the ray
 * it is a borrowed or fitted guess, so the update weights the along-ray
 * component down by `W_PAR` = 100x. As the head yaws, the ray direction rotates
 * in face space, transverse planes from different poses intersect, and
 * `A_zz` literally accumulates sin² of the view angle — the parallax IS the
 * confidence. Depth converges from the user's own ±10–18° of natural browsing,
 * and MediaPipe's z never enters except through the frame-one prior.
 *
 * The model is layered UNDER the view-locked deform (see `measureShape`): the
 * drawn surface is `person.offsets + viewResidual`, so the mesh still covers
 * THIS image every frame while the slow estimate underneath converges silently.
 * Empty, the model contributes exactly zero — frame one is bit-identical to a
 * pipeline without it, which is the invariant every stage re-pins.
 */

import { LM } from './canonical-face.js';
import { noseMaskFor } from './occluder.js';

/**
 * How much an observation is worth along its own view ray, relative to
 * transverse. (0.5 mm ray-pinned landmark noise / 5 mm borrowed-depth error)²
 * — the observation is ~100x more informative across the ray than along it.
 */
export const W_PAR = 0.01;

/**
 * The canonical prior, in unit-weight frames. Conditions the 3x3 solve from
 * frame one (empty model → x̂ = rest exactly) while letting ~1 s of real
 * observation dominate. Added at SOLVE time (`A + λI`), never accumulated into
 * `A` itself — which is what keeps the zConf bookkeeping honest below.
 */
export const PRIOR_LAMBDA = 4;

/**
 * The forgetting cap, in unit-weight frames. ≈10–20 s of good viewing; past it
 * the filter becomes an exponential window with adaptation tau ≈ W_MAX/w̄ ≈
 * 20–60 s — converged but never frozen, so slow drift, glasses-removal marks
 * and lighting bias all heal.
 */
export const W_MAX = 300;

/**
 * How much accumulated parallax before a vertex's depth is trusted prior-free.
 * σ_z ≈ σ_xy/√A_zz = 0.5 mm/5 ≈ 0.1 mm at 25; at the user's ±10–18° browsing
 * (sin²15° ≈ 0.067) that needs ≈370 weighted frames ≈ 15–25 s of natural use.
 *
 * Measured against `zConf`, which is NOT `A_zz` raw — see the commit note.
 * Graft G15: every observation deposits `w·W_PAR` into `A_zz` even at zero
 * parallax (the along-ray share of the update), so a long frontal-only session
 * accumulates `W_PAR·W_i` of `A_zz` that is borrowed-depth information, not
 * triangulation. Expanding the update law:
 *
 *     A_zz = Σ w·(1 − (1−W_PAR)·d_z²)  =  W_PAR·W  +  (1−W_PAR)·Σ w·(1 − d_z²)
 *
 * so the exact prior-free parallax accumulator is
 *
 *     zConf = A_zz − W_PAR·W  =  (1−W_PAR)·Σ w·sin²θ
 *
 * with θ the angle between the view ray and the face-space z axis. On a purely
 * frontal stream sin²θ ≈ 0 at the bridge and zConf stays ≈ 0 whatever W does —
 * prior-only information can never reach Z_CONF_MIN by construction, and the
 * harness asserts it rather than trusting the algebra.
 */
export const Z_CONF_MIN = 25;

/** Pin-fusion maturity: full trust in ~2–4 s of decent observation. */
const W_PIN_FULL = 60;

/** Same bound the deform and the anchors use — a face constant, stated once. */
const OFFSET_LIMIT = 2.0;

/** Per-vertex online noise EMA: time constant and its cold-start value, cm. */
const RES_NOISE_TAU = 2.0;
export const RES_NOISE_INIT = 0.05;

/** Commit cadence: ≤ COMMIT_HZ = 2 Hz, amortised event work. */
const COMMIT_INTERVAL = 0.5;

/** The crossfade's onset ease, seconds — a commit-quantised zConf step never
 * lands on the recovered depth in one frame. */
const CROSSFADE_EASE_TAU = 0.5;

/**
 * G8, the sign-gate discipline: the zConf depth crossfade ships with its apply
 * gain at zero until the synthetic-truth sign gate (inject a +4 mm protrusion
 * error, BOTH yaw signs, assert recovery direction) and the G14 acceptance
 * gates (slide-injected convergence with bounded bias; converged depth stable
 * across the diag f00–f09 stills within 1.5 mm) pass as hard gates. This
 * codebase has shipped one sign bug already; the gates are run in the harness,
 * and the default flips only on a recorded pass (see the Stage-4 landing note
 * in ar/docs/nose-v2/spec.md for the measured values that set this flag).
 * `crossfadeOn` stays an instance flag so the harness can A/B it and a
 * regression can be shipped dark again without an API change.
 */
export const CROSSFADE_DEFAULT_ON = false;

/** G9 dual-baseline tripwire: person residual vs canonical, ratio and dwell. */
const TRIP_RATIO = 1.5;
const TRIP_SECONDS = 2.0;
/** The absolute tripwire the design carries alongside G9. */
const TRIP_ABS_RMS = 0.25;
const TRIP_ABS_SECONDS = 1.0;
/** Soft decay: A, b, W shed 10% per frame while a tripwire holds. */
const DECAY_KEEP = 0.9;

/** Huber gate floor on the transverse residual, cm. */
const HUBER_FLOOR = 0.1;

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

/**
 * Builds an empty person model for one canonical face topology.
 *
 * Memory: ~25 KB of typed arrays. Per-frame cost: one O(468) pass of ~25
 * multiplies per vertex in `accumulate`; the 3x3 solves run only in `commit`,
 * at ≤ 2 Hz. Everything is preallocated — nothing here allocates per frame.
 */
export function createPersonModel(face) {
  const count = face.vertexCount;
  const rest = face.positions.slice();
  const nose = noseMaskFor(face).flags;

  const person = {
    /** Per-vertex symmetric 3x3 information matrix (xx,xy,xz,yy,yz,zz), cm. */
    A: new Float32Array(count * 6),
    /** Information vector. */
    b: new Float32Array(count * 3),
    /** Accumulated scalar weight — unit = one full-trust frontal frame. */
    W: new Float32Array(count),
    /** Committed estimate x̂ (face-space positions). Starts at rest. */
    est: rest.slice(),
    /** x̂ − rest, clamped ±OFFSET_LIMIT — what the occluder layers on. */
    offsets: new Float32Array(count * 3),
    /** The G15 parallax accumulator: A_zz − W_PAR·W, refreshed at commit. */
    zConf: new Float32Array(count),
    /** The crossfade weight actually applied: eased toward the zConf target. */
    zWeight: new Float32Array(count),
    /** Commit-time crossfade target, clamp(zConf/Z_CONF_MIN, 0, 1). */
    zTarget: new Float32Array(count),
    /** Per-vertex online |transverse innovation| EMA, cm. */
    resNoise: new Float32Array(count).fill(RES_NOISE_INIT),
    /** Δoffsets of the last commit — handed back for viewResidual re-basing. */
    deltaOffsets: new Float32Array(count * 3),
    /** Scratch for the residual median. */
    scratch: new Float64Array(count),

    rest,
    count,
    nose,

    // --- scalars and readouts (`__ar.person` reads these fields directly) ---
    frames: 0,
    commits: 0,
    resets: 0,
    decays: 0,
    /** Robust median |transverse residual| over vertices with W > 10, mm. */
    residualRmsMm: 0,
    meanW: 0,
    noseMeanW: 0,
    zConfBridge: 0,
    tripwireActive: false,
    lastDecayCause: null,
    tripwireSeconds: 0,
    crossfadeOn: CROSSFADE_DEFAULT_ON,

    // --- internal clocks ---
    sinceCommit: 0,
    regressSeconds: 0,
    absSeconds: 0,

    /**
     * Folds one frame of ray-pinned observations into the information state.
     *
     * `observed`: the RAW carried points from `carryLandmarks` (pre-shrinkage —
     * graft G16: the render-path shrinkage must never starve the estimator).
     * `cameraInFace`: the camera position in face space ({x,y,z}).
     * `trust`: per-vertex facing×visibility weight, the deform's own gains.
     * `wPose`: the continuous pose trust of this frame.
     */
    accumulate(observed, cameraInFace, trust, wPose, dt) {
      const { A, b, W, est, resNoise, nose: mask, scratch } = this;
      const cx = cameraInFace.x; const cy = cameraInFace.y; const cz = cameraInFace.z;
      const alphaNoise = 1 - Math.exp(-Math.max(dt, 0) / RES_NOISE_TAU);
      const c = 1 - W_PAR;

      let residCount = 0;
      let sumW = 0;
      let noseSumW = 0;
      let noseN = 0;
      let nosePerson = 0;
      let noseCanon = 0;
      let noseResidN = 0;

      for (let i = 0; i < this.count; i++) {
        const at3 = i * 3;
        const px = observed[at3];
        const py = observed[at3 + 1];
        const pz = observed[at3 + 2];

        // The view ray through this observation, in face space.
        let dx = px - cx; let dy = py - cy; let dz = pz - cz;
        const dl = Math.hypot(dx, dy, dz);
        if (!(dl > 1e-6) || !Number.isFinite(px + py + pz)) {
          sumW += W[i];
          if (mask[i]) { noseSumW += W[i]; noseN++; }
          continue;
        }
        dx /= dl; dy /= dl; dz /= dl;

        // Stability self-downweight: a vertex whose own innovations run noisy
        // (the nose tip, measured 2–6x worse) buys less per frame.
        //
        // Pose trust enters SQUARED. Linear was measured live (stage 6): once the
        // trust tails were widened to cover real 30–40° holds, w ≈ 0.3 learned the
        // foreshortened, slid geometry of the turn fast enough to churn the
        // surface — 203 rebuilds and 29 guard pushes in one turn-and-return —
        // and then spent the frontal seconds unlearning it. Squaring keeps the
        // defence (tripwires arm on wPose, admission unchanged) while a 0.35-trust
        // observation buys an eighth of what it did: the model listens at every
        // pose and BELIEVES roughly in proportion to what the pose can actually
        // testify to.
        const noise = resNoise[i];
        let w = wPose * wPose * trust[i] / (1 + (noise / RES_NOISE_INIT) ** 2);

        // Transverse residual against the committed estimate — the only
        // component the observation actually measures.
        const rx = px - est[at3];
        const ry = py - est[at3 + 1];
        const rz = pz - est[at3 + 2];
        const along = rx * dx + ry * dy + rz * dz;
        const tx = rx - along * dx;
        const ty = ry - along * dy;
        const tz = rz - along * dz;
        const rt = Math.hypot(tx, ty, tz);

        // Huber gate: past 3x the vertex's own noise scale, influence grows
        // no further — a hand across the face buys one noise-quantum, not a
        // reshape.
        const huber = 3 * Math.max(HUBER_FLOOR, noise);
        if (rt > huber) w *= huber / rt;

        // The online noise estimate follows only samples the gate trusted.
        if (w > 0.2) resNoise[i] += (rt - noise) * alphaNoise;

        // G9's dual baseline, in the same pass: the nose window's transverse
        // residual against BOTH the person estimate and the canonical rest —
        // a person surface that fits worse than the average head is regression,
        // whatever the absolute number says.
        if (mask[i]) {
          noseN++;
          if (trust[i] > 0.2) {
            const cxr = px - this.rest[at3];
            const cyr = py - this.rest[at3 + 1];
            const czr = pz - this.rest[at3 + 2];
            const alongC = cxr * dx + cyr * dy + czr * dz;
            noseCanon += Math.hypot(cxr - alongC * dx, cyr - alongC * dy, czr - alongC * dz);
            nosePerson += rt;
            noseResidN++;
          }
        }

        if (w > 0) {
          // The anisotropic information update: M = w·(I − (1−W_PAR)·d dᵀ).
          const Mxx = w * (1 - c * dx * dx);
          const Myy = w * (1 - c * dy * dy);
          const Mzz = w * (1 - c * dz * dz);
          const Mxy = -w * c * dx * dy;
          const Mxz = -w * c * dx * dz;
          const Myz = -w * c * dy * dz;
          const at6 = i * 6;
          A[at6] += Mxx; A[at6 + 1] += Mxy; A[at6 + 2] += Mxz;
          A[at6 + 3] += Myy; A[at6 + 4] += Myz; A[at6 + 5] += Mzz;
          b[at3] += Mxx * px + Mxy * py + Mxz * pz;
          b[at3 + 1] += Mxy * px + Myy * py + Myz * pz;
          b[at3 + 2] += Mxz * px + Myz * py + Mzz * pz;
          W[i] += w;

          // Forgetting: past the cap the whole information state scales down,
          // which preserves x̂ exactly while turning the filter exponential.
          if (W[i] > W_MAX) {
            const k = W_MAX / W[i];
            A[at6] *= k; A[at6 + 1] *= k; A[at6 + 2] *= k;
            A[at6 + 3] *= k; A[at6 + 4] *= k; A[at6 + 5] *= k;
            b[at3] *= k; b[at3 + 1] *= k; b[at3 + 2] *= k;
            W[i] = W_MAX;
          }
        }

        sumW += W[i];
        if (mask[i]) noseSumW += W[i];
        if (W[i] > 10) scratch[residCount++] = rt;

        // The crossfade weight eases toward its commit-time target every
        // frame, so a quantised zConf step reads as a 0.5 s fade, never a pop.
        this.zWeight[i] += (this.zTarget[i] - this.zWeight[i])
          * (1 - Math.exp(-Math.max(dt, 0) / CROSSFADE_EASE_TAU));
      }

      this.frames++;
      this.meanW = sumW / this.count;
      this.noseMeanW = noseN ? noseSumW / noseN : 0;
      this.zConfBridge = this.zConf[LM.NOSE_BRIDGE];

      // Robust residual: the median is untouched by the handful of vertices a
      // hand or a hallucination moves, which is exactly why the tripwires can
      // read it without flinching at outliers.
      if (residCount > 8) {
        const view = scratch.subarray(0, residCount);
        view.sort();
        this.residualRmsMm = view[residCount >> 1] * 10;
      }

      // --- tripwires ---
      // Armed only while the pose is trusted enough for the residuals to MEAN
      // anything. At a hard yaw the observations are foreshortened and
      // half-hallucinated, so the residual reads large against ANY correct
      // model — the first live session (stage 6) measured 62 soft-decays, most of
      // them fired during turns, bleeding a converged estimate for the crime of
      // being observed from the side. Below the bar the timers FREEZE rather than
      // reset: a genuine change (glasses on at a yaw) resumes counting the moment
      // the pose recovers, and loses nothing.
      //
      // The bar is 0.6, not 0.3: at 0.3 the same session's SECOND round measured
      // 44 decays during ordinary browsing — the absolute rule kept firing on
      // ordinary mid-turn foreshortening (w 0.3–0.5 residuals routinely read
      // past 2.5 mm on a correct model). The tripwires exist to catch glasses-on
      // and hairstyle changes, which are visible head-on; nothing about them
      // needs to run mid-turn.
      if (wPose >= 0.6) {
        // G9: the person baseline reading worse than the canonical one by 50%,
        // continuously for 2 s, is regression — decay softly, never step.
        const regressed = noseResidN > 8
          && nosePerson > noseCanon * TRIP_RATIO + noseResidN * 0.005;
        this.regressSeconds = regressed ? this.regressSeconds + dt : 0;
        // The absolute rule: a residual past 2.5 mm sustained for 1 s means the
        // model no longer describes what the camera sees (glasses on, hair).
        const absolute = residCount > 8 && this.residualRmsMm > TRIP_ABS_RMS * 10;
        this.absSeconds = absolute ? this.absSeconds + dt : 0;

        const g9 = this.regressSeconds >= TRIP_SECONDS;
        const abs = this.absSeconds >= TRIP_ABS_SECONDS;
        this.tripwireActive = g9 || abs;
        if (this.tripwireActive) {
          this.tripwireSeconds += dt;
          this.softDecay(g9 ? 'regression' : 'residual');
        }
      }
    },

    /**
     * Solves the closed-form estimate and refreshes everything derived, at
     * ≤ 2 Hz. Returns the per-vertex offset delta so the caller can re-base
     * `viewResidual` — a commit is invisible by construction: the composite
     * `person.offsets + viewResidual` is continuous across it, no step lands
     * on the surface, and no rebuild is caused.
     */
    commit() {
      const { A, b, W, est, offsets, deltaOffsets, rest, zConf, zTarget } = this;
      for (let i = 0; i < this.count; i++) {
        const at6 = i * 6;
        const at3 = i * 3;
        // (A + λI) x = b + λ·rest — symmetric 3x3 by cofactors. λ ≥ 4 keeps
        // the matrix strictly positive definite, so the determinant is never
        // near zero and the branch-free solve is safe.
        const axx = A[at6] + PRIOR_LAMBDA;
        const axy = A[at6 + 1];
        const axz = A[at6 + 2];
        const ayy = A[at6 + 3] + PRIOR_LAMBDA;
        const ayz = A[at6 + 4];
        const azz = A[at6 + 5] + PRIOR_LAMBDA;
        const bx = b[at3] + PRIOR_LAMBDA * rest[at3];
        const by = b[at3 + 1] + PRIOR_LAMBDA * rest[at3 + 1];
        const bz = b[at3 + 2] + PRIOR_LAMBDA * rest[at3 + 2];

        const c00 = ayy * azz - ayz * ayz;
        const c01 = axz * ayz - axy * azz;
        const c02 = axy * ayz - axz * ayy;
        const det = axx * c00 + axy * c01 + axz * c02;
        const inv = 1 / det;
        const c11 = axx * azz - axz * axz;
        const c12 = axy * axz - axx * ayz;
        const c22 = axx * ayy - axy * axy;

        const x = (c00 * bx + c01 * by + c02 * bz) * inv;
        const y = (c01 * bx + c11 * by + c12 * bz) * inv;
        const z = (c02 * bx + c12 * by + c22 * bz) * inv;

        est[at3] = x; est[at3 + 1] = y; est[at3 + 2] = z;

        const ox = clamp(x - rest[at3], -OFFSET_LIMIT, OFFSET_LIMIT);
        const oy = clamp(y - rest[at3 + 1], -OFFSET_LIMIT, OFFSET_LIMIT);
        const oz = clamp(z - rest[at3 + 2], -OFFSET_LIMIT, OFFSET_LIMIT);
        deltaOffsets[at3] = ox - offsets[at3];
        deltaOffsets[at3 + 1] = oy - offsets[at3 + 1];
        deltaOffsets[at3 + 2] = oz - offsets[at3 + 2];
        offsets[at3] = ox; offsets[at3 + 1] = oy; offsets[at3 + 2] = oz;

        // G15's bookkeeping, exactly as derived on Z_CONF_MIN above: subtract
        // the W_PAR-weighted share every observation deposits regardless of
        // parallax, so what remains is (1−W_PAR)·Σ w·sin²θ — pure
        // triangulation information, zero on a frontal-only stream.
        zConf[i] = Math.max(A[at6 + 5] - W_PAR * W[i], 0);
        zTarget[i] = clamp(zConf[i] / Z_CONF_MIN, 0, 1);
      }
      this.commits++;
      this.zConfBridge = zConf[LM.NOSE_BRIDGE];
      return deltaOffsets;
    },

    /** The ≤2 Hz commit clock, advanced by the occluder's own dt. */
    commitDue(dt) {
      this.sinceCommit += Math.max(dt, 0);
      if (this.sinceCommit < COMMIT_INTERVAL) return false;
      this.sinceCommit = 0;
      return true;
    },

    /** The fused pin base: the committed bridge estimate. */
    bridgeEstimate(out = { x: 0, y: 0, z: 0 }) {
      const at = LM.NOSE_BRIDGE * 3;
      out.x = this.est[at];
      out.y = this.est[at + 1];
      out.z = this.est[at + 2];
      return out;
    },

    /** Pin-fusion maturity κ — how much of the base the estimate owns. */
    bridgeMaturity() {
      return clamp(this.W[LM.NOSE_BRIDGE] / W_PIN_FULL, 0, 1);
    },

    /**
     * The zConf depth crossfade (G8-gated): blends a recovered camera depth
     * toward the person surface's own camera depth by the vertex's eased
     * parallax trust. `depth` is whatever the borrowed/fitted path produced;
     * `e` is the raw head matrix's elements. With the gain off, or a vertex
     * short of parallax, the input passes through untouched — bit for bit.
     */
    depthFor(i, depth, e) {
      if (!this.crossfadeOn) return depth;
      const w = this.zWeight[i];
      if (!(w > 0)) return depth;
      const at = i * 3;
      const personDepth = e[2] * this.est[at] + e[6] * this.est[at + 1]
        + e[10] * this.est[at + 2] + e[14];
      if (!Number.isFinite(personDepth)) return depth;
      return depth + (personDepth - depth) * w;
    },

    /** Identity change: everything measured is gone; the priors remain. */
    reset() {
      this.A.fill(0);
      this.b.fill(0);
      this.W.fill(0);
      this.est.set(this.rest);
      this.offsets.fill(0);
      this.deltaOffsets.fill(0);
      this.zConf.fill(0);
      this.zWeight.fill(0);
      this.zTarget.fill(0);
      this.resNoise.fill(RES_NOISE_INIT);
      this.frames = 0;
      this.sinceCommit = 0;
      this.regressSeconds = 0;
      this.absSeconds = 0;
      this.tripwireActive = false;
      this.residualRmsMm = 0;
      this.meanW = 0;
      this.noseMeanW = 0;
      this.zConfBridge = 0;
      this.resets++;
    },

    /**
     * The tripwires' self-healing: shed 10% of the information per frame while
     * disagreement holds. x̂ decays toward the prior smoothly (the solve blends
     * by weight), the crossfade weight follows zConf down through its own
     * ease, and nothing visible steps.
     */
    softDecay(cause) {
      const { A, b, W, zConf } = this;
      for (let i = 0; i < this.count; i++) {
        const at6 = i * 6;
        const at3 = i * 3;
        A[at6] *= DECAY_KEEP; A[at6 + 1] *= DECAY_KEEP; A[at6 + 2] *= DECAY_KEEP;
        A[at6 + 3] *= DECAY_KEEP; A[at6 + 4] *= DECAY_KEEP; A[at6 + 5] *= DECAY_KEEP;
        b[at3] *= DECAY_KEEP; b[at3 + 1] *= DECAY_KEEP; b[at3 + 2] *= DECAY_KEEP;
        W[i] *= DECAY_KEEP;
        // zConf is linear in A and W, so the decayed value is exact, not stale.
        zConf[i] *= DECAY_KEEP;
        this.zTarget[i] = clamp(zConf[i] / Z_CONF_MIN, 0, 1);
      }
      this.decays++;
      this.lastDecayCause = cause;
    },
  };

  return person;
}

export const PERSON_CONSTANTS = {
  W_PAR, PRIOR_LAMBDA, W_MAX, Z_CONF_MIN, W_PIN_FULL,
  RES_NOISE_INIT, RES_NOISE_TAU, COMMIT_INTERVAL, CROSSFADE_EASE_TAU,
  TRIP_RATIO, TRIP_SECONDS, TRIP_ABS_RMS, TRIP_ABS_SECONDS, DECAY_KEEP,
  CROSSFADE_DEFAULT_ON,
};
