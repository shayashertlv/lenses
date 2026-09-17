/** The hair schedule as seen in the timing rows (`?hairframes=`, hair/mask-reuse.ts): how many tracked frames drew their
 *  own mask, a reused one or none, how old the reused masks were and how far the head had moved since they were made. */
import {distribution} from './profiler.ts';
import type {Distribution, FrameSample} from './profiler.ts';

export interface HairReport {
  /** Tracked frames with hair on. */
  frames: number;
  own: number; reused: number; none: number;
  /** Frames that started a hair job. */
  jobs: number;
  reuseAgeMs: Distribution | null;
  reuseMotionPx: Distribution | null;
}

const num = (row: FrameSample, key: string): number | null => {const value = row.native?.[key]; return typeof value === 'number' && Number.isFinite(value) ? value : null;};

export function hairReport(rows: readonly FrameSample[]): HairReport {
  let frames = 0, own = 0, reused = 0, none = 0, jobs = 0;
  const ages: number[] = [], motions: number[] = [];
  for (const row of rows) {
    if (row.native?.['hair.requested'] === true) jobs++;
    if (!row.hasFace || !row.hair) continue;
    frames++;
    if (!row.hasMask) {none++; continue;}
    if (row.native?.['hair.carried'] === true) {
      reused++;
      const age = num(row, 'hair.ageMs'), motion = num(row, 'hair.motionPx');
      if (age !== null) ages.push(age);
      if (motion !== null) motions.push(motion);
    } else own++;
  }
  return {frames, own, reused, none, jobs, reuseAgeMs: distribution(ages), reuseMotionPx: distribution(motions)};
}

/** Whether a face request (the landmarker's GPU delegate) shared the GPU with a frame's draw: the suspected reason a
 *  schedule that never waits slows the face landmarker. What was in flight when a frame's finish() began (face request,
 *  hair job); how far each face request's post was from the draw starts around it; and the face inference time of
 *  requests with a draw starting within NEAR_DRAW_MS after the post (a condition set before the request ran, so a slow
 *  request is not counted as overlapped merely for lasting longer) against the others. */
export const NEAR_DRAW_MS = 10;
export interface OverlapReport {
  frames: number; faceAtDrawStart: number; hairAtDrawStart: number;
  beforeDrawMs: Distribution | null; afterDrawMs: Distribution | null;
  nearDraw: {requests: number; inferenceMs: Distribution | null}; apart: {requests: number; inferenceMs: Distribution | null};
}

export function overlapReport(rows: readonly FrameSample[]): OverlapReport {
  let frames = 0, faceAtDrawStart = 0, hairAtDrawStart = 0;
  const before: number[] = [], after: number[] = [], near: number[] = [], apart: number[] = [];
  let nearCount = 0, apartCount = 0;
  for (const row of rows) {
    if (typeof row.native?.['overlap.faceAtSubmit'] !== 'boolean') continue;
    frames++;
    if (row.native['overlap.faceAtSubmit'] === true) faceAtDrawStart++;
    if (row.native['overlap.hairAtSubmit'] === true) hairAtDrawStart++;
    const beforeDraw = num(row, 'overlap.faceBeforeDrawMs'), afterDraw = num(row, 'overlap.faceAfterDrawMs');
    if (afterDraw !== null) after.push(afterDraw);
    if (beforeDraw === null) continue;
    before.push(beforeDraw);
    const inference = typeof row.faceInferenceMs === 'number' && Number.isFinite(row.faceInferenceMs) ? row.faceInferenceMs : null;
    if (beforeDraw <= NEAR_DRAW_MS) {nearCount++; if (inference !== null) near.push(inference);}
    else {apartCount++; if (inference !== null) apart.push(inference);}
  }
  return {frames, faceAtDrawStart, hairAtDrawStart, beforeDrawMs: distribution(before), afterDrawMs: distribution(after),
    nearDraw: {requests: nearCount, inferenceMs: distribution(near)}, apart: {requests: apartCount, inferenceMs: distribution(apart)}};
}

export function describeOverlapReport(report: OverlapReport): string {
  if (report.frames === 0) return 'overlap: no frames';
  const share = (count: number) => `${Math.round(100 * count / report.frames)}%`;
  const median = (d: Distribution | null) => d ? `${d.median.toFixed(1)} ms` : '—';
  return `draw start: face in flight ${share(report.faceAtDrawStart)}, hair ${share(report.hairAtDrawStart)}`
    + ` · face post→next draw ${median(report.beforeDrawMs)}, previous draw→post ${median(report.afterDrawMs)}`
    + ` · face inference ${median(report.nearDraw.inferenceMs)} (n ${report.nearDraw.requests}) with a draw ≤${NEAR_DRAW_MS} ms after post, else ${median(report.apart.inferenceMs)} (n ${report.apart.requests})`;
}

/** One line for the live panel. */
export function describeHairReport(report: HairReport, scheduleLabel: string): string {
  if (report.frames === 0) return `Hair masks (${scheduleLabel}): no tracked frames with hair on in the last 10 s.`;
  const share = (count: number) => `${Math.round(100 * count / report.frames)}%`;
  const reuse = report.reused === 0 ? '' : ` · reused masks moved with the head, age median ${Math.round(report.reuseAgeMs!.median)} / p95 ${Math.round(report.reuseAgeMs!.p95)} ms`
    + (report.reuseMotionPx ? `, head motion median ${report.reuseMotionPx.median.toFixed(1)} / p95 ${report.reuseMotionPx.p95.toFixed(1)} px` : '');
  return `Hair masks, last 10 s (${scheduleLabel}): ${report.frames} tracked frames · own ${share(report.own)} · reused ${share(report.reused)} · none ${share(report.none)} · ${report.jobs} hair jobs${reuse}.`;
}
