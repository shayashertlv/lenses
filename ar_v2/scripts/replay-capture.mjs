#!/usr/bin/env node
/**
 * Replays a real capture through the SHIPPING estimator — no harness, no
 * synthetic anything — and then seats every catalogue frame on the result.
 *
 * This exists because every accuracy figure in this repository except
 * `docs/REAL-FACE.md` is measured on a population drawn from the same shape
 * basis the estimator fits. A recorded capture is the only input that does not
 * share the estimator's assumptions, and until 2026-08-26 nothing in the tree
 * could consume one.
 *
 *     node scripts/replay-capture.mjs "<path to capture.ndjson>"
 *
 * Captures come from the app's **Save this scan** control. They are NOT
 * committed and should not be: a capture is a 478-point facial landmark stream
 * of a named person, and `docs/PRIVACY.md` records what happened the last time
 * this repository held one. Keep them outside the tree and quote the numbers.
 *
 * Two things to read first in the output, because they are the two the
 * synthetic harness cannot show you:
 *
 *   payload:  how many frames carry `visibility` and `silhouette`. Both are
 *             app-supplied, and the harness supplies what the app does not.
 *   scale:    which rung, and its sigma. With no PD this is the iris, and two
 *             scans of one face measured 5.2% apart on it.
 */

import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Relative to this script, so the tree can move.
const R = resolve(dirname(fileURLToPath(import.meta.url)), '..').split('\\').join('/');
const imp = (p) => import(pathToFileURL(`${R}/dist/src/${p}.js`).href);

const { parseCapture } = await imp('enroll/telemetry');
const { enroll } = await imp('enroll/enroll');
const { parseFaceObj, standardRegions, measure } = await imp('core/mesh');
const { buildAnthropometricBasis } = await imp('core/shape/anthropometric');

const file = process.argv[2];
const text = readFileSync(file, 'utf8');
const capture = parseCapture(text);
const h = capture.header;

console.log(`\n=== ${file.split(/[\\/]/).pop()} ===`);
console.log(`subject "${h.subject}"  ${h.date}  ${h.width}x${h.height}  frames ${capture.frames.length}`);
console.log(`note: ${h.note ?? '(none)'}`);
console.log(`intrinsics: f=${h.intrinsics.f.toFixed(2)} solved=${h.intrinsicsSolved}  knownPdMm=${h.knownPdMm}`);

const beats = {};
for (const f of capture.frames) beats[f.beat] = (beats[f.beat] ?? 0) + 1;
console.log('beats:', Object.entries(beats).map(([k, v]) => `${k} ${v}`).join(', '));

// What the app actually supplies, field by field — the app/enroll seam.
const withVis = capture.frames.filter((f) => f.visibility).length;
const withSil = capture.frames.filter((f) => f.silhouette).length;
console.log(`payload: visibility on ${withVis}/${capture.frames.length}, silhouette on ${withSil}/${capture.frames.length}`);

const mesh = parseFaceObj(readFileSync(`${R}/assets/face/canonical_face_model.obj`, 'utf8'));
const basis = buildAnthropometricBasis(mesh);
const regions = standardRegions(mesh);

const t0 = Date.now();
const result = enroll({
  mesh, basis,
  frames: capture.frames,
  imageWidth: h.width, imageHeight: h.height,
  ...(h.knownPdMm ? { knownPdMm: h.knownPdMm } : {}),
});
const ms = Date.now() - t0;
const m = result.model;

console.log(`\n--- solved in ${ms} ms (${result.ranOn ?? 'inline'}) ---`);
console.log(`degraded:            ${m.degraded}`);
if (m.notes.length) console.log(`notes:               ${m.notes.join(' | ')}`);
console.log(`frames used:         ${m.framesUsed}`);
console.log(`reprojection rms:    ${m.reprojectionRmsPx.toFixed(2)} px`);
console.log(`variance factor:     ${m.varianceFactor.toFixed(3)}`);
console.log(`intrinsics solved:   ${m.intrinsicsSolved}   f=${m.intrinsics.f.toFixed(1)}`);
console.log(`scale:               ${m.scale.source}  x${m.scale.factor.toFixed(4)}  +/-${(m.scale.sigma * 100).toFixed(2)}%`);
if (m.scale.disagreementPct != null) console.log(`ruler disagreement:  ${m.scale.disagreementPct.toFixed(2)}%`);
console.log(`PD:                  ${m.pdMm === null ? 'null' : m.pdMm.toFixed(1) + ' mm'}${m.pdSigmaMm ? ' +/- ' + m.pdSigmaMm.toFixed(1) : ''}`);
console.log(`nose sigma:          ${(m.quality.nose?.sigmaMm ?? NaN).toFixed(3)} mm`);
console.log(`nose observations:   ${m.quality.nose?.observations ?? 'n/a'}`);
console.log(`field rms:           ${m.displacementRmsMm.toFixed(3)} mm   max ${m.displacementMaxMm.toFixed(3)}`);
console.log(`coverage:            ${JSON.stringify(result.coverage)}`);

const meas = m.measurements;
console.log('\n--- measurements (mm) ---');
for (const [k, v] of Object.entries(meas)) {
  console.log(`  ${k.padEnd(26)} ${typeof v === 'number' ? v.toFixed(2) : v}`);
}

// Seat every catalogue asset on this real face.
const { readGlb } = await imp('fit/mesh-io');
const { frameFromMesh } = await imp('fit/frame-from-mesh');
const { CATALOGUE } = await imp('fit/catalogue');
const { solveSeat } = await imp('fit/contact');

console.log('\n--- every catalogue frame, seated on this face ---');
console.log('asset                  earSrc     descent  padDepth   panto  padLoad  conv');
for (const e of CATALOGUE) {
  const built = frameFromMesh(readGlb(new Uint8Array(readFileSync(`${R}/${e.file}`))), e);
  if (!built.ok) { console.log(`${e.id.padEnd(22)} REFUSED  ${built.reason.slice(0, 60)}`); continue; }
  const a = built.asset;
  const s = solveSeat(m, mesh, regions, a);
  console.log(
    `${e.id.padEnd(22)} ${a.earRestSource.padEnd(9)} ` +
    `${s.descentMm.toFixed(2).padStart(7)} ${s.padDepthErrorMm.toFixed(2).padStart(9)} ` +
    `${s.pantoscopicDeg.toFixed(1).padStart(6)} ${(s.padLoadFraction * 100).toFixed(0).padStart(6)}% ` +
    `${String(s.converged).padStart(6)}`);
}

// ---------------------------------------------------------------- the wear phase

function std(xs) {
  if (xs.length < 2) return 0;
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  let s = 0;
  for (const x of xs) s += (x - mean) * (x - mean);
  return Math.sqrt(s / (xs.length - 1));
}

function percentile(xs, p) {
  const s = [...xs].sort((a, b) => a - b);
  if (s.length === 0) return 0;
  return s[Math.min(s.length - 1, Math.max(0, Math.floor(p * s.length)))];
}

if (capture.wear.length === 0) {
  console.log("\n--- no wear frames in this capture ---");
  console.log("The file is a v1 scan, or the session was saved before a frame went on.");
} else {
  const { createTracker, track } = await imp("track/tracker");
  const { shippedTrackerOptions, shippedSigma } = await imp("track/profile");
  const { createUncertainty } = await imp("detect/uncertainty");
  const { landmarkSurface } = await imp("core/facemodel");
  const { DETECT_LONG_SIDE } = await imp("detect/mediapipe");
  const { project } = await imp("core/camera");
  const { rotationAngleBetween } = await imp("core/linalg");
  const { LM } = await imp("core/mesh");
  const { distribution } = await imp("testkit/metrics");

  // The app detects at DETECT_LONG_SIDE and scales the landmarks up to source
  // pixels, so every pixel threshold is scaled with them. Reproduced from
  // `framelock.ts`'s `resize` rather than assumed: getting it wrong halves the
  // sigma floor and the tracker trusts the frame four times too much.
  const shrink = Math.min(1, DETECT_LONG_SIDE / Math.max(h.width, h.height));
  const detectW = Math.max(1, Math.round(h.width * shrink));
  const pixelScale = h.width / detectW;

  const K = h.intrinsics;
  const surface = landmarkSurface(m);
  const bridge = LM.NOSE_BRIDGE * 3;

  const applyPose = (pose, p, o) => {
    const x = p[o], y = p[o + 1], z = p[o + 2], R = pose.R;
    return [
      R[0] * x + R[1] * y + R[2] * z + pose.t[0],
      R[3] * x + R[4] * y + R[5] * z + pose.t[1],
      R[6] * x + R[7] * y + R[8] * z + pose.t[2],
    ];
  };

  // ---- 1. fidelity: does replaying the landmarks reproduce the session? ----
  //
  // Everything below is worthless if this fails. A replay that does not land on
  // the poses the app rendered is a replay of some other system, and not
  // measuring some other system is the entire reason to record.
  const replay = (opts) => {
    const state = createTracker(m, shippedTrackerOptions({
      mesh, regions, pixelScale, ...opts,
    }));
    const unc = createUncertainty(mesh.vertexCount);
    const out = [];
    let prevRaw = null;
    for (const f of capture.wear) {
      const sv = shippedSigma({
        state: unc, landmarks: f.landmarks, mesh, positions: surface,
        intrinsics: K, previousPose: prevRaw, pixelScale,
      });
      const r = track(state, {
        landmarks: f.landmarks, sigmaPx: sv.sigmaPx, visibility: sv.visibility,
        intrinsics: K, dt: f.dt,
      });
      prevRaw = r.rawPose ?? (state.lastRaw ? prevRaw : null);
      out.push(r);
    }
    return out;
  };

  // The window is ROLLING, so it opens mid-session with the app's tracker
  // already warm — a previous pose, a smoother with history, a velocity. The
  // replay starts cold and cannot have any of that, so its first frames
  // reproduce a different initial condition rather than a different code path.
  // Those frames are reported separately instead of being averaged into a
  // fidelity claim they are not evidence about.
  const WARMUP = 15;
  const shipped = replay({});
  let bothRefused = 0, worstMm = 0, worstDeg = 0, worstAt = -1;
  const dMm = [], dDeg = [], coldMm = [];
  for (let i = 0; i < capture.wear.length; i++) {
    const rec = capture.wear[i].emitted, got = shipped[i].pose;
    if (!rec && !got) { bothRefused++; continue; }
    if (!rec || !got) continue;
    const d = Math.hypot(got.t[0] - rec.t[0], got.t[1] - rec.t[1], got.t[2] - rec.t[2]);
    const a = (rotationAngleBetween(got.R, rec.R) * 180) / Math.PI;
    if (i < WARMUP) { coldMm.push(d); continue; }
    dMm.push(d); dDeg.push(a);
    if (d > worstMm) { worstMm = d; worstDeg = a; worstAt = i; }
  }
  const matched = dMm.length;
  const trackedFrames = capture.wear.filter((f) => f.raw).length;

  console.log("\n=== THE WEAR PHASE, REPLAYED THROUGH THE SHIPPING TRACKER ===");
  console.log(`frames ${capture.wear.length}   the app tracked ${trackedFrames}, refused ${capture.wear.length - trackedFrames}`);
  console.log(`detect ${detectW} px wide, so pixelScale ${pixelScale.toFixed(2)}`);
  console.log(`\nfidelity: ${matched} frames after a ${WARMUP}-frame warm-up, ${bothRefused} refused by both`);
  if (matched) {
    const md = distribution(dMm);
    const over = (t) => dMm.filter((x) => x > t).length;
    const pct = (t) => (100 * (1 - over(t) / matched)).toFixed(1);
    console.log(`  emitted pose reproduced to ${md.median.toExponential(2)} mm median, ${md.p90.toExponential(2)} p90`);
    console.log(`  rotation                   ${distribution(dDeg).median.toExponential(2)} deg median`);
    console.log(`  within 0.01 mm on ${pct(0.01)}% of frames, 0.1 mm on ${pct(0.1)}%, 1 mm on ${pct(1)}%`);
    console.log(`  worst ${worstMm.toFixed(3)} mm / ${worstDeg.toFixed(3)} deg at frame ${worstAt}`);
    if (coldMm.length) {
      console.log(`  the ${coldMm.length} warm-up frames, excluded: worst ${Math.max(...coldMm).toFixed(3)} mm`);
      console.log('    (the app was warm there and a rolling window cannot record the state that made it so)');
    }
    // Landmarks are stored to a fixed number of decimals. A pose is a nonlinear
    // function of them and a gate can flip on the last digit, so quantisation
    // sets the floor this check can reach; it is not a code divergence.
    console.log(over(1) === 0
      ? '  FAITHFUL — every post-warm-up frame lands within a millimetre of the session.'
      : `  ${over(1)} frames diverge by more than a millimetre. Read those before trusting anything below.`);
  }

  // ---- 2. what the camera actually saw ------------------------------------
  //
  // Both of the synthetic harness's uncalibrated stimuli are measurable from
  // here: `CaptureOptions.wanderScale` (how fast a resting head moves) and
  // `CaptureOptions.noisePx` (the detector's landmark noise). Nothing set
  // either from a measurement before this file existed.
  const speed = [];
  for (let i = 1; i < capture.wear.length; i++) {
    const a = capture.wear[i - 1].raw, b = capture.wear[i].raw;
    if (!a || !b) { speed.push(null); continue; }
    const pa = applyPose(a, m.positions, bridge), pb = applyPose(b, m.positions, bridge);
    speed.push(Math.hypot(pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]));
  }
  const finite = speed.filter((s) => s !== null);
  const sd = distribution(finite);

  // The longest contiguous run below the 25th percentile: the wearer holding
  // still. FOUND rather than assumed, because "the first ten seconds" is a
  // claim about what somebody did and the recording actually knows.
  const quiet = percentile(finite, 0.25);
  let best = { start: 0, len: 0 }, run = 0;
  for (let i = 0; i < speed.length; i++) {
    if (speed[i] !== null && speed[i] <= quiet) {
      run++;
      if (run > best.len) best = { start: i - run + 1, len: run };
    } else run = 0;
  }

  console.log("\n=== WHAT THE CAMERA ACTUALLY SAW ===");
  console.log(`bridge speed, mm/frame:  median ${sd.median.toFixed(3)}   p90 ${sd.p90.toFixed(3)}   worst ${sd.worst.toFixed(3)}`);
  console.log(`longest still run:       ${best.len} frames (${(best.len / 30).toFixed(1)} s) at or below ${quiet.toFixed(3)} mm/frame`);
  const stillSpeeds = speed.slice(best.start, best.start + best.len).filter((s) => s !== null);
  const ss = distribution(stillSpeeds);
  console.log(`  its speed:             median ${ss.median.toFixed(3)}   p90 ${ss.p90.toFixed(3)} mm/frame`);
  console.log(`  the synthetic wander runs 1.328 mm/frame median on the same measure, so`);
  console.log(`  CAPTURE_DEFAULTS.wanderScale would be about ${(ss.median / 1.328).toFixed(2)} to match this wearer.`);

  // ---- 3. the detector's landmark noise, Q1 -------------------------------
  //
  // Each landmark's residual against the model projected at that frame's OWN
  // raw pose already has the rigid head motion removed by the pose. What the
  // model gets wrong about a landmark is static in face space, so over a still
  // run it contributes a constant offset and not a variance. The standard
  // deviation across the run is therefore the detector's noise.
  const noiseOf = (indices) => {
    const per = [];
    const uv = new Float64Array(2);
    for (let v = 0; v < mesh.vertexCount; v++) {
      const rx = [], ry = [];
      for (const i of indices) {
        const f = capture.wear[i];
        if (!f || !f.raw) continue;
        const lx = f.landmarks[v * 2], ly = f.landmarks[v * 2 + 1];
        if (!Number.isFinite(lx) || !Number.isFinite(ly)) continue;
        const cam = applyPose(f.raw, surface, v * 3);
        if (!project(uv, K, cam)) continue;
        rx.push(lx - uv[0]); ry.push(ly - uv[1]);
      }
      if (rx.length < 8) continue;
      per.push(Math.hypot(std(rx), std(ry)) / Math.SQRT2);
    }
    return per;
  };

  // Every quiet frame, not just the longest contiguous run of them. The
  // residual is taken against each frame's OWN pose, so the frames do not have
  // to be adjacent for their spread to be the detector's noise — and the
  // longest run here was 23 frames, which is a 15% error bar on a standard
  // deviation. Requiring contiguity was buying nothing and costing an order of
  // magnitude of samples.
  const quietFrames = [];
  for (let i = 0; i < speed.length; i++) if (speed[i] !== null && speed[i] <= quiet) quietFrames.push(i + 1);
  const allFrames = capture.wear.map((_, i) => i);
  const sn = distribution(noiseOf(quietFrames));
  const an = distribution(noiseOf(allFrames));

  console.log("\n=== THE DETECTOR'S LANDMARK NOISE -- Q1, MEASURED ===");
  console.log("per-landmark standard deviation of the reprojection residual, SOURCE px");
  console.log(`  over ${quietFrames.length} quiet frames: median ${sn.median.toFixed(3)}   p90 ${sn.p90.toFixed(3)}   worst ${sn.worst.toFixed(2)}   (${sn.n} landmarks)`);
  console.log(`  over every frame:     median ${an.median.toFixed(3)}   p90 ${an.p90.toFixed(3)}   worst ${an.worst.toFixed(2)}`);
  console.log(`  in DETECT px (/${pixelScale.toFixed(0)}):    median ${(sn.median / pixelScale).toFixed(3)}   p90 ${(sn.p90 / pixelScale).toFixed(3)}`);
  console.log("");
  console.log("  UNCERTAINTY_DEFAULTS.floorPx assumes 0.7 DETECT px.");
  console.log("  CAPTURE_DEFAULTS.noisePx assumes 0.7 at capture resolution, which is the");
  console.log(`  SOURCE column -- so the harness simulates ${(sn.median / 0.7).toFixed(1)}x less noise than this camera makes.`);

  // ---- 4. lag and shimmer, on this wearer ---------------------------------
  const lag = [], lagDeg = [];
  for (const f of capture.wear) {
    if (!f.emitted || !f.raw) continue;
    const e = applyPose(f.emitted, m.positions, bridge), r = applyPose(f.raw, m.positions, bridge);
    lag.push(Math.hypot(e[0] - r[0], e[1] - r[1], e[2] - r[2]));
    lagDeg.push((rotationAngleBetween(f.emitted.R, f.raw.R) * 180) / Math.PI);
  }
  const secondDiff = (poses) => {
    const out = [];
    for (let i = 2; i < poses.length; i++) {
      const a = poses[i - 2], b = poses[i - 1], c = poses[i];
      if (!a || !b || !c) continue;
      const pa = applyPose(a, m.positions, bridge);
      const pb = applyPose(b, m.positions, bridge);
      const pc = applyPose(c, m.positions, bridge);
      out.push(Math.hypot(
        pc[0] - 2 * pb[0] + pa[0], pc[1] - 2 * pb[1] + pa[1], pc[2] - 2 * pb[2] + pa[2],
      ));
    }
    return out;
  };
  const emittedShim = distribution(secondDiff(capture.wear.map((f) => f.emitted)));
  const rawShim = distribution(secondDiff(capture.wear.map((f) => f.raw)));
  const ld = distribution(lag);

  console.log("\n=== WHAT THE FILTER DID, ON THIS WEARER ===");
  console.log(`lag (emitted vs raw):    median ${ld.median.toFixed(3)} mm   p90 ${ld.p90.toFixed(3)}   worst ${ld.worst.toFixed(2)}`);
  console.log(`                         median ${distribution(lagDeg).median.toFixed(3)} deg  p90 ${distribution(lagDeg).p90.toFixed(3)}`);
  console.log("shimmer, second difference of the bridge, mm:");
  console.log(`  raw solve              median ${rawShim.median.toFixed(3)}   p90 ${rawShim.p90.toFixed(3)}`);
  console.log(`  emitted (smoothed)     median ${emittedShim.median.toFixed(3)}   p90 ${emittedShim.p90.toFixed(3)}`);
  console.log(`  the filter removed ${(100 * (1 - emittedShim.median / rawShim.median)).toFixed(0)}% of it at the median.`);

  // ---- 5. the configurations, replayed on these landmarks -----------------
  console.log("\n=== CONFIGURATIONS, REPLAYED ON THIS WEARER'S OWN LANDMARKS ===");
  console.log("No ground truth exists here, so these are absolute motion rather than");
  console.log("error: shimmer is what the eye punishes, lag is what it costs.");
  console.log("");
  console.log("config                    shimmer med   shimmer p90     lag med    refused");
  for (const [label, opts] of [
    ["shipped (smooth+prior)", {}],
    ["no smoothing          ", { smooth: false }],
    ["no motion prior       ", { motionPrior: false }],
    ["neither               ", { smooth: false, motionPrior: false }],
    ["locked                ", { smooth: "locked" }],
    ["adaptive              ", { smooth: "adaptive" }],
  ]) {
    const rs = replay(opts);
    const sh = distribution(secondDiff(rs.map((r) => r.pose ?? null)));
    const lg = [];
    for (const r of rs) {
      if (!r.pose || !r.rawPose) continue;
      const a = applyPose(r.pose, m.positions, bridge), b = applyPose(r.rawPose, m.positions, bridge);
      lg.push(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]));
    }
    const refused = rs.filter((r) => !r.tracked || !r.pose).length;
    console.log(
      `${label}  ${sh.median.toFixed(3).padStart(11)}  ${sh.p90.toFixed(3).padStart(12)}  `
      + `${distribution(lg).median.toFixed(3).padStart(10)}  ${String(refused).padStart(9)}`,
    );
  }
}
