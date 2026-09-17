/** Hair on every second frame (`?hairframes=2`), a test lever. By default every frame waits for its own hair mask. With
 *  the schedule, frames never wait: a hair job starts on every second frame (sooner when the head moves), and a frame
 *  whose own mask is not ready is drawn with the newest mask of another frame, moved to this frame by the head's motion
 *  between the two frames. (Owner review 2026-09-17: a held, unmoved mask, hair whenever the worker is free, and every
 *  3rd/4th frame were visually unacceptable and were removed.)
 *
 *  The motion is a 2D similarity (shift, turn, scale) fitted to landmarks that move with the skull (forehead, temples,
 *  face outline above the jaw, cheekbones, nose bridge), in the frame's own pixels. Eyes (they follow the gaze), mouth
 *  and jaw (expression) are left out. The fit is closed-form on 24 points; the renderer applies it as one 3×3 matrix
 *  on the mask lookup, so a reused mask costs no inference, no readback and no upload. */
import type {Landmark} from '../face/protocol.ts';

export type HairScheduleMode = 'every' | 'interval';
export interface HairSchedule {
  /** 'every': each frame waits for its own mask (the accepted pipeline). 'interval': a hair job at most every `frames`
   *  frames, sooner when the head moves; frames never wait. */
  readonly mode: HairScheduleMode;
  /** Interval mode: frames between hair jobs (2). */
  readonly frames: number;
  /** Interval mode: start a hair job early when the head moved more than this (px, RMS over the stable landmarks) since
   *  the newest mask's frame; 0 disables the early start. */
  readonly movePx: number;
  /** A mask whose frame was captured more than this far from the drawn frame is not used, ms. */
  readonly maxAgeMs: number;
}
export const DEFAULT_HAIR_SCHEDULE: Readonly<HairSchedule> = Object.freeze({mode: 'every', frames: 1, movePx: 8, maxAgeMs: 200});

/** Landmarks that move rigidly with the skull. */
export const STABLE_LANDMARKS: readonly number[] = Object.freeze([
  10, 338, 297, 332, 284, 251, 389, 356, 454, 109, 67, 103, 54, 21, 162, 127, 234, 168, 6, 197, 116, 345, 123, 352,
]);

/** x' = a·x + b·y + tx, y' = c·x + d·y + ty, in pixels. */
export interface Affine {readonly a: number; readonly b: number; readonly c: number; readonly d: number; readonly tx: number; readonly ty: number;}
/** A reused mask's placement: maps a pixel of the drawn frame to the pixel of the mask's own frame (same size). */
export interface MaskWarp {readonly toMask: Affine; readonly width: number; readonly height: number;}

const points = (landmarks: readonly Landmark[], width: number, height: number): [number, number][] | null => {
  const out: [number, number][] = [];
  for (const index of STABLE_LANDMARKS) {
    const point = landmarks[index];
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    out.push([point.x * width, point.y * height]);
  }
  return out;
};

/** The similarity that carries the stable landmarks of `from` onto those of `to` (least squares), or null when either
 *  set is incomplete or the fit is degenerate or implausible (scale outside 0.5..2). */
export function estimateSimilarity(from: readonly Landmark[], to: readonly Landmark[], width: number, height: number): Affine | null {
  if (!(width > 0) || !(height > 0)) return null;
  const source = points(from, width, height), target = points(to, width, height);
  if (!source || !target) return null;
  const n = source.length;
  let sx = 0, sy = 0, qx = 0, qy = 0;
  for (let i = 0; i < n; i++) {sx += source[i]![0]; sy += source[i]![1]; qx += target[i]![0]; qy += target[i]![1];}
  sx /= n; sy /= n; qx /= n; qy /= n;
  let norm = 0, dot = 0, cross = 0;
  for (let i = 0; i < n; i++) {
    const px = source[i]![0] - sx, py = source[i]![1] - sy, tx = target[i]![0] - qx, ty = target[i]![1] - qy;
    norm += px * px + py * py; dot += px * tx + py * ty; cross += px * ty - py * tx;
  }
  if (!(norm > 1e-9)) return null;
  const cos = dot / norm, sin = cross / norm, scale = Math.hypot(cos, sin);
  if (!(scale >= 0.5 && scale <= 2)) return null;
  return {a: cos, b: -sin, c: sin, d: cos, tx: qx - (cos * sx - sin * sy), ty: qy - (sin * sx + cos * sy)};
}

export function invertAffine(m: Affine): Affine | null {
  const det = m.a * m.d - m.b * m.c;
  if (!(Math.abs(det) > 1e-12) || ![m.a, m.b, m.c, m.d, m.tx, m.ty].every(Number.isFinite)) return null;
  const a = m.d / det, b = -m.b / det, c = -m.c / det, d = m.a / det;
  return {a, b, c, d, tx: -(a * m.tx + b * m.ty), ty: -(c * m.tx + d * m.ty)};
}

export const applyAffine = (m: Affine, x: number, y: number): [number, number] => [m.a * x + m.b * y + m.tx, m.c * x + m.d * y + m.ty];

/** How far the head moved between two frames: RMS displacement of the stable landmarks, px. */
export function headMotionPx(from: readonly Landmark[], to: readonly Landmark[], width: number, height: number): number | null {
  const source = points(from, width, height), target = points(to, width, height);
  if (!source || !target) return null;
  let sum = 0;
  for (let i = 0; i < source.length; i++) sum += (target[i]![0] - source[i]![0]) ** 2 + (target[i]![1] - source[i]![1]) ** 2;
  return Math.sqrt(sum / source.length);
}

/** The warp as a row-major 3×3 matrix on normalised top-left texture coordinates (u, v, 1) of the drawn frame. */
export function maskUvMatrix(warp: MaskWarp): number[] {
  const {toMask: m, width: w, height: h} = warp;
  return [m.a, m.b * h / w, m.tx / w, m.c * w / h, m.d, m.ty / h, 0, 0, 1];
}

/** Decides which frames start a hair job. */
export class HairScheduler {
  readonly schedule: Readonly<HairSchedule>;
  private lastRequested = -Infinity;
  constructor(schedule: Readonly<HairSchedule>) {this.schedule = schedule;}
  /** Whether frame `sequence` should start a hair job. Never while the worker holds a job: nothing queues. */
  shouldRequest(sequence: number, workerIdle: boolean, hasMask: boolean, motionPx: number | null): boolean {
    if (!workerIdle) return false;
    return !hasMask || sequence - this.lastRequested >= this.schedule.frames
      || (this.schedule.movePx > 0 && motionPx !== null && motionPx > this.schedule.movePx);
  }
  noteRequested(sequence: number): void {this.lastRequested = sequence;}
}

export interface StoredMask<M> {
  readonly mask: M; readonly landmarks: readonly Landmark[]; readonly sequence: number; readonly capturedAtMs: number;
  readonly width: number; readonly height: number;
}

/** Keeps the mask of the newest frame (by sequence) whose mask and landmarks are both known. */
export class MaskStore<M> {
  private entry: StoredMask<M> | null = null;
  get newest(): StoredMask<M> | null {return this.entry;}
  offer(entry: StoredMask<M>): void {if (!this.entry || entry.sequence > this.entry.sequence) this.entry = entry;}
  clear(): void {this.entry = null;}
}

export interface MaskChoice<M> {
  readonly mask: M;
  /** True when the mask was computed on another frame. */
  readonly carried: boolean;
  /** For a carried mask: drawn-frame pixel → mask-frame pixel. Null for the frame's own mask. */
  readonly warp: MaskWarp | null;
  readonly ageMs: number;
  /** Drawn frame's sequence minus the mask frame's (negative when the mask came from a newer frame). */
  readonly ageFrames: number;
  /** Head motion between the mask's frame and the drawn frame, px. */
  readonly motionPx: number | null;
}

export interface DrawnFrame {readonly sequence: number; readonly capturedAtMs: number; readonly width: number; readonly height: number; readonly landmarks: readonly Landmark[];}

/** The frame's own mask when it is ready; otherwise the newest stored mask, moved to this frame, when it is recent enough,
 *  was computed on a frame of the same size and the head motion can be fitted; otherwise nothing (no hair this frame). */
export function chooseMask<M>(own: M | null, frame: DrawnFrame, stored: StoredMask<M> | null, schedule: Readonly<HairSchedule>): MaskChoice<M> | null {
  if (own !== null) return {mask: own, carried: false, warp: null, ageMs: 0, ageFrames: 0, motionPx: 0};
  if (!stored || frame.landmarks.length === 0 || stored.width !== frame.width || stored.height !== frame.height) return null;
  const ageMs = Math.abs(frame.capturedAtMs - stored.capturedAtMs);
  if (!(ageMs <= schedule.maxAgeMs)) return null;
  const forward = estimateSimilarity(stored.landmarks, frame.landmarks, frame.width, frame.height);
  const toMask = forward ? invertAffine(forward) : null;
  // The fit was refused (e.g. the tracker jumped to another face): no hair this frame rather than a misplaced mask.
  if (!toMask) return null;
  return {mask: stored.mask, carried: true, warp: {toMask, width: frame.width, height: frame.height}, ageMs,
    ageFrames: frame.sequence - stored.sequence, motionPx: headMotionPx(stored.landmarks, frame.landmarks, frame.width, frame.height)};
}
