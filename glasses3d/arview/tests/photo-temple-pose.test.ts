import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BufferAttribute, Mesh, Texture} from 'three';
import {buildGlb, defaultParts} from './glb-fab.ts';
import {loadModel} from '../src/model/load.ts';
import {normalizeModel} from '../src/model/normalize.ts';
import {ModelReportBuilder} from '../src/model/report.ts';
import {createPhotoTemplePose, parsePhotoTemplePose} from '../src/render/photo-temple-pose.ts';
import {parseCameraSet} from '../qa/capture/cameras.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';

async function fixture() {
  const file = buildGlb().glb, report = new ModelReportBuilder('test', 'test', []);
  const loaded = await loadModel(file, report, {url: 'http://127.0.0.1/test.glb', renderer: null,
    plugins: [() => ({name: 'stub', loadTexture: async () => new Texture()})]});
  normalizeModel(loaded.gltf.scene, report, loaded.source);
  const meshes: Mesh[] = [];
  loaded.gltf.scene.traverse(o => {if (o instanceof Mesh) meshes.push(o);});
  return {root: loaded.gltf.scene, meshes};
}

test('temple-private storage protects a frame sharing geometry and preserves tangent handedness', async () => {
  const {root, meshes} = await fixture();
  const temple = meshes.find(m => m.name === 'Temple_R')!, frame = meshes.find(m => m.name === 'Bridge')!;
  const source = temple.geometry, position = source.getAttribute('position');
  source.setAttribute('normal', new BufferAttribute(Float32Array.from(Array.from({length: position.count}, () => [0, 0, 1]).flat()), 3));
  source.setAttribute('tangent', new BufferAttribute(Float32Array.from(Array.from({length: position.count}, (_, i) => [1, 0, 0, i % 2 ? -1 : 1]).flat()), 4));
  frame.geometry = source;
  const before = Float32Array.from(position.array), tangentBefore = Float32Array.from(source.getAttribute('tangent').array);
  const controller = createPhotoTemplePose(root);
  assert.equal(frame.geometry, source);
  assert.notEqual(temple.geometry, source);
  const pose = parsePhotoTemplePose({'+1': {theta_deg: 31, axis_xz_mm: [68, -2]}});
  for (let repeat = 0; repeat < 2; repeat++) {
    controller.apply(pose);
    const tangent = temple.geometry.getAttribute('tangent'), normal = temple.geometry.getAttribute('normal');
    for (let i = 0; i < tangent.count; i++) {
      assert.ok(Math.abs(tangent.getX(i) - Math.cos(31 * Math.PI / 180)) < 1e-7);
      assert.ok(Math.abs(tangent.getZ(i) - Math.sin(31 * Math.PI / 180)) < 1e-7);
      assert.ok(Math.abs(normal.getX(i) + Math.sin(31 * Math.PI / 180)) < 1e-7);
      assert.ok(Math.abs(normal.getZ(i) - Math.cos(31 * Math.PI / 180)) < 1e-7);
      assert.equal(tangent.getW(i), i % 2 ? -1 : 1);
    }
  }
  assert.deepEqual(Float32Array.from(source.getAttribute('position').array), before);
  assert.deepEqual(Float32Array.from(source.getAttribute('tangent').array), tangentBefore);
  controller.reset();
  assert.deepEqual(Float32Array.from(temple.geometry.getAttribute('position').array), before);
  assert.deepEqual(Float32Array.from(temple.geometry.getAttribute('tangent').array), tangentBefore);
});

test('photo openings and rear-drop clone transitions reproduce the same authored and worn geometry', async () => {
  const {root, meshes} = await fixture();
  const authored = meshes.map(m => Float32Array.from(m.geometry.getAttribute('position').array));
  const controller = createPhotoTemplePose(root), rearDrop = createRearDrop(root, -.115, 0, {sides: [true, true]});
  rearDrop.setShape(0, .004);
  const worn = meshes.map(m => Float32Array.from(m.geometry.getAttribute('position').array));
  assert.ok(meshes.some((_, i) => worn[i]!.some((x, j) => x !== authored[i]![j])));
  for (const theta of [17, -8, 24]) {
    rearDrop.setTerminalFit(null); rearDrop.setShape(0, 0);
    controller.apply(parsePhotoTemplePose({'+1': {theta_deg: theta, axis_xz_mm: [68, -2]},
      '-1': {theta_deg: -theta / 2, axis_xz_mm: [-68, -2]}}));
    controller.reset();
    meshes.forEach((m, i) => assert.deepEqual(Float32Array.from(m.geometry.getAttribute('position').array), authored[i]));
    rearDrop.setShape(0, .004);
    meshes.forEach((m, i) => assert.deepEqual(Float32Array.from(m.geometry.getAttribute('position').array), worn[i]));
  }
  rearDrop.dispose();
  meshes.forEach((m, i) => assert.deepEqual(Float32Array.from(m.geometry.getAttribute('position').array), authored[i]));
});

test('photo opening moves both temples and their own markings, resets without accumulation or source mutation', async () => {
  const file = buildGlb({parts: {...defaultParts(), label: {box: [70, 70.1, 3, 5, -30, -25], material: 'frame', role: 'temple'}}}).glb;
  const beforeFile = file.slice(), report = new ModelReportBuilder('test', 'test', []);
  const loaded = await loadModel(file, report, {url: 'http://127.0.0.1/test.glb', renderer: null,
    plugins: [() => ({name: 'stub', loadTexture: async () => new Texture()})]});
  const root = loaded.gltf.scene;
  normalizeModel(root, report, loaded.source);
  const meshes: Mesh[] = [];
  root.traverse(o => {if (o instanceof Mesh) meshes.push(o);});
  const snapshots = meshes.map(m => Float32Array.from(m.geometry.getAttribute('position').array));
  const controller = createPhotoTemplePose(root);
  for (const [right, left] of [[12, -7], [-4, 18], [0, 0], [12, -7]]) {
    const pose = parsePhotoTemplePose({'+1': {theta_deg: right, axis_xz_mm: [68, -2]}, '-1': {theta_deg: left, axis_xz_mm: [-68, -2]}});
    controller.apply(pose);
    for (const [n, mesh] of meshes.entries()) {
      const original = snapshots[n]!, actual = mesh.geometry.getAttribute('position');
      const side = mesh.name === 'Temple_L' ? -1 : mesh.name === 'Temple_R' || mesh.name === 'label' ? 1 : 0;
      const theta = (side > 0 ? right! : left!) * side * Math.PI / 180;
      for (let i = 0; i < actual.count; i++) {
        const x = original[i * 3]!, z = original[i * 3 + 2]!, dx = x - side * .068, dz = z + .002;
        const expected = side ? [side * .068 + Math.cos(theta) * dx - Math.sin(theta) * dz,
          original[i * 3 + 1]!, -.002 + Math.sin(theta) * dx + Math.cos(theta) * dz] : [...original.slice(i * 3, i * 3 + 3)];
        assert.ok(Math.abs(actual.getX(i) - expected[0]!) < 1e-8);
        assert.ok(Math.abs(actual.getY(i) - expected[1]!) < 1e-8);
        assert.ok(Math.abs(actual.getZ(i) - expected[2]!) < 1e-8);
      }
    }
  }
  controller.reset();
  meshes.forEach((mesh, n) => assert.deepEqual(Float32Array.from(mesh.geometry.getAttribute('position').array), snapshots[n]));
  assert.deepEqual(file, beforeFile);
});

test('camera pose schema refuses non-finite/wrong-side axes before capture', () => {
  for (const value of [null, [], {'0': {theta_deg: 1, axis_xz_mm: [1, 2]}},
    {'+1': {theta_deg: NaN, axis_xz_mm: [68, -2]}}, {'-1': {theta_deg: 2, axis_xz_mm: [68, -2]}},
    {'+1': {theta_deg: 2, axis_xz_mm: [68]}}]) assert.throws(() => parsePhotoTemplePose(value), /temple_pose/);
  const camera = {id: 'a', width: 640, height: 480, view: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -.5, 0, 0, 0, 1],
    background: '#ffffff', fx: 500, fy: 500, cx: 319.5, cy: 239.5, near: .1, far: 1,
    temple_pose: {'+1': {theta_deg: 10, axis_xz_mm: [68, -2]}}};
  const result = parseCameraSet({schema: 'arview.cameras/1', cameras: [camera]});
  assert.deepEqual(result.cameras[0]!.temple_pose, camera.temple_pose);
  assert.ok(Object.isFrozen(result.cameras[0]!.temple_pose!['+1']!.axis_xz_mm));
});
