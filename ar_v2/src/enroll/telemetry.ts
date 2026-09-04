/**
 * A capture, on disk: what the bundle saw, so it can be solved again later.
 *
 * ## Why this is not a port of v1's recorder
 *
 * `docs/HANDOFF.md` budgeted a "~150-line rewrite" of `ar/tests/record-telemetry.js`
 * against v2's modules, and the rewrite turned out to be a smaller and different
 * thing than that, for a reason worth writing down: **v1's fixture format does
 * not describe anything v2 consumes.**
 *
 * v1 recorded one line per frame of `{ t, m:[16], l:[478x3 normalised] }` — the
 * MediaPipe transformation matrix and normalised 3D landmarks. v2's detector
 * returns landmarks in **pixels, 2 per landmark**, and never asks MediaPipe for
 * a matrix at all (`render/scene.ts` records why: "v2 does not consume it").
 * Replaying a v1 fixture through v2 would mean converting a format v2 has no
 * use for into one it does, through the very estimator under test.
 *
 * So this records what `enroll/bundle.ts` actually takes: `BundleFrame` minus
 * the pose it refines in place. `app.collected` is already exactly that array,
 * accumulated by the app's own guided scan, so the recording path is the
 * shipping path and there is no second protocol to keep in step. That is the
 * same rule `fit/frame-layout.ts` exists to enforce, applied to capture.
 *
 * Seven of v1's ten imports were for a live stillness meter, which its own
 * header says recording does not depend on — "a session where the model fails
 * to load still records". None of that is here.
 *
 * ## The format
 *
 *     line 0   the header: subject, date, image size, intrinsics, the wearer's
 *              own PD if they typed one, whether an ID-1 card was in frame
 *     line N   one SCAN frame: { beat, l:[...], s:[...], v:[...] }
 *     line M   one WEAR frame: { w:1, dt, l:[...], e:[12]|null, r:[12]|null }
 *
 * ## Why the wear frames carry so much less
 *
 * A scan frame records `sigmaPx` and `visibility` because the bundle is handed
 * them. The wear path is not: `app/main.ts` derives both from the landmarks and
 * the previous pose through `track/profile.ts`'s `shippedSigma`, so a replay
 * that is given the landmarks and `dt` reproduces the ENTIRE shipped per-frame
 * path — the uncertainty estimator included — rather than being handed its
 * output. Recording sigma would replace the thing under test with a fixture of
 * itself.
 *
 * `e` and `r` are the poses the app emitted and solved for that frame, and they
 * are not inputs. They are the **check**: replaying the landmarks must
 * reproduce them, and a replay that does not is not a replay of the session.
 * Twelve numbers each, row-major R then t.
 *
 * Landmarks at 3 decimals of a pixel — a thousandth of a pixel is four orders
 * below detector noise and keeps the file small. Sigmas and visibilities at 4,
 * because both are compared against thresholds rather than differenced.
 *
 * **No pixels, ever.** Landmarks and sigmas only; nothing here can reconstruct
 * a face image, and nothing uploads. `docs/PRIVACY.md` is the promise this file
 * has to keep.
 */

import type { BundleFrame } from './bundle.js';
import type { Intrinsics } from '../core/camera.js';
import { poseIdentity, type Pose } from '../core/linalg.js';

/** Decimal places for landmark pixels. See the header. */
const LANDMARK_DP = 3;
/**
 * Wear landmarks get an extra digit, and it is not cosmetic.
 *
 * A scan frame is one of hundreds feeding a least-squares solve, where a
 * thousandth of a pixel is four orders below the noise. A WEAR frame is
 * replayed through a tracker whose gates, basin audit and stillness latch are
 * threshold decisions, so the last digit can flip a branch and the pose after
 * it. Measured on the first real session: perturbing landmarks by half of
 * `LANDMARK_DP`'s precision moved the emitted pose by up to 0.029 mm, which was
 * the floor the replay's fidelity check could reach. At four decimals that
 * floor is a tenth of it.
 */
const WEAR_LANDMARK_DP = 4;
const WEIGHT_DP = 4;

export interface CaptureHeader {
  /** Format version. Bumped when a reader would otherwise mis-read an old file. */
  v: 1 | 2;
  /** Whatever the wearer called themselves. Not an identity. */
  subject: string;
  /** ISO date, passed in rather than read from a clock, so a replay is reproducible. */
  date: string;
  width: number;
  height: number;
  intrinsics: Intrinsics;
  /** Whether those intrinsics were solved or assumed — the difference matters. */
  intrinsicsSolved: boolean;
  /**
   * The wearer's own measured PD in millimetres, if they have one.
   *
   * The whole point of a capture session. `docs/HANDOFF.md` records a **6.7 mm
   * PD disagreement across three captures of one person**, which is a scale
   * disagreement no synthetic population can settle: only a real face with a
   * known ruler can. Null when nobody measured.
   */
  knownPdMm: number | null;
  /**
   * Whether an ID-1 card was held in frame during the capture.
   *
   * **There is no card branch to feed.** `enroll/card.ts` left the working
   * tree at `f9c9093` and was never a tracked file, so no commit holds it, and
   * `scale.ts` says so on its own rung list: the ID-1 card was
   * built, measured at 0.80% median scale error against the pooled iris's 5.14%
   * on the same synthetic runs, and taken OUT -- see `docs/SCALE.md`. This flag
   * survives as a property of the CAPTURE, so that a recording made with a card
   * in frame is identifiable if the rung is ever rebuilt. It reaches no
   * estimator today.
   */
  card: boolean;
  /** Free text: lighting, camera, distance, anything a reader would want. */
  note: string;
  /** How many SCAN frames follow. */
  frames: number;
  /**
   * How many WEAR frames follow them. Absent on a v1 file, which is the only
   * difference a reader has to care about.
   */
  wear?: number;
}

/**
 * One frame of the WEAR phase — the half of the pipeline a wearer actually
 * watches, and the half nothing in this tree could replay until 2026-09-04.
 */
export interface WearFrame {
  /** Detector landmarks in SOURCE pixels, 2 per landmark. NaN where absent. */
  landmarks: Float64Array;
  /**
   * Seconds since `track()` was last CALLED — `FrameLock.captureDt`, which is
   * what the app passes and not the consumed-frame interval. See
   * `TrackInput.dt`, which documents why the two differ and why a caller that
   * pre-adds a dropout gap would have it counted twice.
   */
  dt: number;
  /** The pose the app rendered: smoothed. Null on a frame the tracker refused. */
  emitted: Pose | null;
  /** The raw solve behind it, before the filter. Null on a refused frame. */
  raw: Pose | null;
}

export interface Capture {
  header: CaptureHeader;
  frames: Omit<BundleFrame, 'pose'>[];
  /** Empty on a v1 file and on any session that never left the scan. */
  wear: WearFrame[];
}

const round = (x: number, dp: number): number => {
  if (!Number.isFinite(x)) return x;
  const f = 10 ** dp;
  return Math.round(x * f) / f;
};

/**
 * NDJSON, one frame per line.
 *
 * Line-per-frame rather than one JSON document because a capture that is cut
 * short — a browser tab closed, a camera unplugged — is still readable up to
 * the last complete line, and a truncated JSON document is not readable at all.
 */
const poseOut = (p: Pose | null): number[] | null =>
  (p ? [...Array.from(p.R, (x) => round(x, 6)), ...Array.from(p.t, (x) => round(x, 4))] : null);

export function serializeCapture(capture: Capture): string {
  const wear = capture.wear ?? [];
  const lines: string[] = [
    JSON.stringify({
      ...capture.header,
      v: wear.length ? 2 : capture.header.v,
      frames: capture.frames.length,
      ...(wear.length ? { wear: wear.length } : {}),
    }),
  ];
  for (const f of capture.frames) {
    lines.push(JSON.stringify({
      beat: f.beat,
      // `NaN` is not JSON. It is also MEANINGFUL here — it is how the detector
      // says a landmark is absent — so it goes out as null and comes back as
      // NaN, rather than being silently replaced by a coordinate.
      l: Array.from(f.landmarks, (x) => (Number.isFinite(x) ? round(x, LANDMARK_DP) : null)),
      s: Array.from(f.sigmaPx, (x) => (Number.isFinite(x) ? round(x, WEIGHT_DP) : null)),
      v: Array.from(f.visibility, (x) => round(x, WEIGHT_DP)),
      ...(f.silhouette ? { sil: Array.from(f.silhouette, (x) => round(x, LANDMARK_DP)) } : {}),
    }));
  }
  // Wear frames after every scan frame, so a reader that stops at the declared
  // scan count still sees a whole, valid scan.
  for (const f of wear) {
    lines.push(JSON.stringify({
      w: 1,
      dt: round(f.dt, 6),
      l: Array.from(f.landmarks, (x) => (Number.isFinite(x) ? round(x, WEAR_LANDMARK_DP) : null)),
      e: poseOut(f.emitted),
      r: poseOut(f.raw),
    }));
  }
  return lines.join('\n') + '\n';
}

/**
 * Reads a capture back.
 *
 * Refuses loudly rather than returning a half-file: a fixture that silently
 * dropped its last thirty frames is a replay whose numbers nobody can compare
 * against the session that produced it.
 */
export function parseCapture(text: string): Capture {
  const lines = text.split('\n').filter((l) => l.trim().length > 0);
  if (lines.length === 0) throw new Error('capture is empty');

  let header: CaptureHeader;
  try {
    header = JSON.parse(lines[0]) as CaptureHeader;
  } catch (error) {
    throw new Error(`capture header is not JSON: ${(error as Error).message}`);
  }
  if (header.v !== 1 && header.v !== 2) {
    throw new Error(`capture format v${header.v}; this reader understands v1 and v2`);
  }

  const back = (xs: (number | null)[]) =>
    Float64Array.from(xs, (x) => (x === null ? NaN : x));
  const poseIn = (xs: number[] | null | undefined): Pose | null => {
    if (!xs || xs.length !== 12) return null;
    const p = poseIdentity();
    for (let i = 0; i < 9; i++) p.R[i] = xs[i];
    for (let i = 0; i < 3; i++) p.t[i] = xs[9 + i];
    return p;
  };

  const frames: Omit<BundleFrame, 'pose'>[] = [];
  const wear: WearFrame[] = [];
  for (let i = 1; i < lines.length; i++) {
    let raw: {
      w?: 1; dt?: number; e?: number[] | null; r?: number[] | null;
      beat: string; l: (number | null)[]; s: (number | null)[]; v: number[];
      sil?: number[];
    };
    try {
      raw = JSON.parse(lines[i]);
    } catch (error) {
      throw new Error(`capture line ${i + 1} is not JSON: ${(error as Error).message}`);
    }
    if (raw.w === 1) {
      wear.push({
        landmarks: back(raw.l),
        dt: raw.dt ?? 0,
        emitted: poseIn(raw.e),
        raw: poseIn(raw.r),
      });
      continue;
    }
    frames.push({
      landmarks: back(raw.l),
      sigmaPx: back(raw.s),
      visibility: Float64Array.from(raw.v),
      silhouette: raw.sil ? Float64Array.from(raw.sil) : null,
      beat: raw.beat,
    });
  }

  // The header says how many frames it wrote. A file that disagrees was cut
  // short, and a replay of it is not a replay of the session.
  if (typeof header.frames === 'number' && header.frames !== frames.length) {
    throw new Error(
      `capture header declares ${header.frames} frames and the file holds ${frames.length}. `
      + 'It was truncated — a partial replay would not be comparable to the session '
      + 'that produced it.',
    );
  }

  if (typeof header.wear === 'number' && header.wear !== wear.length) {
    throw new Error(
      `capture header declares ${header.wear} wear frames and the file holds ${wear.length}.`,
    );
  }

  return { header, frames, wear };
}
