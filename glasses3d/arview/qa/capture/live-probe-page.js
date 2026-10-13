// arview capture --live-probe: the page module served VIRTUALLY on main's unmodified ar/ (qa/capture/live-probe.mjs
// serves it as ar/qa/__arview_live_probe.js; nothing is ever written into ar/). It loads the GLB the way main's page
// does: the ?model= handover (src/eyewear/external.ts parseExternalModel + installExternalModel), then
// LiveRenderer.create with main's default config (src/main.ts:263), then one synthetic frontal frame (pose + render), and
// returns the exact text main's page would show: the handover note, or the stage message for a failure
// (main.ts closeSession(messageFor(error))).
import * as THREE from 'three';
import {parseConfig} from '../src/config.ts';
import {describeExternalModel, installExternalModel, parseExternalModel} from '../src/eyewear/external.ts';
import {MODELING_AUTO_EYEWEAR_ID} from '../src/eyewear/catalog.ts';
import {LiveRenderer} from '../src/render/live-renderer.ts';

const messageFor = error => error instanceof Error ? error.message : String(error);

// The pre-WP0 prototype's detection() (ar_capture/page.js; deleted 2026-10-13, see git tag review-20261013-pre-cleanup):
// the canonical face posed frontally at 23 cm.
function detection(canonical, width, height, depth = 23) {
  const matrix = new THREE.Matrix4().compose(new THREE.Vector3(0, 0, -depth), new THREE.Quaternion(), new THREE.Vector3(1, 1, 1));
  const positions = [], point = new THREE.Vector3(); let mean = 0;
  for (let i = 0; i < 468; i++) {point.fromArray(canonical.positions, i * 3).applyMatrix4(matrix); positions.push(point.clone()); mean -= point.z / 468;}
  const vertical = 2 * Math.tan(63 * Math.PI / 360), horizontal = vertical * width / height;
  const landmarks = positions.map(p => ({x: .5 + p.x / (-p.z * horizontal), y: .5 - p.y / (-p.z * vertical), z: (-p.z / mean - 1) / horizontal}));
  for (const [a, b] of [[33, 133], [362, 263]]) for (let i = 0; i < 5; i++) landmarks.push({x: (landmarks[a].x + landmarks[b].x) / 2, y: (landmarks[a].y + landmarks[b].y) / 2, z: (landmarks[a].z + landmarks[b].z) / 2});
  return {matrix: matrix.toArray(), landmarks, inferenceMs: 0};
}

async function run(search) {
  const out = {search, runtime: 'main ar/ (unmodified)', steps: []};
  const config = parseConfig('');
  let external;
  try {external = parseExternalModel(search, location.origin);}
  catch (error) {
    out.verdict = 'handover_refused';
    out.text = `The model in this address was not loaded: ${messageFor(error)} The shipped frames remain available.`;
    return out;
  }
  if (!external) {out.verdict = 'handover_refused'; out.text = 'The address carries no model.'; return out;}
  installExternalModel(external);
  out.handover = {name: external.name, clip_m: external.clipZM, width_mm: external.widthMm, sha256: external.sha256, lensenv: external.lensEnvIntensity};
  out.note = describeExternalModel(external);
  const canvas = document.createElement('canvas'), controller = new AbortController();
  let renderer;
  try {
    renderer = await LiveRenderer.create(canvas, controller.signal, MODELING_AUTO_EYEWEAR_ID,
      {hairStartZ: config.hairStartZ, sync: config.sync, guard: config.guard, steady: config.steady});
    out.steps.push({step: 'create', ok: true});
  } catch (error) {
    out.verdict = 'refused'; out.text = messageFor(error); out.steps.push({step: 'create', ok: false, text: out.text});
    return out;
  }
  try {
    out.gpu = renderer.gpuRenderer; out.endpoint_unavailable = renderer.endpointUnavailable ?? null;
    // One frontal frame through the same TryOnRenderer the live pipeline wraps (the live pipeline's prepare/finish need a
    // camera; pose + render is what they call).
    const tryOn = renderer.renderer;
    const canonical = await fetch('/models/canonical-face.json').then(response => response.json());
    const frame = document.createElement('canvas'); frame.width = 1280; frame.height = 854;
    const context = frame.getContext('2d'); context.fillStyle = '#cba68d'; context.fillRect(0, 0, frame.width, frame.height);
    const face = detection(canonical, frame.width, frame.height);
    let tracked = 0;
    for (let t = 0; t <= 5000; t += 100) if (tryOn.pose(frame, face, t)) tracked++;
    const timing = tryOn.render(null, {hair: false});
    out.steps.push({step: 'first_frame', ok: true, tracked_poses: tracked, guarded: timing.guarded, safe_fallback: timing.safeFallback,
      fit_ready: tryOn.fitting?.ready ?? null});
    out.verdict = 'loaded';
    out.text = out.note + (out.endpoint_unavailable ? ` Temple continuity: ${out.endpoint_unavailable}` : '');
  } catch (error) {
    out.verdict = 'failed_first_frame'; out.text = messageFor(error); out.steps.push({step: 'first_frame', ok: false, text: out.text});
  } finally {renderer.dispose(); controller.abort();}
  return out;
}
window.liveProbe = {run};
