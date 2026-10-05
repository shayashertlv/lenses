/** Local camera preview of pinned pipeline results using the normal AR app.
 * node qa/astra-preview.mjs --manifest=PATH --output=PATH [--port=8260]
 * Builds into the named private output, serves only its build plus pinned models,
 * and never publishes ar/site or changes the application/catalog sources.
 */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const option = (name, fallback) => process.argv.find(v => v.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
assert(option('manifest') && option('output'), 'Supply explicit manifest and private output paths');
const manifestPath = path.resolve(option('manifest')), output = path.resolve(option('output'));
const port = Number(option('port', '8260'));
assert(Number.isInteger(port) && port >= 1024 && port <= 65535, 'Invalid preview port');
assert(output.startsWith(path.join(root, 'qa', 'output') + path.sep), 'Keep private preview output under ar/qa/output');
const origin = `http://127.0.0.1:${port}`;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const inputBytes = await fs.readFile(manifestPath), input = JSON.parse(inputBytes);
assert(input.schema_version === 1 && Array.isArray(input.cases) && input.cases.length, 'Invalid preview manifest');
const models = new Map(), cases = [];
for (const row of input.cases) {
  assert(/^[a-z0-9-]{1,60}$/.test(row.id) && !models.has(`/preview-models/${row.id}.glb`), 'Unique safe IDs required');
  const bytes = await fs.readFile(path.resolve(path.dirname(manifestPath), row.path));
  assert.equal(sha(bytes), row.sha256, `Model changed: ${row.id}`);
  assert.equal(bytes.readUInt32LE(0), 0x46546c67); assert.equal(bytes.readUInt32LE(8), bytes.length);
  const document = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));
  for (const resource of [...document.buffers ?? [], ...document.images ?? []]) {
    assert(!resource.uri || resource.uri.startsWith('data:'), 'Models must have embedded resources');
  }
  assert(document.extras?.effectiveOpticalGroups, 'Expected canonical optical model');
  assert(Number.isFinite(row.width_mm) && row.width_mm >= 60 && row.width_mm <= 250);
  assert(Number.isFinite(row.clip_m) && row.clip_m >= -.2 && row.clip_m <= -.045);
  const assetPath = `/preview-models/${row.id}.glb`;
  models.set(assetPath, bytes);
  const params = new URLSearchParams({model: assetPath, name: row.name, width: String(row.width_mm),
    clip: String(row.clip_m), sha256: row.sha256});
  cases.push({id: row.id, name: row.name, product: row.product, variant: row.variant,
    sha256: row.sha256, bytes: bytes.length, asset_url: origin + assetPath, url: `${origin}/?${params}`});
}

await fs.mkdir(output, {recursive: true});
const buildDirectory = path.join(output, 'build');
try { await fs.access(buildDirectory); throw new Error('Use a fresh preview output directory'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
await build({root, configFile: path.join(root, 'vite.config.ts'),
  build: {outDir: buildDirectory, emptyOutDir: false}});

const files = new Map();
async function inventory(folder) {
  for (const entry of await fs.readdir(folder, {withFileTypes: true})) {
    const target = path.join(folder, entry.name);
    if (entry.isDirectory()) await inventory(target);
    else if (entry.isFile()) files.set('/' + path.relative(buildDirectory, target).replaceAll('\\', '/'),
      {path: target, sha256: sha(await fs.readFile(target))});
  }
}
await inventory(buildDirectory);
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const cards = ['oakley', 'miu'].map(product => {
  const rows = cases.filter(c => c.product === product);
  return `<article><h2>${product === 'oakley' ? 'Oakley' : 'Miu Miu'}</h2><div>${rows.map(c =>
    `<a class="${c.variant === 'final' ? 'primary' : 'secondary'}" href="${escape(c.url)}">${c.variant === 'final' ? 'Try after Astra' : 'Try before Astra'}</a>`).join('')}</div></article>`;
}).join('');
const landing = Buffer.from(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Glasses · Camera preview</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#17222b;font:17px/1.6 system-ui,sans-serif}main{max-width:820px;margin:8vh auto;padding:24px}h1{font-size:clamp(30px,5vw,46px);line-height:1.15}h2{margin:0 0 20px}.intro{max-width:630px;color:#485864}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin:32px 0}article{background:white;border:1px solid #dbe2e7;border-radius:18px;padding:28px}article div{display:flex;flex-direction:column;gap:12px}a{padding:12px 18px;border-radius:10px;text-align:center;text-decoration:none;font-weight:650}.primary{background:#125f57;color:white}.secondary{background:#edf3f2;color:#125f57}.steps{background:#e6efed;border-radius:14px;padding:20px 24px}.small{font-size:14px;color:#55646f}</style>
<main><p class="small">LOCAL CAMERA PREVIEW</p><h1>Try the latest glasses on.</h1><p class="intro">Choose a pair, then select <strong>Open camera</strong>. Look straight ahead for a few seconds while the fit settles, then turn your head to inspect the lenses and temples.</p><section class="cards">${cards}</section><div class="steps">Use your browser’s Back button to compare another version. Each link uses the same AR fitting, shadows and occlusion. Camera images stay in your browser.</div><p class="small">Open this preview on this computer in Chrome or Edge. The server is local to this computer. These are experimental models for appearance review.</p></main></html>`);
const manifest = {schema_version: 1, origin, launcher_url: origin + '/preview/', cases,
  source_manifest_sha256: sha(inputBytes), built_at: new Date().toISOString(), pid: process.pid,
  camera_starts_automatically: false, live_site_published: false,
  build_files: Object.fromEntries([...files].map(([url, item]) => [url, item.sha256]))};
const manifestBytes = Buffer.from(JSON.stringify(manifest, null, 2));
const mime = {'.html':'text/html; charset=utf-8', '.js':'text/javascript', '.mjs':'text/javascript',
  '.css':'text/css', '.json':'application/json', '.wasm':'application/wasm', '.glb':'model/gltf-binary',
  '.png':'image/png', '.jpg':'image/jpeg', '.svg':'image/svg+xml', '.webp':'image/webp', '.ico':'image/x-icon'};
const server = createServer(async (req, res) => {
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Permissions-Policy', 'camera=(self), microphone=()');
  try {
    if (!['GET', 'HEAD'].includes(req.method)) {res.writeHead(405); res.end(); return;}
    let route = decodeURIComponent(new URL(req.url, origin).pathname);
    let bytes, type;
    if (route === '/preview/' || route === '/preview') {bytes = landing; type = mime['.html'];}
    else if (route === '/preview/manifest.json') {bytes = manifestBytes; type = mime['.json'];}
    else if (models.has(route)) {bytes = models.get(route); type = mime['.glb'];}
    else {
      if (route === '/') route = '/index.html';
      const item = files.get(route);
      if (!item) {res.writeHead(404); res.end('Not found'); return;}
      bytes = await fs.readFile(item.path);
      assert.equal(sha(bytes), item.sha256, 'Frozen preview file changed');
      type = mime[path.extname(item.path)] ?? 'application/octet-stream';
    }
    res.setHeader('Content-Type', type); res.setHeader('Content-Length', bytes.length);
    res.writeHead(200); res.end(req.method === 'HEAD' ? undefined : bytes);
  } catch (error) {console.error(error.message); res.writeHead(500); res.end('Preview artifact unavailable');}
});
server.listen(port, '127.0.0.1', async () => {
  await fs.writeFile(path.join(output, 'server.json'), JSON.stringify(manifest, null, 2)+'\n');
  console.log(`AR preview ready: ${manifest.launcher_url}`);
});
server.on('error', error => {console.error(error.message); process.exitCode = 1;});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)));
