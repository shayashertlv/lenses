#!/usr/bin/env node
/** arview capture: posed AR images of one GLB for Astra and the owner (WP1_DESIGN.md §6; analysis.capture in
 *  plan/port_review/arview.json).
 *
 *    node qa/capture.mjs <model.glb> --poses default|junction|all --out <dir> [options]   (bin: arview capture ...)
 *
 *  Orchestration (Node): parse the arguments (capture/cli.ts), start the Python validator in parallel (spec.py --full,
 *  the single source of truth for the §2 rows), start Vite on arview (root arview, publicDir ../../ar/public read-only,
 *  cacheDir arview/node_modules/.vite-capture, no watcher, dependencies pre-bundled so a cold cache cannot reload the
 *  page mid-run) and headless Chromium on the GPU (ANGLE D3D11; launched as a BrowserServer so its process tree can be
 *  killed, capture/chromium.mjs), then drive qa/capture.html ONE STEP PER page.evaluate, each with its own deadline
 *  (every other await on Vite or Chromium has one too). A step that misses its deadline is a stall: the Chromium tree
 *  is killed and confirmed gone (report.chromium_process), a partial report is written, and the process exits 1 inside
 *  2 minutes (every stall path is listed in capture/cli.ts stallReportPathsMs; the worst, a stalled connect, is
 *  95 s + 3 s inner kill + 20 s cleanup = 118 s). Vite runs with appType 'mpa' and answers every unrouted /__arview/
 *  path 404, so a missing external resource is missing to arview (never index.html). PNGs stream to disk through the `arviewEmit` binding the
 *  moment the page makes them (sha256 each). A software renderer (SwiftShader, WARP, ...) is refused before any model
 *  is loaded, and again for each renderer that draws (posed, portrait, and their reloads), from its own GPU string.
 *  The worn front portrait renders face-a.jpg, read read-only from ../../ar/qa/fixtures and pinned by sha256;
 *  no other face image is ever used. --live-probe loads the GLB once on main's unmodified ar/ (capture/live-probe.mjs).
 *  `--cameras <cameras.json> --no-face` (dev capture, capture/cameras.ts) replaces the pose sets with explicit pinhole
 *  cameras and no face: the model at its authored size, `<id>__beauty.png` on each camera's flat background and
 *  `<id>__part-id.png`, drawn by the same renderer (TryOnRenderer.poseCamera); no settle, no junction, no portrait.
 *  report.json is assembled by capture/report.ts; every fallback and every capture problem is a warning in it.
 *  Exit codes: 0 captured and spec.py passes; 3 captured but the spec fails or is unverified; 4 refused or unchecked by
 *  arview; 1 infrastructure failure (stall, crash, software GPU, integrity); 2 usage error. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer, version as viteVersion} from 'vite';
import {chromium} from '@playwright/test';
import {ABORT_CLEANUP_BUDGET_MS, INNER_KILL_BUDGET_MS, launchDeadlineMs, parseCaptureArgs, stepDeadlineMs, UsageError, USAGE} from './capture/cli.ts';
import {closeChromium, killProcessTree, launchChromium, runBounded} from './capture/chromium.mjs';
import {buildCapturePlan, SAFE_FILE} from './capture/poses.ts';
import {cameraFile, CAMERA_VARIANTS, CameraSetError, parseCameraSet} from './capture/cameras.ts';
import {assembleCaptureReport, EXIT, softwareRendererReason} from './capture/report.ts';
import {consoleLevel, FACE_A_RELATIVE_PATH, FACE_A_SHA256} from './capture/portrait-contract.ts';
import {readGlbInput} from './capture/glb-input.ts';
import {resolveExternalResources} from './capture/external-resources.ts';
import {runSpecValidator} from './capture/spec-validator.mjs';
import {modelRoutes, routesMiddleware, runLiveProbe} from './capture/live-probe.mjs';

export const ARVIEW_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const LENSES_ROOT = path.resolve(ARVIEW_ROOT, '..', '..');
const AR_ROOT = path.join(LENSES_ROOT, 'ar');
const PREBUNDLED = ['three', 'three/addons/loaders/GLTFLoader.js', 'three/addons/loaders/DRACOLoader.js', 'three/addons/loaders/KTX2Loader.js',
  'three/addons/libs/meshopt_decoder.module.js', 'three/addons/environments/RoomEnvironment.js', 'three/addons/geometries/ConvexGeometry.js',
  '@mediapipe/tasks-vision'];

const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const messageOf = error => error instanceof Error ? error.message : String(error);

class StallError extends Error {
  // The exact deadline (7500 ms prints "7.5 s", never a rounded "8 s").
  constructor(label, ms) {super(`step "${label}" did not finish within ${ms / 1000} s`); this.name = 'StallError'; this.label = label; this.deadlineMs = ms;}
}
/** Race a promise against a deadline. The timer never keeps the process alive. */
export function withTimeout(promise, ms, label) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {timer = setTimeout(() => reject(new StallError(label, ms)), ms); timer.unref?.();})])
    .finally(() => clearTimeout(timer));
}

/** Write a file through a temporary name and a rename, retried: Windows Defender denies about 1 % of renames. */
async function writeAtomic(file, text) {
  const temporary = `${file}.tmp`;
  await fsp.writeFile(temporary, text);
  for (let attempt = 0; ; attempt++) {
    try {await fsp.rename(temporary, file); return;}
    catch (error) {
      if (attempt >= 7) {await fsp.copyFile(temporary, file); await fsp.rm(temporary, {force: true}); return;}
      await new Promise(resolve => setTimeout(resolve, 100 * (attempt + 1)));
    }
  }
}

/** spec.py --full on the input (glasses3d/.venv), or why it is unavailable. Never rejects (capture/spec-validator.mjs,
 *  shared with qa/serve.mjs). */
export {runSpecValidator};

/** The arview commit and whether its tree is dirty: two git calls, asynchronous (they never block the event loop and
 *  its deadlines) and capped at 15 s each; a failure is recorded, not thrown. */
async function gitState() {
  const [head, status] = await Promise.all([runBounded('git', ['rev-parse', 'HEAD'], 15_000, {cwd: ARVIEW_ROOT}),
    runBounded('git', ['status', '--porcelain', '--', '.'], 15_000, {cwd: ARVIEW_ROOT})]);
  const failure = result => result.timedOut ? 'timed out after 15 s' : result.error ?? `exit ${result.status}: ${result.stderr.slice(0, 200)}`;
  return {arview_commit: head.status === 0 ? head.stdout : null, arview_dirty: status.status === 0 ? status.stdout.length > 0 : null,
    ...(head.status === 0 && status.status === 0 ? {} : {git_problem: `git ${head.status !== 0 ? `rev-parse ${failure(head)}` : `status ${failure(status)}`}`})};
}

/** SHA-256 of every source the capture runs (its own files, the page's transitive relative imports, the public binaries
 *  it reads), so a report says exactly which implementation made it, and whether it changed during the run. */
async function implementationSnapshot(extra = []) {
  const todo = ['qa/capture.mjs', 'qa/capture.html', 'bin/arview.mjs', 'package.json', 'package-lock.json', 'vite.config.ts'];
  for (const name of await fsp.readdir(path.join(ARVIEW_ROOT, 'qa/capture'))) todo.push(`qa/capture/${name}`);
  const hashes = {};
  while (todo.length) {
    const name = todo.pop(); if (name in hashes) continue;
    let data;
    try {data = await fsp.readFile(path.join(ARVIEW_ROOT, name));}
    catch (error) {hashes[name] = error.code === 'ENOENT' ? 'missing' : `unreadable: ${error.code}`; continue;}
    hashes[name] = sha256(data);
    if (!/\.(?:mjs|ts|js|html)$/.test(name)) continue;
    for (const match of data.toString('utf8').matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*|\bsrc=)['"](\.{1,2}\/[^'"]+)['"]/g)) {
      const target = path.posix.normalize(path.posix.join(path.posix.dirname(name), match[1]));
      if (!target.startsWith('..') && (target.startsWith('src/') || target.startsWith('qa/'))) todo.push(target);
    }
  }
  for (const file of extra) {
    try {hashes[path.relative(ARVIEW_ROOT, file).replaceAll('\\', '/')] = sha256(await fsp.readFile(file));}
    catch (error) {hashes[file] = `unreadable: ${error.code}`;}
  }
  return Object.fromEntries(Object.entries(hashes).sort(([a], [b]) => a.localeCompare(b)));
}

/** Run one capture. Returns the exit code (the bin and the direct invocation exit with it). */
export async function main(argv) {
  if (argv.includes('--help') || argv.includes('-h')) {console.log(USAGE); return EXIT.ok;}
  let options;
  try {options = parseCaptureArgs(argv);}
  catch (error) {
    if (error instanceof UsageError) {console.error(`arview capture: ${error.message}\n${USAGE}`); return EXIT.usage;}
    throw error;
  }
  const started = Date.now();
  const glbPath = path.resolve(options.glb), out = path.resolve(options.out);
  let glbBytes;
  try {glbBytes = await fsp.readFile(glbPath);}
  catch (error) {console.error(`arview capture: cannot read ${glbPath} (${messageOf(error)})`); return EXIT.usage;}
  // --cameras: the explicit cameras, validated before anything starts or is written (a bad file is a usage error, exit 2).
  let cameraSet = null, camerasBytes = null;
  if (options.cameras) {
    try {camerasBytes = await fsp.readFile(path.resolve(options.cameras)); cameraSet = parseCameraSet(JSON.parse(camerasBytes.toString('utf8')));}
    catch (error) {
      if (error instanceof CameraSetError || error instanceof SyntaxError || error?.code) {
        console.error(`arview capture: --cameras ${path.resolve(options.cameras)}: ${messageOf(error)}`); return EXIT.usage;
      }
      throw error;
    }
  }
  await fsp.mkdir(out, {recursive: true});
  const existing = (await fsp.readdir(out)).filter(name => !name.startsWith('.'));
  if (existing.length) {
    console.error(`arview capture: ${out} is not empty (${existing.length} entries, e.g. ${existing.slice(0, 3).join(', ')}); use a fresh output `
      + 'directory so no stale image can be mistaken for this run\'s.');
    return EXIT.usage;
  }
  const inputSha = sha256(glbBytes);
  const glb = readGlbInput(new Uint8Array(glbBytes.buffer, glbBytes.byteOffset, glbBytes.byteLength));
  const plan = cameraSet ? {set: null, backgrounds: [], poses: [], renderCount: cameraSet.cameras.length * CAMERA_VARIANTS.length, plannedCropCount: 0}
    : buildCapturePlan(options.poses, {backgrounds: options.backgrounds, depthCm: options.depthCm});
  const stepMs = Math.round(options.stepTimeoutS * 1000);
  const warnings = [], errors = [], images = [], poses = [], timings = [], idPalette = [];
  const page_ = {console_errors: [], console_warnings: [], page_errors: [], failed_responses: [], blocked_external: [], navigations: 0};
  const written = new Map();
  const state = {gpu: null, modelReport: null, fitting: null, fitFallback: false, geometry: null, portrait: null, liveProbe: null,
    infrastructureFailure: null, spec: null, chromiumProcess: null, captureRefusal: null};
  const servedName = path.basename(glbPath).replace(/[^A-Za-z0-9._-]/g, '_') || 'model.glb';
  const modelPath = `/__arview/models/${inputSha}/${servedName}`;
  const displayName = path.basename(glbPath);

  // External resources referenced by the GLB (capture/external-resources.ts, shared with arview serve): each read where
  // spec.py reads it (the GLB's folder joined with Python's unquote(uri): '100%.png' and '../shared/a.bin' are found while
  // the file exists, with a warning when it lies outside the folder) and served at the raw pathname arview's loader
  // requests. An absolute URL or a file that cannot be read is answered 404 (routesMiddleware: nothing under /__arview/
  // falls through to Vite), so arview sees it missing and must refuse the model at the Format row (WP1_DESIGN §1), as
  // spec.py fails it; a render anyway is an error below. A resource whose URL would shadow one of the capture's own files
  // (face-a, a Vite path, a file of arview or of the public directory) is an error, never served over it.
  const external = await resolveExternalResources(glbPath, glb.externalUris, {modelPath, tool: 'the capture',
    reserved: (_route, decoded) => decoded !== null && (decoded === '/__arview/face-a.jpg' || decoded.startsWith('/@')
      || [ARVIEW_ROOT, path.join(AR_ROOT, 'public')].some(root => fs.existsSync(path.join(root, decoded))))});
  const resources = external.resources, unreadable = external.unreadable;
  warnings.push(...external.warnings);
  errors.push(...external.errors);
  if (glb.externalUris.length) warnings.push(`the GLB is not self-contained (${glb.externalUris.length} external resource(s)); PLAN §2 prefers one file`);
  // The unreadable resources go along as explicit 404 routes (modelRoutes), wherever they resolve: also for the live probe.
  const model = {bytes: glbBytes, sha256: inputSha, servedName, resources, missing: unreadable};
  const routes = modelRoutes(modelPath, model);

  // face-a: read read-only from ar/qa/fixtures and pinned; any other bytes skip the portrait (never another image).
  let faceA = null;
  if (options.portrait) {
    const facePath = path.resolve(ARVIEW_ROOT, FACE_A_RELATIVE_PATH);
    try {
      const bytes = await fsp.readFile(facePath), digest = sha256(bytes);
      if (digest !== FACE_A_SHA256) warnings.push(`PORTRAIT SKIPPED: ${facePath} has sha256 ${digest}, not the pinned ${FACE_A_SHA256}; no other face image is ever used`);
      else {faceA = {path: facePath, bytes, sha256: digest}; routes.set('/__arview/face-a.jpg', {bytes, type: 'image/jpeg'});}
    } catch (error) {warnings.push(`PORTRAIT SKIPPED: ${facePath} cannot be read (${error.code ?? messageOf(error)})`);}
  }

  const specPromise = runSpecValidator(glbPath).then(value => {state.spec = value; return value;});
  const publicFiles = ['models/canonical-face.json', 'models/face_landmarker.task', 'models/hair/hair-only.tflite'].map(file => path.join(AR_ROOT, 'public', file));
  const before = await implementationSnapshot(publicFiles);
  const git = await gitState();
  if (git.git_problem) warnings.push(`the arview commit could not be recorded (${git.git_problem})`);
  const tool = {name: 'arview capture', ...git, runtime_base: 'arview (fork of ar/ at 75ae133 + chosen stash@{0} edits; WP1 stages A-D2)',
    node: process.version, vite: viteVersion, playwright: null, chromium: null, chromium_pid: null,
    chromium_launch: 'chromium.launchServer + connect (a killable process handle)',
    chromium_args: ['--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist']};
  const request = {glb: glbPath, poses: options.poses, out, backgrounds: plan.backgrounds, size: `${options.width}x${options.height}`,
    depth_cm: options.depthCm, fixed_scale: options.fixedScale, clip: options.clipZM ?? 'auto', lensenv: options.lensEnvIntensity,
    crop_upscale: options.cropUpscale, portrait: options.portrait, live_probe: options.liveProbe, step_timeout_s: options.stepTimeoutS,
    plan: {poses: plan.poses.map(entry => entry.pose.id), renders: plan.renderCount, junction_crops: plan.plannedCropCount}};
  if (cameraSet) {
    // The explicit-camera mode replaces the pose-set fields that do not apply (recorded as such, never as defaults).
    Object.assign(request, {poses: null, backgrounds: null, size: null, depth_cm: null, clip: 'none (no face: the arms are drawn as authored)',
      fixed_scale: true, no_face: true, cameras: {path: path.resolve(options.cameras), sha256: sha256(camerasBytes), schema: cameraSet.schema,
        ids: cameraSet.cameras.map(camera => camera.id)},
      plan: {cameras: cameraSet.cameras.map(camera => camera.id), renders: plan.renderCount, junction_crops: 0}});
  }
  const plannedImages = plan.renderCount + plan.plannedCropCount + (faceA ? 5 : 0);

  const loadMs = stepDeadlineMs(options.stepTimeoutS, 1.5);
  let server = null, browser = null, chrome = null, page = null;
  const time = async (label, run) => {
    const at = Date.now();
    try {const value = await run(); timings.push({step: label, ms: Date.now() - at}); return value;}
    catch (error) {timings.push({step: label, ms: Date.now() - at, failed: true}); throw error;}
  };
  // TEST HOOK (qa/capture-stall.test.mjs): ARVIEW_CAPTURE_TEST_STALL=<step label> makes that one step a page.evaluate that
  // never settles ('chromium: connect': the connect; 'live probe: load on main's ar/': the probe's evaluate), to prove the
  // stall path (deadline, Chromium killed, partial report, exit 1). Recorded in the report.
  const stallAt = process.env.ARVIEW_CAPTURE_TEST_STALL || null;
  if (stallAt) {request.test_stall_step = stallAt; warnings.push(`TEST HOOK: ARVIEW_CAPTURE_TEST_STALL makes step "${stallAt}" hang on purpose`);}
  const step = (label, fn, arg, ms = stepMs) => time(label, () => withTimeout(label === stallAt
    ? page.evaluate(() => new Promise(() => {})) : page.evaluate(fn, arg), ms, label));
  const log = text => process.stderr.write(`[arview capture ${((Date.now() - started) / 1000).toFixed(1)} s] ${text}\n`);

  const finish = async () => {
    const after = await implementationSnapshot(publicFiles).catch(error => ({error: messageOf(error)}));
    const stable = JSON.stringify(before) === JSON.stringify(after);
    if (!stable) warnings.push('the capture\'s own sources or public binaries changed during the run (implementation.before != after)');
    for (const image of images) {
      const record = written.get(image.file);
      if (!record) errors.push(`image ${image.file} is listed but was never written`);
      else if (record.sha256 !== image.sha256) errors.push(`image ${image.file}: the page reported sha256 ${image.sha256}, the file has ${record.sha256}`);
    }
    for (const file of written.keys()) if (!images.some(image => image.file === file)) warnings.push(`file ${file} was written but belongs to no completed step (partial run)`);
    const {report, exitCode} = assembleCaptureReport({createdAt: new Date().toISOString(), tool, input: {path: glbPath, bytes: glbBytes.length,
      sha256: inputSha, self_contained: glb.externalUris.length === 0, external_uris: glb.externalUris, container_error: glb.containerError,
      extensions_used: glb.extensionsUsed, extensions_required: glb.extensionsRequired},
    request, gpu: state.gpu, spec: state.spec, modelReport: state.modelReport, fitting: state.fitting, fitFallback: state.fitFallback,
    geometry: state.geometry, poses, images, plannedImages, idPalette, portrait: state.portrait, liveProbe: state.liveProbe,
    warnings, errors, infrastructureFailure: state.infrastructureFailure, chromiumProcess: state.chromiumProcess, captureRefusal: state.captureRefusal,
    timings: {total_ms: Date.now() - started, steps: timings}, implementation: {before, after, stable}, page: page_});
    await writeAtomic(path.join(out, 'report.json'), JSON.stringify(report, null, 1) + '\n');
    const summary = {status: report.status, exit_code: exitCode, images: images.length, planned_images: plannedImages,
      spec_ok: state.spec?.available ? state.spec.report.ok : 'validator unavailable', warnings: report.warnings.length, errors: report.errors.length,
      infrastructure_failure: state.infrastructureFailure, seconds: Math.round((Date.now() - started) / 100) / 10, report: path.join(out, 'report.json')};
    console.log(JSON.stringify(summary));
    return exitCode;
  };

  // The abort's cleanup is bounded by ABORT_CLEANUP_BUDGET_MS (20 s): kill the Chromium tree and confirm it gone (at most
  // 8 s), then close Vite and a stalled probe's server (5 s) while waiting for spec.py (the rest), so every stall path
  // (cli.ts stallReportPathsMs) is reported inside 2 minutes.
  const abort = async (failure, error) => {
    const cleanupStarted = Date.now();
    state.infrastructureFailure = failure;
    if (error && !(error instanceof StallError)) errors.push(`${failure}: ${error.stack ?? messageOf(error)}`.slice(0, 4000));
    let killed;
    if (!chrome && error?.chromiumProcess) {
      // launchChromium failed: it already killed what it launched (or got no handle) and says so; a kill it could not
      // confirm is retried here on the handle it attached.
      killed = error.chromiumProcess;
      if (killed.gone === false && error.chromiumServer) {
        const retry = await killProcessTree(error.chromiumServer.process?.() ?? null, 8000);
        killed = {...retry, ended: `${killed.ended}; killed again by the abort`, first_attempt: killed.text};
      }
      if (killed.pid && !tool.chromium_pid) tool.chromium_pid = killed.pid;
    } else {
      killed = await killProcessTree(chrome?.server?.process?.() ?? null, 8000);
      killed = {...killed, ended: killed.pid ? 'killed after the failure' : 'never launched'};
    }
    state.chromiumProcess = killed;
    if (killed.pid || killed.ended === 'launch did not complete') warnings.push(`after the failure: ${killed.text}.`);
    if (killed.gone === false) errors.push(`Chromium pid ${killed.pid} is still running after the kill`);
    chrome = null; browser = null;
    const remaining = () => Math.max(1000, ABORT_CLEANUP_BUDGET_MS - (Date.now() - cleanupStarted) - 1000);
    const bounded = (promise, ms) => Promise.race([Promise.resolve(promise).catch(() => {}),
      new Promise(resolve => {const timer = setTimeout(resolve, ms); timer.unref?.();})]);
    // The validator normally finished long ago; a stalled run waits only the rest of the cleanup budget for it.
    await Promise.all([server ? bounded(server.close(), Math.min(5000, remaining())) : null,
      error?.pendingClose ? bounded(error.pendingClose, Math.min(5000, remaining())) : null,
      state.spec ? null : bounded(specPromise, remaining())]);
    if (error?.pendingClose) warnings.push('the stalled --live-probe skipped its graceful close (Chromium was killed instead); its ar/ dev server was closed by the abort');
    server = null;
    timings.push({step: 'abort: cleanup', ms: Date.now() - cleanupStarted});
    log(`INFRASTRUCTURE FAILURE: ${failure} (cleanup ${Date.now() - cleanupStarted} ms)`);
    return finish();
  };
  const onSignal = () => {abort('interrupted (SIGINT)').finally(() => process.exit(EXIT.infrastructure));};
  process.once('SIGINT', onSignal);

  try {
    server = await time('vite: start', async () => {
      const creating = createServer({configFile: path.join(ARVIEW_ROOT, 'vite.config.ts'), root: ARVIEW_ROOT,
        cacheDir: path.join(ARVIEW_ROOT, 'node_modules', '.vite-capture'), logLevel: 'error', clearScreen: false,
        // 'mpa': no SPA fallback, so a missing file is a 404 and never index.html with HTTP 200.
        appType: 'mpa',
        // forwardConsole off: the page's console reaches report.page through Playwright, not the terminal.
        server: {host: '127.0.0.1', port: 0, strictPort: false, hmr: false, watch: null, forwardConsole: false},
        optimizeDeps: {entries: ['qa/capture.html'], include: PREBUNDLED},
        plugins: [{name: 'arview-capture-inputs', configureServer(devServer) {
          // The model, its readable external resources and face-a; any other /__arview/ path is a 404, and so is the URL of
          // every resource that cannot be read, wherever it resolves (modelRoutes `missing`).
          devServer.middlewares.use(routesMiddleware(routes, '/__arview/'));
        }}]});
      let created;
      try {created = await withTimeout(creating, stepMs, 'vite: create server');}
      catch (error) {
        // A server that is created after its deadline is closed the moment it is.
        creating.then(late => late.close(), () => {});
        throw error;
      }
      server = created;           // abort() closes it from here on, even if listen() stalls
      await withTimeout(created.listen(), stepMs, 'vite: listen');
      return created;
    });
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    const launchMs = launchDeadlineMs(options.stepTimeoutS);
    chrome = await time('chromium: launch', () => launchChromium(chromium, {args: tool.chromium_args, launchMs, deadline: withTimeout,
      killMs: INNER_KILL_BUDGET_MS, stallConnect: stallAt === 'chromium: connect'}));
    browser = chrome.browser;
    tool.chromium = browser.version(); tool.chromium_pid = chrome.pid;
    tool.playwright = JSON.parse(fs.readFileSync(path.join(ARVIEW_ROOT, 'node_modules/@playwright/test/package.json'), 'utf8')).version;
    const context = await time('chromium: context', () => withTimeout(browser.newContext({viewport: {width: 1400, height: 1000}}), stepMs, 'chromium: new context'));
    page = await time('chromium: page', () => withTimeout(context.newPage(), stepMs, 'chromium: new page'));
    page.setDefaultTimeout(stepMs);
    await withTimeout(page.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(origin + '/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
      page_.blocked_external.push(url); return route.abort();
    }), stepMs, 'chromium: route');
    page.on('pageerror', error => page_.page_errors.push(error.message));
    page.on('crash', () => page_.page_errors.push('the page crashed'));
    page.on('console', entry => {
      const level = consoleLevel(entry.type(), entry.text());
      if (level === 'error') page_.console_errors.push(entry.text().slice(0, 2000));
      else if (level === 'warning' && page_.console_warnings.length < 200) page_.console_warnings.push(entry.text().slice(0, 500));
    });
    page.on('response', response => {if (response.status() >= 400) page_.failed_responses.push({url: response.url(), status: response.status()});});
    page.on('framenavigated', frame => {if (frame === page.mainFrame()) page_.navigations++;});
    await withTimeout(page.exposeBinding('arviewEmit', async (_source, meta, base64) => {
      const file = meta?.file;
      if (typeof file !== 'string' || !SAFE_FILE.test(file)) throw new Error(`unsafe capture file name ${JSON.stringify(file)}`);
      if (written.has(file)) throw new Error(`capture file ${file} was already written in this run`);
      const bytes = Buffer.from(String(base64), 'base64');
      if (bytes.length < 8 || bytes.readUInt32BE(0) !== 0x89504e47) throw new Error(`capture file ${file} is not a PNG`);
      await fsp.writeFile(path.join(out, file), bytes);
      const record = {sha256: sha256(bytes), bytes: bytes.length};
      written.set(file, record);
      return record;
    }), stepMs, 'chromium: expose the PNG binding');
    await time('page: open', () => withTimeout(page.goto(origin + '/qa/capture.html'), stepMs, 'page: open'));
    await time('page: ready', () => withTimeout(page.waitForFunction(() => !!window.arview), stepMs, 'page: ready'));

    state.gpu = await step('gpu', () => window.arview.gpu());
    const software = softwareRendererReason(state.gpu?.renderer);
    state.gpu = {...state.gpu, software: software !== null};
    log(`GPU: ${state.gpu?.renderer ?? 'unknown'}`);
    if (!state.gpu?.webgl2) return await abort('WebGL 2 is unavailable in headless Chromium; nothing can be rendered');
    if (software) return await abort(`REFUSED: ${software}. The capture never reports images from a software renderer.`);
    if (!(state.gpu.samples > 0)) warnings.push(`the GPU context has ${state.gpu.samples} MSAA samples; the temple visibility (alpha-to-coverage) degrades`);
    if (!state.gpu.float_color) warnings.push('EXT_color_buffer_float is unavailable; shadows and float targets fall back');

    const initRequest = {modelUrl: modelPath, displayName, templeClipZM: options.clipZM, lensEnvIntensity: options.lensEnvIntensity,
      fixedScale: options.fixedScale, width: cameraSet ? cameraSet.cameras[0].width : options.width,
      height: cameraSet ? cameraSet.cameras[0].height : options.height, depthCm: options.depthCm, firstBackground: plan.backgrounds[0] ?? 'skin'};
    // Every renderer that draws images is checked itself, not only the throwaway probe context: a GPU process that fell
    // back to a software rasteriser between the probe and a renderer must never get its images reported.
    const renderers = [];
    const softwareRenderer = (label, renderer) => {
      renderers.push({renderer: label, gpu_renderer: renderer ?? null});
      state.gpu = {...state.gpu, renderers};
      if (renderer !== state.gpu.renderer) warnings.push(`the ${label} GPU string "${renderer}" differs from the probe's "${state.gpu.renderer}"`);
      const reason = softwareRendererReason(renderer);
      return reason ? `REFUSED: the ${label}: ${reason}. The capture never reports images from a software renderer.` : null;
    };
    let init = await step('init: load model', value => window.arview.init(value), initRequest, loadMs);
    state.modelReport = init.report;
    log(`model: ${init.status}${init.status === 'rendered' ? `, ${init.parts.length} meshes, clip ${init.clip_m} m` : `: ${(init.report?.refusal ?? init.report?.unchecked ?? []).join('; ')}`}`);
    if (unreadable.length && init.status === 'rendered') {
      // Whatever let arview get bytes for a missing resource (a route the capture could not answer 404, a page cache), the
      // §1 verdict stands: the model is refused (status refused, exit 4), and the images below are not a pass.
      state.captureRefusal = `arview RENDERED a model whose external resource(s) ${JSON.stringify(unreadable)} could not be read (answered 404 by the `
        + 'capture); §1 Format says it must be refused, so the capture refuses it (status refused, exit 4)';
      errors.push(state.captureRefusal);
    }
    if (init.status === 'rendered') {
      const refused = softwareRenderer('posed renderer', init.gpu_renderer);
      if (refused) return await abort(refused);
      if (init.rendered_sha256 !== inputSha) {
        return await abort(`INTEGRITY: the renderer parsed bytes with sha256 ${init.rendered_sha256}, not the input's ${inputSha}`);
      }
    }
    if (init.status === 'rendered' && cameraSet) {
      // Explicit cameras, no face: nothing to settle (the authored size is shown from the first frame).
      state.fitting = null;
      for (const camera of cameraSet.cameras) {
        const posed = await step(`camera ${camera.id}`, value => window.arview.poseCamera(value), {camera});
        poses.push({...posed, kind: 'explicit_camera', view_row_major_m: camera.view});
        if (!posed.explicit) warnings.push(`camera ${camera.id}: the renderer did not take the explicit camera`);
        for (const variant of CAMERA_VARIANTS) {
          const file = cameraFile(camera.id, variant);
          const result = await step(`render ${file}`, value => window.arview.renderCamera(value), {variant, file});
          for (const warning of result.warnings) warnings.push(warning);
          if (result.palette && !idPalette.length) idPalette.push(...result.palette);
          for (const image of result.images) {
            images.push({file: image.file, pose: camera.id, background: variant === 'beauty' ? camera.background : null, variant, sha256: image.sha256,
              bytes: image.bytes, width: image.width, height: image.height, kind: image.kind, crop: null, side: null, timing: result.timing});
          }
        }
        log(`camera ${camera.id}: ${CAMERA_VARIANTS.length} renders, ${images.length} images so far`);
      }
      const final = await step('finish: posed renderer', () => window.arview.finishPosed());
      if (final.report) state.modelReport = final.report;
      if (final.fitting) state.fitting = final.fitting;
    } else if (init.status === 'rendered') {
      const geometry = await step('geometry: lens top vs brow', () => window.arview.geometry());
      state.geometry = geometry;
      for (const warning of geometry.warnings) warnings.push(`junction geometry: ${warning}`);
      let settled = await step('settle: frontal fit', () => window.arview.settle());
      if (!settled.ready && !options.fixedScale) {
        warnings.push(`FIT DID NOT SETTLE after the frontal settle (state ${settled.fitting?.state}, progress ${settled.fitting?.progress}, rejection `
          + `${settled.fitting?.rejection ?? 'none'}); the capture falls back to the authored size (fixedScale) so the glasses are drawn`);
        state.fitFallback = true;
        init = await step('init: reload at authored size', value => window.arview.init(value), {...initRequest, fixedScale: true}, loadMs);
        if (init.status !== 'rendered') return await abort(`ERROR: the reload at the authored size did not render (${init.status}) although the first load did`);
        state.modelReport = init.report;
        const refusedReload = softwareRenderer('posed renderer (reloaded at the authored size)', init.gpu_renderer);
        if (refusedReload) return await abort(refusedReload);
        if (init.rendered_sha256 !== inputSha) return await abort(`INTEGRITY: the reloaded renderer parsed bytes with sha256 ${init.rendered_sha256}, not the input's ${inputSha}`);
        settled = await step('settle: frontal (authored size)', () => window.arview.settle());
      }
      if (!settled.ready) warnings.push('the fit is still not ready after the fallback; renders may show no glasses');
      if (settled.rejected) warnings.push(`${settled.rejected} of the frontal settle poses were rejected by the renderer`);
      state.fitting = settled.fitting;
      for (const planned of plan.poses) {
        const posed = await step(`pose ${planned.pose.id}`, value => window.arview.pose(value), {pose: planned.pose,
          background: planned.renders.find(render => render.background)?.background ?? plan.backgrounds[0]});
        poses.push(posed);
        if (posed.rejected) warnings.push(`pose ${planned.pose.id}: ${posed.rejected} of 30 settle poses were rejected (face not tracked)`);
        for (const side of posed.junction) if (!side.crop) warnings.push(`pose ${planned.pose.id}: no ${side.side} junction crop (${side.reason})`);
        for (const render of planned.renders) {
          const result = await step(`render ${render.file}`, value => window.arview.render(value), {...render, cropUpscale: options.cropUpscale});
          for (const warning of result.warnings) warnings.push(warning);
          if (result.palette && !idPalette.length) idPalette.push(...result.palette);
          for (const image of result.images) {
            images.push({file: image.file, pose: planned.pose.id, background: render.background, variant: render.variant, sha256: image.sha256,
              bytes: image.bytes, width: image.width, height: image.height, kind: image.kind, crop: image.crop, side: image.side,
              ...(image.kind === 'junction_crop' ? {near: image.near, metrics: image.metrics} : {}),
              ...(image.kind === 'pose' && result.temple_reach ? {temple_reach: result.temple_reach} : {}), timing: result.timing});
          }
        }
        log(`pose ${planned.pose.id}: ${planned.renders.length} renders, ${images.length} images so far`);
      }
      const final = await step('finish: posed renderer', () => window.arview.finishPosed());
      if (final.report) state.modelReport = final.report;
      if (final.fitting) state.fitting = final.fitting;
    }

    if (init.status === 'rendered' && faceA) {
      const portrait = {fixture: {id: 'face-a', synthetic: true, path: faceA.path, sha256: faceA.sha256}, mirrored: false,
        note: 'unmirrored; the live webcam page shows a mirror image (src/style.css #mirror scaleX(-1))', steps: {}, images: [], warnings: []};
      state.portrait = portrait;
      // Each portrait image and warning joins report.images / report.warnings the moment its step returns (as the posed
      // renders do), so a stall later in the portrait still reports what the completed steps produced.
      const addPortraitImages = list => {
        for (const image of list ?? []) {
          portrait.images.push(image);
          images.push({file: image.file, pose: 'portrait-face-a', background: 'face-a', variant: image.variant, sha256: image.sha256, bytes: image.bytes,
            width: image.width, height: image.height, kind: 'portrait', crop: image.crop, side: null, region: image.region});
        }
      };
      const addPortraitWarnings = list => {for (const warning of list ?? []) {portrait.warnings.push(warning); warnings.push(`portrait: ${warning}`);}};
      const prepared = await step('portrait: detect + segment face-a', value => window.arview.portraitPrepare(value), faceA.sha256);
      portrait.steps.prepare = {...prepared, images: undefined};
      addPortraitWarnings(prepared.warnings);
      addPortraitImages(prepared.images);
      if (prepared.ok) {
        const portraitRequest = {modelUrl: modelPath, displayName, sha256: inputSha, fixtureSha256: faceA.sha256, templeClipZM: options.clipZM,
          lensEnvIntensity: options.lensEnvIntensity, fixedScale: options.fixedScale || state.fitFallback};
        let created = await step('portrait: load model', value => window.arview.portraitCreate(value), portraitRequest, loadMs);
        addPortraitWarnings(created.warnings);
        if (created.ok && !created.integrity_ok) {
          return await abort(`INTEGRITY: the portrait renderer parsed bytes with sha256 ${created.integrity_sha256}, not the input's ${inputSha}`);
        }
        if (created.ok) {
          const refusedPortrait = softwareRenderer('portrait renderer', created.gpu_renderer);
          if (refusedPortrait) return await abort(refusedPortrait);
          let settledPortrait = await step('portrait: settle', () => window.arview.portraitSettle());
          if (!settledPortrait.ready && !portraitRequest.fixedScale) {
            addPortraitWarnings([`PORTRAIT FIT DID NOT SETTLE (state ${settledPortrait.fitting?.state}, rejection ${settledPortrait.fitting?.rejection ?? 'none'}); `
              + 'the portrait falls back to the authored size (fixedScale) so the glasses are drawn']);
            created = await step('portrait: reload at authored size', value => window.arview.portraitCreate(value), {...portraitRequest, fixedScale: true}, loadMs);
            if (!created.ok) return await abort(`ERROR: the portrait reload at the authored size did not render (${created.status}) although the first load did`);
            addPortraitWarnings(created.warnings);
            if (!created.integrity_ok) return await abort(`INTEGRITY: the reloaded portrait renderer parsed bytes with sha256 ${created.integrity_sha256}, not the input's ${inputSha}`);
            const refusedPortraitReload = softwareRenderer('portrait renderer (reloaded at the authored size)', created.gpu_renderer);
            if (refusedPortraitReload) return await abort(refusedPortraitReload);
            settledPortrait = await step('portrait: settle (authored size)', () => window.arview.portraitSettle());
          }
          if (settledPortrait.rejected) addPortraitWarnings([`${settledPortrait.rejected} of 51 portrait poses were rejected`]);
          portrait.steps.settle = settledPortrait;
          portrait.conditions = [];
          for (const condition of ['a', 'b']) {
            const rendered = await step(`portrait: condition ${condition}`, value => window.arview.portraitRender(value), condition);
            addPortraitWarnings(rendered.warnings);
            addPortraitImages(rendered.images);
            portrait.conditions.push({...rendered, images: rendered.images.map(image => image.file)});
          }
          const finished = await step('portrait: finish', () => window.arview.portraitFinish());
          portrait.model_report = finished.report; portrait.asset_to_world = finished.asset_to_world;
        } else addPortraitWarnings([`the portrait renderer did not load the model (${created.status}); portrait skipped`]);
      }
      log(`portrait: ${portrait.images.length} images`);
    }

    if (options.liveProbe) {
      try {
        state.liveProbe = await time('live probe: main ar/', () => runLiveProbe({browser, arRoot: AR_ROOT, arviewRoot: ARVIEW_ROOT, model,
          displayName, clipZM: options.clipZM, lensEnvIntensity: options.lensEnvIntensity, stepTimeoutMs: stepMs, withTimeout, stallAt}));
        log(`live probe: ${state.liveProbe.verdict}: ${String(state.liveProbe.text).slice(0, 200)}`);
        if (state.liveProbe.ar_untouched === false) errors.push('the live probe changed ar/ (see live_site_would_fail.probe.ar_before/ar_after)');
        else if (state.liveProbe.ar_untouched === null) warnings.push('the live probe could not verify that ar/ is untouched (git status failed; see live_site_would_fail.probe.ar_before/ar_after)');
      } catch (error) {
        if (error instanceof StallError) throw error;
        state.liveProbe = {verdict: 'probe_failed', text: messageOf(error)};
        warnings.push(`the --live-probe could not run (${messageOf(error)}); the live verdict is the static rules only`);
      }
    }
  } catch (error) {
    if (error instanceof StallError) return await abort(`STALL: ${error.message}; this partial report was written after killing Chromium (see chromium_process)`, error);
    return await abort(`ERROR: ${messageOf(error).split('\n')[0].slice(0, 300)}`, error);
  } finally {process.removeListener('SIGINT', onSignal);}

  for (const text of page_.page_errors) errors.push(`page error: ${text}`);
  for (const text of page_.console_errors) warnings.push(`console error: ${text}`);
  for (const entry of page_.failed_responses) warnings.push(`HTTP ${entry.status} for ${entry.url}`);
  for (const url of page_.blocked_external) warnings.push(`blocked an external request: ${url}`);
  if (page_.navigations > 1) errors.push(`the capture page navigated ${page_.navigations} times (a mid-run reload)`);
  // Close Chromium gracefully; one that does not close within 10 s is killed (tree), and the outcome is recorded.
  state.chromiumProcess = await closeChromium(chrome, 10_000);
  if (state.chromiumProcess.ended !== 'closed') warnings.push(`closing Chromium: ${state.chromiumProcess.text}`);
  if (state.chromiumProcess.gone === false) errors.push(`Chromium pid ${state.chromiumProcess.pid} is still running after the kill`);
  chrome = null; browser = null;
  await Promise.race([Promise.resolve(server?.close()).catch(() => {}), new Promise(resolve => {const timer = setTimeout(resolve, 5000); timer.unref?.();})]);
  if (!state.spec) {
    log('waiting for spec.py (its own deadline is 120 s)');
    await specPromise;
  }
  return finish();
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(code => process.exit(code), error => {console.error(error?.stack ?? error); process.exit(EXIT.infrastructure);});
}
