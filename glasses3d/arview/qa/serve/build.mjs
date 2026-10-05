/** The frozen webcam page for `arview serve` (WP1_DESIGN.md §6): built once per source state, then only verified.
 *
 *  The build is content-addressed: its directory under glasses3d/data/arview/serve/builds/ is named by the SHA-256 of
 *  everything Vite compiles (index.html, src/, vite.config.ts, package.json, package-lock.json). A directory whose stamp
 *  (.arview-build.json: every file's sha256) matches is reused without building; one whose files no longer match its
 *  stamp is set aside (renamed *.corrupt-<time>, never served) and rebuilt. Vite builds into a fresh temporary directory
 *  that is renamed into place only when complete, so a crashed or stalled build never leaves a half build that looks
 *  whole. The public binaries are NOT copied into the build (copyPublicDir false): the server serves them from
 *  ../../ar/public (read-only), each pinned by sha256 when the server starts (publicInventory), so 38 MB are not
 *  duplicated per source state. */
import crypto from 'node:crypto';
import fsp from 'node:fs/promises';
import path from 'node:path';

export const BUILD_STAMP = '.arview-build.json';
export const BUILD_SCHEMA = 'arview.serve-build/1';
const SOURCE_FILES = ['index.html', 'vite.config.ts', 'package.json', 'package-lock.json'];
const SOURCE_DIRECTORIES = ['src'];

const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

/** Every regular file under `root`, as sorted posix paths relative to it (dot-files skipped when `skipDot`). */
async function walk(root, {skipDot = false} = {}) {
  const out = [];
  async function visit(folder) {
    for (const entry of await fsp.readdir(folder, {withFileTypes: true})) {
      if (skipDot && entry.name.startsWith('.')) continue;
      const target = path.join(folder, entry.name);
      if (entry.isDirectory()) await visit(target);
      else if (entry.isFile()) out.push(path.relative(root, target).replaceAll('\\', '/'));
    }
  }
  await visit(root);
  return out.sort();
}

/** The SHA-256 of the build's inputs (file names and contents), and each input's own hash. */
export async function sourceDigest(arviewRoot) {
  const names = [...SOURCE_FILES];
  for (const directory of SOURCE_DIRECTORIES) for (const name of await walk(path.join(arviewRoot, directory))) names.push(`${directory}/${name}`);
  names.sort();
  const files = {}, hash = crypto.createHash('sha256');
  for (const name of names) {
    files[name] = sha256(await fsp.readFile(path.join(arviewRoot, name)));
    hash.update(`${name}\0${files[name]}\n`);
  }
  return {digest: hash.digest('hex'), files};
}

/** route ('/assets/x.js') -> {path, sha256, bytes} for every file under `root` (dot-files skipped), optionally filtered. */
export async function inventory(root, keep = () => true) {
  const files = new Map();
  for (const name of await walk(root, {skipDot: true})) {
    if (!keep(name)) continue;
    const file = path.join(root, name), data = await fsp.readFile(file);
    files.set(`/${name}`, {path: file, sha256: sha256(data), bytes: data.length});
  }
  return files;
}

/** The public binaries the page reads (MediaPipe wasm, face and hair models, canonical face, licences), from
 *  ../../ar/public, read-only. The shipped catalog GLBs there are not served: the page renders only the handed-over model. */
export function publicInventory(publicDir) {
  return inventory(publicDir, name => !name.toLowerCase().endsWith('.glb'));
}

/** Rename with retries (Windows Defender denies about 1 % of renames). Resolves false when the target exists. */
async function renameWithRetry(from, to) {
  for (let attempt = 0; ; attempt++) {
    try {await fsp.rename(from, to); return true;}
    catch (error) {
      if (error.code === 'EEXIST' || error.code === 'ENOTEMPTY') return false;
      if (attempt >= 7) throw error;
      await new Promise(resolve => setTimeout(resolve, 100 * (attempt + 1)));
    }
  }
}

/** Whether `dir` holds a complete build of `digest`: its stamp names that digest and every file matches it exactly.
 *  Returns {ok, files, reason}. */
export async function verifyBuild(dir, digest) {
  let stamp;
  try {stamp = JSON.parse(await fsp.readFile(path.join(dir, BUILD_STAMP), 'utf8'));}
  catch (error) {return {ok: false, reason: error.code === 'ENOENT' ? 'no stamp' : `unreadable stamp (${error.message})`};}
  if (stamp?.schema !== BUILD_SCHEMA || stamp.source_digest !== digest || !stamp.files || typeof stamp.files !== 'object') {
    return {ok: false, reason: 'the stamp is not this source state\'s'};
  }
  const files = await inventory(dir);
  const expected = Object.entries(stamp.files);
  const problems = [];
  for (const [route, hash] of expected) {
    const file = files.get(route);
    if (!file) problems.push(`${route} missing`);
    else if (file.sha256 !== hash) problems.push(`${route} changed`);
  }
  for (const route of files.keys()) if (!(route in stamp.files)) problems.push(`${route} not in the stamp`);
  if (problems.length) return {ok: false, reason: `${problems.length} file(s) differ from the stamp: ${problems.slice(0, 5).join(', ')}`, files};
  if (!files.has('/index.html')) return {ok: false, reason: 'the build has no index.html', files};
  return {ok: true, files, stamp};
}

/** The build for the current sources: reused when verified, else built. `log(text)` reports progress (a line every 10 s
 *  while Vite runs). Resolves {dir, digest, builtNow, files, stamp, warnings}; rejects when the build fails. The caller
 *  bounds the time (SERVE_BUILD_TIMEOUT_MS). */
export async function ensureBuild({arviewRoot, buildsDir, log = () => {}}) {
  const {digest, files: sources} = await sourceDigest(arviewRoot);
  const dir = path.join(buildsDir, digest.slice(0, 16));
  const warnings = [];
  const existing = await verifyBuild(dir, digest);
  if (existing.ok) return {dir, digest, builtNow: false, files: existing.files, stamp: existing.stamp, sources, warnings};
  if (await fsp.stat(dir).then(() => true, () => false)) {
    // A directory that does not verify is never served and never deleted: it is set aside for inspection.
    const aside = `${dir}.corrupt-${new Date().toISOString().replace(/[:.]/g, '-')}`;
    if (!await renameWithRetry(dir, aside)) throw new Error(`could not set aside the unverifiable build ${dir} (${existing.reason})`);
    warnings.push(`the build ${dir} did not verify (${existing.reason}); it was set aside as ${aside} and rebuilt`);
    log(`WARNING: ${warnings.at(-1)}`);
  }
  await fsp.mkdir(buildsDir, {recursive: true});
  const temporary = `${dir}.tmp-${process.pid}-${Date.now()}`;
  const started = Date.now();
  log(`building the frozen page (sources ${digest.slice(0, 16)}) into ${dir}`);
  const ticker = setInterval(() => log(`  still building (${Math.round((Date.now() - started) / 1000)} s)`), 10_000);
  ticker.unref?.();
  try {
    const {build} = await import('vite');
    await build({root: arviewRoot, configFile: path.join(arviewRoot, 'vite.config.ts'), logLevel: 'warn', clearScreen: false,
      build: {outDir: temporary, emptyOutDir: true, copyPublicDir: false}});
  } finally {clearInterval(ticker);}
  const built = await inventory(temporary);
  if (!built.has('/index.html')) throw new Error(`the Vite build wrote no index.html into ${temporary}`);
  const stamp = {schema: BUILD_SCHEMA, source_digest: digest, built_at: new Date().toISOString(), build_ms: Date.now() - started,
    node: process.version, sources, files: Object.fromEntries([...built].map(([route, file]) => [route, file.sha256]))};
  await fsp.writeFile(path.join(temporary, BUILD_STAMP), JSON.stringify(stamp, null, 1) + '\n');
  if (!await renameWithRetry(temporary, dir)) {
    // Another server built the same sources at the same time: use its build if it verifies, and drop this copy.
    const other = await verifyBuild(dir, digest);
    if (!other.ok) throw new Error(`a concurrent build appeared at ${dir} but does not verify (${other.reason})`);
    await fsp.rm(temporary, {recursive: true, force: true});
    return {dir, digest, builtNow: false, files: other.files, stamp: other.stamp, sources, warnings};
  }
  const final = await verifyBuild(dir, digest);
  if (!final.ok) throw new Error(`the fresh build at ${dir} does not verify (${final.reason})`);
  log(`built in ${((Date.now() - started) / 1000).toFixed(1)} s`);
  return {dir, digest, builtNow: true, files: final.files, stamp, sources, warnings};
}
