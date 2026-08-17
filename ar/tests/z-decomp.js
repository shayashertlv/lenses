/**
 * Z-DECOMPOSITION — scratch diagnostician for the ">40° side tilt pushes the
 * glasses forward" complaint (2026-08-17). NOT a suite; asserts nothing but
 * its own bookkeeping. Read-only over src/: it drives the real `updateFrame`
 * exactly as telemetry-replay does and logs, per frame, every term that can
 * move the drawn frame toward the camera:
 *
 *   placement.position.z  =  bridgeZ (fused pin base)
 *                          + contactZ (−(R·(s·noseContact)).z — rigid)
 *                          + pupilZ  (verticalCorrection along bridgeUp)
 *                          + restZ   (seat s channel along bridgeUp)
 *                          + seatZ   (eased ζ + raw guard, bounded)
 *                          + offsetZ (fit slider, 0 here)
 *
 * plus the carrier itself: raw pose z off the fixture matrix, the smoothed
 * pose z, the smoothed scale, and the drawn origin's camera distance
 * (placement.position through the smoothed head matrix — exactly the
 * projection telemetry-replay uses for its screen RMS).
 *
 * Modes:
 *  - ?fixture=<url>   — replay a recorded fixture, production path, one pass.
 *  - ?mode=roll       — synthetic canonical face, 30 fps, 6 s frontal then
 *                       plateaus at 10/20/30/35/40/45/50° of ROLL, through the
 *                       production path. Also proves the rollSearch
 *                       rotated-canvas round-trip (forward rotation + the
 *                       verbatim unrotation math from main.js) is the
 *                       identity, on landmarks, matrix, AND the placement it
 *                       produces.
 *  - ?mode=yaw        — the same protocol about YAW, for a like-for-like
 *                       synthetic comparison with the roll sweep.
 *
 * The per-frame record stays on window.__zdecomp; the page prints per-segment
 * angle-bucket tables in mm so the numbers can be read without pulling the
 * arrays.
 */

import * as THREE from 'three';

import { loadCanonicalFace } from '../src/canonical-face.js';
import { MODELS, DEFAULT_MODEL, loadGlassesModel } from '../src/models.js';
import { createScene } from '../src/scene.js';
import { createOccluder } from '../src/occluder.js';
import { analyseModel, DEFAULT_FIT } from '../src/fit.js';
import { updateFrame } from '../src/frame.js';
import { PoseSmoother, DEFAULT_SMOOTHING } from '../src/smoothing.js';

const statusEl = document.getElementById('status');
const outEl = document.getElementById('out');
const say = (t) => { statusEl.textContent = t; };
const print = (t, cls = '') => {
  const line = document.createElement('div');
  line.className = `line ${cls}`;
  line.textContent = t;
  outEl.append(line);
};

const round = (x, p = 100) => (Number.isFinite(x) ? Math.round(x * p) / p : null);

/** Pane-safe yield — the telemetry-replay MessageChannel trick, verbatim. */
const tick = () => new Promise((resolve) => {
  const ch = new MessageChannel();
  ch.port1.onmessage = () => { ch.port1.close(); resolve(); };
  ch.port2.postMessage(0);
});

// ------------------------------------------------------------- fixture load

async function loadFixtureText(param) {
  const response = await fetch(param, { cache: 'no-store' });
  if (!response.ok) throw new Error(`fixture fetch failed: HTTP ${response.status}`);
  if (param.endsWith('.gz')) {
    const stream = response.body.pipeThrough(new DecompressionStream('gzip'));
    return new Response(stream).text();
  }
  return response.text();
}

function parseFixture(text) {
  const lines = text.split('\n').filter((l) => l.trim().length);
  const header = JSON.parse(lines[0]);
  if (header.v !== 1) throw new Error(`unknown fixture version ${header.v}`);
  const records = new Array(lines.length - 1);
  for (let i = 1; i < lines.length; i++) records[i - 1] = JSON.parse(lines[i]);
  return { header, records };
}

// ------------------------------------------------- synthetic detection maker

/**
 * What MediaPipe would report for a given truth face at a given pose —
 * pipeline-check's `synthesiseLandmarks`, copied verbatim (importing the
 * suite would run the suite).
 */
function synthesiseLandmarks(face, truth, camera, headMatrixWorld) {
  const v = new THREE.Vector3();
  const cameraDepth = new Float32Array(face.vertexCount);
  let meanDepth = 0;
  for (let i = 0; i < face.vertexCount; i++) {
    v.set(truth[i * 3], truth[i * 3 + 1], truth[i * 3 + 2]).applyMatrix4(headMatrixWorld);
    cameraDepth[i] = v.z;
    meanDepth += v.z;
  }
  meanDepth /= face.vertexCount;

  const imageWidthAtFace = 2 * Math.abs(meanDepth)
    * Math.tan((camera.fov * Math.PI) / 360) * camera.aspect;

  const landmarks = [];
  for (let i = 0; i < face.vertexCount; i++) {
    v.set(truth[i * 3], truth[i * 3 + 1], truth[i * 3 + 2])
      .applyMatrix4(headMatrixWorld).project(camera);
    landmarks.push({
      x: (v.x + 1) / 2,
      y: (1 - v.y) / 2,
      z: -(cameraDepth[i] - meanDepth) / imageWidthAtFace,
    });
  }
  return landmarks;
}

/**
 * The rollSearch unrotation — `main.js`'s `unrotateDetection`, verbatim
 * (module cannot be imported without booting the app). Maps a detection made
 * on a canvas rotated by `rotation` back into true-image coordinates.
 */
function unrotateDetection(detection, rotation, w, h) {
  const c = Math.cos(-rotation);
  const s = Math.sin(-rotation);

  for (const p of detection.landmarks) {
    const x = p.x * w - w / 2;
    const y = p.y * h - h / 2;
    p.x = (c * x - s * y + w / 2) / w;
    p.y = (s * x + c * y + h / 2) / h;
  }

  const m = detection.matrix;
  const cz = Math.cos(rotation);
  const sz = Math.sin(rotation);
  for (let col = 0; col < 4; col++) {
    const x = m[col * 4];
    const y = m[col * 4 + 1];
    m[col * 4] = cz * x - sz * y;
    m[col * 4 + 1] = sz * x + cz * y;
  }
}

/** The exact forward of the above: what the detector on the ROTATED canvas
 * would report for a face whose true-image detection is `detection`. */
function rotateDetection(detection, rotation, w, h) {
  const c = Math.cos(rotation);
  const s = Math.sin(rotation);
  for (const p of detection.landmarks) {
    const x = p.x * w - w / 2;
    const y = p.y * h - h / 2;
    p.x = (c * x - s * y + w / 2) / w;
    p.y = (s * x + c * y + h / 2) / h;
  }
  const m = detection.matrix;
  const cz = Math.cos(-rotation);
  const sz = Math.sin(-rotation);
  for (let col = 0; col < 4; col++) {
    const x = m[col * 4];
    const y = m[col * 4 + 1];
    m[col * 4] = cz * x - sz * y;
    m[col * 4 + 1] = sz * x + cz * y;
  }
}

/**
 * A synthetic sweep about one axis: 6 s frontal (the estimators converge, the
 * seat solves, ζ settles), then per plateau [10,20,30,35,40,45,50]° one
 * second of ramp and two of hold. 30 fps. Position fixed at the fixtures' own
 * working distance.
 */
function synthStream(face, camera, axis) {
  const FPS = 30;
  const pos = new THREE.Vector3(1.5, -1.0, -45);
  const plateaus = [10, 20, 30, 35, 40, 45, 50];
  const schedule = [];
  let t = 0;
  const push = (deg) => { schedule.push({ t: t * (1000 / FPS), deg }); t++; };
  for (let i = 0; i < 6 * FPS; i++) push(0);
  let prev = 0;
  for (const target of plateaus) {
    for (let i = 1; i <= FPS; i++) push(prev + (target - prev) * (i / FPS));
    for (let i = 0; i < 2 * FPS; i++) push(target);
    prev = target;
  }

  const records = [];
  const q = new THREE.Quaternion();
  const mat = new THREE.Matrix4();
  const one = new THREE.Vector3(1, 1, 1);
  for (const step of schedule) {
    const rad = step.deg * (Math.PI / 180);
    const euler = axis === 'roll'
      ? new THREE.Euler(0, 0, rad, 'YXZ')
      : new THREE.Euler(0, rad, 0, 'YXZ');
    q.setFromEuler(euler);
    mat.compose(pos, q, one);
    const landmarks = synthesiseLandmarks(face, face.positions, camera, mat);
    const l = new Array(landmarks.length * 3);
    for (let k = 0; k < landmarks.length; k++) {
      l[k * 3] = landmarks[k].x;
      l[k * 3 + 1] = landmarks[k].y;
      l[k * 3 + 2] = landmarks[k].z;
    }
    records.push({ t: step.t, m: mat.toArray(), l, deg: step.deg });
  }
  const header = {
    v: 1,
    subject: `synthetic-${axis}`,
    date: 'synth',
    width: 1280,
    height: 720,
    segments: [
      { name: 'settle', t0: 0, t1: 6000 },
      { name: 'sweep', t0: 6000, t1: schedule[schedule.length - 1].t + 34 },
    ],
  };
  return { header, records };
}

// ------------------------------------------------------------------ one pass

const poseMatrix = new THREE.Matrix4();
const rawPos = new THREE.Vector3();
const rawQuat = new THREE.Quaternion();
const rawScl = new THREE.Vector3();
const euler = new THREE.Euler();
const worldV = new THREE.Vector3();
const contactV = new THREE.Vector3();

/**
 * One production replay with the per-frame z decomposition. `mutate` lets the
 * roll probe push each detection through rotate→unrotate before the pipeline
 * sees it.
 */
async function decompPass({ header, records, face, scene, model, label, mutate = null }) {
  const occluder = createOccluder(face);
  const state = { occluder };
  const smoother = new PoseSmoother(DEFAULT_SMOOTHING);
  const fit = { ...DEFAULT_FIT };
  const source = { width: header.width, height: header.height };
  scene.resize(header.width, header.height, header.width / header.height);

  const segOf = (t) => header.segments.findIndex((s) => t >= s.t0 && t < s.t1);

  const frames = [];
  let prevT = null;
  let sumCheckWorst = 0;
  let prevGuardOverflow = null;
  let prevRebuilds = 0;

  for (let i = 0; i < records.length; i++) {
    const rec = records[i];
    const dt = prevT === null ? 1 / 30
      : Math.min(Math.max((rec.t - prevT) / 1000, 1e-3), 0.5);
    prevT = rec.t;

    const l = rec.l;
    const landmarks = new Array(l.length / 3);
    for (let k = 0; k < landmarks.length; k++) {
      landmarks[k] = { x: l[k * 3], y: l[k * 3 + 1], z: l[k * 3 + 2] };
    }
    const detection = { landmarks, matrix: rec.m.slice() };
    if (mutate) mutate(detection);

    // Signed pose euler off the (possibly mutated) matrix — what the pipeline
    // will decompose too.
    poseMatrix.fromArray(detection.matrix);
    poseMatrix.decompose(rawPos, rawQuat, rawScl);
    euler.setFromQuaternion(rawQuat, 'YXZ');

    const r = updateFrame({
      scene, face, model, fit, smoother, state, source, detection, dt,
      smoothing: true, adaptToFace: true, temples: null, pinMode: 'production',
    });

    const seg = segOf(rec.t);
    if (!r.placement || seg < 0) continue;
    const p = r.placement;
    const seatCfg = state.seatConfig;
    const ns = p.noseSeat;

    const up = r.anchors.bridgeUp;
    const upDiv = Math.max(up.y, 0.2);
    const pupilZ = (p.verticalCorrection / upDiv) * up.z;
    const restZ = (p.restHeight / upDiv) * up.z;
    contactV.copy(model.noseContact).multiplyScalar(p.scale).applyQuaternion(p.quaternion);
    const contactZ = -contactV.z;
    const seatZ = ns ? ns.easedPush : 0;
    const bridgeZ = r.anchors.bridge.z;
    const sumZ = bridgeZ + contactZ + pupilZ + restZ + seatZ + fit.offsetZ;
    const sumErr = Math.abs(sumZ - p.position.z);
    if (sumErr > sumCheckWorst) sumCheckWorst = sumErr;

    worldV.copy(p.position).applyMatrix4(scene.head.matrixWorld);
    const headWorldZ = scene.head.matrixWorld.elements[14];

    const overflow = ns ? ns.guardOverflow : null;
    const overflowDelta = overflow !== null && prevGuardOverflow !== null
      ? Math.max(overflow - prevGuardOverflow, 0) : 0;
    if (overflow !== null) prevGuardOverflow = overflow;

    const rebuilds = state.occluder?.userData?.rebuilds?.surface ?? 0;
    const rebuildDelta = Math.max(rebuilds - prevRebuilds, 0);
    prevRebuilds = rebuilds;

    frames.push({
      t: rec.t,
      seg,
      yaw: euler.y * (180 / Math.PI),
      pitch: euler.x * (180 / Math.PI),
      roll: euler.z * (180 / Math.PI),
      w: state.poseTrust?.w ?? null,
      rawZ: rawPos.z,
      smZ: -r.distanceCm,
      scale: r.headScale,
      bridgeZ,
      carriedZ: state.anchors?.bridge?.z ?? null,
      contactZ,
      pupilZ,
      restZ,
      zeta: seatCfg?.applied?.zeta ?? 0,
      guard: ns ? ns.guard : 0,
      seatZ,
      need: ns && ns.touched > 0 ? ns.push : null,
      placeZ: p.position.z,
      worldZ: worldV.z,
      headWorldZ,
      relZ: worldV.z - headWorldZ,
      overflowDelta,
      rebuildDelta,
      depthW: state.depthFit?.weightApplied ?? null,
      upY: up.y,
      upZ: up.z,
      vCorr: p.verticalCorrection,
      sApplied: seatCfg?.applied?.s ?? 0,
    });

    if (i % 150 === 0) {
      say(`${label}: frame ${i}/${records.length}…`);
      await tick();
    }
  }

  return { label, frames, sumCheckWorst };
}

// -------------------------------------------------------------- aggregation

const MM = 10; // cm → mm

function mean(xs) { return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null; }
function maxOf(xs) { return xs.length ? Math.max(...xs) : null; }
function minOf(xs) { return xs.length ? Math.min(...xs) : null; }
function pct(xs, q) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))];
}

/**
 * Per-segment report: a frontal reference (the segment's own frames under 8°
 * on every axis, first quarter of the segment), then |angle| buckets with the
 * mean/extreme delta of every z term against that reference, in mm.
 */
function reportSegment(frames, header, segIndex, axis) {
  const segName = header.segments[segIndex].name;
  const rows = frames.filter((f) => f.seg === segIndex);
  if (!rows.length) { print(`  ${segName}: no frames`); return null; }

  const frontal = rows.filter((f, i) => i < rows.length * 0.25
    && Math.abs(f.yaw) < 8 && Math.abs(f.pitch) < 8 && Math.abs(f.roll) < 8);
  const refRows = frontal.length >= 5 ? frontal : rows.slice(0, 10);
  const ref = {};
  for (const key of ['rawZ', 'smZ', 'scale', 'bridgeZ', 'carriedZ', 'contactZ', 'pupilZ',
    'restZ', 'zeta', 'seatZ', 'placeZ', 'relZ', 'need', 'worldZ']) {
    ref[key] = mean(refRows.map((f) => f[key]).filter(Number.isFinite));
  }

  const angleOf = (f) => Math.abs(axis === 'pitch' ? f.pitch : (axis === 'roll' ? f.roll : f.yaw));
  const buckets = [[0, 10], [10, 20], [20, 30], [30, 35], [35, 40], [40, 90]];

  print(`  ${segName} — ref (n=${refRows.length}): placeZ ${round(ref.placeZ * MM)}mm ` +
    `= bridge ${round(ref.bridgeZ * MM)} + contact ${round(ref.contactZ * MM)} + pupil ${round(ref.pupilZ * MM)}` +
    ` + rest ${round(ref.restZ * MM)} + seat ${round(ref.seatZ * MM)}; ` +
    `rawZ ${round(ref.rawZ * MM)}mm need ${round((ref.need ?? NaN) * MM)}mm relZ ${round(ref.relZ * MM)}mm`);
  print('    |ang|   n  wPose |  dRawZ  dSmZ  dScl% | dBridge dCarr |  zeta  dZeta guard% gMean  gMax  ovfl | dSeat dNeed | dPupil dRest | dPlaceZ maxPlace | dRelZ  dFwdCam maxFwd');

  const out = [];
  for (const [lo, hi] of buckets) {
    const bs = rows.filter((f) => angleOf(f) >= lo && angleOf(f) < hi);
    if (!bs.length) continue;
    const d = (key) => bs.map((f) => f[key] - ref[key]).filter(Number.isFinite);
    const raw = (key) => bs.map((f) => f[key]).filter(Number.isFinite);
    const guardFires = bs.filter((f) => f.guard > 1e-6);
    const fwd = bs.map((f) => -(f.worldZ - ref.worldZ)).filter(Number.isFinite); // + = toward camera
    const b = {
      bucket: `${lo}-${hi === 90 ? '+' : hi}`,
      n: bs.length,
      w: round(mean(raw('w')), 1000),
      dRawZ: round(mean(d('rawZ')) * MM),
      dSmZ: round(mean(d('smZ')) * MM),
      dScalePct: round(mean(bs.map((f) => (f.scale / ref.scale - 1) * 100))),
      dBridge: round(mean(d('bridgeZ')) * MM),
      dCarried: round(mean(d('carriedZ')) * MM),
      zeta: round(mean(raw('zeta')) * MM),
      dZeta: round(mean(d('zeta')) * MM),
      guardRate: round(guardFires.length / bs.length, 1000),
      guardMean: round(mean(guardFires.map((f) => f.guard)) * MM),
      guardMax: round(maxOf(bs.map((f) => f.guard)) * MM),
      overflow: bs.reduce((a, f) => a + f.overflowDelta, 0),
      dSeat: round(mean(d('seatZ')) * MM),
      dNeed: round(mean(d('need')) * MM),
      dPupil: round(mean(d('pupilZ')) * MM),
      dRest: round(mean(d('restZ')) * MM),
      dPlaceZ: round(mean(d('placeZ')) * MM),
      maxPlaceZ: round(maxOf(d('placeZ')) * MM),
      dRelZ: round(mean(d('relZ')) * MM),
      dFwdCam: round(mean(fwd) * MM),
      maxFwdCam: round(maxOf(fwd) * MM),
      rebuilds: bs.reduce((a, f) => a + f.rebuildDelta, 0),
      depthW: round(mean(raw('depthW')), 1000),
    };
    out.push(b);
    print(`    ${b.bucket.padEnd(6)} ${String(b.n).padStart(3)}  ${String(b.w ?? '—').padEnd(5)} | ` +
      `${String(b.dRawZ ?? '—').padStart(6)} ${String(b.dSmZ ?? '—').padStart(5)} ${String(b.dScalePct ?? '—').padStart(5)} | ` +
      `${String(b.dBridge ?? '—').padStart(7)} ${String(b.dCarried ?? '—').padStart(5)} | ` +
      `${String(b.zeta ?? '—').padStart(5)} ${String(b.dZeta ?? '—').padStart(6)} ${String(b.guardRate ?? '—').padStart(6)} ` +
      `${String(b.guardMean ?? '—').padStart(5)} ${String(b.guardMax ?? '—').padStart(5)} ${String(b.overflow).padStart(5)} | ` +
      `${String(b.dSeat ?? '—').padStart(5)} ${String(b.dNeed ?? '—').padStart(5)} | ` +
      `${String(b.dPupil ?? '—').padStart(6)} ${String(b.dRest ?? '—').padStart(5)} | ` +
      `${String(b.dPlaceZ ?? '—').padStart(7)} ${String(b.maxPlaceZ ?? '—').padStart(8)} | ` +
      `${String(b.dRelZ ?? '—').padStart(5)} ${String(b.dFwdCam ?? '—').padStart(7)} ${String(b.maxFwdCam ?? '—').padStart(6)}`);
  }

  // The worst forward frame by placement z, decomposed whole.
  let worst = null;
  for (const f of rows) {
    const dz = f.placeZ - ref.placeZ;
    if (worst === null || dz > worst.dz) worst = { dz, f };
  }
  if (worst) {
    const f = worst.f;
    print(`    worst placeZ frame: t=${round(f.t / 1000)}s yaw ${round(f.yaw)} pitch ${round(f.pitch)} roll ${round(f.roll)} w ${round(f.w, 1000)} | ` +
      `dPlaceZ ${round(worst.dz * MM)}mm = dBridge ${round((f.bridgeZ - ref.bridgeZ) * MM)} + dContact ${round((f.contactZ - ref.contactZ) * MM)}` +
      ` + dPupil ${round((f.pupilZ - ref.pupilZ) * MM)} + dRest ${round((f.restZ - ref.restZ) * MM)} + dSeat ${round((f.seatZ - ref.seatZ) * MM)}` +
      ` (zeta ${round(f.zeta * MM)} guard ${round(f.guard * MM)} need ${round((f.need ?? NaN) * MM)}) | dRawZ ${round((f.rawZ - ref.rawZ) * MM)}mm`);
  }

  // The excursion statistic the fix phase can gate on.
  const placeExc = rows.map((f) => f.placeZ - ref.placeZ);
  const fwdExc = rows.map((f) => -(f.worldZ - ref.worldZ));
  const stats = {
    segment: segName,
    frames: rows.length,
    placeZExcursionMm: {
      max: round(maxOf(placeExc) * MM), p95: round(pct(placeExc, 0.95) * MM),
      min: round(minOf(placeExc) * MM),
    },
    fwdCamExcursionMm: {
      max: round(maxOf(fwdExc) * MM), p95: round(pct(fwdExc, 0.95) * MM),
      min: round(minOf(fwdExc) * MM),
    },
    buckets: out,
    ref: Object.fromEntries(Object.entries(ref).map(([k, v]) => [k, round(v, 10000)])),
  };
  print(`    excursion: placeZ max ${stats.placeZExcursionMm.max} / p95 ${stats.placeZExcursionMm.p95} mm; ` +
    `drawn-origin toward-camera max ${stats.fwdCamExcursionMm.max} / p95 ${stats.fwdCamExcursionMm.p95} mm`);
  return stats;
}

// ------------------------------------------------------------------------ run

async function run() {
  const params = new URLSearchParams(location.search);
  const mode = params.get('mode') ?? 'fixture';

  say('loading face + model…');
  const face = await loadCanonicalFace();
  const scene = createScene(document.getElementById('gl'), { preserveDrawingBuffer: true });
  const entry = MODELS.find((m) => m.value === DEFAULT_MODEL) ?? MODELS[0];
  const model = analyseModel(await loadGlassesModel(entry, import.meta.url));

  window.__zdecomp = { mode, done: false, passes: [] };

  if (mode === 'fixture') {
    const url = params.get('fixture');
    if (!url) throw new Error('pass ?fixture=<url> or ?mode=roll|yaw');
    const { header, records } = parseFixture(await loadFixtureText(url));
    print(`fixture ${header.subject}/${header.date} — ${records.length} frames ` +
      `${header.width}x${header.height}`, 'ok');
    const pass = await decompPass({ header, records, face, scene, model, label: 'production' });
    print(`sum check: |Σterms − placement.z| worst ${round(pass.sumCheckWorst * MM, 1e6)} mm`,
      pass.sumCheckWorst < 1e-6 ? 'ok' : 'fail');
    const segStats = [];
    for (let s = 0; s < header.segments.length; s++) {
      const axis = header.segments[s].name === 'pitch' ? 'pitch' : 'yaw';
      segStats.push(reportSegment(pass.frames, header, s, axis));
      await tick();
    }
    window.__zdecomp.passes.push({ label: pass.label, header: { subject: header.subject },
      segments: segStats, frames: pass.frames });
  } else {
    // Synthetic sweep, production path. Roll mode runs twice: direct, and
    // through the rollSearch rotate→unrotate round-trip, and diffs them.
    scene.resize(1280, 720, 1280 / 720);
    const axis = mode === 'roll' ? 'roll' : 'yaw';
    const { header, records } = synthStream(face, scene.camera, axis);
    print(`synthetic ${axis} sweep — ${records.length} frames, plateaus 10..50°`, 'ok');

    const direct = await decompPass({ header, records, face, scene, model, label: `${axis}-direct` });
    print(`sum check (direct): worst ${round(direct.sumCheckWorst * MM, 1e6)} mm`,
      direct.sumCheckWorst < 1e-6 ? 'ok' : 'fail');
    const stats = [];
    for (let s = 0; s < header.segments.length; s++) {
      stats.push(reportSegment(direct.frames, header, s, axis));
      await tick();
    }
    window.__zdecomp.passes.push({ label: direct.label, segments: stats, frames: direct.frames });

    if (axis === 'roll') {
      // The production detect path at >~15° roll: canvas rotated upright by
      // the aim law (wanted = −eye-line angle ⇒ rotation ≈ −roll), detector
      // reads the rotated canvas, unrotateDetection maps back. Forward and
      // inverse are exact mirrors, so any placement difference IS the bias
      // of the unrotation math (and of nothing else).
      let worstLm = 0;
      let worstMat = 0;
      const throughRoll = await decompPass({
        header, records, face, scene, model, label: 'roll-rollSearch',
        mutate: (detection) => {
          poseMatrix.fromArray(detection.matrix);
          poseMatrix.decompose(rawPos, rawQuat, rawScl);
          euler.setFromQuaternion(rawQuat, 'YXZ');
          const rollDeg = euler.z * (180 / Math.PI);
          if (Math.abs(rollDeg) < 15) return; // inside ROLL_REAIM — no rotation
          const rotation = -euler.z; // the aim law stands the eye line level
          const before = {
            landmarks: detection.landmarks.map((p) => ({ ...p })),
            matrix: detection.matrix.slice(),
          };
          rotateDetection(detection, rotation, 1280, 720);
          unrotateDetection(detection, rotation, 1280, 720);
          for (let k = 0; k < detection.landmarks.length; k++) {
            worstLm = Math.max(worstLm,
              Math.abs(detection.landmarks[k].x - before.landmarks[k].x) * 1280,
              Math.abs(detection.landmarks[k].y - before.landmarks[k].y) * 720);
          }
          for (let k = 0; k < 16; k++) {
            worstMat = Math.max(worstMat, Math.abs(detection.matrix[k] - before.matrix[k]));
          }
        },
      });
      print(`rollSearch round-trip: worst landmark err ${worstLm.toExponential(2)} px, ` +
        `worst matrix err ${worstMat.toExponential(2)} (translation cm)`,
        worstLm < 1e-9 && worstMat < 1e-9 ? 'ok' : 'fail');
      let worstDz = 0;
      const n = Math.min(direct.frames.length, throughRoll.frames.length);
      for (let i = 0; i < n; i++) {
        worstDz = Math.max(worstDz,
          Math.abs(direct.frames[i].placeZ - throughRoll.frames[i].placeZ));
      }
      print(`placement.z direct vs through-rollSearch: worst |Δ| ${round(worstDz * MM, 1e6)} mm ` +
        'over the whole sweep', worstDz < 1e-6 ? 'ok' : 'fail');
      window.__zdecomp.rollSearch = {
        worstLandmarkPx: worstLm, worstMatrix: worstMat, worstPlaceZMm: worstDz * MM,
      };
    }
  }

  window.__zdecomp.done = true;
  say('done');
  document.title = 'DONE — z-decomp';
}

run().catch((error) => {
  say(`FAILED: ${error}`);
  print(String(error?.stack ?? error), 'fail');
  document.title = 'FAIL — z-decomp';
  window.__zdecomp = window.__zdecomp ?? {};
  window.__zdecomp.error = String(error);
  window.__zdecomp.done = true;
});
