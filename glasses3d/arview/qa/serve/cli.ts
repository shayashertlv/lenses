/** `arview serve` argument parsing (pure; WP1_DESIGN.md §6, the owner's webcam page).
 *
 *    arview serve <model.glb> [--port 8251] [--detach] [--name <label>] [--clip <m>] [--lensenv N]
 *    arview serve --stop [--port 8251]
 *    arview serve --status [--port 8251]
 *
 *  Every value is checked here, before anything is built or served; a bad value is a UsageError (exit 2), never a silent
 *  default. Both `--name value` and `--name=value` are accepted. `--state-dir`, `--log` and `--launch-id` are plumbing for
 *  tests and for the detached child that `--detach` starts. */

/** The frozen webcam page's port (8250 is the dev server, `npm run dev`; ar/ keeps 8240/8241). */
export const DEFAULT_SERVE_PORT = 8251;
/** spec.py and arview's Node-side check run in parallel before the server listens; each has its own deadline, so a
 *  stalled validator or parse delays the page by at most this long and is then a loud warning in the report. */
export const SERVE_SPEC_TIMEOUT_MS = 60_000;
export const SERVE_CHECK_TIMEOUT_MS = 60_000;
/** A vite build that has not finished by then is a stall: the process says so and exits 1. */
export const SERVE_BUILD_TIMEOUT_MS = 100_000;
/** `--detach` waits this long for the detached server to say it is listening (its report needs at most the two deadlines
 *  above, run in parallel), printing a line every 10 s; past it the child is killed and its log tail printed. */
export const DETACH_READY_TIMEOUT_MS = 100_000;
/** `--stop` waits this long for the server to exit after asking it to (then kills it, if it is still that server). */
export const STOP_TIMEOUT_MS = 10_000;
export const MAX_NAME_LENGTH = 120;

export type ServeMode = 'serve' | 'stop' | 'status';

export interface ServeOptions {
  readonly mode: 'serve';
  readonly glb: string;
  /** 0 = any free port (tests); otherwise strict: a port in use is an error, never a silent move to another one. */
  readonly port: number;
  readonly detach: boolean;
  /** The label the page shows; null = the file name. */
  readonly name: string | null;
  /** A temple clip handed to the page (?clip=); null = derived from the model's temples. */
  readonly clipZM: number | null;
  readonly lensEnvIntensity: number | null;
  /** Where the state files live (default glasses3d/data/arview/serve/state). */
  readonly stateDir: string | null;
  /** The detached child writes its log here (set by --detach). */
  readonly log: string | null;
  /** The detached child's launch id, written into its state file so the starter knows the file is its child's. */
  readonly launchId: string | null;
  /** Stop by itself after this many seconds (tests: a server a crashed test leaves behind still ends); null = never. */
  readonly ttlS: number | null;
}

export interface ControlOptions {
  readonly mode: 'stop' | 'status';
  readonly port: number;
  readonly stateDir: string | null;
}

export class UsageError extends Error {
  constructor(message: string) {super(message); this.name = 'UsageError';}
}

export const SERVE_USAGE = [
  'usage: arview serve <model.glb> [options]        (or: node qa/serve.mjs <model.glb> [options])',
  '       arview serve --stop [--port N]            stop the server on that port (default 8251)',
  '       arview serve --status [--port N]          print the state of the server on that port (exit 0 while an arview',
  '                                                 serve owns it: serving, or still checking the model)',
  'Builds the frozen webcam page if its sources changed, serves it and the model on loopback, and prints the URL to open',
  'in Chrome or Edge: http://127.0.0.1:8251/?model=<loopback model URL>&... The page lists the model report (failed §2 rows,',
  'warnings, fallbacks) and the sha256 of the bytes it renders. Ctrl+C stops a foreground server.',
  'options:',
  '  --port N         port on 127.0.0.1 (default 8251; 0 = any free port); a port in use is an error',
  '  --detach         start the server in the background (it survives closing this terminal), print the URL, return',
  '  --name <label>   the label the page shows (default: the file name)',
  '  --clip <metres>  temple clip handed to the page, -0.2 to -0.045 (default: derived from the model\'s temples)',
  '  --lensenv N      lens reflection multiplier handed to the page (0.3-4)',
  'exit codes: 0 serving (or stopped); 1 failure (build, port, detached start); 2 usage error',
].join('\n');

const FLAGS = new Set(['detach', 'stop', 'status']);
const VALUED = new Set(['port', 'name', 'clip', 'lensenv', 'state-dir', 'log', 'launch-id', 'ttl-s']);

function finiteNumber(name: string, text: string): number {
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(text.trim())) throw new UsageError(`--${name} must be a number, got "${text}".`);
  const value = Number(text);
  if (!Number.isFinite(value)) throw new UsageError(`--${name} must be a finite number, got "${text}".`);
  return value;
}

/** Parse the arguments after `serve` (or after `node qa/serve.mjs`). Throws UsageError. */
export function parseServeArgs(argv: readonly string[]): ServeOptions | ControlOptions {
  const values = new Map<string, string>(), flags = new Set<string>(), positional: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const argument = argv[i]!;
    if (!argument.startsWith('--')) {positional.push(argument); continue;}
    const equals = argument.indexOf('=');
    const name = argument.slice(2, equals < 0 ? undefined : equals);
    if (FLAGS.has(name)) {
      if (equals >= 0) throw new UsageError(`--${name} takes no value.`);
      if (flags.has(name)) throw new UsageError(`--${name} is given twice.`);
      flags.add(name); continue;
    }
    if (!VALUED.has(name)) throw new UsageError(`Unknown option --${name}.`);
    if (values.has(name)) throw new UsageError(`--${name} is given twice.`);
    let value: string | undefined;
    if (equals >= 0) value = argument.slice(equals + 1);
    else {value = argv[i + 1]; i++;}
    if (value === undefined || value === '' || (equals < 0 && value.startsWith('--'))) throw new UsageError(`--${name} needs a value.`);
    values.set(name, value);
  }

  let port = DEFAULT_SERVE_PORT;
  const portText = values.get('port');
  if (portText !== undefined) {
    if (!/^\d{1,5}$/.test(portText.trim())) throw new UsageError(`--port must be a whole number, got "${portText}".`);
    port = Number(portText);
    if (port !== 0 && (port < 1024 || port > 65535)) throw new UsageError(`--port must be 0 (any free port) or 1024-65535, got ${port}.`);
  }
  const stateDir = values.get('state-dir') ?? null;

  const control = (['stop', 'status'] as const).filter(flag => flags.has(flag));
  if (control.length > 1) throw new UsageError('--stop and --status cannot be combined.');
  if (control.length === 1) {
    const mode = control[0]!;
    if (positional.length) throw new UsageError(`--${mode} takes no model path (got ${positional.join(' ')}).`);
    for (const name of ['name', 'clip', 'lensenv', 'log', 'launch-id', 'ttl-s']) if (values.has(name)) throw new UsageError(`--${name} cannot be used with --${mode}.`);
    if (flags.has('detach')) throw new UsageError(`--detach cannot be used with --${mode}.`);
    if (port === 0) throw new UsageError(`--${mode} needs the server's real port, not 0.`);
    return Object.freeze({mode, port, stateDir});
  }

  if (positional.length !== 1) {
    throw new UsageError(positional.length ? `Exactly one model path is expected, got ${positional.length}: ${positional.join(' ')}.` : 'The model path is missing.');
  }
  let name: string | null = null;
  const nameText = values.get('name');
  if (nameText !== undefined) {
    name = nameText.trim();
    if (!name) throw new UsageError('--name must not be blank.');
    if (name.length > MAX_NAME_LENGTH) throw new UsageError(`--name is at most ${MAX_NAME_LENGTH} characters (the page shortens longer ones), got ${name.length}.`);
  }
  let clipZM: number | null = null;
  const clip = values.get('clip');
  if (clip !== undefined) {
    clipZM = finiteNumber('clip', clip);
    // The page ignores a clip outside the drawable range with a warning (catalog.ts); here it is caught before serving.
    if (clipZM < -0.2 || clipZM > -0.045) throw new UsageError(`--clip must be -0.2 to -0.045 (model-local metres behind the front), got ${clipZM}.`);
  }
  let lensEnvIntensity: number | null = null;
  const lensenv = values.get('lensenv');
  if (lensenv !== undefined) {
    lensEnvIntensity = finiteNumber('lensenv', lensenv);
    if (lensEnvIntensity < 0.3 || lensEnvIntensity > 4) throw new UsageError(`--lensenv must be 0.3-4, got ${lensEnvIntensity}.`);
  }
  const launchId = values.get('launch-id') ?? null;
  if (launchId !== null && !/^[A-Za-z0-9-]{8,64}$/.test(launchId)) throw new UsageError('--launch-id must be 8-64 letters, digits or dashes.');
  const log = values.get('log') ?? null;
  if (flags.has('detach') && (log !== null || launchId !== null)) throw new UsageError('--log and --launch-id are set by --detach itself.');
  let ttlS: number | null = null;
  const ttl = values.get('ttl-s');
  if (ttl !== undefined) {
    ttlS = finiteNumber('ttl-s', ttl);
    if (ttlS < 10 || ttlS > 86_400) throw new UsageError(`--ttl-s must be 10-86400, got ${ttlS}.`);
  }
  return Object.freeze({mode: 'serve', glb: positional[0]!, port, detach: flags.has('detach'), name, clipZM, lensEnvIntensity, stateDir, log, launchId, ttlS});
}
