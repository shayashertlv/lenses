/** Headless Chromium for `arview capture`, with a process handle the capture can kill (owner rule: no silent stalls).
 *
 *  A Playwright Browser from chromium.launch() exposes no process, so a stall could not kill it: its processes lived
 *  until Node exited. The capture therefore launches a BrowserServer (chromium.launchServer, same options), whose
 *  process() is the Chromium root, and drives it through chromium.connect() over the server's local WebSocket. On a
 *  stall the whole tree (browser, GPU, renderers, utilities) is killed at once (Windows: taskkill /T /F; elsewhere the
 *  root is SIGKILLed and its children exit with their parent's IPC), and the outcome is reported as it happened:
 *  which pid, what the kill command said, and whether the root process is confirmed gone. Nothing here waits on the
 *  browser without a deadline. */
import {spawn} from 'node:child_process';

const messageOf = error => error instanceof Error ? error.message : String(error);

/** True while `pid` names a running process (signal 0 is a liveness probe, it delivers nothing). */
export function isAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {process.kill(pid, 0); return true;}
  catch (error) {return error?.code === 'EPERM';}
}

/** Resolve true once `child` has exited, false when `ms` pass first. */
export function waitForExit(child, ms) {
  if (!child) return Promise.resolve(true);
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
  return new Promise(resolve => {
    const onExit = () => {clearTimeout(timer); resolve(true);};
    const timer = setTimeout(() => {child.off('exit', onExit); resolve(child.exitCode !== null || child.signalCode !== null);}, ms);
    timer.unref?.();
    child.once('exit', onExit);
  });
}

/** Run a command asynchronously (never blocks the event loop, so the capture's other deadlines keep firing), killed
 *  after `ms`. Resolves {status, stdout, stderr, timedOut}; never rejects. */
export function runBounded(command, args, ms, options = {}) {
  return new Promise(resolve => {
    let stdout = '', stderr = '', done = false, child;
    const finish = value => {if (!done) {done = true; clearTimeout(timer); resolve({stdout: stdout.trim(), stderr: stderr.trim(), ...value});}};
    const timer = setTimeout(() => {try {child?.kill();} catch {/* already gone */} finish({status: null, timedOut: true});}, ms);
    timer.unref?.();
    try {child = spawn(command, args, {windowsHide: true, ...options});}
    catch (error) {finish({status: null, timedOut: false, error: messageOf(error)}); return;}
    child.stdout?.on('data', chunk => {stdout += chunk;});
    child.stderr?.on('data', chunk => {stderr += chunk;});
    child.on('error', error => finish({status: null, timedOut: false, error: messageOf(error)}));
    child.on('close', status => finish({status, timedOut: false}));
  });
}

/** Kill a process and all its descendants, then confirm the root is gone. Never throws; at most ~`budgetMs`. */
export async function killProcessTree(child, budgetMs = 10_000) {
  const pid = child?.pid;
  if (!pid) return {pid: null, method: null, gone: null, text: 'no Chromium process handle (nothing was launched)'};
  if (child.exitCode !== null || child.signalCode !== null) {
    return {pid, method: null, gone: true, text: `Chromium pid ${pid} had already exited (code ${child.exitCode}, signal ${child.signalCode})`};
  }
  const started = Date.now();
  let method, detail;
  if (process.platform === 'win32') {
    const result = await runBounded('taskkill', ['/PID', String(pid), '/T', '/F'], Math.max(1000, budgetMs - 3000));
    // One line per terminated process ("... PID 123 (child process of PID 45) ..."): count lines, not PID mentions.
    const terminated = result.stdout.split(/\r?\n/).filter(line => /\bPID \d+/.test(line)).length;
    method = 'taskkill /T /F';
    detail = result.timedOut ? 'taskkill timed out' : `taskkill exit ${result.status}${result.error ? ` (${result.error})` : ''}, ${terminated} process(es) reported terminated`;
  } else {
    method = 'SIGKILL';
    try {process.kill(pid, 'SIGKILL'); detail = 'SIGKILL sent';} catch (error) {detail = `SIGKILL failed: ${messageOf(error)}`;}
  }
  const exited = await waitForExit(child, Math.max(500, budgetMs - (Date.now() - started)));
  const gone = exited || !isAlive(pid);
  return {pid, method, gone, ms: Date.now() - started,
    text: gone ? `Chromium pid ${pid} and its child processes were killed (${method}: ${detail}); pid ${pid} is confirmed gone`
      : `Chromium pid ${pid} could NOT be confirmed killed (${method}: ${detail}); it may still be running`};
}

/** Attach what happened to the Chromium process to a launch error (`error.chromiumProcess`, and the server handle as
 *  `error.chromiumServer` when there is one), so the caller's report says it instead of "nothing was launched". */
function withProcessRecord(error, record, server = null) {
  const target = error instanceof Error ? error : new Error(messageOf(error));
  target.chromiumProcess = record;
  if (server) target.chromiumServer = server;
  return target;
}

/** Launch headless Chromium as a BrowserServer and connect to it. `deadline(promise, ms, label)` is the capture's stall
 *  guard. Returns {server, browser, pid}. On a failure the thrown error carries `chromiumProcess`, the record of what
 *  happened to the process: a launch that did not return has no handle (a late one is killed the moment it completes);
 *  a launched server whose connect failed or stalled is killed here with the short `killMs` budget (the caller's stall
 *  budget counts it) and `chromiumServer` is attached so the caller can retry a kill that could not be confirmed.
 *  `stallConnect` (test hook) replaces the connect with a promise that never settles. */
export async function launchChromium(chromium, {args, channel = 'chromium', launchMs, deadline, killMs = 3000, stallConnect = false}) {
  const launching = chromium.launchServer({headless: true, channel, args, timeout: launchMs});
  let server;
  try {server = await deadline(launching, launchMs, 'chromium: launch');}
  catch (error) {
    // A launch that completes after its deadline is killed the moment it does, never left running.
    launching.then(late => killProcessTree(late.process(), killMs), () => {});
    throw withProcessRecord(error, {pid: null, method: null, gone: null, ended: 'launch did not complete',
      text: `the Chromium launch did not return a process handle (${messageOf(error).split('\n')[0]}); a launch that completes later is killed the moment it does`});
  }
  try {
    const connecting = stallConnect ? new Promise(() => {}) : chromium.connect(server.wsEndpoint(), {timeout: launchMs});
    const browser = await deadline(connecting, launchMs, 'chromium: connect');
    return {server, browser, pid: server.process()?.pid ?? null};
  } catch (error) {
    const killed = await killProcessTree(server.process(), killMs);
    throw withProcessRecord(error, {...killed, ended: 'killed after the connect failed'}, server);
  }
}

/** The success path: disconnect, close Chromium gracefully, and kill the tree if it is still alive after `ms`. */
export async function closeChromium(handle, ms = 10_000) {
  if (!handle?.server) return {pid: null, ended: 'never launched', gone: null, text: 'Chromium was never launched'};
  const child = handle.server.process(), pid = child?.pid ?? null;
  const bounded = (promise, limit) => Promise.race([Promise.resolve(promise).then(() => true, () => false),
    new Promise(resolve => {const timer = setTimeout(() => resolve(false), limit); timer.unref?.();})]);
  await bounded(handle.browser?.close(), Math.min(5000, ms));
  const closed = await bounded(handle.server.close(), ms) && await waitForExit(child, 2000);
  if (closed || !isAlive(pid)) return {pid, ended: 'closed', gone: true, text: `Chromium pid ${pid} closed normally`};
  const killed = await killProcessTree(child);
  return {...killed, ended: 'killed after a close timeout', text: `Chromium did not close within ${ms / 1000} s; ${killed.text}`};
}
