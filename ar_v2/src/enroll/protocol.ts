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
  /** The beat sequence this scan is running. `BEATS` exactly unless an option
   *  added to it at creation; `advanceProtocol` and `summarise` read only
   *  this, never the module constant, so an optional beat cannot desync the
   *  index from the list it indexes into. */
  beats: BeatSpec[];
  index: number;
  held: number;
  done: BeatId[];
  skipped: BeatId[];
  attempts: number;
  neutral: Neutral | null;
  /**
   * Running sum of the `centre` beat's satisfied frames, averaged into
   * `neutral` by `settleNeutral` when the beat completes or is abandoned.
   *
   * Filled only while `beat.id === 'centre' && neutral === null`, and emptied by
   * nothing — every sample in it passed `beat.satisfied`, so it stays valid
   * however the wearer moves afterwards. It used to be emptied on any off-goal
   * frame, which killed the salvage in `abandon`; see the unsatisfied branch.
   */
  neutralAccum: { yaw: number; pitch: number; distance: number; n: number };
  /**
   * The plateau REFERENCE: the value `sinceImprovement` is counted against.
   *
   * Deliberately not the same thing as `peak`. This one only moves when the
   * wearer genuinely gets further, so "frames since improvement" measures what
   * its name says. Latching it to the current value every frame — which is what
   * this did — turns the whole test into a per-frame velocity gate. See the
   * comment at the improvement test.
   */
  best: number;
  /** The furthest the wearer has actually got in the current beat, for the goal
   *  test and for honest reporting. */
  peak: number;
  /** Frames since `best` last improved by more than the epsilon. */
  sinceImprovement: number;
  /** What each beat actually achieved, for honest reporting. */
  achieved: Partial<Record<BeatId, number>>;
  finished: boolean;
}

/**
 * What a finished scan achieved, small enough to live on the model forever.
 *
 * The model outlives the protocol. It is serialised to localStorage and restored
 * on the next page load straight into `wear`, while `ProtocolState` is rebuilt
 * fresh — so by the time a wearer can press "Copy diagnostics", the scan that
 * produced their model is gone. A real dump came back reading
 * `"In progress — on centre, 0 of 7 done"` beside a model built from 48 frames,
 * which is not a contradiction, just two objects with different lifetimes.
 *
 * That mattered more than a cosmetic wrong line: how far the wearer actually
 * turned, in the scan's own units, is the one measurement needed to calibrate
 * the yaw compression in `docs/OPEN-QUESTIONS.md` Q13, and it was being
 * destroyed before anyone could read it.
 */
export interface ScanRecord {
  readonly done: BeatId[];
  readonly skipped: BeatId[];
  readonly achieved: Partial<Record<BeatId, number>>;
  readonly turnAchievedDeg: number;
  readonly neutral: Neutral | null;
  readonly finished: boolean;
  readonly summary: string;
}

/** Snapshot the protocol so it can be carried by the model that came out of it. */
export function scanRecord(state: ProtocolState): ScanRecord {
  return {
    done: [...state.done],
    skipped: [...state.skipped],
    achieved: { ...state.achieved },
    turnAchievedDeg: achievedTurnDeg(state),
    neutral: state.neutral ? { ...state.neutral } : null,
    finished: state.finished,
    summary: summarise(state),
  };
}

export const createProtocol = (): ProtocolState => ({
  beats: BEATS,
  index: 0, held: 0, done: [], skipped: [], attempts: 0, peak: 0,
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
  state.peak = 0;
  state.sinceImprovement = 0;
}

export function advanceProtocol(state: ProtocolState, sample: PoseSample | null): ProtocolStep {
  if (state.finished) return finishedStep();

  const beat = state.beats[state.index];
  let justCompleted: BeatId | null = null;

  // The give-up timer ticks whether or not there is a face. It used to advance
  // only on frames WITH a pose, so a detector that lost the wearer — which is
  // what a deep turn provokes — froze the protocol with no progress, no give-up
  // and no message.
  state.attempts++;

  // Turn whatever centre frames were collected into the neutral. Safe to call on
  // any beat and at any moment: only `centre` ever fills the accumulator, and
  // with `n === 0` this does nothing at all, which is why `complete` and
  // `abandon` can both call it unconditionally.
  const settleNeutral = () => {
    if (state.neutral === null && state.neutralAccum.n > 0) {
      const a = state.neutralAccum;
      state.neutral = {
        yawDeg: a.yaw / a.n, pitchDeg: a.pitch / a.n, distanceMm: a.distance / a.n,
      };
    }
  };

  const complete = () => {
    state.achieved[beat.id] = state.peak;
    settleNeutral();
    state.done.push(beat.id);
    justCompleted = beat.id;
    state.index++;
    resetBeat(state);
  };

  const abandon = (fallback: PoseSample | null) => {
    // Spend the frames the beat DID see before falling back to anything. A
    // `centre` beat that timed out — the wearer half out of frame, the detector
    // dropping in and out — was throwing away every good centre sample it had
    // collected and then adopting the single pose it happened to hold at the
    // moment it gave up, or nothing at all.
    settleNeutral();
    // **The beat id is the test, not "the neutral is still null".**
    //
    // This guard was `state.neutral === null && fallback` and that is the exact
    // condition the comment at the bottom of `advance` rejects, for the exact
    // reason it gives. The fix had landed at the two sites beside that comment
    // and not at this one, so the defect it describes survived here in full:
    // when `centre` is abandoned with an empty accumulator AND no sample on the
    // give-up frame, the neutral stays null, and then the NEXT beat to time out
    // with a sample latched ITS OWN turned pose as straight-ahead.
    //
    // Reproduced end to end: a wearer who turns the wrong way on `turn-right`
    // has their +50 degree pose adopted as the neutral, after which `turn-left`
    // reports 50 degrees of turn that never happened and both nod beats read
    // |dYaw| = 50 at any centre pose and can only time out. Three further beats
    // fail on a fully co-operative wearer, and the fabricated neutral is
    // persisted into `model.scan` and printed by diagnostics.
    //
    // A null neutral is the honest outcome when centre saw nothing: it is
    // reported as a skipped beat, and `measure` receives null rather than a
    // number invented from an unrelated pose.
    if (beat.id === 'centre' && state.neutral === null && fallback) {
      // Adopting zero would mean measuring every later beat against the camera
      // axis, which is the bug the neutral exists to avoid. Take whatever pose
      // was actually seen instead.
      state.neutral = {
        yawDeg: fallback.yawDeg, pitchDeg: fallback.pitchDeg, distanceMm: fallback.distanceMm,
      };
    }
    state.achieved[beat.id] = state.peak;
    state.skipped.push(beat.id);
    state.index++;
    resetBeat(state);
  };

  if (sample) {
    const value = beat.measure(sample, state.neutral);
    const alsoOk = beat.also ? beat.also(sample, state.neutral) : true;

    // Track the furthest they have got — for `reach`, and for reporting.
    if (value > state.peak) state.peak = value;

    // **The reference must NOT be re-latched on a non-improving frame.**
    //
    // This used to carry `if (value > state.best) state.best = value;` in the
    // else-branch, which meant `best` became the previous frame's value every
    // time the test failed — so the comparison was always against LAST FRAME,
    // never against the value at the last reset. That is a velocity gate, not a
    // stillness test.
    //
    // Turning smoothly at anything under `epsilonDeg` per frame — 1.2 deg at
    // 30 fps is 36 deg/s, which is a brisk turn — never once satisfied the
    // test, so `sinceImprovement` climbed from frame 1 while the head was still
    // moving and the beat completed on patience alone. A real wearer's scan
    // finished the turn beats at about 24 degrees of parallax and reported "no
    // profile view"; this is why.
    //
    // Written correctly, the beat gives up when the rate falls below
    // `epsilonDeg / patience` = 0.06 deg/frame = 1.8 deg/s, which is a head that
    // has genuinely stopped. The bug made that threshold twenty times too fast.
    if (value > state.best + PLATEAU.epsilonDeg) {
      state.best = value;
      state.sinceImprovement = 0;
    } else {
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
        && state.peak >= (beat.minimum ?? 0);
      satisfied = alsoOk && (state.peak >= beat.goal || plateaued);
    }

    if (satisfied) {
      state.held++;
      // **Only `centre` may contribute to the neutral, and the beat id is the
      // test — not "the neutral is still null".**
      //
      // Without the id check, a `centre` beat that the detector lost mid-hold
      // leaves the neutral null and the accumulator open, so the NEXT beat feeds
      // it: the wearer's -50 degree turn latches as "straight ahead". Everything
      // after is measured against a lie. `turn-left` then completes in about 25
      // frames at a true-centre pose while reporting 50 degrees of turn that
      // never happened, and both nod beats' `|dYaw| < 25` guard reads 50 at any
      // centre pose and can only time out.
      if (beat.id === 'centre' && state.neutral === null) {
        const a = state.neutralAccum;
        a.yaw += sample.yawDeg; a.pitch += sample.pitchDeg;
        a.distance += sample.distanceMm; a.n++;
      }
      if (state.held >= beat.holdFrames) complete();
    } else {
      // Decay rather than reset: one bad frame mid-hold is usually a blink.
      state.held = Math.max(0, state.held - 1);
      // **And the accumulator is not touched here at all, which it used to be.**
      //
      // An off-goal frame said "start the average over", and that emptied
      // `neutralAccum` on EVERY unsatisfied centre frame — including the give-up
      // frame directly below, which is unsatisfied by definition. So
      // `settleNeutral` inside `abandon` always found n = 0 on this path and the
      // fallback latched the single pose the wearer happened to hold at the
      // moment the beat gave up. The salvage `abandon` documents — "spend the
      // frames the beat DID see" — could only ever run on the `abandon(null)`
      // path, where no unsatisfied frame occurs because there is no frame at
      // all, and that is the only path the suite covered.
      //
      // Measured: seven good centre frames averaging yaw 2, then a wearer who
      // looks at a second monitor and stays in shot, gave a neutral of yaw 40 —
      // their turned pose adopted as straight-ahead, with the whole cascade this
      // file's other tests pin following from it.
      //
      // The reset was right once and is not any more. It arrived in `f5f5e53`
      // when the accumulator was guarded by `state.neutral === null` alone, so
      // ANY beat could feed it and emptying it on a broken hold limited the
      // damage. `f9c9093` added the `beat.id === 'centre'` test — on this branch
      // and the one above — and the salvage, in the same commit. From then on
      // every sample in here had already passed `beat.satisfied`, so it is
      // inside the centre goal by construction and a later off-goal frame says
      // nothing about it. The reset survived that change and has only destroyed
      // evidence since.
      //
      // It also changes the COMPLETING path, deliberately: the neutral is now
      // the mean of every satisfied centre frame rather than of the final
      // unbroken run. That is the beat's own stated reason for averaging — "a
      // single frame's pose carries a degree of noise and postural wander" —
      // applied to all the evidence instead of the last of it. Identical for a
      // wearer who holds still, because then nothing is ever unsatisfied.
      const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
      if (state.attempts > limit) abandon(sample);
    }
  } else {
    const limit = beat.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;
    if (state.attempts > limit) abandon(null);
  }

  if (state.index >= state.beats.length) state.finished = true;
  if (state.finished) return finishedStep(justCompleted);

  const current = state.beats[state.index];
  const value = sample ? current.measure(sample, state.neutral) : null;
  const limit = current.optional ? GIVE_UP_FRAMES.optional : GIVE_UP_FRAMES.required;

  return {
    beat: current,
    prompt: current.prompt,
    progress: state.index / state.beats.length,
    beatProgress: state.held / current.holdFrames,
    reading: value === null ? null : {
      value, best: state.peak, goal: current.goal, unit: current.unit, kind: current.kind,
      atMost: current.compare === 'atMost',
    },
    toward: value === null ? 0 : towardOf(current, value, state.peak),
    settling: current.kind === 'reach'
      && state.sinceImprovement > PLATEAU.patience * 0.4
      && state.peak >= (current.minimum ?? 0),
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
  // Saying "Full scan." while the scan is still running is a small lie that
  // lands in the diagnostics report, which is exactly where a lie is expensive.
  if (!state.finished) {
    const current = state.beats[state.index];
    return `In progress — on "${current ? current.id : '?'}", `
      + `${state.done.length} of ${state.beats.length} done.`;
  }
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
