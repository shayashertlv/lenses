/**
 * DIAG REPLAY — the permanent regression fixture for the nose-pipeline rework.
 *
 * This page is Stage 0 of `ar/docs/nose-v2/spec.md`: it pins the CURRENT
 * pipeline's numbers on the user's own complaint captures so every later stage
 * is judged as a measured delta against this file, not as an impression. It is
 * the formalisation of `diag-probe.js` (which stays as scratch): the same
 * no-rAF bootstrap, the same main-thread tracker, the same REAL production
 * `updateFrame` path — smoothing on, adaptToFace on, occluder deforming,
 * DEFAULT_MODEL — over the twelve stills, fresh state per still.
 *
 * SEED DISCIPLINE. `detectForVideo` is bit-for-bit deterministic on identical
 * pixels (measured: every std-dev 0.000 over 60 repeats on frozen stills), so
 * the only randomness in the whole run is the injected sensor noise — and that
 * is drawn from one mulberry32 PRNG seeded with `REPLAY_SEED`, consumed in a
 * fixed order (noise tile first, then exactly two draws per iteration for the
 * tile offset, misses included). Two runs of this page on the same machine
 * therefore produce IDENTICAL numbers. The amplitude matches the probe run the
 * diagnosis empirics were measured at: a white-noise tile alpha-blended at
 * 0.03 ≈ σ 2.2 digital numbers, an ordinary indoor webcam.
 *
 * BASELINE MODE vs ASSERT MODE. The page fetches `./diag-baseline.json`:
 *   - absent  → baseline mode: the measured numbers are printed into #out and
 *     exposed as `window.__replay.baseline`, ready to be saved as that file;
 *   - present → assert mode: every metric is compared against the baseline
 *     within tolerance and reported PASS/FAIL, pipeline-check style.
 *
 * TOLERANCES (judge-mandated: per-machine numeric baselines, NOT bit-exact
 * snapshots — MediaPipe determinism is per-machine, and later stages will annul
 * bit-exactness anyway):
 *   px metrics        ±25% relative or ±0.05 px absolute, whichever is larger
 *   mm metrics        ±20% relative or ±0.2 mm absolute, whichever is larger
 *   counts            ±20% relative (a baseline of zero must stay zero)
 *   fractions/ratios  ±0.1 absolute
 * updateFrame wall time is reported per still, never asserted per still —
 * timing is the one number that is not deterministic on a shared machine. The
 * aggregate mean IS asserted since stage 3, against 1.2× the stage-0 pinned
 * baseline (see STAGE0_WALL_MS), which is the spec's ceiling on added work.
 *
 * TO RE-BASELINE (only when a stage's delta has been reviewed and accepted):
 * delete `ar/tests/diag-baseline.json`, reload this page, and save the JSON it
 * prints (`window.__replay.baseline`) back to that path. The baseline is a
 * checked-in artifact: a re-pin without a reviewed delta is a silent regression
 * with a green suite, which is exactly what this fixture exists to prevent.
 *
 * Runs without requestAnimationFrame so it works in a hidden tab, exactly like
 * pipeline-check. Results land in `window.__replay` (and `window.__results` /
 * `window.__done` in assert mode, for the same pollers pipeline-check feeds).
 */

import * as THREE from 'three';

import { loadCanonicalFace, LM } from '../src/canonical-face.js';
import { MODELS, DEFAULT_MODEL, loadGlassesModel } from '../src/models.js';
import { createTracker } from '../src/tracker.js';
import { createScene } from '../src/scene.js';
import { createOccluder, surfaceOf } from '../src/occluder.js';
import { analyseModel, DEFAULT_FIT, solvePlacement } from '../src/fit.js';
import { updateFrame } from '../src/frame.js';
import { PoseSmoother, DEFAULT_SMOOTHING } from '../src/smoothing.js';
import { createSampleSource } from '../src/sources.js';
import { createPersonModel } from '../src/person.js';

/**
 * The seed. A constant on purpose: the fixture's whole value is that the same
 * pixels go through the same pipeline every run. Change it and every number in
 * the checked-in baseline is stale — so don't.
 */
const REPLAY_SEED = 20260816;

/** Matches diag-probe: alpha 0.03 over uniform noise ≈ σ 2.2 DN of sensor noise. */
const NOISE_ALPHA = 0.03;

/**
 * The stage-3 wall-time budget (spec stage plan: "updateFrame wall-time assert
 * (≤1.2× baseline) lands HERE, on the harness profile").
 *
 * The reference is the STAGE-0 pinned in-pane mean — 11.43 ms — not whatever the
 * rolling baseline file says: the rolling number ratchets downward with the
 * stages (stage 2 re-pinned at 10.50), and a budget that shrank with every
 * improvement would eventually fail a stage for being merely as fast as the last
 * one. 1.2× the stage-0 mean is the spec's own ceiling on how much per-frame
 * work the whole rework may add, so it is asserted against that constant.
 * Deterministic it is not — this is the one number scheduler noise can touch —
 * which is why it gets ×1.2 of headroom where every other metric gets tolerance
 * bands.
 */
const STAGE0_WALL_MS = 11.43;
const WALL_BUDGET_FACTOR = 1.2;

/** 3 warmup detections (VIDEO mode carries ROI across stills), 60 measured. */
const WARMUP = 3;
const MEASURED = 60;

/**
 * The two frontal StyleGAN portraits the old harness always had, plus the
 * user's ten complaint captures (supine on a pillow, yaw −18..+10°, pitch to
 * −16°, roll, blur, close range) — the regime all three complaints live in.
 */
const STILLS = [
  '../assets/samples/face-a.jpg',
  '../assets/samples/face-b.jpg',
  '../assets/samples/diag/f00.png',
  '../assets/samples/diag/f01.png',
  '../assets/samples/diag/f02.png',
  '../assets/samples/diag/f03.png',
  '../assets/samples/diag/f04.png',
  '../assets/samples/diag/f05.png',
  '../assets/samples/diag/f06.png',
  '../assets/samples/diag/f07.png',
  '../assets/samples/diag/f08.png',
  '../assets/samples/diag/f09.png',
];

/**
 * The cross-still aggregates only make sense over ONE person's stills — the
 * bridge is a face-space constant on a rigid face, so its per-still means
 * SHOULD agree, and how far they don't is the diagnosed wander. face-a/b are
 * different people and would report anatomy, not error.
 */
const DIAG_SET = new Set(['f00.png', 'f01.png', 'f02.png', 'f03.png', 'f04.png',
  'f05.png', 'f06.png', 'f07.png', 'f08.png', 'f09.png']);

/**
 * mulberry32 — a tiny, well-distributed 32-bit PRNG. Chosen over anything
 * fancier because the requirement is only "the same stream every run", and a
 * one-liner that fits in a code review beats a library import that doesn't.
 */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The one stream. Tile generation draws first, then two draws per iteration. */
const rand = mulberry32(REPLAY_SEED);

/** Seeded copy of diag-probe's noise tile — same size, same uniform amplitude. */
const noiseTile = (() => {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 512;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(512, 512);
  for (let i = 0; i < img.data.length; i += 4) {
    img.data[i] = rand() * 256;
    img.data[i + 1] = rand() * 256;
    img.data[i + 2] = rand() * 256;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
})();

/**
 * Repaints the still from its pristine copy, then blends one frame of noise at
 * a seeded offset. Exactly diag-probe's `repaintWithNoise`, with the PRNG in
 * place of Math.random — the two draws happen unconditionally so the stream
 * position never depends on what the detector did with the previous frame.
 */
function repaintWithNoise(element, pristine) {
  const ox = -Math.floor(rand() * 512);
  const oy = -Math.floor(rand() * 512);
  const ctx = element.getContext('2d');
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.drawImage(pristine, 0, 0);
  ctx.globalAlpha = NOISE_ALPHA;
  for (let y = oy; y < element.height; y += 512) {
    for (let x = ox; x < element.width; x += 512) ctx.drawImage(noiseTile, x, y);
  }
  ctx.restore();
}

// ---------------------------------------------------------------- small maths

const round = (x) => (Number.isFinite(x) ? Math.round(x * 10000) / 10000 : x);

const stats = (values) => {
  const v = values.filter(Number.isFinite);
  if (!v.length) return { n: 0, mean: null, sd: null };
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length);
  return { n: v.length, mean: round(mean), sd: round(sd) };
};

// ------------------------------------------------------------------ reporting

const statusEl = document.getElementById('status');
const outEl = document.getElementById('out');
const summaryEl = document.getElementById('summary');
const say = (text) => { statusEl.textContent = text; };

const results = [];
const record = (name, pass, detail) => {
  results.push({ name, pass, detail });
  const line = document.createElement('div');
  line.className = `line ${pass ? 'ok' : 'fail'}`;
  line.textContent = `${pass ? 'PASS' : 'FAIL'}  ${name} — ${detail}`;
  outEl.append(line);
};

// ------------------------------------------------------------------- one still

const poseScratch = new THREE.Vector3();

async function replayStill({ url, face, scene, model, tracker, nextTs }) {
  const name = url.split('/').pop();
  const source = await createSampleSource(new URL(url, import.meta.url).href);
  source.update(0);
  scene.resize(source.width, source.height, source.width / source.height);

  // Pristine copy, so each iteration's noise is fresh rather than accumulating.
  const pristine = document.createElement('canvas');
  pristine.width = source.width;
  pristine.height = source.height;
  pristine.getContext('2d').drawImage(source.element, 0, 0);

  // Fresh state per still — each still models a session that STARTS in that
  // pose, which is what makes the measuring fraction and the canonical
  // fallbacks honest (a session lived on the pillow, not one that measured
  // frontally first and then rolled over). Since stage 2, `measuringLatch` is
  // the derived readout alias (w_pose > 0.5, graft G12) rather than a gate, so
  // `measuringFraction` reads "fraction of frames at a confident pose".
  const occluder = createOccluder(face);
  const state = { occluder };
  const smoother = new PoseSmoother(DEFAULT_SMOOTHING);
  const fit = { ...DEFAULT_FIT };

  const rows = [];
  let misses = 0;
  let wallMs = 0;
  let rebuildsAt30 = null;

  for (let k = 0; k < WARMUP + MEASURED; k++) {
    repaintWithNoise(source.element, pristine);
    const detection = tracker.detect(source.element, nextTs());
    if (!detection) { if (k >= WARMUP) misses++; continue; }

    const t0 = performance.now();
    const result = updateFrame({ scene, face, model, fit, smoother, state, source,
      detection, dt: 1 / 30, smoothing: true, adaptToFace: true, temples: null });
    const t1 = performance.now();

    // Warmup frames warm the production state too (probe discipline), but only
    // measured frames land in the rows or the wall-time mean.
    if (k < WARMUP) continue;
    wallMs += t1 - t0;

    const row = {
      measuring: state.measuringLatch === true ? 1 : 0,
      bridgeX: result.anchors.bridge.x,
      bridgeY: result.anchors.bridge.y,
      bridgeZ: result.anchors.bridge.z,
      noseWidthRatio: result.anchors.noseWidthRatio ?? null,
    };

    const data = occluder.userData;
    row.r2 = data.depthFit?.r2 ?? null;
    row.fitWeight = data.depthFit?.weight ?? null;

    // Visibility holds: vertices with meaningful cover along their own view ray
    // (the same 0.35 = VIS_BIAS reading the probe used).
    let visHeld = 0;
    const behind = data.visibility?.behind;
    if (behind) for (let i = 0; i < behind.length; i++) if (behind[i] > 0.35) visHeld++;
    row.visHeld = visHeld;

    const seat = result.placement?.noseSeat;
    row.seatPush = seat?.push ?? null;
    row.seatEased = seat?.easedPush ?? null;

    if (result.placement) {
      // The placement's own face-space position, kept per row so a jitter
      // number can be decomposed to the channel that caused it (the summary
      // never carries these — they are a debugging handle, not a metric).
      row.px = result.placement.position.x;
      row.py = result.placement.position.y;
      row.pz = result.placement.position.z;
      // The number a viewer sees: the placed frame's origin, projected to
      // pixels through the SMOOTHED head pose the composite draws. On a still
      // image every pixel of movement here is pipeline-generated jitter.
      poseScratch.copy(result.placement.position)
        .applyMatrix4(scene.head.matrixWorld).project(scene.camera);
      row.screenX = (poseScratch.x * 0.5 + 0.5) * source.width;
      row.screenY = (1 - (poseScratch.y * 0.5 + 0.5)) * source.height;
    }

    rows.push(row);
    // Where the settled tail begins — so the rebuild count can be split into
    // the fresh-session warm-up and the sustained rate the C6 target names.
    if (rows.length === 30) rebuildsAt30 = occluder.userData.rebuilds?.surface ?? null;
  }

  // Per-frame rows, for post-hoc decomposition in the console. Deliberately NOT
  // part of the returned summary: the baseline JSON must stay a small, reviewed
  // artifact, and these exist to answer "which channel moved" when a summary
  // number does.
  (window.__replayRows = window.__replayRows ?? {})[name] = rows;

  const of = (pick) => stats(rows.map(pick));

  // Screen jitter: per-axis sd, the 2D RMS about the mean (what "the frame
  // vibrates" measures), and the single worst frame-to-frame step (what a pop
  // measures — an sd can hide one).
  const sx = of((r) => r.screenX);
  const sy = of((r) => r.screenY);
  const rmsPx = Number.isFinite(sx.sd) && Number.isFinite(sy.sd)
    ? round(Math.hypot(sx.sd, sy.sd)) : null;
  let maxStepPx = null;
  for (let i = 1; i < rows.length; i++) {
    const a = rows[i - 1]; const b = rows[i];
    if (![a.screenX, a.screenY, b.screenX, b.screenY].every(Number.isFinite)) continue;
    const step = Math.hypot(b.screenX - a.screenX, b.screenY - a.screenY);
    if (maxStepPx === null || step > maxStepPx) maxStepPx = step;
  }

  // The settled tail (measured frames 30–59), pinned since stage 4. Each
  // still is a FRESH session, so the full window carries the estimators'
  // convergence walk — window fill, EMA warm-up, and now the person model's
  // first commits — on top of the steady-state jitter. Stages 3 and 4 both
  // had to decompose this by hand from the rows to explain full-window
  // deltas; the tail is the number the jitter budget actually reads on, so
  // it is a pinned metric now rather than a console excavation.
  const tailRows = rows.slice(30);
  const tailOf = (pick) => stats(tailRows.map(pick));
  const tsx = tailOf((r) => r.screenX);
  const tsy = tailOf((r) => r.screenY);
  const tailRmsPx = Number.isFinite(tsx.sd) && Number.isFinite(tsy.sd)
    ? round(Math.hypot(tsx.sd, tsy.sd)) : null;
  let tailStepPx = null;
  for (let i = 1; i < tailRows.length; i++) {
    const a = tailRows[i - 1]; const b = tailRows[i];
    if (![a.screenX, a.screenY, b.screenX, b.screenY].every(Number.isFinite)) continue;
    const step = Math.hypot(b.screenX - a.screenX, b.screenY - a.screenY);
    if (tailStepPx === null || step > tailStepPx) tailStepPx = step;
  }

  // `null * 10` is 0 in JavaScript, so the cm→mm scaling must refuse a missing
  // stat rather than multiply it: a metric that could not be measured stays
  // null in the JSON (assert mode then reports the sides as non-numeric and
  // passes only when BOTH are missing), and can never bake a fake 0.0 mm into
  // a reviewed baseline — which is a silent lie with a green suite.
  const mm10 = (x) => (Number.isFinite(x) ? round(x * 10) : null);
  const mm = (s) => ({ mean: mm10(s.mean), sd: mm10(s.sd) });
  const seatRaw = of((r) => r.seatPush);
  const seatEased = of((r) => r.seatEased);

  return {
    name,
    size: `${source.width}x${source.height}`,
    detections: rows.length,
    misses,
    screen: { sdXpx: sx.sd, sdYpx: sy.sd, rmsPx, maxStepPx: round(maxStepPx) },
    screenTail: { rmsPx: tailRmsPx, maxStepPx: round(tailStepPx) },
    bridgeMm: {
      x: mm(of((r) => r.bridgeX)),
      y: mm(of((r) => r.bridgeY)),
      z: mm(of((r) => r.bridgeZ)),
    },
    seatMm: {
      rawMean: mm10(seatRaw.mean),
      rawSd: mm10(seatRaw.sd),
      easedSd: mm10(seatEased.sd),
    },
    measuringFraction: round(rows.reduce((a, r) => a + r.measuring, 0) / Math.max(rows.length, 1)),
    noseWidthRatio: (({ mean, sd }) => ({ mean, sd }))(of((r) => r.noseWidthRatio)),
    depthFit: {
      r2Mean: of((r) => r.r2).mean,
      weightMean: of((r) => r.fitWeight).mean,
      weightSd: of((r) => r.fitWeight).sd,
    },
    surfaceRebuilds: occluder.userData.rebuilds?.surface ?? null,
    /** Rebuilds inside the settled tail — the C6 sustained-rate number. */
    surfaceRebuildsLate: rebuildsAt30 !== null && occluder.userData.rebuilds
      ? occluder.userData.rebuilds.surface - rebuildsAt30 : null,
    visHeld: (({ mean, sd }) => ({ mean, sd }))(of((r) => r.visHeld)),
    /** Reported, never asserted — see the header. */
    updateFrameMs: round(wallMs / Math.max(rows.length, 1)),
  };
}

// ---------------------------------------------- G14(b) crossfade acceptance
//
// The real-world acceptance gate for the zConf depth crossfade (spec grafts G8
// and G14b): before the crossfade may default on, CONVERGED depth must be
// stable across the diag f00–f09 stills — the same face at ten different
// poses. The per-still fixture above cannot ask this (fresh state per still
// means no parallax ever accumulates inside one pose), so this pass runs the
// ten stills as ONE shared session: one state, one person model, carried
// across all ten poses in order — which is exactly the browsing the design's
// zConf arithmetic banks on. Two passes, crossfade forced on and off, so the
// span the crossfade adds or removes is attributable. Report-only against the
// baseline (the gate's verdict is recorded in the spec's stage-4 landing
// note); the summary lands in the JSON so later stages keep watching it.

const SESSION_SETTLE = 10;
const SESSION_MEASURED = 30;

/**
 * The rigid twin of the production placement (anchoring-v3 R0, plan §A.3):
 * the identical `solvePlacement` call `updateFrame` just made, with the pin
 * set to the fused base (`__ar.pin.baseX/Y/Z`). At R0 this isolated the
 * innovation term and DECIDED its fate (up to 13.06 px of per-frame work at
 * −18° yaw per-still-cold, 0.03 px on the live gaze fixture — deleted).
 * Post-deletion the production pin IS the base, so the twin must land on the
 * production floats exactly: rigidMiss ≡ 0.00 px on every still is now the
 * collapse asserted on real detector output, and any nonzero reading means a
 * second bridge value has crept back into flight.
 */
function solveRigidTwin({ result, state, model, fit, face }) {
  const pin = state.pin;
  if (!pin || !Number.isFinite(pin.baseX)) return null;
  const anchors = {
    ...result.anchors,
    bridge: new THREE.Vector3(pin.baseX, pin.baseY, pin.baseZ),
  };
  const seatState = state.seatConfig;
  return solvePlacement({
    model, anchors, fit, face,
    surface: surfaceOf(state.occluder),
    seatConfig: seatState?.hasSolve && fit.seatOnNose !== false
      ? seatState.applied : null,
  });
}

/** Projects a face-space position to image pixels through the drawn head pose. */
function screenOf(position, scene, source, out) {
  poseScratch.copy(position).applyMatrix4(scene.head.matrixWorld).project(scene.camera);
  out.x = (poseScratch.x * 0.5 + 0.5) * source.width;
  out.y = (1 - (poseScratch.y * 0.5 + 0.5)) * source.height;
  return out;
}

async function sharedSessionPass({ face, scene, model, tracker, nextTs, crossfadeOn,
  collectRigid = false }) {
  const occluder = createOccluder(face);
  const state = { occluder, person: createPersonModel(face) };
  state.person.crossfadeOn = crossfadeOn;
  const smoother = new PoseSmoother(DEFAULT_SMOOTHING);
  const fit = { ...DEFAULT_FIT };
  const perStill = [];
  const rigidStills = [];
  const prodScreen = { x: 0, y: 0 };
  const rigidScreen = { x: 0, y: 0 };

  for (const url of STILLS) {
    const name = url.split('/').pop();
    if (!DIAG_SET.has(name)) continue;
    const source = await createSampleSource(new URL(url, import.meta.url).href);
    source.update(0);
    scene.resize(source.width, source.height, source.width / source.height);
    const pristine = document.createElement('canvas');
    pristine.width = source.width;
    pristine.height = source.height;
    pristine.getContext('2d').drawImage(source.element, 0, 0);

    let zSum = 0; let xSum = 0; let n = 0;
    let seatSum = 0; let seatN = 0;
    // rigidMissPx/Mm accumulators (R0): per-frame screen delta between the
    // production and rigid twins, and each mode's mean face-space position —
    // the cross-still span of those means is the wander each mode would carry.
    let missPxSum = 0; let missPxMax = null; let missMmSum = 0; let missN = 0;
    const prodPos = { x: 0, y: 0, z: 0 };
    const rigidPos = { x: 0, y: 0, z: 0 };
    for (let k = 0; k < SESSION_SETTLE + SESSION_MEASURED; k++) {
      repaintWithNoise(source.element, pristine);
      const detection = tracker.detect(source.element, nextTs());
      if (!detection) continue;
      const r = updateFrame({ scene, face, model, fit, smoother, state, source,
        detection, dt: 1 / 30, smoothing: true, adaptToFace: true, temples: null });
      if (k < SESSION_SETTLE) continue;
      zSum += r.anchors.bridge.z;
      xSum += r.anchors.bridge.x;
      n++;
      // The APPLIED seat, in the one fixture where the eased channels carry
      // across poses — the per-still replay is a cold session per pose by
      // construction, so this shared pass is where stage 5's cross-pose
      // claim (the seat is a face constant, not a per-pose guess) is
      // actually measurable.
      const applied = r.placement?.noseSeat?.easedPush;
      if (Number.isFinite(applied)) { seatSum += applied; seatN++; }

      if (collectRigid && r.placement) {
        const rigid = solveRigidTwin({ result: r, state, model, fit, face });
        if (rigid) {
          screenOf(r.placement.position, scene, source, prodScreen);
          screenOf(rigid.position, scene, source, rigidScreen);
          const dPx = Math.hypot(prodScreen.x - rigidScreen.x,
            prodScreen.y - rigidScreen.y);
          missPxSum += dPx;
          if (missPxMax === null || dPx > missPxMax) missPxMax = dPx;
          missMmSum += r.placement.position.distanceTo(rigid.position) * 10;
          prodPos.x += r.placement.position.x;
          prodPos.y += r.placement.position.y;
          prodPos.z += r.placement.position.z;
          rigidPos.x += rigid.position.x;
          rigidPos.y += rigid.position.y;
          rigidPos.z += rigid.position.z;
          missN++;
        }
      }
    }
    perStill.push({
      name,
      bridgeZMm: n ? round((zSum / n) * 10) : null,
      bridgeXMm: n ? round((xSum / n) * 10) : null,
      seatAppliedMm: seatN ? round((seatSum / seatN) * 10) : null,
      zConfBridge: round(state.person.zConfBridge),
      zWeightBridge: round(state.person.zWeight[6]),
    });
    if (collectRigid && missN) {
      rigidStills.push({
        name,
        missMeanPx: round(missPxSum / missN),
        missMaxPx: round(missPxMax),
        missMeanMm: round(missMmSum / missN),
        prodMeanMm: {
          x: round((prodPos.x / missN) * 10),
          y: round((prodPos.y / missN) * 10),
          z: round((prodPos.z / missN) * 10),
        },
        rigidMeanMm: {
          x: round((rigidPos.x / missN) * 10),
          y: round((rigidPos.y / missN) * 10),
          z: round((rigidPos.z / missN) * 10),
        },
      });
    }
  }

  const span = (pick) => {
    const v = perStill.map(pick).filter(Number.isFinite);
    return v.length ? round(Math.max(...v) - Math.min(...v)) : null;
  };

  // The R0 gate arithmetic (report-only until A1): per-still rigid miss ≤ 3 px
  // — ≈1.7 mm at 45 cm, the plan's own detection-threshold budget — and the
  // rigid mode's cross-still wander must not exceed the production (landmark)
  // mode's, because rigid must beat the wander it retires, not merely tie it.
  let rigidMiss = null;
  if (collectRigid && rigidStills.length) {
    const spanOf = (pick) => {
      const v = rigidStills.map(pick).filter(Number.isFinite);
      return v.length ? round(Math.max(...v) - Math.min(...v)) : null;
    };
    const prodSpan = {
      x: spanOf((s) => s.prodMeanMm.x),
      y: spanOf((s) => s.prodMeanMm.y),
      z: spanOf((s) => s.prodMeanMm.z),
    };
    prodSpan.norm = round(Math.hypot(prodSpan.x ?? 0, prodSpan.y ?? 0, prodSpan.z ?? 0));
    const rigidSpan = {
      x: spanOf((s) => s.rigidMeanMm.x),
      y: spanOf((s) => s.rigidMeanMm.y),
      z: spanOf((s) => s.rigidMeanMm.z),
    };
    rigidSpan.norm = round(Math.hypot(rigidSpan.x ?? 0, rigidSpan.y ?? 0, rigidSpan.z ?? 0));
    const worst = rigidStills.reduce(
      (a, s) => (a === null || s.missMeanPx > a.missMeanPx ? s : a), null,
    );
    rigidMiss = {
      perStill: rigidStills,
      worstStill: worst.name,
      worstMissMeanPx: worst.missMeanPx,
      productionSpanMm: prodSpan,
      rigidSpanMm: rigidSpan,
      gatePerStill3px: rigidStills.every((s) => s.missMeanPx <= 3),
      gateSpan: rigidSpan.norm <= prodSpan.norm,
    };
  }

  return {
    crossfadeOn,
    perStill,
    bridgeZSpanMm: span((s) => s.bridgeZMm),
    bridgeXSpanMm: span((s) => s.bridgeXMm),
    /** Stage 5's cross-pose headline: max−min of the applied seat across the
     * ten poses of ONE session. */
    seatAppliedSpanMm: span((s) => s.seatAppliedMm),
    meanW: round(state.person.meanW),
    commits: state.person.commits,
    resets: state.person.resets,
    tripwireActive: state.person.tripwireActive,
    rigidMiss,
    /** The converged session's guts, so a follow-on pass (gazeInjection) can
     * continue exactly where this one stood instead of paying a fresh
     * convergence. Not serialised — `run()` strips it before the JSON. */
    session: { state, smoother, fit },
  };
}

// ------------------------------------------------------ R0: gaze injection
//
// The stills are frozen pixels — nobody's gaze moves in them — so the live
// gaze-coupling (stage 6, 2026-08-17: bridge landmark follows the eyes,
// 2.3 mm mean / 4 mm peaks on a held-still head, ~9 px of drawn frame) can
// only be exercised synthetically. This pass displaces the nose-region
// NORMALIZED landmarks pre-unproject — the exact space `carryLandmarks` and
// `measureAnchors` consume, per the v3 verdict's amendment 5 — with the pose
// matrix untouched, which is precisely what the live measurement showed
// (matrix sub-degree while the landmark swung). Eye corners and irises stay
// at 0.00 displacement, so the gaze ADMISSION door (iris-vs-corner, the
// stage-6 gate's calibration re-consumed by anchoring-v3) correctly does NOT
// refuse: the pass measures the estimator/surface path that remains once the
// pin innovation is deleted — the share the R0 verdict attributed to the
// seat stack riding the morph.
//
// The field: a 0.5 Hz circle whose radius breathes at 0.1 Hz between 0.6 and
// 4.0 mm, mean 2.3 mm over the 10 s window — the wearer's own measured
// numbers. mm are converted to normalized units at the face's own depth
// through the projection (one image width at the face = 2·|z|·tan(fov/2)·
// aspect), so "2.3 mm" means 2.3 mm on the face whatever the still's
// resolution.
//
// VALIDITY GATE (report-only): the production pin must reproduce roughly the
// live ~9+ px coupling — if it does not, the injector is unrealistic and says
// so, rather than flattering the rigid arm with a weak stimulus.

/** The displaced set: bridge 6, tip 4, nasion 168, the four nose walls. */
const GAZE_INJECT_LMS = [
  LM.NOSE_BRIDGE, LM.NOSE_TIP, LM.NASION,
  LM.NOSE_WALL_HIGH_R, LM.NOSE_WALL_HIGH_L, LM.NOSE_WALL_LOW_R, LM.NOSE_WALL_LOW_L,
];
const GAZE_INJECT_STILL = '../assets/samples/diag/f00.png';
const GAZE_INJECT_SECONDS = 10;
const GAZE_MEAN_MM = 2.3;
const GAZE_PEAK_MM = 4.0;
const GAZE_HZ = 0.5;
/** The radius modulation: one full cycle over the 10 s window, so the mean is
 * exactly GAZE_MEAN_MM and the peak exactly GAZE_PEAK_MM. */
const GAZE_BREATHE_HZ = 0.1;

async function gazeInjectionPass({ face, scene, model, tracker, nextTs, session }) {
  const { state, smoother, fit } = session;
  const source = await createSampleSource(new URL(GAZE_INJECT_STILL, import.meta.url).href);
  source.update(0);
  scene.resize(source.width, source.height, source.width / source.height);
  const pristine = document.createElement('canvas');
  pristine.width = source.width;
  pristine.height = source.height;
  pristine.getContext('2d').drawImage(source.element, 0, 0);

  // 2 s of undisplaced settle on the injection still first — the session
  // arrives from f09, and the still-switch transient (median hop, seat
  // re-ease) must not be billed to either track's RMS — then the 10 s
  // measured field.
  const settle = 2 * 30;
  const frames = settle + GAZE_INJECT_SECONDS * 30;
  const prodX = []; const prodY = [];
  const rigX = []; const rigY = [];
  const screen = { x: 0, y: 0 };
  let gazeRefusals = 0;
  let pxPerMm = null;

  for (let k = 0; k < frames; k++) {
    repaintWithNoise(source.element, pristine);
    const detection = tracker.detect(source.element, nextTs());
    if (!detection) continue;

    // The face's camera depth off the matrix (column-major e14 is z), which
    // prices a millimetre at this still's scale. Vertical fov → one image
    // height at the face; aspect → one image width.
    const zCm = Math.abs(detection.matrix[14]);
    const tanHalf = Math.tan((scene.camera.fov * Math.PI) / 360);
    const heightAtFaceCm = 2 * zCm * tanHalf;
    const widthAtFaceCm = heightAtFaceCm * (source.width / source.height);
    if (pxPerMm === null) pxPerMm = round(source.height / heightAtFaceCm / 10);

    let lms = detection.landmarks;
    const measuring = k >= settle;
    if (measuring) {
      const t = (k - settle) / 30;
      const rMm = GAZE_MEAN_MM
        + (GAZE_PEAK_MM - GAZE_MEAN_MM) * Math.sin(2 * Math.PI * GAZE_BREATHE_HZ * t);
      const theta = 2 * Math.PI * GAZE_HZ * t;
      const dxN = ((rMm / 10) * Math.cos(theta)) / widthAtFaceCm;
      const dyN = ((rMm / 10) * Math.sin(theta)) / heightAtFaceCm;
      lms = detection.landmarks.slice();
      for (const i of GAZE_INJECT_LMS) {
        const p = lms[i];
        lms[i] = { x: p.x + dxN, y: p.y + dyN, z: p.z };
      }
    }

    const r = updateFrame({ scene, face, model, fit, smoother, state, source,
      detection: { landmarks: lms, matrix: detection.matrix },
      dt: 1 / 30, smoothing: true, adaptToFace: true, temples: null });
    if (!measuring) continue;
    gazeRefusals = state.gaze?.refusals ?? 0;
    if (!r.placement) continue;

    screenOf(r.placement.position, scene, source, screen);
    prodX.push(screen.x); prodY.push(screen.y);
    const rigid = solveRigidTwin({ result: r, state, model, fit, face });
    if (rigid) {
      screenOf(rigid.position, scene, source, screen);
      rigX.push(screen.x); rigY.push(screen.y);
    }
  }

  const track = (xs, ys) => {
    if (!xs.length) return { rmsPx: null, peakPx: null, n: 0 };
    const mx = xs.reduce((a, b) => a + b, 0) / xs.length;
    const my = ys.reduce((a, b) => a + b, 0) / ys.length;
    let sq = 0; let peak = 0;
    for (let i = 0; i < xs.length; i++) {
      const d2 = (xs[i] - mx) ** 2 + (ys[i] - my) ** 2;
      sq += d2;
      if (d2 > peak) peak = d2;
    }
    return {
      rmsPx: round(Math.sqrt(sq / xs.length)),
      peakPx: round(Math.sqrt(peak)),
      n: xs.length,
    };
  };

  const production = track(prodX, prodY);
  const rigid = track(rigX, rigY);
  return {
    still: GAZE_INJECT_STILL.split('/').pop(),
    seconds: GAZE_INJECT_SECONDS,
    injected: { meanMm: GAZE_MEAN_MM, peakMm: GAZE_PEAK_MM, hz: GAZE_HZ },
    pxPerMm,
    production,
    rigid,
    /** The gaze door must NOT refuse — the injector leaves irises and
     * corners untouched, so a nonzero count here means the injector leaked. */
    gazeRefusals,
    /** Validity: the production control reproduces the live ~9 px coupling
     * (peak ≥ ~7 px is accepted as "roughly" — the pin filter's 0.8 Hz pole
     * passes ~85% of a 0.5 Hz field). */
    validInjector: Number.isFinite(production.peakPx) && production.peakPx >= 7,
  };
}

// ------------------------------------------------------------------ aggregates

function aggregate(stills) {
  const diag = stills.filter((s) => DIAG_SET.has(s.name) && !s.error);
  const span = (pick) => {
    const means = diag.map(pick).filter(Number.isFinite);
    return means.length ? round(Math.max(...means) - Math.min(...means)) : null;
  };
  const msAll = stills.filter((s) => !s.error).map((s) => s.updateFrameMs)
    .filter(Number.isFinite);
  return {
    /** Max−min of per-still bridge means over f00–f09 — one rigid face, so any
     * span at all is pose-systematic anchor error (diagnosed ~6.8 mm in x). */
    bridgeWanderSpanMm: {
      x: span((s) => s.bridgeMm.x.mean),
      y: span((s) => s.bridgeMm.y.mean),
      z: span((s) => s.bridgeMm.z.mean),
    },
    /** Max−min of per-still raw seat-push means over f00–f09 — the diagnosed
     * bimodal measured-vs-canonical regime split (~2.4 mm). */
    seatPushSpreadMm: span((s) => s.seatMm.rawMean),
    updateFrameMs: msAll.length
      ? round(msAll.reduce((a, b) => a + b, 0) / msAll.length) : null,
  };
}

// ----------------------------------------------------------------- assertions

/**
 * Tolerance per metric class — the header's table, verbatim. `count` has no
 * absolute floor on purpose: a deterministic replay of the same seed cannot
 * legitimately move a zero (misses, say) at all.
 */
const TOL = {
  px: (b) => Math.max(Math.abs(b) * 0.25, 0.05),
  mm: (b) => Math.max(Math.abs(b) * 0.20, 0.2),
  count: (b) => Math.abs(b) * 0.20,
  frac: () => 0.1,
};

/** Every asserted per-still metric, as [path into the summary, metric class]. */
const STILL_METRICS = [
  ['detections', 'count'],
  ['misses', 'count'],
  ['screen.sdXpx', 'px'],
  ['screen.sdYpx', 'px'],
  ['screen.rmsPx', 'px'],
  ['screen.maxStepPx', 'px'],
  ['screenTail.rmsPx', 'px'],
  ['screenTail.maxStepPx', 'px'],
  ['bridgeMm.x.mean', 'mm'],
  ['bridgeMm.x.sd', 'mm'],
  ['bridgeMm.y.mean', 'mm'],
  ['bridgeMm.y.sd', 'mm'],
  ['bridgeMm.z.mean', 'mm'],
  ['bridgeMm.z.sd', 'mm'],
  ['seatMm.rawMean', 'mm'],
  ['seatMm.rawSd', 'mm'],
  ['seatMm.easedSd', 'mm'],
  ['measuringFraction', 'frac'],
  ['noseWidthRatio.mean', 'frac'],
  ['noseWidthRatio.sd', 'frac'],
  ['depthFit.r2Mean', 'frac'],
  ['depthFit.weightMean', 'frac'],
  ['depthFit.weightSd', 'frac'],
  ['surfaceRebuilds', 'count'],
  ['surfaceRebuildsLate', 'count'],
  ['visHeld.mean', 'count'],
  ['visHeld.sd', 'count'],
];

const AGGREGATE_METRICS = [
  ['bridgeWanderSpanMm.x', 'mm'],
  ['bridgeWanderSpanMm.y', 'mm'],
  ['bridgeWanderSpanMm.z', 'mm'],
  ['seatPushSpreadMm', 'mm'],
];

const dig = (obj, path) => path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj);

function compare(label, current, baseline, kind) {
  // A metric that exists on one side only is a shape change, and a shape change
  // is a failure — the fixture's contract is that the numbers stay comparable.
  // Missing on BOTH sides is the one honest exception: an unmeasurable metric
  // that stayed unmeasurable is skipped, with a note, not counted as a delta.
  if (!Number.isFinite(current) || !Number.isFinite(baseline)) {
    record(label, current === baseline,
      current === baseline
        ? `missing on both sides (${current}) — skipped, nothing to compare`
        : `current ${current} vs baseline ${baseline} — non-numeric on one side`);
    return;
  }
  const tol = TOL[kind](baseline);
  record(label, Math.abs(current - baseline) <= tol,
    `${current} vs baseline ${baseline} (±${round(tol)} ${kind})`);
}

function assertAgainst(current, baseline) {
  // The baseline is only meaningful for the exact configuration it was pinned
  // under — a different seed or model is a different experiment, not a delta.
  for (const key of ['seed', 'noiseAlpha', 'warmup', 'measured', 'model', 'delegate']) {
    record(`config ${key} matches baseline`, current[key] === baseline[key],
      `current ${current[key]} vs baseline ${baseline[key]}`);
  }

  const baseByName = new Map((baseline.stills ?? []).map((s) => [s.name, s]));
  for (const still of current.stills) {
    const base = baseByName.get(still.name);
    if (still.error || !base || base.error) {
      record(`${still.name} replayed`, !still.error && !!base && !base.error,
        still.error ?? (base ? base.error ?? 'ok' : 'missing from baseline'));
      continue;
    }
    for (const [path, kind] of STILL_METRICS) {
      compare(`${still.name} ${path}`, dig(still, path), dig(base, path), kind);
    }
  }

  for (const [path, kind] of AGGREGATE_METRICS) {
    compare(`aggregate ${path}`, dig(current.aggregates, path),
      dig(baseline.aggregates, path), kind);
  }

  // Wall time: judged since stage 3, against the STAGE-0 constant rather than
  // the rolling baseline — see `STAGE0_WALL_MS`. The per-still numbers stay
  // reported-only; the aggregate mean is the budget the spec states.
  const budget = STAGE0_WALL_MS * WALL_BUDGET_FACTOR;
  record('updateFrame stays inside the stage-3 wall-time budget',
    Number.isFinite(current.aggregates.updateFrameMs)
    && current.aggregates.updateFrameMs <= budget,
    `${current.aggregates.updateFrameMs} ms mean over the replay against `
    + `${round(budget)} ms (1.2× the stage-0 pinned ${STAGE0_WALL_MS} ms; the rolling `
    + `baseline reads ${baseline.aggregates?.updateFrameMs} ms)`);

  const failed = results.filter((r) => !r.pass);
  const summary = `${results.length - failed.length}/${results.length} baseline checks passed`;
  summaryEl.textContent = summary;
  summaryEl.className = failed.length ? 'fail' : 'ok';
  document.title = failed.length ? `FAIL — ${summary}` : `OK — ${summary}`;
  window.__results = results;
}

// ------------------------------------------------------------------------ run

async function run() {
  // Fetched before anything heavy so a malformed baseline fails fast.
  let baseline = null;
  try {
    const response = await fetch('./diag-baseline.json', { cache: 'no-store' });
    if (response.ok) baseline = await response.json();
  } catch { /* absent or unreadable → baseline mode */ }

  say(baseline ? 'assert mode — baseline found, replaying against it…'
    : 'baseline mode — no diag-baseline.json, measuring a fresh baseline…');

  const face = await loadCanonicalFace();
  const canvas = document.getElementById('gl');
  const scene = createScene(canvas, { preserveDrawingBuffer: true });

  const entry = MODELS.find((m) => m.value === DEFAULT_MODEL) ?? MODELS[0];
  const root = await loadGlassesModel(entry, import.meta.url);
  const model = analyseModel(root);

  const tracker = await createTracker();

  let timestamp = 1;
  const current = {
    seed: REPLAY_SEED,
    noiseAlpha: NOISE_ALPHA,
    warmup: WARMUP,
    measured: MEASURED,
    model: entry.value,
    delegate: tracker.delegate,
    stills: [],
    aggregates: null,
  };
  window.__replay = { mode: baseline ? 'assert' : 'baseline', current, done: false };

  for (const url of STILLS) {
    const name = url.split('/').pop();
    say(`replaying ${name}…`);
    try {
      current.stills.push(await replayStill({ url, face, scene, model, tracker,
        nextTs: () => (timestamp += 33) }));
    } catch (error) {
      current.stills.push({ name, error: String(error?.stack ?? error) });
    }
  }
  current.aggregates = aggregate(current.stills);

  // The G14(b) shared-session acceptance runs AFTER the per-still fixture, so
  // the PRNG stream the pinned baseline was measured on is untouched. The R0
  // instruments ride the OFF pass (crossfade dark IS the production config)
  // and then continue its converged session into the gaze-injection still —
  // both consume the stream only after every pinned metric has been measured.
  say('crossfade acceptance — shared session, crossfade ON…');
  const onPass = await sharedSessionPass({ face, scene, model, tracker,
    nextTs: () => (timestamp += 33), crossfadeOn: true });
  say('crossfade acceptance OFF + rigid-miss twin solves…');
  const offPass = await sharedSessionPass({ face, scene, model, tracker,
    nextTs: () => (timestamp += 33), crossfadeOn: false, collectRigid: true });
  say('gaze injection — 10 s on the converged session…');
  current.gazeInjection = await gazeInjectionPass({ face, scene, model, tracker,
    nextTs: () => (timestamp += 33), session: offPass.session });
  delete onPass.session;
  delete offPass.session;
  current.rigidMiss = offPass.rigidMiss;
  delete offPass.rigidMiss;
  delete onPass.rigidMiss;
  current.crossfadeAcceptance = { on: onPass, off: offPass };
  if (baseline) {
    const on = current.crossfadeAcceptance.on;
    const off = current.crossfadeAcceptance.off;
    record('G14b shared-session crossfade acceptance (report)', true,
      `crossfade ON: bridge z span ${on.bridgeZSpanMm} mm across f00–f09 `
      + `(gate 1.5), x span ${on.bridgeXSpanMm} mm, zConfBridge ends at `
      + `${on.perStill[on.perStill.length - 1]?.zConfBridge}, ${on.commits} commits, `
      + `${on.resets} resets; OFF control: z span ${off.bridgeZSpanMm} mm, `
      + `x span ${off.bridgeXSpanMm} mm — reported for the spec's landing note, `
      + `asserted there`);

    // The R0 offline gates, REPORTED not enforced (v3 plan, "STAGED LANDING":
    // they gate proceeding to A1, and the verdict belongs in the landing
    // record, not in a suite that must stay green while the decision is made).
    const rm = current.rigidMiss;
    if (rm) {
      record('R0 rigidMiss (report — gates A1, not this suite)', true,
        `rigid-vs-production screen delta per still: worst ${rm.worstMissMeanPx} px `
        + `mean on ${rm.worstStill} (gate ≤ 3 px per still: `
        + `${rm.gatePerStill3px ? 'MET' : 'NOT MET'}); cross-still span rigid `
        + `${rm.rigidSpanMm.norm} mm vs production ${rm.productionSpanMm.norm} mm `
        + `(rigid must not exceed: ${rm.gateSpan ? 'MET' : 'NOT MET'})`);
    }
    const gz = current.gazeInjection;
    if (gz) {
      record('R0 gazeInjection (report — gates A1, not this suite)', true,
        `${gz.injected.meanMm} mm mean / ${gz.injected.peakMm} mm peak at `
        + `${gz.injected.hz} Hz on ${gz.still} (${gz.pxPerMm} px/mm): production `
        + `pin rms ${gz.production.rmsPx} px, peak ${gz.production.peakPx} px `
        + `(validity — reproduces the live ~9+ px coupling: `
        + `${gz.validInjector ? 'YES' : 'NO — injector unrealistic'}); forced-rigid `
        + `rms ${gz.rigid.rmsPx} px, peak ${gz.rigid.peakPx} px; gaze door `
        + `refused ${gz.gazeRefusals} samples (must be 0 — irises untouched)`);
    }
  }

  if (baseline) {
    window.__replay.baseline = baseline;
    assertAgainst(current, baseline);
    say('done — assert mode');
  } else {
    // The printed JSON IS the baseline file: save it verbatim as
    // ar/tests/diag-baseline.json and reload to enter assert mode.
    window.__replay.baseline = current;
    summaryEl.textContent = 'baseline mode — save the JSON below as ar/tests/diag-baseline.json';
    summaryEl.className = 'ok';
    outEl.textContent = JSON.stringify(current, null, 1);
    document.title = 'BASELINE — diag replay';
    say('done — baseline mode');
  }

  window.__replay.done = true;
  window.__done = true;
}

run().catch((error) => {
  say(`FAILED: ${error}`);
  record('harness', false, String(error?.stack ?? error));
  summaryEl.textContent = `harness crashed: ${error}`;
  summaryEl.className = 'fail';
  document.title = 'FAIL — diag replay crashed';
  if (window.__replay) window.__replay.done = true;
  window.__results = results;
  window.__done = true;
});
