/** Opens the page with the synthetic camera and prints every console message (no filter) for a few seconds.
 *    node qa/gl-probe.mjs [base=http://127.0.0.1:8241] [seconds=12] [query] */
import fs from 'node:fs/promises';
import {chromium} from '@playwright/test';
const [base = 'http://127.0.0.1:8241', seconds = '12', query = 'eyewear=amber-horizon&hairModel=hair-only'] = process.argv.slice(2);
const fixture = await fs.readFile(new URL('./fixtures/face-a.jpg', import.meta.url));
const browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required']});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
const lines = new Map();
page.on('console', message => {const key = `${message.type()}: ${message.text().slice(0, 300)}`; lines.set(key, (lines.get(key) ?? 0) + 1);});
page.on('pageerror', error => {const key = `pageerror: ${error.message.slice(0, 300)}`; lines.set(key, (lines.get(key) ?? 0) + 1);});
await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
await page.addInitScript(() => {
  const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
  const context = source.getContext('2d'), image = new Image();
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;}); image.src = '/ar-camera-fixture';
  const draw = () => {context.fillStyle = '#163949'; context.fillRect(0, 0, 1280, 720); if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, 1280, 720);};
  setInterval(draw, 33);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {await ready; draw(); return source.captureStream(30);}});
});
await page.goto(`${base}/?${query}`, {waitUntil: 'load'});
await page.waitForFunction(() => !!window.__ar);
await page.evaluate(() => window.__ar.open());
await new Promise(resolve => setTimeout(resolve, Number(seconds) * 1000));
const rows = await page.evaluate(() => window.__ar.samplesAfter(0));
console.log(`rows ${rows.length}, tracked ${rows.filter(r => r.hasFace).length}, masked ${rows.filter(r => r.hasMask).length}, last native: ${JSON.stringify(rows.at(-1)?.native ?? {})}`);
for (const [line, count] of [...lines.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15)) console.log(`${count}x ${line}`);
await page.evaluate(() => window.__ar.close());
await browser.close();
