/**
 * The guided scan, as a state machine.
 *
 * ## Why there is a scan at all
 *
 * v1's proudest property was that it had no scan phase: *"the first detection is
 * already a fit"*. That is a genuinely good experience and it is why v2 keeps
 * the fallback — an unscanned wearer still gets a frame on their face
 * immediately, marked as an estimate.
 *
 * But it is also the root of both problems v2 exists to fix. Without a scan
 * there is no parallax, without parallax there is no depth, and without depth
 * the nose is the average one forever. v1's own audit found the reason it could
 * never escape: *parallax and pose trust are the same angle with opposite
 * signs* — turning the head buys `sin²θ` of triangulation and costs `wPose²` of
 * trust, and the trust term won. Over fifteen synthetic subjects at three camera
 * geometries, **zero of forty-five** reached the parallax its own estimator
 * needed.
 *
 * A guided scan resolves that contradiction by simply asking for the angle. Four
 * seconds, once per device, and the wearer is doing something rather than
 * holding still.
 *
 * ## The beats, and what each one is for
 *
 * Every beat is here because a specific quantity is unobservable without it.
 * None of them is padding:
 *
 *   `turn`     ±35 degrees of yaw. Triangulation baseline for the whole face.
 *              Without it, depth is the template's.
 *   `profile`  a hold near 80 degrees, each side. The nose becomes a SILHOUETTE
 *              against the background, which is the only direct measurement of
 *              its protrusion a single camera can make. Measured worth: nose
 *              surface error 0.77 mm with it against 0.92 mm without, and the
 *              worst-case protrusion error nearly halves.
 *   `nod`      ±15 degrees of pitch. The underside of the nose and the lower
 *              sidewall, which is exactly the strip a pad bears on.
 *   `lean`     in and back. Depth variation is the ONLY thing that makes focal
 *              length observable, and without it the camera's field of view has
 *              to be assumed. Measured worth: PD error 1.8 mm with it against
 *              9.2 mm without.
 */

import { eulerYXZ, type Pose } from '../core/linalg.js';

export type BeatId = 'centre' | 'turn-right' | 'turn-left' | 'profile-right'
  | 'profile-left' | 'nod-down' | 'nod-up' | 'lean-in' | 'lean-back';

export interface BeatSpec {
  id: BeatId;
  /** What the wearer is asked to do. */
  prompt: string;
  /** Where the guide dot should be, in normalised screen coordinates. */
  target: { x: number; y: number; scale: number };
  /** The pose condition that counts as satisfying this beat. */
  satisfied(sample: PoseSample): boolean;
  /** Frames at the target before the beat is complete. */
  holdFrames: number;
  /** Beats that can be skipped without invalidating the scan. */
  optional: boolean;
}

export interface PoseSample {
  yawDeg: number;
  pitchDeg: number;
  rollDeg: number;
  /** Distance, as a ratio of the first frame's. */
  distanceRatio: number;
}

export const BEATS: BeatSpec[] = [
  {
    id: 'centre',
    prompt: 'Look straight at the camera',
    target: { x: 0.5, y: 0.5, scale: 1 },
    satisfied: (s) => Math.abs(s.yawDeg) < 8 && Math.abs(s.pitchDeg) < 10,
    holdFrames: 8,
    optional: false,
  },
  {
    id: 'turn-right',
    prompt: 'Slowly turn to your right',
    target: { x: 0.18, y: 0.5, scale: 1 },
    satisfied: (s) => s.yawDeg <= -30,
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'profile-right',
    prompt: 'Keep going — show me your profile',
    target: { x: 0.06, y: 0.5, scale: 1 },
    satisfied: (s) => s.yawDeg <= -65,
    holdFrames: 6,
    optional: true,
  },
  {
    id: 'turn-left',
    prompt: 'Now slowly to your left',
    target: { x: 0.82, y: 0.5, scale: 1 },
    satisfied: (s) => s.yawDeg >= 30,
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'profile-left',
    prompt: 'And your profile on this side',
    target: { x: 0.94, y: 0.5, scale: 1 },
    satisfied: (s) => s.yawDeg >= 65,
    holdFrames: 6,
    optional: true,
  },
  {
    id: 'nod-down',
    prompt: 'Back to centre, then look down a little',
    target: { x: 0.5, y: 0.78, scale: 1 },
    satisfied: (s) => Math.abs(s.yawDeg) < 20 && s.pitchDeg >= 12,
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'nod-up',
    prompt: 'And up a little',
    target: { x: 0.5, y: 0.22, scale: 1 },
    satisfied: (s) => Math.abs(s.yawDeg) < 20 && s.pitchDeg <= -12,
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'lean-in',
    prompt: 'Lean in toward the camera',
    target: { x: 0.5, y: 0.5, scale: 1.5 },
    satisfied: (s) => s.distanceRatio <= 0.75,
    holdFrames: 4,
    optional: true,
  },
  {
    id: 'lean-back',
    prompt: 'And lean back',
    target: { x: 0.5, y: 0.5, scale: 0.65 },
    satisfied: (s) => s.distanceRatio >= 1.3,
    holdFrames: 4,
    optional: true,
  },
];

export interface ProtocolState {
  index: number;
  held: number;
  /** Completed beats, in order. */
  done: BeatId[];
  /** Beats given up on. */
  skipped: BeatId[];
  /** Frames spent on the current beat, for the give-up timer. */
  attempts: number;
  referenceDistance: number | null;
  finished: boolean;
}

export const createProtocol = (): ProtocolState => ({
  index: 0, held: 0, done: [], skipped: [], attempts: 0,
  referenceDistance: null, finished: false,
});

/**
 * Frames to spend on one beat before moving on.
 *
 * Optional beats give up sooner. The scan must never stall: a wearer who cannot
 * or will not turn far enough gets a shorter scan and an honestly-degraded
 * model, not a spinner. This is the same instinct as v1's "keep previous, never
 * assume average" — refuse to invent, but never refuse to proceed.
 */
const GIVE_UP_FRAMES = { required: 210, optional: 90 };

export interface ProtocolStep {
  beat: BeatSpec | null;
  prompt: string;
  /** 0..1 across the whole protocol. */
  progress: number;
  /** 0..1 within the current beat. */
  beatProgress: number;
  justCompleted: BeatId | null;
  finished: boolean;
}

export function advanceProtocol(state: ProtocolState, sample: PoseSample | null): ProtocolStep {
  if (state.finished) {
    return {
      beat: null, prompt: 'Working out your measurements…',
      progress: 1, beatProgress: 1, justCompleted: null, finished: true,
    };
  }

  const beat = BEATS[state.index];
  let justCompleted: BeatId | null = null;

  if (sample) {
    if (state.referenceDistance === null) state.referenceDistance = 1;
    state.attempts++;
    if (beat.satisfied(sample)) {
      state.held++;
      if (state.held >= beat.holdFrames) {
        state.done.push(beat.id);
        justCompleted = beat.id;
        state.index++;
        state.held = 0;
        state.attempts = 0;
      }
    } else {
      // Decay rather than reset: a single bad frame in the middle of a hold is
      // usually a blink, not a wearer who moved away.
      state.held = Math.max(0, state.held - 1);
      const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
      if (state.attempts > limit) {
        state.skipped.push(beat.id);
        state.index++;
        state.held = 0;
        state.attempts = 0;
      }
    }
  }

  if (state.index >= BEATS.length) state.finished = true;

  const current = state.finished ? null : BEATS[state.index];
  return {
    beat: current,
    prompt: current ? current.prompt : 'Working out your measurements…',
    progress: state.index / BEATS.length,
    beatProgress: current ? state.held / current.holdFrames : 1,
    justCompleted,
    finished: state.finished,
  };
}

/** Turns a solved pose into the sample the protocol reads. */
export function sampleFromPose(pose: Pose, referenceDistance: number | null): PoseSample {
  const e = eulerYXZ(pose.R);
  const deg = 180 / Math.PI;
  return {
    yawDeg: e.yaw * deg,
    pitchDeg: e.pitch * deg,
    rollDeg: e.roll * deg,
    distanceRatio: referenceDistance ? pose.t[2] / referenceDistance : 1,
  };
}

/** A one-line summary of what the scan actually got, for the report and the UI. */
export function summarise(state: ProtocolState): string {
  if (state.skipped.length === 0) return 'Full scan.';
  return `Skipped: ${state.skipped.join(', ')}. ` + (
    state.skipped.some((b) => b.startsWith('profile'))
      ? 'Nose depth is inferred rather than seen.'
      : state.skipped.some((b) => b.startsWith('lean'))
        ? 'Camera field of view assumed rather than solved.'
        : 'Some measurements will be softer than usual.'
  );
}
