import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Euler, Matrix4, Quaternion, Vector3} from 'three';
import {DEFAULT_STEADY, PoseStabilizer, poseAngles, validateSteadyOptions} from '../src/render/pose-stabilizer.ts';
import {describePoseShake, poseShake} from '../src/pipeline/steadiness.ts';
import {describeConfig, parseConfig} from '../src/config.ts';
import type {FrameSample} from '../src/pipeline/profiler.ts';

const FRAME_MS = 1000 / 30;
const random = (seed: number) => () => {seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296;};
const gauss = (next: () => number) => Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
const pose = (yawDeg: number, pitchDeg: number, rollDeg: number, zCm: number, x = 1, y = -0.5): number[] => new Matrix4().compose(new Vector3(x, y, zCm),
  new Quaternion().setFromEuler(new Euler(pitchDeg * Math.PI / 180, yawDeg * Math.PI / 180, rollDeg * Math.PI / 180, 'YXZ')), new Vector3(1, 1, 1)).toArray();
const rms = (values: number[]) => Math.sqrt(values.reduce((sum, value) => sum + value * value, 0) / values.length);
const secondDifferences = (series: number[][], from: number, to: number, skip: number) => series.slice(skip).map((_, offset) => {
  const i = offset + skip; return Math.hypot(...series[i]!.slice(from, to).map((value, k) => value - 2 * series[i - 1]![from + k]! + series[i - 2]![from + k]!));
});

test('the first pose, and the first pose after a gap, pass through unchanged', () => {
  const filter = new PoseStabilizer();
  const first = pose(10, -5, 2, -45), result = filter.apply(first, 1000);
  assert.deepEqual(result.matrix, first); assert.equal(result.reset, true); assert.equal(result.lagDeg, 0);
  assert.equal(filter.apply(pose(12, -5, 2, -44), 1000 + FRAME_MS).reset, false);
  const later = pose(40, 10, 0, -60), resumed = filter.apply(later, 1000 + FRAME_MS + DEFAULT_STEADY.resetGapMs + 1);
  assert.deepEqual(resumed.matrix, later); assert.equal(resumed.reset, true);
  filter.reset(); assert.deepEqual(filter.apply(first, 5000).matrix, first);
  assert.equal(filter.apply(first, 5000).reset, true, 'a repeated or earlier timestamp restarts rather than dividing by zero');
});

test('a still head: orientation and depth shake drop below a third, without pulling the pose off the truth', () => {
  const next = random(7), filter = new PoseStabilizer(), raw: number[][] = [], steady: number[][] = [];
  for (let i = 0; i < 300; i++) {
    const matrix = pose(5 + 0.4 * gauss(next), -8 + 0.4 * gauss(next), 2 + 0.4 * gauss(next), -45 + 0.3 * gauss(next));
    const a = poseAngles(matrix), s = poseAngles(filter.apply(matrix, i * FRAME_MS).matrix);
    raw.push([a.yawDeg, a.pitchDeg, a.rollDeg, a.depthCm]); steady.push([s.yawDeg, s.pitchDeg, s.rollDeg, s.depthCm]);
  }
  // Measured 0.15 for both with the defaults; a filter whose cutoff read the noise as motion would not get below ~0.5.
  const angleRatio = rms(secondDifferences(steady, 0, 3, 32)) / rms(secondDifferences(raw, 0, 3, 32));
  const depthRatio = rms(secondDifferences(steady, 3, 4, 32)) / rms(secondDifferences(raw, 3, 4, 32));
  assert.ok(angleRatio < 0.33, `angle shake ratio ${angleRatio}`); assert.ok(depthRatio < 0.33, `depth shake ratio ${depthRatio}`);
  const yawError = steady.slice(30).reduce((sum, row) => sum + Math.abs(row[0]! - 5), 0) / 270;
  assert.ok(yawError < 0.4, `mean yaw error ${yawError} must stay below the 0.4° noise`);
});

test('a reversing head turn is followed closely and never overshot; without the speed term it would trail by far more', () => {
  const sweep = (options = DEFAULT_STEADY) => {
    const filter = new PoseStabilizer(options); let maxError = 0, maxYaw = 0;
    for (let i = 0; i < 180; i++) {
      const t = i / 30, truth = 20 * Math.sin(Math.PI * t), yaw = poseAngles(filter.apply(pose(truth, 0, 0, -45), i * FRAME_MS).matrix).yawDeg;
      if (t >= 1) {maxError = Math.max(maxError, Math.abs(yaw - truth)); maxYaw = Math.max(maxYaw, Math.abs(yaw));}
    }
    return {maxError, maxYaw};
  };
  // ±20° at 0.5 Hz peaks at 63 °/s. Measured: 2.3° with the defaults, 8.8° with a fixed 1 Hz cutoff.
  const tuned = sweep(), fixed = sweep({...DEFAULT_STEADY, rotationBeta: 0});
  assert.ok(tuned.maxError < 4, `trailing ${tuned.maxError}°`); assert.ok(tuned.maxYaw <= 20 + 1e-9, 'a first-order filter cannot overshoot a reversal');
  assert.ok(fixed.maxError > 4, `the speed term must matter: fixed cutoff trails ${fixed.maxError}°`);
});

test('orientations on either side of the ±180° wrap are neighbours, not a half turn apart', () => {
  const filter = new PoseStabilizer();
  filter.apply(pose(179, 0, 0, -45), 0);
  const result = filter.apply(pose(-179, 0, 0, -45), FRAME_MS), yaw = poseAngles(result.matrix).yawDeg;
  assert.ok(result.lagDeg <= 2 + 1e-9, `lag ${result.lagDeg}`); assert.ok(Math.abs(yaw) > 177, `yaw ${yaw} must stay near the wrap`);
});

test('the image-plane position passes through for the bridge pin; the output stays a rigid pose', () => {
  const filter = new PoseStabilizer(); filter.apply(pose(0, 0, 0, -45, 1, -0.5), 0);
  const raw = pose(6, -3, 1, -47, 2.5, 0.75), out = filter.apply(raw, FRAME_MS).matrix;
  assert.equal(out[12], raw[12]); assert.equal(out[13], raw[13]);
  assert.notEqual(out[14], raw[14], 'depth is smoothed'); assert.ok(out[14]! < -45 && out[14]! > -47);
  for (const column of [0, 4, 8]) assert.ok(Math.abs(Math.hypot(out[column]!, out[column + 1]!, out[column + 2]!) - 1) < 1e-9);
  assert.ok(new Matrix4().fromArray(out).determinant() > 0.999);
  assert.deepEqual([out[3], out[7], out[11], out[15]], [0, 0, 0, 1]);
});

test('invalid settings and poses are refused', () => {
  assert.throws(() => new PoseStabilizer({...DEFAULT_STEADY, rotationMinCutoffHz: 0}), /settings/);
  assert.throws(() => validateSteadyOptions({...DEFAULT_STEADY, depthBeta: -1}), /settings/);
  assert.throws(() => new PoseStabilizer().apply([1, 2, 3], 0), /invalid/);
  assert.throws(() => new PoseStabilizer().apply(pose(0, 0, 0, -45).map((v, i) => i === 14 ? NaN : v), 0), /invalid/);
});

test('pose steadiness is on by default with its levers; ?steady=0 turns it off', () => {
  assert.deepEqual(parseConfig('').steady, DEFAULT_STEADY); assert.deepEqual(parseConfig('?steady=1').steady, DEFAULT_STEADY);
  assert.equal(parseConfig('?steady=0').steady, null); assert.equal(parseConfig('?steady=off').steady, null);
  assert.deepEqual(parseConfig('?steadyhz=0.5&steadybeta=0.3&steadydepthhz=2&steadydepthbeta=0').steady,
    {...DEFAULT_STEADY, rotationMinCutoffHz: 0.5, rotationBeta: 0.3, depthMinCutoffHz: 2, depthBeta: 0});
  assert.deepEqual(parseConfig('?steadyhz=0&steadybeta=9').steady, DEFAULT_STEADY, 'out-of-range levers keep the defaults');
  assert.equal(parseConfig('?steady=0&steadyhz=0.5').steady, null, 'a lever does not turn a disabled filter back on');
  assert.match(describeConfig(parseConfig('')), /pose steadiness on: rotation 1 Hz \+ 0.1 Hz per °\/s/);
  assert.match(describeConfig(parseConfig('?steady=0')), /pose steadiness OFF \(\?steady=0\)/);
  assert.match(describeConfig(parseConfig('?steady=0&diag=1')), /pose steadiness OFF \(\?steady=0\) · temple fit original \(\?fit=width for the experiment\) · diagnostics ON \(\?diag=1\)\.$/);
});

test('the live pose-shake line measures second differences and skips a restart', () => {
  const row = (i: number, yaw: number, steadyYaw: number | null, reset = false): FrameSample => ({sequence: i, native: {
    'pose.yawDeg': yaw, 'pose.pitchDeg': 0, 'pose.rollDeg': 0, 'pose.depthCm': 45 + (i % 2) * 0.1,
    'pose.steadyYawDeg': steadyYaw, 'pose.steadyPitchDeg': steadyYaw === null ? null : 0, 'pose.steadyRollDeg': steadyYaw === null ? null : 0,
    'pose.steadyDepthCm': steadyYaw === null ? null : 45, 'pose.steadyLagDeg': steadyYaw === null ? null : Math.abs(yaw - steadyYaw), 'pose.steadyReset': steadyYaw === null ? null : reset,
  }} as unknown as FrameSample);
  // Raw yaw alternates 0/1° (second difference 2°); the steadied yaw is a smooth ramp (second difference 0).
  const on = [0, 1, 2, 3, 4, 5].map(i => row(i, i % 2, i * 0.1));
  const shake = poseShake(on);
  assert.equal(shake.triples, 4); assert.equal(shake.rawAngleDeg?.median, 2); assert.ok(Math.abs(shake.steadyAngleDeg!.median) < 1e-9);
  assert.ok(Math.abs(shake.rawDepthMm!.median - 2) < 1e-9); assert.equal(shake.steadyDepthMm?.median, 0);
  assert.match(describePoseShake(shake, true), /angle 2.00 \/ 2.00° raw → 0.00 \/ 0.00° steady · depth 2.0 \/ 2.0 mm raw → 0.0 \/ 0.0 mm steady · steady trails raw/);
  // A restart passes the raw pose (1°) through and the filter continues from there.
  const restarted = on.map((r, i) => i < 3 ? r : row(i, i % 2, 1 + (i - 3) * 0.1, i === 3));
  // Without the skip, the triple ending on the restart would read 0.7°.
  assert.ok((poseShake(restarted).steadyAngleDeg?.max ?? 0) < 1e-9, 'triples ending on or straddling a restart are not steady shake');
  const off = [0, 1, 2].map(i => row(i, i % 2, null));
  assert.equal(poseShake(off).steadyAngleDeg, null); assert.match(describePoseShake(poseShake(off), false), /steadiness is off \(\?steady=0\)\.$/);
  assert.match(describePoseShake(poseShake([row(0, 0, null)]), false), /waiting/);
  const lost = [row(0, 0, null), {sequence: 1, native: {}} as unknown as FrameSample, row(2, 0, null), row(3, 1, null)];
  assert.equal(poseShake(lost).triples, 0, 'a frame without a face breaks the run');
});
