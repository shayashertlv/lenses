import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {BoxGeometry, BufferGeometry, Float32BufferAttribute, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, PerspectiveCamera, Texture, Vector3} from 'three';
import type {Material} from 'three';
import {buildTempleContinuityModel, projectTempleContinuity} from '../src/render/continuity.ts';
import {checkModel} from '../src/model/check.ts';
import {buildGlb, replaceBox} from './glb-fab.ts';
import {deriveTempleClip, templeInventory} from '../src/render/temple-geometry.ts';
import {publicFile} from './shipped-models.ts';
import {classifyAssetMaterials} from '../src/eyewear/optical-material.ts';

// The pin registry and the second, un-normalised continuity parse (loadTempleContinuityModel) are gone
// (WP1_DESIGN.md §3). Their two tests ('geometry verification always hashes bytes and distinguishes missing pins from
// mismatches', 'continuity parses the supplied pinned bytes without fetching a second asset') went with them; the
// renderer's continuity comes from the scene the load front-end parsed and normalised, as below.
const stubTextures = () => ({name: 'ContinuityTestTextures', loadTexture: async () => new Texture()});
async function normalisedScene(bytes: ArrayBuffer, file: string) {
  const {report, scene} = await checkModel(bytes, {url: `http://127.0.0.1:8250/models/${file}`, renderer: null, plugins: [stubTextures]});
  assert.equal(report.status, 'rendered', report.refusal.join(' | '));
  return {report, scene: scene!};
}

test('continuity is built from the normalised scene of the shipped asset: no pin, no second parse', async () => {
  const raw = await readFile(publicFile('models/tom-ford-clear.glb'));
  const {report, scene} = await normalisedScene(new Uint8Array(raw).buffer, 'tom-ford-clear.glb');
  assert.equal(report.rendered_sha256, createHash('sha256').update(raw).digest('hex'), 'the identity is the rendered bytes');
  const model = buildTempleContinuityModel(scene, -.11);
  assert.equal(model.sides.length, 2);
  assert.ok(model.sides.every(side => side.length === 33));
});

test('a millimetre file gives the same continuity paths as the metre file once the normalisation pass rescaled it', async () => {
  const metres = buildTempleContinuityModel((await normalisedScene(buildGlb().glb, 'm.glb')).scene, -.11);
  const {report, scene} = await normalisedScene(buildGlb({scale: 1.0}).glb, 'mm.glb');
  assert.equal(report.measurements.units_scale, 0.001);
  const millimetres = buildTempleContinuityModel(scene, -.11);
  assert.ok(Math.abs(millimetres.startZM - metres.startZM) < 1e-6, `start ${millimetres.startZM} vs ${metres.startZM}`);
  for (const side of [0, 1]) {
    for (const [index, station] of millimetres.sides[side]!.entries()) {
      const reference = metres.sides[side]![index]!;
      for (const key of ['zM', 'centerXM', 'centerYM', 'minXM', 'maxXM', 'minYM', 'maxYM'] as const) {
        assert.ok(Math.abs(station[key] - reference[key]) < 1e-6, `side ${side} station ${index} ${key}: ${station[key]} vs ${reference[key]}`);
      }
    }
  }
});

// WP1 stage D2 (WP1_DESIGN.md §3): each side stands alone. An arm that ends before the clip used to make the whole model
// throw ('An original posterior arm cross-section is missing.', no side named), turning the endpoint off for both arms.
test('continuity without pins is per side: an arm ending before the clip loses only its own path, named with its z', async () => {
  const parts = replaceBox('Temple_R', [66, 70, 2, 8, -100, -2]);
  const {scene} = await normalisedScene(buildGlb({parts}).glb, 'short-right.glb');
  const inventory = templeInventory(scene), derived = deriveTempleClip(inventory);
  assert.deepEqual(inventory.present, [true, true]);
  assert.ok(Math.abs(derived.clipZM - (-.1 + 1e-6)) < 1e-7, 'the derived clip lies inside the shorter (right) arm (float32 tip)');
  assert.deepEqual(buildTempleContinuityModel(scene, derived.clipZM).sides.map(side => side.length), [33, 33]);
  // A handed-over clip behind the right arm's tip: only that arm has no section there.
  const handed = deriveTempleClip(inventory, -.14);
  assert.match(handed.warnings.join(), /at or behind the right temple's tip \(z -100\.0 mm\)/);
  const model = buildTempleContinuityModel(scene, handed.clipZM);
  assert.deepEqual(model.sides.map(side => side.length), [33, 0]);
  assert.equal(model.missing?.length, 1); assert.equal(model.missing![0]!.side, 1);
  assert.match(model.missing![0]!.reason, /^the right arm has no cross-section at z -10\d\.\d mm \(the 33 sections run from z -16\.0 mm to the clip at z -140\.0 mm\)/);
  const paths = projectTempleContinuity(model, {eyewearMatrix: new Matrix4().makeTranslation(0, 0, -45).toArray(),
    offsetCm: [0, 3.271027, 6.531958919387042], sourceAspect: 4 / 3, width: 640, height: 480, dropM: 0});
  assert.deepEqual(paths?.map(path => path.side), [0]);
});

test('real triangle cross-sections preserve buffers and project the downward Y curve with both yaw signs', () => {
  const root = new Group(), left = new BoxGeometry(.004, .004, .1), right = left.clone();
  left.translate(-.065, 0, -.07); right.translate(.065, 0, -.07);
  const frame = new MeshStandardMaterial(), lensMaterial = new MeshPhysicalMaterial({transmission: 1});
  const lens = new BufferGeometry().setAttribute('position', new Float32BufferAttribute([-.02, 0, -.01, .02, 0, -.01, 0, .01, -.005], 3));
  root.add(new Mesh(left, frame), new Mesh(right, frame), new Mesh(lens, lensMaterial));
  classifyAssetMaterials(root);   // untagged: the transmissive lens is a lens by the (warned) last resort
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
  const bytes = await readFile(publicFile(`models/${id}.glb`));
  // The load front-end and the normalisation pass, as the renderer runs them (the class is decided there, from the JSON).
  const {scene} = await normalisedScene(new Uint8Array(bytes).buffer, `${id}.glb`);
  const geometries = new Set<BufferGeometry>(), materials = new Set<Material>(), textures = new Set<Texture>();
  scene.traverse(object => {if (!(object instanceof Mesh)) return; geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);}});
  try {
    const original = [...geometries].map(geometry => geometry.getAttribute('position').array.slice());
    const model = buildTempleContinuityModel(scene, cap);
    assert.equal(model.sides.length, 2); assert.ok(model.sides.every(side => side.length === 33));
    for (const [index, geometry] of [...geometries].entries()) assert.deepEqual(geometry.getAttribute('position').array, original[index]);
    for (const [side, points] of model.sides.entries()) {
      assert.ok(points.every(point => side === 0 ? point.centerXM < -.045 : point.centerXM > .045));
      assert.equal(points.at(-1)!.zM, cap);
      assert.ok(points.every((point, index) => index === 0 || point.zM < points[index - 1]!.zM));
    }
  } finally {for (const resource of [...textures, ...materials, ...geometries]) resource.dispose();}
});
