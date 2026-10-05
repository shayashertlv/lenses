import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Euler, Matrix4, Quaternion, Vector3} from 'three';
import {ShadowReceiver} from '../src/render/shadow-receiver.ts';

const shape = [-6, 2, 1, -3, 1, 4, 0, 0, 7, 3, 1, 4, 6, 2, 1];
const pose = (depth = 45, yaw = 0, pitch = 0, x = 0): Matrix4 => new Matrix4().compose(new Vector3(x, 0, -depth),
  new Quaternion().setFromEuler(new Euler(pitch, yaw, 0)), new Vector3(1, 1, 1));
function transform(points: ArrayLike<number>, matrix: Matrix4): Float32Array {
  const output = new Float32Array(points.length), point = new Vector3();
  for (let i = 0; i < points.length; i += 3) point.fromArray(points, i).applyMatrix4(matrix).toArray(output, i);
  return output;
}
function close(a: ArrayLike<number>, b: ArrayLike<number>, epsilon = 1e-5): void {
  assert.equal(a.length, b.length);
  for (let i = 0; i < a.length; i++) assert.ok(Math.abs(a[i]! - b[i]!) < epsilon, `coordinate ${i}: ${a[i]} vs ${b[i]}`);
}

test('shadow lookup attaches the observed local face to exactly the glasses pose without changing the input face', () => {
  const filter = new ShadowReceiver(), raw = pose(45.3, .01, -.02), attached = pose(45, .003, -.005, .07);
  const input = transform(shape, raw), before = input.slice(), output = new Float32Array(input.length);
  assert.equal(filter.apply(input, raw.toArray(), attached.toArray(), 100, output), true);
  close(output, transform(shape, attached)); assert.deepEqual(input, before);
  assert.notDeepEqual(output, input, 'the receiver does not retain the raw vs smoothed pose error');
});

test('rapid head motion adds no receiver-pose lag, even while local anatomy is being filtered', () => {
  const filter = new ShadowReceiver(), output = new Float32Array(shape.length);
  for (let frame = 0; frame < 100; frame++) {
    const t = frame / 9, raw = pose(42 + 15 * Math.sin(t), Math.sin(t) * .9, Math.cos(t) * .5, Math.sin(t) * 5);
    const attached = pose(42 + 15 * Math.sin(t) + .1, Math.sin(t) * .9 - .002, Math.cos(t) * .5, Math.sin(t) * 5 + .04);
    const input = transform(shape, raw);
    assert.equal(filter.apply(input, raw.toArray(), attached.toArray(), frame * 33, output), true);
    close(output, transform(shape, attached));
  }
});

test('resting local depth noise is reduced by more than 80% at both camera rates without bias', () => {
  for (const fps of [15, 30]) {
    const filter = new ShadowReceiver(), matrix = pose(), output = new Float32Array(shape.length);
    filter.apply(transform(shape, matrix), matrix.toArray(), matrix.toArray(), 0, output);
    let rawEnergy = 0, filteredEnergy = 0, mean = 0, count = 0;
    for (let frame = 1; frame <= 160; frame++) {
      const noise = frame % 2 ? .15 : -.15;
      const input = transform(shape.map((v, i) => v + (i % 3 === 2 ? noise : 0)), matrix);
      assert.equal(filter.apply(input, matrix.toArray(), matrix.toArray(), frame * 1000 / fps, output), true);
      if (frame > 40) {
        const error = output[8]! - (shape[8]! - 45);
        rawEnergy += noise ** 2; filteredEnergy += error ** 2; mean += error; count++;
      }
    }
    assert.ok(Math.sqrt(filteredEnergy / rawEnergy) < .2);
    assert.ok(Math.abs(mean / count) < 1e-4);
  }
});

test('duplicate/audit timestamps never integrate the local shape twice', () => {
  const actual = new ShadowReceiver(), control = new ShadowReceiver(), matrix = pose().toArray();
  const base = transform(shape, pose()), changed = transform(shape.map((v, i) => v + (i % 3 === 2 ? .2 : 0)), pose());
  const a = new Float32Array(shape.length), b = new Float32Array(shape.length);
  for (const filter of [actual, control]) filter.apply(base, matrix, matrix, 0, a);
  actual.apply(changed, matrix, matrix, 33, a); control.apply(changed, matrix, matrix, 33, b);
  const once = a.slice(); actual.apply(changed, matrix, matrix, 33, a); actual.apply(changed, matrix, matrix, 16, a);
  assert.deepEqual(a, once);
  actual.apply(changed, matrix, matrix, 66, a); control.apply(changed, matrix, matrix, 66, b); assert.deepEqual(a, b);
});

test('invalid geometry is atomic and resets history; tracking gaps seed only the new face', () => {
  const filter = new ShadowReceiver(), matrix = pose().toArray(), output = new Float32Array(shape.length);
  const base = transform(shape, pose()), changed = transform(shape.map((v, i) => v + (i % 3 === 2 ? .4 : 0)), pose());
  filter.apply(base, matrix, matrix, 0, output); const prior = output.slice();
  const invalid = changed.slice(); invalid[invalid.length - 1] = NaN;
  assert.equal(filter.apply(invalid, matrix, matrix, 33, output), false); assert.deepEqual(output, prior);
  assert.equal(filter.apply(changed, matrix, matrix, 66, output), true); close(output, changed);
  filter.apply(base, matrix, matrix, 700, output); close(output, base);
  filter.reset(); filter.apply(changed, matrix, matrix, 733, output); close(output, changed);
});
