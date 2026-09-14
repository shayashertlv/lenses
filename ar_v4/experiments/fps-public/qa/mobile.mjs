/** Production-path portrait capture/lifecycle and full-duration ABBA regression.
 * Run from ar_v4 after the Python mobile server is ready on localhost:8106.
 * --phase=entries|lifecycle|study|all (default all); --modes=g,next-combined,...
 * --limit=1 restricts lifecycle to its first selected configuration for a smoke.
 * This controls a synthetic camera only. It is not iPhone hardware/FPS evidence.
 */
import assert from 'node:assert/strict';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const directory = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(directory, '../../..');
const arg = (name, fallback) => process.argv.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = arg('base', 'http://127.0.0.1:8106').replace(/\/$/, '');
const phase = arg('phase', 'all');
assert.ok(['entries', 'lifecycle', 'study', 'all'].includes(phase));
assert.ok(new URL(base).hostname === '127.0.0.1' || phase === 'entries' && new URL(base).protocol === 'https:',
  'Only read-only entry verification may target a public HTTPS origin.');
const entry = `${base}/ar_testing/fps/experiments/fps-candidate/live.html`;
const buildUrl = `${base}/ar_testing/fps/fps-build.json`;
const studyKey = 'ar-fps-candidate-study-v1';
const out = path.resolve(arg('out', path.join(directory, 'output', `mobile-${new Date().toISOString().replaceAll(':', '-')}`)));
await mkdir(out, {recursive: true});
const fixture = await readFile(path.join(app, 'tests/fixtures/face-a.jpg'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const buildResponse = await fetch(buildUrl); assert.ok(buildResponse.ok);
const build = await buildResponse.json(); assert.equal(build.schema, 'fps-public-build-v1');
assert.match(build.fingerprint, /^[a-f0-9]{64}$/);
const report = {schema: 'fps-public-mobile-qa-v1', createdAt: new Date().toISOString(), phase, base,
  complete: false, passed: false, build, fixtureSHA256: hash(fixture), harnessSHA256: hash(await readFile(import.meta.filename)),
  limits: ['Static checked-in face-a fixture, 720x1280 synthetic camera with an advancing playback getter; real Chromium workers and renderer.',
    'No physical iPhone, wearer movement, mobile thermals, display scanout, or new visual acceptance is measured.',
    'ABBA uses unchanged production five-second warmup and thirty-second measurement windows; no test clocks or deadlines are accelerated.'],
  entries: [], lifecycle: [], study: null, errors: []};
const save = () => writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
const metric = (row, suffix) => Object.entries(row.native ?? {}).find(([key]) => key === suffix || key.endsWith(`.${suffix}`))?.[1];
const expected = mode => ({compose: mode === 'compose' || mode === 'next-combined',
  bookkeeping: mode === 'bookkeeping' || mode === 'next-combined'});
const url = (mode, eyewear = 'amber-horizon', hair = 'hair-only') => `${entry}?${new URLSearchParams({fps: mode, eyewear, hairModel: hair, variant: 'hair'})}`;

async function installCamera(page, entryOnly = false) {
  if (entryOnly) {
    await page.addInitScript(() => {
      const state = {streams: [], workers: [], requests: 0}; window.fpsMobileCamera = state;
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
        state.requests++; throw new Error('Camera startup is forbidden in read-only entry verification.');
      }});
      window.Worker = class {constructor() {state.workers.push({terminated: true}); throw new Error('Worker startup is forbidden in read-only entry verification.');}};
    });
    return;
  }
  await page.route('**/ar_testing/fps/mobile-qa-camera.jpg', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas'); canvas.width = 720; canvas.height = 1280;
    const context = canvas.getContext('2d'), image = new Image();
    const state = {streams: [], workers: [], requests: 0, failNext: false, blank: false, clockReads: 0};
    window.fpsMobileCamera = state;
    const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
    image.src = '/ar_testing/fps/mobile-qa-camera.jpg';
    const draw = () => {
      context.fillStyle = '#808080'; context.fillRect(0, 0, 720, 1280);
      if (!state.blank && image.complete && image.naturalWidth) context.drawImage(image, 0, 400, 720, 480);
    };
    setInterval(draw, 33);
    const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {...descriptor,
      get() {return this.srcObject instanceof MediaStream ? performance.now() / 1000 + ++state.clockReads / 1e6 : descriptor.get.call(this);}});
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
      state.requests++;
      const denyOnce = sessionStorage.getItem('fps-qa-deny-once') === '1';
      if (denyOnce) sessionStorage.removeItem('fps-qa-deny-once');
      if (state.failNext || denyOnce) {state.failNext = false; throw new DOMException('Intentional QA camera denial.', 'NotAllowedError');}
      await ready; draw(); const stream = canvas.captureStream(30); state.streams.push(stream); return stream;
    }});
    const NativeWorker = window.Worker;
    window.Worker = class extends NativeWorker {
      constructor(address, options) {super(address, options); this.record = {terminated: false}; state.workers.push(this.record);}
      terminate() {this.record.terminated = true; super.terminate();}
    };
  });
}

async function newPage(browser, receipt, width = 390, entryOnly = false) {
  const context = await browser.newContext({viewport: {width, height: width === 390 ? 844 : 1080}, acceptDownloads: true});
  const page = await context.newPage(); receipt.errors = []; receipt.unscopedRequests = [];
  page.on('pageerror', error => receipt.errors.push(error.stack ?? error.message));
  page.on('response', response => {if (response.status() >= 400) receipt.errors.push(`${response.status()} ${response.url()}`);});
  page.on('request', request => {
    const target = new URL(request.url());
    if (['data:', 'blob:'].includes(target.protocol)) return;
    if (target.origin !== new URL(base).origin || !target.pathname.startsWith('/ar_testing/'))
      receipt.unscopedRequests.push(request.url());
  });
  await installCamera(page, entryOnly); return {context, page};
}
const closed = page => page.waitForFunction(() => window.fpsMobileCamera.streams.every(stream =>
  stream.getTracks().every(track => track.readyState === 'ended')) && window.fpsMobileCamera.workers.every(worker => worker.terminated), null, {timeout: 60000});
async function tracked(page) {
  const handle = await page.waitForFunction(() => {
    const value = window.hairLivePreview?.diagnostics();
    return value?.state === 'tracking' && value.hairReady && value.stats?.hasMask ? value : false;
  }, null, {timeout: 60000});
  try {return await handle.jsonValue();} finally {await handle.dispose();}
}
async function usage(page, mode) {
  await page.waitForFunction(() => window.arPerformanceProfiler.samplesAfter(0).filter(row => row.hasFace && row.hasMask).length >= 3, null, {timeout: 60000});
  const rows = await page.evaluate(() => window.arPerformanceProfiler.samplesAfter(0));
  const masked = rows.filter(row => row.hasFace && row.hasMask), options = expected(mode);
  assert.ok(masked.length >= 3); assert.ok(rows.every(row => row.pipeline === 'combined' && row.sourceWidth === 720 && row.sourceHeight === 1280));
  assert.ok(rows.every(row => Number.isSafeInteger(row.videoPresentedFrames)), 'rVFC metadata must survive an advancing playback clock.');
  assert.ok(rows.every(row => metric(row, 'pump.maxOwnedFrames') <= 2));
  for (const row of masked) {
    assert.equal(metric(row, 'fps.cpuCompose.used') === true, options.compose);
    assert.equal(metric(row, 'fps.bookkeeping.used') === true, options.bookkeeping);
    if (options.compose) {
      assert.equal(metric(row, 'fps.cpuCompose.wordComparisonUsed'), true);
      assert.equal(metric(row, 'fps.cpuCompose.auditedReferencePixels'), 720 * 1280);
      assert.equal(metric(row, 'fps.cpuCompose.finalAuditPixels'), 720 * 1280);
    }
  }
  return {rows: rows.length, masked: masked.length, visibleHairEditFrames: rows.filter(row => row.changedPixels > 0).length,
    sessionId: masked.at(-1).sessionId, options, samples: rows};
}

async function heldCheck(page) {
  await page.click('#hold-frame'); await page.waitForSelector('.stage[data-state="held"]', {timeout: 60000});
  await page.waitForFunction(() => !window.hairLivePreview.diagnostics().heldBusy, null, {timeout: 60000});
  await closed(page);
  return page.evaluate(async () => {
    const exported = window.hairLivePreview.exportDiagnostic();
    const outputs = Object.values(exported ?? {}).filter(value => value?.sourcePngDataUrl && value.acceptedPngDataUrl && value.hairPngDataUrl);
    if (outputs.length !== 8) throw new Error('The eight held rendering profiles are missing.');
    const first = outputs[0], canvas = document.createElement('canvas'), image = new Image();
    image.src = first.sourcePngDataUrl; await image.decode(); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'}); context.drawImage(image, 0, 0);
    const sha = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2, '0')).join('');
    const from64 = value => Uint8Array.from(atob(value), character => character.charCodeAt(0));
    const sourceSHA256 = await sha(context.getImageData(0, 0, canvas.width, canvas.height).data);
    const checks = await Promise.all(outputs.map(async value => {
      const pair = value.pair, mask = value.mask;
      return {sourceContentHash: (pair.sourceSHA256 ?? pair.sourceIdentity) === sourceSHA256,
        detectionContentHash: (pair.detectionSHA256 ?? pair.detectionIdentity) === await sha(new TextEncoder().encode(JSON.stringify(value.detection))),
        fullMask: value.stats.hasMask && mask?.outputMode === 'full',
        categoryHash: !!mask?.categoryBase64 && await sha(from64(mask.categoryBase64)) === mask.categorySHA256,
        confidenceHash: !!mask?.confidenceBase64 && await sha(from64(mask.confidenceBase64)) === mask.confidenceSHA256,
        samePair: JSON.stringify(pair) === JSON.stringify(first.pair), sameMask: JSON.stringify(mask) === JSON.stringify(first.mask),
        sameDetection: JSON.stringify(value.detection) === JSON.stringify(first.detection),
        acceptedExact: value.acceptedPngDataUrl === first.acceptedPngDataUrl, hairExact: value.hairPngDataUrl === first.hairPngDataUrl,
        sourceExact: value.sourcePngDataUrl === first.sourcePngDataUrl,
        sameGeometry: ['surfacePositions', 'eyewearMatrix', 'protection'].every(key => JSON.stringify(value.captureSnapshot[key]) === JSON.stringify(first.captureSnapshot[key])),
        guards: ['backgroundReferenceCheck', 'protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'].every(key => value.stats[key]?.changedPixels === 0)};
    }));
    return {outputs: outputs.length, sourceSHA256, checks, passed: checks.every(value => Object.values(value).every(Boolean))};
  });
}
function clean(receipt) {assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.unscopedRequests, []);}

async function entries(browser) {
  for (const width of [390, 1440]) {
    const receipt = {width}; report.entries.push(receipt); const {context, page} = await newPage(browser, receipt, width, true);
    try {
      await page.goto(url('next-combined')); await page.waitForFunction(() => window.hairLivePreview && document.querySelector('#fps-mode').value === 'next-combined');
      receipt.buildLabel = await page.locator('#fps-build').textContent(); assert.ok(receipt.buildLabel.includes(build.fingerprint.slice(0, 12)));
      receipt.state = await page.evaluate(() => ({requests: window.fpsMobileCamera.requests, workers: window.fpsMobileCamera.workers.length,
        width: innerWidth, scrollWidth: document.documentElement.scrollWidth, stage: document.querySelector('.stage').dataset.state}));
      assert.equal(receipt.state.requests, 0); assert.equal(receipt.state.workers, 0); assert.equal(receipt.state.stage, 'idle');
      assert.ok(receipt.state.scrollWidth <= receipt.state.width + 1, 'Entry has horizontal overflow.');
      await page.screenshot({path: path.join(out, `entry-${width}.png`), fullPage: true}); clean(receipt); receipt.passed = true;
    } finally {await context.close(); await save();}
    console.log(JSON.stringify({entry: width, passed: true}));
  }
}
async function lifecycle(browser) {
  const configurations = ['g', 'next-combined'].flatMap(mode => ['amber-horizon', 'tom-ford-clear'].flatMap(eyewear =>
    ['hair-only', 'selfie-multiclass'].map(hair => ({mode, eyewear, hair}))));
  configurations.push(...['compose', 'bookkeeping'].map(mode => ({mode, eyewear: 'amber-horizon', hair: 'hair-only'})));
  const modes = arg('modes', '').split(',').filter(Boolean);
  const limit = Number(arg('limit', String(configurations.length))); assert.ok(Number.isSafeInteger(limit) && limit > 0);
  for (const configuration of configurations.filter(value => !modes.length || modes.includes(value.mode)).slice(0, limit)) {
    const receipt = {...configuration}; report.lifecycle.push(receipt); await save();
    const {context, page} = await newPage(browser, receipt);
    try {
      await page.goto(url(receipt.mode, receipt.eyewear, receipt.hair));
      await page.waitForFunction(() => !!window.hairLivePreview);
      assert.equal(await page.locator('#fps-mode').inputValue(), receipt.mode);
      await page.evaluate(() => {window.fpsMobileCamera.failNext = true;});
      await page.click('#start'); await page.waitForSelector('.stage[data-state="error"]'); await closed(page); receipt.denialClosed = true;
      await page.click('#start'); await page.click('#stop'); await page.waitForSelector('.stage[data-state="idle"]'); await closed(page); receipt.startupCancelled = true;
      await page.click('#start'); const live = await tracked(page); receipt.usage = await usage(page, receipt.mode);
      assert.match(live.presented.sourceIdentity ?? live.presented.sourceSHA256, /^[a-f0-9]{64}$/);
      assert.match(live.presented.detectionIdentity ?? live.presented.detectionSHA256, /^[a-f0-9]{64}$/);
      receipt.held = await heldCheck(page); assert.equal(receipt.held.passed, true, 'Held hashes, mask, geometry, exact pixels or safeguards failed.');
      await page.click('#resume-live'); const resumed = await tracked(page); assert.notEqual(resumed.sessionId, live.sessionId); receipt.resumeFreshSession = true;
      await page.evaluate(() => {window.fpsMobileCamera.blank = true;}); await page.waitForSelector('.stage[data-state="searching"]', {timeout: 60000});
      assert.equal(await page.evaluate(() => window.hairLivePreview.exportDiagnostic()), null); receipt.noFaceCleared = true;
      await page.click('#stop'); await closed(page); receipt.cleanup = true; clean(receipt); receipt.passed = true;
    } catch (error) {receipt.errors.push(error.stack ?? String(error)); throw error;}
    finally {await context.close(); await save();}
    console.log(JSON.stringify({...configuration, passed: true, held: receipt.held.passed, masked: receipt.usage.masked}));
  }
}
async function study(browser) {
  const receipt = {mode: 'next-combined'}; report.study = receipt; const {context, page} = await newPage(browser, receipt);
  try {
    await page.goto(url('next-combined')); await page.waitForFunction(() => !!window.hairLivePreview);
    await page.evaluate(() => sessionStorage.setItem('fps-qa-deny-once', '1'));
    await page.click('#fps-study-start'); await page.waitForURL(address => address.searchParams.get('fps') === 'g');
    await page.waitForSelector('.stage[data-state="error"]');
    receipt.denialState = await page.evaluate(key => ({study: JSON.parse(sessionStorage.getItem(key)),
      status: document.querySelector('#fps-study-status').textContent,
      continueHidden: document.querySelector('#fps-study-continue').hidden,
      continueDisabled: document.querySelector('#fps-study-continue').disabled,
      startDisabled: document.querySelector('#start').disabled}), studyKey);
    console.log(JSON.stringify({denialState: receipt.denialState})); await save();
    assert.equal(receipt.denialState.study?.status, 'running', 'Planned navigation must preserve the saved comparison.');
    await page.waitForFunction(() => !document.querySelector('#fps-study-continue').hidden && !document.querySelector('#fps-study-continue').disabled);
    assert.equal(await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)).status, studyKey), 'running');
    await page.click('#fps-study-continue'); await tracked(page); receipt.deniedAutoOpenRetried = true;
    // Real production time: the browser navigates itself between every window.
    const deadline = Date.now() + 420000; let previous = -1, result;
    while (Date.now() < deadline) {
      try {result = await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), studyKey);} catch {await page.waitForTimeout(250); continue;}
      if (result?.index !== previous) {previous = result?.index; console.log(JSON.stringify({studySegment: previous, status: result?.status})); await save();}
      if (result?.status !== 'running') break;
      await page.waitForTimeout(1000);
    }
    assert.equal(result?.status, 'complete', `ABBA did not complete: ${result?.reason ?? 'deadline'}`);
    assert.equal(result.buildId, build.fingerprint); assert.equal(result.warmupMs, 5000); assert.equal(result.measurementMs, 30000);
    assert.deepEqual(result.order, ['g', 'next-combined', 'next-combined', 'g']); assert.equal(result.segments.length, 4);
    assert.equal(new Set(result.segments.map(value => value.timeOriginMs)).size, 4, 'Each ABBA segment needs a fresh document.');
    const sessions = new Set();
    for (const segment of result.segments) {
      assert.ok(segment.rows.length >= 3); sessions.add(segment.rows[0].sessionId);
      assert.equal(new Set(segment.rows.map(row => row.sessionId)).size, 1);
      assert.ok(segment.rows.at(-1).publishedAtMs - segment.rows[0].publishedAtMs >= 30000);
      assert.ok(segment.rows.every(row => row.sourceWidth === 720 && row.sourceHeight === 1280));
      const fps = (segment.rows.length - 1) * 1000 / (segment.rows.at(-1).publishedAtMs - segment.rows[0].publishedAtMs);
      assert.equal(segment.fps, fps); assert.ok(segment.rows.some(row => row.hasFace && row.hasMask));
      for (const row of segment.rows.filter(row => row.hasFace && row.hasMask)) {
        assert.equal(metric(row, 'fps.cpuCompose.used') === true, expected(segment.mode).compose);
        assert.equal(metric(row, 'fps.bookkeeping.used') === true, expected(segment.mode).bookkeeping);
      }
    }
    assert.equal(sessions.size, 4); receipt.result = result;
    assert.doesNotMatch(JSON.stringify(result), /data:image|"(?:sourceIdentity|detectionIdentity|sourceSHA256|detectionSHA256|landmarks|categoryBase64|confidenceBase64)"/);
    const download = page.waitForEvent('download'); await page.click('#fps-study-download'); const file = await download;
    const downloadedPath = path.join(out, 'abba-downloaded.json'); await file.saveAs(downloadedPath);
    assert.deepEqual(JSON.parse(await readFile(downloadedPath, 'utf8')), result);
    assert.deepEqual(JSON.parse(await page.locator('#fps-study-json').inputValue()), result);
    // Prevent file navigation to prove the connected Save link and selected text
    // remain useful independently of browser download behavior.
    await page.evaluate(() => document.querySelector('#fps-study-save').addEventListener('click', event => event.preventDefault()));
    await page.click('#fps-study-download'); await page.click('#fps-study-copy');
    receipt.copy = await page.evaluate(() => {
      const field = document.querySelector('#fps-study-json'), link = document.querySelector('#fps-study-save');
      return {selected: field.selectionStart === 0 && field.selectionEnd === field.value.length,
        connected: link.isConnected && !link.hidden, href: link.href, filename: link.download};
    });
    assert.equal(receipt.copy.selected, true); assert.equal(receipt.copy.connected, true); assert.match(receipt.copy.href, /^blob:/);
    await page.waitForTimeout(1500);
    assert.deepEqual(await page.evaluate(async () => (await fetch(document.querySelector('#fps-study-save').href)).json()), result);
    receipt.persistentSave = true;
    await page.click('#stop'); await closed(page); clean(receipt); receipt.passed = true;
  } catch (error) {receipt.errors.push(error.stack ?? String(error)); throw error;}
  finally {await context.close(); await save();}
}

let browser;
try {
  browser = await chromium.launch({headless: true, args: ['--enable-gpu', '--use-angle=d3d11']}); report.browserVersion = browser.version();
  if (phase === 'entries' || phase === 'all') await entries(browser);
  if (phase === 'lifecycle' || phase === 'all') await lifecycle(browser);
  if (phase === 'study' || phase === 'all') await study(browser);
  const finalBuild = await (await fetch(buildUrl)).json(); assert.equal(finalBuild.fingerprint, build.fingerprint);
  assert.equal(hash(await readFile(path.join(app, 'tests/fixtures/face-a.jpg'))), report.fixtureSHA256);
  report.complete = true; report.passed = true; await save();
} catch (error) {report.errors.push(error.stack ?? String(error)); await save(); throw error;}
finally {await browser?.close(); console.log(JSON.stringify({report: path.join(out, 'report.json'), complete: report.complete, passed: report.passed}));}
