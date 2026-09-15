import test from 'node:test';
import assert from 'node:assert/strict';
import {Box3, Matrix4, Vector3} from 'three';
import {createProtection, nasalRoi, projectBounds, protectionProjection, validateProtection} from '../src/render/protection.ts';
import type {Detection} from '../src/face/protocol.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';

test('the nasal ROI matches the known coordinates, margins, clamp and no-face clearing', () => {
  const detection: Detection = {matrix: Array(16).fill(0), inferenceMs: 0, landmarks: Array.from({length: 478}, () => ({x: .5, y: .4, z: 0}))};
  detection.landmarks[98] = {x: .4, y: .3, z: 0}; detection.landmarks[327] = {x: .6, y: .5, z: 0};
  assert.deepEqual(nasalRoi(detection, 1000, 800), {x0: 394, y0: 234, x1: 606, y1: 406});
  assert.equal(nasalRoi({...detection, matrix: null}, 1000, 800), null);
  detection.landmarks[1] = {x: NaN, y: .4, z: 0}; assert.equal(nasalRoi(detection, 1000, 800), null);
});

test('the protection projects the optical box and the dropped arm corridors, and the nasal rectangle follows the landmarks', () => {
  const landmarks = Array.from({length: 478}, () => ({x: .5, y: .45, z: 0}));
  landmarks[33] = {x: .42, y: .43, z: 0}; landmarks[263] = {x: .58, y: .43, z: 0}; landmarks[2] = {x: .5, y: .52, z: 0};
  const pose = new Matrix4().makeRotationY(20 * Math.PI / 180).setPosition(0, 0, -40).toArray();
  const optical = new Box3(new Vector3(-.07, -.02, -.01), new Vector3(.07, .02, .01));
  const arms = [new Box3(new Vector3(-.075, -.004, -.12), new Vector3(-.065, .004, -.01)), new Box3(new Vector3(.065, -.004, -.12), new Vector3(.075, .004, -.01))];
  const dropped = arms.map(box => box.clone().translate(new Vector3(0, -.02, 0)));
  const protection = createProtection({optical, originalArms: arms, candidateArms: dropped}, pose, GLASSES_OFFSET_CM, landmarks, 1280, 720)!;
  assert.ok(protection);
  validateProtection(protection, 1280, 720);
  assert.equal(protection.protectedRects.length, 2); assert.equal(protection.editableRects.length, 2);
  const projection = protectionProjection(pose, GLASSES_OFFSET_CM, 1280 / 720);
  assert.deepEqual(protection.protectedRects[0], projectBounds(optical, projection, 1280, 720, 4));
  assert.deepEqual(protection.editableRects, arms.map((box, index) => projectBounds(box.clone().union(dropped[index]!), projection, 1280, 720, 4)));
  const nasal = protection.protectedRects[1]!;
  assert.ok(nasal.x0 <= .42 * 1280 && nasal.x1 >= .58 * 1280 && nasal.y1 >= .52 * 720 && nasal.y0 <= .43 * 720);
  // The dropped corridor is taller than the undropped arm box alone.
  const undropped = projectBounds(arms[0]!, projection, 1280, 720, 4)!;
  assert.ok(protection.editableRects[0]!.y1 > undropped.y1);
  // A pose behind the near plane, too few landmarks or an empty box fall back to null rather than a guess.
  assert.equal(createProtection({optical, originalArms: arms, candidateArms: dropped}, new Matrix4().setPosition(0, 0, 5).toArray(), GLASSES_OFFSET_CM, landmarks, 1280, 720), null);
  assert.equal(createProtection({optical, originalArms: arms, candidateArms: dropped}, pose, GLASSES_OFFSET_CM, landmarks.slice(0, 10), 1280, 720), null);
  assert.equal(createProtection({optical: new Box3(), originalArms: arms, candidateArms: dropped}, pose, GLASSES_OFFSET_CM, landmarks, 1280, 720), null);
});
