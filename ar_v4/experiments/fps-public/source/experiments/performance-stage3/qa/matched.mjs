import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {chromium} from '@playwright/test';
import {observeGlErrors} from './gl-errors.mjs';
import {verifyPreservation} from './preservation.mjs';
import {GPU_CONTRACT_READY, expectedMechanismFor} from './gpu-contract.mjs';

const args = process.argv.slice(2);
const option = (name, fallback) => args.find(arg => arg.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8076').replace(/\/$/, ''), phase = option('phase', 'all');
assert.equal(new URL(base).hostname, '127.0.0.1', 'QA attaches only to an explicit local server.');
const currentModule = option('current', '/experiments/performance-stage2/renderer.ts');
const candidateModule = option('candidate', '/experiments/performance-stage3/renderer.ts');
const selectedSource = option('source', null);
const observeGl = args.includes('--gl-errors');
const nativeCaptureProbe = args.includes('--native-capture-probe');
const preservationBefore = await verifyPreservation();
if (nativeCaptureProbe) assert.ok(selectedSource && phase === 'recorded24', 'Capture probes are bounded to one exact recorded source.');
const warmup = Number(option('warmup', '0')), samples = Number(option('samples', '0'));
const diagnosticCase = option('diagnostic-case', null), repetitions = Number(option('repetitions', '12'));
assert.ok(Number.isSafeInteger(warmup) && warmup >= 0 && Number.isSafeInteger(samples) && samples >= 0);
assert.ok(Number.isSafeInteger(repetitions) && repetitions > 0);
if (diagnosticCase) assert.notEqual(phase, 'all', 'A bounded diagnostic must identify its one phase.');
const workspace = process.cwd(), sha = bytes => createHash('sha256').update(bytes).digest('hex');
const specs = [
  {id: 'generated32', generated: true, filename: '.recovery/hair-angle-review-2026-09-09/runs/2026-09-09T08-18-33.789Z/report.json', count: 32,
    browserArgs: ['--enable-gpu', '--use-angle=d3d11']},
  {id: 'recorded24', generated: false, filename: '.recovery/hair-live-performance-2026-09-08/recorded-runs/2026-09-08T19-30-46.916Z/report.json', count: 24,
    browserArgs: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']},
].filter(spec => phase === 'all' || phase === spec.id);
assert.ok(specs.length, 'Use --phase=all, --phase=generated32 or --phase=recorded24.');
const frozen = new Map(), runtime = new Map(), resources = new Map();
const read = async artifact => {
  assert.ok(artifact?.path && /^[0-9a-f]{64}$/.test(artifact.sha256), 'Incomplete input receipt.');
  const filename = path.resolve(workspace, artifact.path), relative = path.relative(path.join(workspace, '.recovery'), filename);
  assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'Private QA artifact must stay inside the preserved recovery archive.');
  const bytes = await fs.readFile(filename);
  assert.equal(sha(bytes), artifact.sha256, `Frozen artifact differs: ${filename}`);
  if (artifact.bytes !== undefined) assert.equal(bytes.length, artifact.bytes, `Frozen artifact size differs: ${filename}`);
  frozen.set(filename, artifact.sha256); resources.set(artifact.sha256, bytes);
  return {...artifact, url: `${base}/qa-performance-artifact/${artifact.sha256}`};
};
const verifyRecording = async artifact => {
  const filename = path.resolve(workspace, artifact.path);
  assert.ok(['.recovery', 'recordings'].some(root => {
    const relative = path.relative(path.join(workspace, root), filename);
    return relative && !relative.startsWith('..') && !path.isAbsolute(relative);
  }), 'Original recording receipt must stay inside its preservation directory.');
  const bytes = await fs.readFile(filename); assert.equal(sha(bytes), artifact.sha256, `Original recording differs: ${filename}`);
  frozen.set(filename, artifact.sha256);
};
const plans = [];
for (const spec of specs) {
  const filename = path.resolve(spec.filename), bytes = await fs.readFile(filename), prior = JSON.parse(bytes);
  assert.equal(prior.complete, true); assert.equal(prior.cases.length, spec.count);
  frozen.set(filename, sha(bytes));
  const sources = [], masks = [], cases = [];
  for (const source of prior.images.filter(source => !selectedSource || source.id === selectedSource)) {
    // Verify original encoded recordings and retained metadata without making new copies.
    for (const key of ['original', 'canonicalDetection', 'originalMetadata', 'selectionMetadata', 'decodedSource']) if (source[key]) await read(source[key]);
    if (source.originalCapture) await verifyRecording(source.originalCapture);
    sources.push({id: source.id, width: source.width, height: source.height,
      title: source.title, attributes: source.attributes ?? null, quality: source.quality,
      sourceSHA256: source.sourceSHA256, detectionSHA256: source.detectionSHA256,
      decodedRgbaSHA256: spec.generated ? source.sourceSHA256 : source.decodedRgbaSHA256,
      source: await read(spec.generated ? source.capture : source.original), detectionFile: await read(source.detectionFile),
      serializedDetection: spec.generated ? await read(source.serializedDetection) : null});
  }
  for (const mask of prior.masks.filter(mask => !selectedSource || mask.id === selectedSource)) masks.push({...mask, categoryU8: await read(mask.categoryU8)});
  for (const row of prior.cases.filter(row => !selectedSource || row.id === selectedSource)) {
    const geometry = spec.generated ? JSON.parse(await fs.readFile((await read(row.geometry)).path)) : row.geometry;
    const expectedGeometry = spec.generated ? geometry.current : geometry;
    cases.push({id: row.id, eyewearModel: row.eyewearModel, hairModel: row.hairModel,
      before: await read(spec.generated ? row.acceptedBefore : row.before),
      after: await read(spec.generated ? row.occludedAfter : row.after), background: await read(row.cleanBackground),
      expectedGeometry, expectedMechanism: expectedMechanismFor(expectedGeometry), noseRoi: spec.generated ? geometry.noseRoi : geometry.hairPreview.noseRoi,
      historicalNoseChecks: row.historicalNoseChecks ?? [], expectedModel: (prior.hairModels ?? prior.models).find(model => model.id === row.hairModel),
      expectedBeforeRgbaSHA256: row.beforeRgbaSHA256, expectedAfterRgbaSHA256: row.afterRgbaSHA256,
      warmup: spec.generated ? warmup : 0, samples: spec.generated ? samples : 0});
  }
  assert.deepEqual([...new Set(cases.map(row => row.eyewearModel))].sort(), ['amber-horizon', 'tom-ford-clear']);
  assert.deepEqual([...new Set(cases.map(row => row.hairModel))].sort(), ['hair-only', 'selfie-multiclass']);
  plans.push({...spec, count: cases.length, archiveCount: spec.count, priorReport: {path: filename, sha256: sha(bytes), bytes: bytes.length}, sources, masks, cases});
}

// Preserve only dependencies relevant to the two implementations, not unrelated live/model-studio work.
async function scan(directory) {
  for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
    if (['output', 'node_modules', '.recovery', 'dist', '.git', 'logs', 'test-results'].includes(entry.name)) continue;
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) await scan(filename);
    else if (/\.(ts|mjs|html|css|json|glb|tflite|task|wasm|js|glsl|vert|frag|wgsl)$/.test(entry.name)) runtime.set(filename, sha(await fs.readFile(filename)));
  }
}
for (const directory of ['references', 'experiments/hair-live-preview', 'experiments/hair-arm-preview',
  'experiments/performance-candidate/native', 'experiments/performance-candidate/temples', 'experiments/performance-candidate/qa', 'public/models', 'public/mediapipe'])
  await scan(path.join(workspace, directory));
for (const file of ['package.json', 'package-lock.json', 'vite.hair.config.ts', 'node_modules/three/build/three.module.js', 'node_modules/three/build/three.core.js',
  'experiments/performance-candidate/renderer.ts', 'experiments/performance-candidate/fast-compose.ts', 'experiments/performance-candidate/background.ts',
  'experiments/performance-candidate/vite.config.ts']) {
  const filename = path.join(workspace, file); runtime.set(filename, sha(await fs.readFile(filename)));
}
for (const directory of ['native', 'temples', 'qa']) await scan(path.join(workspace, 'experiments/performance-stage2', directory));
for (const file of ['renderer.ts', 'background.ts', 'vite.config.ts']) {
  const filename = path.join(workspace, 'experiments/performance-stage2', file); runtime.set(filename, sha(await fs.readFile(filename)));
}
// The previous Stage2 files are wholly immutable. New renderer/GPU directories
// are frozen; the independently developed Stage3 UI is not a replay dependency.
await scan(path.join(workspace, 'experiments/performance-stage2'));
const uiFiles = new Set(['comparison-renderer.ts', 'comparison.spec.ts', 'controls.spec.ts', 'frame-profiler.ts',
  'frame-profiler.test.ts', 'live-main.ts', 'live.css', 'live.html', 'playwright.config.ts']);
const stage3 = path.join(workspace, 'experiments/performance-stage3');
for (const entry of await fs.readdir(stage3, {withFileTypes: true})) {
  if (['output', 'dist', 'logs', 'test-results'].includes(entry.name) || uiFiles.has(entry.name)) continue;
  const filename = path.join(stage3, entry.name);
  if (entry.isDirectory()) await scan(filename);
  else if (/\.(ts|mjs|js|json|glsl|vert|frag|wgsl)$/.test(entry.name)) runtime.set(filename, sha(await fs.readFile(filename)));
}
if (args.includes('--preflight')) {
  const pendingRuntimeFiles = [];
  for (const required of ['renderer.ts', 'vite.config.ts']) try {await fs.access(path.join(stage3, required));} catch {pendingRuntimeFiles.push(required);}
  console.log(JSON.stringify({preflight: true, preservationBefore, pendingRuntimeFiles, gpuMechanismContractReady: GPU_CONTRACT_READY, frozenFilesVerified: frozen.size, runtimeFilesVerified: runtime.size, implementationLabels: {current: 'Test2', candidate: 'Test3'}, phases: plans.map(plan => ({id: plan.id, images: plan.sources.length, masks: plan.masks.length, cases: plan.cases.length,
    gpuExpected: plan.cases.filter(row => row.expectedMechanism === 'gpu-zero-drop').length,
    explicitCpuFallbackExpected: plan.cases.filter(row => row.expectedMechanism === 'cpu-nonzero-drop').length}))}));
  process.exit(0);
}
assert.ok(GPU_CONTRACT_READY, 'Bind the finalized GPU-used/fallback diagnostics contract before launching Stage3 QA.');
const out = path.join(workspace, 'experiments/performance-stage3/qa/output', `matched-${new Date().toISOString().replaceAll(':', '-')}`);
await fs.mkdir(out, {recursive: true});
const report = {schema: diagnosticCase ? 'ar-performance-stage3-bounded-repeat-v1' : 'ar-performance-stage3-matched-v1', complete: false, passed: false, createdAt: new Date().toISOString(),
  base, currentModule, candidateModule, implementationLabels: {current: 'Test2', candidate: 'Test3'}, out, phases: [], runtimeHashes: Object.fromEntries(runtime),
  preservationBefore,
  frozenInputs: [...frozen].map(([filename, sha256]) => ({path: filename, sha256})), errors: [],
  scope: {selectedSource, partialArchiveSelection: Boolean(selectedSource), singlePresentationPerPair: warmup === 0 && samples === 0, reusedSessionsPerEyewearAndPhase: true},
  diagnosticGlAttribution: observeGl,
  diagnosticNativeCaptureProbe: nativeCaptureProbe,
  timingProtocol: {warmupPerGeneratedPair: warmup, samplesPerGeneratedPair: samples, order: 'alternating current/candidate for each repetition',
    measured: 'Synchronous native present wall time, including each implementation\'s owned rendering/readback/composition. Diagnostics and PNG encoding are outside timed sections.',
    recorded: 'No timing samples: SwiftShader is used only for exact archived JPEG/default-canvas and rendering correctness.'},
  evidence: 'Two actual renderer implementations receive identical frozen images, original detections/poses and previously computed category masks; output RGBA, geometry, ownership and safeguards are compared with each other and the visually accepted archived evidence.',
  developmentHmr: 'The disposable QA page replaces only /@vite/client with inert HMR hooks, preserving module imports and isolating native replay from unrelated live-UI edits. Normal application pages are unaffected.',
  limits: ['No face or hair inference is rerun. Renderer wall times do not establish live FPS, face/hair contention, sustained mobile speed or physical-camera latency.',
    'Generated portraits and selected recorded stills are separate evidence; no new long-hair wearer-motion or anatomical ground truth.',
    'The production live scheduler and four-way switching are checked separately.',
    'Prior warmed protected-pixel variance, rejected atlas attempts and interrupted synthetic profiling evidence remain preserved; this later Test2/Test3 comparison does not erase them.']};
if (nativeCaptureProbe) report.limits.push('QA injects extra default/captured framebuffer pixel reads outside runtime counters. This diagnostic run cannot establish ordinary readback counts or performance and can perturb synchronization. No Three state API is called by the probe.');
const save = () => fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
const stats = values => {
  if (!values.length) return null; const sorted = [...values].sort((a, b) => a - b), n = sorted.length;
  return {n, median: n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2,
    p95: sorted[Math.ceil(n * .95) - 1], minimum: sorted[0], maximum: sorted[n - 1]};
};
let browser, page;
await save();
try {
  for (const plan of plans) {
    const measured = {id: plan.id, priorReport: plan.priorReport, environment: null, sources: [], moduleUrls: [], cases: [], controls: [], errors: [], consoleErrors: []};
    report.phases.push(measured); await save();
    browser = await chromium.launch({headless: true, args: plan.browserArgs});
    const context = await browser.newContext();
    // Chromium 153 gates the dev server's localhost HMR WebSocket separately.
    // Scope the permission to this disposable QA context and this local origin.
    await context.grantPermissions(['local-network-access'], {origin: base});
    page = await context.newPage();
    if (observeGl) await page.addInitScript(observeGlErrors);
    page.on('pageerror', error => measured.errors.push(error.stack ?? error.message));
    page.on('console', message => { if (message.type() === 'error') measured.consoleErrors.push(message.text()); });
    page.on('request', request => {
      const url = request.url(); if (/\.(ts|mjs|js)(\?|$)/.test(url) && !measured.moduleUrls.includes(url)) measured.moduleUrls.push(url);
    });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) return route.abort();
      if (url.pathname === '/qa-performance-pairs') return route.fulfill({contentType: 'text/html', body: '<!doctype html><title>Isolated exact-pair performance candidate QA</title>'});
      if (url.pathname === '/favicon.ico') return route.fulfill({status: 204, body: ''});
      if (url.pathname === '/@vite/client') return route.fulfill({contentType: 'text/javascript', body: `
        const data = new Map(), styles = new Map();
        export function createHotContext(id) { if (!data.has(id)) data.set(id, {});
          return {data: data.get(id), accept() {}, acceptExports() {}, dispose() {}, prune() {}, invalidate() {}, on() {}, off() {}, send() {}}; }
        export function injectQuery(url, query) { return url + (url.includes('?') ? '&' : '?') + query; }
        export function updateStyle(id, content) { let style = styles.get(id); if (!style) { style = document.createElement('style'); styles.set(id, style); document.head.append(style); } style.textContent = content; }
        export function removeStyle(id) { styles.get(id)?.remove(); styles.delete(id); }
        export class ErrorOverlay extends HTMLElement {}
      `});
      if (url.pathname.startsWith('/qa-performance-artifact/')) {
        const bytes = resources.get(url.pathname.split('/').at(-1));
        return route.fulfill({status: bytes ? 200 : 404, contentType: 'application/octet-stream', body: bytes ?? 'Unknown frozen input'});
      }
      return route.continue();
    });
    await page.goto(`${base}/qa-performance-pairs`);
    measured.environment = await page.evaluate(async parameters => {
      const {createMatchedStudy} = await import('/experiments/performance-stage3/qa/browser.mjs');
      window.matchedStudy = await createMatchedStudy(parameters);
      if (parameters.nativeCaptureProbe) {
        const {installNativeCaptureProbe} = await import('/experiments/performance-stage3/qa/native-capture-probe.mjs');
        const loaded = performance.getEntriesByType('resource').filter(entry => new URL(entry.name).pathname === '/experiments/performance-stage3/native/gpu-frame.ts').at(-1)?.name;
        if (!loaded) throw new Error('The actual native GPU module URL was not observed.');
        const {NativeGpuFrames} = await import(loaded); installNativeCaptureProbe(NativeGpuFrames);
        window.stage3NativeCaptureProbeModule = loaded;
      }
      const gl = document.createElement('canvas').getContext('webgl2');
      if (!gl) throw new Error('No WebGL2 renderer available.');
      const extension = gl.getExtension('WEBGL_debug_renderer_info');
      const environment = {userAgent: navigator.userAgent, renderer: extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : null};
      gl.getExtension('WEBGL_lose_context')?.loseContext(); return environment;
    }, {currentModule, candidateModule, generated: plan.generated, nativeCaptureProbe});
    assert.match(measured.environment.renderer, plan.generated ? /Intel.*D3D11/ : /SwiftShader/, 'QA backend differs from the archived evidence.');
    for (const source of plan.sources) measured.sources.push(await page.evaluate(input => window.matchedStudy.addSource(input), source));
    for (const mask of plan.masks) await page.evaluate(input => window.matchedStudy.addMask(input), mask);
    if (diagnosticCase) {
      const input = plan.cases.find(row => `${row.id}/${row.eyewearModel}/${row.hairModel}` === diagnosticCase);
      assert.ok(input, 'The exact diagnostic case is absent.');
      await page.evaluate(id => window.matchedStudy.setModel(id), input.eyewearModel);
      measured.diagnostic = await page.evaluate(input => window.matchedStudy.repeatDiagnostic(input), {...input, repetitions});
      for (const [name, dataURL] of Object.entries(measured.diagnostic.pngs)) {
        const bytes = Buffer.from(dataURL.split(',')[1], 'base64'), filename = path.join(out, `${name}.png`);
        await fs.writeFile(filename, bytes, {flag: 'wx'}); measured.diagnostic.pngs[name] = {path: filename, sha256: sha(bytes), bytes: bytes.length};
      }
      await page.evaluate(() => window.matchedStudy.dispose()); await browser.close(); browser = page = null;
      assert.deepEqual(measured.errors, []); assert.deepEqual(measured.consoleErrors, []);
      measured.diagnostic.everySampleExact = measured.diagnostic.samples.every(row => row.matchedAccepted.changedPixels === 0 && row.matchedHair.changedPixels === 0
        && Object.values(row.implementations).every(value => value.frozenAccepted.changedPixels === 0 && value.frozenHair.changedPixels === 0));
      await save(); continue;
    }
    for (const eyewear of ['tom-ford-clear', 'amber-horizon']) {
      await page.evaluate(id => window.matchedStudy.setModel(id), eyewear);
      const modelCases = plan.cases.filter(row => row.eyewearModel === eyewear);
      // First recorded original-55 has exactly zero lateral/frontal visibility and
      // zero rear drop. Exercise it before the candidate has any head-mask target.
      if (!plan.generated) modelCases.sort((a, b) => Number(b.id === 'original-55') - Number(a.id === 'original-55'));
      for (const input of modelCases) {
        const row = await page.evaluate(input => window.matchedStudy.render(input), input);
        const directory = path.join(out, plan.id, row.id, eyewear, row.hairModel); await fs.mkdir(directory, {recursive: true});
        for (const [name, dataURL] of Object.entries(row.pngs)) {
          assert.ok(dataURL.startsWith('data:image/png;base64,'));
          const bytes = Buffer.from(dataURL.split(',')[1], 'base64'), filename = path.join(directory, `${name}.png`);
          await fs.writeFile(filename, bytes, {flag: 'wx'}); row.pngs[name] = {path: filename, sha256: sha(bytes), bytes: bytes.length};
        }
        measured.cases.push(row);
        if (nativeCaptureProbe) {
          measured.nativeCaptureProbe = await page.evaluate(() => window.stage3NativeCaptureProbe);
          measured.nativeCaptureProbeModule = await page.evaluate(() => window.stage3NativeCaptureProbeModule);
          assert.ok(measured.nativeCaptureProbe.length > 0, 'The QA capture probe did not attach to the actual native GPU class.');
        }
        await save();
        assert.equal(row.passed, true, `${plan.id}/${row.id}/${eyewear}/${row.hairModel}: matched rendering, geometry or safeguards differ.`);
        for (const name of ['current', 'candidate']) {
          assert.equal(row[name].beforeRgbaSHA256, input.expectedBeforeRgbaSHA256, `${name}: accepted native RGBA differs.`);
          assert.equal(row[name].afterRgbaSHA256, input.expectedAfterRgbaSHA256, `${name}: hair native RGBA differs.`);
        }
        console.log(JSON.stringify({phase: plan.id, id: row.id, eyewear, hair: row.hairModel, exact: true,
          currentMedianMs: stats(row.samples.current.map(value => value.presentMs))?.median ?? null,
          candidateMedianMs: stats(row.samples.candidate.map(value => value.presentMs))?.median ?? null}));
      }
      const transitionBack = await page.evaluate(input => window.matchedStudy.render(input), {...modelCases[0], warmup: 0, samples: 0});
      delete transitionBack.pngs;
      measured.controls.push({eyewear, returnToFirstPairAfterOtherPoses: transitionBack}); await save();
      assert.equal(transitionBack.passed, true, 'Returning to the first pair after different poses changed its reviewed rendering.');
      for (const hairModel of ['hair-only', 'selfie-multiclass']) {
        const input = modelCases.find(row => row.hairModel === hairModel);
        const controls = await page.evaluate(input => window.matchedStudy.controls(input), input);
        measured.controls.push({eyewear, hairModel, ...controls}); await save(); assert.equal(controls.passed, true, 'Renderer lifecycle controls failed.');
      }
      const cancellation = await page.evaluate(input => window.matchedStudy.cancellation(input), modelCases[0]);
      measured.controls.push({eyewear, cancellation}); await save(); assert.equal(cancellation.passed, true, 'Renderer cancellation failed.');
    }
    await page.evaluate(() => window.matchedStudy.dispose()); await browser.close(); browser = page = null;
    assert.equal(measured.cases.length, plan.count); assert.deepEqual(measured.errors, []); assert.deepEqual(measured.consoleErrors, []);
    for (const required of ['/experiments/performance-stage2/renderer.ts', '/experiments/performance-stage3/renderer.ts',
      '/experiments/performance-stage2/native/shared-readback.ts', '/references/perfect-temples/src/render/eyewear.ts'])
      assert.ok(measured.moduleUrls.some(url => new URL(url).pathname === required), `Required actual renderer dependency was not loaded: ${required}`);
    for (const url of measured.moduleUrls.filter(url => new URL(url).pathname.startsWith('/experiments/performance-stage3/')))
      assert.ok(runtime.has(path.join(workspace, new URL(url).pathname.slice(1))), `Loaded Stage3 replay dependency was not hash-frozen: ${url}`);
    measured.timingSummary = {};
    for (const eyewear of ['tom-ford-clear', 'amber-horizon']) for (const hairModel of ['hair-only', 'selfie-multiclass']) {
      const group = measured.cases.filter(row => row.eyewearModel === eyewear && row.hairModel === hairModel), summary = {};
      for (const name of ['current', 'candidate']) {
        const values = group.flatMap(row => row.samples[name]);
        summary[name] = {presentMs: stats(values.map(row => row.presentMs)), stages: {}};
        for (const field of Object.keys(values[0]?.timings ?? {})) summary[name].stages[field] = stats(values.map(row => row.timings[field]).filter(Number.isFinite));
      }
      measured.timingSummary[`${eyewear}/${hairModel}`] = summary;
    }
    await save();
  }
  for (const [filename, digest] of [...frozen, ...runtime]) assert.equal(sha(await fs.readFile(filename)), digest, `Input/runtime changed while QA ran: ${filename}`);
  report.frozenInputs = [...frozen].map(([filename, sha256]) => ({path: filename, sha256}));
  report.complete = true;
  report.preservationAfter = await verifyPreservation();
  report.passed = diagnosticCase ? report.phases.every(item => item.diagnostic.everySampleExact) : true; await save();
} catch (error) {
  report.errors.push(error.stack ?? String(error));
  if (nativeCaptureProbe && page && !page.isClosed()) {
    try {report.nativeCaptureProbe = await page.evaluate(() => window.stage3NativeCaptureProbe);}
    catch (captureError) {report.errors.push(`Native capture probe unavailable: ${captureError}`);}
  }
  if (observeGl && page && !page.isClosed()) {
    try { report.glErrors = await page.evaluate(() => window.stage2GlErrors ?? []); }
    catch (captureError) { report.errors.push(`GL attribution unavailable: ${captureError}`); }
  }
  await save(); throw error;
}
finally {
  if (page && !page.isClosed()) { try { await page.evaluate(() => window.matchedStudy?.dispose()); } catch {} }
  await browser?.close();
  console.log(JSON.stringify({report: path.join(out, 'report.json'), passed: report.passed, phases: report.phases.map(item => ({id: item.id, cases: item.cases.length}))}));
}
