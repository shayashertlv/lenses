/** Page options, URL-gated. The defaults are the pipeline as accepted; every parameter is a measurement or
 *  diagnostic lever and is listed on the page when it deviates. */
import {DEFAULT_CAPTURE_MAX_EDGE, HAIR_WAIT_MS} from './pipeline/pipeline.ts';
import {DEFAULT_CONTINUITY_RUN_PX, DEFAULT_HAIR_START_Z_M} from './render/renderer.ts';
import type {FaceDelegate} from './face/detector.ts';

export interface Config {
  /** Face landmarker delegates in the order to try; the next one is tried when one fails or times out at startup.
   *  Default: CPU first (perfecto_17fps, measured on the laptop), except on iPhone and iPad where the GPU comes first
   *  (the order the earlier phone tests ran). `?face=gpu` / `?face=cpu` set the order explicitly. */
  faceDelegates: readonly FaceDelegate[];
  /** `?capture=` px (320..1280): the camera frame is drawn into a canvas of at most this edge before anything runs. */
  captureMaxEdge: number;
  /** `?hairwait=` ms (0..200): how long a frame waits for its own hair mask before it is drawn without it. Default 8 ms
   *  (laptop, measured: masks on 97-100 % of frames); on phones PHONE_HAIR_WAIT_MS, because the phone's hair worker
   *  needs 43-65 ms per frame and at 8 ms not one mask in two minutes was drawn (iPhone 17 Pro, 2026-09-15), while at 60 ms
   *  every frame carried its mask at the same rate (18 vs 16-17 fps) and age (103 vs 98-107 ms). */
  hairWaitMs: number;
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
  /** `?diag=0` sends no startup or live diagnostics to the site (an A/B lever for the phone: the beacon is one small
   *  post every 10 s, of a summary the panel computes anyway). */
  diagnostics: boolean;
}

export const DEFAULT_CONFIG: Readonly<Config> = Object.freeze({
  faceDelegates: Object.freeze(['CPU', 'GPU'] as const), captureMaxEdge: DEFAULT_CAPTURE_MAX_EDGE, hairWaitMs: HAIR_WAIT_MS, hairStartZ: DEFAULT_HAIR_START_Z_M, sync: true, exposure: null,
  guard: true, continuity: true, continuityRunPx: DEFAULT_CONTINUITY_RUN_PX, eyewear: null, hairModel: null, hair: null, diagnostics: true,
});

/** The phone default for the hair wait (see Config.hairWaitMs). */
export const PHONE_HAIR_WAIT_MS = 60;

/** iPhone and iPad report themselves in the user agent; iPadOS Safari may claim to be a Mac with touch points. */
export function isApplePhoneOrTablet(userAgent: string, maxTouchPoints = 0): boolean {
  return /iPhone|iPad|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && maxTouchPoints > 1);
}

/** Phones and tablets: Apple's, or any user agent that says Android or Mobile. */
export function isPhoneOrTablet(userAgent: string, maxTouchPoints = 0): boolean {
  return isApplePhoneOrTablet(userAgent, maxTouchPoints) || /Android|Mobile/.test(userAgent);
}

export function parseConfig(search: string, userAgent = typeof navigator === 'undefined' ? '' : navigator.userAgent,
  maxTouchPoints = typeof navigator === 'undefined' ? 0 : navigator.maxTouchPoints ?? 0): Config {
  const params = new URLSearchParams(search);
  const face = params.get('face')?.toLowerCase();
  const gpuFirst = face === 'gpu' || (face !== 'cpu' && isApplePhoneOrTablet(userAgent, maxTouchPoints));
  const number = (name: string, fallback: number, min: number, max: number): number => {
    const value = Number(params.get(name));
    return params.has(name) && Number.isFinite(value) && value >= min && value <= max ? value : fallback;
  };
  const flag = (name: string, fallback: boolean): boolean => {
    const value = params.get(name)?.toLowerCase();
    return value === undefined || value === null ? fallback : !['0', 'off', 'false', 'no'].includes(value);
  };
  const exposure = params.get('exposure');
  return {
    faceDelegates: gpuFirst ? ['GPU', 'CPU'] : ['CPU', 'GPU'],
    captureMaxEdge: Math.round(number('capture', DEFAULT_CAPTURE_MAX_EDGE, 320, 1280)),
    hairWaitMs: number('hairwait', isPhoneOrTablet(userAgent, maxTouchPoints) ? PHONE_HAIR_WAIT_MS : HAIR_WAIT_MS, 0, 200),
    hairStartZ: number('hairz', DEFAULT_HAIR_START_Z_M, -0.2, 0),
    sync: flag('sync', true),
    exposure: exposure === null || exposure.toLowerCase() === 'auto' ? null : number('exposure', 0, 1, 10000) || null,
    guard: flag('guard', true),
    continuity: flag('continuity', true),
    continuityRunPx: number('hairrun', DEFAULT_CONTINUITY_RUN_PX, 1, 200),
    eyewear: params.get('eyewear'),
    hairModel: params.get('hairModel'),
    hair: params.has('hair') ? flag('hair', true) : null,
    diagnostics: flag('diag', true),
  };
}

/** One line for the page: the levers and whether each is at its default. */
export function describeConfig(config: Config): string {
  return [
    `face landmarker ${config.faceDelegates[0] === 'CPU' ? 'CPU delegate, then GPU (?face=)' : 'GPU delegate, then CPU (?face=)'}`,
    `capture ${config.captureMaxEdge} px max edge${config.captureMaxEdge === DEFAULT_CAPTURE_MAX_EDGE ? '' : ' (?capture=)'}`,
    ...(config.hairWaitMs === HAIR_WAIT_MS ? [] : [`hair wait ${config.hairWaitMs} ms${config.hairWaitMs === PHONE_HAIR_WAIT_MS ? ' (phone default)' : ''} (?hairwait=)`]),
    `hair start z ${config.hairStartZ} m${config.hairStartZ === DEFAULT_HAIR_START_Z_M ? '' : ' (?hairz=)'}`,
    `GPU completion gate ${config.sync ? 'on' : 'OFF (?sync=0)'}`,
    `camera exposure ${config.exposure === null ? 'auto (?exposure=312 locks 1/32 s)' : `locked at ${config.exposure} × 100 µs`}`,
    `guard ${config.guard ? 'on' : 'OFF (?guard=0)'}`,
    `continuity cut ${config.continuity ? `on (hair run ≥ ${config.continuityRunPx} px, ?hairrun=)` : 'OFF (?continuity=0)'}`,
    ...(config.diagnostics ? [] : ['diagnostics OFF (?diag=0)']),
  ].join(' · ') + '.';
}
