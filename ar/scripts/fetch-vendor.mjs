#!/usr/bin/env node
/**
 * Fetch the vendored browser libraries, and refuse anything that is not the
 * exact bytes this tree was built against.
 *
 * ## Why these are not in git any more
 *
 * They were, and they were 105,315 of the 172,910 lines in the branch that
 * introduced this tree — 61% of a pull request, none of it written here, none
 * of it reviewable. `three.module.js` and `vision_bundle.mjs` alone are most of
 * that: two minified-ish bundles that no reviewer reads and every `git blame`
 * has to step over.
 *
 * ## Why they are still VENDORED rather than an npm dependency
 *
 * The reason has not changed since `ATTRIBUTION.md` first stated it: the app is
 * a static page served straight off disk, with no build step and no bundler
 * between the source and the browser. `three.module.js` is loaded by an import
 * map, and the MediaPipe wasm is fetched by path at runtime. Adding a bundler
 * to remove five files from a listing is a worse trade than one setup command.
 *
 * So: fetched, pinned, hash-verified, and ignored by git.
 *
 * ## The hashes are the contract
 *
 * A version number pins what a CDN *says* it is serving. The SHA-256 pins what
 * it actually served. If a hash fails, this script writes nothing and exits 1 —
 * a half-written vendor tree is worse than an absent one, because the absent
 * one is obvious and the half-written one produces a runtime error three files
 * away from its cause.
 *
 *   node scripts/fetch-vendor.mjs           fetch anything missing or wrong
 *   node scripts/fetch-vendor.mjs --check   verify only, write nothing
 *   node scripts/fetch-vendor.mjs --force   re-fetch even if the hash matches
 */

import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const VENDOR = resolve(HERE, '..', 'vendor');

/** three.js r185.1 (MIT) and @mediapipe/tasks-vision 1.0.1 (Apache 2.0).
 *  Versions and licences are documented in ../ATTRIBUTION.md. */
const THREE = 'https://cdn.jsdelivr.net/npm/three@0.185.1';
const MP = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1';

const FILES = [
  ['three/LICENSE', `${THREE}/LICENSE`,
    '8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc'],
  ['three/three.module.js', `${THREE}/build/three.module.js`,
    'bbf5ed13fe4373f5bd38b14ea8e62e9f157327da5638edc6d3863e08b167c9c7'],
  ['three/three.core.js', `${THREE}/build/three.core.js`,
    '3718df126d69c125362a03340913204470d8c50238605150e57f808840fb7759'],
  ['three/addons/loaders/GLTFLoader.js', `${THREE}/examples/jsm/loaders/GLTFLoader.js`,
    '97642d720f16cc9a0c9844934198e4d0c023bea8e89576d0f7545d03b2d103d2'],
  ['three/addons/loaders/OBJLoader.js', `${THREE}/examples/jsm/loaders/OBJLoader.js`,
    '86c8384e269b75a21d502d438e3f0dc2e09dc61b6e6da6b81947f5b119112bf8'],
  ['three/addons/environments/RoomEnvironment.js',
    `${THREE}/examples/jsm/environments/RoomEnvironment.js`,
    '55f466192cc84298755a424c5e040345006b2ee1455589b3b54126c2ea4123f4'],
  ['three/addons/utils/BufferGeometryUtils.js',
    `${THREE}/examples/jsm/utils/BufferGeometryUtils.js`,
    '5c552223a9309883743b80538d6e9cdb45e3227f30d3ec56fb2c39b46e78d595'],
  ['three/addons/utils/SkeletonUtils.js', `${THREE}/examples/jsm/utils/SkeletonUtils.js`,
    'b1632a703206c3d830de9fcbe515696770d04b71a15ee6b50afa6d2c3298c86f'],
  ['mediapipe/vision_bundle.mjs', `${MP}/vision_bundle.mjs`,
    'd885630c297c0b20b1fe86096cb06291c4c8080876f27852e724f24ac603713f'],
  ['mediapipe/wasm/vision_wasm_internal.js', `${MP}/wasm/vision_wasm_internal.js`,
    'e170ee67dd4e16c1a6fcd8840a206687e5a59b22c20e4a902bc445b095454d73'],
  ['mediapipe/wasm/vision_wasm_internal.wasm', `${MP}/wasm/vision_wasm_internal.wasm`,
    '8da277a733926eacd0474b8704b36742d6ec3231c57a860c5b889dff8f1df886'],
  ['mediapipe/wasm/vision_wasm_module_internal.js',
    `${MP}/wasm/vision_wasm_module_internal.js`,
    'da8934057f147b622e82cfb4c0dbd85461c598e268588b5a8ba9ca963a8ff82d'],
  ['mediapipe/wasm/vision_wasm_module_internal.wasm',
    `${MP}/wasm/vision_wasm_module_internal.wasm`,
    '2dabd8e23c60984628beb7bb338764c81a08e6837145273f59578684b5d53c1b'],
];

const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

const checkOnly = process.argv.includes('--check');
const force = process.argv.includes('--force');

/** The hash of what is on disk, or null if it is not there. */
async function onDisk(rel) {
  try {
    return sha256(await readFile(join(VENDOR, rel)));
  } catch {
    return null;
  }
}

let missing = 0;
let wrong = 0;
let fetched = 0;
const staged = [];

for (const [rel, url, want] of FILES) {
  const have = await onDisk(rel);
  if (have === want && !force) continue;

  if (have === null) {
    missing++;
    if (checkOnly) { console.error(`missing   ${rel}`); continue; }
  } else if (have !== want) {
    wrong++;
    if (checkOnly) { console.error(`MODIFIED  ${rel}\n          have ${have}\n          want ${want}`); continue; }
  }

  process.stdout.write(`fetching  ${rel} ... `);
  const res = await fetch(url);
  if (!res.ok) {
    console.error(`\n${url}\n  HTTP ${res.status}. Nothing written.`);
    process.exit(1);
  }
  const buf = Buffer.from(await res.arrayBuffer());
  const got = sha256(buf);
  if (got !== want) {
    // Deliberately fatal, and deliberately before anything is written: a CDN
    // serving different bytes under the same version is the case this whole
    // file exists to catch.
    console.error(
      `\nHASH MISMATCH for ${rel}\n  url  ${url}\n  want ${want}\n  got  ${got}\n`
      + '  Nothing written. Either the upstream artefact changed under its own\n'
      + '  version, or the download was tampered with. Do not "fix" this by\n'
      + '  pasting the new hash in without finding out which.',
    );
    process.exit(1);
  }
  staged.push([rel, buf]);
  fetched++;
  console.log('ok');
}

if (checkOnly) {
  if (missing || wrong) {
    console.error(`\n${missing} missing, ${wrong} modified. Run: node scripts/fetch-vendor.mjs`);
    process.exit(1);
  }
  console.log(`vendor tree intact: ${FILES.length} files match their pinned hashes.`);
  process.exit(0);
}

// Every hash verified before the first byte is written.
for (const [rel, buf] of staged) {
  const dest = join(VENDOR, rel);
  await mkdir(dirname(dest), { recursive: true });
  await writeFile(dest, buf);
}

console.log(
  fetched
    ? `vendor ready: ${fetched} file(s) fetched and verified, ${FILES.length - fetched} already correct.`
    : `vendor ready: all ${FILES.length} files already match their pinned hashes.`,
);
