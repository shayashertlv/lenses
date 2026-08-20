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
 * signs*. Over fifteen synthetic subjects at three camera geometries, **zero of
 * forty-five** reached the parallax its own estimator needed.
 *
 * A guided scan resolves that contradiction by asking for the angle. A few
 * seconds, once per device, and the wearer is doing something rather than
 * holding still.
 *
 * ## The design rule this file learned the hard way
 *
 * **Do not ask a wearer for a number you cannot calibrate.**
 *
 * The first version asked for "30 degrees" of turn and then "60 degrees" for a
 * profile. A wearer reported that the first completed only after roughly
 * *seventy* degrees of real head turn, and that the second was then physically
 * impossible — there is nowhere left to go. The measured angle is compressed
 * against the physical one, because MediaPipe's landmarks under-rotate as half
 * the face disappears, and nothing in this file changes that.
 *
 * The synthetic harness could not have caught it: it generates landmarks by
 * projecting true geometry, so its measured yaw tracks the truth to within ten
 * percent — it even slightly *over*-reads. Reality does not.
 * (`docs/OPEN-QUESTIONS.md` Q13.)
 *
 * So the turn beats no longer name an angle. They ask the wearer to go **as far
 * as is comfortable** and detect when they have stopped — a question about their
 * own body that they can always answer, that needs no calibration, and that
 * cannot demand the impossible. What the scan actually got is then *reported*
 * rather than *required*.
 *
 * ## The beats, and what each one is for
 *
 * Every beat is here because a specific quantity is unobservable without it.
 *
 *   `centre`  establishes the wearer's own neutral. Everything else is relative
 *             to it, because a camera is not anybody's eye level — v1's
 *             laptop-lid failure, which scored *0 of 600 admitted frames* for a
 *             camera 13.5 degrees below the eyes.
 *   `turn`    as far as they comfortably can, each side. Triangulation baseline
 *             for the whole face, and — if they get far enough — the nose as a
 *             SILHOUETTE against the background, which is the only direct
 *             measurement of its protrusion a single camera can make.
 *   `nod`     down and up. The underside of the nose and the lower sidewall,
 *             which is exactly the strip a pad bears on.
 *   `lean`    in and back. Depth variation is the ONLY thing that makes focal
 *             length observable; without it the field of view has to be assumed.
 */

import { headEuler } from '../core/camera.js';
import type { Pose } from '../core/linalg.js';

export type BeatId = 'centre' | 'turn-right' | 'turn-left'
  | 'nod-down' | 'nod-up' | 'lean-in' | 'lean-back';

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
 * **Every beat after the first is measured relative to this.** v1 shipped the
 * absolute version and measured its cost: a laptop lid 12 cm below the eyes at
 * 50 cm puts a wearer looking straight at their screen at 13.5 degrees of pose
 * pitch, and an absolute square-on test scored **0 of 600 admitted frames** for
 * that entire class of hardware. Its own note is the lesson: *a camera is not
 * anybody's eye level.*
 */
export interface Neutral {
  yawDeg: number;
  pitchDeg: number;
  distanceMm: number;
}

/**
 * How a beat decides it is done.
 *
 * `threshold` — cross a line and hold it. Right for the beats whose quantity is
 *   bounded and calibratable: "look roughly at the camera", "lean in a bit".
 *
 * `reach` — go as far as you can, and finish when you stop. Right for the turn,
 *   where the number the system measures is not the number the wearer controls,
 *   and demanding a specific one is how you end up asking for the impossible.
 */
export type BeatKind = 'threshold' | 'reach';

export interface BeatSpec {
  id: BeatId;
  kind: BeatKind;
  prompt: string;
  /**
   * Where the guide dot should be, in normalised **mirrored display** space.
   *
   * The preview is a mirror, because an unmirrored self-view feels wrong to
   * everybody. So a wearer turning to their own RIGHT sees their face move to
   * the RIGHT of the screen and the dot has to be on that side. The first
   * version put `turn-right` on the screen left — where the face goes in the RAW
   * camera image — so it instructed the opposite of what the prompt said.
   */
  target: { x: number; y: number; scale: number };
  /** The quantity this beat watches, relative to neutral. */
  measure(sample: PoseSample, neutral: Neutral | null): number;
  unit: string;
  /**
   * Which side of `goal` satisfies the beat.
   *
   * Almost everything is `atLeast` — turn further, nod further, lean further.
   * `centre` is the exception: it measures *degrees off centre*, where the goal
   * is a ceiling. Unifying every beat onto `>=` (which an earlier version did)
   * makes the opening beat satisfiable only by NOT looking at the camera, and
   * since it is the beat that establishes the neutral, nothing after it works
   * either.
   */
  compare?: 'atLeast' | 'atMost';
  /**
   * `threshold`: the value that satisfies it.
   * `reach`: the value at which the wearer is certainly done, whatever the
   *   plateau detector thinks. Generous on purpose — someone with a flexible
   *   neck and a well-behaved camera should not be made to wait for a timeout.
   */
  goal: number;
  /** `reach` only: below this, stopping does not count as finishing. */
  minimum?: number;
  /** An extra condition beyond the measured quantity, e.g. stay roughly frontal. */
  also?(sample: PoseSample, neutral: Neutral | null): boolean;
  holdFrames: number;
  optional: boolean;
}

const dYaw = (s: PoseSample, n: Neutral | null): number => s.yawDeg - (n?.yawDeg ?? 0);
const dPitch = (s: PoseSample, n: Neutral | null): number => s.pitchDeg - (n?.pitchDeg ?? 0);

/**
 * How still the maximum has to be before a `reach` beat calls it finished.
 *
 * `patience` frames without improving by `epsilonDeg`. At 30 fps that is about
 * two thirds of a second of getting no further, which is what "that is as far as
 * I go" looks like — long enough not to fire on the pause mid-turn, short enough
 * not to feel like a hang.
 */
const PLATEAU = { patience: 20, epsilonDeg: 1.2 };

export const BEATS: BeatSpec[] = [
  {
    id: 'centre',
    kind: 'threshold',
    prompt: 'Look straight at the camera',
    target: { x: 0.5, y: 0.5, scale: 1 },
    // The only beat measured in absolute terms, because it is the one that
    // defines the reference. Generous on pitch and tight on yaw: a camera can
    // sit far below the eyes (a phone in the lap is 30 degrees) but is rarely
    // far to one side.
    measure: (s) => Math.abs(s.yawDeg),
    unit: 'deg off centre',
    goal: 14,
    compare: 'atMost',
    also: (s) => Math.abs(s.pitchDeg) < 38,
    holdFrames: 8,
    optional: false,
  },
  {
    id: 'turn-right',
    kind: 'reach',
    prompt: 'Turn to your right, as far as is comfortable',
    target: { x: 0.86, y: 0.5, scale: 1 },
    measure: (s, n) => -dYaw(s, n),
    unit: 'deg right',
    goal: 55,
    minimum: 15,
    holdFrames: 5,
    optional: false,
  },
  {
    id: 'turn-left',
    kind: 'reach',
    prompt: 'And to your left, as far as is comfortable',
    target: { x: 0.14, y: 0.5, scale: 1 },
    measure: (s, n) => dYaw(s, n),
    unit: 'deg left',
    goal: 55,
    minimum: 15,
    holdFrames: 5,
    optional: false,
  },
  {
    id: 'nod-down',
    kind: 'threshold',
    prompt: 'Back to centre, then look down a little',
    target: { x: 0.5, y: 0.76, scale: 1 },
    measure: (s, n) => dPitch(s, n),
    unit: 'deg down',
    goal: 9,
    also: (s, n) => Math.abs(dYaw(s, n)) < 25,
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'nod-up',
    kind: 'threshold',
    prompt: 'And up a little',
    target: { x: 0.5, y: 0.24, scale: 1 },
    measure: (s, n) => -dPitch(s, n),
    unit: 'deg up',
    goal: 9,
    also: (s, n) => Math.abs(dYaw(s, n)) < 25,
    holdFrames: 4,
    optional: false,
  },
  {
    id: 'lean-in',
    kind: 'threshold',
    prompt: 'Lean in toward the camera',
    target: { x: 0.5, y: 0.5, scale: 1.5 },
    measure: (s) => (1 - s.distanceRatio) * 100,
    unit: '% closer',
    goal: 20,
    holdFrames: 4,
    optional: true,
  },
  {
    id: 'lean-back',
    kind: 'threshold',
    prompt: 'And lean back',
    target: { x: 0.5, y: 0.5, scale: 0.65 },
    measure: (s) => (s.distanceRatio - 1) * 100,
    unit: '% further',
    goal: 22,
    holdFrames: 4,
    optional: true,
  },
];

export interface ProtocolState {
  index: number;
  held: number;
  done: BeatId[];
  skipped: BeatId[];
  attempts: number;
  neutral: Neutral | null;
  neutralAccum: { yaw: number; pitch: number; distance: number; n: number };
  /** The furthest the wearer has got in the current beat. */
  best: number;
  /** Frames since `best` last improved by more than the epsilon. */
  sinceImprovement: number;
  /** What each beat actually achieved, for honest reporting. */
  achieved: Partial<Record<BeatId, number>>;
  finished: boolean;
}

export const createProtocol = (): ProtocolState => ({
  index: 0, held: 0, done: [], skipped: [], attempts: 0,
  neutral: null,
  neutralAccum: { yaw: 0, pitch: 0, distance: 0, n: 0 },
  best: 0, sinceImprovement: 0, achieved: {},
  finished: false,
});

/**
 * Frames to spend on one beat before moving on.
 *
 * The scan must never hang. A wearer who cannot or will not turn gets a shorter
 * scan and an honestly-degraded model, not a spinner. Same instinct as v1's
 * "keep previous, never assume average" — refuse to invent, never refuse to
 * proceed.
 */
const GIVE_UP_FRAMES = { required: 240, optional: 100 };

export interface ProtocolStep {
  beat: BeatSpec | null;
  prompt: string;
  progress: number;
  /** 0..1 within the current beat's hold. */
  beatProgress: number;
  /** What the beat is watching and what it wants. Null with no face. */
  reading: {
    value: number; best: number; goal: number; unit: string; kind: BeatKind;
    /** True when the goal is a ceiling rather than a floor. */
    atMost: boolean;
  } | null;
  /** 0..1 toward the goal. A `reach` beat can finish well below 1, which is
   *  exactly the point. */
  toward: number;
  /** A `reach` beat that has seen the wearer stop and is confirming it. */
  settling: boolean;
  struggling: boolean;
  justCompleted: BeatId | null;
  finished: boolean;
}

function resetBeat(state: ProtocolState): void {
  state.held = 0;
  state.attempts = 0;
  state.best = 0;
  state.sinceImprovement = 0;
}

export function advanceProtocol(state: ProtocolState, sample: PoseSample | null): ProtocolStep {
  if (state.finished) return finishedStep();

  const beat = BEATS[state.index];
  let justCompleted: BeatId | null = null;

  // The give-up timer ticks whether or not there is a face. It used to advance
  // only on frames WITH a pose, so a detector that lost the wearer — which is
  // what a deep turn provokes — froze the protocol with no progress, no give-up
  // and no message.
  state.attempts++;

  const settleNeutral = () => {
    if (state.neutral === null && state.neutralAccum.n > 0) {
      const a = state.neutralAccum;
      state.neutral = {
        yawDeg: a.yaw / a.n, pitchDeg: a.pitch / a.n, distanceMm: a.distance / a.n,
      };
    }
  };

  const complete = () => {
    state.achieved[beat.id] = state.best;
    settleNeutral();
    state.done.push(beat.id);
    justCompleted = beat.id;
    state.index++;
    resetBeat(state);
  };

  const abandon = (fallback: PoseSample | null) => {
    if (state.neutral === null && fallback) {
      // Adopting zero would mean measuring every later beat against the camera
      // axis, which is the bug the neutral exists to avoid. Take whatever pose
      // was actually seen instead.
      state.neutral = {
        yawDeg: fallback.yawDeg, pitchDeg: fallback.pitchDeg, distanceMm: fallback.distanceMm,
      };
    }
    state.achieved[beat.id] = state.best;
    state.skipped.push(beat.id);
    state.index++;
    resetBeat(state);
  };

  if (sample) {
    const value = beat.measure(sample, state.neutral);
    const alsoOk = beat.also ? beat.also(sample, state.neutral) : true;

    // Track the furthest they have got — for `reach`, and for reporting.
    if (value > state.best + PLATEAU.epsilonDeg) {
      state.best = value;
      state.sinceImprovement = 0;
    } else {
      if (value > state.best) state.best = value;
      state.sinceImprovement++;
    }

    const meetsGoal = beat.compare === 'atMost' ? value <= beat.goal : value >= beat.goal;

    let satisfied: boolean;
    if (beat.kind === 'threshold') {
      satisfied = meetsGoal && alsoOk;
    } else {
      // Done when they reach a generous goal, OR when they have plainly stopped
      // somewhere useful. The second clause is what makes this beat completable
      // by everybody rather than only by the flexible.
      const plateaued = state.sinceImprovement >= PLATEAU.patience
        && state.best >= (beat.minimum ?? 0);
      satisfied = alsoOk && (state.best >= beat.goal || plateaued);
    }

    if (satisfied) {
      state.held++;
      if (state.neutral === null) {
        const a = state.neutralAccum;
        a.yaw += sample.yawDeg; a.pitch += sample.pitchDeg;
        a.distance += sample.distanceMm; a.n++;
      }
      if (state.held >= beat.holdFrames) complete();
    } else {
      // Decay rather than reset: one bad frame mid-hold is usually a blink.
      state.held = Math.max(0, state.held - 1);
      if (state.neutral === null) {
        state.neutralAccum = { yaw: 0, pitch: 0, distance: 0, n: 0 };
      }
      const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
      if (state.attempts > limit) abandon(sample);
    }
  } else {
    const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
    if (state.attempts > limit) abandon(null);
  }

  if (state.index >= BEATS.length) state.finished = true;
  if (state.finished) return finishedStep(justCompleted);

  const current = BEATS[state.index];
  const value = sample ? current.measure(sample, state.neutral) : null;
  const limit = current.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;

  return {
    beat: current,
    prompt: current.prompt,
    progress: state.index / BEATS.length,
    beatProgress: state.held / current.holdFrames,
    reading: value === null ? null : {
      value, best: state.best, goal: current.goal, unit: current.unit, kind: current.kind,
      atMost: current.compare === 'atMost',
    },
    toward: value === null ? 0 : towardOf(current, value, state.best),
    settling: current.kind === 'reach'
      && state.sinceImprovement > PLATEAU.patience * 0.4
      && state.best >= (current.minimum ?? 0),
    struggling: state.attempts > limit * 0.5,
    justCompleted,
    finished: false,
  };
}

/**
 * How far along the current beat the wearer is, 0..1.
 *
 * For an `atMost` beat this counts DOWN toward the goal, so the dot grows as
 * they get closer to centre rather than as they wander away from it.
 */
function towardOf(beat: BeatSpec, value: number, best: number): number {
  if (beat.compare === 'atMost') {
    if (!(beat.goal > 0)) return value <= 0 ? 1 : 0;
    return Math.max(0, Math.min(1, 1 - (value - beat.goal) / (beat.goal * 3)));
  }
  return Math.max(0, Math.min(1, Math.max(value, best) / beat.goal));
}

const finishedStep = (justCompleted: BeatId | null = null): ProtocolStep => ({
  beat: null,
  prompt: 'Working out your measurements…',
  progress: 1, beatProgress: 1, reading: null, toward: 1,
  settling: false, struggling: false, justCompleted, finished: true,
});

/**
 * Turns a solved pose into the sample the protocol reads.
 *
 * `headEuler`, never `eulerYXZ(pose.R)`. The raw camera-frame euler reports a
 * frontal face at yaw = -180 and inverts the sign of yaw and roll, which made
 * this protocol impossible to finish twice over.
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

/** The largest turn the wearer managed, in the scan's own measured degrees. */
export const achievedTurnDeg = (state: ProtocolState): number =>
  Math.max(state.achieved['turn-right'] ?? 0, state.achieved['turn-left'] ?? 0);

/**
 * What the scan actually got, in the scan's own units.
 *
 * Reported rather than required. The turn beats ask for "as far as comfortable"
 * precisely because the number the system measures is not the number the wearer
 * controls, so the achieved figure is data about this session — useful for
 * telling the wearer which of their measurements is soft, and for calibrating
 * against real captures once there are any.
 */
export function summarise(state: ProtocolState): string {
  const turn = achievedTurnDeg(state);
  const parts: string[] = [];
  if (turn > 0) parts.push(`turned to ${turn.toFixed(0)} deg as measured`);
  if (state.skipped.length) parts.push(`skipped: ${state.skipped.join(', ')}`);
  if (state.skipped.some((b) => b.startsWith('lean'))) {
    parts.push('camera field of view assumed rather than solved');
  }
  if (turn > 0 && turn < 35) parts.push('nose depth is inferred rather than seen');
  return parts.length ? `${parts.join('. ')}.` : 'Full scan.';
}
