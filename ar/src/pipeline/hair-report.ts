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

/** One line for the live panel. */
export function describeHairReport(report: HairReport, scheduleLabel: string): string {
  if (report.frames === 0) return `Hair masks (${scheduleLabel}): no tracked frames with hair on in the last 10 s.`;
  const share = (count: number) => `${Math.round(100 * count / report.frames)}%`;
  const reuse = report.reused === 0 ? '' : ` · reused masks moved with the head, age median ${Math.round(report.reuseAgeMs!.median)} / p95 ${Math.round(report.reuseAgeMs!.p95)} ms`
    + (report.reuseMotionPx ? `, head motion median ${report.reuseMotionPx.median.toFixed(1)} / p95 ${report.reuseMotionPx.p95.toFixed(1)} px` : '');
  return `Hair masks, last 10 s (${scheduleLabel}): ${report.frames} tracked frames · own ${share(report.own)} · reused ${share(report.reused)} · none ${share(report.none)} · ${report.jobs} hair jobs${reuse}.`;
}
