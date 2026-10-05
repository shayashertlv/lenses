/** Normal webcam-app handover smoke, with a synthetic camera only.
 * node qa/astra-preview-smoke.mjs --base=http://127.0.0.1:8260
 * No server is started, real camera requested, model changed or API called.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const option = (name, fallback) => process.argv.find(v => v.startsWith(`--${name}=`))?.slice(name.length+3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8260').replace(/\/$/, '');
const origin = new URL(base);
assert.ok(origin.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname));
assert.equal(origin.pathname, '/'); assert.equal(origin.search, '');
const output = path.resolve(option('out', path.join(here, 'output/astra-preview-v1/smoke')));
await fs.mkdir(output, {recursive: true});
assert.equal((await fs.readdir(output)).length, 0, 'Use a fresh smoke output directory');
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fixture = await fs.readFile(path.join(here, 'fixtures/face-a.jpg'));
const report = {schema: 'astra-preview-webcam-smoke-v1', created_at: new Date().toISOString(),
  base, status: 'running', fixture_sha256: digest(fixture), cases: [], page_errors: [], console: [],
  shader_errors: [], failed_responses: [], blocked_requests: [], screenshots: [],
  limits: ['The camera is the checked-in face-a still streamed from a canvas. No real camera is accessed.',
    'Normal page startup, canonical assets, tracking, fitting and rendered pixels are checked.',
    'This is not a real wearer, photographic quality verdict, motion test or mobile-performance measurement.']};
let browser, page;
const pendingResponses = [];
const assetResponses = new Map();
const get = async url => {
  assert.equal(new URL(url).origin, origin.origin, 'Only the private preview origin is fetched');
  const response = await fetch(url, {redirect: 'error'});
  assert.equal(response.status, 200, `HTTP ${response.status}: ${url}`);
  return Buffer.from(await response.arrayBuffer());
};
const saveImage = async (name, bytes) => {
  const filename = path.join(output, name); await fs.writeFile(filename, bytes);
  const pin = {path: filename, sha256: digest(bytes), bytes: bytes.length};
  report.screenshots.push(pin); return pin;
};
try {
  const manifestBytes = await get(`${base}/preview/manifest.json`);
  const manifest = JSON.parse(manifestBytes);
  await fs.writeFile(path.join(output, 'manifest.json'), manifestBytes);
  report.manifest_sha256 = digest(manifestBytes);
  const cases = ['miu-final', 'oakley-final'].map(id => {
    const entries = manifest.cases.filter(row => row.id === id);
    assert.equal(entries.length, 1, `Expected one ${id} manifest case`); return entries[0];
  });
  browser = await chromium.launch({headless: true, channel: 'chromium', args: [
    '--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required',
  ]});
  // No camera permission is granted. The page must prove its getUserMedia
  // replacement installed successfully before any Open camera button is used.
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(30_000);
  page.on('pageerror', error => report.page_errors.push(error.message));
  page.on('console', message => {
    const text = message.text();
    if (['warning', 'error'].includes(message.type())) report.console.push({type: message.type(), text: text.slice(0, 1800)});
    if ((message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(text))
        || /VALIDATE_STATUS.*false|shader.*(?:failed to compile|compilation failed)|ERROR:\s*\d+:\d+:/i.test(text)) {
      report.shader_errors.push(text.slice(0, 4000));
    }
  });
  page.on('response', response => {
    if (response.status() >= 400) report.failed_responses.push({url: response.url(), status: response.status()});
    if (cases.some(entry => new URL(entry.asset_url, base).href === response.url())) {
      pendingResponses.push(response.body().then(bytes => assetResponses.set(response.url(),
        {sha256: digest(bytes), bytes: bytes.length, status: response.status()})));
    }
  });
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url === `${base}/ar-camera-fixture`) return route.fulfill({contentType: 'image/jpeg', body: fixture});
    if (url.startsWith(origin.origin+'/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
    report.blocked_requests.push(url); return route.abort();
  });
  await page.addInitScript(() => {
    const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
    const context = source.getContext('2d'), image = new Image();
    const state = {installed: false, requests: 0, streams: [], constraints: []}; window.__syntheticCamera = state;
    const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
    image.src = '/ar-camera-fixture';
    const draw = () => {if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, 1280, 720);};
    setInterval(draw, 1000/30);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async constraints => {
      state.requests++; state.constraints.push(constraints); await ready; draw();
      const stream = source.captureStream(30); state.streams.push(stream); return stream;
    }});
    state.installed = true;
  });
  for (const entry of cases) {
    const url = new URL(entry.url, base);
    assert.equal(url.origin, origin.origin);
    const assetUrl = new URL(url.searchParams.get('model'), origin.origin).href;
    assert.equal(assetUrl, new URL(entry.asset_url, base).href);
    assert.match(entry.sha256, /^[a-f0-9]{64}$/);
    assert.equal(url.searchParams.get('sha256'), entry.sha256);
    assert.equal(Number(url.searchParams.get('clip')), -.14);
    assert.equal(Number(url.searchParams.get('width')), 145);
    assert.equal(url.searchParams.has('lensenv'), false, 'Use the canonical default environment strength');
    const raw = await get(assetUrl); assert.equal(digest(raw), entry.sha256, `${entry.id}: served asset pin`);
    assert.equal(raw.readUInt32LE(0), 0x46546c67); assert.equal(raw.readUInt32LE(8), raw.length);
    const doc = JSON.parse(raw.subarray(20, 20+raw.readUInt32LE(12)).toString('utf8'));
    assert.ok(doc.extensionsUsed?.includes('LENSES_lens_appearance'), 'Canonical optical descriptor required');
    assert.ok(doc.extras?.effectiveOpticalGroups, 'Effective optical groups required');
    const row = {id: entry.id, name: entry.name, url: url.href, asset_url: assetUrl,
      sha256: entry.sha256, bytes: raw.length, optical_materials: doc.materials.filter(m => m.extensions?.LENSES_lens_appearance).length,
      status: 'loading'};
    report.cases.push(row);
    console.log(`START ${entry.id}: normal app synthetic webcam`);
    await page.goto(url.href, {waitUntil: 'load'});
    await page.waitForFunction(() => !!window.__ar?.fitting);
    row.initial = await page.evaluate(() => ({secure: isSecureContext, isolated: crossOriginIsolated,
      camera_stub: window.__syntheticCamera.installed, requests: window.__syntheticCamera.requests,
      open: window.__ar.isOpen(), external: document.querySelector('.stage').dataset.externalModel,
      name: document.querySelector('#frame-name').textContent}));
    assert.equal(row.initial.secure, true); assert.equal(row.initial.camera_stub, true);
    assert.equal(row.initial.requests, 0); assert.equal(row.initial.open, false);
    assert.equal(row.initial.external, 'ready');
    assert.equal(row.initial.name, entry.name);
    await page.locator('#start').click();
    await page.waitForFunction(() => {
      const fit = window.__ar.fitting(), sample = window.__ar.samplesAfter(0).at(-1);
      return window.__ar.isOpen() && fit?.state === 'fitted' && fit.ready && fit.faceWidthCm > 0
        && Number.isFinite(fit.scale) && sample?.hasFace && sample.hasMask
        && sample.sessionId === document.querySelector('.stage').dataset.sessionId;
    }, null, {timeout: 180_000});
    row.fitted = await page.evaluate(() => ({fitting: window.__ar.fitting(), sample: window.__ar.samplesAfter(0).at(-1),
      requests: window.__syntheticCamera.requests, constraints: window.__syntheticCamera.constraints,
      state: document.querySelector('.stage').dataset.state, guidance: document.querySelector('#guidance').textContent}));
    assert.equal(row.fitted.requests, 1); assert.equal(row.fitted.constraints[0].audio, false);
    assert.equal(row.fitted.fitting.progress, 1);
    assert.equal(row.fitted.sample.native['render.safeFallback'], false);
    // The ordinary public audit draws the exact held camera frame with and
    // without glasses. Removing shadows isolates actual visible eyewear pixels.
    const audit = await page.evaluate(async () => {
      const value = await window.__ar.audit(); if (!value) return null;
      const decode = async url => {
        const image = new Image(); image.src = url; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
        return context.getImageData(0, 0, canvas.width, canvas.height);
      };
      const [withGlasses, without] = await Promise.all([decode(value.images.noShadowPngDataUrl), decode(value.images.backgroundPngDataUrl)]);
      let visiblePixelsOver8 = 0;
      for (let i=0; i<withGlasses.data.length; i+=4) {
        if (Math.max(...[0,1,2].map(c => Math.abs(withGlasses.data[i+c]-without.data[i+c]))) > 8) visiblePixelsOver8++;
      }
      const {images, detection, ...metadata} = value;
      return {metadata, visiblePixelsOver8, images: {render: images.afterPngDataUrl,
        no_shadow: images.noShadowPngDataUrl, clean_camera: images.backgroundPngDataUrl}};
    });
    assert.ok(audit, `${entry.id}: audit produced no held frame`);
    row.audit = audit.metadata; row.visible_pixels_over8_without_shadows = audit.visiblePixelsOver8;
    assert.ok(audit.visiblePixelsOver8 > 100, `${entry.id}: glasses must change visible pixels without shadows`);
    assert.equal(row.audit.fitting.ready, true); assert.equal(row.audit.guard.safeFallback, false);
    assert.equal(row.audit.checkError, null);
    row.images = {};
    for (const [kind, data] of Object.entries(audit.images)) {
      row.images[kind] = await saveImage(`${entry.id}-${kind}.png`, Buffer.from(data.split(',')[1], 'base64'));
    }
    row.images.page = await saveImage(`${entry.id}-page.png`, await page.screenshot({fullPage: true}));
    await Promise.all(pendingResponses);
    row.browser_asset = assetResponses.get(assetUrl);
    assert.equal(row.browser_asset?.sha256, entry.sha256, `${entry.id}: actual browser fetch pin`);
    await page.locator('#stop').click();
    await page.waitForFunction(() => !window.__ar.isOpen()
      && window.__syntheticCamera.streams.every(s => s.getTracks().every(t => t.readyState === 'ended')));
    row.closed = await page.evaluate(() => ({open: window.__ar.isOpen(), fitting: window.__ar.fitting(),
      streams: window.__syntheticCamera.streams.map(s => s.getTracks().map(t => t.readyState))}));
    assert.equal(row.closed.fitting, null); row.status = 'passed';
    console.log(`PASS ${entry.id}: SHA pinned, fitted, visible, streams closed`);
  }
  assert.deepEqual(report.page_errors, []); assert.deepEqual(report.shader_errors, []);
  assert.deepEqual(report.blocked_requests, []); assert.deepEqual(report.failed_responses, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack ?? String(error);
  if (page) {
    report.failure_state = await page.evaluate(() => ({fitting: window.__ar?.fitting(),
      status: document.querySelector('#stage-status')?.textContent, guidance: document.querySelector('#guidance')?.textContent,
      samples: window.__ar?.samplesAfter(0).slice(-3)})).catch(() => null);
    await saveImage('failure.png', await page.screenshot({fullPage: true})).catch(() => {});
  }
  process.exitCode = 1;
} finally {
  await page?.evaluate(() => {window.__ar?.close(); for (const s of window.__syntheticCamera?.streams ?? []) for (const t of s.getTracks()) t.stop();}).catch(() => {});
  await Promise.allSettled(pendingResponses);
  await browser?.close();
  report.completed_at = new Date().toISOString();
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)+'\n');
  console.log(`Saved ${path.join(output, 'report.json')}: ${report.status}`);
}
