import assert from 'node:assert/strict';
import test from 'node:test';
import {STARTUP_STAGES, StartupWatchdog} from './startup-watchdog.ts';
import type {StartupReceipt, StartupStage, StartupWatchdogOptions} from './startup-watchdog.ts';

interface Scheduled {callback: () => void; at: number; cancelled: boolean;}
function fixture(extra: Partial<StartupWatchdogOptions> = {}) {
  let now = 1000;
  const tasks: Scheduled[] = [], timeouts: StartupReceipt[] = [];
  const watchdog = new StartupWatchdog({sessionId: 'session-a', now: () => now, onTimeout: receipt => timeouts.push(receipt),
    schedule(callback, delayMs) {const task = {callback, at: now + delayMs, cancelled: false}; tasks.push(task); return () => {task.cancelled = true;};}, ...extra});
  return {watchdog, tasks, timeouts, at(value: number) {now = value;},
    fire(value: number) {now = value; for (const task of [...tasks]) if (!task.cancelled && task.at <= now) task.callback();}};
}

test('startup remains idle during camera permission and begins its deadline only after start', () => {
  const f = fixture(); f.at(100000);
  assert.equal(f.watchdog.snapshot().state, 'idle'); assert.equal(f.tasks.length, 0);
  const start = f.watchdog.start();
  assert.equal(start.startedAtMs, 100000); assert.equal(start.stage, 'module');
  assert.equal(start.remainingMs, 60000); assert.equal(start.overallDeadlineAtMs, 340000);
  assert.equal(f.tasks[0]!.at, 160000);
});

test('each declared stage keeps its own deadline and an unfinished promise requires no reply to time out', () => {
  const expected: Record<StartupStage, number> = {'module': 60000, 'g-renderer': 60000, 'candidate-renderer': 60000,
    face: 100000, 'first-ar': 30000};
  for (const stage of STARTUP_STAGES) {
    const f = fixture(); f.watchdog.start();
    for (const next of STARTUP_STAGES.slice(1, STARTUP_STAGES.indexOf(stage) + 1)) f.watchdog.enter(next);
    const deadline = 1000 + expected[stage];
    f.fire(deadline - 1); assert.equal(f.watchdog.snapshot().state, 'running');
    f.fire(deadline); assert.equal(f.watchdog.snapshot().state, 'timed-out');
    assert.equal(f.timeouts.length, 1); assert.equal(f.timeouts[0]!.reason, `${stage}-timeout`);
    assert.equal(f.timeouts[0]!.timeoutScope, 'stage'); assert.equal(f.timeouts[0]!.stage, stage);
  }
});

test('duplicate current milestones do not restart timers or allow an indefinite stage', () => {
  const f = fixture(); f.watchdog.start(); const task = f.tasks[0];
  f.at(30000); f.watchdog.enter('module');
  assert.equal(f.tasks.length, 1); assert.equal(f.tasks[0], task);
  assert.equal(f.watchdog.snapshot().remainingMs, 31000);
  f.fire(61000); assert.equal(f.watchdog.snapshot().state, 'timed-out');
});

test('overall deadline bounds slow consecutive stages without changing existing native worker deadlines', () => {
  const f = fixture(); f.watchdog.start();
  f.at(60000); f.watchdog.enter('g-renderer');
  f.at(119000); f.watchdog.enter('candidate-renderer');
  f.at(178000); const status = f.watchdog.enter('face');
  assert.equal(status.stages.at(-1)!.timeoutMs, 100000);
  assert.equal(status.remainingMs, 63000);
  f.fire(241000);
  assert.equal(f.timeouts.length, 1); assert.equal(f.timeouts[0]!.reason, 'overall-timeout');
  assert.equal(f.timeouts[0]!.stage, 'face'); assert.equal(f.timeouts[0]!.elapsedMs, 240000);
});

test('only a first AR publication can complete startup, and successful completion cancels every callback', () => {
  const f = fixture(); f.watchdog.start();
  assert.throws(() => f.watchdog.complete(), /first completed AR/);
  for (const [index, stage] of STARTUP_STAGES.slice(1).entries()) {f.at(2000 + index * 1000); f.watchdog.enter(stage);}
  f.at(6000); const complete = f.watchdog.complete();
  assert.equal(complete.state, 'complete'); assert.equal(complete.elapsedMs, 5000);
  assert.ok(f.tasks.every(task => task.cancelled));
  f.at(400000); for (const task of f.tasks) task.callback();
  assert.equal(f.timeouts.length, 0); assert.equal(f.watchdog.snapshot().state, 'complete');
  assert.equal(f.watchdog.snapshot().elapsedMs, 5000);
});

test('late timer callbacks from a previous phase cannot terminate the current phase', () => {
  const f = fixture(); f.watchdog.start(); const oldCallback = f.tasks[0]!.callback;
  f.at(2000); f.watchdog.enter('g-renderer');
  f.at(61000); oldCallback();
  assert.equal(f.watchdog.snapshot().state, 'running'); assert.equal(f.timeouts.length, 0);
  f.fire(62000); assert.equal(f.watchdog.snapshot().reason, 'g-renderer-timeout');
});

test('cancelled startup callbacks cannot affect a retry belonging to a new session', () => {
  const old = fixture(); old.watchdog.start(); const callback = old.tasks[0]!.callback;
  old.at(2000); old.watchdog.cancel('user-stopped');
  const retry = fixture({sessionId: 'session-b'}); retry.watchdog.start();
  old.at(61000); callback();
  assert.equal(old.timeouts.length, 0); assert.equal(retry.watchdog.snapshot().state, 'running');
  assert.equal(old.watchdog.enter('g-renderer').state, 'cancelled');
  assert.equal(old.watchdog.complete().state, 'cancelled');
  assert.throws(() => old.watchdog.start(), /one attempt/);
});

test('success or stage transitions arriving after their deadline cannot rescue a timed-out attempt', () => {
  for (const complete of [false, true]) {
    const f = fixture(); f.watchdog.start();
    if (complete) for (const stage of STARTUP_STAGES.slice(1)) f.watchdog.enter(stage);
    f.at(complete ? 31000 : 61000);
    const receipt = complete ? f.watchdog.complete() : f.watchdog.enter('g-renderer');
    assert.equal(receipt.state, 'timed-out'); assert.equal(f.timeouts.length, 1);
    for (const task of f.tasks) task.callback(); assert.equal(f.timeouts.length, 1);
  }
});

test('abort releases timer ownership before or during startup and never emits a timeout', () => {
  const controller = new AbortController(), f = fixture({signal: controller.signal});
  f.watchdog.start(); controller.abort();
  assert.equal(f.watchdog.snapshot().state, 'cancelled'); assert.equal(f.watchdog.snapshot().reason, 'aborted');
  f.fire(61000); assert.equal(f.timeouts.length, 0);
  const preAborted = fixture({signal: controller.signal});
  assert.equal(preAborted.watchdog.start().state, 'cancelled'); assert.equal(preAborted.tasks.length, 0);
});

test('failure preserves the original outcome when close and late replies subsequently arrive', () => {
  const f = fixture(); f.watchdog.start(); f.at(2000);
  assert.equal(f.watchdog.fail('glasses-decode-failed').state, 'failed');
  f.watchdog.cancel('closed'); f.watchdog.enter('g-renderer'); f.watchdog.complete();
  assert.equal(f.watchdog.snapshot().reason, 'glasses-decode-failed');
  f.fire(61000); assert.equal(f.timeouts.length, 0);
});

test('scalar receipts retain ordered intervals without exposing mutable internal stage history', () => {
  const f = fixture(); f.watchdog.start(); f.at(2500); f.watchdog.enter('g-renderer'); f.at(3500);
  const first = f.watchdog.snapshot();
  assert.equal(first.elapsedMs, 2500); assert.equal(first.stageElapsedMs, 1000);
  assert.deepEqual(first.stages[0], {stage: 'module', startedAtMs: 1000, endedAtMs: 2500, deadlineAtMs: 61000, timeoutMs: 60000});
  first.stages[0]!.deadlineAtMs = 1; first.stages.length = 0;
  assert.equal(f.watchdog.snapshot().stages[0]!.deadlineAtMs, 61000);
  assert.equal(JSON.stringify(f.watchdog.snapshot()).includes('image'), false);
  assert.throws(() => f.watchdog.enter('face'), /declared order/);
});

test('an early scheduler callback waits only the remaining duration and a backwards clock cannot extend it', () => {
  const f = fixture(); f.watchdog.start(); f.at(30000); f.tasks[0]!.callback();
  assert.equal(f.tasks[1]!.at, 61000); assert.equal(f.timeouts.length, 0);
  f.at(2000); assert.equal(f.watchdog.snapshot().remainingMs, 31000);
  f.fire(61000); assert.equal(f.timeouts.length, 1);
});

test('invalid deadlines and clocks fail before unbounded startup timers are created', () => {
  assert.throws(() => fixture({overallTimeoutMs: Infinity}), /positive and finite/);
  assert.throws(() => fixture({timeouts: {face: 0}}), /positive and finite/);
  const f = fixture({now: () => NaN});
  assert.throws(() => f.watchdog.start(), /clock/); assert.equal(f.tasks.length, 0);
});
