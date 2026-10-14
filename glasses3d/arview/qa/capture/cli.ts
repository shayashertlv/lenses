/** `arview capture` argument parsing (pure; WP1_DESIGN.md §6, port_review/arview.json analysis.capture.proposed_layout).
 *
 *    arview capture <glb> --poses default|junction|all --out <dir>
 *      [--backgrounds skin,dark,checker] [--size 1280x854] [--depth-cm 23] [--fixed-scale] [--clip auto|<m>]
 *      [--lensenv N] [--crop-upscale 1|2] [--no-portrait] [--live-probe] [--step-timeout-s 60]
 *    arview capture <glb> --cameras <cameras.json> --no-face --out <dir> [--lensenv N] [--live-probe] [--step-timeout-s 60]
 *      (dev capture: explicit cameras without a face, capture/cameras.ts; replaces --poses, implies the authored size and
 *      no portrait; the camera file sets each image's size and background)
 *
 *  Every value is checked here, before a server or browser starts; a bad value is a UsageError (exit 2), never a
 *  silent default. Both `--name value` and `--name=value` are accepted. */
import {BACKGROUND_NAMES, POSE_SET_NAMES} from './poses.ts';
import type {BackgroundName, PoseSetName} from './poses.ts';

/** The render never exceeds this width (src/render/renderer.ts MAX_RENDER_WIDTH); a wider request would be read back
 *  narrower than asked, so it is refused up front. */
export const MAX_CAPTURE_WIDTH = 1280;
/** A stall must be killed and reported within about 2 minutes (owner rule). Every stall path is the stalled step's own
 *  deadline, plus any cleanup that runs inside the failing call before the abort, plus the abort's bounded cleanup
 *  (kill Chromium and confirm it gone, close Vite, wait for spec.py: ABORT_CLEANUP_BUDGET_MS); `stallReportPathsMs`
 *  lists them all and qa/capture-cli.test.mjs holds each below 120 s. */
export const STEP_TIMEOUT_RANGE_S = Object.freeze([5, 90] as const);
export const DEFAULT_STEP_TIMEOUT_S = 60;
export const MAX_STEP_DEADLINE_MS = 95_000;
export const ABORT_CLEANUP_BUDGET_MS = 20_000;
/** The kill launchChromium does itself when the connect fails or stalls (capture/chromium.mjs), before the abort. */
export const INNER_KILL_BUDGET_MS = 3_000;
/** The deadline of one step: `factor` x the per-step deadline (1.5 for a model load), never above MAX_STEP_DEADLINE_MS. */
export function stepDeadlineMs(stepTimeoutS: number, factor = 1): number {
  return Math.min(MAX_STEP_DEADLINE_MS, Math.round(stepTimeoutS * 1000 * factor));
}
/** The Chromium launch and connect deadline (each): the step deadline plus 5 s, never above MAX_STEP_DEADLINE_MS. */
export function launchDeadlineMs(stepTimeoutS: number): number {
  return Math.min(MAX_STEP_DEADLINE_MS, Math.round(stepTimeoutS * 1000) + 5000);
}
/** The worst time from the start of a stalled step to the partial report, per stall path, for a per-step deadline. */
export function stallReportPathsMs(stepTimeoutS: number): Readonly<Record<string, number>> {
  return Object.freeze({
    // A page step or a model load (1.5x): the deadline, then the abort.
    'page step / model load': stepDeadlineMs(stepTimeoutS, 1.5) + ABORT_CLEANUP_BUDGET_MS,
    // The launch returns no handle: nothing to kill inside launchChromium (a late launch is killed in the background).
    'chromium: launch': launchDeadlineMs(stepTimeoutS) + ABORT_CLEANUP_BUDGET_MS,
    // The connect: launchChromium kills the launched server itself (INNER_KILL_BUDGET_MS), then the abort.
    'chromium: connect': launchDeadlineMs(stepTimeoutS) + INNER_KILL_BUDGET_MS + ABORT_CLEANUP_BUDGET_MS,
    // A --live-probe step: on a stall the probe skips its graceful context and server close (the abort kills the tree
    // and closes the probe's server inside its own budget), so nothing runs between the deadline and the abort.
    'live probe step': stepDeadlineMs(stepTimeoutS) + ABORT_CLEANUP_BUDGET_MS,
  });
}
/** The pre-WP0 prototype's camera distance (ar_capture/page.js, deleted 2026-10-13; see git tag
 *  review-20261013-pre-cleanup), kept for WP4 parity and recorded. */
export const DEFAULT_DEPTH_CM = 23;
export const DEPTH_RANGE_CM = Object.freeze([15, 80] as const);
export const DEFAULT_SIZE = Object.freeze({width: 1280, height: 854});

export interface CaptureOptions {
  readonly glb: string;
  /** null with --cameras (explicit cameras replace the pose sets). */
  readonly poses: PoseSetName | null;
  readonly out: string;
  /** null: the pose set's own backgrounds. */
  readonly backgrounds: readonly BackgroundName[] | null;
  readonly width: number;
  readonly height: number;
  readonly depthCm: number;
  readonly fixedScale: boolean;
  readonly wornSides: boolean;
  /** null: 'auto', the clip the renderer derives from the model's own temples. */
  readonly clipZM: number | null;
  readonly lensEnvIntensity: number | null;
  readonly cropUpscale: 1 | 2;
  readonly portrait: boolean;
  readonly liveProbe: boolean;
  readonly stepTimeoutS: number;
  /** --cameras: the explicit cameras file (capture/cameras.ts), or null for a pose set. */
  readonly cameras: string | null;
  /** --no-face: draw without a face (required with --cameras, the only mode without one). */
  readonly noFace: boolean;
}

export class UsageError extends Error {
  constructor(message: string) {super(message); this.name = 'UsageError';}
}

export const USAGE = [
  'usage: arview capture <model.glb> --poses default|junction|all --out <dir> [options]',
  '       arview capture <model.glb> --cameras <cameras.json> --no-face --out <dir> [--lensenv N] [--live-probe] [--step-timeout-s N]',
  '  (or: node qa/capture.mjs <model.glb> --poses ... --out ...)',
  'options:',
  '  --backgrounds skin,dark,checker  backgrounds for the beauty renders (default: the pose set\'s own)',
  '  --size WxH                       capture size in pixels, width <= 1280 (default 1280x854)',
  '  --depth-cm N                     synthetic face distance, 15-80 cm (default 23, the prototype\'s)',
  '  --fixed-scale                    render at the authored size instead of the live auto-fit',
  '  --worn-sides                     append close yaw +50/-50 views with the actual head occluders visible, hair off',
  '  --clip auto|<metres>             temple clip override (default auto: derived from the model\'s temples)',
  '  --lensenv N                      lens reflection multiplier handed to the renderer (0.3-4)',
  '  --crop-upscale 1|2               nearest-neighbour upscale of junction crops (default 1, native pixels)',
  '  --no-portrait                    skip the worn front portrait on face-a.jpg',
  '  --live-probe                     also load the GLB once on main\'s unmodified ar/ and record its verdict',
  '  --step-timeout-s N               per-step deadline, 5-90 s (default 60; model loads get 1.5x, at most 95 s); a stall kills',
  '                                   Chromium and is reported within 2 minutes',
  '  --cameras <cameras.json>         dev capture: explicit pinhole cameras (schema arview.cameras/1, qa/capture/cameras.ts) instead',
  '                                   of --poses; the model at its authored size, <id>__beauty.png on each camera\'s background and',
  '                                   <id>__part-id.png; the try-on\'s renderer, materials, lighting and tone mapping',
  '  --no-face                        draw without a face: no occluders, hair, face shadows, arm spread or temple clip (with --cameras)',
  'exit codes: 0 captured and spec.py passes; 3 captured but the spec fails or could not be verified;',
  '            4 refused or unchecked by arview (nothing rendered); 1 infrastructure failure; 2 usage error',
].join('\n');

const FLAGS = new Set(['fixed-scale', 'no-portrait', 'live-probe', 'no-face', 'worn-sides']);
const VALUED = new Set(['poses', 'out', 'backgrounds', 'size', 'depth-cm', 'clip', 'lensenv', 'crop-upscale', 'step-timeout-s', 'cameras']);
/** Options a camera file replaces (it sets each image's size and background; no face means no depth, clip or crops). */
const NOT_WITH_CAMERAS = ['poses', 'backgrounds', 'size', 'depth-cm', 'clip', 'crop-upscale'] as const;

function finiteNumber(name: string, text: string): number {
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(text.trim())) throw new UsageError(`--${name} must be a number, got "${text}".`);
  const value = Number(text);
  if (!Number.isFinite(value)) throw new UsageError(`--${name} must be a finite number, got "${text}".`);
  return value;
}

/** Parse the arguments after `capture` (or after `node qa/capture.mjs`). Throws UsageError. */
export function parseCaptureArgs(argv: readonly string[]): CaptureOptions {
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
  if (positional.length !== 1) {
    throw new UsageError(positional.length ? `Exactly one model path is expected, got ${positional.length}: ${positional.join(' ')}.` : 'The model path is missing.');
  }
  const glb = positional[0]!;
  const cameras = values.get('cameras') ?? null;
  if (cameras !== null) return parseCameraCapture(glb, cameras, values, flags);
  if (flags.has('no-face')) throw new UsageError('--no-face needs --cameras (explicit cameras are the only capture without a face).');
  const poses = values.get('poses');
  if (!poses) throw new UsageError('--poses is required (default, junction or all).');
  if (!(POSE_SET_NAMES as readonly string[]).includes(poses)) throw new UsageError(`--poses must be one of ${POSE_SET_NAMES.join(', ')}, got "${poses}".`);
  const out = values.get('out');
  if (!out) throw new UsageError('--out <dir> is required.');

  let backgrounds: BackgroundName[] | null = null;
  const backgroundText = values.get('backgrounds');
  if (backgroundText !== undefined) {
    const names = backgroundText.split(',').map(name => name.trim()).filter(Boolean);
    if (!names.length) throw new UsageError('--backgrounds needs at least one background.');
    for (const name of names) {
      if (!(BACKGROUND_NAMES as readonly string[]).includes(name)) throw new UsageError(`Unknown background "${name}" (one of ${BACKGROUND_NAMES.join(', ')}).`);
    }
    if (new Set(names).size !== names.length) throw new UsageError('--backgrounds lists a background twice.');
    backgrounds = names as BackgroundName[];
  }

  let width: number = DEFAULT_SIZE.width, height: number = DEFAULT_SIZE.height;
  const size = values.get('size');
  if (size !== undefined) {
    const match = /^(\d{2,5})x(\d{2,5})$/i.exec(size.trim());
    if (!match) throw new UsageError(`--size must be WIDTHxHEIGHT in pixels, got "${size}".`);
    width = Number(match[1]); height = Number(match[2]);
    if (width < 320 || width > MAX_CAPTURE_WIDTH) {
      throw new UsageError(`--size width must be 320-${MAX_CAPTURE_WIDTH} px (the renderer never draws wider than ${MAX_CAPTURE_WIDTH}), got ${width}.`);
    }
    if (height < 240 || height > 1280) throw new UsageError(`--size height must be 240-1280 px, got ${height}.`);
  }

  let depthCm = DEFAULT_DEPTH_CM;
  const depth = values.get('depth-cm');
  if (depth !== undefined) {
    depthCm = finiteNumber('depth-cm', depth);
    if (depthCm < DEPTH_RANGE_CM[0] || depthCm > DEPTH_RANGE_CM[1]) throw new UsageError(`--depth-cm must be ${DEPTH_RANGE_CM[0]}-${DEPTH_RANGE_CM[1]}, got ${depthCm}.`);
  }

  let clipZM: number | null = null;
  const clip = values.get('clip');
  if (clip !== undefined && clip !== 'auto') {
    clipZM = finiteNumber('clip', clip);
    // The renderer ignores a clip outside [-0.2, -0.045] m with a warning (catalog.ts); a positive value is a sign
    // error the user should see now.
    if (clipZM >= 0) throw new UsageError(`--clip is a model-local z behind the front in metres (negative, e.g. -0.12), got ${clipZM}.`);
  }

  let lensEnvIntensity: number | null = null;
  const lensenv = values.get('lensenv');
  if (lensenv !== undefined) {
    lensEnvIntensity = finiteNumber('lensenv', lensenv);
    if (lensEnvIntensity < 0.3 || lensEnvIntensity > 4) throw new UsageError(`--lensenv must be 0.3-4, got ${lensEnvIntensity}.`);
  }

  let cropUpscale: 1 | 2 = 1;
  const upscale = values.get('crop-upscale');
  if (upscale !== undefined) {
    if (upscale !== '1' && upscale !== '2') throw new UsageError(`--crop-upscale must be 1 or 2, got "${upscale}".`);
    cropUpscale = upscale === '2' ? 2 : 1;
  }

  let stepTimeoutS = DEFAULT_STEP_TIMEOUT_S;
  const timeout = values.get('step-timeout-s');
  if (timeout !== undefined) {
    stepTimeoutS = finiteNumber('step-timeout-s', timeout);
    if (stepTimeoutS < STEP_TIMEOUT_RANGE_S[0] || stepTimeoutS > STEP_TIMEOUT_RANGE_S[1]) {
      throw new UsageError(`--step-timeout-s must be ${STEP_TIMEOUT_RANGE_S[0]}-${STEP_TIMEOUT_RANGE_S[1]} (a stall must be reported within about 2 minutes), got ${stepTimeoutS}.`);
    }
  }

  return Object.freeze({glb, poses: poses as PoseSetName, out, backgrounds: backgrounds ? Object.freeze(backgrounds) : null, width, height,
    depthCm, fixedScale: flags.has('fixed-scale'), wornSides: flags.has('worn-sides'), clipZM, lensEnvIntensity, cropUpscale, portrait: !flags.has('no-portrait'),
    liveProbe: flags.has('live-probe'), stepTimeoutS, cameras: null, noFace: false});
}

function lensEnvOf(values: ReadonlyMap<string, string>): number | null {
  const lensenv = values.get('lensenv');
  if (lensenv === undefined) return null;
  const value = finiteNumber('lensenv', lensenv);
  if (value < 0.3 || value > 4) throw new UsageError(`--lensenv must be 0.3-4, got ${value}.`);
  return value;
}
function stepTimeoutOf(values: ReadonlyMap<string, string>): number {
  const timeout = values.get('step-timeout-s');
  if (timeout === undefined) return DEFAULT_STEP_TIMEOUT_S;
  const value = finiteNumber('step-timeout-s', timeout);
  if (value < STEP_TIMEOUT_RANGE_S[0] || value > STEP_TIMEOUT_RANGE_S[1]) {
    throw new UsageError(`--step-timeout-s must be ${STEP_TIMEOUT_RANGE_S[0]}-${STEP_TIMEOUT_RANGE_S[1]} (a stall must be reported within about 2 minutes), got ${value}.`);
  }
  return value;
}

/** `--cameras <file> --no-face`: the explicit-camera dev capture (capture/cameras.ts). The file itself is read and
 *  validated by the orchestrator before any server or browser starts (a bad file is a usage error too). */
function parseCameraCapture(glb: string, cameras: string, values: ReadonlyMap<string, string>, flags: ReadonlySet<string>): CaptureOptions {
  if (flags.has('worn-sides')) throw new UsageError('--worn-sides cannot be combined with --cameras (worn views need a face).');
  for (const name of NOT_WITH_CAMERAS) {
    if (values.has(name)) {
      throw new UsageError(`--${name} cannot be combined with --cameras (the camera file sets each image's size and background; without a face there `
        + 'is no depth, clip or crop).');
    }
  }
  if (!flags.has('no-face')) throw new UsageError('--cameras renders without a face: say so with --no-face.');
  const out = values.get('out');
  if (!out) throw new UsageError('--out <dir> is required.');
  // The authored size and no portrait are implied (a metric camera, no face); --fixed-scale and --no-portrait are accepted.
  return Object.freeze({glb, poses: null, out, backgrounds: null, width: DEFAULT_SIZE.width, height: DEFAULT_SIZE.height, depthCm: DEFAULT_DEPTH_CM,
    fixedScale: true, wornSides: false, clipZM: null, lensEnvIntensity: lensEnvOf(values), cropUpscale: 1, portrait: false, liveProbe: flags.has('live-probe'),
    stepTimeoutS: stepTimeoutOf(values), cameras, noFace: true});
}
