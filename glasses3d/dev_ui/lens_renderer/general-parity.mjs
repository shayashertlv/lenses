/** Exact panel/production-AR parity on already patched immutable test copies.
 * node general-parity.mjs --out DIR --model GLB --materials DTO.json
 * Repeat for each solid/gradient fixture; never invokes providers or edits input. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {main as arCapture} from '../../arview/qa/capture.mjs';

const here = path.dirname(fileURLToPath(import.meta.url)), build = path.resolve(here, '../.lens-renderer');
const require = createRequire(path.resolve(here, '../../arview/package.json'));
const {chromium} = require('@playwright/test');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
export async function proveGeneralParity({out, model, materials}) {
  await fs.mkdir(path.dirname(out), {recursive: true}); await fs.mkdir(out);
  const bytes = await fs.readFile(model), supplied = JSON.parse(await fs.readFile(materials, 'utf8')), rows = supplied.materials || supplied;
  const errors = [], server = http.createServer(async (req, res) => {
    try {const pathname = new URL(req.url, 'http://localhost').pathname;
      if (pathname === '/') {res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><div id="viewer" style="width:800px;height:500px"></div>');}
      else if (pathname === '/model.glb') {res.setHeader('Content-Type', 'model/gltf-binary'); res.end(bytes);}
      else if (pathname.startsWith('/lens-renderer/')) {const file = path.resolve(build, decodeURIComponent(pathname.slice('/lens-renderer/'.length)));
        assert.ok(file.startsWith(build + path.sep)); res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream'); res.end(await fs.readFile(file));}
      else {res.statusCode = 404; res.end();}
    } catch (error) {res.statusCode = 500; res.end(error.message);}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser, camera, policy;
  try {
    browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
    const page = await browser.newPage({viewport: {width: 1000, height: 700}, deviceScaleFactor: 1});
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const capture = await page.evaluate(async rows => {
      const {createLensColourViewer} = await import('/lens-renderer/lens-renderer.js');
      const viewer = await createLensColourViewer(document.querySelector('#viewer'), {modelUrl: '/model.glb', materials: rows});
      viewer.setView('front'); const capture = viewer.capture(); viewer.dispose(); return capture;
    }, rows);
    camera = capture.camera; policy = capture.policy;
    await fs.writeFile(path.join(out, 'panel.png'), Buffer.from(capture.data_url.split(',')[1], 'base64'));
    assert.deepEqual(errors, []);
  } finally {await browser?.close(); await new Promise(resolve => server.close(resolve));}
  const cameras = path.join(out, 'cameras.json');
  await fs.writeFile(cameras, JSON.stringify({schema: 'arview.cameras/1', cameras: [camera]}, null, 2));
  const arOut = path.join(out, 'ar');
  const code = await arCapture([path.resolve(model), '--out', arOut, '--cameras', cameras, '--no-face', '--fixed-scale', '--lensenv', '1']);
  const report = JSON.parse(await fs.readFile(path.join(arOut, 'report.json'), 'utf8'));
  assert.equal(code, 0, JSON.stringify(report.errors)); assert.deepEqual(report.errors, []); assert.equal(report.chromium_process.gone, true);
  const panel = await fs.readFile(path.join(out, 'panel.png')), ar = await fs.readFile(path.join(arOut, `${camera.id}__beauty.png`));
  browser = await chromium.launch({headless: true}); let comparison;
  try {const page = await browser.newPage(); comparison = await page.evaluate(async ({a, b}) => {
    async function decode(data) {const img = new Image(); img.src = 'data:image/png;base64,' + data; await img.decode(); const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
      const context = canvas.getContext('2d'); context.drawImage(img, 0, 0); return {width: img.width, height: img.height, data: context.getImageData(0, 0, img.width, img.height).data};}
    const left = await decode(a), right = await decode(b); if (left.width !== right.width || left.height !== right.height) throw new Error('Dimensions differ');
    let changed = 0, maximum = 0; left.data.forEach((value, index) => {const delta = Math.abs(value - right.data[index]); if (delta) changed++; maximum = Math.max(maximum, delta);});
    return {width: left.width, height: left.height, changed_channels: changed, max_difference: maximum};
  }, {a: panel.toString('base64'), b: ar.toString('base64')});} finally {await browser.close();}
  const result = {schema: 'g3d.general_edit_parity/1', source_sha256: hash(bytes), policy, camera, comparison,
    panel_sha256: hash(panel), ar_sha256: hash(ar), source_unchanged: hash(await fs.readFile(model)) === hash(bytes), passed: comparison.changed_channels === 0};
  await fs.writeFile(path.join(out, 'parity.json'), JSON.stringify(result, null, 2));
  assert.equal(result.source_unchanged, true); assert.equal(result.passed, true, JSON.stringify(comparison)); return result;
}
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const args = Object.fromEntries(process.argv.slice(2).reduce((items, value, index, all) => {if (index % 2 === 0) items.push([value.replace(/^--/, ''), all[index + 1]]); return items;}, []));
  const result = await proveGeneralParity({out: path.resolve(args.out), model: path.resolve(args.model), materials: path.resolve(args.materials)});
  console.log(JSON.stringify({passed: result.passed, comparison: result.comparison}));
}
