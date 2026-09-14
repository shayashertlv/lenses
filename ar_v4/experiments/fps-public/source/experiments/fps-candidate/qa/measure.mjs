/** Separate production sessions, real workers and hardware GPU; numeric timing receipts only.
 * node experiments/fps-candidate/qa/measure.mjs --base=http://127.0.0.1:8100
 * --order=g,queries,identity,combined,combined,identity,queries,g --warm-ms=5000 --measure-ms=20000
 * --matrix covers both frame/hair models; --controls runs lifecycle checks instead of timing.
 * --fixture=generated:02-down-turn-auburn --require-hair-edits uses a verified
 * preserved still at its native 1280x853 size, with fresh live inference.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {chromium} from '@playwright/test';
import {verifyPreservation} from './verify-preservation.mjs';
import {FPS_MODES, optionsForFpsMode} from '../runtime/options.ts';
import {loadFixture} from './fixture.mjs';

const args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8100').replace(/\/$/, '');
assert.equal(new URL(base).hostname, '127.0.0.1', 'Use an explicit local candidate server.');
const order = option('order', 'g,queries,identity,combined,combined,identity,queries,g').split(',');
assert.ok(order.length && order.every(value => FPS_MODES.includes(value)), 'Unknown FPS mode.');
const fixtureInput = await loadFixture(option('fixture', 'face-a'));
const width = Number(option('width', String(fixtureInput.width))), height = Number(option('height', String(fixtureInput.height)));
const warmMs = Number(option('warm-ms', '5000')), measureMs = Number(option('measure-ms', '20000'));
assert.ok([width, height, warmMs, measureMs].every(value => Number.isSafeInteger(value) && value > 0));
const controls = args.includes('--controls');
const requireHairEdits = args.includes('--require-hair-edits');
const fixture = fixtureInput.bytes;
const sha = data => createHash('sha256').update(data).digest('hex');
const harnessFiles = ['experiments/fps-candidate/qa/measure.mjs', 'experiments/fps-candidate/qa/fixture.mjs'];
const harnessHashes = Object.fromEntries(await Promise.all(harnessFiles.map(async filename => [filename, sha(await fs.readFile(filename))])));
const out = path.resolve(option('out', `experiments/fps-candidate/qa/output/${controls ? 'controls' : 'measure'}-${new Date().toISOString().replaceAll(':', '-')}`));
await fs.mkdir(out, {recursive: true});
const matrix = args.includes('--matrix') ? ['amber-horizon', 'tom-ford-clear'].flatMap(eyewear =>
  ['hair-only', 'selfie-multiclass'].map(hair => ({eyewear, hair})))
  : [{eyewear: option('eyewear', 'amber-horizon'), hair: option('hair', 'hair-only')}];
const report = {schema: 'ar-fps-candidate-production-v1', complete: false, passed: false, createdAt: new Date().toISOString(),
  controls, base, order, matrix, width, height, warmMs, measureMs, requireHairEdits,
  fixtureSHA256: sha(fixture), fixture: fixtureInput.provenance, fixturePreservationBefore: await fixtureInput.verify(), harnessHashes,
  runs: [], summaries: {}, errors: [],
  protocol: 'One new isolated browser context and live session for each configuration. Identical fixed warmup and measurement duration after readiness. The listed order is repeated for each frame/hair pairing. No frames are dropped from the timing window because their mask or tracking was missing.',
  evidenceLimits: ['The 30 FPS canvas camera shares the main thread with AR and can itself be delayed. One static source image is drawn at the requested camera size; any size change scales it. This is not a physical camera, wearer motion or mobile thermal measurement.',
    'Age is software capture-to-publication wall time, not motion-to-photon latency. Presented FPS counts distinct completed AR profiler samples, not requestAnimationFrame callbacks.',
    'Output pixels/pose/mask pairing and angle safeguards are checked separately by the exact frozen-input matched harness. No FPS result establishes owner visual acceptance.']};
report.originalPreservationBefore = await verifyPreservation();
const buildResponse=await fetch(base+'/fps-build.json');
assert.ok(buildResponse.ok,'Production source fingerprint must be served.');
report.productionBuild=await buildResponse.json();
assert.equal(report.productionBuild.schema,'fps-candidate-source-build-v1');
const distribution = values => {
  const a = values.filter(Number.isFinite).sort((x, y) => x - y);
  return a.length ? {n: a.length, median: a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2,
    p95: a[Math.ceil(a.length * .95) - 1], min: a[0], max: a.at(-1)} : null;
};
const native = (row, suffix) => Object.entries(row.native ?? {}).find(([key]) => key === suffix || key.endsWith(`.${suffix}`))?.[1];
const summarize = rows => {
  const first = rows[0], last = rows.at(-1), durationMs = rows.length > 1 ? last.publishedAtMs - first.publishedAtMs : 0;
  const intervals = rows.slice(1).map((row, i) => row.publishedAtMs - rows[i].publishedAtMs);
  const tracked = rows.filter(row => row.hasFace).length, masked = rows.filter(row => row.hasFace && row.hasMask).length;
  const videoDuration = first && last ? last.capturedAtMs - first.capturedAtMs : 0;
  const stages = ['sourceDrawMs', 'sourceReadbackMs', 'sourceHashMs', 'detectionHashMs', 'faceInferenceMs', 'faceRequestWallMs',
    'faceTransportSchedulingMs', 'hairInferenceMs', 'hairExtractionMs', 'hairWaitMs', 'prepareMs', 'finishMs', 'schedulerWaitMs',
    'composeMs', 'continuityMs', 'finalChecksMs', 'publishMs'];
  const pboKeys = ['speedLab.pbo.waitMs', 'speedLab.pbo.extractMs', 'speedLab.pbo.polls', 'speedLab.pbo.queuedCalls',
    'speedLab.pbo.retrievedCalls', 'speedLab.pbo.retrievedBytes', 'cpuReadbackCalls', 'cpuReadbackBytes'];
  return {frames: rows.length, processedFps: durationMs > 0 ? (rows.length - 1) * 1000 / durationMs : null,
    windowFps: rows.length * 1000 / measureMs, intervalMs: distribution(intervals), ageMs: distribution(rows.map(row => row.totalMs)),
    gapsOver100ms: intervals.filter(value => value > 100).length, gapsOver200ms: intervals.filter(value => value > 200).length,
    tracked, masked, fullCoverage: rows.length > 0 && tracked === rows.length && masked === tracked,
    changedHairFrames: rows.filter(row => row.changedPixels > 0).length,
    changedHairPixels: distribution(rows.map(row => row.changedPixels)),
    actualPboFrames: rows.filter(row => native(row, 'asyncReadbackUsed') === true && native(row, 'pbo.completed') === true).length,
    pboFallbacks: rows.filter(row => native(row, 'asyncFallback')).map(row => native(row, 'asyncFallback')),
    queryReductionFrames:rows.filter(row=>native(row,'reducedGlQueriesUsed')===true).length,
    identityModes:[...new Set(rows.map(row=>native(row,'fps.identityMode')??'original-g-sha256'))],
    composeFrames: rows.filter(row => native(row, 'fps.cpuCompose.used') === true).length,
    composeWordComparisonFrames: rows.filter(row => native(row, 'fps.cpuCompose.wordComparisonUsed') === true).length,
    composeReferenceAuditPixels: distribution(rows.map(row => native(row, 'fps.cpuCompose.auditedReferencePixels'))),
    composeFinalAuditPixels: distribution(rows.map(row => native(row, 'fps.cpuCompose.finalAuditPixels'))),
    composeRgbWritePixels: distribution(rows.map(row => native(row, 'fps.cpuCompose.rgbWritePixels'))),
    bookkeepingFrames: rows.filter(row => native(row, 'fps.bookkeeping.used') === true).length,
    publicationWorkMs: distribution(rows.map(row => native(row, 'fps.bookkeeping.publicationWorkMs'))),
    statsReadsAvoided: distribution(rows.map(row => native(row, 'fps.bookkeeping.statsReadsAvoided'))),
    bookkeepingLast: Object.fromEntries(['rawRowsIndexed', 'summaryBuilds', 'summaryReuses', 'summaryWorkMs'].map(key => [key, native(last ?? {}, `fps.bookkeeping.${key}`) ?? null])),
    stateQueries:distribution(rows.map(row=>native(row,'stateQueryCalls'))),
    errorChecks:distribution(rows.map(row=>native(row,'errorCheckCalls'))),
    videoDeliveryFps: videoDuration > 0 && Number.isFinite(first.videoPresentedFrames) && Number.isFinite(last.videoPresentedFrames)
      ? (last.videoPresentedFrames - first.videoPresentedFrames) * 1000 / videoDuration : null,
    stages: Object.fromEntries(stages.map(key => [key, distribution(rows.map(row => row[key]))])),
    native: Object.fromEntries(pboKeys.map(key => [key, distribution(rows.map(row => native(row, key)))])),
    delegates: {face: [...new Set(rows.map(row => row.faceDelegate))], hair: [...new Set(rows.map(row => row.hairDelegate))]},
    sourceSizes: [...new Set(rows.map(row => `${row.sourceWidth}x${row.sourceHeight}`))],
    gpuRenderers: [...new Set(rows.map(row => row.gpuRenderer))]};
};
const save = async () => {
  for (const model of matrix) for (const variant of [...new Set(order)]) {
    const runs = report.runs.filter(run => run.variant === variant && run.eyewear === model.eyewear && run.hair === model.hair && run.summary);
    report.summaries[`${model.eyewear}/${model.hair}/${variant}`] = {runs: runs.length,
      fps: distribution(runs.map(run => run.summary.processedFps)), p95AgeMs: distribution(runs.map(run => run.summary.ageMs?.p95)),
      p95IntervalMs: distribution(runs.map(run => run.summary.intervalMs?.p95)),
      fullCoverage: runs.length > 0 && runs.every(run => run.summary.fullCoverage),
      allFramesUsedPbo: runs.length > 0 && runs.every(run => run.summary.actualPboFrames === run.summary.frames)};
  }
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
};

async function installCamera(page) {
  await page.route('**/fps-camera-fixture', route => route.fulfill({contentType: fixtureInput.mimeType, body: fixture}));
  await page.addInitScript(({width, height}) => {
    const source = document.createElement('canvas'); source.width = width; source.height = height;
    const context = source.getContext('2d'), image = new Image();
    const state = {blank: false, failNext: false, streams: [], workers: [], draws: 0}; window.fpsCamera = state;
    const ready = new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; }); image.src = '/fps-camera-fixture';
    const draw = () => { context.fillStyle = '#163949'; context.fillRect(0, 0, width, height);
      if (!state.blank && image.complete && image.naturalWidth) context.drawImage(image, 0, 0, width, height); state.draws++; };
    setInterval(draw, 1000 / 30);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      if (state.failNext) { state.failNext = false; throw new DOMException('Deliberate camera permission failure for lifecycle QA.', 'NotAllowedError'); }
      await ready; draw(); const stream = source.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    window.Worker = class extends NativeWorker {
      constructor(url, options) { super(url, options); this.index = state.workers.length; state.workers.push({terminated: false}); }
      terminate() { state.workers[this.index].terminated = true; super.terminate(); }
    };
  }, {width, height});
}
const waitClosed = page => page.waitForFunction(() => window.fpsCamera.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))
  && window.fpsCamera.workers.every(w => w.terminated), null, {timeout: 60000});
const waitTracked = async page => {
  // Capture the ready publication atomically. Reading diagnostics in a second
  // evaluate can observe the next private prepare, whose stats are correctly null.
  const handle = await page.waitForFunction(() => {
    const d = window.hairLivePreview.diagnostics(); return d.state === 'tracking' && d.hairReady && d.stats?.hasMask ? d : false;
  }, null, {timeout: 60000});
  try { return await handle.jsonValue(); } finally { await handle.dispose(); }
};
async function runControls(page, run) {
  await page.evaluate(() => { window.fpsCamera.failNext = true; });
  await page.click('#start');
  await page.waitForFunction(() => !document.querySelector('#start').disabled && document.querySelector('.stage').dataset.state !== 'tracking');
  await waitClosed(page); run.cameraFailureClosed = true;
  await page.click('#start'); await page.click('#stop');
  await page.waitForSelector('.stage[data-state="idle"]'); await waitClosed(page); run.cancelStartupClosed = true;
  await page.click('#start'); run.live = await waitTracked(page);
  const session = await page.locator('.stage').getAttribute('data-session-id');
  if (optionsForFpsMode(run.variant).identity) {
    const presented = run.live.presented;
    assert.equal(presented.identityKind, 'session-frame');
    assert.equal(presented.sourceIdentity, `live:source:${session}:${presented.sequence}`);
    assert.equal(presented.detectionIdentity, `live:detection:${session}:${presented.sequence}`);
    assert.equal(presented.sourceSHA256, undefined);
    assert.equal(presented.detectionSHA256, undefined);
    run.liveSessionFrameOwnership = true;
  } else {
    const presented = run.live.presented;
    if (run.variant !== 'g') assert.equal(presented.identityKind, 'sha256');
    assert.match(presented.sourceIdentity ?? presented.sourceSHA256, /^[0-9a-f]{64}$/);
    assert.match(presented.detectionIdentity ?? presented.detectionSHA256, /^[0-9a-f]{64}$/);
    run.liveContentHashIdentity = true;
  }
  if (requireHairEdits) assert.ok(run.live.stats.changedPixels > 0, 'Lifecycle input must exercise visible hair-temple replacement.');
  // Live diagnostics are only used for ownership checks and are excluded from numeric timing runs.
  await page.click('#hold-frame'); await page.waitForSelector('.stage[data-state="held"]', {timeout: 60000});
  await page.waitForFunction(() => !window.hairLivePreview.diagnostics().heldBusy, null, {timeout: 60000});
  await waitClosed(page);
  const held = await page.evaluate(async () => {
    const report = window.hairLivePreview.exportDiagnostic();
    if (!report) throw new Error('Held diagnostic missing.');
    const outputs = Object.values(report).filter(value => value && typeof value === 'object' && value.sourcePngDataUrl && value.acceptedPngDataUrl && value.hairPngDataUrl);
    if (!outputs.length) throw new Error('Held renderer outputs missing.');
    const first = outputs[0], source = document.createElement('canvas'), image = new Image(); image.src = first.sourcePngDataUrl; await image.decode();
    source.width = image.naturalWidth; source.height = image.naturalHeight;
    const context = source.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'}); context.drawImage(image, 0, 0);
    const sha = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
    const sourceSHA256 = await sha(context.getImageData(0, 0, source.width, source.height).data);
    const checks = await Promise.all(outputs.map(async output => {
      const pair = output.pair, mask = output.mask;
      return {sourceContentHash: (pair.sourceSHA256 ?? pair.sourceIdentity) === sourceSHA256,
        detectionContentHash: (pair.detectionSHA256 ?? pair.detectionIdentity) === await sha(new TextEncoder().encode(JSON.stringify(output.detection))),
        hasMask: output.stats.hasMask, fullMask: mask?.outputMode === 'full',
        samePair: JSON.stringify(pair) === JSON.stringify(first.pair), sameMask: JSON.stringify(mask) === JSON.stringify(first.mask),
        acceptedExact: output.acceptedPngDataUrl === first.acceptedPngDataUrl, hairExact: output.hairPngDataUrl === first.hairPngDataUrl,
        sourceExact: output.sourcePngDataUrl === first.sourcePngDataUrl,
        guards: ['backgroundReferenceCheck', 'protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'].every(key => output.stats[key]?.changedPixels === 0)};
    }));
    return {sourceSHA256, outputs: outputs.length, checks, passed: checks.every(row => Object.values(row).every(Boolean))};
  });
  run.held = held; assert.equal(held.passed, true, 'Held exact image, content hashes, mask or safeguards differ.');
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path: path.join(out, `${run.index}-${run.variant}-${run.eyewear}-${run.hair}-held.png`), fullPage: true});
  await page.click('#resume-live'); await waitTracked(page);
  assert.notEqual(await page.locator('.stage').getAttribute('data-session-id'), session); run.resumeFreshSession = true;
  await page.evaluate(() => { window.fpsCamera.blank = true; });
  await page.waitForSelector('.stage[data-state="searching"]', {timeout: 60000});
  assert.equal(await page.evaluate(() => window.hairLivePreview.exportDiagnostic()), null); run.noFaceClears = true;
  await page.click('#stop'); await waitClosed(page); run.stoppedClosed = true;
}

let browser;
try {
  browser = await chromium.launch({headless: true, args: ['--enable-gpu', '--use-angle=d3d11']});
  report.browserVersion = browser.version();
  for (const model of matrix) for (const variant of order) {
    const run = {index: report.runs.length, variant, ...model, rows: [], loadedScripts: [], errors: []}; report.runs.push(run); await save();
    const context = await browser.newContext({viewport: {width: 1440, height: 1080}}), page = await context.newPage();
    page.on('pageerror', error => run.errors.push(error.stack ?? error.message));
    page.on('request', request => { if (request.resourceType() === 'script') run.loadedScripts.push(request.url()); });
    page.on('response', response => { if (response.status() >= 400) run.errors.push(`${response.status()} ${response.url()}`); });
    try {
      await installCamera(page);
      await page.goto(`${base}/experiments/fps-candidate/live.html?fps=${variant}`);
      assert.equal(await page.locator('#fps-mode').inputValue(), variant, 'Requested candidate mode was not selected.');
      run.environment = await page.evaluate(() => {
        const gl = document.createElement('canvas').getContext('webgl2'); if (!gl) throw new Error('WebGL2 unavailable.');
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        const value = {userAgent: navigator.userAgent, hardwareConcurrency: navigator.hardwareConcurrency,
          devicePixelRatio, renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null};
        gl.getExtension('WEBGL_lose_context')?.loseContext(); return value;
      });
      assert.match(run.environment.renderer, /D3D11/); assert.doesNotMatch(run.environment.renderer, /SwiftShader|Microsoft Basic Render/i);
      await page.selectOption('#eyewear-select', model.eyewear); await page.selectOption('#hair-model-select', model.hair);
      if (controls) { await runControls(page, run); }
      else {
        const startup = Date.now(); await page.click('#start'); await waitTracked(page); run.startupMs = Date.now() - startup;
        await page.waitForTimeout(warmMs);
        await page.waitForFunction(() => window.arPerformanceProfiler.samplesAfter(0).filter(row => row.hasFace && row.hasMask).length >= 3);
        const begin = await page.evaluate(() => ({time: performance.now(), serial: window.arPerformanceProfiler.samplesAfter(0).at(-1)?.serial ?? 0, draws: window.fpsCamera.draws}));
        await page.waitForTimeout(measureMs);
        const observation = await page.evaluate(begin => ({rows: window.arPerformanceProfiler.samplesAfter(begin.serial).filter(row => row.publishedAtMs <= begin.time + begin.duration),
          now: performance.now(), draws: window.fpsCamera.draws, hairError: window.hairLivePreview.diagnostics().hairError}), {...begin, duration: measureMs});
        run.rows = observation.rows; run.measureStartedAtMs = begin.time; run.measureEndedAtMs = begin.time + measureMs;
        run.observationElapsedMs = observation.now - begin.time; run.sourceDrawsObserved = observation.draws - begin.draws; run.hairError = observation.hairError;
        run.summary = summarize(run.rows);
        assert.ok(run.rows.length > 1, 'No useful completed frame window.');
        assert.ok(run.rows.every(row => row.pipeline === 'combined' && row.sourceWidth === width && row.sourceHeight === height));
        assert.equal(run.hairError, null);
        const selectedOptions = optionsForFpsMode(run.variant);
        if (selectedOptions.cpuCompose) {
          assert.equal(run.summary.composeFrames, run.summary.masked, 'The selected CPU composition must actually run on every masked frame.');
          assert.equal(run.summary.composeWordComparisonFrames, run.summary.masked, 'Word comparisons were not exercised.');
          assert.ok(run.rows.filter(row => row.hasFace && row.hasMask).every(row =>
            native(row, 'fps.cpuCompose.auditedReferencePixels') === width * height &&
            native(row, 'fps.cpuCompose.finalAuditPixels') === width * height), 'Composition must retain full-frame reference/final audits.');
        }
        if (selectedOptions.bookkeeping) assert.equal(run.summary.bookkeepingFrames, run.summary.frames,
          'Every measured publication must use the selected bookkeeping path.');
        if (requireHairEdits) assert.equal(run.summary.changedHairFrames, run.summary.frames,
          'Every measured frame must exercise visible hair-temple replacement; no zero-edit frames are filtered out.');
        await page.click('#stop'); await waitClosed(page);
      }
      assert.deepEqual(run.errors, []); run.passed = true;
      console.log(JSON.stringify({variant, ...model, passed: true, summary: run.summary ?? run.held}));
    } catch (error) {
      run.errors.push(error.stack ?? String(error)); run.passed = false; await save(); throw error;
    } finally { await context.close(); await save(); }
  }
  report.originalPreservationAfter = await verifyPreservation();
  report.fixturePreservationAfter = await fixtureInput.verify();
  for (const [filename, digest] of Object.entries(harnessHashes)) assert.equal(sha(await fs.readFile(filename)), digest, `Timing harness changed during its run: ${filename}`);
  const finalBuildResponse = await fetch(base + '/fps-build.json'); assert.ok(finalBuildResponse.ok);
  report.productionBuildAfter = await finalBuildResponse.json();
  assert.equal(report.productionBuildAfter.fingerprint, report.productionBuild.fingerprint, 'Production build changed during measurement.');
  report.complete = true; report.passed = report.runs.every(run => run.passed); await save();
} catch (error) { report.errors.push(error.stack ?? String(error)); await save(); throw error; }
finally { await browser?.close(); console.log(JSON.stringify({report: path.join(out, 'report.json'), complete: report.complete, passed: report.passed})); }
