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
const { assessFit } = await imp('fit/score');

console.log('\n--- every catalogue frame, seated on this face ---');
console.log('asset                  earSrc     descent  padDepth   panto  padLoad  conv  score');
for (const e of CATALOGUE) {
  const built = frameFromMesh(readGlb(new Uint8Array(readFileSync(`${R}/${e.file}`))), e);
  if (!built.ok) { console.log(`${e.id.padEnd(22)} REFUSED  ${built.reason.slice(0, 60)}`); continue; }
  const a = built.asset;
  const s = solveSeat(m, mesh, regions, a);
  const fit = assessFit(m, mesh, regions, a, s);
  console.log(
    `${e.id.padEnd(22)} ${a.earRestSource.padEnd(9)} ` +
    `${s.descentMm.toFixed(2).padStart(7)} ${s.padDepthErrorMm.toFixed(2).padStart(9)} ` +
    `${s.pantoscopicDeg.toFixed(1).padStart(6)} ${(s.padLoadFraction * 100).toFixed(0).padStart(6)}% ` +
    `${String(s.converged).padStart(6)} ${String(fit.score).padStart(6)}`);
}
