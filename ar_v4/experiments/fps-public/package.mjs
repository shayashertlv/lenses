/** Add only the reviewed FPS build to the existing public allowlist. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {lstat, mkdir, readFile, readdir, rename, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const directory = fileURLToPath(new URL('./', import.meta.url));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const entry = 'experiments/fps-candidate/live.html';
const forbiddenParts = new Set(['src', 'source', 'private', 'recording', 'recordings', 'recovery',
  'archive', 'archives', 'qa', 'logs', 'test-results', 'tests', 'node_modules']);
export function canonicalPublicPath(name) {
  return typeof name === 'string' && /^[A-Za-z0-9_@./-]+$/.test(name)
    && name.split('/').every(part => part && !part.startsWith('.') && !forbiddenParts.has(part.toLowerCase()));
}
export function allowedFpsFile(name) {
  return name === entry || name === 'fps-build.json' || /^assets\/[\w.-]+\.(?:js|css)$/.test(name);
}
export function validateManifest(manifest) {
  assert.equal(manifest.schemaVersion, 1);
  assert.ok(Array.isArray(manifest.files) && manifest.files.length > 0, 'A public manifest is required.');
  const seen = new Set();
  for (const item of manifest.files) {
    assert.ok(canonicalPublicPath(item.path) && item.path !== 'public-manifest.json', 'Invalid public path.');
    assert.ok(!seen.has(item.path), 'Duplicate public entry.'); seen.add(item.path);
    assert.ok(Number.isSafeInteger(item.size) && item.size >= 0, 'Invalid public size.');
    assert.match(item.sha256, /^[a-f0-9]{64}$/);
  }
  assert.ok(seen.has('experiments/efficiency-lab/live.html'), 'The existing default entry must remain.');
  return manifest.files;
}
async function regularBytes(root, relative) {
  assert.ok(canonicalPublicPath(relative), 'Noncanonical file path.');
  let current = root;
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    const state = await lstat(current);
    assert.ok(!state.isSymbolicLink(), 'Public files must not traverse links.');
  }
  assert.ok((await lstat(current)).isFile(), 'Public entry must be a regular file.');
  return readFile(current);
}
async function files(root, prefix = '') {
  const result = [];
  for (const item of await readdir(root, {withFileTypes: true})) {
    const relative = prefix + item.name;
    if (item.isDirectory()) result.push(...await files(path.join(root, item.name), relative + '/'));
    else {
      assert.ok(item.isFile(), 'Build files must not contain links.');
      result.push(relative);
    }
  }
  return result.sort();
}
async function verifyEntries(root, entries) {
  for (const item of entries) {
    const bytes = await regularBytes(root, item.path);
    assert.equal(bytes.length, item.size, `Public size changed: ${item.path}`);
    assert.equal(sha256(bytes), item.sha256, `Public bytes changed: ${item.path}`);
  }
}

export async function packageFps({dist, site, merge = false}) {
  const manifestPath = path.join(site, 'public-manifest.json');
  const originalManifestBytes = await readFile(manifestPath);
  const manifest = JSON.parse(originalManifestBytes.toString('utf8'));
  const originals = validateManifest(manifest);
  const preserved = originals.filter(item => !item.path.startsWith('fps/'));
  await verifyEntries(site, originals);
  const generated = [];
  for (const name of await files(dist)) {
    assert.ok(allowedFpsFile(name), `Unexpected FPS public file: ${name}`);
    const bytes = await regularBytes(dist, name);
    if (name.endsWith('.js') || name.endsWith('.html')) {
      assert.ok(!/(['"`])\/(?:models|mediapipe|hair-preview-models)\//.test(bytes.toString('utf8')),
        `Unscoped asset address: ${name}`);
      assert.ok(!/sourceMappingURL=/.test(bytes.toString('utf8')), 'Source maps are excluded.');
    }
    generated.push({path: 'fps/' + name, size: bytes.length, sha256: sha256(bytes), bytes});
  }
  assert.ok(generated.some(item => item.path === 'fps/' + entry), 'FPS entry is missing.');
  const releaseBytes = generated.find(item => item.path === 'fps/fps-build.json')?.bytes;
  assert.ok(releaseBytes, 'FPS build identity is missing.');
  const release = JSON.parse(releaseBytes.toString('utf8'));
  assert.equal(release.schema, 'fps-public-build-v1');
  assert.match(release.fingerprint, /^[a-f0-9]{64}$/);
  const old = new Map(originals.map(item => [item.path, item]));
  // The explicit FPS rebuild may update only its own subtree. Every original
  // route and shared asset stays byte-exact; older hashed FPS assets are kept.
  for (const item of generated) {
    const prior = old.get(item.path);
    if (prior && !merge) assert.deepEqual({path: item.path, size: item.size, sha256: item.sha256}, prior,
      `FPS public file differs from the intended build: ${item.path}`);
    else if (!merge) assert.fail(`FPS entry is not published in the local package: ${item.path}`);
    const destination = path.join(site, item.path);
    try {
      const existing = await regularBytes(site, item.path);
      if (!merge || !prior) assert.equal(sha256(existing), item.sha256, `Conflicting destination: ${destination}`);
    } catch (error) {if (error.code !== 'ENOENT') throw error;}
  }
  if (merge) {
    // Check the manifest again before writing. Existing assets were verified
    // before staging and will be checked again before the allowlist is replaced.
    assert.deepEqual(await readFile(manifestPath), originalManifestBytes, 'Manifest changed during packaging.');
    for (const item of generated) {
      if (old.get(item.path)?.sha256 === item.sha256) continue;
      const destination = path.join(site, item.path);
      await mkdir(path.dirname(destination), {recursive: true});
      await writeFile(destination, item.bytes);
    }
    await verifyEntries(site, preserved);
    const added = generated.filter(item => !old.has(item.path)).map(({path: name, size, sha256: digest}) => ({path: name, size, sha256: digest}));
    const replacements = new Map(generated.map(({path: name, size, sha256: digest}) => [name, {path: name, size, sha256: digest}]));
    const next = {...manifest, files: [...originals.map(item => replacements.get(item.path) ?? item), ...added]};
    const stagedManifest = manifestPath + '.fps-stage';
    await writeFile(stagedManifest, JSON.stringify(next, null, 2) + '\n');
    await rename(stagedManifest, manifestPath);
  }
  const final = JSON.parse(await readFile(manifestPath, 'utf8'));
  await verifyEntries(site, validateManifest(final));
  for (const original of preserved)
    assert.deepEqual(final.files.find(item => item.path === original.path), original, 'Existing allowlist entry changed.');
  return {schema: 'fps-public-package-verification-v1', checkedAt: new Date().toISOString(),
    fingerprint: release.fingerprint, originalFilesPreserved: preserved.length,
    fpsFiles: generated.length, allPublicFiles: final.files.length,
    originalManifestSHA256: sha256(originalManifestBytes),
    publicManifestSHA256: sha256(await readFile(manifestPath)),
    originals: preserved, fps: generated.map(({path: name, size, sha256: digest}) => ({path: name, size, sha256: digest}))};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  assert.ok(args.length === 1 && ['--merge', '--verify'].includes(args[0]), 'Use --merge or --verify.');
  const result = await packageFps({dist: path.join(directory, 'dist'),
    site: path.resolve(directory, '../../mobile-site'), merge: args[0] === '--merge'});
  await mkdir(path.join(directory, 'qa/output'), {recursive: true});
  await writeFile(path.join(directory, 'qa/output', args[0] === '--merge' ? 'package-merge.json' : 'package-verify.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify({fingerprint: result.fingerprint, originalFilesPreserved: result.originalFilesPreserved,
    fpsFiles: result.fpsFiles, allPublicFiles: result.allPublicFiles}));
}
