/** Page options, URL-gated. The defaults are the pipeline as accepted; every parameter is a measurement or
 *  diagnostic lever and is listed on the page when it deviates. */
import {DEFAULT_CAPTURE_MAX_EDGE, DEFAULT_HAIR_INPUT_MAX_EDGE, HAIR_WAIT_MS} from './pipeline/pipeline.ts';
import type {HairDelegate} from './hair/protocol.ts';
import type {CaptureSource} from './pipeline/capture.ts';
import {DEFAULT_CONTINUITY_RUN_PX, DEFAULT_HAIR_START_Z_M} from './render/renderer.ts';
import type {FaceDelegate} from './face/detector.ts';
import {DEFAULT_STEADY} from './render/pose-stabilizer.ts';
import type {SteadyOptions} from './render/pose-stabilizer.ts';
import {DEFAULT_TEMPLE_VISIBILITY_MODE, TEMPLE_VISIBILITY_PARAMETERS} from './render/temple-visibility.ts';
import type {TempleVisibilityMode} from './render/temple-visibility.ts';
import {DEFAULT_HAIR_SCHEDULE} from './hair/mask-reuse.ts';
import type {HairSchedule} from './hair/mask-reuse.ts';

/** Which temple-fit pipeline draws the frame. `original` is the shipped geometry; `width` adds the experimental
 *  relative face-width fit (`src/render/face-width.ts`). One of the two runs at a time, and the page's selector
 *  switches between them inside a live session. */
export type FitMode = 'original' | 'width';

export interface Config {
  /** Face landmarker delegates in the order to try; the next one is tried when one fails or times out at startup.
   *  Default: CPU first on every device (laptop: perfecto_17fps; Android always was; iPhone and iPad since the iPhone
   *  runs of 2026-09-17, where a GPU face request posted just before a frame's draw took about twice as long and held
   *  hair on every second frame at 24-28 fps, while with the CPU landmarker it kept the camera's 30 fps to 168 s).
   *  `?face=gpu` / `?face=cpu` set the order. */
  faceDelegates: readonly FaceDelegate[];
  /** `?capture=` px (320..1280): the camera frame is drawn into a canvas of at most this edge before anything runs. */
  captureMaxEdge: number;
  /** `?source=videoframe|canvas`: how the frame's pixels are taken (see pipeline/capture.ts). Both were measured on
   *  2026-09-16: VideoFrame won on the laptop's webcam, the canvas won on the phone, and each is that device's default. */
  captureSource: CaptureSource;
  /** `?hairwait=` ms (0..400): the guard after which a frame is drawn without its hair mask (see HAIR_WAIT_MS). Read
   *  only where frames wait for their own mask: hair on every frame (laptop default, `?hairframes=1`), and with hair on
   *  every second frame only the frame an audit holds. A measurement lever only. */
  hairWaitMs: number;
  /** `?hairinput=` px (256..1280): the hair segmenter sees a copy of at most this edge (default 640, see
   *  DEFAULT_HAIR_INPUT_MAX_EDGE; 1280 restores the frame-size mask). */
  hairInputMaxEdge: number;
  /** `?hairdelegate=cpu|gpu` forces the hair segmenter's delegate; default: the graphics probe chooses, except on Apple
   *  phones and tablets, which default to the CPU (iPhone 17 Pro, 2026-09-15, with the face landmarker then on the GPU:
   *  no mask readback at all, and the GPU throttled less; 29 fps for 40 s instead of 20 s and 2-3 fps more at 30-60 s,
   *  level by 70 s. Kept for the defaults of 2026-09-17, which measured it with the CPU face landmarker; GPU hair with
   *  the CPU face landmarker is unmeasured; other phones are unmeasured and keep the probe). */
  hairDelegate: HairDelegate | 'auto';
  /** `?hairz=` mesh-local metres behind which temple fragments may blend toward the camera under hair. */
  hairStartZ: number;
  /** `?sync=0` stops gating each frame on the previous frame's GPU completion. */
  sync: boolean;
  /** `?exposure=<units of 100 µs>` locks the camera exposure after it opens (dim light otherwise halves the camera's rate). */
  exposure: number | null;
  /** `?guard=0` draws the unguarded single pass. */
  guard: boolean;
  /** `?continuity=0` disables the cut from an arm's first consistent hair run to its tip. */
  continuity: boolean;
  /** `?hairrun=` px (1..200): the minimum hair run along the arm that counts as a patch. */
  continuityRunPx: number;
  /** `?eyewear=`, `?hairModel=`, `?hair=0|1`: initial control values, or null for the page defaults. */
  eyewear: string | null;
  hairModel: string | null;
  hair: boolean | null;
  /** `?diag=1` sends the startup step log and, every 10 s, the last 10 s of stage medians (numbers only; never an image,
   *  detection, mask or hash) to this site, readable at /ar/diagnostics.json. Off by default since the phone work of
   *  2026-09-15 closed; the page says so in its footer when on. */
  diagnostics: boolean;
  /** Pose steadiness (pose-stabilizer.ts), on by default since the owner's live test of 2026-09-17: the glasses'
   *  orientation and depth are smoothed over time; `?steady=0` restores the unfiltered pose (null). `?steadyhz=` rotation
   *  cutoff at rest (0.05..20 Hz), `?steadybeta=` added Hz per °/s (0..5), `?steadydepthhz=` (0.05..20 Hz) and
   *  `?steadydepthbeta=` added Hz per cm/s (0..5). */
  steady: SteadyOptions | null;
  /** Which frames run the hair segmenter (hair/mask-reuse.ts). Laptop default: every frame waits for its own mask.
   *  Phone and tablet default (owner decision 2026-09-17, after the iPhone runs), or `?hairframes=2` anywhere: hair on
   *  every second frame (sooner when the head moves more than `?hairmove=` px, default 8, 0 = never sooner); frames
   *  never wait, and a frame without its own mask draws the newest mask of another frame moved with the head, if it is
   *  at most `?hairmaxage=` ms old (default 200). `?hairframes=1` makes every frame wait for its own mask anywhere. */
  hairSchedule: HairSchedule;
  /** `?fit=width` selects the experimental face-width fit; `?fit=original` (the default) is the shipped geometry. The
   *  selector on the page changes it live, so this is only the mode a session starts in. */
  fit: FitMode;
  /** `?temples=` which rule gives up part of an arm to the head. `depth` (v4, the default since 2026-09-18) decides per
   *  pixel from the head's own depth; `angles` restores the former v3 rule, two per-side percentages computed from the
   *  head's yaw, pitch and camera bearing. The selector on the page changes it live. */
  temples: TempleVisibilityMode;
  /** `?templekeep=` / `?templedrop=` cm: with `?temples=depth`, an arm fragment up to `templeKeepCm` behind the head
   *  surface is drawn whole and one beyond `templeDropCm` is given up, fading between. Raise them to see more arm
   *  alongside the head, lower them to tuck it away sooner. Visual choices, exposed so they can be judged live. */
  templeKeepCm: number;
  templeDropCm: number;
}

export const DEFAULT_CONFIG: Readonly<Config> = Object.freeze({
  faceDelegates: Object.freeze(['CPU', 'GPU'] as const), captureMaxEdge: DEFAULT_CAPTURE_MAX_EDGE, captureSource: 'videoframe', hairWaitMs: HAIR_WAIT_MS, hairInputMaxEdge: DEFAULT_HAIR_INPUT_MAX_EDGE, hairDelegate: 'auto', hairStartZ: DEFAULT_HAIR_START_Z_M, sync: true, exposure: null,
  guard: true, continuity: true, continuityRunPx: DEFAULT_CONTINUITY_RUN_PX, eyewear: null, hairModel: null, hair: null, diagnostics: false,
  steady: DEFAULT_STEADY, hairSchedule: DEFAULT_HAIR_SCHEDULE, fit: 'original', temples: DEFAULT_TEMPLE_VISIBILITY_MODE,
  templeKeepCm: TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm, templeDropCm: TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm,
});

/** Every address option the page reads: parseConfig's, and eyewear/external.ts's model handover. Any other key is
 *  ignored, so a misspelled lever (`?hairframe=2`) would silently run the default; the settings line names it instead. */
export const ADDRESS_OPTIONS: readonly string[] = Object.freeze(['face', 'capture', 'source', 'hairwait', 'hairinput', 'hairdelegate', 'hairz',
  'sync', 'exposure', 'guard', 'continuity', 'hairrun', 'eyewear', 'hairModel', 'hair', 'diag', 'steady', 'steadyhz', 'steadybeta',
  'steadydepthhz', 'steadydepthbeta', 'hairframes', 'hairmove', 'hairmaxage', 'fit', 'temples', 'templekeep', 'templedrop', 'model', 'name', 'clip', 'width', 'sha256']);

/** The address keys this page does not read, once each, in address order (names are case-sensitive). */
export function unrecognizedOptions(search: string): string[] {
  const known = new Set(ADDRESS_OPTIONS);
  return [...new Set(new URLSearchParams(search).keys())].filter(key => key !== '' && !known.has(key));
}

/** Both ends of v4's relief band, or the defaults when the pair does not make a band. */
function reliefBand(keepCm: number, dropCm: number): {templeKeepCm: number; templeDropCm: number} {
  return dropCm > keepCm ? {templeKeepCm: keepCm, templeDropCm: dropCm}
    : {templeKeepCm: TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm, templeDropCm: TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm};
}

/** The Apple phone default for the hair delegate (see Config.hairDelegate). */
export const APPLE_PHONE_HAIR_DELEGATE: HairDelegate = 'CPU';

/** True when the browser reports no fine pointer at all (no mouse or touchpad): a touch-only device. */
function noFinePointer(): boolean {
  return typeof matchMedia === 'function' && !matchMedia('(any-pointer: fine)').matches;
}

/** Phones and tablets: Apple's (an iPad asking for the desktop site says Macintosh with touch points), any user agent
 *  that says Android or Mobile, and a Linux desktop user agent with touch and no fine pointer: Chrome asks for the
 *  desktop site by default on large Android tablets. A touch laptop keeps its touchpad's fine pointer. */
export function isPhoneOrTablet(userAgent: string, maxTouchPoints: number, touchOnly: boolean = noFinePointer()): boolean {
  return isApplePhoneOrTablet(userAgent, maxTouchPoints) || /Android|Mobile/.test(userAgent)
    || (/X11; Linux/.test(userAgent) && maxTouchPoints > 1 && touchOnly);
}

/** iPhone and iPad report themselves in the user agent; iPadOS Safari may claim to be a Mac with touch points. */
export function isApplePhoneOrTablet(userAgent: string, maxTouchPoints = 0): boolean {
  return /iPhone|iPad|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && maxTouchPoints > 1);
}

export function parseConfig(search: string, userAgent = typeof navigator === 'undefined' ? '' : navigator.userAgent,
  maxTouchPoints = typeof navigator === 'undefined' ? 0 : navigator.maxTouchPoints ?? 0, touchOnly = noFinePointer()): Config {
  const params = new URLSearchParams(search);
  const face = params.get('face')?.toLowerCase();
  const gpuFirst = face === 'gpu', mobile = isPhoneOrTablet(userAgent, maxTouchPoints, touchOnly), hairFrames = params.get('hairframes');
  const number = (name: string, fallback: number, min: number, max: number): number => {
    const value = Number(params.get(name));
    return params.has(name) && Number.isFinite(value) && value >= min && value <= max ? value : fallback;
  };
  const flag = (name: string, fallback: boolean): boolean => {
    const value = params.get(name)?.toLowerCase();
    return value === undefined || value === null ? fallback : !['0', 'off', 'false', 'no'].includes(value);
  };
  const exposure = params.get('exposure'), hairDelegate = params.get('hairdelegate')?.toUpperCase(), source = params.get('source')?.toLowerCase();
  const fit = params.get('fit')?.toLowerCase(), temples = params.get('temples')?.toLowerCase();
  return {
    faceDelegates: gpuFirst ? ['GPU', 'CPU'] : ['CPU', 'GPU'],
    captureMaxEdge: Math.round(number('capture', DEFAULT_CAPTURE_MAX_EDGE, 320, 1280)),
    captureSource: source === 'videoframe' || source === 'canvas' ? source : mobile ? 'canvas' : 'videoframe',
    hairWaitMs: number('hairwait', HAIR_WAIT_MS, 0, 400),
    hairInputMaxEdge: Math.round(number('hairinput', DEFAULT_HAIR_INPUT_MAX_EDGE, 256, 1280)),
    hairDelegate: hairDelegate === 'CPU' || hairDelegate === 'GPU' ? hairDelegate : isApplePhoneOrTablet(userAgent, maxTouchPoints) ? APPLE_PHONE_HAIR_DELEGATE : 'auto',
    hairStartZ: number('hairz', DEFAULT_HAIR_START_Z_M, -0.2, 0),
    sync: flag('sync', true),
    exposure: exposure === null || exposure.toLowerCase() === 'auto' ? null : number('exposure', 0, 1, 10000) || null,
    guard: flag('guard', true),
    continuity: flag('continuity', true),
    continuityRunPx: number('hairrun', DEFAULT_CONTINUITY_RUN_PX, 1, 200),
    eyewear: params.get('eyewear'),
    hairModel: params.get('hairModel'),
    hair: params.has('hair') ? flag('hair', true) : null,
    diagnostics: flag('diag', false),
    steady: flag('steady', true) ? Object.freeze({...DEFAULT_STEADY,
      rotationMinCutoffHz: number('steadyhz', DEFAULT_STEADY.rotationMinCutoffHz, 0.05, 20), rotationBeta: number('steadybeta', DEFAULT_STEADY.rotationBeta, 0, 5),
      depthMinCutoffHz: number('steadydepthhz', DEFAULT_STEADY.depthMinCutoffHz, 0.05, 20), depthBeta: number('steadydepthbeta', DEFAULT_STEADY.depthBeta, 0, 5)}) : null,
    hairSchedule: hairFrames === '2' || (mobile && hairFrames !== '1') ? Object.freeze({...DEFAULT_HAIR_SCHEDULE, mode: 'interval', frames: 2,
      movePx: Math.round(number('hairmove', DEFAULT_HAIR_SCHEDULE.movePx, 0, 200)), maxAgeMs: Math.round(number('hairmaxage', DEFAULT_HAIR_SCHEDULE.maxAgeMs, 30, 1000))}) : DEFAULT_HAIR_SCHEDULE,
    fit: fit === 'width' ? 'width' : 'original',
    temples: temples === 'angles' ? 'angles' : temples === 'depth' ? 'depth' : DEFAULT_TEMPLE_VISIBILITY_MODE,
    ...reliefBand(number('templekeep', TEMPLE_VISIBILITY_PARAMETERS.reliefBehindStartCm, 0, 6),
      number('templedrop', TEMPLE_VISIBILITY_PARAMETERS.reliefBehindFullCm, 0.1, 12)),
  };
}


/** One line for the page: the levers and whether each is at its default. */
export function describeConfig(config: Config, userAgent = typeof navigator === 'undefined' ? '' : navigator.userAgent,
  maxTouchPoints = typeof navigator === 'undefined' ? 0 : navigator.maxTouchPoints ?? 0, touchOnly = noFinePointer()): string {
  const mobile = isPhoneOrTablet(userAgent, maxTouchPoints, touchOnly), everyFrame = config.hairSchedule.mode === 'every';
  return [
    `face landmarker ${config.faceDelegates[0] === 'CPU' ? 'CPU delegate, then GPU (?face=)' : 'GPU delegate, then CPU (?face=)'}`,
    `capture ${config.captureMaxEdge} px max edge${config.captureMaxEdge === DEFAULT_CAPTURE_MAX_EDGE ? '' : ' (?capture=)'}`,
    `capture source ${config.captureSource === 'videoframe' ? 'VideoFrame' : 'canvas'} (?source=)`,
    ...(config.hairWaitMs === HAIR_WAIT_MS ? [] : [everyFrame ? `hair mask guard ${config.hairWaitMs} ms (?hairwait=)`
      : `hair mask guard ${config.hairWaitMs} ms (?hairwait=) applies only to an audited frame: with hair every ${config.hairSchedule.frames} frames no other frame waits (?hairframes=1)`]),
    ...(config.hairInputMaxEdge === DEFAULT_HAIR_INPUT_MAX_EDGE ? [] : [`hair input ${config.hairInputMaxEdge} px max edge (?hairinput=)`]),
    ...(config.hairDelegate === 'auto' ? [] : [`hair delegate ${config.hairDelegate}${config.hairDelegate === APPLE_PHONE_HAIR_DELEGATE && isApplePhoneOrTablet(userAgent, maxTouchPoints) ? ' (Apple phone default)' : ''} (?hairdelegate=)`]),
    `hair start z ${config.hairStartZ} m${config.hairStartZ === DEFAULT_HAIR_START_Z_M ? '' : ' (?hairz=)'}`,
    `GPU completion gate ${config.sync ? 'on' : 'OFF (?sync=0)'}`,
    `camera exposure ${config.exposure === null ? 'auto (?exposure=312 locks 1/32 s)' : `locked at ${config.exposure} × 100 µs`}`,
    `guard ${config.guard ? 'on' : 'OFF (?guard=0)'}`,
    `continuity cut ${config.continuity ? `on (hair run ≥ ${config.continuityRunPx} px, ?hairrun=)` : 'OFF (?continuity=0)'}`,
    ...(everyFrame ? mobile ? ['hair on every frame, each waits for its own mask (?hairframes=1)'] : []
      : [`hair every ${config.hairSchedule.frames} frames (${mobile ? 'phone and tablet default; ?hairframes=1 for every frame' : '?hairframes=2'})${config.hairSchedule.movePx > 0 ? `, sooner when the head moves > ${config.hairSchedule.movePx} px (?hairmove=)` : ', never sooner (?hairmove=0)'}; frames never wait, others reuse the newest mask moved with the head up to ${config.hairSchedule.maxAgeMs} ms old (?hairmaxage=)`]),
    config.steady ? `pose steadiness on: rotation ${config.steady.rotationMinCutoffHz} Hz + ${config.steady.rotationBeta} Hz per °/s (?steadyhz=, ?steadybeta=), depth ${config.steady.depthMinCutoffHz} Hz + ${config.steady.depthBeta} Hz per cm/s (?steadydepthhz=, ?steadydepthbeta=)` : 'pose steadiness OFF (?steady=0)',
    `temple occlusion ${config.temples === 'depth'
      ? `per pixel from the head's own depth, v4: kept to ${config.templeKeepCm} cm behind it, gone by ${config.templeDropCm} cm (?templekeep=, ?templedrop=, ?temples=angles for the former per-side percentages)`
      : 'PER-SIDE PERCENTAGES from the head angles, the former v3 (?temples=depth)'}`,
    `temple fit ${config.fit === 'width' ? 'WIDTH FIT, experimental: the head occluder and the posterior arm spread follow a stable face-width ratio (?fit=original restores the shipped geometry)' : 'original (?fit=width for the experiment)'}`,
    ...(config.diagnostics ? ['diagnostics ON (?diag=1)'] : []),
  ].join(' · ') + '.';
}
