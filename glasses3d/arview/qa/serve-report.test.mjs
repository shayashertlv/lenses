/** arview serve: the served report (qa/serve/report.ts) and what the webcam page lists from it and from the renderer's
 *  report (src/model/served-report.ts). arview's half is the REAL Node-side check the server runs (serve.mjs
 *  checkInNode: the load front-end and normalisation on synthetic GLBs); spec.py's half is its real --full output shape
 *  (qa/fixtures/spec-full-q001.json) with the rows each case needs. */
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {checkInNode, geometryOnly} from './serve.mjs';
import {pythonUnquote, resolveExternalResources} from './capture/external-resources.ts';
import {assembleServeReport, SERVED_REPORT_SCHEMA} from './serve/report.ts';
import {digestLine, isSpecPyNote, parseServedReport, renderedReportLines, servedReportLines} from '../src/model/served-report.ts';
import {reportLines} from '../src/model/report.ts';
import {buildGlb, pngBytes, r9Like} from '../tests/glb-fab.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/spec-full-q001.json'), 'utf8'))[0];
const ORIGIN = 'http://127.0.0.1:8251';
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

async function served(built, {spec = 'pass', rows = {}, arviewError = null, serveErrors = [], serveWarnings = [], specSha = null, specWarnings = null,
  resources = {}} = {}) {
  const bytes = Buffer.from(built.glb), sha = sha256(bytes);
  const modelPath = `/__arview/models/${sha}/model.glb`, url = ORIGIN + modelPath;
  const routes = new Map([[modelPath, {bytes, type: 'model/gltf-binary', model: true}]]);
  for (const [name, data] of Object.entries(resources)) routes.set(`/__arview/models/${sha}/${name}`, {bytes: Buffer.from(data), type: 'application/octet-stream'});
  const checked = arviewError ? {report: null, templeClip: null, error: arviewError} : await checkInNode(bytes, {url, routes, displayName: 'model.glb', sha});
  let outcome;
  if (spec === 'unavailable') outcome = {available: false, reason: 'no Python interpreter in glasses3d/.venv', command: null, ms: 0};
  else {
    const report = structuredClone(FIXTURE);
    report.sha256 = specSha ?? sha;
    for (const [row, value] of Object.entries(rows)) report.rows[row] = value;
    if (specWarnings) report.warnings = specWarnings;
    report.ok = Object.values(report.rows).every(row => row.pass);
    report.errors = Object.entries(report.rows).flatMap(([row, value]) => value.pass ? [] : value.detail.map(detail => `${row}: ${detail}`));
    outcome = {available: true, report, command: ['python', '-m', 'glasses3d.g3d_host.spec', '--full'], ms: 5};
  }
  const report = assembleServeReport({createdAt: '2026-10-05T12:00:00.000Z',
    model: {path: 'C:/models/model.glb', file: 'model.glb', bytes: bytes.length, sha256: sha, url, selfContained: true, externalUris: [],
      servedResources: [], unreadableResources: [], containerError: null},
    pageUrl: `${ORIGIN}/?model=${encodeURIComponent(url)}`, reportUrl: `${ORIGIN}/__arview/report.json`, handover: {name: 'model.glb'},
    spec: outcome, arview: checked.report, arviewError: checked.report ? null : checked.error, templeClip: checked.templeClip,
    serveWarnings, serveErrors, server: {pid: 1, instance: 'x'}, build: {}, publicFiles: {}});
  return {report: JSON.parse(JSON.stringify(report)), checked, sha};
}
const texts = lines => lines.map(line => `${line.kind}: ${line.text}`);

test('r9-like (bridge 10.23 mm high): spec.py and arview fail Origin together; the page lists the row, the ORIGIN warning and the sha256', async () => {
  const {report, checked, sha} = await served(r9Like(), {rows: {origin: {pass: false, detail: ['bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm)']}}});
  assert.equal(report.schema, SERVED_REPORT_SCHEMA);
  assert.equal(report.status, 'rendered');
  assert.equal(report.served_sha256, sha); assert.equal(report.rendered_sha256, undefined, 'nothing is rendered when the server reports');
  assert.equal(checked.report.rendered_sha256, sha, 'arview hashed the very bytes served');
  assert.deepEqual(report.failed_rows, [{row: 'origin', title: 'Origin', source: 'spec.py', detail: ['bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm)'], notes: []}]);
  assert.ok(report.rows.every(row => row.agree === true), JSON.stringify(report.rows.map(row => [row.row, row.agree])));
  assert.deepEqual(report.errors, []);
  assert.ok(report.warnings.some(text => text.startsWith('ORIGIN: bridge underside is at y +10.23 mm')), report.warnings.join(' | '));
  assert.equal(report.verdict.spec_ok, false);
  assert.equal(report.temple_clip.cutoff_m, -0.14, 'the temple clip the renderer derives (long arms: the -0.14 m floor)');
  assert.ok(report.live_site_would_fail.would_fail);
  const parsed = parseServedReport(report);
  const lines = servedReportLines(parsed);
  assert.deepEqual(lines[0], {kind: 'error', text: 'Origin: bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm)'});
  assert.ok(lines.some(line => line.kind === 'warning' && line.text.startsWith('ORIGIN: ')), texts(lines).join(' | '));
  assert.deepEqual(digestLine(parsed, null), {kind: 'pending', sha256: sha,
    text: `Served sha256 ${sha}. The renderer re-hashes the bytes it parses when the camera opens.`});
});

test('a millimetre file without spec.py: VALIDATOR UNAVAILABLE first, arview\'s Units row, the rescale listed once as a labelled fallback', async () => {
  const {report} = await served(buildGlb({scale: 1}), {spec: 'unavailable'});
  assert.equal(report.status, 'rendered');
  assert.equal(report.spec.available, false);
  assert.match(report.serve_warnings[0], /^VALIDATOR UNAVAILABLE: no Python interpreter/);
  const units = report.failed_rows.find(row => row.row === 'units');
  assert.ok(units && units.source === 'arview, spec.py unavailable', JSON.stringify(report.failed_rows));
  assert.ok(report.rows.every(row => row.source === 'arview' && row.agree === null));
  const fallback = report.fallbacks.find(entry => entry.subsystem === 'units');
  assert.ok(fallback && /^UNITS: the model is in millimetres/.test(fallback.detail), JSON.stringify(report.fallbacks));
  assert.ok(report.warnings.includes(fallback.detail), 'every fallback is a warning in the report');
  const lines = servedReportLines(parseServedReport(report));
  assert.match(lines[0].text, /^VALIDATOR UNAVAILABLE/);
  assert.ok(lines.some(line => line.kind === 'error' && line.text.startsWith('Units: ') && line.text.endsWith('(arview, spec.py unavailable)')), texts(lines).join(' | '));
  assert.ok(lines.some(line => line.kind === 'fallback' && line.text === `Fallback · units: ${fallback.detail}`));
  assert.ok(!lines.some(line => line.kind === 'warning' && line.text === fallback.detail), 'the fallback is listed once, as a fallback');
});

test('disagreements, a digest mismatch and a check that never finished are loud; nothing is shown as clean', async () => {
  // spec.py passes Origin, arview fails it: the row follows spec.py (the authority) and the disagreement is a warning.
  const disagree = await served(r9Like());
  const origin = disagree.report.rows.find(row => row.row === 'origin');
  assert.deepEqual([origin.pass, origin.source, origin.agree, origin.arview.pass], [true, 'spec.py', false, false]);
  assert.ok(disagree.report.node_check_warnings.some(text => /disagree with spec\.py on origin \(spec\.py pass, arview fail\)/.test(text)), disagree.report.node_check_warnings.join(' | '));
  assert.ok(disagree.report.warnings.some(text => /disagree with spec\.py on origin/.test(text)), 'listed before the camera opens');
  // spec.py read different bytes than the server serves.
  const changed = await served(buildGlb(), {specSha: 'f'.repeat(64)});
  assert.ok(changed.report.errors.some(text => text.startsWith(`spec.py checked bytes with sha256 ${'f'.repeat(64)}`)), changed.report.errors.join(' | '));
  // arview's check timed out: no status, an error first on the page.
  const stalled = await served(buildGlb(), {arviewError: 'arview\'s Node-side check did not finish within 60 s'});
  assert.equal(stalled.report.status, 'not_checked');
  assert.deepEqual(stalled.report.node_check_errors, [stalled.report.errors[0]]);
  assert.ok(stalled.report.errors[0].startsWith('arview\'s check of the model did not complete: arview\'s Node-side check did not finish within 60 s'));
  assert.equal(servedReportLines(parseServedReport(stalled.report))[0].kind, 'error');
  // The server's own errors come first.
  const missing = await served(buildGlb(), {serveErrors: ['the public binary /models/face_landmarker.task is missing']});
  assert.deepEqual(servedReportLines(parseServedReport(missing.report))[0], {kind: 'error', text: 'the public binary /models/face_landmarker.task is missing'});
});

test('after Open camera: the renderer\'s report with spec.py beside it; the rendered sha256 binds the verdict', async () => {
  const r9 = await served(r9Like(), {rows: {origin: {pass: false, detail: ['bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm)']}}});
  const parsed = parseServedReport(r9.report), model = r9.checked.report;
  const lines = renderedReportLines(model, parsed);
  assert.deepEqual(lines[0], {kind: 'error', text: 'Origin: bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm)'});
  assert.equal(lines.filter(line => line.text.startsWith('Origin: ')).length, 1, 'agreeing rows are listed once');
  // A row spec.py fails that the renderer passes is listed as spec.py's failure.
  const spec = await served(buildGlb(), {rows: {components: {pass: false, detail: ['material "Frame acetate" is used by lens and frame primitives']}}});
  const specLines = renderedReportLines(spec.checked.report, parseServedReport(spec.report));
  assert.ok(specLines.some(line => line.kind === 'error' && line.text === 'Components (spec.py; arview\'s renderer passes it): material "Frame acetate" is used by lens and frame primitives'),
    texts(specLines).join(' | '));
  // A row only the renderer fails is a disagreement warning.
  const disagree = await served(r9Like());
  assert.ok(renderedReportLines(disagree.checked.report, parseServedReport(disagree.report))
    .some(line => line.kind === 'warning' && /renderer fails the Origin row but spec\.py passes it/.test(line.text)));
  // spec.py unavailable; the renderer's fallbacks are labelled.
  const mm = await served(buildGlb({scale: 1}), {spec: 'unavailable'});
  const mmLines = renderedReportLines(mm.checked.report, parseServedReport(mm.report));
  assert.ok(mmLines.some(line => line.kind === 'warning' && /^VALIDATOR UNAVAILABLE: no Python interpreter/.test(line.text)));
  assert.ok(mmLines.some(line => line.kind === 'fallback' && line.text.startsWith('Fallback · units: UNITS: ')));
  // Without a served report the renderer's lines stand alone.
  assert.ok(renderedReportLines(model, null).some(line => line.text.startsWith('Origin: ')));
  // The sha256 line.
  assert.deepEqual(digestLine(parsed, r9.sha), {kind: 'ok', sha256: r9.sha, text: `Rendered sha256 ${r9.sha} (the served file). Verdicts bind to these bytes.`});
  assert.equal(digestLine(parsed, 'a'.repeat(64)).kind, 'mismatch');
  assert.match(digestLine(parsed, 'a'.repeat(64)).text, /DIFFERS from the served file's/);
  assert.equal(digestLine(null, r9.sha).text, `Rendered sha256 ${r9.sha}. Verdicts bind to these bytes.`);
  assert.equal(digestLine(null, null), null);
});

test('parseServedReport refuses a report it cannot trust', async () => {
  const {report} = await served(buildGlb());
  assert.doesNotThrow(() => parseServedReport(report));
  for (const [edit, pattern] of [
    [r => {r.schema = 'arview.serve/0';}, /schema/], [r => {delete r.model.url;}, /model/], [r => {r.served_sha256 = 'abc';}, /served_sha256/],
    [r => {delete r.spec_warnings;}, /spec_warnings/], [r => {delete r.node_check_errors;}, /node_check_errors/], [r => {r.failed_rows = [{title: 'Units', detail: []}];}, /failed row/],
    [r => {r.warnings = 'x';}, /warnings/], [r => {r.failed_rows = [{title: 'Units'}];}, /failed row/], [r => {r.fallbacks = [{detail: 'x'}];}, /fallback/],
    [r => {delete r.spec;}, /spec/], [r => {r.status = 3;}, /status/],
  ]) {
    const copy = structuredClone(report); edit(copy);
    assert.throws(() => parseServedReport(copy), pattern);
  }
  assert.throws(() => parseServedReport([]), /not a JSON object/);
});

test('an external buffer beside the GLB: arview\'s Node-side check renders it (no FileLoader, no ProgressEvent) with the not-self-contained warning', async () => {
  const built = buildGlb({externalBuffer: 'model.bin'});
  const {report, checked} = await served(built, {resources: {'model.bin': built.external}});
  assert.equal(checked.error, null); assert.equal(report.status, 'rendered', JSON.stringify(report.refusal));
  assert.ok(report.warnings.includes('not self-contained: external resources ["model.bin"]'), report.warnings.join(' | '));
  assert.deepEqual(report.errors, []);
  // Without the buffer: refused, the missing resource named, and the Format row's note is not listed as a failure.
  const missing = await served(built, {rows: {format: {pass: false, detail: ['GLB 2.0, 999 bytes', 'buffer 0: missing external resource model.bin']}}});
  assert.equal(missing.report.status, 'refused');
  assert.deepEqual(missing.report.failed_rows.find(row => row.row === 'format'),
    {row: 'format', title: 'Format', source: 'spec.py', detail: ['buffer 0: missing external resource model.bin'], notes: ['GLB 2.0, 999 bytes']});
  for (const lines of [servedReportLines(parseServedReport(missing.report)), renderedReportLines(missing.checked.report, parseServedReport(missing.report)), reportLines(missing.checked.report)]) {
    assert.ok(!lines.some(line => /GLB 2\.0, \d+ bytes/.test(line.text)), `a note is never listed: ${texts(lines).join(' | ')}`);
  }
});

test('external resources: read where spec.py reads them (100%.png, ../, links), served with a warning when outside the folder; only absolute or missing ones are refused', async () => {
  const base = path.join(here, '..', '..', 'data', 'arview');
  fs.mkdirSync(base, {recursive: true});
  const work = fs.mkdtempSync(path.join(base, 'serve-resources-'));
  try {
    const folder = path.join(work, 'model'), outside = path.join(work, 'outside');
    fs.mkdirSync(path.join(folder, 'sub'), {recursive: true}); fs.mkdirSync(outside);
    fs.writeFileSync(path.join(folder, 'model.bin'), 'B'); fs.writeFileSync(path.join(folder, 'sub', 'tex.png'), 'P');
    fs.writeFileSync(path.join(folder, '100%.png'), 'X'); fs.writeFileSync(path.join(outside, 'shared.bin'), 'S');
    fs.writeFileSync(path.join(folder, 'my model.glb'), 'glTF'); fs.writeFileSync(path.join(folder, 'my_model.glb'), 'another file');
    fs.writeFileSync(path.join(folder, 'reserved.png'), 'R');
    let linked = false;
    try {fs.symlinkSync(outside, path.join(folder, 'link'), 'junction'); linked = true;} catch {/* no links on this file system */}
    // The served name of 'my model.glb' is 'my_model.glb' (serve.mjs servedName): a different file of that name collides.
    const glbPath = path.join(folder, 'my model.glb'), modelPath = '/__arview/models/abc/my_model.glb';
    const uris = ['model.bin', 'sub/tex.png', './model.bin', '100%.png', '../outside/shared.bin', '%2e%2e/outside/shared.bin', ...linked ? ['link/shared.bin'] : [],
      'http://example.com/x.bin', '//example.com/x.bin', 'missing.png', '%zz-missing.png', 'my_model.glb', 'my%20model.glb', 'reserved.png'];
    try {
      const result = await resolveExternalResources(glbPath, uris, {modelPath, tool: 'arview serve',
        reserved: (_route, decoded) => decoded === '/__arview/models/abc/reserved.png'});
      assert.deepEqual(result.served, ['model.bin', 'sub/tex.png', './model.bin', '100%.png', '../outside/shared.bin', '%2e%2e/outside/shared.bin',
        ...linked ? ['link/shared.bin'] : [], 'my%20model.glb'], 'every file that exists is served');
      // One route per URL: './model.bin' is model.bin's URL and bytes, '%2e%2e/' is '../' (the URL parser normalises both).
      assert.deepEqual(result.resources.map(entry => [entry.uri, entry.route]), [['model.bin', '/__arview/models/abc/model.bin'],
        ['sub/tex.png', '/__arview/models/abc/sub/tex.png'], ['100%.png', '/__arview/models/abc/100%.png'], ['../outside/shared.bin', '/__arview/models/outside/shared.bin'],
        ...linked ? [['link/shared.bin', '/__arview/models/abc/link/shared.bin']] : [], ['my%20model.glb', '/__arview/models/abc/my%20model.glb']],
      'routes are the raw pathnames arview\'s loader requests (percent escapes kept)');
      assert.equal(result.resources.find(entry => entry.uri === '100%.png').bytes.toString(), 'X', '100%.png is the file 100%.png (Python\'s unquote keeps a malformed escape)');
      assert.equal(result.resources.find(entry => entry.uri === 'my%20model.glb').bytes.toString(), 'glTF', 'my%20model.glb is the GLB itself, at its own URL');
      assert.deepEqual(result.outside, ['../outside/shared.bin', '%2e%2e/outside/shared.bin', ...linked ? ['link/shared.bin'] : []]);
      assert.deepEqual(result.resources.filter(entry => entry.outside).map(entry => entry.uri), ['../outside/shared.bin', ...linked ? ['link/shared.bin'] : []]);
      assert.deepEqual(result.unreadable, ['http://example.com/x.bin', '//example.com/x.bin', 'missing.png', '%zz-missing.png']);
      assert.deepEqual(result.unservable, ['my_model.glb', 'reserved.png']);
      assert.equal(result.errors.length, 2, result.errors.join('\n'));
      assert.match(result.errors[0], /"my_model\.glb" .*exists, but the URL arview requests for it \(\/__arview\/models\/abc\/my_model\.glb\) is the model's own URL.*not a §2 verdict/);
      assert.match(result.errors[1], /"reserved\.png" .*one of arview serve's own paths/);
      const warning = uri => result.warnings.find(text => text.startsWith(`external resource "${uri}"`)) ?? '';
      for (const uri of ['../outside/shared.bin', '%2e%2e/outside/shared.bin']) assert.match(warning(uri), /outside the GLB's folder .*served \(spec\.py reads the same file\)/, uri);
      if (linked) assert.match(warning('link/shared.bin'), /\(a link to .*\), outside the GLB's folder/);
      for (const uri of ['http://example.com/x.bin', '//example.com/x.bin']) assert.match(warning(uri), /absolute URL.*answers it 404, so arview refuses the model/, uri);
      assert.match(warning('missing.png'), /cannot be read \(ENOENT\); arview serve answers it 404/);
      assert.match(warning('%zz-missing.png'), /%zz-missing\.png\) cannot be read \(ENOENT\)/, 'a malformed escape is kept, as Python\'s unquote keeps it');
      assert.equal(warning('model.bin'), '', 'a file in the folder carries no warning');
      // A GLB naming itself at the model's own URL is served (as the model), with nothing more to route.
      const self = await resolveExternalResources(path.join(folder, 'model.bin'), ['model.bin'], {modelPath: '/__arview/models/abc/model.bin', tool: 'arview serve'});
      assert.deepEqual([self.served, self.resources, self.unservable, self.errors, self.warnings], [['model.bin'], [], [], [], []]);
    } finally {
      // Unlink the junction first: removing a folder that holds one could follow it (git worktree remove did).
      if (linked) fs.unlinkSync(path.join(folder, 'link'));
    }
  } finally {fs.rmSync(work, {recursive: true, force: true});}
});

test('pythonUnquote is Python\'s urllib.parse.unquote (spec.py resolves every URI with it), malformed escapes and bad UTF-8 included', t => {
  const cases = ['100%.png', '%2e%2e/a.bin', 'a%20b.png', '%%41', '%E9.png', '%C3%A9.png', 'é%41', '%zz%4', '%', 'a%2', '%F0%9F%98%80x', '%C3', '%ED%A0%80',
    '%C3%28', '%E2%82', 'plain.bin'];
  const python = [path.join(here, '..', '..', '.venv', 'Scripts', 'python.exe'), path.join(here, '..', '..', '.venv', 'bin', 'python')].find(file => fs.existsSync(file));
  if (!python) {t.skip('glasses3d/.venv has no Python: the comparison with urllib.parse.unquote cannot run'); return;}
  const run = spawnSync(python, ['-c', 'import json,sys\nfrom urllib.parse import unquote\nprint(json.dumps([unquote(c) for c in json.loads(sys.stdin.buffer.read().decode("utf-8"))]))'],
    {input: JSON.stringify(cases), encoding: 'utf8', timeout: 30_000, windowsHide: true});
  assert.equal(run.status, 0, run.stderr || run.error?.message);
  assert.deepEqual(cases.map(pythonUnquote), JSON.parse(run.stdout));
});

test('EXT_texture_webp and EXT_texture_avif: the Node-side check renders them (three\'s built-in handlers no longer reach self.URL)', async () => {
  const png = Buffer.from(pngBytes(4, 4)).toString('base64');
  const variants = {
    EXT_texture_webp: 'data:image/webp;base64,UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA',
    EXT_texture_avif: 'data:image/avif;base64,AAAA',
  };
  for (const [extension, image] of Object.entries(variants)) {
    const built = buildGlb({patch: doc => {
      doc.images = [{uri: `data:image/png;base64,${png}`}, {uri: image}];
      doc.textures = [{source: 0, extensions: {[extension]: {source: 1}}}];
      doc.materials[0].pbrMetallicRoughness.baseColorTexture = {index: 0};
      doc.extensionsUsed = [...doc.extensionsUsed ?? [], extension];
    }});
    const {report, checked} = await served(built);
    assert.equal(checked.error, null, extension);
    assert.equal(checked.report.status, 'rendered', `${extension}: ${JSON.stringify(checked.report.refusal)}`);
    assert.deepEqual(checked.report.refusal, [], extension);
    assert.equal(report.status, 'rendered', extension); assert.deepEqual(report.errors, [], extension);
  }
  // The texture paths of the parser itself are replaced (the built-ins call parser.loadTextureImage directly).
  const parser = {};
  const plugin = geometryOnly(parser);
  assert.equal(plugin.name, 'ArviewServeGeometryOnly');
  for (const texture of await Promise.all([parser.loadTextureImage(0, 1, null), parser.loadImageSource(1, null), plugin.loadTexture(0)])) assert.equal(texture.isTexture, true);
});

test('after Open camera: spec.py\'s own warnings stay listed, an "unverified" pass is said, both-fail rows show spec.py\'s detail first', async () => {
  // spec.py-only warnings (worded differently from arview's) are listed before AND after the camera opens.
  const specOnly = ['image 0: size could not be read (not a PNG/JPEG/WebP header)', 'origin unverified: bridge underside ... (compressed primitive(s) ["Bridge"], not decoded here, reach the measured region and could change this)'];
  const warned = await served(buildGlb(), {specWarnings: specOnly});
  const parsed = parseServedReport(warned.report);
  assert.deepEqual(parsed.spec_warnings, specOnly);
  const before = servedReportLines(parsed).filter(line => line.kind === 'warning').map(line => line.text);
  const after = renderedReportLines(warned.checked.report, parsed).filter(line => line.kind === 'warning').map(line => line.text);
  for (const text of specOnly) {assert.ok(before.includes(`spec.py: ${text}`), before.join(' | ')); assert.ok(after.includes(`spec.py: ${text}`), after.join(' | '));}
  // spec.py passes Origin only as unverified, arview's renderer fails it: the disagreement line says the pass is no verdict.
  const unverified = await served(r9Like(), {rows: {origin: {pass: true, detail: ['unverified: bridge underside is at y +10.2 mm (must be 0 +/- 1.0 mm)']}}});
  const lines = renderedReportLines(unverified.checked.report, parseServedReport(unverified.report));
  const line = lines.find(entry => entry.kind === 'warning' && entry.text.startsWith('arview\'s renderer fails the Origin row'));
  assert.ok(line && /passes it only as unverified/.test(line.text) && !/authority/.test(line.text), texts(lines).join(' | '));
  assert.ok(lines.some(entry => entry.kind === 'error' && entry.text.startsWith('Origin (arview\'s renderer; spec.py passes it): bridge underside')), texts(lines).join(' | '));
  // Both fail, worded differently: spec.py's detail (the authority) first, then arview's own.
  const both = await served(r9Like(), {rows: {origin: {pass: false, detail: ['bridge underside 10.2 mm above y = 0']}}});
  const bothLines = renderedReportLines(both.checked.report, parseServedReport(both.report)).filter(entry => entry.text.startsWith('Origin'));
  assert.equal(bothLines[0].text, 'Origin: bridge underside 10.2 mm above y = 0');
  assert.ok(bothLines.length >= 2 && bothLines.slice(1).every(entry => entry.text.startsWith('Origin (arview\'s renderer): ')), texts(bothLines).join(' | '));
});

test('after Open camera: a refused renderer never calls spec.py the authority, a spec.py failure is not hidden behind "not checked", the Node check\'s own lines are dropped', async () => {
  // The renderer refused (a missing external buffer); spec.py fails Units (which the renderer could not check) and passes Format.
  const built = buildGlb({externalBuffer: 'model.bin'});
  const refused = await served(built, {rows: {units: {pass: false, detail: ['width 1.4190 units = 1419.0 mm (metres)', 'implausible width 1419.0 mm (expected 100-180 mm)']}}});
  const model = refused.checked.report;
  assert.equal(model.status, 'refused'); assert.match(model.spec.units.detail[0], /^not checked/);
  const lines = renderedReportLines(model, parseServedReport(refused.report));
  assert.ok(!lines.some(entry => /authority/.test(entry.text)), texts(lines).join(' | '));
  assert.ok(lines.some(entry => entry.kind === 'refusal' && /missing external resource/.test(entry.text)));
  assert.ok(lines.some(entry => entry.kind === 'error' && entry.text === 'Units: implausible width 1419.0 mm (expected 100-180 mm)'), texts(lines).join(' | '));
  assert.ok(!lines.some(entry => /not checked/.test(entry.text)), 'rows the renderer could not check are not listed as failures');
  assert.ok(!lines.some(entry => /width 1\.4190 units/.test(entry.text)), 'spec.py\'s note is not a failure');
  // What only the Node-side check said is dropped once the renderer reports: its disagreement and its failure to finish.
  const disagree = await served(r9Like());
  const after = renderedReportLines(disagree.checked.report, parseServedReport(disagree.report));
  assert.ok(disagree.report.node_check_warnings.length > 0);
  for (const text of disagree.report.node_check_warnings) assert.ok(!after.some(entry => entry.text === text), `dropped: ${text}`);
  const stalled = await served(buildGlb(), {arviewError: 'arview\'s Node-side check did not finish within 60 s'});
  const rendered = renderedReportLines(disagree.checked.report, parseServedReport(stalled.report));
  assert.ok(!rendered.some(entry => /did not complete/.test(entry.text)), texts(rendered).join(' | '));
  const serverError = await served(buildGlb(), {serveErrors: ['the public binary /models/face_landmarker.task is missing']});
  assert.deepEqual(renderedReportLines(disagree.checked.report, parseServedReport(serverError.report))[0], {kind: 'error', text: 'the public binary /models/face_landmarker.task is missing'});
});

test('spec.py\'s notes are recognised by wording; every Report.note site in spec.py is one of the known three', () => {
  for (const text of ['GLB 2.0, 6104 bytes', 'width 0.1419 units = 141.9 mm (metres)', 'unverified: the geometry is compressed (Draco/meshopt is not decoded here)',
    'unverified: compressed primitives without bounds']) assert.equal(isSpecPyNote(text), true, text);
  for (const text of ['bridge underside is at y +10.23 mm (must be 0 +/- 1.0 mm)', 'not checked: the file could not be read', 'unreadable GLB: x']) assert.equal(isSpecPyNote(text), false, text);
  const source = fs.readFileSync(path.join(here, '..', '..', 'g3d_host', 'spec.py'), 'utf8');
  const sites = [...source.matchAll(/\b(?:rep|self)\.note\(\s*([^,]+),\s*f?"([^"]*)/g)].map(match => match[2]);
  assert.deepEqual(sites.map(text => text.startsWith('GLB 2.0') ? 'glb' : text.startsWith('width ') ? 'width' : text.startsWith('unverified: ') ? 'unverified' : text).sort(),
    ['glb', 'unverified', 'unverified', 'unverified', 'width'], `spec.py's note sites changed: ${JSON.stringify(sites)}; update SPEC_PY_NOTE_PATTERNS`);
  assert.equal([...source.matchAll(/row\["detail"\] = True, \["unverified: /g)].length, 1, 'the unbounded-compression downgrade writes one "unverified: " note');
});
