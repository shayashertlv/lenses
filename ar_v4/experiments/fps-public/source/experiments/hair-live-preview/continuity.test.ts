import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, BufferGeometry, Float32BufferAttribute, Group, Matrix4, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Texture} from 'three';
import type {Material} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {buildTempleContinuityModel, findDetachedTemplePixels, projectTempleContinuity} from './continuity.ts';
import type {ContinuityInput, ProjectedTemplePath} from './continuity.ts';

function fixture(reversed = false): ContinuityInput {
  const width = 64, height = 18, background = new Uint8ClampedArray(width * height * 4).fill(20);
  for (let i = 3; i < background.length; i += 4) background[i] = 255;
  const before = background.slice();
  for (let y = 8; y <= 10; y++) for (let x = 8; x <= 50; x++) for (let c = 0; c < 3; c++) before[(y * width + x) * 4 + c] = 200;
  const points = reversed ? [{x: 54.5, y: 9.5, radiusPx: 2, progressPx: 0}, {x: 7.5, y: 9.5, radiusPx: 2, progressPx: 47}]
    : [{x: 7.5, y: 9.5, radiusPx: 2, progressPx: 0}, {x: 54.5, y: 9.5, radiusPx: 2, progressPx: 47}];
  return {width, height, before, background, after: before.slice(),
    paths: [{side: 0, points, lengthPx: 47}, {side: 1, points: points.map(point => ({...point, y: 30})), lengthPx: 47}],
    protection: {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [{x0: 0, y0: 0, x1: 5, y1: 4}, {x0: 24, y0: 0, x1: 28, y1: 3}],
      editableRects: [{x0: 5, y0: 6, x1: 58, y1: 14}]}, noseRoi: {x0: 29, y0: 0, x1: 32, y1: 4}};
}
function hideBand(input: ContinuityInput, x0 = 28, x1 = 34, baselineToo = false): void {
  for (let y = 8; y <= 10; y++) for (let x = x0; x <= x1; x++) {
    const index = (y * input.width + x) * 4;
    input.after.set(input.background.subarray(index, index + 4), index);
    if (baselineToo) input.before.set(input.background.subarray(index, index + 4), index);
  }
}
test('hair-created middle gap removes only the downstream tip on an originally connected arm', () => {
  const input = fixture(); hideBand(input); const before = input.before.slice(), after = input.after.slice();
  const result = findDetachedTemplePixels(input);
  assert.equal(result.diagnostics.unavailableReason, null); assert.equal(result.indices.length, 16 * 3);
  assert.ok(result.indices.every(index => index % input.width >= 35 && index % input.width <= 50));
  assert.equal(result.diagnostics.detachedComponents, 1); assert.deepEqual(input.before, before); assert.deepEqual(input.after, after);
});
test('opposite yaw ordering removes the leftward distal piece instead of assuming right means terminal', () => {
  const input = fixture(true); hideBand(input); const result = findDetachedTemplePixels(input);
  assert.equal(result.indices.length, 20 * 3); assert.ok(result.indices.every(index => index % input.width <= 27));
});
test('existing accepted fade/depth disconnection does not become a new continuity cut', () => {
  const input = fixture(); hideBand(input, 28, 34, true);
  const result = findDetachedTemplePixels(input); assert.equal(result.indices.length, 0); assert.equal(result.diagnostics.originalComponents, 2);
});
test('connected partial-alpha hair and unchanged/blank hair controls never terminate an arm', () => {
  const input = fixture(); assert.equal(findDetachedTemplePixels(input).indices.length, 0);
  for (let y = 8; y <= 10; y++) for (let x = 28; x <= 34; x++) for (let c = 0; c < 3; c++) input.after[(y * input.width + x) * 4 + c] = 80;
  assert.equal(findDetachedTemplePixels(input).indices.length, 0);
});
test('near/far overlap is rejected and sparse callers cannot include nose, optics, outside corridor or clean camera pixels', () => {
  const input = fixture(); hideBand(input);
  input.paths = input.paths!.map(path => ({...path, points: input.paths![0]!.points}));
  assert.equal(findDetachedTemplePixels(input).indices.length, 0);
  const protectedInput = fixture(); hideBand(protectedInput);
  protectedInput.noseRoi = {x0: 40, y0: 8, x1: 46, y1: 11};
  protectedInput.eligibleIndices = new Uint32Array(Array.from({length: protectedInput.width * protectedInput.height}, (_, index) => index));
  const result = findDetachedTemplePixels(protectedInput);
  assert.ok(result.indices.every(index => {const x = index % protectedInput.width, y = Math.floor(index / protectedInput.width);
    return x >= 5 && x < 58 && y >= 6 && y < 14 && !(x >= 40 && x < 46 && y >= 8 && y < 11)
      && protectedInput.before[index * 4] !== protectedInput.background[index * 4];}));
});
test('missing/invalid ordering and invalid dimensions fail closed without mutating the hair output', () => {
  const input = fixture(); hideBand(input); const after = input.after.slice();
  input.paths = null; assert.equal(findDetachedTemplePixels(input).indices.length, 0); assert.deepEqual(input.after, after);
  input.width++; assert.ok(findDetachedTemplePixels(input).diagnostics.unavailableReason);
});
test('real triangle cross-sections preserve buffers and project the accepted downward Y curve with both yaw signs', () => {
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
  }
  for (const resource of [left, right, lens, frame, lensMaterial]) resource.dispose();
});

for (const [id, cap] of [['tom-ford-clear', -.11], ['amber-horizon', -.105]] as const) test(`${id}: both original GLB shafts have complete ordered cross-sections`, async () => {
  const bytes = await readFile(new URL(`../../public/models/${id}.glb`, import.meta.url));
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
