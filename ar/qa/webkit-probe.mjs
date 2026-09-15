// Opens the live /ar/ page in Playwright WebKit (iPhone emulation) with a synthetic camera and prints every console
// message, page error, failed request and the stage state over time.
import fs from 'node:fs/promises';
import {webkit, devices} from '@playwright/test';
const [base = 'https://web-production-ef3ca.up.railway.app/ar', seconds = '45', browserName = 'webkit'] = process.argv.slice(2);
const fixture = await fs.readFile(new URL('./fixtures/face-a.jpg', import.meta.url));
const browser = await webkit.launch({headless: true});
const context = await browser.newContext({...devices['iPhone 15'], permissions: ['camera']});
const page = await context.newPage();
const lines = [];
page.on('console', m => lines.push(`${(performance.now() / 1000).toFixed(1)}s console.${m.type()}: ${m.text().slice(0, 400)}`));
page.on('pageerror', e => lines.push(`${(performance.now() / 1000).toFixed(1)}s pageerror: ${e.message.slice(0, 400)}`));
page.on('requestfailed', r => lines.push(`${(performance.now() / 1000).toFixed(1)}s requestfailed: ${r.url().slice(0, 160)} ${r.failure()?.errorText ?? ''}`));
page.on('response', r => {if (r.status() >= 400) lines.push(`${(performance.now() / 1000).toFixed(1)}s http ${r.status()} ${r.url().slice(0, 160)}`);});
await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
await page.addInitScript(() => {
  const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
  const ctx = source.getContext('2d'), image = new Image();
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;}); image.src = '/ar-camera-fixture';
  const draw = () => {ctx.fillStyle = '#163949'; ctx.fillRect(0, 0, 1280, 720); if (image.complete && image.naturalWidth) ctx.drawImage(image, 0, 0, 1280, 720);};
  setInterval(draw, 33);
  const getUserMedia = async () => {await ready; draw(); if (typeof source.captureStream !== 'function') throw new Error('captureStream unavailable in this engine'); return source.captureStream(30);};
  if (navigator.mediaDevices) Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: getUserMedia});
  else Object.defineProperty(navigator, 'mediaDevices', {configurable: true, value: {getUserMedia, enumerateDevices: async () => []}});
});
const t0 = performance.now();
await page.goto(base + '/', {waitUntil: 'load'});
lines.push(`${((performance.now() - t0) / 1000).toFixed(1)}s loaded; UA ${await page.evaluate(() => navigator.userAgent)}`);
lines.push(`webgl2: ${await page.evaluate(() => {const c = document.createElement('canvas'); const gl = c.getContext('webgl2', {stencil: true}); if (!gl) return 'unavailable'; const d = gl.getExtension('WEBGL_debug_renderer_info'); return gl.getParameter(d ? d.UNMASKED_RENDERER_WEBGL : gl.RENDERER) + ' samples ' + gl.getParameter(gl.SAMPLES);})}`);
lines.push(`features: rVFC ${await page.evaluate(() => typeof HTMLVideoElement.prototype.requestVideoFrameCallback)} createImageBitmap ${await page.evaluate(() => typeof createImageBitmap)} crossOriginIsolated ${await page.evaluate(() => window.crossOriginIsolated)} randomUUID ${await page.evaluate(() => typeof crypto.randomUUID)}`);
await page.click('#start');
const deadline = performance.now() + Number(seconds) * 1000;
let last = '';
while (performance.now() < deadline) {
  const state = await page.evaluate(() => `${document.querySelector('.stage')?.dataset.state} | ${document.getElementById('stage-status')?.textContent} | ${document.getElementById('guidance')?.textContent} | rows ${window.__ar?.samplesAfter(0).length ?? '?'} | hair ${document.getElementById('hair-engine')?.textContent} | gpu ${document.getElementById('gpu')?.textContent}`);
  if (state !== last) {lines.push(`${((performance.now() - t0) / 1000).toFixed(1)}s STATE ${state}`); last = state;}
  if (/tracking|searching|error/.test(state) && (window_rows(state) >= 20 || /error/.test(state))) break;
  await new Promise(r => setTimeout(r, 500));
}
function window_rows(state) {const m = /rows (\d+)/.exec(state); return m ? Number(m[1]) : 0;}
for (const line of lines) console.log(line);
await browser.close();
