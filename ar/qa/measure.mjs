/** Synthetic-camera driver: runs the page's own measurement (fresh session per run, warmup then measurement), saves a
 *  screenshot of the stage, and audits one frame (the CPU reference compose and the four protection checks on the GPU
 *  output; images saved next to the report). Loopback, one machine, the checked-in face-a fixture with slow drift:
 *  controlled-input evidence, not a real camera, wearer motion, phone or thermal evidence.
 *    node qa/measure.mjs --base=http://127.0.0.1:8241 --sessions=1 --warm=10 --measure=30
 *      [--capture=960] [--hairz=-0.02] [--sync=0] [--face=gpu] [--guard=0] [--continuity=0] [--hairrun=10]
 *      [--eyewear=amber-horizon] [--hairModel=hair-only] [--hair=0] [--out=dir] [--no-shot] [--no-audit] [--headed] */
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, '..');
const args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const flag = name => args.includes(`--${name}`);
const base = option('base', 'http://127.0.0.1:8241').replace(/\/$/, '');
const sessions = Math.max(1, Number(option('sessions', '1')) || 1);
const warmMs = Number(option('warm', '10')) * 1000, measureMs = Number(option('measure', '30')) * 1000;
const fixture = await fs.readFile(path.join(here, 'fixtures/face-a.jpg'));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const out = path.resolve(option('out', path.join(here, 'output', `measure-${new Date().toISOString().replaceAll(':', '-')}`)));
await fs.mkdir(out, {recursive: true});
const query = new URLSearchParams();
for (const name of ['face', 'capture', 'hairz', 'sync', 'exposure', 'guard', 'continuity', 'hairrun', 'eyewear', 'hairModel', 'hair']) if (option(name, '')) query.set(name, option(name, ''));
const browser = await chromium.launch({headless: !flag('headed'), channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required']});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
const consoleLines = [];
page.on('console', message => {if (['error', 'warning'].includes(message.type()) && !/THREE\.WebGLProgram|X4122|X3595|xnnpack|gl_context|XNNPACK|Feedback manager/i.test(message.text())) consoleLines.push(`${message.type()}: ${message.text().slice(0, 240)}`);});
page.on('pageerror', error => consoleLines.push(`pageerror: ${error.message.slice(0, 240)}`));
await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
await page.addInitScript(() => {
  const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
  const context = source.getContext('2d'), image = new Image();
  const state = {requests: 0, draws: 0}; window.__syntheticCamera = state;
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;}); image.src = '/ar-camera-fixture';
  const started = performance.now();
  const draw = () => {context.fillStyle = '#163949'; context.fillRect(0, 0, 1280, 720);
    if (image.complete && image.naturalWidth) {const t = (performance.now() - started) / 1000; const s = 1 + Math.sin(t * 2 * Math.PI * 0.07) * 0.03;
      context.drawImage(image, Math.sin(t * 2 * Math.PI * 0.2) * 20 + (1280 - 1280 * s) / 2, Math.cos(t * 2 * Math.PI * 0.13) * 12 + (720 - 720 * s) / 2, 1280 * s, 720 * s);}
    state.draws++;};
  setInterval(draw, 1000 / 30);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {state.requests++; await ready; draw(); return source.captureStream(30);}});
});
await page.goto(`${base}/?${query}`, {waitUntil: 'load'});
await page.waitForFunction(() => !!window.__ar);
if (await page.evaluate(() => window.__syntheticCamera.requests) !== 0) throw new Error('Page opened the camera on load.');
console.log(`sessions ${sessions} · warm ${warmMs / 1000} s · measure ${measureMs / 1000} s · ${query || '(defaults)'}`);
const result = await page.evaluate(({sessions, warmMs, measureMs}) => window.__ar.measure(sessions, warmMs, measureMs), {sessions, warmMs, measureMs});
const ms = v => v === null || v === undefined ? '—' : Math.round(v);
const cameraFps = rows => {const counted = rows.filter(r => Number.isFinite(r.videoPresentedFrames)); const first = counted[0], last = counted.at(-1);
  return first && last && last !== first && last.capturedAtMs > first.capturedAtMs ? ((last.videoPresentedFrames - first.videoPresentedFrames) * 1000 / (last.capturedAtMs - first.capturedAtMs)).toFixed(1) : '—';};
for (const s of result.sessions) {
  const m = s.summary;
  console.log(`#${s.index + 1} ${s.status}${s.error ? ' · ' + s.error : ''} · startup ${ms(s.startupMs)} ms · ${m?.processedFps?.toFixed(2) ?? '—'} fps (camera ${cameraFps(s.rows)}) · age med/p95 ${ms(m?.processing?.median)}/${ms(m?.processing?.p95)} ms · interval p95 ${ms(m?.frameInterval?.p95)} · tracked ${m?.trackedFrames ?? 0}/${m?.frames ?? 0} masked ${m?.maskedFrames ?? 0} · size ${s.rows[0]?.sourceWidth ?? '?'}x${s.rows[0]?.sourceHeight ?? '?'} · face ${ms(m?.stages?.faceRequestWallMs?.median)} prepare ${ms(m?.stages?.prepareMs?.median)} (gpu wait ${m?.stages?.gpuWaitMs?.median?.toFixed(1) ?? '—'}, pose ${m?.stages?.poseMs?.median?.toFixed(1) ?? '—'}) finish ${ms(m?.stages?.finishMs?.median)} (submit ${m?.stages?.submitMs?.median?.toFixed(1) ?? '—'}, mask upload ${m?.stages?.maskUploadMs?.median?.toFixed(1) ?? '—'}, continuity ${m?.stages?.continuityMs?.median?.toFixed(1) ?? '—'}) hairWait ${ms(m?.stages?.hairWaitMs?.median)}`);
}
const med = v => v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null;
const done = result.sessions.filter(s => s.summary?.processedFps);
const summary = {sessions: done.length, medianFps: med(done.map(s => s.summary.processedFps).sort((a, b) => a - b)), medianAgeP95: med(done.map(s => s.summary.processing?.p95 ?? 0).sort((a, b) => a - b))};
console.log(`pooled: ${summary.sessions} sessions median ${summary.medianFps?.toFixed(2) ?? '—'} fps age p95 ${ms(summary.medianAgeP95)} ms`);
let shot = null;
if (!flag('no-shot')) {
  await page.evaluate(() => window.__ar.open());
  const ok = await page.waitForFunction(() => {const rows = window.__ar.samplesAfter(0); const last = rows[rows.length - 1]; return !!last && last.hasFace && last.hasMask && rows.filter(r => r.sessionId === last.sessionId).length >= 20;}, null, {timeout: 120_000}).then(() => true).catch(() => false);
  await new Promise(resolve => setTimeout(resolve, 500));
  const file = path.join(out, 'stage.png');
  await page.locator('.stage').screenshot({path: file});
  shot = {file: path.relative(app, file), maskedFrameSeen: ok};
  await page.evaluate(() => window.__ar.close());
  await new Promise(resolve => setTimeout(resolve, 800));
}
// Audit one frame: the CPU reference compose and the four checks on the GPU output; images saved next to the report.
let audit = null;
if (!flag('no-audit')) {
  const rowsBefore = await page.evaluate(() => window.__ar.samplesAfter(0).length);
  await page.evaluate(() => window.__ar.open());
  // Wait for 20 masked rows of the NEW session (rows of the previous session must not satisfy this).
  await page.waitForFunction(before => {const rows = window.__ar.samplesAfter(0).slice(before); const last = rows[rows.length - 1]; return !!last && last.hasFace && last.hasMask && rows.filter(r => r.sessionId === last.sessionId && r.hasMask).length >= 20;}, rowsBefore, {timeout: 120_000}).catch(() => undefined);
  const result = await page.evaluate(() => window.__ar.audit());
  await page.evaluate(() => window.__ar.close());
  if (result) {
    const files = {};
    for (const [name, url] of Object.entries(result.images)) {
      if (!url) continue;
      const file = path.join(out, `audit-${name.replace('PngDataUrl', '')}.png`);
      await fs.writeFile(file, Buffer.from(url.slice(url.indexOf(',') + 1), 'base64')); files[name] = path.relative(app, file);
    }
    audit = {...result, images: files};
    const c = result.checks, ck = (n, v) => `${n} ${v ? v.changedPixels === 0 ? 'pass' : v.changedPixels + ' px' : '—'}`;
    console.log(`audit frame ${result.sequence} ${result.width}x${result.height} · guard ${result.guard.guarded} (${result.guard.protectedRects.length} protected / ${result.guard.editableRects.length} editable rects) · hair ${result.hairApplied} · ${c ? [ck('protected', c.protectedCheck), ck('nose', c.noseCheck), ck('outsideEditable', c.outsideEditableCheck), ck('background', c.backgroundPreservationCheck)].join(' · ') : 'checks unavailable: ' + result.checkError} · GPU edit ${result.afterVsBefore.differentPixels} px (maxΔ ${result.afterVsBefore.maxDelta}) · drop inside protected ${result.dropInsideProtected ? `${result.dropInsideProtected.differentPixels} px (${result.dropInsideProtected.differentPixelsOver8} over 8, maxΔ ${result.dropInsideProtected.maxDelta})` : '—'}${result.afterVsReference ? ` · vs reference compose+continuity ${result.afterVsReference.differentPixels} px differ (maxΔ ${result.afterVsReference.maxDelta}), reference changes ${result.reference.changedPixels} px, reference continuity removes ${result.reference.continuityRemovedPixels ?? '—'} px${result.reference.fallbackReason ? ', reference fallback: ' + result.reference.fallbackReason : ''}` : result.reference.error ? ' · reference error: ' + result.reference.error : ''} · cut ${result.cut.continuity ? `L ${result.cut.negative === null ? 'none' : (result.cut.negative * 1000).toFixed(0) + ' mm'} / R ${result.cut.positive === null ? 'none' : (result.cut.positive * 1000).toFixed(0) + ' mm'}` : 'off'} · ${Math.round(result.timings.totalMs)} ms`);
  } else console.log('audit: unavailable');
}
const report = {schema: 'ar-measure-v1', createdAt: new Date().toISOString(), base, sessions, warmMs, measureMs, query: Object.fromEntries(query), fixtureSHA256: sha(fixture), summary, shot, audit, consoleLines,
  report: await page.evaluate(() => window.__ar.lastReport()),
  limits: ['Loopback synthetic 30 fps still with drift; not a real camera, wearer motion, phone or thermal evidence.', 'The frame age ends at GPU submission; each frame is gated on the previous frame\'s GPU completion.']};
await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
if (consoleLines.length) console.log(`console: ${consoleLines.slice(0, 8).join(' | ')}`);
console.log(`saved ${path.relative(app, path.join(out, 'report.json'))}`);
await browser.close();
