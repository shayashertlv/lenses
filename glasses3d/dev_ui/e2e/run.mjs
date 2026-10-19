/** Real M12 UI proof driver. No endpoint is mocked; all paid buttons use an
 * injected, offline-only Python server. Evidence remains under the proof root.
 * Usage: node glasses3d/dev_ui/e2e/run.mjs <browser-config.json>
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const requireAr = createRequire(path.resolve(HERE, '../../arview/package.json'));
const {chromium} = requireAr('@playwright/test');
const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const base = new URL(config.base_url);
assert.equal(base.hostname, '127.0.0.1');
assert(Number(base.port) >= 8290 && Number(base.port) <= 8299, 'Dedicated D1 UI port required');
const proofRoot = path.resolve(config.proof_root);
const output = path.resolve(config.output_root ?? path.join(proofRoot, 'browser'));
assert(output.startsWith(proofRoot + path.sep), 'Browser evidence must remain in the private proof');
await fs.mkdir(output, {recursive: true});
const TIMEOUT = config.timeout_ms ?? 3_600_000;
let report = {schema: 'g3d.m12_browser_proof/1', started_at: new Date().toISOString(),
  offline_test_mode: true, actual_spend_usd: 0, scenarios: [], requests: [], responses: [],
  screenshots: [], captures: [], assertions: [], page_errors: [], network_violations: [],
  fixtures: {description: 'Synthetic eyewear for the offline browser proof.\nOnly these typed owner inputs belong in the job.',
    specs_text: '{"model_number":"D1 synthetic","frame_width_mm":140}',
    names: {success: 'D1 finished', cutoff: 'D1 cutoff', slow: 'D1 survival'}}, state: {}};
if (config.resume) {
  const previous = JSON.parse(await fs.readFile(path.join(output, 'browser-report.json'), 'utf8'));
  assert.equal(previous.schema, report.schema);
  const attempt = (previous.attempts?.length ?? 0) + 1;
  await fs.writeFile(path.join(output, `attempt-${attempt}.json`), JSON.stringify(previous, null, 2) + '\n', {flag: 'wx'});
  report = previous;
  report.attempts ??= [];
  report.attempts.push({archive: `attempt-${attempt}.json`, status: previous.status,
    started_at: previous.started_at, ended_at: previous.ended_at, error: previous.error,
    incomplete_scenarios: previous.scenarios.filter(row => row.status !== 'passed')});
  report.scenarios = report.scenarios.filter(row => row.status === 'passed');
  report.resumed_at = new Date().toISOString();
  delete report.error; delete report.ended_at;
}
report.status = 'running';
const initialPageErrorCount = report.page_errors.length;
let browser, context, page, activeScenario = null, screenshotCount = report.screenshots.length;
let pageNavigationAt = 0;
const pendingEvidence = new Set();
const responseWaiters = new Set();
const digest = b => createHash('sha256').update(b).digest('hex');
const write = async (name, value) => fs.writeFile(path.join(output, name), JSON.stringify(value, null, 2) + '\n');
let checkpointWrites = Promise.resolve();
const checkpoint = () => {
  const bytes = JSON.stringify(report, null, 2) + '\n';
  checkpointWrites = checkpointWrites.then(async () => {
    const temporary = path.join(output, 'browser-report.writing.json');
    await fs.writeFile(temporary, bytes);
    await fs.rename(temporary, path.join(output, 'browser-report.json'));
  });
  return checkpointWrites;
};
const check = (condition, message) => { assert(condition, message); report.assertions.push({scenario: activeScenario, message, at: new Date().toISOString()}); };
function background(promise) {
  pendingEvidence.add(promise);
  promise.catch(error => report.page_errors.push({kind: 'evidence', message: error.message})).finally(() => pendingEvidence.delete(promise));
}
const loopback = url => !/^https?:/.test(url) || ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname);
const progress = setInterval(() => {
  console.log(JSON.stringify({event: 'browser_alive', scenario: activeScenario, url: page?.url(), at: new Date().toISOString()}));
  background(checkpoint());
}, 30_000);

function multipart(request) {
  const buffer = request.postDataBuffer();
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(request.headers()['content-type'] ?? '');
  if (!buffer || !boundary) return null;
  const delimiter = Buffer.from('--' + (boundary[1] ?? boundary[2]));
  const parts = {};
  let start = buffer.indexOf(delimiter) + delimiter.length + 2;
  while (start > delimiter.length) {
    const end = buffer.indexOf(delimiter, start);
    if (end < 0) break;
    const headerEnd = buffer.indexOf(Buffer.from('\r\n\r\n'), start);
    if (headerEnd < 0 || headerEnd > end) break;
    const header = buffer.subarray(start, headerEnd).toString('utf8');
    const field = /name="([^"]+)"/.exec(header)?.[1];
    if (field) parts[field] = buffer.subarray(headerEnd + 4, end - 2);
    start = end + delimiter.length + 2;
  }
  return parts;
}

async function launch() {
  browser = await chromium.launch({headless: true, channel: 'chromium', timeout: 60_000,
    args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
  context = await browser.newContext({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1,
    serviceWorkers: 'block'});
  // Chromium discards Network.getResponseBody handles on location.assign.
  // Copy the genuine JSON response before the UI receives it and navigates.
  // The original Response object, HTTP request and response bytes are untouched.
  await context.exposeBinding('__m12ObserveResponse', async (_source, observed) => {
    const row = {...observed, at: new Date().toISOString(), scenario: activeScenario};
    report.responses.push(row);
    for (const waiter of [...responseWaiters]) if (row.path.endsWith(waiter.suffix) && row.method === waiter.method) {
      responseWaiters.delete(waiter); clearTimeout(waiter.timer);
      waiter.resolve({json: async () => row.body, status: () => row.status, ok: () => row.status >= 200 && row.status < 300});
    }
  });
  await context.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const response = await originalFetch(input, init);
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
      if (url.pathname.startsWith('/api/') && (response.headers.get('content-type') ?? '').includes('json')) {
        const body = await response.clone().json();
        await window.__m12ObserveResponse({method: (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase(),
          path: url.pathname, status: response.status, body});
      }
      return response;
    };
  });
  await context.route('**/*', async route => {
    const url = route.request().url();
    if (!loopback(url)) { report.network_violations.push(url); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  context.on('request', request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return;
    const row = {at: new Date().toISOString(), scenario: activeScenario, method: request.method(), path: url.pathname};
    if (request.method() !== 'GET') {
      const parts = multipart(request);
      if (parts?.manifest) row.manifest = JSON.parse(parts.manifest.toString('utf8'));
      else if (parts?.capture) {
        row.capture = JSON.parse(parts.capture.toString('utf8'));
        row.files = {};
        for (const name of ['viewer', 'annotated']) {
          assert(parts[name], `Real capture multipart is missing ${name}`);
          row.files[name] = {sha256: digest(parts[name]), bytes: parts[name].length,
            width: parts[name].readUInt32BE(16), height: parts[name].readUInt32BE(20)};
        }
        const index = report.captures.length + 1;
        report.captures.push({index, capture: row.capture, files: row.files});
        background(Promise.all([write(`capture-${index}.json`, row.capture),
          fs.writeFile(path.join(output, `capture-${index}-viewer.png`), parts.viewer),
          fs.writeFile(path.join(output, `capture-${index}-annotated.png`), parts.annotated)]));
      } else {
        try { row.body = request.postDataJSON(); } catch { row.body = request.postData(); }
      }
    }
    report.requests.push(row);
  });
  context.on('page', p => p.on('pageerror', error => report.page_errors.push({url: p.url(), message: error.message})));
  page = await context.newPage();
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) pageNavigationAt = Date.now(); });
  page.setDefaultTimeout(30_000);
  page.setDefaultNavigationTimeout(60_000);
}

async function banner(p = page) {
  await p.locator('#offline-banner').waitFor({state: 'visible'});
  check((await p.locator('#offline-banner').innerText()).includes('OFFLINE TEST MODE'), 'Every dev UI page shows the offline banner');
}
async function shot(name, p = page, {fullPage = true, devPage = true} = {}) {
  if (devPage) await banner(p);
  const file = `${String(++screenshotCount).padStart(3, '0')}-${name}.png`;
  // Native preview panels can make an understanding page tens of thousands of
  // pixels tall. Keep proof screenshots bounded; original images are archived.
  const documentHeight = await p.evaluate(() => document.documentElement.scrollHeight);
  fullPage = fullPage && documentHeight <= 15_000;
  await p.screenshot({path: path.join(output, file), fullPage});
  report.screenshots.push({file, url: p.url(), scenario: activeScenario, full_page: fullPage,
    document_height: documentHeight, at: new Date().toISOString()});
  await checkpoint();
}
async function navigate(url) {
  await page.goto(new URL(url, base).href);
  await banner();
}
function nextResponse(p, suffix, method = 'POST') {
  return new Promise((resolve, reject) => {
    const waiter = {suffix, method, resolve};
    waiter.timer = setTimeout(() => { responseWaiters.delete(waiter); reject(new Error(`Timed out observing real ${method} ${suffix}`)); }, TIMEOUT);
    responseWaiters.add(waiter);
  });
}
async function clickJSON(selector, suffix, {double = false, p = page} = {}) {
  const responsePromise = nextResponse(p, suffix);
  if (double) {
    const button = p.locator(selector); await button.scrollIntoViewIfNeeded();
    const rect = await button.boundingBox();
    await p.mouse.dblclick(rect.x + rect.width / 2, rect.y + rect.height / 2, {delay: 60});
  } else await p.locator(selector).click();
  const response = await responsePromise;
  const body = await response.json();
  check(response.ok(), `Successful real POST ${suffix}: ${JSON.stringify(body).slice(0, 250)}`);
  return body;
}
async function waitPath(prefix, p = page) {
  const started = Date.now();
  const allowed = Array.isArray(prefix) ? prefix : [prefix];
  while (!allowed.includes(new URL(p.url()).pathname.split('/')[1])) {
    const error = await p.locator('#page-error').isVisible().catch(() => false);
    const runState = await p.locator('#run-state').allTextContents().catch(() => []);
    const activeRun = runState.some(value => /^(starting|running|finalizing)\b/.test(value));
    // A legitimate cutoff/Stop reason is displayed while the immutable draft is
    // still being finalized. Its presence is not a failed publication.
    if (error && !activeRun) throw new Error(`UI failed while awaiting /${prefix}: ${await p.locator('#page-error').innerText()}`);
    const state = await p.locator('#run-state, #task-state').allTextContents().catch(() => []);
    if (state.some(value => /^failed\b/.test(value))) throw new Error(`Real worker failed while awaiting /${prefix}: ${state.join('; ')}`);
    if (Date.now() - started > TIMEOUT) throw new Error(`Timed out waiting for /${prefix}; ${p.url()}`);
    await p.waitForTimeout(500);
  }
  await banner(p);
}
const key = p => new URL(p.url()).pathname.split('/')[2];
async function dto(endpoint) {
  // Reuse the genuine response this page already requests. In particular,
  // UNDERSTAND verifies its sealed native evidence; do not do that twice.
  const observed = report.responses.findLast(row => row.method === 'GET' && row.path === endpoint && Date.parse(row.at) >= pageNavigationAt);
  if (observed) {
    check(observed.status === 200, `Read observed real DTO ${endpoint}`); return observed.body;
  }
  const [route, routeKey] = new URL(page.url()).pathname.split('/').filter(Boolean);
  const pageEndpoint = {understand: `/api/edits/${routeKey}/understanding`, results: `/api/deliveries/${routeKey}`,
    edit: `/api/edit-drafts/${routeKey}`, run: `/api/runs/${routeKey}`, task: `/api/tasks/${routeKey}`}[route];
  if (endpoint === pageEndpoint) {
    const response = await nextResponse(page, endpoint, 'GET');
    check(response.status() === 200, `Read pending real page DTO ${endpoint}`); return response.json();
  }
  // A read not owned by the current page uses an ordinary browser fetch.
  // Writes are always the actual UI controls.
  const result = await page.evaluate(async endpoint => {
    const r = await fetch(endpoint); return {status: r.status, body: await r.json()};
  }, endpoint);
  check(result.status === 200, `Read real DTO ${endpoint}`);
  return result.body;
}
async function readyUnderstanding(expected) {
  check(new URL(page.url()).pathname.startsWith('/understand/'), 'UNDERSTAND readiness belongs to the visible understanding page');
  const actual = await dto(`/api/edits/${key(page)}/understanding`);
  if (expected) check(actual.edit_key === expected.edit_key && actual.understanding_sha256 === expected.understanding_sha256,
    'The rendered UNDERSTAND still matches the sealed selection source');
  // The real GET verifies native evidence and can take minutes. Observe that
  // page-owned response, then wait for its controls, before any selection.
  for (const item of actual.items) {
    const card = page.locator(`[data-item-id="${item.item_id}"]`);
    await card.waitFor({state: 'visible', timeout: TIMEOUT});
    for (const option of item.options) await card.locator(`button[data-option-id="${option.option_id}"]`).waitFor({state: 'visible', timeout: TIMEOUT});
  }
  return actual;
}
async function readyViewer(p = page) {
  await p.locator('#viewer canvas').waitFor();
  await p.waitForFunction(() => {
    const c = document.querySelector('#viewer canvas');
    if (!c || c.width < 2 || c.height < 2) return false;
    const sample = document.createElement('canvas'); sample.width = 32; sample.height = 32;
    const ctx = sample.getContext('2d'); ctx.drawImage(c, 0, 0, 32, 32);
    const bytes = ctx.getImageData(0, 0, 32, 32).data, colors = new Set();
    for (let i = 0; i < bytes.length; i += 4) colors.add(`${bytes[i]},${bytes[i + 1]},${bytes[i + 2]}`);
    return colors.size > 10;
  });
  if (await p.locator('#capture-annotate').count()) await p.locator('#capture-annotate').waitFor({state: 'visible'});
  await p.waitForTimeout(200);
}
async function fixtureForm(p, scenario) {
  await p.goto(new URL('/new', base).href); await banner(p);
  await p.locator('#photos').setInputFiles(config.photos.map(photo => photo.path));
  for (const [i, photo] of config.photos.entries()) {
    await p.locator(`#photo-kind-${i}`).selectOption(photo.kind);
    await p.locator(`#photo-side-${i}`).selectOption(photo.side);
  }
  await p.locator('#name').fill(report.fixtures.names[scenario] ?? 'D1 refused concurrent start');
  await p.locator('#description').fill(report.fixtures.description);
  await p.locator('#specs').fill(report.fixtures.specs_text);
  await p.locator('#cutoff').fill(scenario === 'cutoff' ? '0.015' : '25.00');
}
async function start(scenario, {double = false} = {}) {
  const retained = config.resume && report.state.jobs_started?.[scenario];
  if (retained) {
    await navigate(`/run/${retained.run_key}`);
    console.log(JSON.stringify({event: 'resume_existing_job', scenario, run_key: retained.run_key}));
    return retained;
  }
  await fs.writeFile(config.control_path ?? path.join(proofRoot, 'control.json'), JSON.stringify({next_job_scenario: scenario}));
  await fixtureForm(page, scenario);
  await shot(`${scenario}-new-filled`);
  const countBefore = report.requests.filter(r => r.method === 'POST' && r.path === '/api/jobs/start').length;
  const started = await clickJSON('#start-run', '/api/jobs/start', {double});
  report.state.jobs_started ??= {}; report.state.jobs_started[scenario] = started; await checkpoint();
  await waitPath('run');
  await page.locator('#run-spend').waitFor();
  await shot(`${scenario}-running`);
  check((await page.locator('#run-spend').innerText()).includes(' of '), 'Running page displays settled spend and cutoff');
  check(await page.locator('#run-elapsed').innerText(), 'Running page displays elapsed time');
  check(await page.locator('#stop-run').isVisible(), 'Running page displays Stop');
  if (double) check(report.requests.filter(r => r.method === 'POST' && r.path === '/api/jobs/start').length === countBefore + 1,
    'A real double click sends exactly one paid-start POST');
  return started;
}
async function completed(started, name) {
  await waitPath('results'); await readyViewer();
  const delivery = await dto(`/api/deliveries/${key(page)}`);
  const run = await dto(`/api/runs/${started.run_key}`);
  await shot(`${name}-results`);
  return {started, run, delivery};
}
async function scenario(id, action) {
  const completed = report.scenarios.find(row => row.id === id && row.status === 'passed');
  if (config.resume && completed) {
    console.log(JSON.stringify({event: 'scenario_already_passed', id})); return completed.result;
  }
  activeScenario = id;
  const row = {id, started_at: new Date().toISOString(), request_start: report.requests.length,
    response_start: report.responses.length, actual_spend_usd: 0};
  report.scenarios.push(row); await checkpoint();
  const t = performance.now();
  console.log(JSON.stringify({event: 'scenario_start', id}));
  try { row.result = await action(); row.status = 'passed'; }
  catch (error) { row.status = 'failed'; row.error = {message: error.message, stack: error.stack}; throw error; }
  finally { row.elapsed_seconds = (performance.now() - t) / 1000; row.ended_at = new Date().toISOString();
    const prior = (report.attempts ?? []).flatMap(attempt => attempt.incomplete_scenarios ?? []).filter(previous => previous.id === id);
    row.prior_attempts = prior.length;
    row.total_browser_seconds = row.elapsed_seconds + prior.reduce((sum, previous) => sum + (previous.elapsed_seconds ?? 0), 0);
    row.wall_seconds_including_resumes = (Date.parse(row.ended_at) - Date.parse(prior[0]?.started_at ?? row.started_at)) / 1000;
    row.request_end = report.requests.length; row.response_end = report.responses.length;
    row.browser_api_request_count = row.request_end - row.request_start;
    row.browser_mutation_count = report.requests.slice(row.request_start, row.request_end).filter(r => r.method !== 'GET').length;
    await checkpoint(); console.log(JSON.stringify({event: 'scenario_end', id, status: row.status, elapsed_seconds: row.elapsed_seconds})); }
}

/** The helper only reads a second GLTFLoader instance. It mirrors the viewer's
 * documented initial camera and actual pointer yaw, then raycasts candidate
 * triangle centres to choose visible click positions. It never injects marks,
 * uploads, camera changes, API responses, or model edits into the application. */
async function coordinates(delivery, marks, yaw = 0) {
  return page.evaluate(async ({modelUrl, marks, yaw}) => {
    const THREE = await import('three');
    const {GLTFLoader} = await import('three/addons/loaders/GLTFLoader.js');
    const {fitCameraToBounds, meshIdentity} = await import('/static/viewer.js');
    const model = await new GLTFLoader().loadAsync(modelUrl);
    model.scene.updateMatrixWorld(true);
    const rect = document.querySelector('#viewer canvas').getBoundingClientRect();
    const camera = new THREE.PerspectiveCamera(35, rect.width / rect.height, .001, 10);
    const bounds = new THREE.Box3().setFromObject(model.scene);
    const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, .001);
    camera.near = Math.max(.00001, radius / 1000);
    const {center, distance} = fitCameraToBounds(camera, bounds);
    camera.far = Math.max(10, distance + radius * 100);
    camera.position.copy(center).add(new THREE.Vector3(distance * Math.sin(yaw), 0, distance * Math.cos(yaw)));
    camera.lookAt(center); camera.updateMatrixWorld(true);
    const width = 1600, height = Math.round(1600 / camera.aspect);
    camera.aspect = width / height; camera.updateProjectionMatrix();
    const ray = new THREE.Raycaster(); ray.near = camera.near; ray.far = camera.far;
    const projected = new Map();
    model.scene.traverse(mesh => {
      if (!mesh.isMesh) return;
      const identity = meshIdentity(mesh, model.parser);
      if (!marks.some(m => m.node_name === identity.node_name)) return;
      const g = mesh.geometry, count = g.index?.count ?? g.attributes.position.count, candidates = [];
      const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
      for (let i = 0; i < count; i += 3) {
        mesh.getVertexPosition(g.index ? g.index.getX(i) : i, a);
        mesh.getVertexPosition(g.index ? g.index.getX(i + 1) : i + 1, b);
        mesh.getVertexPosition(g.index ? g.index.getX(i + 2) : i + 2, c);
        const world = a.clone().add(b).add(c).divideScalar(3).applyMatrix4(mesh.matrixWorld);
        const p = world.clone().project(camera);
        if (Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && p.z > -1 && p.z < 1) candidates.push({xy: [(p.x + 1) * width / 2, (1 - p.y) * height / 2], world: world.toArray()});
      }
      projected.set(identity.node_name, [...(projected.get(identity.node_name) ?? []), ...candidates]);
    });
    const result = marks.map(mark => ({...mark, points: mark.points_mm.map(mm => {
      const desired = new THREE.Vector3(...mm).multiplyScalar(.001).project(camera);
      const dx = (desired.x + 1) * width / 2, dy = (1 - desired.y) * height / 2;
      const candidates = projected.get(mark.node_name);
      if (!candidates?.length) throw new Error('Cannot see requested model node: ' + mark.node_name);
      const nearest = candidates.map(p => ({...p, distance: (p.xy[0] - dx) ** 2 + (p.xy[1] - dy) ** 2})).sort((a, b) => a.distance - b.distance);
      for (const p of nearest.slice(0, 2000)) {
        let centreHit = null, interior = true;
        // Chromium pointer coordinates are quantized to CSS pixels. A triangle
        // centre on the silhouette may miss after CSS-to-native rounding.
        // Demand an interior neighbourhood on the actual requested surface.
        for (const [ox, oy] of [[0, 0], [-3, 0], [3, 0], [0, -3], [0, 3], [-2, -2], [-2, 2], [2, -2], [2, 2]]) {
          ray.setFromCamera(new THREE.Vector2((p.xy[0] + ox) / width * 2 - 1, 1 - (p.xy[1] + oy) / height * 2), camera);
          const hit = ray.intersectObject(model.scene, true)[0];
          if (!hit || meshIdentity(hit.object, model.parser).node_name !== mark.node_name) { interior = false; break; }
          if (!ox && !oy) centreHit = hit;
        }
        if (interior) return {xy: p.xy, world_mm: centreHit.point.clone().multiplyScalar(1000).toArray(), desired_mm: mm,
          raycast_interior_margin_px: 3};
      }
      throw new Error('No visible click near declared point for ' + mark.node_name);
    })}));
    model.scene.traverse(o => { o.geometry?.dispose(); for (const m of o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : []) m.dispose(); });
    return {width, height, yaw, camera: {position_m: camera.position.toArray(), target_m: center.toArray()}, marks: result};
  }, {modelUrl: delivery.model_url, marks, yaw});
}
const corner = sign => ({kind: 'closed_shape', node_name: 'Acetate_front',
  points_mm: [[sign * 64, 10, 8], [sign * 64, 18, 8], [sign * 56, 18, 8], [sign * 56, 10, 8]]});
const temple = [
  {label: 'C', kind: 'closed_shape', node_name: 'Temple_body_1', points_mm: [[72, 12, -10], [70.62446594238281, -12.328399658203125, -33], [70, -6, -10]]},
  {label: 'D', kind: 'point', node_name: 'Temple_body_1', points_mm: [[70.62446594238281, -12.328399658203125, -33]]},
];
async function captureCycle(delivery, {marks, words, name, yaw = 0, more = false}) {
  await page.locator('#capture-annotate').waitFor({state: 'visible'});
  await page.waitForFunction(() => !document.querySelector('#capture-annotate').disabled);
  const positions = await coordinates(delivery, marks, yaw);
  await write(`${name}-click-plan-${report.captures.length + 1}.json`, positions);
  await page.getByRole('button', {name: more ? 'Yes, capture another' : 'Capture and annotate', exact: true}).click();
  const dialog = page.locator('[data-testid="annotation-dialog"]');
  await dialog.waitFor();
  const canvas = dialog.locator('canvas');
  const clickPoint = async p => {
    const rect = await canvas.boundingBox();
    await page.mouse.click(rect.x + p[0] / positions.width * rect.width, rect.y + p[1] / positions.height * rect.height);
  };
  for (const mark of positions.marks) {
    await dialog.getByRole('button', {name: mark.kind === 'point' ? 'Point' : 'Shape', exact: true}).click();
    for (const p of mark.points) await clickPoint(p.xy);
    if (mark.kind === 'closed_shape') {
      check(await dialog.getByRole('button', {name: 'Done annotating', exact: true}).isDisabled(), 'An open polygon cannot be submitted');
      await clickPoint(mark.points[0].xy);
    }
    const input = dialog.getByLabel(`Label ${mark.label}`, {exact: true});
    await input.waitFor(); await input.fill(mark.label); await input.blur();
  }
  await shot(`${name}-annotation`, page, {fullPage: false});
  await dialog.getByRole('button', {name: 'Done annotating', exact: true}).click();
  await dialog.getByRole('textbox', {name: 'What should Astra change?'}).fill(words);
  await shot(`${name}-words`, page, {fullPage: false});
  const response = nextResponse(page, '/items');
  await dialog.getByRole('button', {name: 'Done', exact: true}).click();
  const posted = await response, body = await posted.json();
  check(posted.ok(), `Actual capture accepted: ${JSON.stringify(body).slice(0, 200)}`);
  await dialog.waitFor({state: 'detached'});
  check(body.item.request_text === words, 'Verbatim item words survive real browser multipart');
  check(JSON.stringify(body.item.labels) === JSON.stringify(marks.map(m => m.label)), 'Declared labels survive real browser multipart');
  const bundle = report.captures.at(-1);
  check(bundle.capture.image.width_px === 1600, 'Browser capture retains native 1600-pixel long edge');
  check(bundle.files.viewer.width === bundle.capture.image.width_px && bundle.files.viewer.height === bundle.capture.image.height_px,
    'Actual PNG dimensions equal capture metadata');
  for (const mark of bundle.capture.marks) {
    check(mark.footprint.pixels > 0 && mark.footprint.faces.length > 0, `Mark ${mark.label} has real model footprint`);
    check(mark.vertices.every(v => v.anchor.kind === 'surface'), `Mark ${mark.label} vertices anchor to real model surfaces`);
  }
  const maxError = Math.max(...bundle.capture.camera.position_m.map((v, i) => Math.abs(v - positions.camera.position_m[i])));
  check(maxError < 1e-7, 'Read-only click projection agrees with actual captured viewer camera');
  await shot(`${name}-saved-item`);
  return body.item;
}
async function orbitTemple() {
  await page.locator('#viewer canvas').scrollIntoViewIfNeeded();
  const rect = await page.locator('#viewer canvas').boundingBox();
  const yaw = 75 * Math.PI / 180, delta = -rect.height * yaw / (2 * Math.PI);
  const x = rect.x + rect.width * .65, y = rect.y + rect.height * .5;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + delta, y, {steps: 15}); await page.mouse.up();
  await shot('temple-orbited');
  return yaw;
}
async function sendUnderstanding(name) {
  let run = config.resume && report.state.understand_runs?.[name];
  if (run) await navigate(`/run/${run.run_key}`);
  else {
    await page.getByRole('button', {name: 'No, send to Astra', exact: true}).click();
    await page.locator('#edit-cutoff').fill('25.00');
    await shot(`${name}-send-form`);
    run = await clickJSON('#send-edits', '/send');
    report.state.understand_runs ??= {}; report.state.understand_runs[name] = run; await checkpoint();
  }
  if (new URL(page.url()).pathname.startsWith('/understand/')) {
    const understanding = await readyUnderstanding();
    await shot(`${name}-understanding-resumed`); return {run, understanding, url: page.url()};
  }
  await waitPath(['run', 'understand']); await shot(`${name}-running`);
  await waitPath('understand');
  const understanding = await readyUnderstanding();
  await shot(`${name}-understanding`);
  return {run, understanding, url: page.url()};
}
async function picks(understanding, single = false) {
  await readyUnderstanding(understanding);
  for (const item of understanding.items.filter(i => i.disposition === 'previewed')) {
    const card = page.locator(`[data-item-id="${item.item_id}"]`);
    if (single && item.item_id === 'I2') {
      await card.getByRole('button', {name: 'None of these', exact: true}).click();
      await card.locator('textarea').fill('None of these right corner options; keep this side unchanged in the single-pick proof.');
    } else {
      const option = item.options.find(o => o.option_id === 'B') ?? item.options[1];
      check(!!option, `${item.item_id} offers at least two real preview options`);
      await card.locator(`button[data-option-id="${option.option_id}"]`).click();
    }
  }
  await page.locator('#note-I3').fill('I3 is not applied; its genuine preview refusal remains visible.');
}
async function applyAndInspect(understanding, name, single = false) {
  let task = config.resume && report.state.apply_tasks?.[name];
  if (task) await navigate(`/task/${task.task_key}`);
  else {
    await picks(understanding, single); await shot(`${name}-picked`);
    task = await clickJSON('#apply-picks', '/apply');
    report.state.apply_tasks ??= {}; report.state.apply_tasks[name] = task; await checkpoint();
  }
  if (!new URL(page.url()).pathname.startsWith('/edit/')) {
    await waitPath(['task', 'edit']); await shot(`${name}-task`);
  }
  await waitPath('edit'); await readyViewer();
  const draft = await dto(`/api/edit-drafts/${key(page)}`);
  const delivery = await dto(`/api/deliveries/${draft.source.delivery_key}`);
  const taskDTO = await dto(`/api/tasks/${task.task_key}`);
  check(taskDTO.phase === 'done', 'Real local apply task completed');
  check(delivery.origin === 'applied_picks', 'Result carries immutable applied-picks origin');
  check(delivery.applied.length === (single ? 1 : 2), 'Result shows the exact number of selected items');
  check(delivery.notes.some(n => n.item_id === 'I3'), 'Blocked I3 remains visibly not applied');
  check(delivery.exactness?.ok === true, 'Immutable delivery exposes successful exactness proof');
  if (single) {
    check(delivery.exactness.mode === 'single_glb_bytes' && delivery.exactness.single_pick_glb_equal === true, 'Single pick preserves preview GLB byte equality');
    check(delivery.notes.some(n => n.item_id === 'I2' && n.note.includes('None of these right corner options')), 'None-of-these note survives immutable delivery');
  } else {
    check(delivery.exactness.mode === 'per_item_geometry', 'Two picks use exact per-item geometry comparison');
    check(delivery.exactness.items?.length === 2 && delivery.exactness.items.every(i => i.exact_preview_geometry === true), 'Both disjoint picked regions reproduce their sealed previews');
    check(!delivery.exactness.overlaps?.length, 'Two front-corner picks have no overlapping geometry');
    const exported = delivery.exactness.exported_geometry;
    check(exported?.ok === true && !exported.overlaps?.length && exported.comparisons.every(row => (row.overlapping_rows ?? 0) === 0), 'Exported two-pick geometry has zero overlapping rows');
    check((await page.locator('.exactness summary').innerText()) === 'Selected disjoint changes match their previews exactly.', 'The owner sees exact disjoint preview confirmation');
  }
  check((await page.locator('#more-edits').innerText()).includes('Any more edits?'), 'Applied model returns to the owner edit loop');
  await shot(`${name}-edited-model`);
  return {task: taskDTO, draft, delivery};
}

try {
  await launch();
  await navigate('/'); await shot('jobs-initial');
  const serverConfig = await dto('/api/config'); check(serverConfig.offline_test_mode === true, 'Server is injection-only offline mode');
  const wanted = new Set(config.scenarios ?? ['new_run', 'cutoff', 'survival', 'edit', 'fallback']);
  if (wanted.has('new_run')) await scenario('1-new-run-and-double-click', async () => {
    const result = await completed(await start('success', {double: true}), 'success');
    check(result.delivery.finished === true && result.delivery.label === 'finished', 'Finished synthetic run reaches a finished 3D results page');
    check(result.run.terminal_status === 'completed', 'Successful run has completed terminal status');
    const popupPromise = context.waitForEvent('page');
    const arDTO = await clickJSON('#view-ar', '/ar');
    const popup = await popupPromise;
    await popup.waitForURL(url => url.href === arDTO.url, {timeout: 60_000});
    await popup.waitForLoadState('domcontentloaded');
    check(loopback(arDTO.url), 'AR URL is loopback');
    const ar = await popup.evaluate(async () => {
      const response = await fetch(location.href); return {status: response.status, text: await response.text()};
    });
    check(ar.status === 200 && ar.text.includes('<html'), 'AR handoff serves a working HTML page');
    const modelURL = new URL(arDTO.url).searchParams.get('model');
    check(!!modelURL, 'AR handoff identifies the shown model');
    const served = await popup.evaluate(async model => {
      const r = await fetch(new URL(model, location.href)); const b = await r.arrayBuffer();
      const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', b));
      return {status: r.status, bytes: b.byteLength, sha256: Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('')};
    }, modelURL);
    check(served.status === 200 && served.sha256 === result.delivery.glb_sha256, 'AR URL serves exact finished GLB bytes');
    await shot('success-ar', popup, {devPage: false}); await popup.close();
    report.state.success = result; return {...result, ar: {url: arDTO.url, model: served}};
  });
  if (wanted.has('cutoff')) await scenario('2-cutoff-draft', async () => {
    const result = await completed(await start('cutoff'), 'cutoff');
    check(result.run.terminal_status === 'budget_limit', 'Cutoff ends with budget_limit');
    check(Number(result.run.settled_usd) > Number(result.run.cutoff_usd), 'Only the in-flight response crosses the typed cutoff');
    check(!result.delivery.finished && result.delivery.label === 'not finished', 'Cutoff publishes a clearly labelled unfinished draft');
    check((await page.locator('.status-tag').innerText()) === 'not finished', 'Draft wording is visible in the actual results page');
    report.state.cutoff = result; return result;
  });
  if (wanted.has('survival')) await scenario('3-browser-survival-stop-and-concurrency', async () => {
    const started = await start('slow');
    let before = await dto(`/api/runs/${started.run_key}`);
    const admissionStarted = Date.now();
    while (Number(before.settled_usd) <= 0) {
      check(['starting', 'running'].includes(before.phase), 'Survival proof waits for a genuine active request');
      if (Date.now() - admissionStarted > TIMEOUT) throw new Error('Slow proof never admitted its first scripted request');
      await page.waitForTimeout(500);
      before = await dto(`/api/runs/${started.run_key}`);
    }
    check(Number(before.settled_usd) > 0, 'The browser is closed only after a real scripted request settles');
    const other = await context.newPage();
    await fixtureForm(other, 'concurrent');
    const refusedPromise = nextResponse(other, '/api/jobs/start');
    await other.locator('#start-run').click();
    const refused = await refusedPromise, rejection = await refused.json();
    check(refused.status() === 409 && rejection.error?.code === 'paid_run_busy', 'Concurrent Start is rejected before approval by the real busy-run check');
    await other.locator('#page-error').waitFor({state: 'visible'});
    await shot('concurrent-start-refused', other); await other.close();
    await browser.close();
    await new Promise(resolve => setTimeout(resolve, 1500));
    await launch(); await navigate('/'); await shot('survival-reopened-jobs');
    await page.locator(`[data-job-id="${started.job_id}"] a[href="/run/${started.run_key}"]`).click();
    await waitPath('run');
    const after = await dto(`/api/runs/${started.run_key}`);
    check(['starting', 'running', 'finalizing'].includes(after.phase), 'Detached run continues after Chromium exits');
    check(after.elapsed_seconds >= before.elapsed_seconds, 'Reopened job retains progressing elapsed time');
    await shot('survival-reopened-running');
    page.once('dialog', dialog => dialog.accept());
    const stop = await clickJSON('#stop-run', '/stop');
    check(stop.stop_requested === true, 'Stop is recorded by the real run API');
    await shot('survival-stop-requested');
    const result = await completed(started, 'survival');
    check(result.run.terminal_status === 'stopped', 'Run ends cooperatively after the current step');
    check(!result.delivery.finished, 'Cooperatively stopped run publishes an unfinished draft');
    report.state.survival = result; return {...result, before, after, concurrent_refusal: {status: refused.status(), body: rejection}};
  });
  if (wanted.has('edit')) await scenario('4-captures-understand-two-picks-and-single-exact-apply', async () => {
    let source, draft, understood;
    if (config.resume && report.state.edit?.understanding) {
      ({source, draft, ...understood} = report.state.edit);
      await page.goto(understood.url); await banner();
      const saved = await readyUnderstanding(understood.understanding);
      check(saved.understanding_sha256 === understood.understanding.understanding_sha256, 'Resumed UNDERSTAND remains sealed to its saved hash');
    } else if (config.resume && report.state.captures_ready) {
      ({source, draft} = report.state.captures_ready);
      await navigate(`/edit/${draft.draft_id}`);
      understood = await sendUnderstanding('three-item');
      report.state.edit = {source, draft, ...understood}; await checkpoint();
    } else {
      let sourceKey = config.prada_delivery_key;
      if (!sourceKey) {
        const jobs = await dto('/api/jobs');
        const job = jobs.jobs.find(j => j.job_id === config.prada_job_id || j.name === config.prada_job_name);
        check(!!job?.deliveries?.length, 'Private copied Prada source is registered'); sourceKey = job.deliveries[0].delivery_key;
      }
      await navigate(`/results/${sourceKey}`); await readyViewer(); await shot('prada-source-results');
      source = await dto(`/api/deliveries/${sourceKey}`);
      draft = await clickJSON('#edit-delivery', '/api/edit-drafts');
      await waitPath('edit'); await readyViewer(); await shot('prada-edit-empty');
      await captureCycle(source, {marks: [{...corner(1), label: 'A'}], name: 'left-corner',
        words: 'Repair the visibly different shading inside A at the upper outer left corner.'});
      await captureCycle(source, {marks: [{...corner(-1), label: 'B'}], name: 'right-corner', more: true,
        words: 'Repair the visibly different shading inside B at the upper outer right corner.'});
      const yaw = await orbitTemple();
      await captureCycle(source, {marks: temple, name: 'temple', yaw, more: true,
        words: 'Move the temple triangle marked C inward toward the head, especially its lower corner D; keep both temples symmetric.'});
      report.state.captures_ready = {source, draft}; await checkpoint();
      understood = await sendUnderstanding('three-item');
      report.state.edit = {source, draft, ...understood};
      await checkpoint();
    }
    const understanding = understood.understanding;
    check(understanding.items.length === 3, 'Scripted UNDERSTAND retains all three actual capture items');
    for (const item of understanding.items.slice(0, 2)) {
      await page.locator(`[data-item-id="${item.item_id}"]`).scrollIntoViewIfNeeded();
      check(item.disposition === 'previewed' && item.options.length >= 2 && item.options.length <= 3, `${item.item_id} has two or three sealed previews`);
      check(item.options.every(o => o.images.length && o.images.every(i => JSON.stringify(i.panels) === '["photo","before","option"]')), `${item.item_id} preview images are photo/before/option`);
      const images = page.locator(`[data-item-id="${item.item_id}"] .preview-image`);
      await images.first().waitFor();
      await page.waitForFunction(itemId => [...document.querySelectorAll(`[data-item-id="${itemId}"] .preview-image`)].every(i => i.complete && i.naturalWidth > 0), item.item_id);
      check(await images.evaluateAll(imgs => imgs.every(i => i.complete && i.naturalWidth > 0)), `${item.item_id} real preview images loaded`);
      await shot(`understanding-${item.item_id}`, page, {fullPage: false});
    }
    const blocked = understanding.items[2];
    check(blocked.item_id === 'I3' && blocked.disposition === 'cannot_preview' && !!blocked.reason && !blocked.options.length,
      'I3 exposes its genuine preview refusal and no invented option');
    await page.locator('[data-item-id="I3"]').scrollIntoViewIfNeeded();
    await shot('understanding-I3-refusal', page, {fullPage: false});
    if (!report.state.stale_simulation) {
      // Simulate a stale browser-held SHA while keeping every backend response real.
      await picks(understanding);
      const applyPath = `/api/edits/${understanding.edit_key}/apply`;
      await page.route(`**${applyPath}`, async route => {
        const body = route.request().postDataJSON(); body.understanding_sha256 = '0'.repeat(64);
        await route.continue({postData: JSON.stringify(body)});
      }, {times: 1});
      const stalePromise = nextResponse(page, '/apply'); await page.locator('#apply-picks').click();
      const stale = await stalePromise, staleBody = await stale.json();
      check(stale.status() === 409 && staleBody.error?.code === 'stale_understanding' && !staleBody.task_key,
        'A stale understanding is refused before creating an apply task');
      await page.locator('#page-error').waitFor({state: 'visible'}); await shot('stale-understanding-refused');
      report.state.stale_simulation = {method: 'Only outgoing understanding_sha256 replaced by 64 zeroes; genuine backend response and visible UI error', status: stale.status(), body: staleBody};
      await checkpoint();
    }
    if (await page.locator('#page-error').isVisible()) {
      await page.goto(understood.url); await banner();
    }
    const both = await applyAndInspect(understanding, 'two-picks-exact');
    report.state.edit.both = both; await checkpoint();
    const reusedSingle = !!(config.resume && report.state.apply_tasks?.['one-pick-none']);
    if (!reusedSingle) {
      await page.goto(understood.url); await banner();
    }
    // Reopen and check the actual immutable model/DTO even when reusing 4b's
    // already-proved native single apply. No new apply POST is needed then.
    const single = await applyAndInspect(understanding, 'one-pick-none', true);
    report.state.edit = {source, draft, ...understood, both,
      single, single_reused: reusedSingle};
    return report.state.edit;
  });
  if (wanted.has('single_apply')) await scenario('4b-single-pick-exact-apply', async () => {
    const edit = report.state.edit ?? config.edit;
    check(!!edit?.understanding, 'Independent single apply has the real sealed three-item understanding');
    if (!edit.single) {
      await page.goto(edit.url); await banner();
      edit.single = await applyAndInspect(edit.understanding, 'one-pick-none', true);
      report.state.edit = edit; await checkpoint();
    }
    return edit.single;
  });
  if (wanted.has('fallback')) await scenario('5-fallback-and-fresh-understanding', async () => {
    const edit = report.state.edit ?? config.edit;
    check(!!edit, 'Fallback has a real sealed three-item understanding');
    let started = config.resume && report.state.fallback_run;
    if (started) await navigate(`/run/${started.run_key}`);
    else {
      await page.goto(edit.url); await banner();
      await readyUnderstanding(edit.understanding);
      await page.locator('#fallback-cutoff-I3').fill('25.00'); await shot('fallback-send');
      const card = page.locator('[data-item-id="I3"]');
      const response = nextResponse(page, '/full-edit');
      await card.getByRole('button', {name: 'Send this item to a full paid edit', exact: true}).click();
      const sent = await response; started = await sent.json(); check(sent.ok(), 'I3 fallback starts through real separately approved send');
      report.state.fallback_run = started; await checkpoint();
    }
    await waitPath(['run', 'results']); await shot('fallback-running');
    const result = await completed(started, 'fallback');
    check(result.delivery.origin === 'full_edit_item', 'Fallback result retains immutable single-item origin');
    check(!!result.delivery.side_effects, 'Fallback DTO contains the genuine side-effect measurement');
    check((await page.locator('.delivery-outcome').innerText()).toLowerCase().includes('side effect'), 'Fallback side effects are visible to the owner');
    await page.locator('.side-effects').scrollIntoViewIfNeeded();
    await page.getByText('Full side-effect report', {exact: true}).click();
    await shot('fallback-side-effects', page, {fullPage: false});
    let draft = config.resume && report.state.fallback_draft;
    if (draft) await navigate(`/edit/${draft.draft_id}`);
    else { draft = await clickJSON('#edit-delivery', '/api/edit-drafts'); report.state.fallback_draft = draft; await checkpoint(); }
    await waitPath('edit'); await readyViewer(); await shot('fallback-new-edit');
    check((await page.locator('#more-edits').innerText()).includes('Any more edits?'), 'Fallback delivery returns to the owner edit-loop question');
    if (!report.state.fallback_capture_done) {
      await captureCycle(result.delivery, {marks: [{...corner(1), label: 'A'}], name: 'fallback-fresh-capture', more: true,
        words: 'Inspect A after the full edit; report what can be previewed.'});
      report.state.fallback_capture_done = true; await checkpoint();
    }
    const fresh = await sendUnderstanding('fallback-fresh');
    check(fresh.understanding.source.glb_sha256 === result.delivery.glb_sha256,
      'Fresh UNDERSTAND is admitted from the fallback delivery, with the new source hash');
    check(fresh.understanding.items.length === 1, 'Fresh UNDERSTAND contains only its new capture');
    report.state.fallback = {...result, draft, fresh}; return report.state.fallback;
  });
  await Promise.allSettled([...pendingEvidence]);
  check(report.network_violations.length === 0, 'Chromium made no non-loopback network request');
  check(report.page_errors.length === initialPageErrorCount, 'No JavaScript page exceptions or evidence failures in this attempt');
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = {message: error.message, stack: error.stack};
  console.error(error.stack);
  if (page && !page.isClosed()) await shot('failure', page, {devPage: false}).catch(() => {});
  process.exitCode = 1;
} finally {
  clearInterval(progress);
  for (const waiter of responseWaiters) clearTimeout(waiter.timer);
  responseWaiters.clear();
  await browser?.close().catch(() => {});
  await Promise.allSettled([...pendingEvidence]);
  report.ended_at = new Date().toISOString(); await checkpoint();
  console.log(JSON.stringify({event: 'browser_completed', status: report.status, output}));
}
