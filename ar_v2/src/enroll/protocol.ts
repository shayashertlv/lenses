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

import { headEuler } from '../core/camera.js';
import type { Pose } from '../core/linalg.js';

export type BeatId = 'centre' | 'turn-right' | 'turn-left' | 'profile-right'
  | 'profile-left' | 'nod-down' | 'nod-up' | 'lean-in' | 'lean-back';

export interface BeatSpec {
  id: BeatId;
  /** What the wearer is asked to do. */
  prompt: string;
  /**
   * Where the guide dot should be, in normalised **mirrored display** space.
   *
   * The preview is a mirror (`#stage` is CSS-flipped), because an unmirrored
   * self-view feels wrong to everybody. So a wearer turning to their own RIGHT
   * sees their face move to the RIGHT of the screen, and the dot has to be on
   * that side. The first version of this table put `turn-right` at x = 0.18 —
   * screen left — which is where the face goes in the RAW camera image, and it
   * actively instructed the wearer to turn the wrong way while the prompt text
   * told them the right way.
   */
  target: { x: number; y: number; scale: number };
  /**
   * Whether this sample satisfies the beat.
   *
   * `neutral` is null only during the opening beat, which is the one that
   * establishes it; every other beat can rely on it.
   */
  satisfied(sample: PoseSample, neutral: Neutral | null): boolean;
  /**
   * What this beat is watching, right now, against what it needs.
   *
   * Purely for the wearer and for anyone diagnosing a stuck scan. Without it,
   * "the scan is stuck" and "you are nearly there" look identical from the
   * outside — which cost two round trips of a wearer reporting a stall with no
   * way to say what the scan thought it was seeing.
   */
  measure(sample: PoseSample, neutral: Neutral | null): { value: number; target: number; unit: string };
  /** Frames at the target before the beat is complete. */
  holdFrames: number;
  /** Beats that can be skipped without invalidating the scan. */
  optional: boolean;
}

export interface PoseSample {
  yawDeg: number;
  pitchDeg: number;
  rollDeg: number;
  /** Camera-space distance to the head, mm. */
  distanceMm: number;
  /** Distance, as a ratio of the neutral hold's. */
  distanceRatio: number;
}

/**
 * The wearer's own neutral, learned during the opening beat.
 *
 * **Every beat after the first is measured RELATIVE to this**, and that is not a
 * refinement — it is the difference between a protocol that works and one that
 * only works for people whose camera is at eye level.
 *
 * v1 shipped exactly this bug and measured its cost: a laptop lid 12 cm below
 * the eyes at 50 cm puts a wearer who is looking straight at their screen at
 * `atan(12/50) = 13.5` degrees of *pose pitch*, and v1's square-on test — an
 * absolute cone about the camera axis — scored **0 of 600 admitted frames** for
 * that entire class of hardware. Its own note is the lesson: *"a camera is not
 * anybody's eye level."*
 *
 * An absolute `pitch >= 12` for "look down" repeats it. On a laptop the wearer
 * is already there before they move; on a phone in the lap (30 degrees) they
 * satisfy "look down" while looking straight ahead and can never satisfy "look
 * up". Relative to their own neutral, both work and neither needs to know what
 * kind of device this is.
 *
 * Yaw is also carried, for the same reason at a smaller magnitude: a camera
 * offset to one side, or a wearer sitting at an angle to it.
 */
export interface Neutral {
  yawDeg: number;
  pitchDeg: number;
  distanceMm: number;
}

/** Relative to neutral. Null neutral means "not established yet", which only
 *  happens during the opening beat. */
const dYaw = (s: PoseSample, n: Neutral | null): number => s.yawDeg - (n?.yawDeg ?? 0);
const dPitch = (s: PoseSample, n: Neutral | null): number => s.pitchDeg - (n?.pitchDeg ?? 0);

export const BEATS: BeatSpec[] = [
  {
    id: 'centre',
    prompt: 'Look straight at the camera',
    target: { x: 0.5, y: 0.5, scale: 1 },
    // The only beat measured in absolute terms, because it is the one that
    // defines the reference. The bounds are generous on pitch and tight on yaw:
    // a camera can be far below the eyes (a phone in the lap is 30 degrees) but
    // is rarely far to one side, and a wearer who is genuinely turned 20 degrees
    // away is not looking at the camera.
    satisfied: (s) => Math.abs(s.yawDeg) < 14 && Math.abs(s.pitchDeg) < 38,
    measure: (s) => ({ value: Math.abs(s.yawDeg), target: 14, unit: 'deg off centre' }),
    holdFrames: 8,
    optional: false,
  },
  {
    id: 'turn-right',
    prompt: 'Slowly turn to your right',
    target: { x: 0.82, y: 0.5, scale: 1 },
    satisfied: (s, n) => dYaw(s, n) <= -30,
    measure: (s, n) => ({ value: -dYaw(s, n), target: 30, unit: 'deg right' }),
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'profile-right',
    prompt: 'Keep going — show me your profile',
    target: { x: 0.94, y: 0.5, scale: 1 },
    satisfied: (s, n) => dYaw(s, n) <= -60,
    measure: (s, n) => ({ value: -dYaw(s, n), target: 60, unit: 'deg right' }),
    holdFrames: 6,
    optional: true,
  },
  {
    id: 'turn-left',
    prompt: 'Now slowly to your left',
    target: { x: 0.18, y: 0.5, scale: 1 },
    satisfied: (s, n) => dYaw(s, n) >= 30,
    measure: (s, n) => ({ value: dYaw(s, n), target: 30, unit: 'deg left' }),
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'profile-left',
    prompt: 'And your profile on this side',
    target: { x: 0.06, y: 0.5, scale: 1 },
    satisfied: (s, n) => dYaw(s, n) >= 60,
    measure: (s, n) => ({ value: dYaw(s, n), target: 60, unit: 'deg left' }),
    holdFrames: 6,
    optional: true,
  },
  {
    id: 'nod-down',
    prompt: 'Back to centre, then look down a little',
    target: { x: 0.5, y: 0.78, scale: 1 },
    satisfied: (s, n) => Math.abs(dYaw(s, n)) < 25 && dPitch(s, n) >= 10,
    measure: (s, n) => ({ value: dPitch(s, n), target: 10, unit: 'deg down' }),
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'nod-up',
    prompt: 'And up a little',
    target: { x: 0.5, y: 0.22, scale: 1 },
    satisfied: (s, n) => Math.abs(dYaw(s, n)) < 25 && dPitch(s, n) <= -10,
    measure: (s, n) => ({ value: -dPitch(s, n), target: 10, unit: 'deg up' }),
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'lean-in',
    prompt: 'Lean in toward the camera',
    target: { x: 0.5, y: 0.5, scale: 1.5 },
    satisfied: (s) => s.distanceRatio <= 0.78,
    measure: (s) => ({ value: (1 - s.distanceRatio) * 100, target: 22, unit: '% closer' }),
    holdFrames: 4,
    optional: true,
  },
  {
    id: 'lean-back',
    prompt: 'And lean back',
    target: { x: 0.5, y: 0.5, scale: 0.65 },
    satisfied: (s) => s.distanceRatio >= 1.25,
    measure: (s) => ({ value: (s.distanceRatio - 1) * 100, target: 25, unit: '% further' }),
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
  /** The wearer's own neutral, set when the opening beat completes. */
  neutral: Neutral | null;
  /** Running mean of the samples seen during the opening beat, for `neutral`. */
  neutralAccum: { yaw: number; pitch: number; distance: number; n: number };
  finished: boolean;
}

export const createProtocol = (): ProtocolState => ({
  index: 0, held: 0, done: [], skipped: [], attempts: 0,
  neutral: null,
  neutralAccum: { yaw: 0, pitch: 0, distance: 0, n: 0 },
  finished: false,
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
  /** 0..1 within the current beat's HOLD. */
  beatProgress: number;
  /** What the beat is watching and what it needs, for the wearer. Null when
   *  there is no face to measure. */
  reading: { value: number; target: number; unit: string } | null;
  /** 0..1 toward the target angle — how far the wearer has got, as opposed to
   *  how long they have held it. */
  toward: number;
  /** True once this beat has been tried long enough that it is about to be
   *  given up on. Lets the UI say so rather than looking frozen. */
  struggling: boolean;
  justCompleted: BeatId | null;
  finished: boolean;
}

export function advanceProtocol(state: ProtocolState, sample: PoseSample | null): ProtocolStep {
  if (state.finished) {
    return {
      beat: null, prompt: 'Working out your measurements…',
      progress: 1, beatProgress: 1, reading: null, toward: 1,
      struggling: false, justCompleted: null, finished: true,
    };
  }

  const beat = BEATS[state.index];
  let justCompleted: BeatId | null = null;

  // The give-up timer ticks whether or not there is a face.
  //
  // It used to advance only on frames with a pose, so a detector that lost the
  // wearer — which is exactly what happens at a profile hold — froze the
  // protocol indefinitely: no progress, no give-up, no message. A scan must
  // never stall, and "never" has to include the case where it cannot see.
  if (!sample) state.attempts++;

  if (sample) {
    state.attempts++;
    if (beat.satisfied(sample, state.neutral)) {
      state.held++;
      // While holding the opening beat, average what we see. A single frame's
      // pose carries a degree of noise and postural wander; the reference every
      // later beat is measured against should not.
      if (state.neutral === null) {
        const a = state.neutralAccum;
        a.yaw += sample.yawDeg; a.pitch += sample.pitchDeg;
        a.distance += sample.distanceMm; a.n++;
      }
      if (state.held >= beat.holdFrames) {
        if (state.neutral === null && state.neutralAccum.n > 0) {
          const a = state.neutralAccum;
          state.neutral = {
            yawDeg: a.yaw / a.n, pitchDeg: a.pitch / a.n, distanceMm: a.distance / a.n,
          };
        }
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
      if (state.neutral === null) {
        state.neutralAccum = { yaw: 0, pitch: 0, distance: 0, n: 0 };
      }
      const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
      if (state.attempts > limit) {
        // Giving up on the OPENING beat means there is no neutral, and every
        // later beat would then be measured against zero — i.e. against the
        // camera axis, which is the bug this whole mechanism exists to avoid.
        // So a skipped opening beat adopts whatever pose was actually seen.
        if (state.neutral === null) {
          state.neutral = {
            yawDeg: sample.yawDeg, pitchDeg: sample.pitchDeg, distanceMm: sample.distanceMm,
          };
        }
        state.skipped.push(beat.id);
        state.index++;
        state.held = 0;
        state.attempts = 0;
      }
    }
  }

  // A beat can also be abandoned with no face at all, which is the branch the
  // block above exists for.
  if (sample === null && !state.finished) {
    const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
    if (state.attempts > limit) {
      if (state.neutral === null) {
        // No usable pose was ever seen for the opening beat. Adopting zero here
        // would mean measuring every later beat against the camera axis, so the
        // neutral stays null and the beats fall back to absolute angles — worse,
        // but honest, and reported through `summarise`.
        state.skipped.push(beat.id);
      } else {
        state.skipped.push(beat.id);
      }
      state.index++;
      state.held = 0;
      state.attempts = 0;
    }
  }

  if (state.index >= BEATS.length) state.finished = true;

  const current = state.finished ? null : BEATS[state.index];
  const reading = current && sample ? current.measure(sample, state.neutral) : null;
  const limit = current
    ? (current.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required)
    : 1;
  return {
    beat: current,
    prompt: current ? current.prompt : 'Working out your measurements…',
    progress: state.index / BEATS.length,
    beatProgress: current ? state.held / current.holdFrames : 1,
    reading,
    toward: reading && reading.target !== 0
      ? Math.max(0, Math.min(1, reading.value / reading.target))
      : 0,
    struggling: state.attempts > limit * 0.45,
    justCompleted,
    finished: state.finished,
  };
}

/**
 * Turns a solved pose into the sample the protocol reads.
 *
 * `headEuler`, never `eulerYXZ(pose.R)` — see the note on `headEuler`. Using the
 * raw camera-frame euler here is what made this protocol impossible to finish:
 * a frontal face reported yaw = -180, so every beat with a `|yaw| < k` clause
 * was unsatisfiable and the wearer sat looking down at a prompt that would never
 * advance.
 */
export function sampleFromPose(pose: Pose, neutral: Neutral | null): PoseSample {
  const e = headEuler(pose);
  const deg = 180 / Math.PI;
  const distanceMm = pose.t[2];
  return {
    yawDeg: e.yaw * deg,
    pitchDeg: e.pitch * deg,
    rollDeg: e.roll * deg,
    distanceMm,
    distanceRatio: neutral && neutral.distanceMm > 1 ? distanceMm / neutral.distanceMm : 1,
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
