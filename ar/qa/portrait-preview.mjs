/** Exact native GLB on the approved synthetic portrait, using the actual AR renderer. No camera/API/publish. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {consoleLevel, validatePortraitManifest} from './portrait-preview-contract.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = name => process.argv.find(v => v.startsWith(`--${name}=`))?.slice(name.length + 3);
assert(option('manifest') && option('output'), 'Explicit manifest and output paths required');
const manifestPath = path.resolve(option('manifest')), output = path.resolve(option('output'));
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const original = await fs.readFile(manifestPath), manifest = validatePortraitManifest(JSON.parse(original));
const bytes = await fs.readFile(path.resolve(path.dirname(manifestPath), manifest.glb));
assert.equal(digest(bytes), manifest.model_sha256, 'Input GLB changed');
assert(bytes.length >= 20 && bytes.readUInt32LE(0) === 0x46546c67 && bytes.readUInt32LE(4) === 2, 'Expected GLB v2');
assert.equal(bytes.readUInt32LE(8), bytes.length);
assert.equal(bytes.readUInt32LE(16), 0x4e4f534a);
const doc = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));
for (const resource of [...doc.buffers ?? [], ...doc.images ?? []]) assert(!resource.uri || resource.uri.startsWith('data:'), 'External model resources are not supported');
const fixturePath = path.join(root, 'qa/fixtures/face-a.jpg'), fixture = await fs.readFile(fixturePath);
assert.equal(digest(fixture), manifest.fixture.sha256, 'Portrait fixture changed');
const routes = new Map([
  ['/__portrait/manifest.json', [Buffer.from(JSON.stringify(manifest)), 'application/json']],
  ['/__portrait/candidate.glb', [bytes, 'model/gltf-binary']],
  ['/__portrait/face-a.jpg', [fixture, 'image/jpeg']],
]);
async function snapshot() {
  const todo = ['qa/portrait-preview.mjs', 'qa/portrait-preview.html', 'qa/portrait-preview-contract.mjs',
    'qa/provider-comparison-lighting.mjs', 'package.json', 'src/render/renderer.ts', 'src/face/detector.ts',
    'src/face/detector.worker.ts', 'src/hair/client.ts', 'src/hair/worker.ts', 'src/eyewear/catalog.ts',
    'src/render/continuity.ts', 'public/models/canonical-face.json', 'public/models/face_landmarker.task',
    'public/models/hair/hair-only.tflite'], hashes = {};
  try {await fs.access(path.join(root, 'package-lock.json')); todo.push('package-lock.json');}
  catch (error) {if (error.code !== 'ENOENT') throw error;}
  while (todo.length) {
    const name = todo.pop(); if (name in hashes) continue;
    const data = await fs.readFile(path.join(root, name)); hashes[name] = digest(data);
    if (!/\.(?:mjs|ts|html)$/.test(name)) continue;
    for (const match of data.toString('utf8').matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*)['"](\.{1,2}\/[^'"]+)['"]/g)) {
      const target = path.posix.normalize(path.posix.join(path.posix.dirname(name), match[1]));
      if (target.startsWith('src/') || target.startsWith('qa/')) todo.push(target);
    }
  }
  return Object.fromEntries(Object.entries(hashes).sort(([a], [b]) => a.localeCompare(b)));
}
const before = await snapshot(), errors = [], warnings = [], informational = [], failedResponses = [], blockedExternal = [];
await fs.mkdir(output, {recursive: true});
try {await fs.access(path.join(output, 'report.json')); throw Error('Use a fresh portrait output directory');}
catch (error) {if (error.code !== 'ENOENT') throw error;}
const server = await createServer({configFile: false, root, cacheDir: path.join(root, 'node_modules/.vite-portrait-preview'),
  worker: {format: 'es'}, server: {host: '127.0.0.1', port: 0, hmr: false}, plugins: [{
    name: 'pinned-portrait-inputs', configureServer(server) {server.middlewares.use((req, res, next) => {
      const url = new URL(req.url ?? '/', 'http://127.0.0.1');
      if (!url.pathname.startsWith('/__portrait/')) return next();
      const record = routes.get(url.pathname); if (!record) {res.statusCode = 404; res.end(); return;}
      res.setHeader('Content-Type', record[1]); res.setHeader('Cache-Control', 'no-store'); res.end(record[0]);
    });},
  }]});
let browser;
try {
  await server.listen(); const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
  const page = await browser.newPage({viewport: {width: 1280, height: 1280}}); page.setDefaultTimeout(180000);
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(origin + '/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
    blockedExternal.push(url); return route.abort();
  });
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    const level = consoleLevel(message.type(), message.text());
    if (level === 'error') errors.push(message.text());
    if (level === 'warning') warnings.push(message.text());
    if (level === 'info') informational.push(message.text());
  });
  page.on('response', response => {if (response.status() >= 400) failedResponses.push({url: response.url(), status: response.status()});});
  await page.goto(origin + '/qa/portrait-preview.html');
  await page.waitForFunction(() => !!window.portraitPreview);
  let report;
  try {report = await page.evaluate(() => window.portraitPreview.run());}
  catch (error) {report = {...await page.evaluate(() => window.portraitReport ?? {}), status: 'failed', error: String(error.stack ?? error)};}
  Object.assign(report, {manifest_sha256: digest(original), implementation: before, implementation_after: await snapshot(),
    errors, warnings, informational, failed_responses: failedResponses, blocked_external: blockedExternal, created_at: new Date().toISOString()});
  report.framing_key = report.comparison_key;
  report.comparison_key = digest(JSON.stringify({framing_key: report.framing_key, implementation: before}));
  report.source_snapshot_stable = JSON.stringify(before) === JSON.stringify(report.implementation_after);
  report.input_files_stable = digest(await fs.readFile(path.resolve(path.dirname(manifestPath), manifest.glb))) === manifest.model_sha256
    && digest(await fs.readFile(fixturePath)) === manifest.fixture.sha256;
  if (!report.source_snapshot_stable || !report.input_files_stable || errors.length || failedResponses.length || blockedExternal.length) report.status = 'failed';
  for (const item of await page.evaluate(() => window.portraitImages ?? [])) {
    assert(/^[a-z0-9-]+\.png$/.test(item.filename));
    const data = Buffer.from(item.data.split(',')[1], 'base64'); await fs.writeFile(path.join(output, item.filename), data);
    const capture = report.captures.find(c => c.filename === item.filename);
    capture.path = path.join(output, item.filename); capture.sha256 = digest(data); capture.bytes = data.length;
  }
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({status: report.status, captures: report.captures?.length, output, error: report.error, errors}));
  assert.equal(report.status, 'inspected', 'Portrait evidence capture failed');
} finally {await browser?.close(); await server.close();}
