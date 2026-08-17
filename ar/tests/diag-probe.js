/**
 * SCRATCH DIAGNOSTIC PROBE — 2026-08-16 redesign investigation, phase 1 empirics.
 *
 * Not part of the pipeline-check suite. This page measures, it does not assert:
 *
 *  - runs N repeated detections on IDENTICAL stills (advancing timestamps, the same
 *    `detectForVideo` path production uses) and reports the detector's temporal noise
 *    floor per quantity — landmark px, pose degrees — frontal vs the user's own
 *    complaint captures (assets/samples/diag/f00–f09: roll+pitch on a pillow);
 *  - drives the REAL production per-frame path (`updateFrame`, smoothing on, occluder
 *    deforming) on each still and reports fitLandmarkDepth r2/weight, visibility
 *    holds, depth clamps, raw vs eased seat push, and the on-screen pixel jitter of
 *    the placed frame — which on a still image is pure pipeline-generated jitter.
 *
 * Runs without requestAnimationFrame so it works in a hidden tab, exactly like
 * pipeline-check. Results land in `window.__probe` and are printed into #out.
 *
 * Kept deliberately (phase 3 will formalise it into the harness).
 */

import * as THREE from 'three';

import { loadCanonicalFace, LM } from '../src/canonical-face.js';
import { MODELS, DEFAULT_MODEL, loadGlassesModel } from '../src/models.js';
import { createTracker, estimateYaw } from '../src/tracker.js';
import { createScene } from '../src/scene.js';
import { createOccluder } from '../src/occluder.js';
import { analyseModel, DEFAULT_FIT } from '../src/fit.js';
import { updateFrame } from '../src/frame.js';
import { PoseSmoother, DEFAULT_SMOOTHING } from '../src/smoothing.js';
import { createSampleSource } from '../src/sources.js';

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

/** 3 warmup detections (VIDEO mode carries ROI across stills), 60 measured. */
const WARMUP = 3;
const MEASURED = 60;

/**
 * Per-iteration sensor-noise blend. Measured first WITHOUT this: `detectForVideo`
 * is bit-for-bit deterministic on identical pixels (every std-dev came back 0.000),
 * so the temporal jitter a wearer sees is the detector's response to the camera's
 * own per-frame pixel noise. This reintroduces that input: a white-noise tile
 * alpha-blended over the still at a fresh random offset each iteration.
 * alpha 0.03 over uniform noise ≈ σ 2.2 digital numbers — an ordinary indoor webcam.
 */
const NOISE_ALPHA = 0.03;
const noiseTile = (() => {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 512;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(512, 512);
  for (let i = 0; i < img.data.length; i += 4) {
    img.data[i] = Math.random() * 256;
    img.data[i + 1] = Math.random() * 256;
    img.data[i + 2] = Math.random() * 256;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
})();

/** Repaints the still from its pristine copy, then blends one frame of noise. */
function repaintWithNoise(element, pristine) {
  const ctx = element.getContext('2d');
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.drawImage(pristine, 0, 0);
  ctx.globalAlpha = NOISE_ALPHA;
  const ox = -Math.floor(Math.random() * 512);
  const oy = -Math.floor(Math.random() * 512);
  for (let y = oy; y < element.height; y += 512) {
    for (let x = ox; x < element.width; x += 512) ctx.drawImage(noiseTile, x, y);
  }
  ctx.restore();
}

const TRACKED = {
  noseTip: LM.NOSE_TIP,
  noseBridge: LM.NOSE_BRIDGE,
  noseWallHighR: LM.NOSE_WALL_HIGH_R,
  noseWallHighL: LM.NOSE_WALL_HIGH_L,
  noseWallLowR: LM.NOSE_WALL_LOW_R,
  noseWallLowL: LM.NOSE_WALL_LOW_L,
  eyeInnerR: LM.EYE_INNER_R,
  eyeInnerL: LM.EYE_INNER_L,
};

const statusEl = document.getElementById('status');
const outEl = document.getElementById('out');
const say = (text) => { statusEl.textContent = text; };

const stats = (values) => {
  const v = values.filter(Number.isFinite);
  if (!v.length) return null;
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length);
  return {
    n: v.length,
    mean: round(mean),
    sd: round(sd),
    min: round(Math.min(...v)),
    max: round(Math.max(...v)),
  };
};
const round = (x) => (Number.isFinite(x) ? Math.round(x * 10000) / 10000 : x);

const poseMatrix = new THREE.Matrix4();
const posV = new THREE.Vector3();
const quatQ = new THREE.Quaternion();
const scaleV = new THREE.Vector3();
const euler = new THREE.Euler();
const worldV = new THREE.Vector3();

async function run() {
  say('loading canonical face…');
  const face = await loadCanonicalFace();

  const canvas = document.getElementById('gl');
  const scene = createScene(canvas, { preserveDrawingBuffer: true });

  say('loading glasses model…');
  const entry = MODELS.find((m) => m.value === DEFAULT_MODEL) ?? MODELS[0];
  const root = await loadGlassesModel(entry, import.meta.url);
  const model = analyseModel(root);

  say('starting tracker…');
  const tracker = await createTracker();

  let timestamp = 1;
  const results = { delegate: tracker.delegate, model: entry.value,
    noiseAlpha: NOISE_ALPHA, stills: [] };
  window.__probe = results;

  for (const url of STILLS) {
    const name = url.split('/').pop();
    say(`probing ${name}…`);
    try {
      const summary = await probeStill({ url, name, face, scene, model, tracker,
        nextTs: () => (timestamp += 33) });
      results.stills.push(summary);
    } catch (error) {
      results.stills.push({ name, error: String(error?.stack ?? error) });
    }
    render(results);
  }

  results.done = true;
  say('done');
  render(results);
}

async function probeStill({ url, name, face, scene, model, tracker, nextTs }) {
  const source = await createSampleSource(new URL(url, import.meta.url).href);
  source.update(0);
  scene.resize(source.width, source.height, source.width / source.height);

  // Pristine copy, so each iteration's noise is fresh rather than accumulating.
  const pristine = document.createElement('canvas');
  pristine.width = source.width;
  pristine.height = source.height;
  pristine.getContext('2d').drawImage(source.element, 0, 0);

  const occluder = createOccluder(face);
  const state = { occluder };
  const smoother = new PoseSmoother(DEFAULT_SMOOTHING);
  const fit = { ...DEFAULT_FIT };

  const rows = [];
  let misses = 0;

  for (let k = 0; k < WARMUP + MEASURED; k++) {
    repaintWithNoise(source.element, pristine);
    const detection = tracker.detect(source.element, nextTs());
    if (!detection) { if (k >= WARMUP) misses++; continue; }
    if (k < WARMUP) {
      // Warm the production state too, so measured rows describe steady behaviour.
      updateFrame({ scene, face, model, fit, smoother, state, source, detection,
        dt: 1 / 30, smoothing: true, adaptToFace: true, temples: null });
      continue;
    }

    poseMatrix.fromArray(detection.matrix);
    poseMatrix.decompose(posV, quatQ, scaleV);
    euler.setFromQuaternion(quatQ, 'YXZ');

    const row = {
      px: posV.x, py: posV.y, pz: posV.z,
      yawDeg: THREE.MathUtils.radToDeg(euler.y),
      pitchDeg: THREE.MathUtils.radToDeg(euler.x),
      rollDeg: THREE.MathUtils.radToDeg(euler.z),
      scale: (scaleV.x + scaleV.y + scaleV.z) / 3,
      imgYaw: estimateYaw(detection.landmarks),
      lm: {},
    };
    for (const [key, index] of Object.entries(TRACKED)) {
      const p = detection.landmarks[index];
      row.lm[key] = [p.x, p.y, p.z];
    }

    const result = updateFrame({ scene, face, model, fit, smoother, state, source,
      detection, dt: 1 / 30, smoothing: true, adaptToFace: true, temples: null });

    const data = occluder.userData;
    row.r2 = data.depthFit?.r2 ?? null;
    row.fitWeight = data.depthFit?.weight ?? null;
    row.depthClamped = data.depthClamped ?? null;
    row.measuring = state.measuringLatch === true ? 1 : 0;

    // Visibility holds: vertices with meaningful cover along their own view ray.
    let visHeld = 0;
    const behind = data.visibility?.behind;
    if (behind) for (let i = 0; i < behind.length; i++) if (behind[i] > 0.35) visHeld++;
    row.visHeld = visHeld;

    const seat = result.placement?.noseSeat;
    row.seatPush = seat?.push ?? null;
    row.seatEased = seat?.easedPush ?? null;
    row.seatTouched = seat?.touched ?? null;
    row.seatInterference = seat?.interference ?? null;

    if (result.placement) {
      row.plX = result.placement.position.x;
      row.plY = result.placement.position.y;
      row.plZ = result.placement.position.z;
      // The number a viewer sees: the placed frame's origin, projected to pixels
      // through the SMOOTHED head pose the composite draws. On a still image every
      // pixel of movement here is pipeline-generated jitter.
      worldV.copy(result.placement.position).applyMatrix4(scene.head.matrixWorld)
        .project(scene.camera);
      row.screenX = (worldV.x * 0.5 + 0.5) * source.width;
      row.screenY = (1 - (worldV.y * 0.5 + 0.5)) * source.height;
    }
    row.bridgeX = result.anchors.bridge.x;
    row.bridgeY = result.anchors.bridge.y;
    row.bridgeZ = result.anchors.bridge.z;
    row.noseWidthRatio = result.anchors.noseWidthRatio ?? null;

    rows.push(row);
  }

  const of = (pick) => stats(rows.map(pick));
  const meanDist = Math.abs(of((r) => r.pz)?.mean ?? 45);
  const fovRad = (scene.camera.fov * Math.PI) / 180;
  const aspect = source.width / source.height;
  /** Screen px per face-space cm at this head's distance. */
  const pxPerCm = (source.height / 2) / (meanDist * Math.tan(fovRad / 2));
  /** cm of camera depth per unit of MediaPipe landmark z (weak perspective slope). */
  const cmPerZ = 2 * meanDist * Math.tan(fovRad / 2) * aspect;

  const lmSummary = {};
  for (const key of Object.keys(TRACKED)) {
    const sx = stats(rows.map((r) => r.lm[key][0] * source.width));
    const sy = stats(rows.map((r) => r.lm[key][1] * source.height));
    const sz = stats(rows.map((r) => r.lm[key][2]));
    lmSummary[key] = {
      sdXpx: sx?.sd, sdYpx: sy?.sd,
      sdZcm: sz ? round(sz.sd * cmPerZ) : null,
      meanZ: sz?.mean,
    };
  }

  return {
    name,
    size: `${source.width}x${source.height}`,
    detections: rows.length,
    misses,
    surfaceRebuilds: occluder.userData.rebuilds?.surface ?? null,
    distanceCm: round(meanDist),
    pxPerCm: round(pxPerCm),
    pose: {
      yawDeg: of((r) => r.yawDeg),
      pitchDeg: of((r) => r.pitchDeg),
      rollDeg: of((r) => r.rollDeg),
      posXcm: of((r) => r.px),
      posYcm: of((r) => r.py),
      posZcm: of((r) => r.pz),
      imgYaw: of((r) => r.imgYaw),
    },
    gates: {
      measuringFraction: round(rows.reduce((a, r) => a + r.measuring, 0) / Math.max(rows.length, 1)),
    },
    landmarks: lmSummary,
    depthFit: {
      r2: of((r) => r.r2),
      weight: of((r) => r.fitWeight),
      depthClamped: of((r) => r.depthClamped),
    },
    visibility: { held: of((r) => r.visHeld) },
    seat: {
      rawPushMm: scaled(of((r) => r.seatPush), 10),
      easedPushMm: scaled(of((r) => r.seatEased), 10),
      touched: of((r) => r.seatTouched),
      interferenceMm: scaled(of((r) => r.seatInterference), 10),
    },
    placement: {
      xMm: scaled(of((r) => r.plX), 10),
      yMm: scaled(of((r) => r.plY), 10),
      zMm: scaled(of((r) => r.plZ), 10),
      screenXpx: of((r) => r.screenX),
      screenYpx: of((r) => r.screenY),
    },
    bridge: {
      xMm: scaled(of((r) => r.bridgeX), 10),
      yMm: scaled(of((r) => r.bridgeY), 10),
      zMm: scaled(of((r) => r.bridgeZ), 10),
    },
    noseWidthRatio: of((r) => r.noseWidthRatio),
  };
}

const scaled = (s, factor) => (s ? {
  n: s.n, mean: round(s.mean * factor), sd: round(s.sd * factor),
  min: round(s.min * factor), max: round(s.max * factor),
} : null);

function render(results) {
  outEl.textContent = JSON.stringify(results, null, 1);
}

run().catch((error) => {
  say(`FAILED: ${error}`);
  outEl.textContent = String(error?.stack ?? error);
});
