// Measure G and opt-in speed experiments on this machine's real GPU with the synthetic camera
// the promotion receipts used (tests/fixtures/face-a.jpg on a captureStream canvas).
// Usage: node experiments/speed-lab/qa/measure-experiments.mjs <baseURL> <sourceWidth> <sourceHeight> <warmMs> <measureMs> <config>...
//   config = name=query  e.g. G=  cpu=face=cpu  hair640=hair=640  tx=tx=0.5
import {createRequire} from 'node:module';
import fs from 'node:fs';
const require = createRequire(new URL('../../../package.json', import.meta.url));
const {chromium} = require('@playwright/test');
const [baseURL, sw, sh, warmArg, measureArg, ...configArgs] = process.argv.slice(2);
const sourceWidth = Number(sw), sourceHeight = Number(sh), warmMs = Number(warmArg), measureMs = Number(measureArg);
const configs = configArgs.map(a => { const i = a.indexOf('='); return {name: a.slice(0, i), query: a.slice(i + 1)}; });
const fixture = fs.readFileSync(new URL('../../../tests/fixtures/face-a.jpg', import.meta.url));
const dist = v => { const a = v.filter(Number.isFinite).sort((x, y) => x - y); if (!a.length) return null;
  const q = p => a[Math.min(a.length - 1, Math.ceil(a.length * p) - 1)];
  return {n: a.length, med: a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2, p95: q(0.95), max: a[a.length - 1]}; };
const f1 = d => d ? d.med.toFixed(1) : '—';
const N = (s, k) => { const v = s.native?.[k]; return typeof v === 'number' ? v : null; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

const browser = await chromium.launch({args: ['--enable-gpu', '--use-angle=d3d11']});
const results = [];
for (const config of configs) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1080}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 200)); });
  await page.route('**/performance-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(({width, height}) => {
    const source = document.createElement('canvas'); source.width = width; source.height = height;
    const context = source.getContext('2d'), image = new Image();
    const ready = new Promise((resolve, reject) => { image.onload = () => resolve(); image.onerror = reject; });
    image.src = '/performance-camera.jpg';
    const draw = () => { context.fillStyle = '#163949'; context.fillRect(0, 0, width, height);
      if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, width, height); };
    setInterval(draw, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => { await ready; draw(); return source.captureStream(30); }});
  }, {width: sourceWidth, height: sourceHeight});
  // Harness-only directives (not app URL params): model=<hair model id>, eyewear=<eyewear id>.
  const params = new URLSearchParams(config.query);
  const model = params.get('model'), eyewear = params.get('eyewear'); params.delete('model'); params.delete('eyewear');
  const query = params.toString();
  const url = `${baseURL}/experiments/speed-lab/live.html${query ? '?' + query : ''}`;
  const t0 = Date.now();
  await page.goto(url);
  if (model) await page.selectOption('#hair-model-select', model);
  if (eyewear) await page.selectOption('#eyewear-select', eyewear);
  await page.click('#start');
  try {
    await page.waitForSelector('.stage[data-state="tracking"]', {timeout: 60000});
    await page.waitForFunction(() => window.hairLivePreview.diagnostics().hairReady === true, null, {timeout: 60000});
  } catch (e) { console.log(`[${config.name}] startup failed: ${e.message.slice(0, 200)}; errors=${JSON.stringify(errors).slice(0, 500)}`); await context.close(); continue; }
  const startupMs = Date.now() - t0;
  await sleep(warmMs);
  const serial = await page.evaluate(() => { const s = window.arPerformanceProfiler.samplesAfter(0); return s.length ? s[s.length - 1].serial : 0; });
  await sleep(measureMs);
  const rows = await page.evaluate(serial => window.arPerformanceProfiler.samplesAfter(serial), serial);
  const diag = await page.evaluate(() => window.hairLivePreview.diagnostics());
  await page.click('#stop').catch(() => {});
  await context.close();
  const intervals = rows.slice(1).map((s, i) => s.publishedAtMs - rows[i].publishedAtMs);
  const dur = rows.length > 1 ? rows.at(-1).publishedAtMs - rows[0].publishedAtMs : 0;
  const branch = rows.filter(s => N(s, 'nativePipeline.branchReadbackCalls') > 0).length;
  const r = {
    name: config.name, query: config.query, rows: rows.length, fps: dur ? (rows.length - 1) * 1000 / dur : 0,
    interval: dist(intervals), age: dist(rows.map(s => s.totalMs)), gaps100: intervals.filter(x => x > 100).length,
    face: dist(rows.map(s => s.faceInferenceMs)), faceWall: dist(rows.map(s => s.faceRequestWallMs)), faceTransport: dist(rows.map(s => s.faceTransportSchedulingMs)),
    hairInf: dist(rows.map(s => s.hairInferenceMs)), hairExt: dist(rows.map(s => s.hairExtractionMs)), hairWait: dist(rows.map(s => s.hairWaitMs)),
    prepare: dist(rows.map(s => s.prepareMs)), finish: dist(rows.map(s => s.finishMs)), sched: dist(rows.map(s => s.schedulerWaitMs)),
    draw: dist(rows.map(s => s.sourceDrawMs)), hash: dist(rows.map(s => s.sourceHashMs)),
    beauty: dist(rows.map(s => N(s, 'nativePipeline.native.beautySubmitMs'))), pboWait: dist(rows.map(s => N(s, 'nativePipeline.native.speedLab.pbo.waitMs'))),
    pboExt: dist(rows.map(s => N(s, 'nativePipeline.native.speedLab.pbo.extractMs'))), branchPresent: dist(rows.map(s => N(s, 'nativePipeline.branchPresentMs'))),
    branchRead: dist(rows.map(s => N(s, 'nativePipeline.branchReadbackMs'))), branchPct: rows.length ? 100 * branch / rows.length : 0,
    tracked: rows.filter(s => s.hasFace).length, masked: rows.filter(s => s.hasMask).length, edits: rows.filter(s => s.changedPixels > 0).length,
    faceDelegate: rows[0]?.faceDelegate, hairDelegate: rows[0]?.hairDelegate, gpu: rows[0]?.gpuRenderer, size: rows[0] ? `${rows[0].sourceWidth}x${rows[0].sourceHeight}` : '',
    maskSize: rows[0] ? `${N(rows[0], 'experiment.hairMaskWidth')}x${N(rows[0], 'experiment.hairMaskHeight')}` : '', tx: N(rows[0] ?? {}, 'experiment.transmissionResolutionScale'),
    startupMs, errors: errors.filter(e => !/XNNPACK/.test(e)).slice(0, 5), hairError: diag.hairError, backend: diag.backend,
    hairModel: model ?? 'hair-only', eyewear: eyewear ?? 'amber-horizon',
  };
  results.push(r);
  console.log(`[${r.name}] ${r.size} face=${r.faceDelegate} hair=${r.hairDelegate} mask=${r.maskSize} tx=${r.tx} startup=${startupMs}ms gpu=${(r.gpu ?? '').slice(0, 40)}`);
  console.log(`  fps ${r.fps.toFixed(2)}  interval med ${f1(r.interval)} p95 ${r.interval ? r.interval.p95.toFixed(1) : '—'} gaps>100 ${r.gaps100}  age ${f1(r.age)}  rows ${r.rows} tracked ${r.tracked} masked ${r.masked} edits ${r.edits} branch ${r.branchPct.toFixed(0)}%`);
  console.log(`  face inf ${f1(r.face)} wall ${f1(r.faceWall)} transport ${f1(r.faceTransport)} | hair inf ${f1(r.hairInf)} ext ${f1(r.hairExt)} wait ${f1(r.hairWait)} | sched ${f1(r.sched)} draw ${f1(r.draw)} hash ${f1(r.hash)}`);
  console.log(`  prepare ${f1(r.prepare)} = beauty ${f1(r.beauty)} + pboWait ${f1(r.pboWait)} + pboExt ${f1(r.pboExt)} + branch ${f1(r.branchPresent)}/${f1(r.branchRead)} | finish ${f1(r.finish)}${r.errors.length ? ' | errors: ' + JSON.stringify(r.errors) : ''}${r.hairError ? ' | hairError: ' + r.hairError : ''}`);
}
await browser.close();
const out = process.env.MEASURE_OUT; if (out) fs.writeFileSync(out, JSON.stringify(results, null, 2));
