import assert from 'node:assert/strict';
import {mkdtemp, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {chromium} from '@playwright/test';
import {createServer} from 'vite';

test('GPU crystal twin samples transmission and thickness maps and blends authored opacity', async t => {
  const work = await mkdtemp(path.join(os.tmpdir(), 'arview-twin-map-'));
  const root = fileURLToPath(new URL('../', import.meta.url));
  let server, browser;
  t.after(async () => {await browser?.close(); await server?.close(); assert.equal(path.dirname(work), os.tmpdir()); await rm(work, {recursive: true, force: true});});
  server = await createServer({configFile: false, root, publicDir: false, cacheDir: path.join(work, '.vite'), logLevel: 'error',
    server: {host: '127.0.0.1', port: 0, hmr: false, watch: null}, optimizeDeps: {entries: [], include: ['three']}});
  await server.listen();
  browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/qa/twin-map-proof.html`);
  await page.waitForFunction(() => typeof globalThis.twinMapProof === 'function');
  const pixels = await page.evaluate(() => globalThis.twinMapProof());
  assert.deepEqual(errors, [], errors.join('\n'));
  assert.ok(pixels.transmission[0] < 10 && pixels.transmission[1] > 240, JSON.stringify(pixels));
  assert.ok(pixels.thickness[0] > pixels.thickness[1] + 80, JSON.stringify(pixels));
  assert.ok(pixels.opaque[0] > pixels.alpha[0] + 80 && pixels.alpha[0] > 100, JSON.stringify(pixels));
  t.diagnostic(JSON.stringify(pixels));
});
