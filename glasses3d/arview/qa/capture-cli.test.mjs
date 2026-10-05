/** arview capture: argument parsing (qa/capture/cli.ts). */
import assert from 'node:assert/strict';
import test from 'node:test';
import {ABORT_CLEANUP_BUDGET_MS, DEFAULT_DEPTH_CM, DEFAULT_STEP_TIMEOUT_S, INNER_KILL_BUDGET_MS, launchDeadlineMs, MAX_STEP_DEADLINE_MS, parseCaptureArgs,
  stallReportPathsMs, STEP_TIMEOUT_RANGE_S, stepDeadlineMs, UsageError, USAGE} from './capture/cli.ts';

const base = ['model.glb', '--poses', 'default', '--out', 'out/dir'];

test('the minimal command gets the documented defaults', () => {
  const options = parseCaptureArgs(base);
  assert.deepEqual({...options}, {glb: 'model.glb', poses: 'default', out: 'out/dir', backgrounds: null, width: 1280, height: 854,
    depthCm: DEFAULT_DEPTH_CM, fixedScale: false, clipZM: null, lensEnvIntensity: null, cropUpscale: 1, portrait: true, liveProbe: false,
    stepTimeoutS: DEFAULT_STEP_TIMEOUT_S});
});

test('every option parses, in both the spaced and the = form', () => {
  const options = parseCaptureArgs(['--poses=junction', 'm.glb', '--out=o', '--backgrounds', 'dark,checker', '--size=800x600', '--depth-cm', '45',
    '--fixed-scale', '--clip', '-0.12', '--lensenv=1.5', '--crop-upscale', '2', '--no-portrait', '--live-probe', '--step-timeout-s=20']);
  assert.equal(options.poses, 'junction'); assert.equal(options.glb, 'm.glb'); assert.equal(options.out, 'o');
  assert.deepEqual(options.backgrounds, ['dark', 'checker']);
  assert.equal(options.width, 800); assert.equal(options.height, 600); assert.equal(options.depthCm, 45);
  assert.equal(options.fixedScale, true); assert.equal(options.clipZM, -0.12); assert.equal(options.lensEnvIntensity, 1.5);
  assert.equal(options.cropUpscale, 2); assert.equal(options.portrait, false); assert.equal(options.liveProbe, true); assert.equal(options.stepTimeoutS, 20);
  assert.equal(parseCaptureArgs([...base, '--clip', 'auto']).clipZM, null);
});

test('bad input is a UsageError naming the problem, never a silent default', () => {
  const cases = [
    [[], /model path is missing/],
    [['a.glb', 'b.glb', '--poses', 'default', '--out', 'o'], /Exactly one model path/],
    [['m.glb', '--out', 'o'], /--poses is required/],
    [['m.glb', '--poses', 'front', '--out', 'o'], /--poses must be one of default, junction, all/],
    [['m.glb', '--poses', 'default'], /--out <dir> is required/],
    [[...base, '--backgrounds', 'skin,green'], /Unknown background "green"/],
    [[...base, '--backgrounds', 'skin,skin'], /twice/],
    [[...base, '--size', '1920x1080'], /width must be 320-1280/],
    [[...base, '--size', 'big'], /WIDTHxHEIGHT/],
    [[...base, '--depth-cm', '5'], /--depth-cm must be 15-80/],
    [[...base, '--clip', '0.12'], /negative/],
    [[...base, '--clip', 'far'], /--clip must be a number/],
    [[...base, '--lensenv', '9'], /0.3-4/],
    [[...base, '--crop-upscale', '3'], /1 or 2/],
    [[...base, '--step-timeout-s', '600'], /5-90/],
    [[...base, '--step-timeout-s', '91'], /5-90/],
    [[...base, '--step-timeout-s', 'NaN'], /must be a number/],
    [[...base, '--frobnicate'], /Unknown option --frobnicate/],
    [[...base, '--fixed-scale=yes'], /takes no value/],
    [[...base, '--poses', 'all'], /given twice/],
    [['m.glb', '--poses', '--out', 'o'], /--poses needs a value/],
  ];
  for (const [argv, pattern] of cases) assert.throws(() => parseCaptureArgs(argv), error => error instanceof UsageError && pattern.test(error.message), argv.join(' '));
});

test('the usage text lists every option and the exit codes', () => {
  for (const option of ['--poses', '--out', '--backgrounds', '--size', '--depth-cm', '--fixed-scale', '--clip', '--lensenv', '--crop-upscale',
    '--no-portrait', '--live-probe', '--step-timeout-s']) assert.match(USAGE, new RegExp(option));
  assert.match(USAGE, /exit codes: 0/);
});

test('every stall path (deadline + inner cleanup + abort cleanup) stays inside the 2-minute stall rule, at every step deadline', () => {
  const longest = Math.max(stepDeadlineMs(STEP_TIMEOUT_RANGE_S[1], 1.5), launchDeadlineMs(STEP_TIMEOUT_RANGE_S[1]));
  assert.ok(longest <= MAX_STEP_DEADLINE_MS);
  assert.equal(stepDeadlineMs(60, 1.5), 90_000); assert.equal(stepDeadlineMs(90, 1.5), 95_000); assert.equal(stepDeadlineMs(20), 20_000);
  assert.equal(launchDeadlineMs(5), 10_000); assert.equal(launchDeadlineMs(90), 95_000);
  // The paths the review found missing: the connect's inner kill before the abort, and the probe's cleanup.
  const paths = stallReportPathsMs(STEP_TIMEOUT_RANGE_S[1]);
  assert.deepEqual(Object.keys(paths), ['page step / model load', 'chromium: launch', 'chromium: connect', 'live probe step']);
  assert.equal(paths['chromium: connect'], MAX_STEP_DEADLINE_MS + INNER_KILL_BUDGET_MS + ABORT_CLEANUP_BUDGET_MS);
  assert.equal(paths['chromium: connect'], 118_000);
  assert.equal(paths['live probe step'], 90_000 + ABORT_CLEANUP_BUDGET_MS);
  for (let seconds = STEP_TIMEOUT_RANGE_S[0]; seconds <= STEP_TIMEOUT_RANGE_S[1]; seconds += 0.5) {
    for (const [name, ms] of Object.entries(stallReportPathsMs(seconds))) assert.ok(ms <= 120_000, `--step-timeout-s ${seconds}: ${name} takes ${ms} ms`);
  }
});
