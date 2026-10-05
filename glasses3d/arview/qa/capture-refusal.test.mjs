/** arview capture: a GLB that §1 says must be refused is refused by the capture (WP1_DESIGN.md §1, the Format row: a
 *  missing external resource). Round-2 review: the capture's Vite middleware handed unrouted paths to Vite's SPA
 *  fallback, which answered a missing model-relative resource with index.html and HTTP 200, so arview got bytes, did not
 *  refuse, and the report contradicted spec.py. Now every unrouted path under the model prefix is a 404 (both servers
 *  also run with appType 'mpa').
 *
 *  1. routesMiddleware on fake requests (pure).
 *  2. The REAL orchestrator in this process on a box GLB whose texture 'missing_texture.png' is not beside it: status
 *     'refused', exit 4, the refusal names the missing resource, the page saw HTTP 404 for it, no image. Needs headless
 *     Chromium on a hardware GPU (it refuses before anything is rendered).
 *  3. The same with a present '100%.png' and an absent '%zz-missing.png' (malformed percent escapes): no URIError, the
 *     report is written, only the absent file is refused (spec.py agrees: capture/external-resources.ts). */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {main} from './capture.mjs';
import {modelRoutes, routesMiddleware} from './capture/live-probe.mjs';
import {buildGlb, pngBytes} from '../tests/glb-fab.ts';

/** Call a connect middleware with a fake request; resolves {status, type, body, next}. */
function call(middleware, url) {
  return new Promise(resolve => {
    const headers = {};
    const response = {statusCode: 200, setHeader: (name, value) => {headers[name.toLowerCase()] = value;},
      end: body => resolve({status: response.statusCode, type: headers['content-type'] ?? null, body: body === undefined ? '' : String(body), next: false})};
    middleware({url}, response, () => resolve({status: null, type: null, body: '', next: true}));
  });
}

test('routesMiddleware: routed files are served, every other path under the prefix is a 404, the rest falls through', async () => {
  const modelPath = '/__arview/models/abc/model.glb';
  const routes = modelRoutes(modelPath, {bytes: Buffer.from('glTF'), resources: [{uri: 'tex/a.png', type: 'image/png', bytes: Buffer.from('png')}]});
  const middleware = routesMiddleware(routes, '/__arview/');
  assert.deepEqual(await call(middleware, modelPath), {status: 200, type: 'model/gltf-binary', body: 'glTF', next: false});
  assert.deepEqual(await call(middleware, '/__arview/models/abc/tex/a.png?x=1'), {status: 200, type: 'image/png', body: 'png', next: false});
  const missing = await call(middleware, '/__arview/models/abc/missing_texture.png');
  assert.equal(missing.status, 404); assert.equal(missing.next, false); assert.match(missing.type, /^text\/plain/);
  assert.match(missing.body, /missing_texture\.png is not served/);
  assert.equal((await call(middleware, '/__arview/face-a.jpg')).status, 404);
  assert.equal((await call(middleware, '/qa/capture.html')).next, true);
  assert.equal((await call(middleware, '/models/canonical-face.json')).next, true);
  const live = routesMiddleware(new Map(), '/__arview_live/');
  assert.equal((await call(live, '/__arview_live/models/abc/missing_texture.png')).status, 404);
});

test('a GLB with a missing external image is REFUSED by the capture: status refused, exit 4, HTTP 404 seen', {timeout: 120_000}, async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'arview-refusal-'));
  try {
    const built = buildGlb({patch: doc => {
      doc.images = [{uri: 'missing_texture.png'}]; doc.textures = [{source: 0}];
      doc.materials[0].pbrMetallicRoughness.baseColorTexture = {index: 0};
    }});
    const glb = path.join(work, 'format-missing-image.glb'); fs.writeFileSync(glb, Buffer.from(built.glb));
    const out = path.join(work, 'out');
    const code = await main([glb, '--poses', 'default', '--out', out, '--no-portrait', '--step-timeout-s', '30']);
    const report = JSON.parse(fs.readFileSync(path.join(out, 'report.json'), 'utf8'));
    assert.equal(code, 4, `exit ${code}: ${report.status} ${report.infrastructure_failure ?? ''}`);
    assert.equal(report.status, 'refused'); assert.equal(report.exit_code, 4);
    assert.equal(report.runtime.status, 'refused');
    assert.ok(report.runtime.refusal.some(text => /missing external resource/.test(text) && /missing_texture\.png/.test(text)), JSON.stringify(report.runtime.refusal));
    assert.equal(report.runtime.spec.format.pass, false);
    assert.deepEqual(report.images, []);
    assert.ok(report.page.failed_responses.some(entry => entry.status === 404 && entry.url.endsWith('/missing_texture.png')), JSON.stringify(report.page.failed_responses));
    assert.ok(report.warnings.some(text => /missing_texture\.png.*the capture answers it 404, so arview refuses the model/.test(text)), report.warnings.join('\n'));
    assert.ok(!report.errors.some(text => /RENDERED a model/.test(text)), report.errors.join('\n'));
    if (report.spec.available) {
      // spec.py fails Format too; the rows arview refused before checking are uncompared, never false disagreements.
      assert.equal(report.spec.rows.format.pass, false);
      assert.deepEqual(report.runtime.spec_disagreements, []);
      assert.ok(report.runtime.spec_rows_compared.includes('format'));
    } else assert.ok(report.warnings.some(text => /^VALIDATOR UNAVAILABLE/.test(text)));
  } finally {fs.rmSync(work, {recursive: true, force: true});}
});

test('malformed percent escapes: \'100%.png\' (present) is served, \'%zz-missing.png\' (absent) refused; the report is written, no URIError', {timeout: 120_000}, async () => {
  // Review finding (stage F, round 2): decodeURIComponent('100%.png') threw outside any try block, so the capture crashed
  // with a raw URIError stack, exit 1 and no report, on a file spec.py passes. Now both tools resolve URIs as spec.py does
  // (Python's unquote keeps a malformed escape), and only the missing file is refused.
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'arview-escape-'));
  try {
    const built = buildGlb({patch: doc => {
      doc.images = [{uri: '100%.png'}, {uri: '%zz-missing.png'}]; doc.textures = [{source: 0}];
      doc.materials[0].pbrMetallicRoughness.baseColorTexture = {index: 0};
    }});
    const glb = path.join(work, 'escapes.glb'); fs.writeFileSync(glb, Buffer.from(built.glb));
    fs.writeFileSync(path.join(work, '100%.png'), Buffer.from(pngBytes(4, 4)));
    const out = path.join(work, 'out');
    const code = await main([glb, '--poses', 'default', '--out', out, '--no-portrait', '--step-timeout-s', '30']);
    const report = JSON.parse(fs.readFileSync(path.join(out, 'report.json'), 'utf8'));
    assert.equal(code, 4, `exit ${code}: ${report.status} ${report.infrastructure_failure ?? ''}`);
    assert.equal(report.status, 'refused'); assert.equal(report.runtime.status, 'refused');
    const refusal = report.runtime.refusal.join(' ');
    assert.match(refusal, /missing external resource.*%zz-missing\.png/); assert.doesNotMatch(refusal, /100%\.png/, 'the present file is not missing');
    assert.ok(report.page.failed_responses.some(entry => entry.status === 404 && entry.url.endsWith('/%zz-missing.png')), JSON.stringify(report.page.failed_responses));
    assert.ok(!report.page.failed_responses.some(entry => entry.url.endsWith('/100%.png')), 'the present file was served at the raw path the browser requested');
    assert.ok(report.warnings.some(text => /"%zz-missing\.png" .*%zz-missing\.png\) cannot be read \(ENOENT\); the capture answers it 404/.test(text)), report.warnings.join('\n'));
    assert.ok(!report.warnings.some(text => /^external resource "100%\.png"/.test(text)), report.warnings.join('\n'));
    assert.ok(!report.errors.some(text => /URIError|RENDERED a model/.test(text)), report.errors.join('\n'));
    if (report.spec.available) {
      assert.equal(report.spec.rows.format.pass, false, 'spec.py fails Format for the missing file');
      assert.ok(report.spec.rows.format.detail.some(text => /%zz-missing\.png/.test(text)) && !report.spec.rows.format.detail.some(text => /'100%\.png'/.test(text)),
        JSON.stringify(report.spec.rows.format.detail));
      assert.deepEqual(report.runtime.spec_disagreements, []);
    }
  } finally {fs.rmSync(work, {recursive: true, force: true});}
});
