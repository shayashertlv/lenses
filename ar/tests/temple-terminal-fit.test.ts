import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, Euler, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Texture, Vector4} from 'three';
import type {BufferGeometry, Material} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {SHIPPED_EYEWEAR, GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {buildTempleContinuityModel, projectTempleContinuity} from '../src/render/continuity.ts';
import {protectionProjection} from '../src/render/protection.ts';
import {createTempleTerminalFit, TEMPLE_HEAD_VOLUME, terminalFitSlope, terminalFitX, terminalReturn} from '../src/render/temple-terminal-fit.ts';

type Point = readonly [number, number, number];

/** Clip triangles after deformation, as the GPU sees them. Intersections are interpolated from the drawn
 * corners, not obtained by evaluating the deformation formula at an original plane intersection. */
function clipAtZ(polygon: readonly Point[], plane: number, keepAbove: boolean): Point[] {
  const out: Point[] = [];
  for (let index = 0; index < polygon.length; index++) {
    const a = polygon[index]!, b = polygon[(index + 1) % polygon.length]!;
    const insideA = keepAbove ? a[2] >= plane : a[2] <= plane;
    const insideB = keepAbove ? b[2] >= plane : b[2] <= plane;
    if (insideA) out.push(a);
    if (insideA !== insideB) {
      const t = (plane - a[2]) / (b[2] - a[2]);
      out.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), plane]);
    }
  }
  return out;
}

/** Moving each tested point 3 mm outward must still leave it inside the depth proxy. This expresses the
 * safety margin as a geometric containment property, independent of the fitter's boundary calculation. */
function clearanceValue(point: Point): number {
  const [rx, ry, rz] = TEMPLE_HEAD_VOLUME.scaleCm, [cx, cy, cz] = TEMPLE_HEAD_VOLUME.centerCm;
  const x = point[0] * 100 + GLASSES_OFFSET_CM[0] - cx;
  const y = point[1] * 100 + GLASSES_OFFSET_CM[1] - cy;
  const z = point[2] * 100 + GLASSES_OFFSET_CM[2] - cz;
  return ((Math.abs(x) + .3) / rx) ** 2 + (y / ry) ** 2 + (z / rz) ** 2;
}

for (const definition of Object.values(SHIPPED_EYEWEAR)) test(`${definition.name}: the drawn terminal band is contained, with the optical front and fixed baseline preserved`, async t => {
  const bytes = await readFile(new URL('../public' + definition.assetUrl, import.meta.url));
  const gltf = await new GLTFLoader().register(() => ({name: 'TerminalFitGeometryOnly', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const resources = new Set<BufferGeometry | Material | Texture>();
  const meshes: Mesh[] = [];
  gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    meshes.push(object); resources.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      resources.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) resources.add(value);
    }
  });
  const rear = createRearDrop(gltf.scene, definition.templeClipLocalZM);
  t.after(() => {rear.dispose(); for (const resource of resources) resource.dispose();});
  const continuity = buildTempleContinuityModel(gltf.scene, definition.templeClipLocalZM);
  assert.ok(rear.hingeZM !== null, 'the actual asset supplies its own hinge');
  const maximumZM = definition.templeClipLocalZM + .025;
  const fit = createTempleTerminalFit(gltf.scene, {offsetCm: definition.offsetCm, spreadM: .018,
    spreadStartZM: rear.spreadStartZM, modelCutoffZM: definition.templeClipLocalZM, maximumZM});
  assert.ok(fit, 'both shipped frames support the fixed posterior return');
  assert.ok(fit.negativeInsetM > 0 && fit.positiveInsetM > 0);
  rear.setShape(0, .018);
  const baseline = meshes.map(mesh => ({positions: mesh.geometry.getAttribute('position').array.slice(),
    normals: mesh.geometry.getAttribute('normal')?.array.slice()}));
  rear.setTerminalFit(fit);

  const counts = [0, 0], capIntersections = [0, 0], drawnTriangles: Point[][] = [];
  let changedPosterior = 0, testedFront = 0, worstClearance = 0;
  for (const [meshIndex, mesh] of meshes.entries()) {
    const geometry = mesh.geometry, positions = geometry.getAttribute('position'), index = geometry.getIndex();
    const normals = geometry.getAttribute('normal'), saved = baseline[meshIndex]!;
    for (let vertex = 0; vertex < positions.count; vertex++) {
      const offset = vertex * 3, z = positions.getZ(vertex);
      assert.equal(positions.getY(vertex), saved.positions[offset + 1]);
      assert.equal(z, saved.positions[offset + 2]);
      if (z >= fit.startZM) {
        testedFront++;
        for (let axis = 0; axis < 3; axis++) {
          assert.equal(positions.getComponent(vertex, axis), saved.positions[offset + axis]);
          if (normals && saved.normals) assert.equal(normals.getComponent(vertex, axis), saved.normals[offset + axis]);
        }
      } else if (positions.getX(vertex) !== saved.positions[offset]) changedPosterior++;
    }
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const [materialIndex, material] of materials.entries()) {
      if (material.transparent || material instanceof MeshPhysicalMaterial && material.transmission > 0) continue;
      const groups = Array.isArray(mesh.material) ? geometry.groups.filter(group => group.materialIndex === materialIndex)
        : [{start: 0, count: index?.count ?? positions.count}];
      for (const group of groups) for (let offset = group.start; offset < group.start + group.count; offset += 3) {
        const triangle: Point[] = [0, 1, 2].map(corner => {
          const vertex = index ? index.getX(offset + corner) : offset + corner;
          return [positions.getX(vertex), positions.getY(vertex), positions.getZ(vertex)];
        });
        if (Math.max(...triangle.map(point => point[2])) >= maximumZM && Math.min(...triangle.map(point => point[2])) <= -.05) drawnTriangles.push(triangle);
        const clipped = clipAtZ(clipAtZ(triangle, maximumZM, true), maximumZM + .005, false);
        if (clipped.length < 3) continue;
        const side = clipped[0]![0] < 0 ? 0 : 1;
        assert.ok(clipped.every(point => (point[0] < 0 ? 0 : 1) === side), 'terminal triangles remain on one arm');
        // A convex polygon's corners and centroid cover both generated cap edges and triangle interiors.
        const centroid: Point = [0, 1, 2].map(axis => clipped.reduce((sum, point) => sum + point[axis]!, 0) / clipped.length) as unknown as Point;
        for (const point of [...clipped, centroid]) {
          counts[side]!++;
          if (Math.abs(point[2] - maximumZM) < 1e-10) capIntersections[side]!++;
          worstClearance = Math.max(worstClearance, clearanceValue(point));
        }
      }
    }
  }
  assert.ok(testedFront > 100 && changedPosterior > 100, 'the test exercises the front and the deformed arms');
  assert.ok(counts.every(count => count > 100) && capIntersections.every(count => count > 0), 'both full terminal bands and their caps were examined');
  assert.ok(worstClearance <= 1 + 2e-7, `terminal triangles exceed the 3 mm clearance: ellipsoid value ${worstClearance}`);
  t.diagnostic(`${counts.join('/')} negative/positive band samples; maximum clearance ellipsoid value ${worstClearance.toFixed(9)}; insets ${(fit.negativeInsetM * 1000).toFixed(3)}/${(fit.positiveInsetM * 1000).toFixed(3)} mm`);

  // Intersect the deformed triangles independently at every relevant centreline station. The hair lookup must
  // follow this drawn cross-section, not the old outward-splayed arm, at close/far and oblique camera poses.
  let maximumMaskPixelError = 0, projectedChecks = 0;
  const sections = continuity.sides.map((stations, side) => stations.map(station => {
    if (station.zM < maximumZM || station.zM > -.05) return null;
    const intersections: Point[] = [];
    for (const triangle of drawnTriangles) for (let edge = 0; edge < 3; edge++) {
      const a = triangle[edge]!, b = triangle[(edge + 1) % 3]!;
      if (Math.abs(a[2] - b[2]) < 1e-12) continue;
      const amount = (station.zM - a[2]) / (b[2] - a[2]);
      if (amount < 0 || amount > 1) continue;
      const x = a[0] + amount * (b[0] - a[0]);
      if ((x < 0 ? 0 : 1) === side) intersections.push([x, a[1] + amount * (b[1] - a[1]), station.zM]);
    }
    assert.ok(intersections.length > 0, 'the drawn arm crosses every sampled rear plane');
    return [
      (Math.min(...intersections.map(point => point[0])) + Math.max(...intersections.map(point => point[0]))) / 2,
      (Math.min(...intersections.map(point => point[1])) + Math.max(...intersections.map(point => point[1]))) / 2,
      station.zM,
    ] as Point;
  }));
  for (const depth of [30, 75]) for (const [yaw, pitch] of [[0, 0], [40, -25], [-40, 25]]) {
    const pose = new Matrix4().makeRotationFromEuler(new Euler(pitch! * Math.PI / 180, yaw! * Math.PI / 180, 0))
      .setPosition(0, 0, -depth).toArray();
    const paths = projectTempleContinuity(continuity, {eyewearMatrix: pose, offsetCm: definition.offsetCm,
      width: 1280, height: 720, sourceAspect: 1280 / 720, dropM: 0, spreadM: .018,
      spreadStartZM: rear.spreadStartZM, terminalFit: fit});
    assert.ok(paths, 'the camera pose projects both temple paths');
    const projection = protectionProjection(pose, definition.offsetCm, 1280 / 720);
    for (const path of paths) for (const [index, section] of sections[path.side]!.entries()) {
      if (!section) continue;
      const p = new Vector4(...section, 1).applyMatrix4(projection);
      const drawnX = (p.x / p.w + 1) * 640, drawnY = (1 - p.y / p.w) * 360;
      const predicted = path.points[index]!;
      maximumMaskPixelError = Math.max(maximumMaskPixelError, Math.hypot(drawnX - predicted.x, drawnY - predicted.y) / 2);
      projectedChecks++;
    }
  }
  assert.ok(projectedChecks > 100);
  assert.ok(maximumMaskPixelError < 1, `hair lookup is more than one 640 px mask texel from the drawn arm: ${maximumMaskPixelError}`);
  t.diagnostic(`${projectedChecks} rear station/pose checks; maximum centreline error ${maximumMaskPixelError.toFixed(6)} mask pixels`);

  rear.setTerminalFit(null);
  for (const [index, mesh] of meshes.entries()) {
    assert.deepEqual(mesh.geometry.getAttribute('position').array, baseline[index]!.positions, 'removing the fit restores the exact spread baseline');
    assert.deepEqual(mesh.geometry.getAttribute('normal').array, baseline[index]!.normals, 'removing the fit restores baseline normals');
  }
});

test('the posterior return joins the fixed shaft and terminal segment with continuous zero slopes', () => {
  const fit = {startZM: -.05, endZM: -.11, maximumZM: -.115, negativeInsetM: .019, positiveInsetM: .024};
  const h = 1e-7;
  for (const x of [-.074, .074]) {
    assert.equal(terminalFitX(x, -.049, fit), x, 'the front is unmodified');
    assert.equal(terminalFitX(x, -.12, fit), terminalFitX(x, fit.endZM, fit), 'the final band is a constant translation');
    for (const z of [fit.startZM, fit.endZM]) {
      assert.equal(terminalReturn(z, fit).slope, 0);
      assert.equal(Math.abs(terminalFitSlope(x, z, fit)), 0);
      for (const direction of [-1, 1]) {
        const derivative = (terminalFitX(x, z + direction * h, fit) - terminalFitX(x, z, fit)) / (direction * h);
        assert.ok(Math.abs(derivative) < 1e-5, `the join has a geometric kink: derivative ${derivative}`);
      }
    }
    for (const z of [-.06, -.08, -.10]) {
      const derivative = (terminalFitX(x, z + h, fit) - terminalFitX(x, z - h, fit)) / (2 * h);
      assert.ok(Math.abs(derivative - terminalFitSlope(x, z, fit)) < 1e-7, 'normal correction matches the geometric derivative');
    }
  }
});

test('unusable external geometry declines the return instead of inventing a fit', () => {
  const input = {offsetCm: GLASSES_OFFSET_CM, spreadM: .018, spreadStartZM: -.015, modelCutoffZM: -.14, maximumZM: -.115};
  assert.equal(createTempleTerminalFit(new Group(), input), null, 'missing shafts');
  const root = new Group(), material = new MeshStandardMaterial(), geometry = new BoxGeometry(.003, .003, .07);
  geometry.translate(-.25, 0, -.09); root.add(new Mesh(geometry, material));
  const other = geometry.clone(); other.translate(.5, 0, 0); root.add(new Mesh(other, material));
  try {
    assert.equal(createTempleTerminalFit(root, input), null, 'an implausibly wide model requires excessive inset');
    assert.equal(createTempleTerminalFit(root, {...input, maximumZM: -.045}), null, 'no room for the transition');
    assert.equal(createTempleTerminalFit(root, {...input, spreadM: NaN}), null, 'invalid input');
    assert.equal(createTempleTerminalFit(root, {...input, offsetCm: [0, 30, GLASSES_OFFSET_CM[2]]}), null, 'the terminal lies outside the head in Y');
  } finally {geometry.dispose(); other.dispose(); material.dispose();}
});
