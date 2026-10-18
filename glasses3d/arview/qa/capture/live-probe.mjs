/** `arview capture --live-probe`: load the GLB once on main's UNMODIFIED ar/ and record the exact refusal or success
 *  text its page would show (WP1_DESIGN.md §6; PLAN WP1.6 "first re-run it on main's ar/ to record the actual refusal").
 *
 *  ar/ is read-only: Vite serves root ar/ with no config file (ar/vite.config.ts is not bundled, so nothing is written to
 *  ar/node_modules/.vite-temp), its dependency cache redirected into arview/node_modules/.vite-live, no file watcher, and
 *  the probe page is a virtual module (live-probe-page.js) answered by a plugin at a path that is never written. The
 *  model reaches main's page through its own ?model= handover (src/eyewear/external.ts), exactly as a Studio try-on
 *  link hands it over (model, name, sha256, plus clip/lensenv when the capture was given them). Before and after, the
 *  probe records `git status` of ar/ and the entries of ar/node_modules, and reports whether anything changed. */
import fs from 'node:fs';
import path from 'node:path';
import {createServer} from 'vite';
import {runBounded} from './chromium.mjs';
import {isAbsoluteUri, resourceRoute} from './external-resources.ts';

const normal = value => path.resolve(value).replaceAll('\\', '/').toLowerCase();

/** The routes of a served model: its own path, and each external resource at the raw pathname arview's loader requests
 *  against the model URL (external-resources.ts resourceRoute: a relative URI, '..' included). `resources` are
 *  {uri, type, bytes} entries the capture read where spec.py reads them (only URIs the GLB's JSON references are ever
 *  served). `missing` are the URIs of resources that could not be read: each is an explicit {missing: true} route, answered
 *  404 by routesMiddleware WHATEVER its prefix (a URI climbing out of the model's folder resolves outside the prefix, where
 *  the dev server would otherwise answer with one of its own files and arview would get bytes for a missing resource). */
export function modelRoutes(modelPath, model) {
  const routes = new Map([[modelPath, {bytes: model.bytes, type: 'model/gltf-binary'}]]);
  for (const resource of model.resources ?? []) {
    routes.set(resourceRoute(resource.uri, modelPath), {bytes: resource.bytes, type: resource.type});
  }
  for (const uri of model.missing ?? []) {
    if (isAbsoluteUri(uri)) continue;
    let route;
    try {route = resourceRoute(uri, modelPath);} catch {continue;}
    if (!routes.has(route)) routes.set(route, {missing: true, uri});
  }
  return routes;
}

/** The connect middleware that serves `routes` and answers 404 for every other path under `prefix`, so a resource the
 *  GLB references but the capture could not read is MISSING to the page, exactly as on a file server: never handed to
 *  Vite's next middleware (its SPA fallback answered such a path with index.html and HTTP 200, so arview got bytes and
 *  did not refuse). Both servers also run with appType 'mpa' (no SPA fallback anywhere). */
export function routesMiddleware(routes, prefix) {
  return (request, response, next) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    const route = routes.get(url.pathname);
    if (route && !route.missing) {response.setHeader('Content-Type', route.type); response.setHeader('Cache-Control', 'no-store'); response.end(route.bytes); return;}
    if (!route && !url.pathname.startsWith(prefix)) {next(); return;}
    response.statusCode = 404;
    response.setHeader('Content-Type', 'text/plain; charset=utf-8'); response.setHeader('Cache-Control', 'no-store');
    response.end(route ? `404: ${url.pathname} is the external resource "${route.uri}" the GLB references, which cannot be read (missing)`
      : `404: ${url.pathname} is not served (only the GLB and the external resources it references that exist beside it)`);
  };
}

/** What could reveal a write into ar/: tracked/untracked changes, and the dependency caches in ar/node_modules. The git
 *  call is asynchronous (the capture's deadlines keep firing) and capped at 15 s; a timeout is recorded, not thrown. */
export async function arSnapshot(arRoot) {
  const git = await runBounded('git', ['status', '--porcelain', '--untracked-files=all', '--', '.'], 15_000, {cwd: arRoot});
  const modules = path.join(arRoot, 'node_modules');
  let entries = [];
  try {
    entries = fs.readdirSync(modules, {withFileTypes: true}).filter(entry => entry.name.startsWith('.')).map(entry => {
      const stat = fs.statSync(path.join(modules, entry.name));
      return `${entry.name}@${Math.round(stat.mtimeMs)}`;
    }).sort();
  } catch (error) {entries = [`unreadable: ${error.message}`];}
  return {git_status: git.status === 0 ? git.stdout
    : `git status failed: ${git.timedOut ? 'timed out after 15 s' : (git.stderr || git.error || `exit ${git.status}`)}`, node_modules_dot_entries: entries};
}

/** Run the probe in its own browser context. `withTimeout(promise, ms, label)` is the capture's stall guard; a stall
 *  (an error named 'StallError') skips the graceful context and server close, because the capture's abort kills the
 *  Chromium tree anyway: the server's close is started, not awaited, and handed to the abort as `error.pendingClose`
 *  (it waits for it inside its own bounded cleanup). `stallAt` (test hook) makes the step of that label never settle. */
export async function runLiveProbe({browser, arRoot, arviewRoot, model, displayName, clipZM, lensEnvIntensity, stepTimeoutMs, withTimeout, stallAt = null}) {
  const started = Date.now();
  const cacheDir = path.join(arviewRoot, 'node_modules', '.vite-live');
  const virtualPath = path.join(arRoot, 'qa', '__arview_live_probe.js');
  if (fs.existsSync(virtualPath)) throw new Error(`${virtualPath} exists on disk; the live probe never writes it and will not shadow it`);
  const pageSource = fs.readFileSync(path.join(arviewRoot, 'qa', 'capture', 'live-probe-page.js'), 'utf8');
  const before = await arSnapshot(arRoot);
  const modelPath = `/__arview_live/models/${model.sha256}/${model.servedName}`;
  const routes = modelRoutes(modelPath, model);
  const html = '<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,"><title>arview live probe</title>'
    + '<script type="module" src="/qa/__arview_live_probe.js"></script>';
  const creating = createServer({configFile: false, root: arRoot, cacheDir, logLevel: 'error', clearScreen: false, envFile: false, appType: 'mpa',
    define: {__BUILD_TIME__: JSON.stringify(new Date().toISOString())}, worker: {format: 'es'},
    server: {host: '127.0.0.1', port: 0, strictPort: false, hmr: false, watch: null, forwardConsole: false, fs: {strict: true, allow: [arRoot, cacheDir]}},
    optimizeDeps: {entries: [], include: ['three', 'three/addons/loaders/GLTFLoader.js', 'three/addons/environments/RoomEnvironment.js',
      'three/addons/geometries/ConvexGeometry.js']},
    plugins: [{name: 'arview-live-probe',
      resolveId(id) {
        const clean = id.split('?')[0];
        if (clean === '/qa/__arview_live_probe.js' || normal(clean) === normal(virtualPath)) return virtualPath;
        return null;
      },
      load(id) {return normal(id.split('?')[0]) === normal(virtualPath) ? pageSource : null;},
      configureServer(devServer) {
        const serveModel = routesMiddleware(routes, '/__arview_live/');
        devServer.middlewares.use((request, response, next) => {
          const url = new URL(request.url ?? '/', 'http://127.0.0.1');
          if (url.pathname === '/__arview_live.html') {response.setHeader('Content-Type', 'text/html'); response.setHeader('Cache-Control', 'no-store'); response.end(html); return;}
          serveModel(request, response, next);
        });
      }}]});
  let server;
  try {server = await withTimeout(creating, stepTimeoutMs, 'live probe: create the ar/ server');}
  catch (error) {creating.then(late => late.close(), () => {}); throw error;}
  let context, stalled = null;
  const events = {errors: [], failed_responses: [], blocked_external: []};
  const evaluate = (page, fn, arg, label) => withTimeout(label === stallAt ? page.evaluate(() => new Promise(() => {})) : page.evaluate(fn, arg), stepTimeoutMs, label);
  try {
    await withTimeout(server.listen(), stepTimeoutMs, 'live probe: ar/ server listen');
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    const params = new URLSearchParams({model: origin + modelPath, name: displayName, sha256: model.sha256});
    if (clipZM !== null) params.set('clip', String(clipZM));
    if (lensEnvIntensity !== null) params.set('lensenv', String(lensEnvIntensity));
    const search = '?' + params.toString();
    context = await withTimeout(browser.newContext({viewport: {width: 1400, height: 1000}}), stepTimeoutMs, 'live probe: new context');
    const page = await withTimeout(context.newPage(), stepTimeoutMs, 'live probe: new page'); page.setDefaultTimeout(stepTimeoutMs);
    await withTimeout(page.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(origin + '/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
      events.blocked_external.push(url); return route.abort();
    }), stepTimeoutMs, 'live probe: route');
    page.on('pageerror', error => events.errors.push(`pageerror: ${error.message}`));
    page.on('console', entry => {if (entry.type() === 'error') events.errors.push(`console: ${entry.text()}`);});
    page.on('response', response => {if (response.status() >= 400) events.failed_responses.push({url: response.url(), status: response.status()});});
    let result = null, attempts = 0;
    while (!result) {
      attempts++;
      await withTimeout(page.goto(origin + '/__arview_live.html'), stepTimeoutMs, 'live probe: open main\'s ar/ page');
      await withTimeout(page.waitForFunction(() => !!window.liveProbe), stepTimeoutMs, 'live probe: page ready');
      try {result = await evaluate(page, value => window.liveProbe.run(value), search, 'live probe: load on main\'s ar/');}
      catch (error) {
        // A cold dependency cache can make Vite reload the page once (a late-discovered import); retry exactly once.
        if (attempts < 2 && /Execution context was destroyed|navigation/i.test(String(error?.message))) continue;
        throw error;
      }
    }
    const after = await arSnapshot(arRoot);
    // A failed git status cannot prove ar/ untouched: unknown (null), with both snapshots, never a false 'true'.
    const unverifiable = [before, after].some(snapshot => snapshot.git_status.startsWith('git status failed'));
    const untouched = unverifiable ? null : JSON.stringify(before) === JSON.stringify(after);
    return {...result, attempts, ms: Date.now() - started, page_events: events, ar_untouched: untouched,
      ...(untouched === true ? {} : {ar_before: before, ar_after: after}), vite_cache_dir: cacheDir};
  } catch (error) {
    if (error?.name === 'StallError') stalled = error;
    throw error;
  } finally {
    if (stalled) {
      // No graceful close after a stall: the abort kills the Chromium tree (the context goes with it) and waits for this
      // server's close inside its own bounded cleanup, so the stall is still reported within 2 minutes.
      stalled.pendingClose = Promise.resolve().then(() => server.close()).catch(() => {});
    } else {
      const bounded = (promise, ms) => Promise.race([Promise.resolve(promise).catch(() => {}), new Promise(resolve => {const timer = setTimeout(resolve, ms); timer.unref?.();})]);
      await bounded(context?.close(), 5000);
      await bounded(server.close(), 5000);
    }
  }
}
