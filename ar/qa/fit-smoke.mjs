/** Automatic glasses fit, live controls and wearer/session boundaries on a synthetic camera.
 * node qa/fit-smoke.mjs [--base=http://127.0.0.1:8247] [--out=qa/output/fit-preview]
 * Checks hidden calibration, fitted audits and controls for both shipped models; no real camera is requested. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url)), args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8247').replace(/\/$/, '');
const output = path.resolve(option('out', path.join(here, 'output', 'fit-preview')));
assert.equal(new URL(base).search, '', 'Use the normal page without query overrides');
await fs.mkdir(output, {recursive: true});
const fixture = await fs.readFile(path.join(here, 'fixtures', 'face-a.jpg'));
const report = {schema: 'ar-fit-smoke-v1', createdAt: new Date().toISOString(), base, status: 'running', checks: [],
  layouts: [], sessions: [], stability: [], adjustments: [], audits: [], hiddenFrames: [], screenshots: [], console: [], pageErrors: [], shaderErrors: [],
  limits: ['The camera is a stream of the checked-in face-a still. No real camera is accessed.',
    'The test preserves the mirror drawing buffer to read calibration pixels; normal live rendering does not read pixels.',
    'Mobile checks resize a desktop browser viewport; they do not establish mobile performance or compatibility.',
    'Calibration and geometry checks do not establish measured physical fit or real-wearer appearance.']};
const browser = await chromium.launch({headless: true, channel: 'chromium', args: [
  '--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required',
]});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
page.setDefaultTimeout(30_000);
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('console', message => {
  const text = message.text();
  if (['warning', 'error'].includes(message.type())) report.console.push({type: message.type(), text});
  if ((message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(text))
    || /VALIDATE_STATUS.*false|shader.*(?:failed to compile|compilation failed)|ERROR:\s*\d+:\d+:/i.test(text)) report.shaderErrors.push(text);
});
await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
await page.addInitScript(() => {
  const getContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, options, ...rest) {
    return getContext.call(this, type, type === 'webgl2' && this.id === 'mirror' ? {...options, preserveDrawingBuffer: true} : options, ...rest);
  };
  const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
  const context = source.getContext('2d'), image = new Image();
  const state = {requests: 0, streams: [], requestActiveStreams: [], peakActiveStreams: 0}; window.__syntheticCamera = state;
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;}); image.src = '/ar-camera-fixture';
  const liveCount = () => state.streams.filter(stream => stream.getTracks().some(track => track.readyState === 'live')).length;
  const draw = () => {if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, source.width, source.height);};
  setInterval(draw, 1000 / 30);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
    state.requests++; state.requestActiveStreams.push(liveCount()); await ready; draw();
    const stream = source.captureStream(30); state.streams.push(stream); state.peakActiveStreams = Math.max(state.peakActiveStreams, liveCount()); return stream;
  }});
});
const check = label => {report.checks.push(label); console.log(`PASS ${label}`);};
const snapshot = () => page.evaluate(() => ({fitting: window.__ar.fitting(), sessionId: document.querySelector('.stage').dataset.sessionId,
  open: window.__ar.isOpen(), eyewear: document.querySelector('#eyewear-select').value, requests: window.__syntheticCamera.requests,
  streams: window.__syntheticCamera.streams.map(stream => stream.getTracks().map(track => track.readyState)),
  requestActiveStreams: window.__syntheticCamera.requestActiveStreams.slice(), peakActiveStreams: window.__syntheticCamera.peakActiveStreams}));
const screenshot = async (name, selector = null) => {
  const filename = path.join(output, `${name}.png`);
  if (selector) await page.locator(selector).screenshot({path: filename}); else await page.screenshot({path: filename, fullPage: true});
  report.screenshots.push(filename);
};
const layout = async name => {
  const result = await page.evaluate(() => ({width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
    elements: ['.stage', '#eyewear-select', '.fit-controls', '#fit-adjustment', '#fit-refit'].map(selector => {
      const rect = document.querySelector(selector).getBoundingClientRect(); return {selector, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
    })}));
  report.layouts.push({name, ...result});
  assert.ok(result.scrollWidth <= result.width + 1, `${name}: horizontal overflow`);
  for (const item of result.elements) assert.ok(item.left >= -1 && item.right <= result.width + 1 && item.width > 0 && item.height > 0, `${name}: ${item.selector} is clipped`);
  await screenshot(name); check(`${name}: fit controls remain visible without horizontal overflow`);
};
const waitFitted = async (label, afterSerial = 0) => {
  await page.waitForFunction(after => {const fit = window.__ar.fitting(), row = window.__ar.samplesAfter(after).at(-1);
    return fit?.state === 'fitted' && fit.ready && fit.faceWidthCm > 0 && Number.isFinite(fit.scale)
      && row?.hasFace && row.sessionId === document.querySelector('.stage').dataset.sessionId;
  }, afterSerial, {timeout: 120_000});
  const value = await snapshot(); report.sessions.push({label, ...value});
  assert.equal(value.fitting.progress, 1, `${label}: completed fit progress`); return value;
};
const stable = async (eyewear, expectedWidth) => {
  const rows = await page.evaluate(async () => {
    const rows = [], deadline = performance.now() + 60_000;
    let serial = window.__ar.samplesAfter(0).at(-1)?.serial ?? 0;
    while (rows.length < 20 && performance.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 50));
      const latest = window.__ar.samplesAfter(serial).at(-1);
      if (latest) {serial = latest.serial; rows.push({serial, ...window.__ar.fitting()});}
    }
    return rows;
  });
  assert.equal(rows.length, 20, `${eyewear}: insufficient fitted frames`);
  for (const row of rows) {assert.equal(row.faceWidthCm, expectedWidth); assert.equal(row.state, 'fitted'); assert.equal(row.ready, true);}
  const scales = rows.map(row => row.scale), range = Math.max(...scales) - Math.min(...scales);
  assert.ok(range <= .001, `${eyewear}: locked size moves by ${range}`);
  report.stability.push({eyewear, scaleRange: range, rows}); check(`${eyewear}: calibrated width and scale remain stable across 20 new frames`);
};
const adjust = async (value, expectedWidth, label) => {
  const serial = await page.evaluate(() => window.__ar.samplesAfter(0).at(-1)?.serial ?? 0);
  await page.evaluate(value => window.__ar.setFitAdjustment(value), value);
  const immediate = await snapshot(); assert.equal(immediate.fitting.adjustment, value); assert.equal(immediate.fitting.faceWidthCm, expectedWidth);
  assert.equal(immediate.fitting.ready, true, `${label}: manual adjustment must keep the glasses visible`);
  const settled = await waitFitted(label, serial); assert.equal(settled.fitting.faceWidthCm, expectedWidth); assert.equal(settled.fitting.adjustment, value);
  assert.equal(settled.sessionId, immediate.sessionId); assert.equal(settled.requests, immediate.requests);
  report.adjustments.push({label, requested: value, ...settled}); return settled;
};
const pendingHiddenFrames = [];
let cleanCamera = null;
const compareHiddenFrames = async () => {
  if (!cleanCamera) return;
  for (const entry of pendingHiddenFrames.splice(0)) {
    const difference = await page.evaluate(async ({actual, expected}) => {
      const decode = async url => {
        const image = new Image(); image.src = url; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
        return {width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data};
      };
      const [a, b] = await Promise.all([decode(actual), decode(expected)]);
      let differentPixels = 0, maxDelta = 0;
      for (let index = 0; index < a.data.length; index += 4) {
        const delta = Math.max(...[0, 1, 2].map(channel => Math.abs(a.data[index + channel] - b.data[index + channel])));
        if (delta > 0) differentPixels++;
        maxDelta = Math.max(maxDelta, delta);
      }
      return {width: a.width, height: a.height, expectedWidth: b.width, expectedHeight: b.height, differentPixels, maxDelta};
    }, {actual: entry.pixels, expected: cleanCamera});
    const {pixels, ...metadata} = entry; report.hiddenFrames.push({...metadata, difference});
    assert.equal(difference.width, difference.expectedWidth); assert.equal(difference.height, difference.expectedHeight);
    assert.equal(difference.differentPixels, 0, `${entry.label}: pending fit differs from the clean camera`);
    check(`${entry.label}: glasses, shadows and hair composition stay hidden during ${entry.fitting.state}`);
  }
};
const hiddenFrame = async (label, state, afterSerial = 0) => {
  // A real rendered tracked frame is required after Refit; reading immediately could
  // otherwise inspect the preceding fitted frame while the next pose is still pending.
  await page.waitForFunction(({state, afterSerial}) => {
    const fit = window.__ar.fitting(), row = window.__ar.samplesAfter(afterSerial).at(-1);
    return fit?.state === state && !fit.ready && row?.hasFace && row.sessionId === document.querySelector('.stage').dataset.sessionId;
  }, {state, afterSerial}, {polling: 10, timeout: 120_000});
  const value = await page.evaluate(() => ({fitting: window.__ar.fitting(), row: window.__ar.samplesAfter(0).at(-1),
    pixels: document.querySelector('#mirror').toDataURL('image/png')}));
  assert.equal(value.fitting.state, state, `${label}: capture missed the requested fitting phase`);
  assert.equal(value.fitting.ready, false, `${label}: incomplete fitting must remain hidden`);
  assert.equal(value.row.hasMask, false, `${label}: hair composite ran before glasses were ready`);
  const filename = path.join(output, `${label}.png`);
  await fs.writeFile(filename, Buffer.from(value.pixels.split(',')[1], 'base64')); report.screenshots.push(filename);
  pendingHiddenFrames.push({label, ...value}); await compareHiddenFrames();
};
const audit = async label => {
  await page.waitForFunction(() => {const last = window.__ar.samplesAfter(0).at(-1); return last?.hasFace && last?.hasMask;}, null, {timeout: 120_000});
  const result = await page.evaluate(async () => {const audit = await window.__ar.audit(); if (!audit) return null; const {images, detection, ...report} = audit;
    return {report, cleanCamera: images.backgroundPngDataUrl};});
  const value = result?.report;
  assert.ok(value, `${label}: missing audit`); report.audits.push({label, ...value});
  cleanCamera = result.cleanCamera; await compareHiddenFrames();
  assert.equal(value.fitting.ready, true, `${label}: completed fit must reveal glasses`);
  assert.equal(value.checkError, null, `${label}: audit check failure`);
  for (const name of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck']) assert.equal(value.checks?.[name]?.changedPixels, 0, `${label}: ${name}`);
  assert.equal(value.guard.safeFallback, false); assert.equal(value.hairApplied, true);
  assert.equal(value.shadows?.applied, true); assert.deepEqual(value.shadows.settings, {enabled: true, frameStrength: .16, lensStrength: .18, softness: 1.9});
  await screenshot(label, '.stage'); check(`${label}: four audit protections pass with the normal shadows enabled`);
};
const close = async () => {
  await page.locator('#stop').click();
  await page.waitForFunction(() => !window.__ar.isOpen() && window.__syntheticCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')));
  assert.equal(await page.evaluate(() => window.__ar.fitting()), null);
};

try {
  await page.goto(`${base}/`, {waitUntil: 'load'}); await page.waitForFunction(() => !!window.__ar?.fitting);
  assert.equal((await snapshot()).requests, 0); assert.equal(await page.locator('#fit-adjustment').isDisabled(), true);
  await layout('idle-desktop'); await page.setViewportSize({width: 390, height: 844}); await layout('idle-mobile');
  await page.setViewportSize({width: 1440, height: 1000});
  await page.locator('#eyewear-select').selectOption('amber-horizon'); await page.locator('#start').click();
  await hiddenFrame('amber-collecting', 'collecting');
  await hiddenFrame('amber-settling', 'settling');
  const amber = await waitFitted('amber-initial'), width = amber.fitting.faceWidthCm;
  assert.equal(amber.fitting.adjustment, 0); assert.equal(await page.locator('#eyewear-select').isDisabled(), false);
  await stable('amber-horizon', width); await audit('amber-fitted');
  await layout('fitted-desktop'); await page.setViewportSize({width: 390, height: 844}); await layout('fitted-mobile');
  await page.setViewportSize({width: 1440, height: 1000});
  // Exercise the actual slider at its upper limit, then its lower limit through the public API.
  const beforeAdjustment = await page.evaluate(() => window.__ar.samplesAfter(0).at(-1)?.serial ?? 0);
  await page.locator('#fit-adjustment').focus(); await page.locator('#fit-adjustment').press('End');
  assert.equal((await snapshot()).fitting.ready, true, 'The size slider must not hide an already fitted frame');
  const amberPlus = await waitFitted('amber-plus-eight', beforeAdjustment);
  assert.equal(amberPlus.fitting.adjustment, .08); assert.equal(amberPlus.fitting.faceWidthCm, width);
  assert.ok(amberPlus.fitting.scale > amber.fitting.scale, 'Positive adjustment must enlarge the rendered frame');
  await audit('amber-plus-eight');
  const amberMinus = await adjust(-.08, width, 'amber-minus-eight');
  assert.ok(amberMinus.fitting.scale < amber.fitting.scale, 'Negative adjustment must shrink the rendered frame');
  const preserved = await adjust(.04, width, 'amber-before-switch');
  check('Manual sizing works in both directions without recalibrating the face or reopening the camera');

  await page.locator('#eyewear-select').selectOption('tom-ford-clear');
  await page.waitForFunction(old => document.querySelector('.stage').dataset.sessionId !== old && !!window.__ar.fitting(), preserved.sessionId, {timeout: 120_000});
  const switched = await snapshot();
  assert.equal(switched.fitting.faceWidthCm, width, 'Model switch must immediately retain the locked face width');
  assert.equal(switched.fitting.adjustment, .04, 'Model switch must retain manual adjustment');
  assert.equal(switched.requests, preserved.requests + 1);
  assert.ok(switched.streams.slice(0, -1).every(tracks => tracks.every(state => state === 'ended')));
  assert.equal(switched.peakActiveStreams, 1); assert.ok(switched.requestActiveStreams.every(count => count === 0));
  report.sessions.push({label: 'model-switch-immediate', ...switched});
  check('Switching glasses reuses wearer calibration and adjustment after stopping the previous stream');
  await waitFitted('clear-preserved');
  await adjust(0, width, 'clear-automatic'); await stable('tom-ford-clear', width); await audit('clear-fitted');
  await adjust(.08, width, 'clear-plus-eight'); await audit('clear-plus-eight');

  const beforeRefit = await page.evaluate(() => window.__ar.samplesAfter(0).at(-1)?.serial ?? 0);
  const reset = await page.evaluate(() => {window.__ar.refit(); return window.__ar.fitting();});
  assert.equal(reset.state, 'collecting'); assert.equal(reset.faceWidthCm, null); assert.equal(reset.adjustment, 0); assert.equal(reset.progress, 0);
  assert.equal(reset.ready, false, 'Refit must hide the previous fit');
  await hiddenFrame('clear-refit-collecting', 'collecting', beforeRefit);
  await hiddenFrame('clear-refit-settling', 'settling', beforeRefit);
  const refitted = await waitFitted('clear-refitted');
  assert.equal(refitted.sessionId, switched.sessionId); assert.equal(refitted.requests, switched.requests);
  check('Refit clears calibration and manual adjustment, then calibrates again in the same session');

  await adjust(.03, refitted.fitting.faceWidthCm, 'clear-before-close'); await close();
  const fresh = await page.evaluate(async () => {await window.__ar.open(); return window.__ar.fitting();});
  assert.ok(fresh); assert.equal(fresh.state, 'collecting'); assert.equal(fresh.faceWidthCm, null); assert.equal(fresh.adjustment, 0);
  assert.equal(fresh.ready, false, 'A fresh camera session must start with glasses hidden');
  await hiddenFrame('clear-reopened-collecting', 'collecting');
  const restarted = await waitFitted('clear-new-wearer'); assert.notEqual(restarted.sessionId, refitted.sessionId);
  await close(); check('Explicit Close/Open starts a fresh fit and clears the previous wearer adjustment');
  assert.deepEqual(report.pageErrors, [], 'Uncaught browser errors'); assert.deepEqual(report.shaderErrors, [], 'WebGL/shader errors');
  check('Both fitted models finish without uncaught browser or shader errors'); report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack ?? String(error);
  report.failureState = await snapshot().catch(() => null);
  await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true}).catch(() => {}); throw error;
} finally {
  await page.evaluate(() => window.__ar?.close()).catch(() => {});
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close(); console.log(`Saved ${path.join(output, 'report.json')}`);
}
