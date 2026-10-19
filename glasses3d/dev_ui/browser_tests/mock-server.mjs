/** Loopback-only mock of M12 section 7. Never invokes pipeline code or a model provider. */
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {makeAnchorGlb, makePng, sha256} from './fixtures.mjs';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
export const DATA = path.join(ROOT, 'glasses3d/data/m12/tmp/B');
export const arRequire = createRequire(path.join(ROOT, 'glasses3d/arview/package.json'));
export const {chromium} = arRequire('@playwright/test');
const STATIC = path.join(ROOT, 'glasses3d/dev_ui/static');
const THREE = path.join(ROOT, 'glasses3d/arview/node_modules/three');
const clone = value => structuredClone(value);

export async function startMockServer() {
  await fs.mkdir(DATA, {recursive: true});
  const fixture = makeAnchorGlb(), alphaFixture = makeAnchorGlb({alphaMask: true}), png = makePng();
  await fs.writeFile(path.join(DATA, 'synthetic-anchor.glb'), fixture.bytes);
  await fs.writeFile(path.join(DATA, 'synthetic-alpha.glb'), alphaFixture.bytes);
  await fs.writeFile(path.join(DATA, 'photo.png'), png);
  const hash = sha256(fixture.bytes), created = '2026-10-19T10:00:00Z', previewUrl = `/api/artifacts/${sha256(png)}`;
  const source = {delivery_key: 'delivery_demo', glb_sha256: hash};
  const state = {requests: [], startDelay: 0, nextError: null, fixture, alphaFixture, png, source, artifacts: new Map(),
    config: {schema: 'g3d.dev_config/1', offline_test_mode: true, view_kinds: ['front', 'side', 'angled', 'top', 'rear', 'top_oblique'], sides: ['both', 'left', 'right', 'none'], effort: 'high'},
    run: {schema: 'g3d.dev_run/1', run_key: 'run_demo', kind: 'job', job_id: 'job_demo', run_id: 'run_demo', phase: 'running', loop_state: 'working', started_at: new Date(Date.now() - 65_000).toISOString(), updated_at: created, elapsed_seconds: 65, settled_usd: '1.25', held_usd: '0', unknown_usd: '0', cutoff_usd: '25.50', estimate_usd: '8.50', terminal_status: null, stop_requested: false, delivery_key: null, edit_key: null, error: null, offline_test_mode: true},
    delivery: {schema: 'g3d.dev_delivery/1', ...source, job_id: 'job_demo', delivery_id: 'delivery_demo', model_url: '/api/deliveries/delivery_demo/model.glb', finished: false, accepted_finish: false, run_status: 'budget_limit', label: 'not finished', run_settled_usd: '25.65', created_utc: created, checks: [{name: 'GLB', ok: true, detail: 'Synthetic fixture'}], origin: 'run', applied: [], notes: [], warnings: []},
    draft: {schema: 'g3d.dev_edit_draft/1', draft_id: 'draft_demo', source, items: [], labels: [], frozen: false},
    task: {schema: 'g3d.dev_task/1', task_key: 'task_demo', kind: 'apply_picks', phase: 'running', started_at: created, elapsed_seconds: 1, progress: 'Applying chosen options', result: null, error: null},
    understanding: {schema: 'g3d.dev_understanding/1', edit_key: 'edit_demo', understanding_sha256: 'e'.repeat(64), source, run_settled_usd: '2.35', items: [
      {item_id: 'I1', number: 1, labels: ['A'], request_text: 'Move A inward.', annotated_url: previewUrl, interpretation: 'Move the selected frame point inward.', disposition: 'previewed', reason: '', fallback_allowed: false, options: [
        {option_id: 'small', label: 'Small move', amount_text: '1 mm', images: [{url: previewUrl, view: 'front', panels: ['photo', 'before', 'option']}]},
        {option_id: 'large', label: 'Large move', amount_text: '3 mm', images: [{url: previewUrl, view: 'top', panels: ['photo', 'before', 'option']}]}]},
      {item_id: 'I2', number: 2, labels: ['B'], request_text: 'Refine B.', annotated_url: previewUrl, interpretation: 'Refine the selected corner.', disposition: 'previewed', reason: '', fallback_allowed: false, options: [{option_id: 'smooth', label: 'Smooth corner', amount_text: '0.2 mm', images: [{url: previewUrl, view: 'side', panels: ['photo', 'before', 'option']}]}]},
      {item_id: 'I3', number: 3, labels: ['C'], request_text: 'Change C.', annotated_url: previewUrl, interpretation: 'A new surface needs interpretation.', disposition: 'cannot_preview', reason: 'The requested surface needs a full edit.', fallback_allowed: true, options: []},
      {item_id: 'I4', number: 4, labels: ['D'], request_text: 'Move D.', annotated_url: previewUrl, interpretation: 'Direction is unclear.', disposition: 'needs_clarification', reason: 'Please describe the direction for D.', fallback_allowed: false, options: []},
      {item_id: 'I5', number: 5, labels: ['E'], request_text: 'Change E.', annotated_url: previewUrl, interpretation: 'No supported operation.', disposition: 'unsupported', reason: 'This change cannot be previewed.', fallback_allowed: false, options: []},
    ], map_images: [{url: previewUrl, label: 'Model map'}], limitations: ['Synthetic mock previews.']}, realBytes: null};
  const json = (res, value, status = 200) => {res.writeHead(status, {'content-type': 'application/json; charset=utf-8'}); res.end(JSON.stringify(value));};
  const file = async (res, filename, type) => {const bytes = await fs.readFile(filename); res.writeHead(200, {'content-type': type}); res.end(bytes);};
  const server = http.createServer(async (req, res) => {
    res.setHeader('cache-control', 'no-store');
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), route = url.pathname, method = req.method;
      let body = null, parts = null;
      if (method !== 'GET' && method !== 'HEAD') {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const bytes = Buffer.concat(chunks);
        if ((req.headers['content-type'] || '').startsWith('multipart/')) {
          const data = await new Request('http://127.0.0.1/', {method, headers: req.headers, body: bytes}).formData();
          parts = {};
          for (const [key, value] of data) parts[key] = typeof value === 'string' ? value : {name: value.name, type: value.type, bytes: Buffer.from(await value.arrayBuffer())};
        } else if (bytes.length) body = JSON.parse(bytes.toString('utf8'));
      }
      state.requests.push({method, route, body, parts});
      if (state.nextError && route === state.nextError.route) {const error = state.nextError; state.nextError = null; return json(res, {error: {code: 'test_error', message: error.message}}, 409);}
      if (route === '/api/config') return json(res, state.config);
      if (route === '/api/jobs') return json(res, {jobs: [
        {job_id: 'job_old', name: 'Older glasses', created_utc: '2026-10-18T10:00:00Z', deliveries: [], runs: [], drafts: [], edits: []},
        {job_id: 'job_demo', name: 'Synthetic glasses', created_utc: created, deliveries: [{delivery_key: 'delivery_demo', label: 'not finished', created_utc: created}], runs: [{run_key: 'run_demo', kind: 'job', phase: 'running'}], drafts: [{draft_id: 'draft_demo'}], edits: [{edit_key: 'edit_demo', phase: 'ended'}]},
      ]});
      if (route === '/api/jobs/start') {if (state.startDelay) await new Promise(resolve => setTimeout(resolve, state.startDelay)); return json(res, {run_key: 'run_demo', job_id: 'job_demo', run_id: 'run_demo', cutoff_usd: JSON.parse(parts.manifest).cutoff_usd});}
      if (route === '/api/runs/run_demo/stop') {state.run.stop_requested = true; return json(res, state.run);}
      if (route === '/api/runs/run_demo') return json(res, state.run);
      if (route === '/__test/alpha.glb') {res.writeHead(200, {'content-type': 'model/gltf-binary'}); return res.end(alphaFixture.bytes);}
      if (/^\/api\/deliveries\/[^/]+\/model.glb$/.test(route) || route === '/__test/model.glb') {res.writeHead(200, {'content-type': 'model/gltf-binary'}); return res.end(route === '/__test/model.glb' && state.realBytes ? state.realBytes : fixture.bytes);}
      if (route === '/api/deliveries/delivery_demo/ar') return json(res, {url: `${origin}/__test/ar`});
      if (/^\/api\/deliveries\/[^/]+$/.test(route)) return json(res, state.delivery);
      if (route === '/api/edit-drafts' && method === 'POST') {if (body.delivery_key === 'delivery_after') return json(res, {...state.draft, draft_id: 'draft_after', source: {...source, delivery_key: 'delivery_after'}, items: [], labels: []}); return json(res, state.draft);}
      if (/^\/api\/edit-drafts\/[^/]+\/items$/.test(route) && method === 'POST') {
        const capture = JSON.parse(parts.capture), n = state.draft.items.length + 1;
        const viewerKey = sha256(parts.viewer.bytes), annotatedKey = sha256(parts.annotated.bytes);
        state.artifacts.set(viewerKey, parts.viewer.bytes); state.artifacts.set(annotatedKey, parts.annotated.bytes);
        const item = {item_id: `I${n}`, capture_id: capture.capture_id, labels: capture.marks.map(mark => mark.label), request_text: capture.request_text, viewer_url: `/api/artifacts/${viewerKey}`, annotated_url: `/api/artifacts/${annotatedKey}`, warnings: []};
        state.draft.items.push(item); state.draft.labels = state.draft.items.flatMap(item => item.labels); return json(res, {item, warnings: []});
      }
      if (/^\/api\/edit-drafts\/[^/]+\/items\/[^/]+$/.test(route)) {
        const id = route.split('/').at(-1), item = state.draft.items.find(item => item.item_id === id);
        if (method === 'PATCH') {item.request_text = body.request_text; return json(res, {item, warnings: []});}
        if (method === 'DELETE') {state.draft.items = state.draft.items.filter(item => item.item_id !== id); state.draft.labels = state.draft.items.flatMap(item => item.labels); return json(res, state.draft);}
      }
      if (/^\/api\/edit-drafts\/[^/]+\/send$/.test(route)) return json(res, {run_key: 'run_demo', edit_key: 'edit_demo'});
      if (/^\/api\/edit-drafts\/[^/]+$/.test(route)) return json(res, route.endsWith('draft_after') ? {...state.draft, draft_id: 'draft_after', source: {...source, delivery_key: 'delivery_after'}, items: [], labels: []} : state.draft);
      if (route === '/api/edits/edit_demo/understanding') return json(res, state.understanding);
      if (route === '/api/edits/edit_demo/apply') return json(res, {task_key: 'task_demo'});
      if (/^\/api\/edits\/edit_demo\/items\/[^/]+\/full-edit$/.test(route)) return json(res, {run_key: 'run_demo'});
      if (route === '/api/tasks/task_demo') return json(res, state.task);
      if (route.startsWith('/api/artifacts/')) {res.writeHead(200, {'content-type': 'image/png'}); return res.end(state.artifacts.get(route.split('/').at(-1)) || png);}
      if (route === '/__test/ar') {res.end('<!doctype html><title>Mock AR</title><p>Offline AR handoff</p>'); return;}
      if (route === '/__test/viewer') {res.writeHead(200, {'content-type': 'text/html'}); res.end(`<!doctype html><meta charset="utf-8"><title>Viewer fixture</title><link rel="stylesheet" href="/static/styles.css"><script type="importmap">{"imports":{"three":"/vendor/three/build/three.module.js","three/addons/":"/vendor/three/examples/jsm/"}}</script><div id="fixture-viewer" style="width:800px;height:500px"></div>`); return;}
      if (route.startsWith('/static/')) {const relative = decodeURIComponent(route.slice(8)); if (relative.includes('..')) return json(res, {error: {message: 'Invalid path'}}, 404); return file(res, path.join(STATIC, relative), relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html');}
      if (route.startsWith('/vendor/three/')) {const relative = decodeURIComponent(route.slice('/vendor/three/'.length)); if ((!relative.startsWith('build/') && !relative.startsWith('examples/jsm/')) || relative.includes('..') || !relative.endsWith('.js')) return json(res, {error: {message: 'Invalid vendor path'}}, 404); return file(res, path.join(THREE, relative), 'text/javascript');}
      const page = route === '/' ? 'index' : route.split('/')[1];
      if (['index', 'new', 'run', 'results', 'edit', 'understand', 'task'].includes(page)) return file(res, path.join(STATIC, `${page}.html`), 'text/html');
      return json(res, {error: {message: `Mock has no route ${method} ${route}`}}, 404);
    } catch (error) {return json(res, {error: {code: 'mock_failure', message: error.message}}, 500);}
  });
  let port;
  for (let candidate = 8280; candidate <= 8289; candidate++) {
    try {await new Promise((resolve, reject) => {server.once('error', reject); server.listen(candidate, '127.0.0.1', resolve);}); port = candidate; break;}
    catch (error) {if (error.code !== 'EADDRINUSE') throw error;}
    finally {server.removeAllListeners('error');}
  }
  if (!port) throw new Error('All M12-B test ports 8280–8289 are occupied.');
  const origin = `http://127.0.0.1:${port}`;
  return {origin, state, close: () => new Promise(resolve => {server.closeAllConnections(); server.close(resolve);}), snapshot: () => clone(state.requests)};
}

export async function launchBrowser() {
  return chromium.launch({headless: true, channel: 'chromium', timeout: 60_000, args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']});
}

export async function browserContext(browser, origin, options = {}) {
  const context = await browser.newContext({viewport: {width: 1280, height: 900}, ...options});
  const audit = {blocked: [], errors: [], shaderErrors: []};
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(`${origin}/`) || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
    audit.blocked.push(url); return route.abort();
  });
  context.on('page', page => {
    page.setDefaultTimeout(15_000);
    page.on('pageerror', error => audit.errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(message.text())) audit.shaderErrors.push(message.text());});
  });
  return {context, audit};
}
