import test from 'node:test';
import assert from 'node:assert/strict';
import {Euler, Matrix4, Quaternion, Vector3} from 'three';
import {TempleContactDepth, TEMPLE_CONTACT_DEPTH} from '../src/render/temple-contact-depth.ts';

const shape = [-6, 1, 0, -3, -2, 3, 0, -4, 4, 3, -2, 3, 6, 1, 0];
function pose(depth = 40, pitch = 0, yaw = 0, roll = 0, x = 0, y = 0, scale = 1): Matrix4 {
  return new Matrix4().compose(new Vector3(x, y, -depth),
    new Quaternion().setFromEuler(new Euler(pitch, yaw, roll)), new Vector3(scale, scale, scale));
}
function projected(matrix: Matrix4, points = shape): Float32Array {
  const result = new Float32Array(points.length), point = new Vector3();
  for (let i = 0; i < points.length; i += 3) point.fromArray(points, i).applyMatrix4(matrix).toArray(result, i);
  return result;
}
function close(actual: ArrayLike<number>, expected: ArrayLike<number>, tolerance = 1e-5): void {
  assert.equal(actual.length, expected.length);
  for (let i = 0; i < actual.length; i++) assert.ok(Math.abs(actual[i]! - expected[i]!) <= tolerance,
    `coordinate ${i}: ${actual[i]} vs ${expected[i]}`);
}
function depthShift(points: number[], amount: number): number[] {
  return points.map((value, i) => value + (i % 3 === 2 ? amount : 0));
}

test('first observation is an exact copy and does not alter the input geometry or pose', () => {
  const filter = new TempleContactDepth(), matrix = pose(43, .43, -.8, .27, 3, -4).toArray();
  const input = projected(new Matrix4().fromArray(matrix)), original = input.slice(), originalMatrix = matrix.slice();
  const output = new Float32Array(input.length);
  assert.equal(filter.apply(input, matrix, 0, output), true);
  assert.deepEqual(output, input);
  assert.deepEqual(input, original);
  assert.deepEqual(matrix, originalMatrix);
});

test('rapid rigid rotation, translation, scale and camera-distance changes have no added pose lag', () => {
  for (const fps of [15, 30, 60]) {
    const filter = new TempleContactDepth(), output = new Float32Array(shape.length);
    for (let frame = 0; frame < 80; frame++) {
      const t = frame / 13;
      const matrix = pose(20 + 50 * (.5 + .5 * Math.sin(t)), .8 * Math.cos(2 * t),
        1.1 * Math.sin(1.7 * t), .6 * Math.cos(1.3 * t), 8 * Math.sin(2 * t), 6 * Math.cos(t),
        .9 + .2 * (.5 + .5 * Math.sin(.8 * t)));
      const input = projected(matrix);
      assert.equal(filter.apply(input, matrix.toArray(), frame * 1000 / fps, output), true);
      close(output, input, 8e-6);
    }
  }
});

test('current observed XY projection stays on exactly the current rays during shape changes', () => {
  const filter = new TempleContactDepth(), output = new Float32Array(shape.length);
  const initialPose = pose(50, .2, .5, -.1, 2, 3);
  assert.equal(filter.apply(projected(initialPose), initialPose.toArray(), 0, output), true);
  const matrix = pose(22, -.6, -.8, .4, -3, 2);
  const changed = shape.map((v, i) => v + (i % 3 === 0 ? .6 : i % 3 === 1 ? -.4 : .3));
  const input = projected(matrix, changed), before = input.slice();
  assert.equal(filter.apply(input, matrix.toArray(), 16, output), true);
  assert.ok(output.some((v, i) => Math.abs(v - input[i]!) > .01), 'fixture must exercise depth smoothing');
  for (let i = 0; i < input.length; i += 3) {
    assert.ok(Math.abs(output[i]! / output[i + 2]! - input[i]! / input[i + 2]!) < 3e-8);
    assert.ok(Math.abs(output[i + 1]! / output[i + 2]! - input[i + 1]! / input[i + 2]!) < 3e-8);
  }
  assert.deepEqual(input, before);
});

test('alternating observed local-depth noise is attenuated without changing its mean', () => {
  const filter = new TempleContactDepth(), matrix = pose(40), output = new Float32Array(shape.length);
  const rawErrors: number[] = [], filteredErrors: number[] = [];
  filter.apply(projected(matrix), matrix.toArray(), 0, output);
  for (let frame = 1; frame <= 100; frame++) {
    const input = projected(matrix, depthShift(shape, frame % 2 ? .1 : -.1));
    filter.apply(input, matrix.toArray(), frame * 1000 / 30, output);
    if (frame > 20) {rawErrors.push(input[2]! + 40); filteredErrors.push(output[2]! + 40);}
  }
  const rms = (values: number[]) => Math.sqrt(values.reduce((sum, value) => sum + value * value, 0) / values.length);
  assert.ok(rms(filteredErrors) < .4 * rms(rawErrors));
  assert.ok(Math.abs(filteredErrors.reduce((sum, value) => sum + value, 0) / filteredErrors.length) < 1e-5);
});

test('metric correction stays within 0.75mm and cannot turn a truly foreground shaft into a 1mm burial', () => {
  for (const depth of [20, 45, 70]) for (const direction of [-1, 1]) {
    const filter = new TempleContactDepth(), matrix = pose(depth), output = new Float32Array(shape.length);
    filter.apply(projected(matrix), matrix.toArray(), 0, output);
    const input = projected(matrix, depthShift(shape, direction * 3));
    assert.equal(filter.apply(input, matrix.toArray(), 1, output), true);
    for (let i = 2; i < output.length; i += 3) {
      assert.ok(Math.abs(output[i]! - input[i]!) <= TEMPLE_CONTACT_DEPTH.maximumCorrectionCm + 5e-6);
      const genuinelyForegroundArmZ = input[i]! + .001;
      assert.ok(output[i]! - genuinelyForegroundArmZ < .1, 'cannot reach the 1mm occlusion onset');
    }
  }
});

test('a real shape step follows immediately within its bound and settles to the new observation', () => {
  const filter = new TempleContactDepth(), matrix = pose(40), output = new Float32Array(shape.length);
  filter.apply(projected(matrix), matrix.toArray(), 0, output);
  const changed = projected(matrix, depthShift(shape, .8));
  let previousError = Infinity;
  for (let frame = 1; frame <= 12; frame++) {
    filter.apply(changed, matrix.toArray(), frame * 1000 / 30, output);
    const error = Math.abs(output[2]! - changed[2]!);
    assert.ok(error <= .075005, 'no slow traversal from the old cheek to a real new cheek');
    assert.ok(error <= previousError + 1e-6);
    previousError = error;
  }
  assert.ok(previousError < .0002, 'new measured anatomy is retained, not pulled back toward canonical shape');
});

test('duplicate and backwards timestamps do not integrate history or alter later elapsed time', () => {
  const baseline = new TempleContactDepth(), audited = new TempleContactDepth(), matrix = pose(40);
  const initial = projected(matrix), changed = projected(matrix, depthShift(shape, .1));
  const a = new Float32Array(shape.length), b = new Float32Array(shape.length);
  baseline.apply(initial, matrix.toArray(), 0, a); audited.apply(initial, matrix.toArray(), 0, b);
  baseline.apply(changed, matrix.toArray(), 33, a); audited.apply(changed, matrix.toArray(), 33, b);
  const first = b.slice();
  for (let repeat = 0; repeat < 8; repeat++) {
    audited.apply(changed, matrix.toArray(), repeat % 2 ? 20 : 33, b);
    assert.deepEqual(b, first, 'audit rerenders are idempotent');
  }
  baseline.apply(changed, matrix.toArray(), 66, a); audited.apply(changed, matrix.toArray(), 66, b);
  assert.deepEqual(b, a);
});

test('reset, a long gap and a vertex-count change reseed exactly from the current observation', () => {
  const matrix = pose(40), initial = projected(matrix), changed = projected(matrix, depthShift(shape, .8));
  for (const mode of ['reset', 'gap', 'size'] as const) {
    const filter = new TempleContactDepth(); let output = new Float32Array(shape.length);
    filter.apply(initial, matrix.toArray(), 0, output);
    filter.apply(changed, matrix.toArray(), 33, output);
    if (mode === 'reset') filter.reset();
    const input = mode === 'size' ? changed.slice(0, 6) : changed;
    output = new Float32Array(input.length);
    assert.equal(filter.apply(input, matrix.toArray(), mode === 'gap' ? 284 : 66, output), true);
    assert.deepEqual(output, input);
  }
});

test('invalid data and singular poses leave output untouched, clear history, and cannot partially pollute a frame', () => {
  const matrix = pose(40).toArray(), initial = projected(new Matrix4().fromArray(matrix));
  const cases: {input?: Float32Array; pose?: number[]; time?: number; length?: number}[] = [
    {input: new Float32Array(0)}, {input: new Float32Array(4)}, {length: initial.length - 3},
    {pose: matrix.slice(0, 15)}, {pose: matrix.map((v, i) => i === 14 ? NaN : v)},
    {pose: matrix.map((v, i) => i === 0 ? 0 : v)}, {pose: matrix.map((v, i) => i === 3 ? .1 : v)},
    {time: NaN}, {time: Infinity}, {time: -1},
    {input: Float32Array.from(initial, (v, i) => i === initial.length - 1 ? NaN : v)},
    {input: Float32Array.from(initial, (v, i) => i === initial.length - 2 ? Infinity : v)},
    {input: Float32Array.from(initial, (v, i) => i === initial.length - 1 ? 0 : v)},
    {input: Float32Array.from(initial, (v, i) => i === initial.length - 1 ? 1 : v)},
  ];
  for (const invalid of cases) {
    const filter = new TempleContactDepth(), setup = new Float32Array(initial.length);
    filter.apply(initial, matrix, 0, setup);
    const output = new Float32Array(invalid.length ?? initial.length).fill(123), before = output.slice();
    assert.equal(filter.apply(invalid.input ?? initial, invalid.pose ?? matrix, invalid.time ?? 33, output), false);
    assert.deepEqual(output, before);
    const changed = projected(new Matrix4().fromArray(matrix), depthShift(shape, .8));
    assert.equal(filter.apply(changed, matrix, 66, setup), true);
    assert.deepEqual(setup, changed, 'failure erased prior anatomy and timestamp');
  }
});

test('output may alias the input without affecting validation or current-frame ray anchoring', () => {
  const separate = new TempleContactDepth(), aliased = new TempleContactDepth(), matrix = pose(40, .2, .4);
  const firstA = projected(matrix), firstB = firstA.slice(), output = new Float32Array(firstA.length);
  separate.apply(firstA, matrix.toArray(), 0, output); aliased.apply(firstB, matrix.toArray(), 0, firstB);
  const changedA = projected(matrix, depthShift(shape, .2)), changedB = changedA.slice();
  separate.apply(changedA, matrix.toArray(), 33, output); aliased.apply(changedB, matrix.toArray(), 33, changedB);
  assert.deepEqual(changedB, output);
});
