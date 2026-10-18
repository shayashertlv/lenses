/** Prepare/verify arview's independent ignored asset cache, never the live ar/ checkout.
 * Missing files come from immutable Git objects (offline); --download permits the same immutable snapshot from
 * GitHub for a shallow clone. Existing mismatches fail rather than replacing evidence silently. */
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {ASSET_PINS, ASSET_REVISION} from '../src/asset-pins.ts';
import {FIXTURE_PINS} from '../tests/fixture-pins.ts';

const ARVIEW_ROOT = fileURLToPath(new URL('../', import.meta.url));
export const PINNED_ASSETS = Object.freeze({...ASSET_PINS, ...FIXTURE_PINS});
export const MEDIAPIPE_RUNTIME = Object.freeze(['vision_wasm_module_internal.js', 'vision_wasm_module_internal.wasm']);
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const sourcePath = name => name.startsWith('qa/fixtures/models/') ? `ar/public/models/${path.basename(name)}` : `ar/${name}`;

/** Read-only verification; never provisions or reaches the network. */
export async function verifyPublicAssets(arviewRoot = ARVIEW_ROOT) {
  const problems = [], files = {};
  for (const [name, expected] of Object.entries(PINNED_ASSETS)) {
    let bytes;
    try {bytes = await readFile(path.join(arviewRoot, name));}
    catch (error) {problems.push(`${name} cannot be read (${error.code ?? error.message}); run npm run assets`); continue;}
    files[name] = sha256(bytes);
    if (files[name] !== expected) problems.push(`${name} has sha256 ${files[name]}, not the pinned ${expected}`);
  }
  return {ok: problems.length === 0, problems, files};
}

export async function prepareAssets({arviewRoot = ARVIEW_ROOT, download = false} = {}) {
  for (const [name, expected] of Object.entries(PINNED_ASSETS)) {
    const target = path.join(arviewRoot, name);
    try {
      const existing = await readFile(target);
      if (sha256(existing) !== expected) throw new Error(`${name} differs from its pin; preserve/investigate it before re-provisioning`);
      continue;
    } catch (error) {if (error.code !== 'ENOENT') throw error;}
    let bytes;
    if (name.startsWith('public/mediapipe/')) {
      bytes = await readFile(path.join(arviewRoot, 'node_modules/@mediapipe/tasks-vision/wasm', path.basename(name)));
    } else {
      const source = sourcePath(name);
      const result = spawnSync('git', ['show', `${ASSET_REVISION}:${source}`], {cwd: arviewRoot, windowsHide: true, maxBuffer: 64 * 1024 * 1024});
      if (result.status === 0) bytes = result.stdout;
      else if (download) {
        const response = await fetch(`https://raw.githubusercontent.com/shayashertlv/lenses/${ASSET_REVISION}/${source}`);
        if (!response.ok) throw new Error(`${name}: immutable download returned ${response.status}`);
        bytes = Buffer.from(await response.arrayBuffer());
      } else throw new Error(`${name}: snapshot ${ASSET_REVISION} unavailable; use npm run assets -- --download for a shallow clone`);
    }
    if (sha256(bytes) !== expected) throw new Error(`${name}: source bytes fail the pinned SHA-256; nothing written`);
    await mkdir(path.dirname(target), {recursive: true});
    await writeFile(target, bytes, {flag: 'wx'}).catch(async error => {
      if (error.code !== 'EEXIST' || sha256(await readFile(target)) !== expected) throw error;
    });
  }
  return verifyPublicAssets(arviewRoot);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.slice(2).some(arg => arg !== '--download')) throw new Error('usage: node scripts/prepare-assets.mjs [--download]');
  const result = await prepareAssets({download: process.argv.includes('--download')});
  if (!result.ok) {console.error(result.problems.join('\n')); process.exit(1);}
  console.log(`Verified ${Object.keys(result.files).length} viewer-owned pinned assets (live ar/ was not read or written).`);
}
