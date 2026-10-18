/** Reproduce a live-site asset update in a disposable workspace without touching ar/. */
import assert from 'node:assert/strict';
import {copyFile, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {MEDIAPIPE_RUNTIME, PINNED_ASSETS, prepareAssets, verifyPublicAssets} from '../scripts/prepare-assets.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
test('viewer bootstrap and verification ignore changed live assets and keep catalog fixtures out of public', async t => {
  const parent = path.resolve(root, '../data/arview');
  await mkdir(parent, {recursive: true});
  const work = await mkdtemp(path.join(parent, 'asset-isolation-'));
  t.after(async () => {assert.equal(path.dirname(work), parent); await rm(work, {recursive: true, force: true});});
  const isolated = path.join(work, 'glasses3d/arview');
  const wasm = path.join(isolated, 'node_modules/@mediapipe/tasks-vision/wasm');
  await mkdir(wasm, {recursive: true});
  for (const name of MEDIAPIPE_RUNTIME) await copyFile(path.join(root, 'node_modules/@mediapipe/tasks-vision/wasm', name), path.join(wasm, name));
  // Old arview would read these files and fail its pins; the new cache never uses the mutable checkout.
  const live = path.join(work, 'ar/public/models');
  await mkdir(live, {recursive: true});
  await writeFile(path.join(live, 'canonical-face.json'), 'routine live asset update');
  assert.equal((await prepareAssets({arviewRoot: isolated})).ok, true);
  assert.equal((await verifyPublicAssets(isolated)).ok, true);
  assert.ok(Object.keys(PINNED_ASSETS).every(name => !name.startsWith('public/') || !name.endsWith('.glb')));
  assert.equal(await readFile(path.join(live, 'canonical-face.json'), 'utf8'), 'routine live asset update');
  await writeFile(path.join(isolated, 'public/models/canonical-face.json'), 'corrupt cache');
  const bad = await verifyPublicAssets(isolated);
  assert.equal(bad.ok, false);
  assert.match(bad.problems.join('\n'), /canonical-face.json has sha256/);
  await assert.rejects(prepareAssets({arviewRoot: isolated}), /differs from its pin/);
});
