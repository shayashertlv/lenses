import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {allowedFpsFile, canonicalPublicPath, packageFps} from './package.mjs';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
async function fixture(run) {
  const root = await mkdtemp(path.join(tmpdir(), 'fps-public-package-'));
  const site = path.join(root, 'site'), dist = path.join(root, 'dist');
  async function put(base, name, content) {const target = path.join(base, name); await mkdir(path.dirname(target), {recursive: true}); await writeFile(target, content);}
  const oldName = 'experiments/efficiency-lab/live.html', oldBytes = Buffer.from('old approved page');
  await put(site, oldName, oldBytes);
  await put(site, 'public-manifest.json', JSON.stringify({schemaVersion: 1,
    files: [{path: oldName, size: oldBytes.length, sha256: digest(oldBytes)}]}));
  await put(dist, 'experiments/fps-candidate/live.html', '<script type="module" src="/ar_testing/fps/assets/entry-abcdefgh.js"></script>');
  await put(dist, 'assets/entry-abcdefgh.js', 'export const url="/ar_testing/models/amber-horizon.glb";');
  await put(dist, 'fps-build.json', JSON.stringify({schema: 'fps-public-build-v1', fingerprint: 'a'.repeat(64)}));
  try {await run({root, site, dist, put, oldName, oldBytes});}
  finally {
    const resolved = path.resolve(root);
    assert.equal(path.dirname(resolved), path.resolve(tmpdir()));
    assert.ok(path.basename(resolved).startsWith('fps-public-package-'));
    await rm(resolved, {recursive: true, force: true});
  }
}

test('only FPS executable assets, entry and build identity are eligible', () => {
  for (const name of ['assets/main-abcdefgh.js', 'assets/main-abcdefgh.css', 'fps-build.json', 'experiments/fps-candidate/live.html'])
    assert.equal(allowedFpsFile(name), true);
  for (const name of ['assets/main.js.map', 'tests/fixture.jpg', '.recovery/clip.mp4', 'source/entry.ts', 'models/face.task', 'other.html'])
    assert.equal(allowedFpsFile(name), false);
  for (const name of ['../assets/a.js', 'assets//a.js', 'assets\\a.js', 'fps/qa/a.json', 'fps/%2e/a.json'])
    assert.equal(canonicalPublicPath(name), false);
});
test('additive merge and repeated verification retain the existing allowlist and exact files', async () => fixture(async f => {
  const original = JSON.parse(await readFile(path.join(f.site, 'public-manifest.json'), 'utf8')).files[0];
  const receipt = await packageFps({...f, merge: true});
  assert.equal(receipt.originalFilesPreserved, 1); assert.equal(receipt.fpsFiles, 3); assert.equal(receipt.allPublicFiles, 4);
  assert.deepEqual(await readFile(path.join(f.site, f.oldName)), f.oldBytes);
  const final = JSON.parse(await readFile(path.join(f.site, 'public-manifest.json'), 'utf8'));
  assert.deepEqual(final.files[0], original);
  assert.equal((await packageFps({...f})).allPublicFiles, 4);
  assert.equal((await packageFps({...f, merge: true})).allPublicFiles, 4);
}));
test('an altered existing asset blocks the merge before the manifest changes', async () => fixture(async f => {
  const manifest = await readFile(path.join(f.site, 'public-manifest.json'));
  await f.put(f.site, f.oldName, 'corrupted');
  await assert.rejects(packageFps({...f, merge: true}), /Public size changed|Public bytes changed/);
  assert.deepEqual(await readFile(path.join(f.site, 'public-manifest.json')), manifest);
}));
test('unexpected source and unscoped runtime paths cannot become public', async () => fixture(async f => {
  await f.put(f.dist, 'assets/entry-abcdefgh.js', 'export const path="/models/face.task";');
  await assert.rejects(packageFps({...f, merge: true}), /Unscoped asset address/);
  await f.put(f.dist, 'assets/entry-abcdefgh.js', 'export const safe=true;');
  await f.put(f.dist, 'source/private.json', '{}');
  await assert.rejects(packageFps({...f, merge: true}), /Unexpected FPS public file/);
}));
test('an explicit FPS rebuild changes its own entry while retaining previous assets and original routes', async () => fixture(async f => {
  await packageFps({...f, merge: true});
  const manifest = await readFile(path.join(f.site, 'public-manifest.json'));
  await f.put(f.dist, 'assets/entry-abcdefgh.js', 'export const changed=true;');
  await assert.rejects(packageFps({...f}), /FPS public file differs/);
  assert.deepEqual(await readFile(path.join(f.site, 'public-manifest.json')), manifest);
  await packageFps({...f, merge: true});
  assert.equal(await readFile(path.join(f.site, 'fps/assets/entry-abcdefgh.js'), 'utf8'), 'export const changed=true;');
  assert.deepEqual(await readFile(path.join(f.site, f.oldName)), f.oldBytes);
  assert.equal((await packageFps({...f})).allPublicFiles, 4);
}));
