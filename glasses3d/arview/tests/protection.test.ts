import test from 'node:test';
import assert from 'node:assert/strict';
import {Box3, Group, Matrix4, PerspectiveCamera, Vector3} from 'three';
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

test('fitted protection matches a rendered asset transform without scaling the bridge translation', () => {
  const pose = new Matrix4().makeRotationY(.35).setPosition(1, -2, -42), aspect = 16 / 9;
  const camera = new PerspectiveCamera(63, aspect, 1, 10_000); camera.updateMatrixWorld();
  const attachment = new Group(); attachment.matrixAutoUpdate = false; attachment.matrix.copy(pose);
  const asset = new Group(); asset.position.set(...GLASSES_OFFSET_CM); attachment.add(asset);
  const landmarks = Array.from({length: 478}, () => ({x: .5, y: .45, z: 0}));
  const optical = new Box3(new Vector3(-.07, -.02, -.01), new Vector3(.07, .02, .01));
  const bounds = {optical, originalArms: [], candidateArms: []};
  let bridge: Vector3 | null = null;
  for (const fitScale of [.8, 1, 1.25]) {
    asset.scale.setScalar(100 * fitScale); attachment.updateMatrixWorld(true);
    const projection = protectionProjection(pose.toArray(), GLASSES_OFFSET_CM, aspect, fitScale);
    const origin = new Vector3().applyMatrix4(projection);
    bridge ??= origin.clone(); assert.ok(origin.distanceTo(bridge) < 1e-12, 'fitting must keep the same bridge origin');
    const protection = createProtection(bounds, pose.toArray(), GLASSES_OFFSET_CM, landmarks, 1280, 720, aspect, fitScale);
    assert.ok(protection);
    const rect = protection.protectedRects[0]!;
    for (const x of [optical.min.x, optical.max.x]) for (const y of [optical.min.y, optical.max.y]) for (const z of [optical.min.z, optical.max.z]) {
      const drawn = new Vector3(x, y, z).applyMatrix4(asset.matrixWorld).project(camera);
      const guarded = new Vector3(x, y, z).applyMatrix4(projection);
      assert.ok(drawn.distanceTo(guarded) < 1e-12, 'the guard uses the actual scaled scene transform');
      const px = (drawn.x + 1) * 640, py = (1 - drawn.y) * 360;
      assert.ok(px >= rect.x0 + 3 && px <= rect.x1 - 3 && py >= rect.y0 + 3 && py <= rect.y1 - 3,
        'every fitted optical corner remains inside the protected margin');
    }
  }
  for (const fitScale of [0, -1, NaN, Infinity]) {
    assert.throws(() => protectionProjection(pose.toArray(), GLASSES_OFFSET_CM, aspect, fitScale), /fit scale/);
    assert.equal(createProtection(bounds, pose.toArray(), GLASSES_OFFSET_CM, landmarks, 1280, 720, aspect, fitScale), null);
  }
});
