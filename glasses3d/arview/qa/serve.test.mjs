/** arview serve end to end (WP1 stage F): the REAL qa/serve.mjs started as its own process on synthetic GLBs (port 0,
 *  a private state directory under glasses3d/data/arview, --ttl-s as a safety net), fetched over HTTP from Node, then
 *  stopped with --stop.
 *
 *  1. Foreground, r9-like (bridge underside 10.23 mm high): every response's isolation and camera headers; the page, a
 *     built script, a public binary and the model (bytes, X-Arview-Sha256, Repr-Digest); 404 for everything else
 *     (sources, the catalog GLBs, unknown /__arview/ paths), 405 for other methods, 403 for a stop without the token; the
 *     report endpoint lists the Origin failure and the ORIGIN warning; a second server on the same port is refused; the
 *     page itself (headless Chromium, hardware GPU) shows the failed row, the warning and the served sha256 before the
 *     camera opens; with face-a streamed as the camera, Open camera shows the renderer's report and the RENDERED sha256,
 *     the fit settles with hair masks, Hold & audit sees the glasses, Close camera ends the stream; --status, then --stop
 *     ends the process (exit 0) and removes its state file. Also: a request with another Host is a 421 (DNS rebinding),
 *     localhost:<port> is redirected (307) to the same address on 127.0.0.1 and the page opened as localhost lands
 *     there, and a file re-exported to the same path while serving is a loud error in the report.
 *  2. A GLB with only an external buffer beside it: the server starts (no ProgressEvent crash), the report says rendered
 *     with the not-self-contained warning, the page and the buffer are served.
 *  3. External resources read where spec.py reads them: a buffer, '100%.png' (a malformed escape; the file exists) and
 *     '../outside.png' (outside the folder; the file exists) are all served (raw request paths), the report says rendered
 *     with an outside-the-folder warning and spec.py passes; then a GLB with two missing images (one with a malformed
 *     escape) gets 404s and is refused, as spec.py fails Format.
 *  4. The check window: with the check held back (ARVIEW_SERVE_TEST_CHECK_DELAY_MS), the state file exists (ready:false),
 *     the report path answers 503 naming the server, --status says "still checking" (exit 0), a second serve on that
 *     port is refused as an arview serve, and --stop ends it during the check.
 *  5. --detach on a millimetre file: the starter returns with the address, the server keeps running in its own process
 *     (WMI on Windows), the report lists the units rescale; --stop ends it. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import {request as httpRequest} from 'node:http';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import {isAlive} from './capture/chromium.mjs';
import {FACE_A_RELATIVE_PATH, FACE_A_SHA256} from './capture/portrait-contract.ts';
import {buildGlb, pngBytes, r9Like} from '../tests/glb-fab.ts';

const ARVIEW = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LENSES = path.resolve(ARVIEW, '..', '..');
const DATA = path.join(LENSES, 'glasses3d', 'data', 'arview');
const SCRIPT = path.join(ARVIEW, 'qa', 'serve.mjs');
const PYTHON = [path.join(LENSES, 'glasses3d/.venv/Scripts/python.exe'), path.join(LENSES, 'glasses3d/.venv/bin/python')].some(file => fs.existsSync(file));
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const URL_LINE = /^(http:\/\/127\.0\.0\.1:\d+\/\?model=\S+)$/m;

function workDir() {
  fs.mkdirSync(DATA, {recursive: true});
  return fs.mkdtempSync(path.join(DATA, 'serve-test-'));
}

/** Start `node qa/serve.mjs args...` and wait (at most `ms`) for the address line. */
function startServe(args, ms = 120_000, env = {}) {
  const child = spawn(process.execPath, [SCRIPT, ...args], {cwd: ARVIEW, windowsHide: true, env: {...process.env, ...env}});
  const output = {stdout: '', stderr: ''};
  child.stdout.on('data', chunk => {output.stdout += chunk;});
  child.stderr.on('data', chunk => {output.stderr += chunk;});
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {child.kill(); reject(new Error(`no address within ${ms / 1000} s; stderr:\n${output.stderr.slice(-2000)}`));}, ms);
    const check = () => {const match = URL_LINE.exec(output.stdout); if (match) {clearTimeout(timer); resolve(match[1]);}};
    child.stdout.on('data', check);
    child.on('exit', code => {clearTimeout(timer); reject(new Error(`serve exited ${code} before it printed an address; stderr:\n${output.stderr.slice(-2000)}`));});
  });
  ready.catch(() => {});
  const exited = new Promise(resolve => child.on('exit', (code, signal) => resolve({code, signal})));
  return {child, output, ready, exited};
}

/** Run `node qa/serve.mjs args...` to completion (killed after `ms`). */
function runServe(args, ms = 150_000) {
  return new Promise(resolve => {
    const child = spawn(process.execPath, [SCRIPT, ...args], {cwd: ARVIEW, windowsHide: true});
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => {stdout += chunk;});
    child.stderr.on('data', chunk => {stderr += chunk;});
    const timer = setTimeout(() => child.kill(), ms);
    child.on('exit', code => {clearTimeout(timer); resolve({code, stdout, stderr});});
  });
}

/** One GET with an explicit Host header (fetch does not let a test set it). Resolves {status, headers, body}. */
function rawGet(port, route, host) {
  return new Promise((resolve, reject) => {
    const request = httpRequest({host: '127.0.0.1', port, path: route, headers: {Host: host}, agent: false}, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks).toString('utf8')}));
    });
    request.setTimeout(10_000, () => request.destroy(new Error('no answer within 10 s')));
    request.on('error', reject);
    request.end();
  });
}

/** Wait (at most `ms`) for a state file in `stateDir`; resolves its JSON. */
async function waitForState(stateDir, ms) {
  for (const started = Date.now(); Date.now() - started < ms; await sleep(200)) {
    for (const name of fs.existsSync(stateDir) ? fs.readdirSync(stateDir) : []) {
      if (!/^port-\d+\.json$/.test(name)) continue;
      try {return JSON.parse(fs.readFileSync(path.join(stateDir, name), 'utf8'));} catch {/* being written */}
    }
  }
  throw new Error(`no state file in ${stateDir} within ${ms / 1000} s`);
}

function isolationHeaders(response, what) {
  assert.equal(response.headers.get('cross-origin-opener-policy'), 'same-origin', `${what}: COOP`);
  assert.equal(response.headers.get('cross-origin-embedder-policy'), 'require-corp', `${what}: COEP`);
  assert.equal(response.headers.get('cross-origin-resource-policy'), 'same-origin', `${what}: CORP`);
  assert.match(response.headers.get('permissions-policy') ?? '', /camera=\(self\)/, `${what}: camera permission`);
  assert.equal(response.headers.get('cache-control'), 'no-store', `${what}: no-store`);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff', `${what}: nosniff`);
}

test('foreground serve: headers, page, model with its sha256, the report endpoint, the page shows it, --stop', {timeout: 300_000}, async () => {
  const work = workDir(), stateDir = path.join(work, 'state');
  const glbPath = path.join(work, 'r9-like.glb'), bytes = Buffer.from(r9Like().glb), sha = sha256(bytes);
  fs.writeFileSync(glbPath, bytes);
  const server = startServe([glbPath, '--port', '0', '--state-dir', stateDir, '--ttl-s', '280']);
  let browser = null;
  try {
    const address = await server.ready;
    const page = new URL(address), origin = page.origin, port = Number(page.port);
    const modelUrl = page.searchParams.get('model'), reportUrl = page.searchParams.get('report');
    assert.equal(modelUrl, `${origin}/__arview/models/${sha}/r9-like.glb`);
    assert.equal(reportUrl, `${origin}/__arview/report.json`);
    assert.equal(page.searchParams.get('sha256'), sha);
    assert.equal(page.searchParams.get('name'), 'r9-like.glb');
    const state = JSON.parse(fs.readFileSync(path.join(stateDir, `port-${port}.json`), 'utf8'));
    assert.equal(state.pid, server.child.pid); assert.equal(state.url, address); assert.equal(state.sha256, sha); assert.match(state.token, /^[0-9a-f]{48}$/);
    assert.equal(state.ready, true);
    // DNS rebinding: another Host is refused before anything is answered. localhost:<port> is redirected to the same path
    // and query on 127.0.0.1 (one origin: the handover URLs are 127.0.0.1's, which a localhost page could not fetch).
    for (const host of [`evil.example:${port}`, `127.0.0.1:${port + 1}`, '127.0.0.1', `localhost:${port + 1}`]) {
      const response = await rawGet(port, '/__arview/report.json', host);
      assert.equal(response.status, 421, host); assert.ok(!response.body.includes(sha), 'nothing of the report leaks');
    }
    const local = await rawGet(port, `/${page.search}`, `localhost:${port}`);
    assert.equal(local.status, 307); assert.equal(local.headers.location, address); assert.ok(!local.body.includes('arview.serve/1'), 'the redirect carries no report');
    assert.equal((await rawGet(port, '/__arview/report.json', `localhost:${port}`)).headers.location, reportUrl);

    // The page (with and without the handover query) and a script it loads.
    const index = await fetch(address);
    assert.equal(index.status, 200); isolationHeaders(index, 'page');
    assert.match(index.headers.get('content-type'), /^text\/html/);
    const html = await index.text();
    assert.match(html, /id="model-digest"/); assert.match(html, /<script type="module"[^>]+src="\/assets\/main-[^"]+\.js"/);
    assert.equal(await (await fetch(`${origin}/`)).text(), html);
    const script = await fetch(origin + /src="(\/assets\/main-[^"]+\.js)"/.exec(html)[1]);
    assert.equal(script.status, 200); isolationHeaders(script, 'script'); assert.match(script.headers.get('content-type'), /^text\/javascript/);
    // A public binary, read-only from ar/public, and what is never served.
    const face = await fetch(`${origin}/models/canonical-face.json`);
    assert.equal(face.status, 200); isolationHeaders(face, 'public file');
    assert.ok(Buffer.from(await face.arrayBuffer()).equals(fs.readFileSync(path.join(LENSES, 'ar/public/models/canonical-face.json'))));
    for (const route of ['/models/amber-horizon.glb', '/src/main.ts', '/package.json', '/__arview/models/x.glb', '/__arview/nope', '/qa/serve.mjs']) {
      const response = await fetch(origin + route);
      assert.equal(response.status, 404, route); isolationHeaders(response, route);
    }
    assert.equal((await fetch(`${origin}/`, {method: 'POST'})).status, 405);
    assert.equal((await fetch(`${origin}/__arview/stop`, {method: 'POST'})).status, 403, 'a stop without the token is refused');
    assert.equal((await fetch(`${origin}/__arview/stop`, {method: 'POST', headers: {'X-Arview-Stop-Token': 'f'.repeat(48)}})).status, 403);

    // The model, with its sha256.
    const model = await fetch(modelUrl);
    assert.equal(model.status, 200); isolationHeaders(model, 'model');
    assert.equal(model.headers.get('content-type'), 'model/gltf-binary');
    assert.equal(model.headers.get('x-arview-sha256'), sha);
    assert.equal(model.headers.get('repr-digest'), `sha-256=:${Buffer.from(sha, 'hex').toString('base64')}:`);
    assert.equal(model.headers.get('etag'), `"${sha}"`);
    assert.ok(Buffer.from(await model.arrayBuffer()).equals(bytes));
    const head = await fetch(modelUrl, {method: 'HEAD'});
    assert.equal(head.status, 200); assert.equal(head.headers.get('content-length'), String(bytes.length)); assert.equal((await head.arrayBuffer()).byteLength, 0);

    // The report endpoint: the Origin failure, the ORIGIN warning, the sha256 the verdicts bind to.
    const reportResponse = await fetch(reportUrl);
    assert.equal(reportResponse.status, 200); isolationHeaders(reportResponse, 'report');
    const report = await reportResponse.json();
    assert.equal(report.schema, 'arview.serve/1');
    assert.equal(report.model.url, modelUrl); assert.equal(report.served_sha256, sha); assert.equal(report.page_url, address);
    assert.equal(report.status, 'rendered');
    assert.equal(report.server.pid, server.child.pid);
    assert.ok(report.warnings.some(text => /^ORIGIN: bridge underside is at y \+10\.23 mm/.test(text)), report.warnings.join(' | '));
    const origin_ = report.failed_rows.find(row => row.row === 'origin');
    assert.ok(origin_ && origin_.detail.some(text => text.includes('+10.23 mm')), JSON.stringify(report.failed_rows));
    if (PYTHON) {
      assert.equal(report.spec.available, true, report.spec.reason); assert.equal(report.spec.ok, false);
      assert.equal(origin_.source, 'spec.py'); assert.ok(report.rows.every(row => row.agree === true), JSON.stringify(report.rows));
    } else assert.ok(report.warnings.some(text => text.startsWith('VALIDATOR UNAVAILABLE')));
    assert.deepEqual(report.errors, []);
    assert.equal(report.build.files > 0, true);

    // A second server on the same port is refused (the state file answers with its identity).
    const second = await runServe([glbPath, '--port', String(port), '--state-dir', stateDir], 60_000);
    assert.equal(second.code, 1, second.stderr); assert.match(second.stderr, /already serves .*r9-like\.glb/);

    // The page itself, driven like the owner drives it, with face-a (the only face image arview uses, read read-only from
    // ar/qa/fixtures and pinned) streamed as the camera (the port of the old qa/astra-preview-smoke.mjs synthetic webcam):
    // before the camera, the served report and sha256; after Open camera, the renderer's report and the RENDERED sha256,
    // the fit settles on the face with hair masks, Hold & audit shows the glasses changing visible pixels, Close camera
    // ends the stream.
    const faceBytes = fs.readFileSync(path.join(ARVIEW, FACE_A_RELATIVE_PATH));
    assert.equal(sha256(faceBytes), FACE_A_SHA256, 'face-a is the pinned fixture');
    browser = await chromium.launch({headless: true, channel: 'chromium', timeout: 60_000, args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
    const context = await browser.newContext({viewport: {width: 1400, height: 1000}});
    const tab = await context.newPage();
    tab.setDefaultTimeout(30_000);
    const failed = [], blocked = [], pageErrors = [], shaderErrors = [];
    tab.on('response', response => {if (response.status() >= 400) failed.push(`${response.status()} ${response.url()}`);});
    tab.on('pageerror', error => pageErrors.push(error.message));
    tab.on('console', message => {
      if (message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(message.text())) shaderErrors.push(message.text().slice(0, 600));
    });
    await tab.route('**/*', route => {
      const url = route.request().url();
      if (url === `${origin}/__arview-test/face-a.jpg`) return route.fulfill({contentType: 'image/jpeg', body: faceBytes});
      // The localhost address is allowed only to be redirected (the owner may type it; README §3).
      if (url.startsWith(`${origin}/`) || url.startsWith(`http://localhost:${port}/`) || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
      blocked.push(url); return route.abort();
    });
    await tab.addInitScript(() => {
      const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
      const context2d = source.getContext('2d'), image = new Image();
      const state = {installed: false, requests: 0, streams: []}; window.__syntheticCamera = state;
      const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
      image.src = '/__arview-test/face-a.jpg';
      const draw = () => {if (image.complete && image.naturalWidth) {context2d.fillStyle = '#000'; context2d.fillRect(0, 0, 1280, 720); context2d.drawImage(image, 280, 0, 720, 720);}};
      setInterval(draw, 1000 / 30);
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
        state.requests++; await ready; draw();
        const stream = source.captureStream(30); state.streams.push(stream); return stream;
      }});
      state.installed = true;
    });
    // Opened as localhost (the review found the page broken there: COEP blocked the 127.0.0.1 report and model), it
    // lands on the 127.0.0.1 address, where everything below is same-origin.
    await tab.goto(address.replace(`http://127.0.0.1:${port}/`, `http://localhost:${port}/`));
    assert.equal(tab.url(), address, 'localhost was redirected to the printed 127.0.0.1 address');
    await tab.waitForFunction(() => !document.getElementById('model-digest').hidden && document.querySelectorAll('#model-warnings li').length > 0);
    const before = await tab.evaluate(() => ({digest: document.getElementById('model-digest').textContent, code: document.querySelector('#model-digest code')?.textContent,
      lines: [...document.querySelectorAll('#model-warnings li')].map(li => [li.className, li.textContent]), isolated: crossOriginIsolated,
      ignored: document.getElementById('config-note').textContent.includes('IGNORED'), camera: window.__syntheticCamera.requests, open: window.__ar.isOpen()}));
    assert.equal(before.code, sha); assert.match(before.digest, /^Served sha256 /);
    assert.equal(before.isolated, true, 'the page is cross-origin isolated'); assert.equal(before.ignored, false, 'every address option is known');
    assert.deepEqual([before.camera, before.open], [0, false], 'nothing starts before Open camera');
    assert.ok(before.lines.some(([kind, text]) => kind === 'model-error' && text.startsWith('Origin: bridge underside is at y +10.23 mm')), JSON.stringify(before.lines));
    assert.ok(before.lines.some(([kind, text]) => kind === 'model-warning' && text.startsWith('ORIGIN: ')), JSON.stringify(before.lines));
    await tab.locator('#start').click();
    await tab.waitForFunction(() => /^Rendered sha256/.test(document.getElementById('model-digest').textContent ?? '')
      || document.querySelector('.stage')?.dataset.state === 'error', null, {timeout: 60_000});
    const after = await tab.evaluate(() => ({digest: document.getElementById('model-digest').textContent, kind: document.getElementById('model-digest').dataset.kind,
      state: document.querySelector('.stage').dataset.state, guidance: document.getElementById('guidance').textContent,
      lines: [...document.querySelectorAll('#model-warnings li')].map(li => [li.className, li.textContent]), rendered: window.__ar.modelReport()?.rendered_sha256}));
    assert.notEqual(after.state, 'error', `the session failed: ${after.guidance}`);
    assert.equal(after.kind, 'ok'); assert.equal(after.rendered, sha);
    assert.equal(after.digest, `Rendered sha256 ${sha} (the served file). Verdicts bind to these bytes.`);
    assert.ok(after.lines.some(([kind, text]) => kind === 'model-error' && text.startsWith('Origin: ')), JSON.stringify(after.lines));
    // The live fit settles on face-a (tracked, with hair masks) and the glasses are drawn.
    const fittedOrFailed = () => {
      const fit = window.__ar.fitting(), sample = window.__ar.samplesAfter(0).at(-1);
      return document.querySelector('.stage')?.dataset.state === 'error' || (window.__ar.isOpen() && fit?.state === 'fitted' && fit.ready
        && sample?.hasFace && sample.hasMask && sample.sessionId === document.querySelector('.stage').dataset.sessionId);
    };
    await tab.waitForFunction(fittedOrFailed, null, {timeout: 90_000});
    const fitted = await tab.evaluate(() => ({state: document.querySelector('.stage').dataset.state, guidance: document.getElementById('guidance').textContent,
      fitting: window.__ar.fitting(), requests: window.__syntheticCamera.requests, safeFallback: window.__ar.samplesAfter(0).at(-1)?.native?.['render.safeFallback']}));
    assert.notEqual(fitted.state, 'error', `the session failed: ${fitted.guidance}`);
    assert.equal(fitted.requests, 1); assert.equal(fitted.fitting.progress, 1); assert.equal(fitted.safeFallback, false);
    const audit = await tab.evaluate(async () => {
      const value = await window.__ar.audit(); if (!value) return null;
      const decode = async url => {
        const image = new Image(); image.src = url; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const context2d = canvas.getContext('2d'); context2d.drawImage(image, 0, 0);
        return context2d.getImageData(0, 0, canvas.width, canvas.height);
      };
      const [withGlasses, without] = await Promise.all([decode(value.images.noShadowPngDataUrl), decode(value.images.backgroundPngDataUrl)]);
      let visible = 0;
      for (let i = 0; i < withGlasses.data.length; i += 4) if (Math.max(...[0, 1, 2].map(c => Math.abs(withGlasses.data[i + c] - without.data[i + c]))) > 8) visible++;
      return {visible, ready: value.fitting.ready, safeFallback: value.guard.safeFallback, checkError: value.checkError};
    });
    assert.ok(audit, 'Hold & audit produced no held frame');
    assert.ok(audit.visible > 100, `the glasses change ${audit.visible} visible pixels (without shadows); expected > 100`);
    assert.deepEqual([audit.ready, audit.safeFallback, audit.checkError], [true, false, null]);
    await tab.locator('#stop').click();
    await tab.waitForFunction(() => !window.__ar.isOpen() && window.__syntheticCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')));
    assert.deepEqual(failed, [], 'no request of the page failed');
    assert.deepEqual(blocked, [], 'the page requested nothing outside the loopback origin');
    assert.deepEqual(pageErrors, []); assert.deepEqual(shaderErrors, []);
    await browser.close(); browser = null;

    // The file re-exported to the same path while serving: the report says so, loudly; the original bytes are still
    // what is served (and what the sha256 binds); putting them back clears the error.
    fs.writeFileSync(glbPath, Buffer.from(buildGlb().glb));
    const stale = await (await fetch(reportUrl)).json();
    assert.ok(stale.errors.some(text => text.startsWith('the file on disk changed: ') && text.includes(sha) && /Restart arview serve/.test(text)), stale.errors.join(' | '));
    assert.ok(Buffer.from(await (await fetch(modelUrl)).arrayBuffer()).equals(bytes), 'the served bytes are unchanged');
    fs.writeFileSync(glbPath, bytes);
    assert.deepEqual((await (await fetch(reportUrl)).json()).errors, []);
    assert.match(server.output.stderr, /ERROR: the file on disk changed/);

    // --status, then --stop: the process ends with exit 0 and its state file is gone.
    const status = await runServe(['--status', '--port', String(port), '--state-dir', stateDir], 30_000);
    assert.equal(status.code, 0, status.stderr);
    assert.equal(JSON.parse(status.stdout).serving, true);
    const stop = await runServe(['--stop', '--port', String(port), '--state-dir', stateDir], 30_000);
    assert.equal(stop.code, 0, stop.stderr); assert.match(stop.stdout, /stopped arview serve on port/);
    const exit = await Promise.race([server.exited, sleep(10_000).then(() => null)]);
    assert.deepEqual(exit, {code: 0, signal: null}, 'the server exited 0 on --stop');
    assert.equal(fs.existsSync(path.join(stateDir, `port-${port}.json`)), false);
    assert.match(server.output.stderr, /stopping \(a --stop request\)/);
    const again = await runServe(['--stop', '--port', String(port), '--state-dir', stateDir], 30_000);
    assert.equal(again.code, 0); assert.match(again.stderr, /nothing is serving on port/);
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (server.child.exitCode === null) server.child.kill();
    fs.rmSync(work, {recursive: true, force: true});
  }
});

test('an external buffer beside the GLB: the server starts, the report says rendered with the not-self-contained warning, the page and buffer are served', {timeout: 180_000}, async () => {
  const work = workDir(), stateDir = path.join(work, 'state');
  const built = buildGlb({externalBuffer: 'model.bin'});
  const glbPath = path.join(work, 'external.glb');
  fs.writeFileSync(glbPath, Buffer.from(built.glb)); fs.writeFileSync(path.join(work, 'model.bin'), built.external);
  const server = startServe([glbPath, '--port', '0', '--state-dir', stateDir, '--ttl-s', '170']);
  try {
    const page = new URL(await server.ready), modelUrl = page.searchParams.get('model');
    const index = await fetch(page);
    assert.equal(index.status, 200); isolationHeaders(index, 'page'); assert.match(await index.text(), /id="model-digest"/);
    const buffer = await fetch(new URL('model.bin', modelUrl));
    assert.equal(buffer.status, 200); isolationHeaders(buffer, 'external buffer');
    assert.ok(Buffer.from(await buffer.arrayBuffer()).equals(Buffer.from(built.external)));
    const report = await (await fetch(page.searchParams.get('report'))).json();
    assert.equal(report.status, 'rendered', JSON.stringify(report.refusal));
    assert.ok(report.warnings.includes('not self-contained: external resources ["model.bin"]'), report.warnings.join(' | '));
    assert.ok(report.serve_warnings.some(text => /not self-contained \(1 external resource/.test(text)), report.serve_warnings.join(' | '));
    assert.deepEqual(report.model.served_resources, ['model.bin']); assert.deepEqual(report.model.unreadable_resources, []);
    assert.deepEqual(report.errors, []);
    if (PYTHON) assert.equal(report.spec.available, true, report.spec.reason);
    assert.doesNotMatch(server.output.stderr, /ProgressEvent|uncaught/);
  } finally {
    if (server.child.exitCode === null) server.child.kill();
    await Promise.race([server.exited, sleep(5000)]);
    fs.rmSync(work, {recursive: true, force: true});
  }
});

test('external resources: every file that exists is served where spec.py finds it (\'100%.png\', \'../\'), with a warning outside the folder; only a missing one is refused, as spec.py fails it', {timeout: 240_000}, async () => {
  const work = workDir(), stateDir = path.join(work, 'state'), folder = path.join(work, 'model');
  fs.mkdirSync(folder);
  const png = Buffer.from(pngBytes(4, 4));
  // 1. Every resource exists: '100%.png' (a malformed escape, which Python's unquote keeps) and '../outside.png' (outside
  //    the GLB's folder). spec.py passes; arview must render, never refuse.
  const present = buildGlb({externalBuffer: 'model.bin', patch: doc => {
    doc.images = [{uri: '100%.png'}, {uri: '../outside.png'}]; doc.textures = [{source: 0}, {source: 1}];
    doc.materials[0].pbrMetallicRoughness.baseColorTexture = {index: 0};
  }});
  const glbPath = path.join(folder, 'external.glb');
  fs.writeFileSync(glbPath, Buffer.from(present.glb)); fs.writeFileSync(path.join(folder, 'model.bin'), present.external);
  fs.writeFileSync(path.join(folder, '100%.png'), png); fs.writeFileSync(path.join(work, 'outside.png'), png);
  let server = startServe([glbPath, '--port', '0', '--state-dir', stateDir, '--ttl-s', '230']);
  try {
    const page = new URL(await server.ready), modelUrl = page.searchParams.get('model');
    const buffer = await fetch(new URL('model.bin', modelUrl));
    assert.equal(buffer.status, 200); isolationHeaders(buffer, 'external buffer');
    assert.ok(Buffer.from(await buffer.arrayBuffer()).equals(Buffer.from(present.external)));
    // Requested exactly as the browser requests them: '100%.png' keeps its '%' (the server looks resources up raw).
    for (const uri of ['100%.png', '../outside.png']) {
      const response = await fetch(new URL(uri, modelUrl));
      assert.equal(response.status, 200, uri); isolationHeaders(response, uri);
      assert.ok(Buffer.from(await response.arrayBuffer()).equals(png), uri);
    }
    assert.match(new URL('100%.png', modelUrl).pathname, /\/100%\.png$/, 'the request path keeps the malformed escape');
    const report = await (await fetch(page.searchParams.get('report'))).json();
    assert.equal(report.status, 'rendered', JSON.stringify(report.refusal)); assert.deepEqual(report.refusal, []);
    assert.deepEqual(report.model.served_resources, ['model.bin', '100%.png', '../outside.png']);
    assert.deepEqual(report.model.unreadable_resources, []); assert.deepEqual(report.model.outside_resources, ['../outside.png']);
    assert.deepEqual(report.errors, []);
    assert.ok(report.serve_warnings.some(text => /"\.\.\/outside\.png" is .*outside\.png, outside the GLB's folder .*served \(spec\.py reads the same file\)/.test(text)), report.serve_warnings.join(' | '));
    assert.ok(!report.serve_warnings.some(text => /"100%\.png"/.test(text)), 'a file in the folder needs no warning');
    if (PYTHON) {
      assert.equal(report.spec.available, true, report.spec.reason); assert.equal(report.spec.ok, true, JSON.stringify(report.failed_rows));
      assert.ok(report.rows.every(row => row.agree !== false), JSON.stringify(report.rows.map(row => [row.row, row.agree])));
    }
    assert.doesNotMatch(server.output.stderr, /URIError|uncaught/);
    const stop = await runServe(['--stop', '--port', page.port, '--state-dir', stateDir], 30_000);
    assert.equal(stop.code, 0, stop.stderr);
    await Promise.race([server.exited, sleep(10_000)]);

    // 2. A missing image (and a missing one whose URI has a malformed escape): 404, and refused, as spec.py fails Format.
    const missing = buildGlb({externalBuffer: 'model.bin', patch: doc => {
      doc.images = [{uri: 'missing_texture.png'}, {uri: '%zz-missing.png'}]; doc.textures = [{source: 0}];
      doc.materials[0].pbrMetallicRoughness.baseColorTexture = {index: 0};
    }});
    const missingPath = path.join(folder, 'missing.glb');
    fs.writeFileSync(missingPath, Buffer.from(missing.glb)); fs.writeFileSync(path.join(folder, 'model.bin'), missing.external);
    server = startServe([missingPath, '--port', '0', '--state-dir', stateDir, '--ttl-s', '170']);
    const second = new URL(await server.ready), secondModel = second.searchParams.get('model');
    for (const uri of ['missing_texture.png', '%zz-missing.png']) assert.equal((await fetch(new URL(uri, secondModel))).status, 404, uri);
    const refused = await (await fetch(second.searchParams.get('report'))).json();
    assert.equal(refused.status, 'refused');
    assert.ok(refused.refusal.some(text => /missing external resource/.test(text) && /missing_texture\.png/.test(text) && /%zz-missing\.png/.test(text)), JSON.stringify(refused.refusal));
    assert.deepEqual(refused.model.served_resources, ['model.bin']);
    assert.deepEqual(refused.model.unreadable_resources, ['missing_texture.png', '%zz-missing.png']);
    assert.ok(refused.serve_warnings.some(text => /"missing_texture\.png".*cannot be read \(ENOENT\); arview serve answers it 404/.test(text)), refused.serve_warnings.join(' | '));
    assert.equal(refused.failed_rows.find(row => row.row === 'format')?.detail.length > 0, true);
    if (PYTHON) assert.equal(refused.rows.find(row => row.row === 'format').spec_py.pass, false, 'spec.py fails Format for the same missing files');
    assert.ok(refused.failed_rows.every(row => row.detail.every(text => !/^GLB 2\.0, \d+ bytes$/.test(text))), 'notes are not failures');
    assert.doesNotMatch(server.output.stderr, /URIError|uncaught/);
  } finally {
    if (server.child.exitCode === null) server.child.kill();
    await Promise.race([server.exited, sleep(5000)]);
    fs.rmSync(work, {recursive: true, force: true});
  }
});

test('while the model is checked: the state file exists, the report path names the server (503), --status and a second serve know it, --stop ends it', {timeout: 180_000}, async () => {
  const work = workDir(), stateDir = path.join(work, 'state');
  const glbPath = path.join(work, 'slow.glb');
  fs.writeFileSync(glbPath, Buffer.from(buildGlb().glb));
  const server = startServe([glbPath, '--port', '0', '--state-dir', stateDir, '--ttl-s', '170'], 120_000, {ARVIEW_SERVE_TEST_CHECK_DELAY_MS: '90000'});
  try {
    const state = await waitForState(stateDir, 120_000);
    assert.equal(state.ready, false); assert.equal(state.pid, server.child.pid);
    const pending = await fetch(`${state.origin}/__arview/report.json`);
    assert.equal(pending.status, 503); isolationHeaders(pending, 'pending report');
    const body = await pending.json();
    assert.deepEqual([body.schema, body.ready, body.server.pid, body.server.instance], ['arview.serve-pending/1', false, state.pid, state.instance]);
    const index = await fetch(`${state.origin}/`);
    assert.equal(index.status, 503); assert.match(await index.text(), /still checking the model/);
    const status = await runServe(['--status', '--port', String(state.port), '--state-dir', stateDir], 30_000);
    assert.equal(status.code, 0, status.stderr);
    const parsed = JSON.parse(status.stdout);
    assert.deepEqual([parsed.serving, parsed.checking, parsed.detail], [false, true, 'still checking the model']);
    const second = await runServe([glbPath, '--port', String(state.port), '--state-dir', stateDir], 60_000);
    assert.equal(second.code, 1, second.stderr); assert.match(second.stderr, /has an arview serve still checking .*slow\.glb/);
    const asked = Date.now();
    const stop = await runServe(['--stop', '--port', String(state.port), '--state-dir', stateDir], 30_000);
    assert.equal(stop.code, 0, stop.stderr); assert.match(stop.stdout, /stopped arview serve on port/); assert.match(stop.stderr, /still checking the model; asking it to stop/);
    const exit = await Promise.race([server.exited, sleep(10_000).then(() => null)]);
    assert.deepEqual(exit, {code: 0, signal: null}, 'the server exited 0 during its check');
    assert.ok(Date.now() - asked < 20_000, 'the stop did not wait for the held-back check');
    assert.equal(fs.existsSync(path.join(stateDir, `port-${state.port}.json`)), false);
    assert.match(server.output.stderr, /WARNING: ARVIEW_SERVE_TEST_CHECK_DELAY_MS=90000/);
  } finally {
    if (server.child.exitCode === null) server.child.kill();
    await Promise.race([server.exited, sleep(5000)]);
    fs.rmSync(work, {recursive: true, force: true});
  }
});

test('--detach: the server outlives its starter, the report lists the units rescale, --stop ends it', {timeout: 300_000}, async () => {
  const work = workDir(), stateDir = path.join(work, 'state');
  const glbPath = path.join(work, 'millimetres.glb');
  fs.writeFileSync(glbPath, Buffer.from(buildGlb({scale: 1}).glb));
  let pid = null;
  try {
    const started = await runServe([glbPath, '--port', '0', '--detach', '--state-dir', stateDir, '--ttl-s', '280'], 200_000);
    assert.equal(started.code, 0, `${started.stdout}\n${started.stderr}`);
    const address = URL_LINE.exec(started.stdout)?.[1];
    assert.ok(address, started.stdout);
    if (process.platform === 'win32') assert.match(started.stderr, /started the detached server \(pid \d+, wmi\)/, 'created through WMI, outside the starter\'s tree and job');
    const page = new URL(address), port = Number(page.port);
    const state = JSON.parse(fs.readFileSync(path.join(stateDir, `port-${port}.json`), 'utf8'));
    pid = state.pid;
    assert.ok(isAlive(pid), 'the server is still running after its starter exited');
    const report = await (await fetch(page.searchParams.get('report'))).json();
    assert.equal(report.server.pid, pid); assert.equal(report.server.detached, true);
    assert.equal(report.status, 'rendered');
    const units = report.fallbacks.find(entry => entry.subsystem === 'units');
    assert.ok(units && /^UNITS: the model is in millimetres/.test(units.detail), JSON.stringify(report.fallbacks));
    assert.ok(report.warnings.includes(units.detail));
    assert.ok(report.failed_rows.some(row => row.row === 'units'), JSON.stringify(report.failed_rows));
    const model = await fetch(page.searchParams.get('model'), {method: 'HEAD'});
    assert.equal(model.status, 200); isolationHeaders(model, 'detached model');
    assert.ok(fs.readFileSync(state.log, 'utf8').includes(address), 'the detached server logs its address');
    const stop = await runServe(['--stop', '--port', String(port), '--state-dir', stateDir], 30_000);
    assert.equal(stop.code, 0, stop.stderr);
    for (let waited = 0; waited < 10_000 && isAlive(pid); waited += 200) await sleep(200);
    assert.equal(isAlive(pid), false, 'the detached server ended on --stop');
    assert.equal(fs.existsSync(path.join(stateDir, `port-${port}.json`)), false);
    assert.match(fs.readFileSync(state.log, 'utf8'), /stopped/);
  } finally {
    if (pid && isAlive(pid)) {try {process.kill(pid);} catch {/* gone */}}
    await sleep(300);
    fs.rmSync(work, {recursive: true, force: true});
  }
});
