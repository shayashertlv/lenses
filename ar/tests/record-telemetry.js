/**
 * TELEMETRY RECORDER — the live capture protocol (anchoring-v3 stage R0).
 *
 * ⚠ THIS PAGE USES requestAnimationFrame — deliberately, and it is the ONLY
 * page under tests/ allowed to. Every other harness page is no-rAF so it runs
 * in a hidden pane; this one exists to run in the USER'S REAL CHROME with a
 * live camera, where rAF is the camera loop's clock exactly as in `main.js`.
 * (The `?source=sample` self-test below runs the no-rAF loop instead — the
 * pipeline-check idiom — so the format and the replay can be validated
 * end-to-end in a hidden pane without a camera.)
 *
 * WHAT IT DOES. Boots the production tracker and camera the way `main.js`
 * does (main-thread tracker — the plan allows it; the recorded data is
 * landmarks + matrices, which are identical either side of the worker hop),
 * walks the wearer through the scripted segment protocol with big on-screen
 * countdowns — each segment announced 3 s BEFORE it starts — and records one
 * NDJSON line per detected frame:
 *
 *     line 0:  { v:1, subject, date, width, height, fovDeg, note,
 *                segments:[{name, t0, t1}, …] }   (t on the camera clock, ms)
 *     line N:  { t:<cameraMs>, m:[16 floats column-major, 6 dp],
 *                l:[478×3 normalized, 5 dp, flat x,y,z,x,y,z…] }
 *
 * 5-decimal landmarks ≈ 0.006 px at 640 wide — far under detector noise —
 * and keep gzip small. Landmarks and matrices ONLY: **no pixels ever leave
 * the page** (nothing is uploaded anywhere either; the file downloads to the
 * wearer's own disk). At the end it offers `telemetry-<date>.ndjson`, plus a
 * `.ndjson.gz` when CompressionStream exists.
 *
 * The full placement pipeline also runs live (headless scene, real
 * `updateFrame`) purely to feed `__ar.stab` — the trailing-5 s stillness
 * meter that the protocol reads out loud (PASS bars: ≤ 2 px during the still
 * and eye-circle segments, ≤ 5 px at the pitch holds). Recording does not
 * depend on it; a session where the model fails to load still records.
 *
 * SELF-TEST (`?source=sample`): the sample face stands in for the camera, a
 * synthetic 30 fps clock stands in for rVFC, segments shrink to 2 s with 1 s
 * lead-ins, and the finished fixture lands in IndexedDB (`idb:selftest`) for
 * `telemetry-replay.html?fixture=idb:selftest` to consume — the whole loop,
 * validated without a camera or a single rAF tick.
 */

import { loadCanonicalFace } from '../src/canonical-face.js';
import { MODELS, DEFAULT_MODEL, loadGlassesModel } from '../src/models.js';
import { createTracker, DETECT_LONG_SIDE } from '../src/tracker.js';
import { createScene } from '../src/scene.js';
import { createOccluder } from '../src/occluder.js';
import { analyseModel, DEFAULT_FIT } from '../src/fit.js';
import { updateFrame } from '../src/frame.js';
import { PoseSmoother, DEFAULT_SMOOTHING } from '../src/smoothing.js';
import { createCameraSource, createSampleSource } from '../src/sources.js';
import { createStabMeter, screenPointOf } from '../src/stab.js';
import { putFixture } from './telemetry-store.js';

const params = new URLSearchParams(location.search);
const SAMPLE_MODE = params.get('source') === 'sample';

/**
 * The protocol, verbatim from the v3 plan's capture flow (~90 s of segments;
 * each is announced through a 3 s lead-in before its clock starts). The
 * pass-bar text is what the wearer reads against the meter, out loud, DURING
 * the segment — the numbers are decided live, not reconstructed later.
 *
 * `yaw-hold` is an ADDITION (2026-08-18) and the reason is measured, not
 * stylistic. The metric that decides the ">40° forward push" — the wearer's
 * own complaint, and the delta the stage-10 ratchet stopped on — is
 * `over40MeanMm`, taken over the frames with |yaw| > 40°. On the 2026-08-17
 * capture the `yaw` segment produced **ten** such frames: a third of a second,
 * and exactly the minimum `over40Frames >= 10` the gate refuses to run below.
 * A sweep at ±30° barely enters the regime it is being read for, and it never
 * DWELLS there, while the complaint is specifically about a HELD turn. So the
 * sweep stays exactly as it was — every existing metric is computed on the
 * same instructions and stays comparable — and a segment that holds past 40°
 * is added beside it. It opens with a frontal dwell on purpose: `seatZStats`
 * takes its zero from the segment's own first-quarter frames with every axis
 * under 8°, and a segment that starts mid-turn has no such frames and falls
 * back to whatever its first ten happen to be.
 */
const LEAD_S = SAMPLE_MODE ? 1 : 3;
const SEGMENTS = [
  { name: 'still', seconds: 15, text: 'Hold perfectly still. Look at the camera.',
    bar: 'PASS ≤ 2 px' },
  { name: 'eye-circles', seconds: 15, text: 'Head STILL — roll your eyes in slow circles.',
    bar: 'PASS ≤ 2 px' },
  { name: 'glances', seconds: 10, text: 'Head STILL — glance hard left, hold, back. Then right.',
    bar: 'PASS ≤ 2 px' },
  { name: 'pitch', seconds: 15, text: 'Tilt your head back to your comfort limit, hold 3 s, return. Twice.',
    bar: 'PASS ≤ 5 px at the holds' },
  { name: 'yaw', seconds: 15, text: 'Sweep your head left–right, about ±30°, slow and steady.',
    bar: 'watch for slide across the face' },
  { name: 'yaw-hold', seconds: 22,
    text: 'Face the camera 4 s. Then FULL left, hold 5 s — centre — FULL right, hold 5 s.',
    bar: 'watch: does the frame creep FORWARD off the nose at the holds?' },
  { name: 'browse', seconds: 20, text: 'Browse normally — move however you naturally would.',
    bar: 'subjective: settled? any slide?' },
].map((s) => (SAMPLE_MODE ? { ...s, seconds: 2 } : s));

const dom = {
  view: document.getElementById('view'),
  instruction: document.getElementById('instruction'),
  countdown: document.getElementById('countdown'),
  meter: document.getElementById('meter'),
  status: document.getElementById('status'),
  stage: document.getElementById('stage'),
  done: document.getElementById('done'),
};
const say = (t) => { dom.status.textContent = t; };

const round5 = (x) => Math.round(x * 1e5) / 1e5;
const round6 = (x) => Math.round(x * 1e6) / 1e6;

async function run() {
  say('loading face model…');
  const face = await loadCanonicalFace();
  const scene = createScene(document.getElementById('gl'));
  const occluder = createOccluder(face);
  const state = { occluder };
  const smoother = new PoseSmoother(DEFAULT_SMOOTHING);
  const fit = { ...DEFAULT_FIT };
  const stab = createStabMeter();

  // The glasses load in parallel and the recorder does not wait: the stab
  // meter needs them, the RECORDING does not, and a capture must never be
  // hostage to a 24 MB asset.
  let model = null;
  const entry = MODELS.find((m) => m.value === DEFAULT_MODEL) ?? MODELS[0];
  loadGlassesModel(entry, import.meta.url)
    .then((root) => { model = analyseModel(root); })
    .catch((error) => console.warn('glasses failed to load — recording anyway', error));

  say('loading tracker…');
  const tracker = await createTracker({ onStatus: say });

  say(SAMPLE_MODE ? 'sample self-test…' : 'requesting camera…');
  const source = SAMPLE_MODE
    ? await createSampleSource(new URL('../assets/samples/face-a.jpg', import.meta.url).href)
    : await createCameraSource({
      onLost: () => say('CAMERA LOST — the recording cannot continue'),
    });

  // Wait for real pixels before sizing anything off them.
  for (let i = 0; i < 600 && !(source.ready && source.width > 0); i++) {
    source.update(performance.now());
    await new Promise((r) => setTimeout(r, 50));
  }
  if (!(source.width > 0)) throw new Error('source never produced pixels');

  scene.resize(source.width, source.height, source.width / source.height);
  dom.view.width = source.width;
  dom.view.height = source.height;
  const viewCtx = dom.view.getContext('2d');

  // The production detect path: capped long side, same as `main.js` (no roll
  // search — the protocol is seated and roughly upright; a capture that needs
  // the supine machinery is a different protocol).
  const shrink = Math.min(1, DETECT_LONG_SIDE / Math.max(source.width, source.height));
  const detectCanvas = document.createElement('canvas');
  detectCanvas.width = Math.round(source.width * shrink);
  detectCanvas.height = Math.round(source.height * shrink);
  const detectCtx = detectCanvas.getContext('2d');

  // ---------------------------------------------------------- the recording
  const lines = [];
  const segments = SEGMENTS.map((s) => ({ name: s.name, t0: null, t1: null }));
  const perSegmentFrames = SEGMENTS.map(() => 0);
  let stage = { phase: 'lead', index: 0, phaseStartMs: null };
  let lastSubmitMs = 0;
  let recordingDone = false;
  let framesRecorded = 0;
  let misses = 0;

  const hud = (cameraMs) => {
    const seg = SEGMENTS[stage.index];
    const elapsed = (cameraMs - stage.phaseStartMs) / 1000;
    if (stage.phase === 'lead') {
      const left = Math.max(LEAD_S - elapsed, 0);
      dom.stage.className = 'lead';
      dom.instruction.textContent = `NEXT — ${seg.text}`;
      dom.countdown.textContent = Math.ceil(left).toFixed(0);
    } else {
      const left = Math.max(seg.seconds - elapsed, 0);
      dom.stage.className = 'run';
      dom.instruction.textContent = `${seg.text}  (${seg.bar})`;
      dom.countdown.textContent = Math.ceil(left).toFixed(0);
    }
  };

  /** Advances the segment script on the camera clock; true while recording. */
  const advance = (cameraMs) => {
    if (stage.phaseStartMs === null) stage.phaseStartMs = cameraMs;
    const seg = SEGMENTS[stage.index];
    const elapsed = (cameraMs - stage.phaseStartMs) / 1000;
    if (stage.phase === 'lead' && elapsed >= LEAD_S) {
      stage = { phase: 'run', index: stage.index, phaseStartMs: cameraMs };
      segments[stage.index].t0 = Math.round(cameraMs * 10) / 10;
    } else if (stage.phase === 'run' && elapsed >= seg.seconds) {
      segments[stage.index].t1 = Math.round(cameraMs * 10) / 10;
      if (stage.index + 1 >= SEGMENTS.length) return false;
      stage = { phase: 'lead', index: stage.index + 1, phaseStartMs: cameraMs };
    }
    return true;
  };

  const record = (cameraMs, detection) => {
    const m = new Array(16);
    for (let i = 0; i < 16; i++) m[i] = round6(detection.matrix[i]);
    const l = new Array(detection.landmarks.length * 3);
    for (let i = 0; i < detection.landmarks.length; i++) {
      const p = detection.landmarks[i];
      l[i * 3] = round5(p.x);
      l[i * 3 + 1] = round5(p.y);
      l[i * 3 + 2] = round5(p.z);
    }
    lines.push(JSON.stringify({ t: Math.round(cameraMs * 10) / 10, m, l }));
    framesRecorded++;
    if (stage.phase === 'run') perSegmentFrames[stage.index]++;
  };

  /** One camera frame through detect → record → pipeline → meter → paint. */
  const processFrame = (frame, nowMs) => {
    detectCtx.drawImage(source.element, 0, 0, detectCanvas.width, detectCanvas.height);
    const detection = tracker.detect(detectCanvas, frame.timestampMs);
    viewCtx.drawImage(source.element, 0, 0);
    if (!advance(frame.capturedAtMs)) return false;
    hud(frame.capturedAtMs);
    if (!detection) { misses++; return true; }

    record(frame.capturedAtMs, detection);

    // The live meter — the whole reason the pipeline runs during a capture.
    const dt = lastSubmitMs ? Math.min((frame.capturedAtMs - lastSubmitMs) / 1000, 0.5) : 1 / 30;
    lastSubmitMs = frame.capturedAtMs;
    if (model) {
      const measured = updateFrame({
        scene, face, model, fit, smoother, state, source, detection, dt,
        smoothing: true, adaptToFace: true, temples: null,
      });
      if (measured.placement) {
        const pt = screenPointOf(measured.placement.position, scene.head.matrixWorld,
          scene.camera, source.width, source.height);
        const s = stab.update(pt.x, pt.y,
          smoother.position.measuredSpeed, smoother.rotation.measuredRate, nowMs / 1000);
        dom.meter.innerHTML = `stab (trailing 5 s, still-gated)<br>`
          + `<b>${s.rmsPx === null ? '—' : s.rmsPx.toFixed(2)} px</b> rms · `
          + `${s.maxStepPx === null ? '—' : s.maxStepPx.toFixed(2)} px worst step · `
          + `still ${(s.stillFrac * 100).toFixed(0)}%`;
      }
    }
    return true;
  };

  const finish = async () => {
    if (recordingDone) return;
    recordingDone = true;
    const date = new Date().toISOString().slice(0, 10);
    const header = {
      v: 1,
      subject: params.get('subject') ?? (SAMPLE_MODE ? 'sample-face-a' : 'live'),
      date,
      width: source.width,
      height: source.height,
      fovDeg: scene.camera.fov,
      note: SAMPLE_MODE
        ? 'no-camera self-test: sample face, synthetic 30 fps clock, 2 s segments'
        : `live capture, ${navigator.userAgent}`,
      segments,
    };
    const text = `${JSON.stringify(header)}\n${lines.join('\n')}\n`;

    dom.stage.className = '';
    dom.instruction.textContent = 'Done — thank you.';
    dom.countdown.textContent = '';

    const links = [];
    const offer = (blob, name) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.textContent = `save ${name} (${(blob.size / 1e6).toFixed(1)} MB)`;
      links.push(a);
      return a;
    };
    const raw = offer(new Blob([text], { type: 'application/x-ndjson' }),
      `telemetry-${date}.ndjson`);
    let gzBytes = null;
    if (typeof CompressionStream === 'function') {
      const stream = new Blob([text]).stream()
        .pipeThrough(new CompressionStream('gzip'));
      const gz = await new Response(stream).blob();
      gzBytes = gz.size;
      offer(gz, `telemetry-${date}.ndjson.gz`);
    }
    dom.done.replaceChildren(
      Object.assign(document.createElement('div'),
        { textContent: `${framesRecorded} frames recorded (${misses} faceless)` }),
      ...links,
    );
    dom.done.classList.add('show');
    if (!SAMPLE_MODE) raw.click(); // the real capture saves itself; links remain as backup

    if (SAMPLE_MODE) {
      await putFixture('selftest', text);
      say('fixture stored as idb:selftest');
    }

    window.__telemetry = {
      mode: SAMPLE_MODE ? 'sample' : 'live',
      header,
      frames: framesRecorded,
      misses,
      bytes: text.length,
      gzBytes,
      perSegmentFrames,
      stab: { ...stab.readout },
    };
    window.__done = true;
  };

  say('');
  if (SAMPLE_MODE) {
    // The no-rAF loop, pipeline-check style: a synthetic ~30 fps camera clock,
    // yielding to the event loop so the page stays inspectable. 34 ms, not
    // 1000/30: the sample source's nextFrame gate is `< 1000/30`, and a clock
    // stepping exactly at the gate lands on it to within a float ulp — half
    // the frames then flip-flop refused and the self-test records at an
    // alternating 15/30 cadence for no reason a fixture consumer could see.
    let clock = 1;
    let iter = 0;
    while (!recordingDone) {
      clock += 34;
      source.update(clock);
      const frame = source.nextFrame(clock);
      if (frame && !processFrame(frame, clock)) { await finish(); break; }
      if (++iter % 30 === 0) await new Promise((r) => setTimeout(r));
    }
  } else {
    const tick = async (nowMs) => {
      if (recordingDone) return;
      source.update(nowMs);
      const frame = source.ready ? source.nextFrame(nowMs) : null;
      if (frame && !processFrame(frame, nowMs)) { await finish(); return; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
}

run().catch((error) => {
  console.error(error);
  say(`FAILED: ${error.message}`);
  document.getElementById('instruction').textContent = `FAILED — ${error.message}`;
  window.__telemetry = { error: String(error?.stack ?? error) };
  window.__done = true;
});
