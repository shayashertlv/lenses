// Scratch-only junction diagnosis runner. Serves ar/ with Vite (no repo writes), injects a virtual page + module,
// drives headless Chromium (ANGLE D3D11 like qa/provider-comparison.mjs), writes PNGs to this scratch folder.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
const AR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../ar');
const {createServer} = await import(pathToFileURL(path.join(AR, 'node_modules/vite/dist/node/index.js')).href);
const {chromium} = await import(pathToFileURL(path.join(AR, 'node_modules/@playwright/test/index.mjs')).href);
const here = path.dirname(fileURLToPath(import.meta.url));
const configPath = process.argv[2] ?? path.join(here, 'config.json');
const config = JSON.parse(await fs.readFile(configPath, 'utf8'));
const out = path.resolve(here, config.output ?? 'renders');
await fs.mkdir(out, {recursive: true});
const pageModule = await fs.readFile(path.join(here, 'page.js'), 'utf8');
const models = new Map();
for (const c of config.cases) if (c.path) {const b = await fs.readFile(c.path); c.sha256 = crypto.createHash('sha256').update(b).digest('hex'); models.set(c.id + '.glb', b);}
const VIRTUAL_JS = path.join(AR, 'qa', '__junction_virtual.js').replaceAll('\\', '/');
const server = await createServer({configFile: false, root: AR, logLevel: 'warn', server: {host: '127.0.0.1', port: 0, hmr: false},
  plugins: [{name: 'junction-virtual',
    resolveId(id) {if (id === '/qa/__junction_virtual.js' || id.replaceAll('\\', '/') === VIRTUAL_JS) return VIRTUAL_JS; return null;},
    load(id) {if (id.replaceAll('\\', '/') === VIRTUAL_JS) return pageModule; return null;},
    configureServer(s) {s.middlewares.use((req, res, next) => {
      const url = new URL(req.url ?? '/', 'http://x');
      if (url.pathname === '/__junction.html') {res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,"><p id="s">ready</p><script type="module" src="/qa/__junction_virtual.js"></script>'); return;}
      if (url.pathname === '/__junction/config.json') {res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(config)); return;}
      if (url.pathname.startsWith('/__junction/models/')) {const b = models.get(url.pathname.slice('/__junction/models/'.length)); if (!b) {res.statusCode = 404; res.end(); return;} res.setHeader('Content-Type', 'model/gltf-binary'); res.end(b); return;}
      next();
    });}}]});
let browser;
const errors = [];
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
  const page = await browser.newPage({viewport: {width: 1400, height: 1000}}); page.setDefaultTimeout(600000);
  await page.route('**/*', route => {const u = route.request().url(); if (u.startsWith(origin + '/') || u.startsWith('data:') || u.startsWith('blob:')) return route.continue(); errors.push('blocked ' + u); return route.abort();});
  page.on('pageerror', e => errors.push('pageerror ' + e.message));
  page.on('console', m => {if (m.type() === 'error') errors.push('console ' + m.text()); if (m.type() === 'log') console.log('[page]', m.text());});
  await page.goto(origin + '/__junction.html');
  await page.waitForFunction(() => !!window.junction);
  const report = await page.evaluate(() => window.junction.run());
  const count = await page.evaluate(() => window.junctionImages.length);
  for (let i = 0; i < count; i++) {
    const img = await page.evaluate(k => window.junctionImages[k], i);
    await fs.writeFile(path.join(out, img.filename), Buffer.from(img.data.split(',')[1], 'base64'));
  }
  report.errors = errors; report.gpu = await page.evaluate(() => window.junctionGpu ?? null);
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 1));
  console.log(JSON.stringify({images: count, errors, cases: report.cases.map(c => ({id: c.id, status: c.status, error: c.error, lens: c.lensMeshes}))}, null, 1));
} finally {await browser?.close(); await server.close();}
