import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {chromium} from '@playwright/test';
import {installSyntheticCamera, warmPipeline, measurePipeline, releaseState} from './profile-browser.mjs';
import {verifyPreservation} from './preservation.mjs';
import {GPU_CONTRACT_READY, verifyFrameMechanisms} from './gpu-contract.mjs';
import {freezeProductionBuild, verifyProductionBuild, verifyServedEntries} from './production-build.mjs';

const args = process.argv.slice(2), option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8076').replace(/\/$/, ''), pagePath = option('page', '/experiments/performance-stage3/live.html');
const buildDirectory = option('build-dir', ['8077', '8078'].includes(new URL(base).port) ? 'experiments/performance-stage3/dist' : null);
assert.equal(new URL(base).hostname, '127.0.0.1');
const eyewears = option('eyewear', 'amber-horizon,tom-ford-clear').split(','), hairModels = option('hair', 'hair-only,selfie-multiclass').split(',');
const pipelines = option('pipelines', 'test2,test3').split(','), width = Number(option('width', '960')), height = Number(option('height', '640'));
const durationMs = Number(option('duration', '30')) * 1000, warmupMs = Number(option('warmup', '5')) * 1000;
const preservationBefore = await verifyPreservation();
assert.ok(durationMs >= 30000 && Number.isFinite(durationMs), 'Measured segments must last at least 30 seconds.');
assert.ok(warmupMs >= 5000 && Number.isFinite(warmupMs), 'Retain at least five seconds of warmup.');
assert.ok(Number.isSafeInteger(width) && width > 0 && Number.isSafeInteger(height) && height > 0);
for (const id of eyewears) assert.ok(['amber-horizon', 'tom-ford-clear'].includes(id));
for (const id of hairModels) assert.ok(['hair-only', 'selfie-multiclass'].includes(id));
for (const id of pipelines) assert.ok(['current', 'test', 'test2', 'test3'].includes(id));
assert.equal(new Set(pipelines).size, pipelines.length);
const sha = bytes => createHash('sha256').update(bytes).digest('hex'), frozen = new Map(), workspace = process.cwd();
// Emscripten emits this precise initialization notice through console.error.
// Retain its original level and CPU-delegate content; all other errors still fail.
const knownInformationalStderr = 'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.';
const sourceReportPath = path.resolve('.recovery/hair-arm-preview-2026-09-08/runs/2026-09-08T17-15-24.119Z/report.json');
const sourceReportBytes = await fs.readFile(sourceReportPath), sourceReport = JSON.parse(sourceReportBytes);
const fixture = sourceReport.images.find(image => image.id === '03-curly-yaw-left').original;
const fixturePath = path.resolve(fixture.path), fixtureBytes = await fs.readFile(fixturePath);
assert.equal(sha(fixtureBytes), fixture.sha256); if (fixture.bytes !== undefined) assert.equal(fixtureBytes.length, fixture.bytes);
frozen.set(sourceReportPath, sha(sourceReportBytes)); frozen.set(fixturePath, fixture.sha256);
async function scan(directory) {
  for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
    if (['output', 'dist', 'node_modules', '.recovery', '.git', 'logs', 'test-results'].includes(entry.name)) continue;
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) await scan(filename);
    else if (/\.(ts|mjs|js|html|css|json|glb|tflite|task|wasm|glsl|vert|frag|wgsl)$/.test(entry.name)) frozen.set(filename, sha(await fs.readFile(filename)));
  }
}
for (const directory of ['references', 'experiments/hair-live-preview', 'experiments/hair-arm-preview', 'experiments/performance-candidate', 'experiments/performance-stage2', 'experiments/performance-stage3', 'public/models', 'public/mediapipe'])
  await scan(path.resolve(directory));
for (const file of ['package.json', 'package-lock.json', 'node_modules/three/build/three.module.js', 'node_modules/three/build/three.core.js']) {
  const filename = path.resolve(file); frozen.set(filename, sha(await fs.readFile(filename)));
}
if (args.includes('--preflight')) {
  console.log(JSON.stringify({preflight: true, preservationBefore, gpuMechanismContractReady: GPU_CONTRACT_READY, frozenFiles: frozen.size, source: fixturePath,
    segments: eyewears.length * hairModels.length * pipelines.length, minimumMeasuredSeconds: durationMs / 1000 * eyewears.length * hairModels.length * pipelines.length}));
  process.exit(0);
}
assert.ok(!pipelines.includes('test3') || GPU_CONTRACT_READY, 'Bind Test3 GPU-used/fallback diagnostics before performance measurement.');
const productionBuild = buildDirectory ? await freezeProductionBuild(buildDirectory) : null;
const servedEntries = productionBuild ? await verifyServedEntries(base, pagePath, productionBuild) : null;
const out = path.resolve('experiments/performance-stage3/qa/output', `sustained-${new Date().toISOString().replaceAll(':', '-')}`); await fs.mkdir(out, {recursive: true});
const report = {schema: 'ar-performance-stage3-sustained-synthetic-v1', complete: false, passed: false, createdAt: new Date().toISOString(), out, base, pagePath,
  preservationBefore,
  productionBuild, servedEntries,
  fixture: {...fixture, path: fixturePath, purpose: 'Previously generated long-haired portrait. The synthetic camera rescales it and changes a 16px corner marker; this is not an archived exact-pair replay.'},
  source: {width, height, requestedFps: 30}, durationMs, warmupMs, minimumPairedWarmupFrames: 3,
  selectedEyewears: eyewears, selectedHairModels: hairModels, selectedPipelines: pipelines,
  timingPolicy: 'Collect every completed publication from the application\'s bounded numeric telemetry ring, draining once per second. Main FPS counts publications within the measured page-clock window; interval cadence is separately reported. Parallel stage durations must not be added.',
  limits: ['Headless synthetic source timers, image conversion, browser scheduling and this hardware affect cadence; no physical-camera/display-scanout or moving-wearer claim.',
    'Frame age starts at application capture, excluding sensor/camera buffering and final display latency. A WebGL GPU identity and requested delegate do not prove every inference operation executes on the GPU.',
    'This is sustained only over the recorded segment duration. Phone smoothness, battery consumption and thermal limits remain unmeasured.',
    'The prior Test1 warmed current-renderer protected-pixel variance remains unresolved; timing success does not establish visual acceptance.'],
  frozenInputs: [...frozen].map(([filename, sha256]) => ({path: filename, sha256})), sessions: [], errors: []};
const save = () => fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
const distribution = values => {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b), n = sorted.length;
  return n ? {n, median: n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2,
    p95: sorted[Math.ceil(n * .95) - 1], maximum: sorted[n - 1]} : null;
};
const summarize = segment => {
  const rows = segment.rows, first = rows[0], last = rows.at(-1), intervals = rows.slice(1).map((row, index) => row.publishedAtMs - rows[index].publishedAtMs);
  const videoSteps = rows.slice(1).flatMap((row, index) => typeof row.videoPresentedFrames === 'number' && typeof rows[index].videoPresentedFrames === 'number'
    ? [row.videoPresentedFrames - rows[index].videoPresentedFrames] : []);
  const tracked = rows.filter(row => row.hasFace), stages = {};
  for (const key of Object.keys(first ?? {}).filter(key => key.endsWith('Ms') && !['capturedAtMs', 'publishedAtMs', 'videoPresentationTimeMs'].includes(key)))
    stages[key] = distribution(rows.map(row => row[key]).filter(value => typeof value === 'number'));
  const nativeKeys = new Set(rows.flatMap(row => Object.keys(row.native ?? {})));
  const native = Object.fromEntries([...nativeKeys].map(key => [key, distribution(rows.map(row => row.native?.[key]).filter(value => typeof value === 'number'))]));
  return {frames: rows.length, measuredSeconds: segment.measuredDurationMs / 1000, publicationsPerSecond: rows.length * 1000 / segment.measuredDurationMs,
    intervalCadenceFps: rows.length > 1 ? (rows.length - 1) * 1000 / (last.publishedAtMs - first.publishedAtMs) : null,
    frameIntervalsMs: distribution(intervals), frameAgeAtPublicationMs: distribution(rows.map(row => row.publishedAtMs - row.capturedAtMs)),
    stallsOver250ms: intervals.filter(value => value > 250).length, stallsOver500ms: intervals.filter(value => value > 500).length,
    trackedFrames: tracked.length, maskedFrames: rows.filter(row => row.hasMask).length,
    hairCoverageAmongTracked: tracked.length ? tracked.filter(row => row.hasMask).length / tracked.length : null,
    framesWithActualHairPixelChanges: rows.filter(row => row.changedPixels > 0).length,
    fallbacks: Object.fromEntries([...new Set(rows.map(row => row.fallback ?? 'none'))].map(reason => [reason, rows.filter(row => (row.fallback ?? 'none') === reason).length])),
    sourceSizes: [...new Set(rows.map(row => `${row.sourceWidth}x${row.sourceHeight}`))],
    cameraSettingFps: [...new Set(rows.map(row => row.cameraSettingFps))],
    observedVideoDeliveryFps: rows.length > 1 && first.videoPresentedFrames !== null && last.videoPresentedFrames !== null
      ? (last.videoPresentedFrames - first.videoPresentedFrames) * 1000 / (last.capturedAtMs - first.capturedAtMs) : null,
    videoFrameStepsBetweenProcessedRows: distribution(videoSteps), repeatedVideoPresentationCounters: videoSteps.filter(step => step === 0).length,
    videoFramesBetweenProcessedRows: videoSteps.length ? videoSteps.reduce((total, step) => total + Math.max(0, step - 1), 0) : null,
    syntheticDrawsPerSecond: segment.syntheticDraws.length * 1000 / segment.measuredDurationMs,
    actualRendererStrings: [...new Set(rows.map(row => row.gpuRenderer))], faceDelegates: [...new Set(rows.map(row => row.faceDelegate))], hairDelegates: [...new Set(rows.map(row => row.hairDelegate))],
    longTaskCount: segment.longTaskSupported ? segment.longTasks.length : null,
    longTaskDurationMs: segment.longTaskSupported ? distribution(segment.longTasks.map(row => row.durationMs)) : null,
    stages, native};
};
let browser, page; await save();
try {
  browser = await chromium.launch({headless: true, args: ['--enable-gpu', '--use-angle=d3d11']});
  let combination = 0;
  for (const eyewear of eyewears) for (const hair of hairModels) {
    const context = await browser.newContext(); await context.grantPermissions(['local-network-access'], {origin: base}); page = await context.newPage();
    const session = {eyewear, hair, order: pipelines.map((_, index) => pipelines[(index + combination) % pipelines.length]), environment: null, segments: [], errors: [], networkErrors: [], consoleErrors: [], informationalConsoleMessages: [], release: null};
    combination++; report.sessions.push(session); await save();
    page.on('pageerror', error => session.errors.push(error.stack ?? error.message));
    page.on('response', response => {if (response.status() >= 400) session.networkErrors.push({status: response.status(), url: response.url()});});
    page.on('console', message => {if (message.type() === 'error') {
      session.consoleErrors.push(message.text());
      if (message.text() === knownInformationalStderr) session.informationalConsoleMessages.push({originalLevel: 'error', text: message.text()});
    }});
    await page.route('**/*', route => {
      const url = new URL(route.request().url()); if (url.origin !== base) return route.abort();
      if (url.pathname === '/qa-stage3-synthetic.png') return route.fulfill({contentType: 'image/png', body: fixtureBytes});
      if (url.pathname === '/favicon.ico') return route.fulfill({status: 204, body: ''});
      if (url.pathname === '/@vite/client') return route.fulfill({contentType: 'text/javascript', body: `
        const states = new Map(), styles = new Map();
        export function createHotContext(id) { if (!states.has(id)) states.set(id, {}); return {data:states.get(id),accept(){},acceptExports(){},dispose(){},prune(){},invalidate(){},on(){},off(){},send(){}}; }
        export function injectQuery(url,query){return url+(url.includes('?')?'&':'?')+query;}
        export function updateStyle(id,content){let s=styles.get(id);if(!s){s=document.createElement('style');styles.set(id,s);document.head.append(s);}s.textContent=content;}
        export function removeStyle(id){styles.get(id)?.remove();styles.delete(id);}
        export class ErrorOverlay extends HTMLElement {}
      `});
      return route.continue();
    });
    await page.addInitScript(installSyntheticCamera, {fixtureUrl: '/qa-stage3-synthetic.png', width, height, requestedFps: 30});
    await page.goto(base + pagePath);
    await page.waitForFunction(() => typeof window.arPerformanceProfiler?.samplesAfter === 'function');
    session.environment = await page.evaluate(() => {
      const gl = document.createElement('canvas').getContext('webgl2'), ext = gl?.getExtension('WEBGL_debug_renderer_info');
      const environment = {userAgent: navigator.userAgent, renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null, timeOriginMs: performance.timeOrigin};
      gl?.getExtension('WEBGL_lose_context')?.loseContext(); return environment;
    });
    assert.match(session.environment.renderer, /Intel.*D3D11/, 'No verified hardware Intel D3D11 context; do not interpret software rendering as hardware performance.');
    await page.selectOption('#eyewear-select', eyewear); await page.selectOption('#hair-model-select', hair);
    await page.selectOption('#variant-select', 'hair'); await page.selectOption('#pipeline-select', session.order[0]);
    await page.click('#start');
    await page.waitForFunction(() => window.hairLivePreview.diagnostics().phase === 'live' && window.hairLivePreview.diagnostics().state === 'tracking', null, {timeout: 60000});
    for (const pipeline of session.order) {
      await page.selectOption('#pipeline-select', pipeline);
      const warmup = await page.evaluate(warmPipeline, {pipeline, warmupMs, minimumMaskFrames: 3, timeoutMs: 90000});
      console.log(JSON.stringify({event: 'measuring', eyewear, hair, pipeline, seconds: durationMs / 1000, warmupFrames: warmup.frames, warmupMaskFrames: warmup.maskFrames}));
      const segment = await page.evaluate(measurePipeline, {pipeline, durationMs, sessionId: warmup.sessionId});
      segment.warmup = warmup; segment.summary = summarize(segment); session.segments.push(segment); await save();
      if (pipeline === 'test3') {
        segment.gpuMechanism = verifyFrameMechanisms(segment.rows, pipeline); await save();
        assert.equal(segment.gpuMechanism.passed, true, 'Measured Test3 frames did not use the required GPU path.');
      }
      assert.ok(segment.measuredDurationMs >= durationMs && segment.rows.length > 1, 'The requested sustained interval was not measured.');
      assert.deepEqual(segment.serialGaps, [], 'Application telemetry ring dropped uncollected publications.');
      assert.deepEqual(segment.failures, [], 'The session ended or changed during the measured interval.');
      assert.deepEqual(segment.unexpectedRows, [], 'A different pipeline/session/variant published during this interval.');
      assert.deepEqual(segment.workerErrors, [], 'Worker failure during measurement.');
      assert.ok(segment.rows.every(row => Number.isFinite(row.publishedAtMs) && Number.isFinite(row.capturedAtMs) && row.publishedAtMs >= row.capturedAtMs));
      assert.ok(segment.rows.every((row, index) => index === 0 || row.sequence > segment.rows[index - 1].sequence && row.publishedAtMs > segment.rows[index - 1].publishedAtMs));
      console.log(JSON.stringify({event: 'measured', eyewear, hair, pipeline, frames:segment.summary.frames,
        fps:segment.summary.publicationsPerSecond,intervalP95:segment.summary.frameIntervalsMs?.p95,
        frameAgeP95:segment.summary.frameAgeAtPublicationMs?.p95,hairCoverage:segment.summary.hairCoverageAmongTracked,
        gpuCoverage:segment.gpuMechanism?.gpuCoverageAmongTracked ?? null}));
    }
    await page.click('#stop'); await page.waitForFunction(() => window.hairLivePreview.diagnostics().state === 'idle');
    session.release = await page.evaluate(releaseState); await save();
    assert.ok(session.release.allTracksEnded && session.release.allWorkersTerminated, 'Session did not release all owned camera tracks/workers.');
    assert.deepEqual(session.errors, []); assert.deepEqual(session.networkErrors, []);
    assert.deepEqual(session.consoleErrors.filter(message => message !== knownInformationalStderr), []);
    await context.close(); page = null;
  }
  for (const [filename, digest] of frozen) assert.equal(sha(await fs.readFile(filename)), digest, `Source/input changed during profiling: ${filename}`);
  report.preservationAfter = await verifyPreservation();
  if (productionBuild) report.productionBuildAfter = await verifyProductionBuild(productionBuild);
  report.complete = report.passed = true; await save();
} catch (error) {report.errors.push(error.stack ?? String(error)); await save(); throw error;}
finally {
  if (page && !page.isClosed()) {try {await page.click('#stop', {timeout: 1500});} catch {} }
  await browser?.close(); console.log(JSON.stringify({report: path.join(out, 'report.json'), passed: report.passed, completedSegments: report.sessions.reduce((n, row) => n + row.segments.length, 0)}));
}
