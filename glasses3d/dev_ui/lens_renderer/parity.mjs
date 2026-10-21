/** $0 renderer parity proof on generated geometry or a read-only product COPY.
 * Run serially after task 18 exits. Uses the unchanged production AR capture CLI.
 * node .../parity.mjs --out <ignored directory> [--model <glb> --materials <DTO.json>]
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {buildGlb} from '../../arview/tests/glb-fab.ts';
import {main as arCapture} from '../../arview/qa/capture.mjs';
import iridescenceShader from '../../arview/node_modules/three/src/renderers/shaders/ShaderChunk/iridescence_fragment.glsl.js';
import commonShader from '../../arview/node_modules/three/src/renderers/shaders/ShaderChunk/common.glsl.js';
import {evalCoatingReflectance} from './coating.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const require = createRequire(path.join(root, 'glasses3d/arview/package.json'));
const {chromium} = require('@playwright/test');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function glbJson(bytes) {return JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));}
function patchGlb(bytes, rows) {
  const doc = glbJson(bytes);
  for (const {material_index: index, values: v} of rows) {
    const material = doc.materials[index], pbr = material.pbrMetallicRoughness ??= {};
    pbr.baseColorFactor = [...v.tint, pbr.baseColorFactor?.[3] ?? 1];
    pbr.metallicFactor = v.metallic; pbr.roughnessFactor = v.roughness;
    const extensions = material.extensions ??= {};
    (extensions.KHR_materials_transmission ??= {}).transmissionFactor = v.transmission;
    (extensions.KHR_materials_ior ??= {}).ior = v.ior;
    Object.assign(extensions.KHR_materials_iridescence ??= {}, {iridescenceFactor: v.iridescence,
      iridescenceIor: v.iridescence_ior, iridescenceThicknessMinimum: v.iridescence_thickness_min,
      iridescenceThicknessMaximum: v.iridescence_thickness_max});
    doc.extensionsUsed = [...new Set([...(doc.extensionsUsed ?? []), ...Object.keys(extensions)])];
  }
  const raw = Buffer.from(JSON.stringify(doc)), json = Buffer.concat([raw, Buffer.alloc((4 - raw.length % 4) % 4, 32)]);
  const remainder = bytes.subarray(20 + bytes.readUInt32LE(12));
  const header = Buffer.from(bytes.subarray(0, 20));
  header.writeUInt32LE(20 + json.length + remainder.length, 8); header.writeUInt32LE(json.length, 12);
  return Buffer.concat([header, json, remainder]);
}
function valuesOf(material) {
  const pbr = material.pbrMetallicRoughness ?? {}, ext = material.extensions ?? {}, film = ext.KHR_materials_iridescence ?? {};
  return {tint: (pbr.baseColorFactor ?? [1, 1, 1, 1]).slice(0, 3), transmission: ext.KHR_materials_transmission?.transmissionFactor ?? 0,
    metallic: pbr.metallicFactor ?? 1, roughness: pbr.roughnessFactor ?? 1, ior: ext.KHR_materials_ior?.ior ?? 1.5,
    iridescence: film.iridescenceFactor ?? 0, iridescence_ior: film.iridescenceIor ?? 1.3,
    iridescence_thickness_min: film.iridescenceThicknessMinimum ?? 100,
    iridescence_thickness_max: film.iridescenceThicknessMaximum ?? 400};
}

async function pixelFraming(page, image) {
  return page.evaluate(async image => {
    const img = new Image(); img.src = image; await img.decode();
    const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
    const context = canvas.getContext('2d'); context.drawImage(img, 0, 0);
    const rgba = context.getImageData(0, 0, img.width, img.height).data;
    let occupied = 0, minX = img.width, maxX = -1, minY = img.height, maxY = -1;
    for (let y = 0; y < img.height; y++) for (let x = 0; x < img.width; x++) {
      const at = (y * img.width + x) * 4;
      if (Math.max(...[0, 1, 2].map(channel => Math.abs(rgba[at + channel] - rgba[channel]))) < 8) continue;
      occupied++; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    return {occupied_fraction: occupied / (img.width * img.height), width_fraction: (maxX - minX + 1) / img.width,
      margins: {left: minX, right: img.width - 1 - maxX, top: minY, bottom: img.height - 1 - maxY}};
  }, image);
}

export async function proveParity({out, model, materials, opaque = false}) {
  if (model && !materials) throw new Error('A real model requires its exact lens material DTO.');
  await fs.mkdir(path.dirname(out), {recursive: true});
  await fs.mkdir(out); // Failed attempts remain immutable proof evidence.
  const sourceBytes = model ? await fs.readFile(model) : Buffer.from(buildGlb({materials: {
    frame: {name: 'Synthetic frame', pbrMetallicRoughness: {baseColorFactor: [.13, .08, .04, 1], metallicFactor: 0, roughnessFactor: .45}},
    lens: {name: 'Synthetic lens', pbrMetallicRoughness: {baseColorFactor: [.7, .85, 1, 1], metallicFactor: .1, roughnessFactor: .1},
      ...(opaque ? {} : {extensions: {KHR_materials_transmission: {transmissionFactor: .9}}})},
  }}).glb);
  const doc = glbJson(sourceBytes);
  const materialData = materials ? JSON.parse(await fs.readFile(materials, 'utf8')) : null;
  const rows = materialData ? (materialData.materials ?? materialData)
    : [{material_index: 1, name: doc.materials[1].name, values: valuesOf(doc.materials[1])}];
  const afterRows = rows.map(row => ({...row, values: {...row.values, tint: [.08, .55, .9],
    transmission: .67, metallic: .4, roughness: .13, ior: 1.6, iridescence: .8,
    iridescence_ior: 1.4, iridescence_thickness_min: 170, iridescence_thickness_max: 630}}));
  const afterBytes = patchGlb(sourceBytes, afterRows);
  const beforeGlb = path.join(out, 'before-copy.glb'), afterGlb = path.join(out, 'after-copy.glb');
  await fs.writeFile(beforeGlb, sourceBytes); await fs.writeFile(afterGlb, afterBytes);
  const built = spawnSync(process.execPath, [path.join(here, 'build.mjs')], {cwd: root, windowsHide: true, encoding: 'utf8'});
  assert.equal(built.status, 0, built.stderr);
  const buildRoot = path.resolve(here, '../.lens-renderer');
  const server = http.createServer(async (req, res) => {
    try {
      const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
      if (pathname === '/') {res.writeHead(200, {'content-type': 'text/html'}); res.end('<!doctype html><meta charset="utf-8"><div id="viewer" style="width:800px;height:500px"></div>'); return;}
      if (pathname === '/model.glb') {res.writeHead(200, {'content-type': 'model/gltf-binary'}); res.end(sourceBytes); return;}
      if (pathname.startsWith('/lens-renderer/')) {
        const relative = decodeURIComponent(pathname.slice('/lens-renderer/'.length));
        const file = path.resolve(buildRoot, relative);
        if (!file.startsWith(buildRoot + path.sep)) throw new Error('Invalid path');
        res.writeHead(200, {'content-type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.json') ? 'application/json' : 'application/wasm'});
        res.end(await fs.readFile(file)); return;
      }
      res.writeHead(404); res.end();
    } catch (error) {res.writeHead(500); res.end(error.message);}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser, camera, policy, framing, coatingParity, resizeProof;
  const consoleErrors = [];
  try {
    browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
    const page = await browser.newPage({viewport: {width: 1000, height: 700}, deviceScaleFactor: 1});
    page.on('pageerror', error => consoleErrors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const coatingCases = [
      {ior: 1.3, cosine: 1, thickness: 100, base: [.04, .04, .04]},
      {ior: 1.4, cosine: .7, thickness: 630, base: [.08, .55, .9]},
      {ior: 2.3, cosine: .2, thickness: 400, base: [.45, .12, .73]},
      {ior: 1, cosine: .95, thickness: 0, base: [.04, .04, .04]},
    ];
    const coatingPixels = await page.evaluate(({common, film, cases}) => {
      const canvas = document.createElement('canvas'); canvas.width = cases.length; canvas.height = 1;
      const gl = canvas.getContext('webgl2', {antialias: false, alpha: false});
      const compile = (type, source) => {
        const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
        return shader;
      };
      const vertex = compile(gl.VERTEX_SHADER, '#version 300 es\nin vec2 position;void main(){gl_Position=vec4(position,0,1);}');
      const body = cases.map((item, index) => `if (int(gl_FragCoord.x) == ${index}) colour = evalIridescence(1.0, ${Number(item.ior).toFixed(8)}, ${Number(item.cosine).toFixed(8)}, ${Number(item.thickness).toFixed(8)}, vec3(${item.base.map(x => Number(x).toFixed(8)).join(',')}));`).join('\n');
      const fragment = compile(gl.FRAGMENT_SHADER, `#version 300 es\nprecision highp float;\n#define USE_IRIDESCENCE\n${common}\n${film}\nout vec4 result;void main(){vec3 colour=vec3(0);${body}\nresult=vec4(clamp(colour,0.0,1.0),1);}`);
      const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
      gl.useProgram(program); const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
      const attribute = gl.getAttribLocation(program, 'position'); gl.enableVertexAttribArray(attribute); gl.vertexAttribPointer(attribute, 2, gl.FLOAT, false, 0, 0);
      gl.viewport(0, 0, cases.length, 1); gl.drawArrays(gl.TRIANGLES, 0, 3);
      const pixels = new Uint8Array(cases.length * 4); gl.readPixels(0, 0, cases.length, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      gl.deleteBuffer(buffer); gl.deleteProgram(program); gl.deleteShader(vertex); gl.deleteShader(fragment);
      gl.getExtension('WEBGL_lose_context')?.loseContext(); return Array.from(pixels);
    }, {common: commonShader, film: iridescenceShader, cases: coatingCases});
    coatingParity = coatingCases.map((sample, index) => {
      const expected = evalCoatingReflectance(1, sample.ior, sample.cosine, sample.thickness, sample.base)
        .map(value => Math.round(255 * Math.min(1, Math.max(0, value))));
      const actual = coatingPixels.slice(index * 4, index * 4 + 3);
      assert.ok(actual.every((value, channel) => Math.abs(value - expected[channel]) <= 1), JSON.stringify({sample, actual, expected}));
      return {sample, gpu_rgb: actual, cpu_rgb: expected};
    });
    const before = await page.evaluate(async rows => {
      const module = await import('/lens-renderer/lens-renderer.js');
      window.preview = await module.createLensColourViewer(document.querySelector('#viewer'), {modelUrl: '/model.glb', materials: rows});
      window.preview.setView('front');
      return window.preview.capture();
    }, rows);
    camera = before.camera; policy = before.policy;
    framing = await pixelFraming(page, before.data_url);
    assert.ok(framing.occupied_fraction > .01, `Initial orbit preview is blank or tiny: ${JSON.stringify(framing)}`);
    assert.ok(framing.width_fraction > .35 && framing.width_fraction < .99,
      `Initial orbit view must show the frame at a useful size without clipping: ${JSON.stringify(framing)}`);
    await fs.writeFile(path.join(out, 'panel-before.png'), Buffer.from(before.data_url.split(',')[1], 'base64'));
    const after = await page.evaluate(rows => {window.preview.applyMaterials(rows); return window.preview.capture();}, afterRows);
    await fs.writeFile(path.join(out, 'panel-after.png'), Buffer.from(after.data_url.split(',')[1], 'base64'));
    // Crossing transmission 0 re-enters tone mapping, then restoring it recovers
    // exactly; this checks the material path used by the Mirror/See-through controls.
    const restored = await page.evaluate(({before, after}) => {
      window.preview.applyMaterials(after.map(row => ({...row, values: {...row.values, transmission: 0, iridescence: 0}})));
      const opaque = window.preview.capture();
      window.preview.applyMaterials(before); const reset = window.preview.capture();
      return {opaque: opaque.data_url, reset: reset.data_url};
    }, {before: rows, after: afterRows});
    assert.notEqual(before.data_url, after.data_url, 'The material change must change the drawn pixels.');
    assert.notEqual(after.data_url, restored.opaque, 'Transmission/coating feature boundaries must change pixels.');
    assert.equal(restored.reset, before.data_url, 'Reset must restore exact original pixels.');
    // Resize the host itself, as the responsive two-column panel does. The live
    // observer must keep the full object visible without resetting an orbit.
    const resizeCapture = async width => page.evaluate(async width => {
      document.querySelector('#viewer').style.width = `${width}px`;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const image = window.preview.capture(), target = window.preview.controls.target.clone();
      const offset = window.preview.camera.position.clone().sub(target);
      return {...image, target: target.toArray(), direction: offset.clone().normalize().toArray(), distance: offset.length()};
    }, width);
    resizeProof = {front: [], orbit: []};
    for (const width of [540, 360, 800]) {
      const shot = await resizeCapture(width), metrics = await pixelFraming(page, shot.data_url);
      assert.ok(metrics.occupied_fraction > .01 && metrics.width_fraction > .35,
        `Resized front must remain useful: ${JSON.stringify({width, metrics})}`);
      assert.ok(Object.values(metrics.margins).every(value => value >= 2),
        `Resized front is clipped: ${JSON.stringify({width, metrics})}`);
      if (width === 800) assert.equal(shot.data_url, before.data_url, 'Returning to the original width restores its pixels.');
      await fs.writeFile(path.join(out, `resize-front-${width}.png`), Buffer.from(shot.data_url.split(',')[1], 'base64'));
      resizeProof.front.push({width, ...metrics});
    }
    await page.evaluate(() => {
      const viewer = window.preview;
      viewer.setView('angled', 'right');
      viewer.camera.position.sub(viewer.controls.target).multiplyScalar(1.25).add(viewer.controls.target);
      viewer.camera.position.x += .003; viewer.controls.target.x += .003;
      viewer.controls.update();
    });
    const orbit = await resizeCapture(800);
    for (const width of [360, 800]) {
      const shot = await resizeCapture(width), metrics = await pixelFraming(page, shot.data_url);
      assert.ok(Object.values(metrics.margins).every(value => value >= 2), 'Resizing the zoomed-out orbit must not clip it.');
      assert.deepEqual(shot.target, orbit.target, 'Resizing must preserve the panned target.');
      assert.ok(shot.direction.every((value, index) => Math.abs(value - orbit.direction[index]) < 1e-12),
        'Resizing must preserve the orbit direction.');
      if (width === 800) {
        assert.ok(Math.abs(shot.distance - orbit.distance) < 1e-12, 'Resize round trip must preserve the chosen zoom.');
        assert.equal(shot.data_url, orbit.data_url, 'Resize round trip must restore the panned orbit pixels.');
      }
      await fs.writeFile(path.join(out, `resize-orbit-${width}.png`), Buffer.from(shot.data_url.split(',')[1], 'base64'));
      resizeProof.orbit.push({width, ...metrics, target: shot.target, direction: shot.direction, distance: shot.distance});
    }
    assert.deepEqual(consoleErrors, []);
    await page.evaluate(() => window.preview.dispose());
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
  const cameras = path.join(out, 'cameras.json');
  await fs.writeFile(cameras, JSON.stringify({schema: 'arview.cameras/1', cameras: [camera]}, null, 2));
  const captures = [];
  for (const [label, glb] of [['before', beforeGlb], ['after', afterGlb]]) {
    const captureOut = path.join(out, `ar-${label}`);
    const code = await arCapture([glb, '--out', captureOut, '--cameras', cameras, '--no-face', '--fixed-scale', '--lensenv', '1']);
    const report = JSON.parse(await fs.readFile(path.join(captureOut, 'report.json'), 'utf8'));
    assert.equal(code, 0, JSON.stringify({code, errors: report.errors, refusal: report.refusal}));
    assert.deepEqual(report.errors, []);
    assert.equal(report.chromium_process.gone, true);
    captures.push({label, file: path.join(captureOut, `${camera.id}__beauty.png`), report});
  }
  // Decode both independent PNGs in the browser. PNG container encoders are not
  // assumed identical; exact RGBA is the rendering contract.
  const comparisons = [];
  browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    for (const {label, file} of captures) {
      const panel = await fs.readFile(path.join(out, `panel-${label}.png`)), ar = await fs.readFile(file);
      const comparison = await page.evaluate(async ({panel, ar}) => {
        const decode = async data => {
          const img = new Image(); img.src = `data:image/png;base64,${data}`; await img.decode();
          const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
          const context = canvas.getContext('2d'); context.drawImage(img, 0, 0);
          return {width: img.width, height: img.height, bytes: context.getImageData(0, 0, img.width, img.height).data};
        };
        const a = await decode(panel), b = await decode(ar); let changed = 0, maxDifference = 0;
        if (a.width !== b.width || a.height !== b.height) throw new Error('Capture dimensions differ.');
        for (let i = 0; i < a.bytes.length; i++) {
          const difference = Math.abs(a.bytes[i] - b.bytes[i]); if (difference) changed++;
          maxDifference = Math.max(maxDifference, difference);
        }
        return {width: a.width, height: a.height, changed_channels: changed, max_difference: maxDifference};
      }, {panel: panel.toString('base64'), ar: ar.toString('base64')});
      comparisons.push({label, ...comparison, panel_sha256: hash(panel), ar_sha256: hash(ar)});
    }
  } finally {await browser.close();}
  const result = {schema: 'g3d.lens_renderer_parity/1', source_sha256: hash(sourceBytes), after_sha256: hash(afterBytes),
    policy, camera, initial_orbit_framing: framing, responsive_resize: resizeProof, coating_shader_parity: coatingParity,
    materials_before: rows, materials_after: afterRows, comparisons,
    source_unchanged: !model || hash(await fs.readFile(model)) === hash(sourceBytes),
    passed: comparisons.every(row => row.changed_channels === 0)};
  await fs.writeFile(path.join(out, 'parity.json'), JSON.stringify(result, null, 2));
  assert.equal(result.source_unchanged, true);
  assert.equal(result.passed, true, JSON.stringify(comparisons));
  return result;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) => {
    if (index % 2 === 0) rows.push([value.replace(/^--/, ''), all[index + 1]]); return rows;
  }, []));
  if (!args.out) throw new Error('--out is required');
  const result = await proveParity({out: path.resolve(args.out), model: args.model, materials: args.materials, opaque: args.opaque === 'true'});
  console.log(JSON.stringify({passed: result.passed, comparisons: result.comparisons}));
}
