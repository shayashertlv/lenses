/** Default production pipeline smoke test using the checked-in face-a synthetic camera.
 * node qa/pipeline-smoke.mjs --base=http://127.0.0.1:8241 [--out=qa/output/pipeline-smoke]
 * Checks ordinary UI, startup, fixed temple geometry, motion settings and held-frame protection.
 * This is a lifecycle/rendering check, not an assessment of real-wearer appearance. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url)), args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8241').replace(/\/$/, '');
assert.equal(new URL(base).search, '', 'Use the default page without query overrides');
const output = path.resolve(option('out', path.join(here, 'output', 'pipeline-smoke')));
await fs.mkdir(output, {recursive: true});
const fixture = await fs.readFile(path.join(here, 'fixtures', 'face-a.jpg'));
const report = {schema: 'ar-pipeline-smoke-v1', createdAt: new Date().toISOString(), base, status: 'running',
  checks: [], layouts: [], sessions: [], audits: [], screenshots: [], console: [], pageErrors: [], shaderErrors: [],
  limits: ['Synthetic camera from a checked-in still image with slow drift. No real camera is accessed.',
    'This verifies default UI, pipeline lifecycle and protection checks; it does not establish hair realism, wearer motion or phone performance.']};
const browser = await chromium.launch({headless: true, channel: 'chromium', args: [
  '--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required',
]});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
page.setDefaultTimeout(30_000);
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('console', message => {
  const text = message.text();
  if (['warning', 'error'].includes(message.type())) report.console.push({type: message.type(), text: text.slice(0, 1800)});
  if ((message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(text))
    || /VALIDATE_STATUS.*false|shader.*(?:failed to compile|compilation failed)|ERROR:\s*\d+:\d+:/i.test(text)) report.shaderErrors.push(text.slice(0, 4000));
});
await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
await page.addInitScript(() => {
  const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
  const context = source.getContext('2d'), image = new Image();
  const state = {requests: 0, streams: []}; window.__syntheticCamera = state;
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
  image.src = '/ar-camera-fixture';
  const started = performance.now();
  const draw = () => {
    context.fillStyle = '#163949'; context.fillRect(0, 0, 1280, 720);
    if (image.complete && image.naturalWidth) {
      const t = (performance.now() - started) / 1000, scale = 1 + Math.sin(t * 2 * Math.PI * .07) * .03;
      context.drawImage(image, Math.sin(t * 2 * Math.PI * .2) * 20 + (1280 - 1280 * scale) / 2,
        Math.cos(t * 2 * Math.PI * .13) * 12 + (720 - 720 * scale) / 2, 1280 * scale, 720 * scale);
    }
  };
  setInterval(draw, 1000 / 30);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
    state.requests++; await ready; draw();
    const stream = source.captureStream(30); state.streams.push(stream); return stream;
  }});
});

const check = label => {report.checks.push(label); console.log(`PASS ${label}`);};
const screenshot = async (name, stage = false) => {
  const filename = path.join(output, `${name}.png`);
  if (stage) await page.locator('.stage').screenshot({path: filename});
  else await page.screenshot({path: filename, fullPage: true});
  report.screenshots.push(filename);
};
const layout = async name => {
  const result = await page.evaluate(() => ({width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
    elements: ['h1', '.stage', '#eyewear-select'].map(selector => {
      const rect = document.querySelector(selector).getBoundingClientRect();
      return {selector, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
    })}));
  assert.ok(result.scrollWidth <= result.width + 1, `${name}: page overflows horizontally`);
  for (const element of result.elements) {
    assert.ok(element.left >= -1 && element.right <= result.width + 1, `${name}: ${element.selector} escapes viewport`);
    assert.ok(element.width > 0 && element.height > 0, `${name}: ${element.selector} has no visible size`);
  }
  assert.equal(await page.locator('[id^="temple-preview"], [data-temple-preview], .temple-preview-buttons, #temple-test, #temple-mode, #fit-mode').count(), 0,
    `${name}: comparison UI remains in the document`);
  assert.doesNotMatch(await page.locator('body').innerText(), /temple preview|revision\s+\d+/i,
    `${name}: experimental revision label remains visible`);
  assert.equal(await page.title(), 'Lenses · AR');
  report.layouts.push({name, ...result});
  check(`${name}: ordinary AR interface without comparison controls or horizontal overflow`);
};
const serial = () => page.evaluate(() => window.__ar.samplesAfter(0).at(-1)?.serial ?? 0);
const waitTracked = async after => {
  await page.waitForFunction(after => {
    const rows = window.__ar.samplesAfter(after).filter(row => row.hasFace && row.hasMask && row.native?.['render.hairApplied']);
    return window.__ar.isOpen() && rows.length >= 8;
  }, after, {timeout: 120_000});
  const state = await page.evaluate(after => {
    const rows = window.__ar.samplesAfter(after).filter(row => row.hasFace && row.hasMask && row.native?.['render.hairApplied']);
    return {steady: window.__ar.config().steady, sample: rows.at(-1), frames: rows.length,
      requests: window.__syntheticCamera.requests, sessionId: document.querySelector('.stage').dataset.sessionId};
  }, after);
  assert.equal(state.steady.rotationBeta, .3); assert.equal(state.steady.depthBeta, .8);
  assert.equal(state.steady.derivativeCutoffHz, 3);
  assert.ok(Number.isFinite(state.sample.native['pose.steadyLagDeg']), 'responsive smoothing is active in the running pipeline');
  for (const field of ['templePreview', 'dropM', 'widthFit', 'continuity']) {
    assert.equal(Object.hasOwn(state.sample.native, `render.${field}`), false, `obsolete render metadata: ${field}`);
  }
  assert.ok(Math.abs(state.sample.native['render.armSpreadM'] - .018) < 1e-9, 'fixed 18 mm temple spread');
  assert.equal(state.sample.native['render.hairApplied'], true);
  assert.equal(state.sample.native['render.safeFallback'], false);
  const maximum = state.sample.native['render.templeEndMaximumZM'];
  assert.ok(Number.isFinite(maximum) && maximum < 0);
  for (const side of ['Negative', 'Positive']) {
    const endpoint = state.sample.native[`render.templeEnd${side}ZM`];
    assert.ok(Number.isFinite(endpoint) && endpoint >= maximum - 1e-9 && endpoint < 0, `${side}: bounded endpoint`);
  }
  assert.notEqual(state.sample.native['render.templeEndState'], 'off');
  return state;
};
const close = async () => {
  await page.locator('#stop').click();
  await page.waitForFunction(() => !window.__ar.isOpen()
    && window.__syntheticCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')));
};
const open = async (eyewear, label) => {
  await page.locator('#eyewear-select').selectOption(eyewear);
  const requestsBefore = await page.evaluate(() => window.__syntheticCamera.requests), after = await serial();
  await page.locator('#start').click();
  const state = await waitTracked(after);
  assert.equal(state.requests, requestsBefore + 1);
  assert.ok(state.sessionId);
  report.sessions.push({eyewear, label, ...state});
  await screenshot(`${label}-live`, true);
  check(`${label}: default fixed-shape frames with responsive tracking, hair and bounded endpoints`);
  return state;
};
const audit = async eyewear => {
  const result = await page.evaluate(async () => {
    const value = await window.__ar.audit();
    if (!value) return null;
    const {images, detection, ...report} = value;
    return report;
  });
  assert.ok(result, `${eyewear}: audit unavailable`);
  for (const field of ['templePreview', 'cut', 'widthFit', 'dropInsideProtected']) {
    assert.equal(Object.hasOwn(result, field), false, `${eyewear}: obsolete audit metadata: ${field}`);
  }
  assert.ok(result.templeEndpoint.fixedReturn, `${eyewear}: fixed return absent`);
  assert.equal(result.templeEndpoint.fixedReturn.startZM, -.075, `${eyewear}: longer fixed shaft absent`);
  assert.ok(result.templeEndpoint.fixedReturn.negativeInsetM > 0 && result.templeEndpoint.fixedReturn.positiveInsetM > 0);
  assert.ok(result.templeEndpoint.headFit, `${eyewear}: head fit metadata absent`);
  assert.equal(result.guard.safeFallback, false);
  assert.equal(result.hairApplied, true);
  assert.equal(result.checkError, null);
  assert.ok(result.checks);
  for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck']) {
    assert.equal(result.checks[key]?.changedPixels, 0, `${eyewear}: ${key}`);
  }
  report.audits.push({eyewear, ...result});
  check(`${eyewear}: fixed-return metadata and all four protection checks pass`);
};

try {
  await page.goto(`${base}/`, {waitUntil: 'load'});
  await page.waitForFunction(() => !!window.__ar);
  assert.equal(await page.evaluate(() => window.__syntheticCamera.requests), 0);
  assert.equal(await page.evaluate(() => window.__ar.isOpen()), false);
  const retiredApis = await page.evaluate(() => ['templePreview', 'setTemplePreview', 'fit', 'setFit', 'temples', 'setTemples']
    .filter(name => Object.hasOwn(window.__ar, name)));
  assert.deepEqual(retiredApis, [], 'Obsolete comparison APIs remain exposed');
  assert.equal(new URL(page.url()).search, '', 'The default page must not redirect into an experiment');
  check('Default AR starts idle without requesting a camera or exposing comparison APIs');
  await layout('Desktop 1440'); await screenshot('idle-desktop');
  await page.setViewportSize({width: 390, height: 844});
  await layout('Mobile 390'); await screenshot('idle-mobile');
  await page.setViewportSize({width: 1440, height: 1000});
  const first = await open('amber-horizon', 'amber');
  await audit('amber-horizon');
  await close();
  const restarted = await open('amber-horizon', 'amber-restarted');
  assert.notEqual(restarted.sessionId, first.sessionId);
  assert.equal(restarted.requests, first.requests + 1);
  await close();
  check('Close ends the synthetic stream; restart restores the default pipeline in a fresh session');
  await open('tom-ford-clear', 'tom-ford');
  await audit('tom-ford-clear');
  await close();
  assert.deepEqual(report.pageErrors, [], 'Uncaught browser errors');
  assert.deepEqual(report.shaderErrors, [], 'WebGL shader errors');
  check('Both shipped models finish without uncaught browser or shader errors');
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack ?? String(error);
  await screenshot('failure').catch(() => {});
  throw error;
} finally {
  await page.evaluate(() => window.__ar?.close()).catch(() => {});
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(`Saved ${path.join(output, 'report.json')}`);
}
