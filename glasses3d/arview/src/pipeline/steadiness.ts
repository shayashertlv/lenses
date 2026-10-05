/** Pose shake read from the timing rows: how much the glasses' orientation and depth change beyond smooth motion.
 *
 *  The measure is the per-frame second difference, |x(t+1) − 2·x(t) + x(t−1)|, over three consecutive tracked rows. A
 *  steady turn or a smooth lag leaves it near zero, so it grades noise and not how far the filter trails; the trailing
 *  is reported separately as the angle between the raw and the steadied orientation. */
import {distribution} from './profiler.ts';
import type {Distribution, FrameSample} from './profiler.ts';

export interface PoseShake {
  /** Triples of consecutive tracked rows measured. */
  triples: number;
  rawAngleDeg: Distribution | null;
  steadyAngleDeg: Distribution | null;
  rawDepthMm: Distribution | null;
  steadyDepthMm: Distribution | null;
  /** Raw-to-steadied orientation angle over the rows, degrees. */
  steadyLagDeg: Distribution | null;
}

const value = (row: FrameSample, key: string): number | null => {
  const entry = row.native?.['pose.' + key];
  return typeof entry === 'number' && Number.isFinite(entry) ? entry : null;
};
const angles = (row: FrameSample, prefix: '' | 'steady'): [number, number, number] | null => {
  const name = (axis: string) => prefix ? prefix + axis : axis.charAt(0).toLowerCase() + axis.slice(1);
  const yaw = value(row, name('YawDeg')), pitch = value(row, name('PitchDeg')), roll = value(row, name('RollDeg'));
  return yaw === null || pitch === null || roll === null ? null : [yaw, pitch, roll];
};
const depth = (row: FrameSample, prefix: '' | 'steady'): number | null => value(row, prefix ? 'steadyDepthCm' : 'depthCm');

export function poseShake(rows: readonly FrameSample[]): PoseShake {
  const rawAngle: number[] = [], steadyAngle: number[] = [], rawDepth: number[] = [], steadyDepth: number[] = [], lag: number[] = [];
  let triples = 0;
  for (const row of rows) {const l = value(row, 'steadyLagDeg'); if (l !== null) lag.push(l);}
  for (let index = 2; index < rows.length; index++) {
    const a = rows[index - 2]!, b = rows[index - 1]!, c = rows[index]!;
    const ra = angles(a, ''), rb = angles(b, ''), rc = angles(c, '');
    if (!ra || !rb || !rc) continue;
    triples++;
    rawAngle.push(Math.hypot(...ra.map((_, axis) => rc[axis]! - 2 * rb[axis]! + ra[axis]!)));
    rawDepth.push(Math.abs(depth(c, '')! - 2 * depth(b, '')! + depth(a, '')!) * 10);
    // A restart passes the raw pose through, which is a step in the steadied series, not shake.
    if (b.native?.['pose.steadyReset'] === true || c.native?.['pose.steadyReset'] === true) continue;
    const sa = angles(a, 'steady'), sb = angles(b, 'steady'), sc = angles(c, 'steady');
    const da = depth(a, 'steady'), db = depth(b, 'steady'), dc = depth(c, 'steady');
    if (sa && sb && sc) steadyAngle.push(Math.hypot(...sa.map((_, axis) => sc[axis]! - 2 * sb[axis]! + sa[axis]!)));
    if (da !== null && db !== null && dc !== null) steadyDepth.push(Math.abs(dc - 2 * db + da) * 10);
  }
  return {triples, rawAngleDeg: distribution(rawAngle), steadyAngleDeg: distribution(steadyAngle), rawDepthMm: distribution(rawDepth),
    steadyDepthMm: distribution(steadyDepth), steadyLagDeg: distribution(lag)};
}

/** One line for the live panel. */
export function describePoseShake(shake: PoseShake, steadyOn: boolean): string {
  if (!shake.rawAngleDeg || !shake.rawDepthMm) return 'Pose shake: waiting for three tracked frames in a row.';
  const pair = (d: {median: number; p95: number}, digits: number, unit: string) => `${d.median.toFixed(digits)} / ${d.p95.toFixed(digits)}${unit}`;
  const angle = `angle ${pair(shake.rawAngleDeg, 2, '°')}${shake.steadyAngleDeg ? ` raw → ${pair(shake.steadyAngleDeg, 2, '°')} steady` : ''}`;
  const depthText = `depth ${pair(shake.rawDepthMm, 1, ' mm')}${shake.steadyDepthMm ? ` raw → ${pair(shake.steadyDepthMm, 1, ' mm')} steady` : ''}`;
  const lagText = shake.steadyLagDeg ? ` · steady trails raw by ${pair(shake.steadyLagDeg, 2, '°')}` : '';
  return `Pose shake, last 10 s (per-frame 2nd difference, median / p95, ${shake.triples} frames): ${angle} · ${depthText}${lagText}`
    + (steadyOn ? '.' : ' · steadiness is off (?steady=0).');
}
