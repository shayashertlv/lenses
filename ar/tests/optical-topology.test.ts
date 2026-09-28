import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BufferGeometry, Float32BufferAttribute, Mesh, PlaneGeometry} from 'three';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import {validateCanonicalOpticalTopology} from '../src/render/optical-topology.ts';

function sheet(xSegments = 1, ySegments = 1, z = 0): Mesh {
  return new Mesh(new PlaneGeometry(2, 2, xSegments, ySegments).translate(0, 0, z));
}
function merge(...meshes: Mesh[]): Mesh {
  return new Mesh(mergeGeometries(meshes.map(mesh => mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry)));
}
function rectangularRing(): Mesh {
  const geometry = new BufferGeometry();
  const points = [[-1, -1], [1, -1], [1, 1], [-1, 1], [-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]];
  geometry.setAttribute('position', new Float32BufferAttribute(points.flatMap(([x, y]) => [x!, y!, 0]), 3));
  const indices: number[] = [];
  for (let i = 0; i < 4; i++) {
    const j = (i + 1) % 4;
    indices.push(i, j, j + 4, i, j + 4, i + 4);
  }
  geometry.setIndex(indices);
  return new Mesh(geometry);
}

test('shared triangle edges/vertices and distinct-depth sheet layers are supported', () => {
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([sheet(12, 9), sheet(9, 11, .01)]));
  const right = sheet(); right.geometry.translate(2, 0, 0);
  const corner = sheet(); corner.geometry.translate(2, 2, 0);
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([sheet(), right, corner]));
});

test('same-mesh coincident different tessellations and stacked sheets are rejected', () => {
  assert.throws(() => validateCanonicalOpticalTopology([merge(sheet(), sheet(3, 2))]), /single-valued.*XY triangle overlap/);
  assert.throws(() => validateCanonicalOpticalTopology([merge(sheet(), sheet(3, 2, .1))]), /single-valued/);
});

test('cross-mesh coincident and partially overlapping coplanar triangles are rejected', () => {
  assert.throws(() => validateCanonicalOpticalTopology([sheet(), sheet(3, 2)]), /coincident coplanar patches/);
  const shifted = sheet(3, 2); shifted.geometry.translate(1.5, .3, 0);
  assert.throws(() => validateCanonicalOpticalTopology([sheet(), shifted]), /undefined layer order/);
});

test('holes and concave outlines are tested by triangles rather than overlapping boxes', () => {
  const insideHole = sheet(); insideHole.geometry.scale(.2, .2, 1);
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([rectangularRing(), insideHole]));
  const left = sheet(), bottom = sheet(), missingCorner = sheet();
  left.geometry.scale(.5, 1, 1).translate(-.5, 0, 0);
  bottom.geometry.scale(.5, .5, 1).translate(.5, -.5, 0);
  missingCorner.geometry.scale(.2, .2, 1).translate(.5, .5, 0);
  const concave = merge(left, bottom);
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([concave, missingCorner]));
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([merge(concave, missingCorner)]));
});

test('curved single-valued surfaces and noncoplanar crossing sheets are supported', () => {
  const curved = sheet(21, 17), position = curved.geometry.getAttribute('position');
  for (let i = 0; i < position.count; i++) position.setZ(i, .1 * position.getX(i) ** 2 + .2 * position.getY(i) ** 2);
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([curved]));
  const crossing = sheet(8, 7), p = crossing.geometry.getAttribute('position');
  for (let i = 0; i < p.count; i++) p.setZ(i, .1 * p.getX(i));
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([sheet(7, 6), crossing]));
});

test('a partial coincident patch is rejected even when the remainder of a mesh bends away', () => {
  const bent = sheet(4, 4), position = bent.geometry.getAttribute('position');
  for (let i = 0; i < position.count; i++) position.setZ(i, Math.max(0, position.getX(i)) * .2);
  assert.throws(() => validateCanonicalOpticalTopology([sheet(3, 3), bent]), /coincident coplanar patches/);
});

test('numerical decisions scale with authored units', () => {
  for (const scale of [1e-6, 1, 1e6]) {
    const a = sheet(3, 3), b = sheet(2, 2, .01);
    a.geometry.scale(scale, scale, scale); b.geometry.scale(scale, scale, scale);
    assert.doesNotThrow(() => validateCanonicalOpticalTopology([a, b]));
    const overlap = sheet(5, 4); overlap.geometry.scale(scale, scale, scale);
    assert.throws(() => validateCanonicalOpticalTopology([a, overlap]), /coincident/);
  }
});

test('unrelated distant geometry cannot loosen local overlap tolerance', () => {
  const a = sheet(), narrowOverlap = sheet(), distant = sheet();
  narrowOverlap.geometry.translate(1.999, 0, 0);
  distant.geometry.translate(1e6, 1e6, 1000);
  assert.throws(() => validateCanonicalOpticalTopology([a, narrowOverlap, distant]), /coincident/);
});

test('dense triangulated sheets exercise broadphase without an all-pairs scan', () => {
  assert.doesNotThrow(() => validateCanonicalOpticalTopology([sheet(100, 80)]));
});
