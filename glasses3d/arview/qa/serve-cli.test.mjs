/** arview serve: argument parsing (qa/serve/cli.ts) and the Windows command-line quoting of the detached start
 *  (qa/serve/detach.mjs). Pure. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {DEFAULT_SERVE_PORT, DETACH_READY_TIMEOUT_MS, parseServeArgs, SERVE_BUILD_TIMEOUT_MS, SERVE_CHECK_TIMEOUT_MS, SERVE_SPEC_TIMEOUT_MS,
  SERVE_USAGE, UsageError} from './serve/cli.ts';
import {quoteWindowsArg} from './serve/detach.mjs';

test('defaults: port 8251, foreground, no handover values', () => {
  assert.equal(DEFAULT_SERVE_PORT, 8251);
  assert.deepEqual(parseServeArgs(['model.glb']), {mode: 'serve', glb: 'model.glb', port: 8251, detach: false, name: null, clipZM: null,
    lensEnvIntensity: null, stateDir: null, log: null, launchId: null, ttlS: null});
});

test('every option, in both spellings', () => {
  const parsed = parseServeArgs(['m.glb', '--port', '8300', '--detach', '--name=  FT1119 q001 ', '--clip', '-0.12', '--lensenv=1.5', '--state-dir', 'D', '--ttl-s', '60']);
  assert.deepEqual(parsed, {mode: 'serve', glb: 'm.glb', port: 8300, detach: true, name: 'FT1119 q001', clipZM: -0.12, lensEnvIntensity: 1.5,
    stateDir: 'D', log: null, launchId: null, ttlS: 60});
  assert.equal(parseServeArgs(['m.glb', '--port=0']).port, 0, 'port 0 = any free port (tests)');
  assert.deepEqual(parseServeArgs(['m.glb', '--log', 'L.log', '--launch-id', 'abcdef12-3456']).log, 'L.log');
  assert.deepEqual(parseServeArgs(['--stop']), {mode: 'stop', port: 8251, stateDir: null});
  assert.deepEqual(parseServeArgs(['--status', '--port', '8300', '--state-dir', 'D']), {mode: 'status', port: 8300, stateDir: 'D'});
});

test('every bad value is a usage error, never a silent default', () => {
  for (const [argv, pattern] of [
    [[], /model path is missing/], [['a.glb', 'b.glb'], /Exactly one model path/], [['m.glb', '--bogus'], /Unknown option --bogus/],
    [['m.glb', '--port', '80'], /0 \(any free port\) or 1024-65535/], [['m.glb', '--port', '70000'], /1024-65535/], [['m.glb', '--port', 'x'], /whole number/],
    [['m.glb', '--port'], /needs a value/], [['m.glb', '--port', '--detach'], /needs a value/], [['m.glb', '--detach=1'], /takes no value/],
    [['m.glb', '--detach', '--detach'], /given twice/], [['m.glb', '--clip', '-0.3'], /-0.2 to -0.045/], [['m.glb', '--clip', '0.12'], /-0.2 to -0.045/],
    [['m.glb', '--clip', 'abc'], /must be a number/], [['m.glb', '--lensenv', '9'], /0.3-4/], [['m.glb', '--name', '   '], /must not be blank/],
    [['m.glb', '--name', 'x'.repeat(121)], /at most 120/], [['m.glb', '--launch-id', 'bad id'], /launch-id/], [['m.glb', '--ttl-s', '5'], /10-86400/],
    [['m.glb', '--detach', '--log', 'x.log'], /set by --detach itself/], [['--stop', 'm.glb'], /takes no model path/], [['--stop', '--status'], /cannot be combined/],
    [['--stop', '--port', '0'], /real port/], [['--stop', '--name', 'x'], /cannot be used with --stop/], [['--status', '--detach'], /cannot be used with --status/],
  ]) assert.throws(() => parseServeArgs(argv), error => error instanceof UsageError && pattern.test(error.message), `${argv.join(' ')} -> ${pattern}`);
});

test('the deadlines keep every wait inside the no-silent-stall rule', () => {
  // spec.py and the Node check run in parallel before the server listens; --detach waits for both, then gives up.
  assert.ok(Math.max(SERVE_SPEC_TIMEOUT_MS, SERVE_CHECK_TIMEOUT_MS) < DETACH_READY_TIMEOUT_MS);
  assert.ok(DETACH_READY_TIMEOUT_MS <= 100_000 && SERVE_BUILD_TIMEOUT_MS <= 100_000, 'each phase reports or ends within 2 minutes');
  assert.match(SERVE_USAGE, /--detach/); assert.match(SERVE_USAGE, /--stop/); assert.match(SERVE_USAGE, /8251/);
});

/** CommandLineToArgvW's splitting rules (what the C runtime and Node apply to a Windows command line). */
function splitWindows(line) {
  const out = []; let i = 0;
  while (i < line.length) {
    while (line[i] === ' ' || line[i] === '\t') i++;
    if (i >= line.length) break;
    let arg = '', quoted = false;
    for (; i < line.length; i++) {
      const c = line[i];
      if (c === '\\') {
        let n = 0; while (line[i] === '\\') {n++; i++;}
        if (line[i] === '"') {arg += '\\'.repeat(Math.floor(n / 2)); if (n % 2) {arg += '"'; continue;} i--; continue;}
        arg += '\\'.repeat(n); i--; continue;
      }
      if (c === '"') {quoted = !quoted; continue;}
      if (!quoted && (c === ' ' || c === '\t')) break;
      arg += c;
    }
    out.push(arg);
  }
  return out;
}

test('Windows quoting round-trips every argument the detached start passes (paths with spaces, quotes, trailing backslashes)', () => {
  const args = ['C:\\Program Files\\nodejs\\node.exe', 'C:\\Users\\Shay\\Py Charm\\qa\\serve.mjs', 'C:\\models\\FT 1119\\', 'plain', '', 'x"y', 'a\\"b',
    'end\\\\', '--name', 'Tom Ford "crystal"', '-0.12'];
  assert.deepEqual(splitWindows(args.map(quoteWindowsArg).join(' ')), args);
  assert.equal(quoteWindowsArg('plain'), 'plain');
  assert.equal(quoteWindowsArg('a b'), '"a b"');
  assert.equal(quoteWindowsArg('C:\\dir with space\\'), '"C:\\dir with space\\\\"');
});
