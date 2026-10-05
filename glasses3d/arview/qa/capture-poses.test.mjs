/** arview capture: pose sets, file names and backgrounds (qa/capture/poses.ts). */
import assert from 'node:assert/strict';
import test from 'node:test';
import {buildCapturePlan, cropFile, paintBackground, poseId, renderFile, SAFE_FILE, VARIANT_OVERRIDE} from './capture/poses.ts';

test('default: front, yaw ±30, yaw ±45, pitch ±20 on skin, beauty + part-id, no crops (14 images)', () => {
  const plan = buildCapturePlan('default', {depthCm: 23});
  assert.deepEqual(plan.poses.map(entry => entry.pose.id), ['front', 'yaw_p30', 'yaw_m30', 'yaw_p45', 'yaw_m45', 'pitch_p20', 'pitch_m20']);
  assert.deepEqual(plan.backgrounds, ['skin']);
  assert.equal(plan.renderCount, 14); assert.equal(plan.plannedCropCount, 0);
  assert.deepEqual(plan.poses[1].renders.map(render => render.file), ['yaw_p30__part-id.png', 'yaw_p30__skin__beauty.png']);
  assert.ok(plan.poses.every(entry => entry.pose.order === 'YXZ' && entry.pose.roll === 0 && entry.pose.depthCm === 23 && !entry.pose.junction));
  assert.deepEqual(plan.poses.map(entry => [entry.pose.yaw, entry.pose.pitch]), [[0, 0], [30, 0], [-30, 0], [45, 0], [-45, 0], [0, 20], [0, -20]]);
});

test('junction: yaw ±30 x pitch ±20 on skin and dark, beauty + part-id, two side crops of every render (36 images)', () => {
  const plan = buildCapturePlan('junction', {depthCm: 23});
  assert.deepEqual(plan.poses.map(entry => entry.pose.id), ['yaw_p30_pitch_p20', 'yaw_p30_pitch_m20', 'yaw_m30_pitch_p20', 'yaw_m30_pitch_m20']);
  assert.deepEqual(plan.backgrounds, ['skin', 'dark']);
  assert.equal(plan.renderCount, 12); assert.equal(plan.plannedCropCount, 24);
  assert.ok(plan.poses.every(entry => entry.pose.junction && entry.renders.every(render => render.crops)));
  assert.deepEqual(plan.poses[0].renders.map(render => [render.variant, render.background]), [['part-id', null], ['beauty', 'skin'], ['beauty', 'dark']]);
});

test('all: both pose lists, skin/dark/checker, diagnostics, junction crops only on the junction poses', () => {
  const plan = buildCapturePlan('all', {depthCm: 30});
  assert.equal(plan.poses.length, 11);
  assert.deepEqual(plan.backgrounds, ['skin', 'dark', 'checker']);
  const variants = plan.poses[0].renders.map(render => `${render.variant}/${render.background}`);
  assert.deepEqual(variants, ['part-id/null', 'normals/null', 'beauty/skin', 'beauty/dark', 'beauty/checker', 'lens-hidden/skin', 'lens-only/skin']);
  assert.equal(plan.renderCount, 77); assert.equal(plan.plannedCropCount, 4 * 7 * 2);
  assert.ok(plan.poses.slice(0, 7).every(entry => entry.renders.every(render => !render.crops)));
  assert.ok(plan.poses.slice(7).every(entry => entry.renders.every(render => render.crops)));
  const files = plan.poses.flatMap(entry => entry.renders.map(render => render.file));
  assert.equal(new Set(files).size, files.length, 'every file name is unique');
});

test('--backgrounds replaces the set\'s list; flat variants stay once per pose', () => {
  const plan = buildCapturePlan('junction', {depthCm: 23, backgrounds: ['checker']});
  assert.deepEqual(plan.backgrounds, ['checker']);
  assert.equal(plan.renderCount, 8);
  assert.throws(() => buildCapturePlan('everything', {depthCm: 23}), /Unknown pose set/);
});

test('file names are safe and deterministic', () => {
  assert.equal(poseId(0, 0), 'front'); assert.equal(poseId(-45, 0), 'yaw_m45'); assert.equal(poseId(30, -20), 'yaw_p30_pitch_m20');
  assert.equal(renderFile('front', 'part-id', null), 'front__part-id.png');
  assert.equal(renderFile('front', 'beauty', 'dark'), 'front__dark__beauty.png');
  assert.equal(cropFile('front__dark__beauty.png', 'left'), 'front__dark__beauty__crop_left.png');
  for (const plan of ['default', 'junction', 'all'].map(set => buildCapturePlan(set, {depthCm: 23}))) {
    for (const entry of plan.poses) for (const render of entry.renders) {
      assert.match(render.file, SAFE_FILE);
      if (render.crops) for (const side of ['left', 'right']) assert.match(cropFile(render.file, side), SAFE_FILE);
    }
  }
  for (const bad of ['../x.png', 'a/b.png', 'x.jpg', '.png', 'A.png']) assert.doesNotMatch(bad, SAFE_FILE);
  assert.deepEqual(VARIANT_OVERRIDE, {beauty: null, 'part-id': 'part-id', normals: 'normals', 'lens-hidden': 'lens-hidden', 'lens-only': 'lens-only'});
});

test('backgrounds paint the prototype colours (skin #cba68d, dark #2b2522, 32 px checker)', () => {
  const calls = [];
  const context = {fillStyle: null, fillRect(x, y, w, h) {calls.push([this.fillStyle, x, y, w, h]);}};
  paintBackground(context, 64, 32, 'skin'); assert.deepEqual(calls, [['#cba68d', 0, 0, 64, 32]]);
  calls.length = 0; paintBackground(context, 64, 32, 'dark'); assert.deepEqual(calls, [['#2b2522', 0, 0, 64, 32]]);
  calls.length = 0; paintBackground(context, 64, 32, 'checker');
  assert.deepEqual(calls.slice(1), [['#ebe0d2', 0, 0, 32, 32], ['#b4a190', 32, 0, 32, 32]]);
});
