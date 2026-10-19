#!/usr/bin/env node
/** arview serve: the owner's webcam page for one GLB (glasses3d/plan/archive/20261019/WP1_DESIGN.md §6; WP1 stage F).
 *
 *    node qa/serve.mjs <model.glb> [--port 8251] [--detach] [--name <label>] [--clip <m>] [--lensenv N]   (bin: arview serve ...)
 *    node qa/serve.mjs --stop [--port 8251]          node qa/serve.mjs --status [--port 8251]
 *
 *  1. Builds the frozen page if its sources changed (serve/build.mjs: content-addressed under
 *     glasses3d/data/arview/serve/builds/, verified file by file when reused; a build past SERVE_BUILD_TIMEOUT_MS is a
 *     stall: reported, exit 1).
 *  2. Checks the model on the very bytes it will serve, in parallel and each with a deadline: spec.py --full (the single
 *     source of truth for the §2 rows) and arview's own load front-end and normalisation pass in Node (src/model/check.ts,
 *     the renderer's load minus drawing; textures not decoded) plus the temple clip the renderer derives. The result is
 *     /__arview/report.json (serve/report.ts), which the page lists before the camera opens; a check that fails or times
 *     out is an error in that report, never a silent gap.
 *  3. Serves, on 127.0.0.1 only: the build (each file pinned by sha256, re-checked on every request), the public
 *     binaries from arview/public (independent pinned cache; test GLBs live outside it), the model at
 *     /__arview/models/<sha256>/<name> with X-Arview-Sha256 and Repr-Digest headers, the external resources it names
 *     (capture/external-resources.ts, shared with arview capture: each read where spec.py reads it, the GLB's folder
 *     joined with Python's unquote(uri), so '100%.png' and '../shared/a.bin' are served while the file exists, with a
 *     warning when it lies outside the folder; routed at the raw pathname arview's loader requests; only an absolute URL
 *     or a file that cannot be read is answered 404, so arview refuses the model as spec.py fails it; a resource whose URL
 *     would be one of the server's own paths is an error in the report), and the report. Every
 *     response carries COOP same-origin, COEP require-corp, CORP same-origin, Permissions-Policy camera=(self) and
 *     no-store. A request with Host localhost:<port> is redirected (307) to the same path on 127.0.0.1 (one origin: the
 *     handover URLs are 127.0.0.1's); any other Host but 127.0.0.1:<port> is answered 421 (DNS rebinding). Anything
 *     else is a 404 (no SPA fallback); only GET and HEAD, plus the token-guarded POST /__arview/stop. While the model is
 *     being checked every GET is a 503 (the report path answers a JSON body naming this server, so --status, --stop and a
 *     second serve recognise it). Each fetch of the page, the model or the report re-hashes the file on disk (when its
 *     size or mtime changed): a file re-exported to the same path is a loud error in the report, never a silent stale page.
 *  4. Prints the address to open: http://127.0.0.1:8251/?model=<loopback model URL>&name=&sha256=&report=<report URL>,
 *     and records the server in glasses3d/data/arview/serve/state/port-<port>.json (pid, URL, model, stop token; written
 *     with ready:false as soon as the port listens, rewritten with ready:true once the report exists).
 *  An asynchronous failure nobody handled (a rejected promise inside a library) is logged and becomes an error in the
 *  report; it never stops the server. An uncaught exception stops it (logged, exit 1).
 *
 *  --detach builds in the foreground, then starts the server as a process that outlives the terminal (serve/detach.mjs:
 *  WMI on Windows), waits for it to listen (a line every 10 s, at most DETACH_READY_TIMEOUT_MS; a child that dies or
 *  never listens is reported with its log tail and exit 1), prints the address and returns. --stop asks the server on
 *  that port to stop (its state file's token), waits for its process to end, and kills it if it does not (only if the
 *  process is still that server). Exit codes: 0 serving / stopped; 1 failure; 2 usage error. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import {createServer, request as httpRequest} from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Mesh, Texture} from 'three';
import {DEFAULT_SERVE_PORT, DETACH_READY_TIMEOUT_MS, parseServeArgs, SERVE_BUILD_TIMEOUT_MS, SERVE_CHECK_TIMEOUT_MS, SERVE_SPEC_TIMEOUT_MS,
  SERVE_USAGE, STOP_TIMEOUT_MS, UsageError} from './serve/cli.ts';
import {ensureBuild, publicInventory} from './serve/build.mjs';
import {launchDetached} from './serve/detach.mjs';
import {assembleServeReport, SERVED_REPORT_SCHEMA} from './serve/report.ts';
import {readGlbInput} from './capture/glb-input.ts';
import {resolveExternalResources} from './capture/external-resources.ts';
import {runSpecValidator} from './capture/spec-validator.mjs';
import {isAlive, runBounded} from './capture/chromium.mjs';
import {checkModel} from '../src/model/check.ts';
import {selectQATempleCutoff} from './temple-cutoff.mjs';
import {verifyPublicAssets} from '../scripts/prepare-assets.mjs';

export const ARVIEW_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const LENSES_ROOT = path.resolve(ARVIEW_ROOT, '..', '..');
export const AR_PUBLIC = path.join(ARVIEW_ROOT, 'public');
export const SERVE_DATA = path.join(LENSES_ROOT, 'glasses3d', 'data', 'arview', 'serve');
const SCRIPT = fileURLToPath(import.meta.url);
const STATE_SCHEMA = 'arview.serve-state/1';
/** The report path's 503 body while the model is being checked: names this server (pid, instance), so a probe knows it. */
const PENDING_SCHEMA = 'arview.serve-pending/1';
/** Test plumbing: milliseconds to hold the model check back (a warning in the log and the report when set). */
export const CHECK_DELAY_ENV = 'ARVIEW_SERVE_TEST_CHECK_DELAY_MS';
const REPORT_PATH = '/__arview/report.json', STOP_PATH = '/__arview/stop';
/** Public binaries the page cannot start without (face tracker, hair segmenter, canonical face). */
const REQUIRED_PUBLIC = ['/models/face_landmarker.task', '/models/canonical-face.json', '/models/hair/hair-only.tflite',
  '/mediapipe/vision_wasm_module_internal.js', '/mediapipe/vision_wasm_module_internal.wasm'];
const CONTENT_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.wasm': 'application/wasm', '.glb': 'model/gltf-binary',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  '.ktx2': 'image/ktx2', '.bin': 'application/octet-stream', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
  '.task': 'application/octet-stream', '.tflite': 'application/octet-stream'};
export const SECURITY_HEADERS = Object.freeze({
  'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin',
  'Permissions-Policy': 'camera=(self), microphone=(), geolocation=()', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer'});

const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const messageOf = error => error instanceof Error ? error.message : String(error);
const statePath = (dir, port) => path.join(dir, `port-${port}.json`);
// Not unref'd: a wait loop (--detach, --stop) must keep the process alive; an unref'd timer let it exit 0 mid-wait.
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

class Deadline extends Error {constructor(what, ms) {super(`${what} did not finish within ${ms / 1000} s`); this.name = 'Deadline';}}
/** Race `promise` against `ms`; the timer never keeps the process alive. */
function within(promise, ms, what) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {timer = setTimeout(() => reject(new Deadline(what, ms)), ms); timer.unref?.();})])
    .finally(() => clearTimeout(timer));
}

/** Progress lines on stderr (and into --log), the address and summary on stdout (and into --log). */
function makeLogger(logFile) {
  let fd = null;
  if (logFile) {fs.mkdirSync(path.dirname(logFile), {recursive: true}); fd = fs.openSync(logFile, 'a');}
  const file = text => {if (fd !== null) try {fs.writeSync(fd, text);} catch {/* the log never stops the server */}};
  return {
    info(text) {const line = `[arview serve ${new Date().toISOString().slice(11, 19)}] ${text}\n`; try {process.stderr.write(line);} catch {/* hidden console */} file(line);},
    out(text) {try {process.stdout.write(`${text}\n`);} catch {/* hidden console */} file(`${text}\n`);},
    close() {if (fd !== null) {try {fs.closeSync(fd);} catch {/* already closed */} fd = null;}},
  };
}

async function readState(dir, port) {
  try {return JSON.parse(await fsp.readFile(statePath(dir, port), 'utf8'));}
  catch (error) {if (error.code === 'ENOENT') return null; return {unreadable: messageOf(error)};}
}

/** One loopback HTTP request with its own deadline, over a connection that is closed afterwards (node:http, agent
 *  false). Not fetch: undici keeps pooled sockets and timers alive, and a process.exit while they close tripped a libuv
 *  assertion on Windows (Node 26: "!(handle->flags & UV_HANDLE_CLOSING)", exit 0xC0000409). Resolves {status, body};
 *  rejects on a connection error or the deadline. */
function loopbackRequest(port, route, {method = 'GET', headers = {}, timeoutMs = 3000} = {}) {
  return new Promise((resolve, reject) => {
    const request = httpRequest({host: '127.0.0.1', port, path: route, method, headers: {...headers, Connection: 'close'}, agent: false}, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => {clearTimeout(timer); resolve({status: response.statusCode, body: Buffer.concat(chunks)});});
      response.on('error', error => {clearTimeout(timer); reject(error);});
    });
    const timer = setTimeout(() => request.destroy(new Error(`no answer within ${timeoutMs / 1000} s`)), timeoutMs);
    request.on('error', error => {clearTimeout(timer); reject(error);});
    request.end();
  });
}

/** Asks the server recorded in `state` for its report: {answering, arview, ready, pending, identity, report, pendingBody}.
 *  arview: the port answers as an arview serve (ready: with its report; pending: still checking the model, the 503 body
 *  naming the server). identity: it is that very server (same instance id and pid), not another program or a later
 *  server on the same port. */
async function probeServer(state) {
  try {
    const response = await loopbackRequest(state.port, REPORT_PATH);
    let body = null;
    if (response.status === 200 || response.status === 503) {try {body = JSON.parse(response.body.toString('utf8'));} catch {/* not an arview report */}}
    const ready = response.status === 200 && body?.schema === SERVED_REPORT_SCHEMA;
    const pending = response.status === 503 && body?.schema === PENDING_SCHEMA;
    return {answering: true, arview: ready || pending, ready, pending, report: ready ? body : null, pendingBody: pending ? body : null,
      identity: (ready || pending) && body?.server?.instance === state.instance && body?.server?.pid === state.pid, status: response.status};
  } catch (error) {return {answering: false, arview: false, ready: false, pending: false, identity: false, report: null, pendingBody: null, error: messageOf(error)};}
}

/** Whether `pid` is a running node process (so a stale pid now used by another program is never killed). */
async function isNodeProcess(pid) {
  if (!isAlive(pid)) return false;
  const result = process.platform === 'win32' ? await runBounded('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'], 15_000)
    : await runBounded('ps', ['-p', String(pid), '-o', 'comm='], 15_000);
  return result.status === 0 && /node/i.test(result.stdout);
}

/** Refuse a port that already has a live arview serve (its state file answers with its identity); clear a stale file. */
async function checkPortState(stateDir, port, logger) {
  if (port === 0) return null;
  const state = await readState(stateDir, port);
  if (!state) return null;
  if (state.unreadable) {logger.info(`WARNING: the state file ${statePath(stateDir, port)} is unreadable (${state.unreadable}); it is replaced`); return null;}
  const probe = await probeServer(state);
  if (probe.identity) {
    return probe.pending ? `port ${port} has an arview serve still checking ${state.model} (pid ${state.pid}). Wait for it, or stop it: node qa/serve.mjs --stop --port ${port}`
      : `port ${port} already serves ${state.model} (pid ${state.pid}, ${state.url}). Stop it first: node qa/serve.mjs --stop --port ${port}`;
  }
  logger.info(`a stale state file for port ${port} (pid ${state.pid}, ${probe.answering ? 'the port answers as another server' : 'not answering'}) is replaced`);
  await fsp.rm(statePath(stateDir, port), {force: true});
  return null;
}

async function buildOrExit(logger) {
  try {
    return await within(ensureBuild({arviewRoot: ARVIEW_ROOT, buildsDir: path.join(SERVE_DATA, 'builds'), log: text => logger.info(text)}),
      SERVE_BUILD_TIMEOUT_MS, 'the frozen page build');
  } catch (error) {
    logger.info(error instanceof Deadline ? `STALL: ${error.message}; stopping (exit 1). Run npx vite build to see why.`
      : `BUILD FAILED: ${error?.stack ?? messageOf(error)}`);
    return null;
  }
}

/** The GLTFLoader plugin of the Node-side check: every texture becomes an empty Texture, because Node cannot decode
 *  images (GLTFParser.loadImageSource reads `self.URL`, which Node lacks: the parse threw "self is not defined" and the
 *  check refused a valid file). Intercepting only the plugin chain's tail was not enough: three's built-in
 *  EXT_texture_webp, EXT_texture_avif and KHR_texture_basisu handlers are registered first, are asked first, and call
 *  parser.loadTextureImage themselves. So the parser's own image paths are replaced as well, and the tail still answers a
 *  texture none of them handles (a KTX2-only texture with no transcoder: the parser's default path would read a missing
 *  `source`). What the textures look like is the renderer's report, after Open camera. */
export function geometryOnly(parser) {
  parser.loadTextureImage = () => Promise.resolve(new Texture());
  parser.loadImageSource = () => Promise.resolve(new Texture());
  return {name: 'ArviewServeGeometryOnly', loadTexture: () => Promise.resolve(new Texture())};
}

/** The model report plus the fallbacks the renderer will report from the temples alone (the temple clip receipt's
 *  load_fallbacks, temple-geometry.ts templeLoadFallbacks: the same rules and sentences as renderer.ts installTemples and
 *  its continuity refusal), each also a warning, as ModelReportBuilder.fallback records it. Without this the served
 *  report said '0 fallback(s)' for a model whose capture lists the missing temples' fixed caps, the default clip and
 *  continuity off (WP1 proof, the temples-removed q001 copy). */
export function withTempleLoadFallbacks(report, loadFallbacks) {
  if (!report || !loadFallbacks?.length) return report;
  const fallbacks = [...report.fallbacks], warnings = [...report.warnings];
  for (const entry of loadFallbacks) {
    if (!fallbacks.some(known => known.subsystem === entry.subsystem && known.detail === entry.detail)) fallbacks.push({...entry});
    if (!warnings.includes(entry.detail)) warnings.push(entry.detail);
  }
  return {...report, fallbacks, warnings};
}

/** arview's Node-side check of the served bytes: the load front-end and normalisation pass (textures replaced by empty
 *  ones, geometryOnly; external buffers handed to the parser from `routes`, load.ts prefetchedBuffers), the temple clip
 *  receipt (with the handed-over `clipZM`, as the renderer gets it) and the renderer's temple load fallbacks. Resolves
 *  {report, templeClip, error}; never rejects: a check that throws or passes its deadline is {report: null, error}, and a
 *  temple clip receipt that fails is {report, error}. */
export async function checkInNode(bytes, {url, routes, displayName, sha, clipZM = null}) {
  const controller = new AbortController();
  const run = (async () => {
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const {report, scene} = await checkModel(buffer, {url, renderer: null, signal: controller.signal, displayName, handoverSha256: sha,
      parseTimeoutMs: SERVE_CHECK_TIMEOUT_MS - 10_000,
      plugins: [geometryOnly],
      fetchResource: async resource => {
        const requested = new URL(resource);
        if (requested.origin !== new URL(url).origin) return null;
        const route = routes.get(requested.pathname);
        return route && !route.missing ? route.bytes.buffer.slice(route.bytes.byteOffset, route.bytes.byteOffset + route.bytes.byteLength) : null;
      }});
    let templeClip = null, error = null;
    if (scene) {
      try {templeClip = selectQATempleCutoff(scene, clipZM);}
      catch (failure) {error = `the temple clip receipt failed: ${messageOf(failure)}`;}
      finally {
        scene.traverse(object => {
          if (!(object instanceof Mesh)) return;
          object.geometry.dispose();
          for (const material of Array.isArray(object.material) ? object.material : [object.material]) material.dispose();
        });
      }
    }
    return {report: withTempleLoadFallbacks(report, templeClip?.load_fallbacks), templeClip, error};
  })();
  try {return await within(run, SERVE_CHECK_TIMEOUT_MS, 'arview\'s Node-side check');}
  catch (error) {controller.abort(); return {report: null, templeClip: null, error: messageOf(error)};}
}

function send(response, status, bytes, type, headers = {}, head = false) {
  response.statusCode = status;
  response.setHeader('Content-Type', type);
  response.setHeader('Content-Length', bytes.length);
  for (const [name, value] of Object.entries(headers)) response.setHeader(name, value);
  response.end(head ? undefined : bytes);
}
const text = value => Buffer.from(`${value}\n`, 'utf8');

/** The foreground server. Resolves with the exit code when it stops. */
async function serve(options) {
  const logger = makeLogger(options.log);
  const glbPath = path.resolve(options.glb);
  let bytes;
  try {bytes = await fsp.readFile(glbPath);}
  catch (error) {logger.info(`cannot read ${glbPath} (${messageOf(error)})`); logger.close(); return 2;}
  const stateDir = path.resolve(options.stateDir ?? path.join(SERVE_DATA, 'state'));
  await fsp.mkdir(stateDir, {recursive: true});
  const busy = await checkPortState(stateDir, options.port, logger);
  if (busy) {logger.info(busy); logger.close(); return 1;}

  // Errors that arrive after the report was assembled (an unhandled asynchronous failure, the file changing on disk) are
  // joined to it whenever it is served: never dropped, never a process exit.
  const lateErrors = [];
  let diskProblem = null;
  process.on('unhandledRejection', reason => {
    logger.info(`ERROR (unhandled asynchronous failure; the server keeps running): ${reason?.stack ?? messageOf(reason)}`);
    lateErrors.push(`an asynchronous failure nobody handled, in the server process: ${messageOf(reason)}. The report may be incomplete; restart arview serve.`);
  });

  const build = await buildOrExit(logger);
  if (!build) {logger.close(); return 1;}
  for (const warning of build.warnings) logger.info(`WARNING: ${warning}`);
  const publicFiles = await publicInventory(AR_PUBLIC);
  const errors = [], serveWarnings = [];
  // The pinned public binaries (face and hair models, MediaPipe runtime equal to node_modules'): a mismatch means the page
  // would run something other than what arview was tested with.
  const assets = await verifyPublicAssets(ARVIEW_ROOT);
  for (const problem of assets.problems) if (!problem.startsWith('qa/')) errors.push(`public asset check: ${problem}`);
  for (const route of REQUIRED_PUBLIC) {
    if (!publicFiles.has(route)) errors.push(`the public binary ${route} is missing from ${AR_PUBLIC}; the page cannot start the camera pipeline without it`);
  }

  const modelSha = sha256(bytes);
  const glb = readGlbInput(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength));
  const servedName = path.basename(glbPath).replace(/[^A-Za-z0-9._-]/g, '_') || 'model.glb';
  const modelPath = `/__arview/models/${modelSha}/${servedName}`;
  const routes = new Map([[modelPath, {bytes, type: 'model/gltf-binary', model: true}]]);
  // The external resources it names: read where spec.py reads them, routed where arview's loader asks for them
  // (capture/external-resources.ts, shared with arview capture); a route that is one of this server's own paths is an
  // error (the page would hand arview the wrong bytes), never served over the page's file.
  const external = await resolveExternalResources(glbPath, glb.externalUris, {modelPath, tool: 'arview serve',
    reserved: (_route, decoded) => decoded !== null && (decoded === '/' || decoded === REPORT_PATH || decoded === STOP_PATH
      || build.files.has(decoded) || publicFiles.has(decoded))});
  for (const resource of external.resources) routes.set(resource.route, {bytes: resource.bytes, type: resource.type});
  // A resource that cannot be read is an explicit 404 at the URL arview requests, looked up before the frozen page and the
  // public files: a URI climbing out of the model's folder ('../../../index.html') would otherwise get the page's own file
  // (HTTP 200) and the renderer would load the model with bytes for a missing file (WP1 review E/F).
  for (const {uri, route} of external.missingRoutes) if (!routes.has(route)) routes.set(route, {missing: true, uri});
  serveWarnings.push(...external.warnings);
  errors.push(...external.errors);
  if (glb.externalUris.length) serveWarnings.push(`the GLB is not self-contained (${glb.externalUris.length} external resource(s)); PLAN §2 prefers one file`);

  /** Re-hash the file on disk when its size or mtime changed since the last look; a different digest (or a file that
   *  can no longer be read) is a loud error in the report, cleared if the original bytes come back. */
  let lastStat = null;
  async function checkDisk() {
    let stat;
    try {stat = await fsp.stat(glbPath);}
    catch (error) {setDiskProblem(`${glbPath} can no longer be read (${error.code ?? messageOf(error)}); this server keeps serving the bytes it read at start (sha256 ${modelSha}). Restart arview serve to judge the file on disk.`); lastStat = null; return;}
    if (lastStat && stat.size === lastStat.size && stat.mtimeMs === lastStat.mtimeMs) return;
    lastStat = stat;
    let now;
    try {now = sha256(await fsp.readFile(glbPath));}
    catch (error) {setDiskProblem(`${glbPath} can no longer be read (${error.code ?? messageOf(error)}); this server keeps serving the bytes it read at start (sha256 ${modelSha}). Restart arview serve to judge the file on disk.`); lastStat = null; return;}
    setDiskProblem(now === modelSha ? null : `the file on disk changed: ${glbPath} is now sha256 ${now}, but this page, its model and this report are of the bytes the server read at start (sha256 ${modelSha}). Restart arview serve to judge the new file.`);
  }
  function setDiskProblem(problem) {
    if (problem && problem !== diskProblem) logger.info(`ERROR: ${problem}`);
    diskProblem = problem;
  }

  const instance = crypto.randomUUID(), token = crypto.randomBytes(24).toString('hex');
  const startedAt = new Date().toISOString();
  let ready = false, report = null, stopping = null, resolveStopped = () => {};
  const stopped = new Promise(resolve => {resolveStopped = resolve;});
  const state = {stopAfter: null};
  const reportBody = () => {
    const extra = [...lateErrors, ...diskProblem ? [diskProblem] : []].filter(text => !report.errors.includes(text));
    return Buffer.from(JSON.stringify(extra.length ? {...report, errors: [...report.errors, ...extra]} : report, null, 1) + '\n', 'utf8');
  };
  let listeningPort = null;
  const server = createServer(async (request, response) => {
    for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.setHeader(name, value);
    const head = request.method === 'HEAD';
    try {
      const url = new URL(request.url ?? '/', 'http://127.0.0.1');
      const host = String(request.headers.host ?? '').toLowerCase();
      // One origin. The handover's ?model= and ?report= are absolute 127.0.0.1 URLs, and under COEP require-corp with
      // CORP same-origin (no CORS) a page opened as localhost could not fetch them ("Failed to fetch"): localhost:<port>
      // is redirected to the same path and query on 127.0.0.1 (307 keeps the method).
      if (host === `localhost:${listeningPort}`) {
        const location = `http://127.0.0.1:${listeningPort}${url.pathname}${url.search}`;
        send(response, 307, text(`307: this server's page lives at ${location}`), 'text/plain; charset=utf-8', {Location: location}, head);
        return;
      }
      // DNS rebinding: a page on another name that resolves to 127.0.0.1 must not read the report or the model.
      if (host !== `127.0.0.1:${listeningPort}`) {
        send(response, 421, text(`421: this server answers only http://127.0.0.1:${listeningPort}/ (Host was ${JSON.stringify(host.slice(0, 80))})`), 'text/plain; charset=utf-8', {}, head);
        return;
      }
      // The model and its resources are looked up by the RAW pathname (their routes keep percent escapes: '100%.png' is
      // requested as '/…/100%.png', which does not decode); every other path is compared decoded, and one that does not
      // decode is a 400 only once it is not a resource route.
      let route = null;
      try {route = decodeURIComponent(url.pathname);} catch {/* not decodable: only a resource route can match it */}
      if (request.method === 'POST' && route === STOP_PATH) {
        const given = String(request.headers['x-arview-stop-token'] ?? '');
        if (given.length !== token.length || !crypto.timingSafeEqual(Buffer.from(given), Buffer.from(token))) {
          send(response, 403, text('403: wrong or missing stop token'), 'text/plain; charset=utf-8'); return;
        }
        send(response, 202, text('stopping'), 'text/plain; charset=utf-8');
        setImmediate(() => {void shutdown('a --stop request');});
        return;
      }
      if (request.method !== 'GET' && !head) {send(response, 405, text('405: only GET and HEAD'), 'text/plain; charset=utf-8', {Allow: 'GET, HEAD'}); return;}
      if (!ready) {
        const pending = route === REPORT_PATH
          ? [Buffer.from(JSON.stringify({schema: PENDING_SCHEMA, ready: false, detail: 'arview serve is still checking the model', model: {path: glbPath, sha256: modelSha},
            server: {pid: process.pid, instance, port: listeningPort, started_at: startedAt}}, null, 1) + '\n'), CONTENT_TYPES['.json']]
          : [text(`503: arview serve (pid ${process.pid}) is still checking the model; reload in a few seconds`), 'text/plain; charset=utf-8'];
        send(response, 503, pending[0], pending[1], {'Retry-After': '5'}, head);
        return;
      }
      if (route === REPORT_PATH) {
        await checkDisk();
        send(response, 200, reportBody(), CONTENT_TYPES['.json'], {}, head);
        logger.info(`report fetched (${request.headers['user-agent']?.slice(0, 40) ?? 'no user agent'})`);
        return;
      }
      const modelRoute = routes.get(url.pathname);
      if (modelRoute?.missing) {
        send(response, 404, text(`404: ${url.pathname} is the external resource "${modelRoute.uri}" the model references, which cannot be read (missing)`),
          'text/plain; charset=utf-8', {}, head);
        return;
      }
      if (modelRoute) {
        if (modelRoute.model) await checkDisk();
        const digest = sha256(modelRoute.bytes);
        send(response, 200, modelRoute.bytes, modelRoute.type, {'X-Arview-Sha256': digest, ETag: `"${digest}"`,
          'Repr-Digest': `sha-256=:${Buffer.from(digest, 'hex').toString('base64')}:`}, head);
        if (modelRoute.model) logger.info(`model fetched (${modelRoute.bytes.length} bytes)`);
        return;
      }
      if (route === null && !url.pathname.startsWith('/__arview/')) {send(response, 400, text('400: malformed path (a percent escape that does not decode)'), 'text/plain; charset=utf-8', {}, head); return;}
      if (route === null || route.startsWith('/__arview/')) {
        // A resource the GLB names but that is not served (missing) is MISSING to arview: 404, as on a file server.
        send(response, 404, text(`404: ${route ?? url.pathname} is not served (only the model, the readable external resources it names, and the report)`),
          'text/plain; charset=utf-8', {}, head);
        return;
      }
      if (route === '/' || route === '/index.html') await checkDisk();
      const item = build.files.get(route === '/' ? '/index.html' : route) ?? publicFiles.get(route);
      if (!item) {send(response, 404, text(`404: ${route} is not part of the frozen page`), 'text/plain; charset=utf-8', {}, head); return;}
      const data = await fsp.readFile(item.path);
      if (sha256(data) !== item.sha256) {
        logger.info(`ERROR: ${item.path} changed after the server pinned it (sha256); answered 500. Restart the server.`);
        send(response, 500, text(`500: ${route} changed after the server started (frozen file pinned by sha256); restart arview serve`), 'text/plain; charset=utf-8', {}, head);
        return;
      }
      send(response, 200, data, CONTENT_TYPES[path.extname(item.path).toLowerCase()] ?? 'application/octet-stream', {}, head);
    } catch (error) {
      logger.info(`ERROR answering ${request.method} ${request.url}: ${messageOf(error)}`);
      if (!response.headersSent) send(response, 500, text(`500: ${messageOf(error)}`), 'text/plain; charset=utf-8');
      else response.destroy();
    }
  });

  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(options.port, '127.0.0.1', () => {server.off('error', reject); resolve();});
    });
  } catch (error) {
    logger.info(error.code === 'EADDRINUSE' ? `port ${options.port} on 127.0.0.1 is in use by another program (npm run preview also uses 8251); `
      + 'stop it or choose another port with --port N' : `cannot listen on 127.0.0.1:${options.port} (${messageOf(error)})`);
    logger.close();
    return 1;
  }
  server.on('error', error => logger.info(`ERROR: server: ${messageOf(error)}`));
  const port = server.address().port, origin = `http://127.0.0.1:${port}`;
  listeningPort = port;
  const modelUrl = origin + modelPath, reportUrl = origin + REPORT_PATH;
  const displayName = options.name ?? path.basename(glbPath);
  const query = new URLSearchParams({model: modelUrl, name: displayName, sha256: modelSha, report: reportUrl});
  if (options.clipZM !== null) query.set('clip', String(options.clipZM));
  if (options.lensEnvIntensity !== null) query.set('lensenv', String(options.lensEnvIntensity));
  const pageUrl = `${origin}/?${query}`;
  const ownState = statePath(stateDir, port);
  const writeState = isReady => fsp.writeFile(ownState, JSON.stringify({schema: STATE_SCHEMA, ready: isReady, pid: process.pid, instance, port, origin, url: pageUrl,
    report_url: reportUrl, model: glbPath, sha256: modelSha, started_at: startedAt, launch_id: options.launchId, log: options.log, token}, null, 1) + '\n');

  async function removeOwnState() {
    try {
      const recorded = JSON.parse(await fsp.readFile(ownState, 'utf8'));
      if (recorded.instance === instance) await fsp.rm(ownState, {force: true});
    } catch {/* already gone, or another server's */}
  }
  function shutdown(reason) {
    if (stopping) return stopping;
    logger.info(`stopping (${reason})`);
    stopping = (async () => {
      if (state.stopAfter) clearTimeout(state.stopAfter);
      await removeOwnState();
      await Promise.race([new Promise(resolve => {server.close(() => resolve()); server.closeAllConnections?.();}), sleep(3000)]);
      logger.info('stopped');
      logger.close();
      resolveStopped(0);
      return 0;
    })();
    return stopping;
  }
  const onSignal = signal => {void shutdown(signal).then(code => process.exit(code));};
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGBREAK', 'SIGHUP']) process.on(signal, onSignal);
  process.on('uncaughtException', error => {
    logger.info(`ERROR (uncaught): ${error?.stack ?? messageOf(error)}`);
    void shutdown('an uncaught error').then(() => process.exit(1));
  });
  // The state file exists while the model is checked (ready:false): --status, --stop and a second serve on this port
  // recognise this server (its 503 report body names it), and --stop works during the check.
  try {await writeState(false);}
  catch (error) {logger.info(`ERROR: cannot write the state file ${ownState} (${messageOf(error)}); stopping`); await shutdown('no state file'); return 1;}
  if (options.ttlS !== null) {
    state.stopAfter = setTimeout(() => {void shutdown(`--ttl-s ${options.ttlS} elapsed`).then(code => process.exit(code));}, options.ttlS * 1000);
  }

  logger.info(`checking ${glbPath} (sha256 ${modelSha.slice(0, 16)}…): spec.py and arview's Node-side check, ${SERVE_SPEC_TIMEOUT_MS / 1000} s each at most`);
  // Test plumbing (qa/serve.test.mjs): hold the check back so the "still checking" window can be exercised. Never silent.
  const delayMs = Math.min(Math.max(Number(process.env[CHECK_DELAY_ENV] ?? 0) || 0, 0), 120_000);
  if (delayMs > 0) {
    logger.info(`WARNING: ${CHECK_DELAY_ENV}=${delayMs}: the model check starts ${delayMs / 1000} s late (test plumbing)`);
    serveWarnings.push(`${CHECK_DELAY_ENV}=${delayMs} held the model check back by ${delayMs / 1000} s (test plumbing; unset it for real use)`);
  }
  const checks = (delayMs > 0 ? sleep(delayMs) : Promise.resolve()).then(() => Promise.all([runSpecValidator(glbPath, SERVE_SPEC_TIMEOUT_MS),
    checkInNode(bytes, {url: modelUrl, routes, displayName, sha: modelSha, clipZM: options.clipZM})]));
  // A stop during the check (--stop, a signal, --ttl-s) ends the server now, not when the check finishes.
  const outcome = await Promise.race([checks, stopped.then(() => null)]);
  if (outcome === null || stopping) return stopped;
  const [spec, checked] = outcome;
  // The file must still be the bytes being served (spec.py read it from disk on its own).
  const onDisk = await fsp.readFile(glbPath).then(sha256, error => `unreadable (${error.code ?? messageOf(error)})`);
  if (onDisk !== modelSha) errors.push(`${glbPath} changed while the server started (now sha256 ${onDisk}); the server keeps serving the bytes it read (${modelSha}). Restart it to serve the new file.`);
  report = assembleServeReport({createdAt: new Date().toISOString(),
    model: {path: glbPath, file: path.basename(glbPath), bytes: bytes.length, sha256: modelSha, url: modelUrl, selfContained: glb.externalUris.length === 0,
      externalUris: glb.externalUris, servedResources: external.served, unreadableResources: external.unreadable,
      unservableResources: external.unservable, outsideResources: external.outside,
      containerError: glb.containerError},
    pageUrl, reportUrl, handover: {name: displayName, clip: options.clipZM, lensenv: options.lensEnvIntensity},
    spec, arview: checked.report, arviewError: checked.report ? null : checked.error, templeClip: checked.templeClip,
    nodeCheckWarnings: checked.error && checked.report ? [checked.error] : [], serveWarnings, serveErrors: errors,
    server: {pid: process.pid, instance, origin, port, started_at: startedAt, node: process.version, detached: options.launchId !== null,
      log: options.log, ttl_s: options.ttlS},
    build: {dir: build.dir, source_digest: build.digest, built_now: build.builtNow, built_at: build.stamp?.built_at ?? null, files: build.files.size},
    publicFiles: {dir: AR_PUBLIC, files: publicFiles.size, sha256: sha256(Buffer.from([...publicFiles].map(([route, item]) => `${route}\0${item.sha256}`).join('\n')))}});
  ready = true;
  try {await writeState(true);}
  catch (error) {logger.info(`ERROR: cannot rewrite the state file ${ownState} (${messageOf(error)}); stopping`); await shutdown('no state file'); return 1;}

  const failed = report.failed_rows.map(row => row.row);
  const shown = JSON.parse(reportBody().toString('utf8'));
  logger.out(`arview serve: ${glbPath}`);
  logger.out(`  sha256 ${modelSha} (${bytes.length} bytes)`);
  logger.out(`  spec.py: ${spec.available ? (spec.report.ok ? 'PASS' : `FAIL (${failed.join(', ') || 'see the report'})`) : `UNAVAILABLE: ${spec.reason}`}`);
  logger.out(`  arview: ${shown.status}; ${shown.warnings.length} warning(s), ${shown.fallbacks.length} fallback(s), ${shown.errors.length} error(s)`);
  for (const error of shown.errors) logger.out(`  ERROR: ${error}`);
  logger.out(`  report: ${reportUrl}`);
  logger.out(`  build: ${build.dir} (${build.builtNow ? 'built now' : 'reused'})`);
  logger.out('Open this address in Chrome or Edge on this computer:');
  logger.out(pageUrl);
  logger.out(options.launchId ? `Stop: node qa/serve.mjs --stop --port ${port}` : `Stop: Ctrl+C here, or node qa/serve.mjs --stop --port ${port}`);
  return stopped;
}

/** --detach: build in the foreground, start the server detached, wait until it listens, print its address. */
async function detach(options) {
  const logger = makeLogger(null);
  const glbPath = path.resolve(options.glb);
  try {await fsp.access(glbPath, fs.constants.R_OK);}
  catch (error) {logger.info(`cannot read ${glbPath} (${messageOf(error)})`); return 2;}
  const stateDir = path.resolve(options.stateDir ?? path.join(SERVE_DATA, 'state'));
  await fsp.mkdir(stateDir, {recursive: true});
  const busy = await checkPortState(stateDir, options.port, logger);
  if (busy) {logger.info(busy); return 1;}
  if (!await buildOrExit(logger)) return 1;
  const launchId = crypto.randomUUID();
  // Logs live beside the state directory (default glasses3d/data/arview/serve/logs).
  const logFile = path.join(path.dirname(stateDir), 'logs', `serve-${options.port || 'any'}-${new Date().toISOString().replace(/[:.]/g, '-')}.log`);
  const args = [SCRIPT, glbPath, '--port', String(options.port), '--log', logFile, '--launch-id', launchId, '--state-dir', stateDir];
  if (options.name !== null) args.push('--name', options.name);
  if (options.clipZM !== null) args.push('--clip', String(options.clipZM));
  if (options.lensEnvIntensity !== null) args.push('--lensenv', String(options.lensEnvIntensity));
  if (options.ttlS !== null) args.push('--ttl-s', String(options.ttlS));
  let launched;
  try {launched = await launchDetached(process.execPath, args, ARVIEW_ROOT);}
  catch (error) {logger.info(`could not start the detached server: ${messageOf(error)}`); return 1;}
  if (launched.warning) logger.info(`WARNING: ${launched.warning}`);
  logger.info(`started the detached server (pid ${launched.pid}, ${launched.method}); log ${logFile}`);
  const started = Date.now();
  let lastLine = started;
  const tail = async () => {
    try {return (await fsp.readFile(logFile, 'utf8')).split(/\r?\n/).slice(-25).join('\n');}
    catch (error) {return `(no log: ${error.code ?? messageOf(error)})`;}
  };
  while (Date.now() - started < DETACH_READY_TIMEOUT_MS) {
    for (const name of await fsp.readdir(stateDir).catch(() => [])) {
      if (!/^port-\d+\.json$/.test(name)) continue;
      let state;
      try {state = JSON.parse(await fsp.readFile(path.join(stateDir, name), 'utf8'));} catch {continue;}
      if (state.launch_id !== launchId) continue;
      const probe = await probeServer(state);
      // Its own state file exists from the moment it listens (ready:false while it checks the model): wait for the report.
      if (!probe.identity || !probe.ready) continue;
      const report = probe.report;
      logger.out(`arview serve (detached, pid ${state.pid}): ${glbPath}`);
      logger.out(`  sha256 ${state.sha256}`);
      logger.out(`  spec.py: ${report.spec?.available ? (report.spec.ok ? 'PASS' : `FAIL (${report.failed_rows.map(row => row.row).join(', ')})`) : `UNAVAILABLE: ${report.spec?.reason}`}`);
      logger.out(`  arview: ${report.status}; ${report.warnings.length} warning(s), ${report.fallbacks.length} fallback(s), ${report.errors.length} error(s)`);
      for (const error of report.errors) logger.out(`  ERROR: ${error}`);
      logger.out(`  report: ${state.report_url}`);
      logger.out(`  log: ${logFile}`);
      logger.out('Open this address in Chrome or Edge on this computer:');
      logger.out(state.url);
      logger.out(`Stop: node qa/serve.mjs --stop --port ${state.port}`);
      return 0;
    }
    if (!isAlive(launched.pid)) {
      logger.info(`the detached server (pid ${launched.pid}) exited before it was ready. Its log:\n${await tail()}`);
      return 1;
    }
    if (Date.now() - lastLine >= 10_000) {lastLine = Date.now(); logger.info(`  waiting for the detached server to check the model and listen (${Math.round((Date.now() - started) / 1000)} s)`);}
    await sleep(250);
  }
  try {process.kill(launched.pid);} catch {/* already gone */}
  logger.info(`STALL: the detached server (pid ${launched.pid}) did not become ready within ${DETACH_READY_TIMEOUT_MS / 1000} s; it was killed. Its log:\n${await tail()}`);
  return 1;
}

/** --stop / --status. */
async function control(options) {
  const logger = makeLogger(null);
  const stateDir = path.resolve(options.stateDir ?? path.join(SERVE_DATA, 'state'));
  const state = await readState(stateDir, options.port);
  if (!state || state.unreadable) {
    const stranger = await probeServer({port: options.port, instance: null, pid: null});
    const body = stranger.report ?? stranger.pendingBody;
    const what = stranger.arview ? `port ${options.port} answers as arview serve (pid ${body.server?.pid}, ${body.model?.path}${stranger.pending ? ', still checking the model' : ''}) `
      + `but has no state file in ${stateDir}; stop it with Ctrl+C in its terminal (or end that pid)`
      : stranger.answering ? `port ${options.port} is used by another program, not an arview serve` : `nothing is serving on port ${options.port}`;
    if (options.mode === 'status') {
      logger.out(JSON.stringify({serving: stranger.ready, checking: stranger.pending, port: options.port, detail: what}, null, 1));
      return stranger.arview ? 0 : 1;
    }
    logger.info(what);
    return stranger.answering ? 1 : 0;
  }
  const probe = await probeServer(state);
  if (options.mode === 'status') {
    // Exit 0 while that arview serve owns the port, serving or still checking the model.
    logger.out(JSON.stringify({serving: probe.identity && probe.ready, checking: probe.identity && probe.pending, port: state.port, pid: state.pid, url: state.url,
      model: state.model, sha256: state.sha256, started_at: state.started_at, log: state.log, status: probe.report?.status ?? null,
      spec_ok: probe.report?.verdict?.spec_ok ?? null, failed_rows: probe.report?.failed_rows?.map(row => row.row) ?? null,
      warnings: probe.report?.warnings?.length ?? null, errors: probe.report?.errors?.length ?? null,
      detail: probe.identity ? (probe.ready ? 'serving' : 'still checking the model') : probe.answering ? 'the port answers as another server (stale state file)'
        : 'not answering (stale state file)'}, null, 1));
    return probe.identity ? 0 : 1;
  }
  if (probe.identity && probe.pending) logger.info(`the server on port ${state.port} (pid ${state.pid}) is still checking the model; asking it to stop`);
  if (probe.identity) {
    try {
      const response = await loopbackRequest(state.port, STOP_PATH, {method: 'POST', headers: {'X-Arview-Stop-Token': state.token}});
      if (response.status !== 202) logger.info(`the server answered ${response.status} to the stop request`);
    } catch (error) {logger.info(`the stop request failed (${messageOf(error)})`);}
    const asked = Date.now();
    while (Date.now() - asked < STOP_TIMEOUT_MS && isAlive(state.pid)) await sleep(200);
    if (!isAlive(state.pid)) {
      await fsp.rm(statePath(stateDir, options.port), {force: true});
      logger.out(`stopped arview serve on port ${state.port} (pid ${state.pid}, ${state.model})`);
      return 0;
    }
    logger.info(`the server (pid ${state.pid}) did not exit within ${STOP_TIMEOUT_MS / 1000} s of the stop request; killing it`);
  }
  // A kill needs proof that the process is that server (it answered with its instance id just now): a pid recorded in
  // a stale state file may belong to another program by now, and is never killed on the file's word alone.
  if (probe.identity && await isNodeProcess(state.pid)) {
    try {process.kill(state.pid);} catch (error) {logger.info(`could not kill pid ${state.pid}: ${messageOf(error)}`); return 1;}
    const killed = Date.now();
    while (Date.now() - killed < 5000 && isAlive(state.pid)) await sleep(200);
    if (isAlive(state.pid)) {logger.info(`pid ${state.pid} is still running after the kill`); return 1;}
    await fsp.rm(statePath(stateDir, options.port), {force: true});
    logger.out(`killed arview serve on port ${state.port} (pid ${state.pid}): it did not stop when asked`);
    return 0;
  }
  if (!probe.answering && await isNodeProcess(state.pid)) {
    logger.info(`the state file names pid ${state.pid}, a running node process, but nothing answers on port ${options.port}; it is not killed `
      + `because it cannot be shown to be that server. If it is (a hung server), end it yourself (Windows: taskkill /PID ${state.pid}); `
      + `the state file ${statePath(stateDir, options.port)} is kept.`);
    return 1;
  }
  await fsp.rm(statePath(stateDir, options.port), {force: true});
  logger.out(`arview serve on port ${options.port} was not running (pid ${state.pid} ${isAlive(state.pid) ? 'is another program now' : 'is gone'}); removed its stale state file`);
  return 0;
}

/** Run one command. Resolves with the exit code (a foreground server resolves when it stops). */
export async function main(argv) {
  if (argv.includes('--help') || argv.includes('-h')) {console.log(SERVE_USAGE); return 0;}
  let options;
  try {options = parseServeArgs(argv);}
  catch (error) {
    if (error instanceof UsageError) {console.error(`arview serve: ${error.message}\n${SERVE_USAGE}`); return 2;}
    throw error;
  }
  if (options.mode !== 'serve') return control(options);
  return options.detach ? detach(options) : serve(options);
}

if (process.argv[1] && path.resolve(process.argv[1]) === SCRIPT) {
  main(process.argv.slice(2)).then(code => process.exit(code), error => {console.error(error?.stack ?? error); process.exit(1);});
}
export {DEFAULT_SERVE_PORT};
