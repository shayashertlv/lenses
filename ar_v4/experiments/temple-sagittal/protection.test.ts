import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Box3, Matrix4, PerspectiveCamera, Vector3} from 'three';
import {composeProtectedPixels, containsPixel, createProtection, projectBounds, protectionProjection} from './protection.ts';
import {validateProtection, validateRearDrop} from './contracts.ts';
import type {ProtectionConfiguration} from './contracts.ts';
import {VIRTUAL_CAMERA} from '../../src/render/projection.ts';

test('integer composition rejects a full-frame hostile candidate under optical/nasal guards and outside its corridor', () => {
  const width = 12, height = 10;
  const baseline = new Uint8ClampedArray(width * height * 4).map((_, index) => (index * 37) % 256);
  const candidate = new Uint8ClampedArray(baseline.length).map((_, index) => 255 - baseline[index]!);
  const policy: ProtectionConfiguration = {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
    protectedRects: [{x0: 4, y0: 0, x1: 9, y1: 5}, {x0: 5, y0: 3, x1: 8, y1: 10}],
    editableRects: [{x0: 0, y0: 2, x1: 12, y1: 8}]};
  const baselineCopy = baseline.slice(), candidateCopy = candidate.slice();
  const output = composeProtectedPixels(baseline, candidate, policy);
  let expectedChanges = 0;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const protect = policy.protectedRects.some(rect => containsPixel(rect, x, y));
    const edit = !protect && y >= 2 && y < 8;
    expectedChanges += Number(edit);
    const index = (y * width + x) * 4;
    assert.deepEqual(output.pixels.slice(index, index + 4), (edit ? candidate : baseline).slice(index, index + 4));
  }
  assert.equal(output.changedPixels, expectedChanges);
  assert.deepEqual(baseline, baselineCopy); assert.deepEqual(candidate, candidateCopy);
  assert.notEqual(output.pixels, baseline); assert.notEqual(output.pixels, candidate);
  assert.throws(() => composeProtectedPixels(baseline.subarray(4), candidate, policy), /pixel sizes differ/);
});

test('full optical box projection includes hidden near/far corners and applies millimeter-to-centimeter attachment exactly once', () => {
  const matrix = new Matrix4().makeRotationX(.4).setPosition(2, -1, -45).toArray();
  const offset = [0, 3.271027, 6.531958919387042] as const;
  const projection = protectionProjection(matrix, offset, 16 / 9);
  const camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, 16 / 9, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  const box = new Box3(new Vector3(-.075, -.04, -.04), new Vector3(.075, .025, .02));
  const rect = projectBounds(box, projection, 1280, 720, 4)!;
  assert.ok(rect);
  for (const x of [-.075, .075]) for (const y of [-.04, .025]) for (const z of [-.04, .02]) {
    const point = new Vector3(x, y, z).multiplyScalar(100).add(new Vector3(...offset))
      .applyMatrix4(new Matrix4().fromArray(matrix)).project(camera);
    const px = (point.x + 1) * 640, py = (1 - point.y) * 360;
    assert.ok(px >= rect.x0 + 3 && px <= rect.x1 - 3 && py >= rect.y0 + 3 && py <= rect.y1 - 3);
  }
  const nearCrossing = new Box3(new Vector3(-1, -1, -.5), new Vector3(1, 1, .5));
  assert.throws(() => projectBounds(nearCrossing, camera.projectionMatrix, 1280, 720, 4), /near plane/);
});

test('paired mask creation follows source-camera aspect, covers optical and nasal guards, and fails closed on invalid inputs', () => {
  const optical = new Box3(new Vector3(-.07, -.02, -.025), new Vector3(.07, .025, .01));
  const arm = new Box3(new Vector3(.06, -.01, -.11), new Vector3(.075, .01, -.025));
  const shifted = arm.clone(); shifted.min.y -= .02;
  const bounds = {optical, originalArms: [arm], candidateArms: [shifted]};
  const landmarks = Array.from({length: 468}, () => ({x: .5, y: .5, z: 0}));
  landmarks[33]!.x = .35; landmarks[263]!.x = .65; landmarks[2]!.y = .7; landmarks[168]!.y = .4;
  const pose = new Matrix4().setPosition(0, 0, -50).toArray();
  const policy = createProtection(bounds, pose, [0, 3.271027, 6.531958919387042], landmarks, 1280, 667, 1920 / 1001);
  assert.ok(policy);
  assert.equal(policy.protectedRects.length, 2); assert.equal(policy.editableRects.length, 1);
  assert.ok(containsPixel(policy.protectedRects[1]!, .5 * 1280, .7 * 667));
  assert.ok(policy.protectedRects.every(rect => rect.x0 >= 0 && rect.y0 >= 0 && rect.x1 <= 1280 && rect.y1 <= 667));
  assert.equal(createProtection(bounds, [NaN], [0, 0, 0], landmarks, 1280, 720), null);
  assert.equal(createProtection(bounds, pose, [0, 0, 0], landmarks.slice(0, 12), 1280, 720), null);
  assert.equal(createProtection({...bounds, optical: new Box3()}, pose, [0, 0, 0], landmarks, 1280, 720), null);
  const badLandmarks = structuredClone(landmarks); badLandmarks[1]!.x = NaN;
  assert.equal(createProtection(bounds, pose, [0, 0, 0], badLandmarks, 1280, 720), null);
});

test('saved masks and drop policy reject invalid dimensions, weakened margins, bounds and nonfinite geometry', () => {
  const valid: ProtectionConfiguration = {method: 'temple-optics-copy-v1', width: 20, height: 20, marginPx: 4,
    protectedRects: [{x0: 2, y0: 2, x1: 10, y1: 10}, {x0: 5, y0: 8, x1: 12, y1: 17}], editableRects: []};
  assert.doesNotThrow(() => validateProtection(valid, 20, 20));
  for (const value of [{...valid, marginPx: 2}, {...valid, width: 21}, {...valid, protectedRects: []},
    {...valid, editableRects: [{x0: -1, y0: 0, x1: 20, y1: 20}]},
    {...valid, editableRects: [{x0: 0, y0: 0, x1: 20.5, y1: 20}]}]) {
    assert.throws(() => validateProtection(value, 20, 20), /protection/);
  }
  assert.doesNotThrow(() => validateRearDrop({method: 'temple-rear-drop-v1', dropM: .02}));
  for (const dropM of [NaN, Infinity, -.001, .03001]) assert.throws(() => validateRearDrop({method: 'temple-rear-drop-v1', dropM}), /rear-drop/);
});
