import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {BoxGeometry, BufferGeometry, Float32BufferAttribute, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, Texture, Vector3} from 'three';
import type {Material} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {buildTempleContinuityModel, loadTempleContinuityModel, projectTempleContinuity,
  registerPinnedGeometry, verifyPinnedGeometryBytes} from '../src/render/continuity.ts';
import type {ProjectedTemplePath} from '../src/render/continuity.ts';

test('geometry verification always hashes bytes and distinguishes missing pins from mismatches', async () => {
  const bytes = new Uint8Array([1, 2, 3]).buffer;
  const sha256 = createHash('sha256').update(new Uint8Array(bytes)).digest('hex');
  assert.deepEqual(await verifyPinnedGeometryBytes('memory:unregistered-verification.glb', bytes), {sha256, pinned: false});
  await assert.rejects(verifyPinnedGeometryBytes('memory:unregistered-verification.glb', bytes, true), /No pinned/);
  const url = 'memory:pinned-verification.glb?request=1';
  registerPinnedGeometry(url, sha256);
  assert.deepEqual(await verifyPinnedGeometryBytes(url, bytes, true), {sha256, pinned: true});
  const changed = new Uint8Array([1, 2, 4]).buffer;
  await assert.rejects(verifyPinnedGeometryBytes(url, changed), /differs from the pinned/);
  await assert.rejects(verifyPinnedGeometryBytes(url, changed, true), /differs from the pinned/);
});

test('continuity parses the supplied pinned bytes without fetching a second asset', async () => {
  const raw = await readFile(new URL('../public/models/tom-ford-clear.glb', import.meta.url));
  const bytes = new Uint8Array(raw).buffer;
  const sha256 = createHash('sha256').update(raw).digest('hex');
  // fetch cannot load this scheme: success proves no network/global fetch mock is needed.
  const url = 'memory:shared-render-and-continuity.glb';
  registerPinnedGeometry(url, sha256);
  const model = await loadTempleContinuityModel(url, -.11, new AbortController().signal, bytes);
  assert.equal(model.assetSHA256, sha256);
  assert.equal(model.assetUrl, url);
  assert.equal(model.sides.length, 2);
  assert.ok(model.sides.every(side => side.length === 33));
  const changed = bytes.slice(0), changedView = new Uint8Array(changed); changedView[0] = changedView[0]! ^ 1;
  await assert.rejects(loadTempleContinuityModel(url, -.11, new AbortController().signal, changed), /differs from the pinned/);
  const aborted = new AbortController(); aborted.abort();
  await assert.rejects(loadTempleContinuityModel(url, -.11, aborted.signal, bytes), {name: 'AbortError'});
});

test('real triangle cross-sections preserve buffers and project the downward Y curve with both yaw signs', () => {
  const root = new Group(), left = new BoxGeometry(.004, .004, .1), right = left.clone();
  left.translate(-.065, 0, -.07); right.translate(.065, 0, -.07);
  const frame = new MeshStandardMaterial(), lensMaterial = new MeshPhysicalMaterial({transmission: 1});
  const lens = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([-.02, 0, -.01, .02, 0, -.01, 0, .01, -.005], 3));
  root.add(new Mesh(left, frame), new Mesh(right, frame), new Mesh(lens, lensMaterial));
  const original = left.getAttribute('position').array.slice(), model = buildTempleContinuityModel(root, -.1);
  assert.deepEqual(left.getAttribute('position').array, original); assert.equal(model.sides[0]!.length, 33);
  assert.ok(Math.abs(model.sides[0]![0]!.centerXM + .065) < 1e-6);
  for (const yaw of [-30, 30]) {
    const pose = new Matrix4().makeRotationY(yaw * Math.PI / 180).setPosition(0, 0, -40).toArray();
    const input = {eyewearMatrix: pose, offsetCm: [0, 3.271027, 6.531958919387042] as const, sourceAspect: 1.5, width: 1200, height: 800, dropM: 0};
    const originalPaths = projectTempleContinuity(model, input)!, down = projectTempleContinuity(model, {...input, dropM: .02})!;
    assert.equal(originalPaths.length, 2); assert.ok(originalPaths.every(path => path.lengthPx > 8));
    for (const side of [0, 1]) {
      assert.equal(down[side]!.points[0]!.x, originalPaths[side]!.points[0]!.x);
      assert.equal(down[side]!.points[0]!.y, originalPaths[side]!.points[0]!.y);
      assert.ok(down[side]!.points.at(-1)!.y > originalPaths[side]!.points.at(-1)!.y);
    }
    const attachment = new Group(); attachment.matrixAutoUpdate = false; attachment.matrix.fromArray(pose);
    const asset = new Group(); asset.position.set(...input.offsetCm); attachment.add(asset);
    const camera = new PerspectiveCamera(63, input.sourceAspect, 1, 10_000); camera.updateMatrixWorld();
    for (const fitScale of [.8, 1, 1.25]) {
      asset.scale.setScalar(100 * fitScale); attachment.updateMatrixWorld(true);
      const paths = projectTempleContinuity(model, {...input, fitScale}); assert.ok(paths);
      for (const path of paths) for (const [index, station] of model.sides[path.side]!.entries()) {
        const projected = new Vector3(station.centerXM, station.centerYM, station.zM).applyMatrix4(asset.matrixWorld).project(camera);
        const x = (projected.x + 1) * input.width / 2, y = (1 - projected.y) * input.height / 2;
        assert.ok(Math.hypot(x - path.points[index]!.x, y - path.points[index]!.y) < 1e-8,
          'hair evidence follows the rendered station after uniform visual fitting');
      }
    }
    for (const fitScale of [0, -1, NaN, Infinity]) assert.equal(projectTempleContinuity(model, {...input, fitScale}), null);
  }
  for (const resource of [left, right, lens, frame, lensMaterial]) resource.dispose();
});

for (const [id, cap] of [['tom-ford-clear', -.11], ['amber-horizon', -.105]] as const) test(`${id}: both original GLB shafts have complete ordered cross-sections`, async () => {
  const bytes = await readFile(new URL(`../public/models/${id}.glb`, import.meta.url));
  const gltf = await new GLTFLoader().register(() => ({name: 'GeometryOnlyTestTextures', loadTexture: async () => new Texture()})).parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const geometries = new Set<BufferGeometry>(), materials = new Set<Material>(), textures = new Set<Texture>();
  gltf.scene.traverse(object => {if (!(object instanceof Mesh)) return; geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);}});
  try {
    const original = [...geometries].map(geometry => geometry.getAttribute('position').array.slice());
    const model = buildTempleContinuityModel(gltf.scene, cap);
    assert.equal(model.sides.length, 2); assert.ok(model.sides.every(side => side.length === 33));
    for (const [index, geometry] of [...geometries].entries()) assert.deepEqual(geometry.getAttribute('position').array, original[index]);
    for (const [side, points] of model.sides.entries()) {
      assert.ok(points.every(point => side === 0 ? point.centerXM < -.045 : point.centerXM > .045));
      assert.equal(points.at(-1)!.zM, cap);
      assert.ok(points.every((point, index) => index === 0 || point.zM < points[index - 1]!.zM));
    }
  } finally {for (const resource of [...textures, ...materials, ...geometries]) resource.dispose();}
});

// Keep the public path type checked independently of the GLB loader.
const samplePath: ProjectedTemplePath = {side: 0, lengthPx: 10, points: [{x: 0, y: 0, radiusPx: 1, progressPx: 0}, {x: 10, y: 0, radiusPx: 1, progressPx: 10}]};
assert.equal(samplePath.points.length, 2);
