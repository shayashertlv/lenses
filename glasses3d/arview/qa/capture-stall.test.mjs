/** arview capture: a stalled step is killed and reported (owner rule: no silent stalls). Runs the REAL orchestrator
 *  IN THIS PROCESS (main() of qa/capture.mjs, so nothing is cleaned up by a process exit) with its test hook
 *  ARVIEW_CAPTURE_TEST_STALL=<step label> (that step never settles), on three stall paths:
 *  - 'gpu': a page step (5 s step deadline);
 *  - 'chromium: connect': Chromium is launched but the connect never settles (deadline 10 s = step + 5 s); launchChromium
 *    kills what it launched itself, and the report must say so (not "nothing was launched");
 *  - 'live probe: load on main's ar/': the --live-probe's evaluate on main's unmodified ar/ (15 s step deadline); the probe
 *    must skip its graceful closes so the abort runs at once.
 *  When main() returns: exit code 1 within seconds of the deadline, a partial report.json naming the stalled step, the
 *  Chromium root pid recorded and confirmed gone, and NO Chromium process left whose parent is this process (the
 *  capture) or that root (its GPU/renderer/utility children), checked against the OS process table. launchChromium's
 *  own failure records are unit-tested on fakes and a real child process. Needs headless Chromium (npx playwright
 *  install chromium); every run stalls before any model is rendered. */
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {main, withTimeout} from './capture.mjs';
import {isAlive, launchChromium} from './capture/chromium.mjs';

const CHROMIUM_NAME = /chrome|chromium|headless_shell/i;
const STILL_LISTED_MS = 30_000;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Live processes whose parent is one of `parents`: [{pid, ppid, name}] from the OS process table. */
function childrenOf(parents) {
  if (process.platform === 'win32') {
    const filter = parents.map(pid => `ParentProcessId=${pid}`).join(' OR ');
    const run = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      `Get-CimInstance Win32_Process -Filter "${filter}" | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress`],
    {encoding: 'utf8', timeout: 30_000, windowsHide: true});
    assert.equal(run.status, 0, `process table query failed: ${run.stderr}`);
    const text = run.stdout.trim();
    if (!text) return [];
    const parsed = JSON.parse(text);
    // `type`: Chromium's --type= of the process (gpu-process, renderer, utility, crashpad-handler; null for the browser).
    return (Array.isArray(parsed) ? parsed : [parsed]).map(entry => ({pid: entry.ProcessId, ppid: entry.ParentProcessId, name: entry.Name,
      type: /--type=([\w-]+)/.exec(entry.CommandLine ?? '')?.[1] ?? null}));
  }
  const run = spawnSync('ps', ['-A', '-o', 'pid=,ppid=,comm='], {encoding: 'utf8', timeout: 30_000});
  assert.equal(run.status, 0, `ps failed: ${run.stderr}`);
  return run.stdout.split('\n').map(line => line.trim().match(/^(\d+)\s+(\d+)\s+(.+)$/)).filter(Boolean)
    .map(match => ({pid: Number(match[1]), ppid: Number(match[2]), name: match[3], type: null})).filter(entry => parents.includes(entry.ppid));
}

/** Run main() with the stall hook at `label`; returns {code, seconds, report}. */
async function stalledRun(label, stepTimeoutS, extra = []) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'arview-stall-'));
  const previous = process.env.ARVIEW_CAPTURE_TEST_STALL;
  process.env.ARVIEW_CAPTURE_TEST_STALL = label;
  try {
    const glb = path.join(work, 'not-a-model.glb'); fs.writeFileSync(glb, Buffer.from('not a glb'));
    const out = path.join(work, 'out');
    const started = Date.now();
    const code = await main([glb, '--poses', 'default', '--out', out, '--no-portrait', '--step-timeout-s', String(stepTimeoutS), ...extra]);
    const seconds = (Date.now() - started) / 1000;
    assert.equal(fs.existsSync(path.join(out, 'report.json.tmp')), false);
    return {code, seconds, report: JSON.parse(fs.readFileSync(path.join(out, 'report.json'), 'utf8'))};
  } finally {
    if (previous === undefined) delete process.env.ARVIEW_CAPTURE_TEST_STALL; else process.env.ARVIEW_CAPTURE_TEST_STALL = previous;
    fs.rmSync(work, {recursive: true, force: true});
  }
}

/** The common stall verdict: exit 1, a partial report naming the step, the Chromium tree killed and confirmed gone. */
async function assertStallReport({code, report}, label, deadlineS, ended) {
  assert.equal(code, 1);
  assert.equal(report.status, 'infrastructure_failed'); assert.equal(report.exit_code, 1);
  assert.match(report.infrastructure_failure, new RegExp(`^STALL: step "${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}" did not finish within ${deadlineS} s`));
  assert.ok(report.warnings.some(text => /TEST HOOK/.test(text)));
  assert.equal(report.request.test_stall_step, label);
  assert.deepEqual(report.images, []);
  // The kill is reported from what happened, and what happened is checked against the OS.
  const chromiumProcess = report.chromium_process;
  assert.ok(Number.isInteger(chromiumProcess?.pid) && chromiumProcess.pid > 0, JSON.stringify(chromiumProcess));
  assert.equal(report.tool.chromium_pid, chromiumProcess.pid);
  assert.equal(chromiumProcess.gone, true, chromiumProcess.text);
  assert.equal(chromiumProcess.ended, ended);
  assert.ok(report.warnings.some(text => text.includes(`Chromium pid ${chromiumProcess.pid} and its child processes were killed`)
    && /confirmed gone/.test(text)), report.warnings.join('\n'));
  assert.equal(isAlive(chromiumProcess.pid), false, `Chromium root pid ${chromiumProcess.pid} is still alive after main() returned`);
  // A process the tree kill terminated can stay in the OS process table while it exits. Under the full qa suite (files
  // in parallel, several Chromiums on one GPU), 3 of 14 runs on 2026-10-05 listed Chromium processes right after main()
  // returned (twice the killed root's whole tree; once a child of the root still listed 5 s later, gone a minute or two
  // after); the other 11 listed none. A LEFT process is one still listed STILL_LISTED_MS later (a real leak fails);
  // every sighting is logged with its Chromium type and parent, never silent.
  const listed = () => childrenOf([process.pid, chromiumProcess.pid]).filter(entry => CHROMIUM_NAME.test(entry.name));
  let left = listed();
  const first = left, started = Date.now();
  while (left.length && Date.now() - started < STILL_LISTED_MS) {await sleep(250); left = listed();}
  if (first.length) {
    const whose = entry => entry.ppid === chromiumProcess.pid ? 'a child of the killed root' : 'a child of this process';
    console.log(`[stall test] ${first.length} Chromium process(es) listed right after main() returned (${first.map(entry => `pid ${entry.pid} ${entry.type ?? 'browser'}, ${whose(entry)}`).join('; ')}); `
      + `${left.length ? `still listed after ${STILL_LISTED_MS / 1000} s` : `gone after ${Date.now() - started} ms`}`);
  }
  assert.deepEqual(left, [], `Chromium processes still running ${STILL_LISTED_MS / 1000} s after main() returned: ${JSON.stringify(left)}`);
}

test('a stalled page step: Chromium tree killed and confirmed gone, partial report, exit 1, well inside the 2-minute rule', {timeout: 90_000}, async () => {
  const run = await stalledRun('gpu', 5);
  assert.ok(run.seconds < 45, `took ${run.seconds} s`);
  await assertStallReport(run, 'gpu', 5, 'killed after the failure');
  assert.ok(run.report.timings.steps.some(entry => entry.step === 'gpu' && entry.failed));
});

test('a stalled connect: the launched Chromium is killed and REPORTED (pid, kill outcome), never "nothing was launched"', {timeout: 90_000}, async () => {
  const run = await stalledRun('chromium: connect', 5);
  // Deadline 10 s (step + 5 s) + the inner kill (3 s budget) + the abort.
  assert.ok(run.seconds < 45, `took ${run.seconds} s`);
  await assertStallReport(run, 'chromium: connect', 10, 'killed after the connect failed');
  assert.notEqual(run.report.chromium_process.text, 'no Chromium process handle (nothing was launched)');
  assert.ok(run.report.chromium_process.ms <= 3500, `inner kill took ${run.report.chromium_process.ms} ms (budget 3000)`);
  assert.ok(run.report.timings.steps.some(entry => entry.step === 'chromium: launch' && entry.failed));
});

test('a stalled --live-probe step: no graceful close first; the tree is killed and the report written at once', {timeout: 120_000}, async () => {
  const label = 'live probe: load on main\'s ar/';
  const run = await stalledRun(label, 15, ['--live-probe']);
  await assertStallReport(run, label, 15, 'killed after the failure');
  const probeStep = run.report.timings.steps.find(entry => entry.step === 'live probe: main ar/');
  assert.ok(probeStep?.failed, JSON.stringify(run.report.timings.steps));
  // The probe handed its server to the abort instead of closing gracefully, and the abort stayed inside its budget.
  assert.ok(run.report.warnings.some(text => /stalled --live-probe skipped its graceful close/.test(text)), run.report.warnings.join('\n'));
  const cleanup = run.report.timings.steps.find(entry => entry.step === 'abort: cleanup');
  assert.ok(cleanup && cleanup.ms < 20_000, JSON.stringify(cleanup));
});

test('launchChromium: a launch that does not return records "launch did not complete", with no pid to report', async () => {
  const fake = {launchServer: () => new Promise(() => {})};
  const error = await launchChromium(fake, {args: [], launchMs: 200, deadline: withTimeout}).then(() => null, value => value);
  assert.equal(error?.name, 'StallError');
  assert.deepEqual({pid: error.chromiumProcess.pid, ended: error.chromiumProcess.ended}, {pid: null, ended: 'launch did not complete'});
  assert.match(error.chromiumProcess.text, /did not return a process handle/);
});

test('launchChromium: a failed connect kills the launched process with the short budget and attaches the outcome', {timeout: 30_000}, async () => {
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {windowsHide: true, stdio: 'ignore'});
  try {
    const server = {process: () => child, wsEndpoint: () => 'ws://127.0.0.1:9/never'};
    const fake = {launchServer: async () => server, connect: async () => {throw new Error('connect refused');}};
    const started = Date.now();
    const error = await launchChromium(fake, {args: [], launchMs: 5000, deadline: withTimeout, killMs: 3000}).then(() => null, value => value);
    assert.ok(Date.now() - started < 4000, `took ${Date.now() - started} ms`);
    assert.equal(error?.message, 'connect refused');
    assert.equal(error.chromiumProcess.pid, child.pid); assert.equal(error.chromiumProcess.gone, true, error.chromiumProcess.text);
    assert.equal(error.chromiumProcess.ended, 'killed after the connect failed');
    assert.equal(error.chromiumServer, server);
    assert.equal(isAlive(child.pid), false);
  } finally {try {child.kill('SIGKILL');} catch {/* gone */}}
});
