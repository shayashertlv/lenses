import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Plane, SphereGeometry, Vector3} from 'three';
import type {BufferGeometry} from 'three';
import {canonicalFaceOuterBoundary, createTempleHeadShell} from '../src/render/temple-head-shell.ts';
import {TEMPLE_HEAD_VOLUME} from '../src/render/temple-terminal-fit.ts';

const face: {positions: number[]; indices: number[]} = JSON.parse(await readFile(
  new URL('../public/models/canonical-face.json', import.meta.url), 'utf8'));
const planes = (geometry: BufferGeometry): Plane[] => {
  const p = geometry.getAttribute('position'), result: Plane[] = [];
  for (let i = 0; i < p.count; i += 3) result.push(new Plane().setFromCoplanarPoints(
    new Vector3().fromBufferAttribute(p, i), new Vector3().fromBufferAttribute(p, i + 1), new Vector3().fromBufferAttribute(p, i + 2)));
  return result;
};

test('the real canonical exterior ring has 36 vertices and excludes interior nose and eyes', () => {
  const ring = canonicalFaceOuterBoundary(face.positions, face.indices);
  assert.equal(ring.length, 36); assert.equal(new Set(ring).size, 36);
  for (const index of [1, 4, 6, 33, 133, 168, 263, 362]) assert.ok(!ring.includes(index));
  assert.ok(ring.includes(10) && ring.includes(152) && ring.includes(234) && ring.includes(454));
});

test('a mesh with multiple boundary loops chooses the external loop instead of an internal hole', () => {
  // A flat square annulus: inner-loop Z is deliberately far forward to expose wrong boundary selection.
  const positions = [-4, -4, 0, 4, -4, 0, 4, 4, 0, -4, 4, 0, -1, -1, 50, 1, -1, 50, 1, 1, 50, -1, 1, 50];
  const indices = [0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7];
  assert.deepEqual(new Set(canonicalFaceOuterBoundary(positions, indices)), new Set([0, 1, 2, 3]));
});

test('the joined shell is watertight, convex and contains both the perimeter and existing ellipsoid vertices', () => {
  const geometry = createTempleHeadShell(face.positions, face.indices), sphere = new SphereGeometry(1, 24, 16);
  try {
    const position = geometry.getAttribute('position'), edges = new Map<string, number>();
    const key = (i: number): string => `${position.getX(i)},${position.getY(i)},${position.getZ(i)}`;
    for (let i = 0; i < position.count; i += 3) for (const [a, b] of [[i, i + 1], [i + 1, i + 2], [i + 2, i]] as const) {
      const ka = key(a), kb = key(b); assert.notEqual(ka, kb, 'no collapsed edges');
      const edge = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`; edges.set(edge, (edges.get(edge) ?? 0) + 1);
    }
    assert.ok(edges.size > 100);
    for (const count of edges.values()) assert.equal(count, 2, 'every edge closes between exactly two triangles');
    const boundary = canonicalFaceOuterBoundary(face.positions, face.indices).map(index => new Vector3().fromArray(face.positions, index * 3));
    const p = sphere.getAttribute('position'), scale = TEMPLE_HEAD_VOLUME.scaleCm, center = TEMPLE_HEAD_VOLUME.centerCm;
    const input = boundary.concat(Array.from({length: p.count}, (_, i) => new Vector3(
      p.getX(i) * scale[0] + center[0], p.getY(i) * scale[1] + center[1], p.getZ(i) * scale[2] + center[2])));
    for (const plane of planes(geometry)) for (const point of input)
      assert.ok(plane.distanceToPoint(point) <= 1e-5, 'every input point is behind every outward hull plane');
    const expectedFront = Math.max(...boundary.map(point => point.z), center[2] + scale[2]);
    assert.ok(Math.abs(geometry.boundingBox!.max.z - expectedFront) < 1e-5);
  } finally {geometry.dispose(); sphere.dispose();}
});

test('interior facial geometry cannot move the posterior shell in front of the exterior perimeter', () => {
  const normal = createTempleHeadShell(face.positions, face.indices), changed = face.positions.slice();
  const boundary = new Set(canonicalFaceOuterBoundary(face.positions, face.indices));
  for (let index = 0; index < changed.length / 3; index++) if (!boundary.has(index)) changed[index * 3 + 2] = 1000;
  const shell = createTempleHeadShell(changed, face.indices);
  try {assert.deepEqual(shell.getAttribute('position').array, normal.getAttribute('position').array);}
  finally {normal.dispose(); shell.dispose();}
});

test('the closed join contains side-face to posterior-volume segments that the ellipsoid alone misses', () => {
  const geometry = createTempleHeadShell(face.positions, face.indices), hullPlanes = planes(geometry);
  try {
    const scale = TEMPLE_HEAD_VOLUME.scaleCm, center = new Vector3(...TEMPLE_HEAD_VOLUME.centerCm);
    let gapPoints = 0;
    for (const index of canonicalFaceOuterBoundary(face.positions, face.indices)) {
      const edge = new Vector3().fromArray(face.positions, index * 3);
      if (Math.abs(edge.x) < 6) continue;
      const point = edge.clone().lerp(center, .05);
      const q = point.clone().sub(center), ellipsoid = (q.x / scale[0]) ** 2 + (q.y / scale[1]) ** 2 + (q.z / scale[2]) ** 2;
      if (ellipsoid <= 1) continue;
      gapPoints++;
      assert.ok(hullPlanes.every(plane => plane.distanceToPoint(point) < 1e-5));
    }
    assert.ok(gapPoints >= 4, 'the test exercises the actual lateral gap outside the old head volume');
  } finally {geometry.dispose();}
});
