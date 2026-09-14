import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import {
  Box3, BufferGeometry, Float32BufferAttribute, Group, InterleavedBuffer, InterleavedBufferAttribute,
  Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Texture, Vector3,
} from 'three';
import type {Material} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {EYEWEAR} from '../../src/render/eyewear.ts';
import {createRearDrop, rearDropCurve, rearDropForPose} from './rear-drop.ts';

const close = (actual: number, expected: number, tolerance = 1e-7) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
const pose = (down: number, yaw = 0, roll = 0) => new Matrix4().makeRotationZ(roll * Math.PI / 180)
  .multiply(new Matrix4().makeRotationY(yaw * Math.PI / 180)).multiply(new Matrix4().makeRotationX(down * Math.PI / 180))
  .setPosition(0, 0, -40).toArray();

function fixture(interleaved = false) {
  // Lens rear -10mm => start approximately -25mm. Keep the proximal sample
  // strictly ahead of that computed plane despite Float32 representation.
  const positions = [.065, .005, 0, .065, .005, -.024, .065, .005, -.0625,
    .065, .005, -.1, -.065, .005, -.0625, .03, .005, -.0625];
  const geometry = new BufferGeometry();
  if (interleaved) {
    const values = positions.flatMap((value, index) => index % 3 === 0
      ? [value, positions[index + 1]!, positions[index + 2]!, 0, 1, 0] : []);
    const buffer = new InterleavedBuffer(new Float32Array(values), 6);
    geometry.setAttribute('position', new InterleavedBufferAttribute(buffer, 3, 0));
    geometry.setAttribute('normal', new InterleavedBufferAttribute(buffer, 3, 3));
  } else {
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new Float32BufferAttribute(Array.from({length: 6}, () => [0, 1, 0]).flat(), 3));
  }
  geometry.setAttribute('tangent', new Float32BufferAttribute(Array.from({length: 6}, () => [0, 0, 1, -1]).flat(), 4));
  const frameMaterial = new MeshStandardMaterial(), frame = new Mesh(geometry, frameMaterial);
  const lensGeometry = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([-.02, 0, -.01, .02, 0, -.01, 0, .02, -.005], 3));
  const lensMaterial = new MeshPhysicalMaterial({transmission: 1}), lens = new Mesh(lensGeometry, lensMaterial);
  const root = new Group().add(frame, lens);
  return {root, frame, lens, geometry, dispose() {geometry.dispose(); lensGeometry.dispose(); frameMaterial.dispose(); lensMaterial.dispose();}};
}

test('smooth posterior curve keeps start fixed, reaches the cap, and has the analytic Jacobian', () => {
  const start = -.025, cap = -.1, drop = .02;
  assert.deepEqual(rearDropCurve(start, start, cap, drop), {loweringM: 0, dyDz: 0});
  assert.deepEqual(rearDropCurve(0, start, cap, drop), {loweringM: 0, dyDz: 0});
  assert.deepEqual(rearDropCurve(cap, start, cap, drop), {loweringM: drop, dyDz: 0});
  assert.deepEqual(rearDropCurve(-.15, start, cap, drop), {loweringM: drop, dyDz: 0});
  const z = -.0625, epsilon = 1e-7, curve = rearDropCurve(z, start, cap, drop);
  close(curve.loweringM, .01);
  close(curve.dyDz, .4);
  const derivative = (-rearDropCurve(z + epsilon, start, cap, drop).loweringM + rearDropCurve(z - epsilon, start, cap, drop).loweringM) / (2 * epsilon);
  close(curve.dyDz, derivative, 1e-8);
  for (const invalid of [-.001, .03001, NaN, Infinity]) assert.throws(() => rearDropCurve(z, start, cap, invalid));
});

for (const interleaved of [false, true]) test(`owned clones preserve X/Z, front, lens, originals and analytic normal/tangent directions; interleaved=${interleaved}`, t => {
  const f = fixture(interleaved); t.after(f.dispose);
  const originalP = f.geometry.getAttribute('position'), originalN = f.geometry.getAttribute('normal');
  const originalT = f.geometry.getAttribute('tangent');
  const beforePositions = Array.from({length: originalP.count}, (_, i) => [originalP.getX(i), originalP.getY(i), originalP.getZ(i)]);
  const normalValues = Array.from({length: originalN.count}, (_, i) => [originalN.getX(i), originalN.getY(i), originalN.getZ(i)]);
  const tangentValues = Array.from({length: originalT.count}, (_, i) => [originalT.getX(i), originalT.getY(i), originalT.getZ(i), originalT.getW(i)]);
  const controller = createRearDrop(f.root, -.1); t.after(() => controller.dispose());
  const cloned = f.frame.geometry;
  assert.notEqual(cloned, f.geometry);
  close(controller.diagnostics.startZM, -.025);
  assert.equal(controller.diagnostics.fadeLengthM, .015);
  controller.setDrop(.02);
  const p = cloned.getAttribute('position'), n = cloned.getAttribute('normal'), tangent = cloned.getAttribute('tangent');
  for (let i = 0; i < p.count; i++) {
    assert.equal(p.getX(i), originalP.getX(i)); assert.equal(p.getZ(i), originalP.getZ(i));
    if ([0, 1, 5].includes(i)) {
      close(p.getY(i), originalP.getY(i));
      assert.deepEqual([n.getX(i), n.getY(i), n.getZ(i)], normalValues[i]);
    }
  }
  close(p.getY(2), -.005); close(p.getY(3), -.015);
  const expectedNormal = new Vector3(0, 1, -.4).normalize(), expectedTangent = new Vector3(0, .4, 1).normalize();
  close(n.getY(2), expectedNormal.y); close(n.getZ(2), expectedNormal.z);
  close(tangent.getY(2), expectedTangent.y); close(tangent.getZ(2), expectedTangent.z);
  assert.equal(tangent.getW(2), -1);
  close(new Vector3(n.getX(2), n.getY(2), n.getZ(2)).dot(new Vector3(tangent.getX(2), tangent.getY(2), tangent.getZ(2))), 0);
  assert.deepEqual(Array.from({length: originalP.count}, (_, i) => [originalP.getX(i), originalP.getY(i), originalP.getZ(i)]), beforePositions);
  const changedBounds = controller.candidateArmBounds;
  assert.equal(changedBounds.length, 2);
  assert.ok(changedBounds.every(bounds => bounds instanceof Box3));
  changedBounds[0]!.makeEmpty();
  assert.equal(controller.candidateArmBounds[0]!.isEmpty(), false, 'bounds access is owned');
  const beforeInvalid = p.getY(2);
  assert.throws(() => controller.setDrop(.031)); assert.equal(p.getY(2), beforeInvalid);
  controller.setDrop(0);
  assert.deepEqual(Array.from({length: p.count}, (_, i) => [p.getX(i), p.getY(i), p.getZ(i)]), beforePositions);
  assert.deepEqual(Array.from({length: n.count}, (_, i) => [n.getX(i), n.getY(i), n.getZ(i)]), normalValues);
  assert.deepEqual(Array.from({length: tangent.count}, (_, i) => [tangent.getX(i), tangent.getY(i), tangent.getZ(i), tangent.getW(i)]), tangentValues);
  const overlay = f.frame.clone(false); f.root.add(overlay);
  let cloneDisposals = 0, originalDisposals = 0;
  cloned.addEventListener('dispose', () => cloneDisposals++); f.geometry.addEventListener('dispose', () => originalDisposals++);
  controller.dispose(); controller.dispose();
  assert.equal(f.frame.geometry, f.geometry); assert.equal(overlay.geometry, f.geometry);
  assert.equal(cloneDisposals, 1); assert.equal(originalDisposals, 0);
  assert.throws(() => controller.setDrop(.01), /disposed/);
});

test('material groups protect shared lens vertices and meshes reuse a single owned geometry clone', t => {
  const f = fixture(); t.after(f.dispose);
  const g = f.geometry;
  g.setIndex([0, 1, 2, 2, 3, 4]); g.addGroup(0, 3, 1); g.addGroup(3, 3, 0);
  const originalMaterial = f.frame.material;
  (f.frame as Mesh).material = [originalMaterial, f.lens.material];
  const sibling = new Mesh(g, originalMaterial); f.root.add(sibling);
  const controller = createRearDrop(f.root, -.1); t.after(() => controller.dispose());
  assert.equal(f.frame.geometry, sibling.geometry);
  const p = f.frame.geometry.getAttribute('position');
  controller.setDrop(.02);
  assert.equal(p.getY(2), g.getAttribute('position').getY(2), 'a vertex used by any physical lens stays unchanged');
  controller.dispose(); assert.equal(sibling.geometry, g);
});

test('protection includes undeformed proximal shafts, small-X posterior pads and non-lens transparency', t => {
  const f = fixture(); t.after(f.dispose);
  const otherGeometry = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([.09, -.02, -.08, .08, -.01, -.08, .09, -.01, -.07], 3));
  const otherMaterial = new MeshStandardMaterial({transparent: true, opacity: .5});
  f.root.add(new Mesh(otherGeometry, otherMaterial));
  t.after(() => {otherGeometry.dispose(); otherMaterial.dispose();});
  const controller = createRearDrop(f.root, -.1); t.after(() => controller.dispose());
  const bounds = controller.opticalBounds, p = f.geometry.getAttribute('position');
  for (const index of [0, 1, 5]) assert.ok(bounds.containsPoint(new Vector3().fromBufferAttribute(p, index)));
  assert.ok(bounds.containsPoint(new Vector3().fromBufferAttribute(otherGeometry.getAttribute('position'), 0)));
  assert.equal(controller.diagnostics.protectionIncludesAllUndeformedReferencedVertices, true);
});

test('nonidentity mesh transforms reject without replacing or disposing source geometry', t => {
  const f = fixture(); t.after(f.dispose);
  f.frame.position.y = .01;
  let sourceDisposals = 0; f.geometry.addEventListener('dispose', () => sourceDisposals++);
  assert.throws(() => createRearDrop(f.root, -.1), /identity mesh transforms/);
  assert.equal(f.frame.geometry, f.geometry); assert.equal(sourceDisposals, 0);
});

test('down factory is continuous, keeps up/level and both yaw controls at zero, and rejects disagreement', () => {
  for (const down of [-45, -10, 0, 5, 8]) assert.equal(rearDropForPose(pose(down)), 0);
  close(rearDropForPose(pose(28)), .02);
  close(rearDropForPose(pose(40), .01), .01);
  for (const sign of [-1, 1]) for (const down of [10, 28, 50, 75]) assert.equal(rearDropForPose(pose(down, sign * 26)), 0);
  for (const roll of [-15, 15]) {
    assert.ok(rearDropForPose(pose(30, 0, roll)) > 0);
    assert.equal(rearDropForPose(pose(-30, 0, roll)), 0);
  }
  const conflicting = new Matrix4().fromArray(pose(28)).setPosition(0, 30, -40).toArray();
  assert.equal(rearDropForPose(conflicting), 0);
  let previous = 0;
  for (let down = 0; down <= 50; down += .1) {
    const value = rearDropForPose(pose(down));
    assert.ok(value >= previous - 1e-12 && value - previous < .0002); previous = value;
  }
  assert.throws(() => rearDropForPose(Array(16).fill(0)), /singular/);
  assert.throws(() => rearDropForPose([NaN]), /invalid/);
  assert.throws(() => rearDropForPose(pose(30), .031));
});

for (const definition of Object.values(EYEWEAR)) test(`${definition.name}: original GLB buffers, optical front, X/Z and 15mm original-Z fade convention stay exact`, async t => {
  const bytes = await readFile(new URL('../../public' + definition.assetUrl, import.meta.url));
  const gltf = await new GLTFLoader().register(() => ({name: 'MathOnlyTextureStub', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const meshes: Mesh[] = [], geometries = new Set<BufferGeometry>(), materials = new Set<Material>(), textures = new Set<Texture>();
  gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    meshes.push(object); geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
    }
  });
  const originals = meshes.map(mesh => ({mesh, geometry: mesh.geometry, position: mesh.geometry.getAttribute('position').array.slice(),
    normal: mesh.geometry.getAttribute('normal').array.slice(), index: mesh.geometry.getIndex()!.array.slice()}));
  const controller = createRearDrop(gltf.scene, definition.templeClipLocalZM);
  t.after(() => {controller.dispose(); for (const resource of [...geometries, ...materials, ...textures]) resource.dispose();});
  controller.setDrop(.02);
  assert.equal(controller.diagnostics.cutoffZM, definition.templeClipLocalZM);
  assert.equal(controller.diagnostics.fadeLengthM, .015);
  for (const original of originals) {
    assert.deepEqual(original.geometry.getAttribute('position').array, original.position);
    assert.deepEqual(original.geometry.getAttribute('normal').array, original.normal);
    const p = original.mesh.geometry.getAttribute('position'), n = original.mesh.geometry.getAttribute('normal');
    const lens = original.mesh.material instanceof MeshPhysicalMaterial && original.mesh.material.transmission > 0;
    for (let i = 0; i < p.count; i++) {
      assert.equal(p.getX(i), original.position[i * 3]); assert.equal(p.getZ(i), original.position[i * 3 + 2]);
      if (lens || original.position[i * 3 + 2]! >= controller.diagnostics.startZM || Math.abs(original.position[i * 3]!) <= .045) {
        assert.equal(p.getY(i), original.position[i * 3 + 1]);
        assert.deepEqual([n.getX(i), n.getY(i), n.getZ(i)], Array.from(original.normal.slice(i * 3, i * 3 + 3)));
      }
    }
    assert.deepEqual(original.mesh.geometry.getIndex()!.array, original.index);
  }
  controller.setDrop(0);
  for (const original of originals) {
    assert.deepEqual(original.mesh.geometry.getAttribute('position').array, original.position);
    assert.deepEqual(original.mesh.geometry.getAttribute('normal').array, original.normal);
  }
});
