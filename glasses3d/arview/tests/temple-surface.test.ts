import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Euler, Matrix4, Quaternion, Vector3} from 'three';
import {TempleSurface} from '../src/render/temple-surface.ts';

const canonical = [0, 1, 6, 3, 2, 5, -7, 0, 0, 7, 0, 0];
const pose = (depth: number, pitch = 0) => new Matrix4().compose(new Vector3(0, 0, -depth),
  new Quaternion().setFromEuler(new Euler(pitch, 0, 0)), new Vector3(1, 1, 1));
const project = (matrix: Matrix4, points = canonical) => new Float32Array(points.flatMap((_, i) => i % 3 ? []
  : new Vector3().fromArray(points, i).applyMatrix4(matrix).toArray()));

test('side occlusion follows the attached glasses through distance and pitch changes; central samples stay observed', () => {
  const surface = new TempleSurface(canonical);
  for (let frame = 0; frame < 20; frame++) {
    const raw = pose(60 - frame, frame * .01), attached = pose(60.7 - frame, frame * .01 - .008);
    const input = project(raw), original = input.slice(), expected = project(attached);
    surface.apply(input, attached.toArray());
    assert.deepEqual([...input.slice(0, 6)], [...original.slice(0, 6)]);
    for (let i = 6; i < input.length; i++) assert.ok(Math.abs(input[i]! - expected[i]!) < .00001);
  }
});

test('rigid sides ignore asymmetric observed lateral shape while preserving the observed center exactly', () => {
  const surface = new TempleSurface(canonical);
  for (let frame = 0; frame < 12; frame++) {
    const raw = pose(60 - frame, frame * .025), attached = pose(60.7 - frame, frame * .025 - .008);
    const changed = canonical.slice();
    changed[0] = .25; changed[2] = 7.4; // observed central nose has its own shape
    changed[6] = -8.2 - frame * .01; changed[8] = .8;
    changed[9] = 6.8; changed[11] = -.6 - frame * .03;
    const input = project(raw, changed), original = input.slice(), expected = project(attached);
    surface.apply(input, attached.toArray());
    assert.deepEqual([...input.slice(0, 6)], [...original.slice(0, 6)]);
    for (let i = 6; i < input.length; i++) assert.ok(Math.abs(input[i]! - expected[i]!) < .00001);
  }
});

test('the transition band blends the canonical attachment and observation without changing the central face', () => {
  const shape = [0, 1, 6, -5.5, 1, 2, 5.5, 1, 2], changed = shape.slice();
  changed[3] = -6.5; changed[8] = 3;
  const raw = pose(45, .2), attached = pose(45.5, .18), surface = new TempleSurface(shape);
  const input = project(raw, changed), original = input.slice(), expected = project(attached, shape);
  surface.apply(input, attached.toArray());
  assert.deepEqual([...input.slice(0, 3)], [...original.slice(0, 3)]);
  for (let i = 3; i < input.length; i++)
    assert.ok(Math.abs(input[i]! - (original[i]! + expected[i]!) / 2) < .00001, 'x=5.5 cm receives exactly half the lateral blend');
});

test('lateral coordinates are an owned canonical copy and cannot acquire observed shape history', () => {
  const source = canonical.slice(), surface = new TempleSurface(source), matrix = pose(40);
  for (const x of [-20, -8, -9]) {
    source[6] = x;
    const input = project(matrix, source); surface.apply(input, matrix.toArray());
    assert.equal(input[6], -7);
  }
});
