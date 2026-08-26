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
import { VENDOR_FILES } from './vendor-manifest.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const VENDOR = resolve(HERE, '..', 'vendor');

// The manifest lives in its own module so `check-selfcontained.mjs` can
// require exactly what this fetches. See `vendor-manifest.mjs`.
const FILES = VENDOR_FILES;



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
