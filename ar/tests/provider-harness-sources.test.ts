/** The pipeline's AR harness (qa/provider-comparison.mjs, run by automation's bsa.archeck for every observation). Two
 *  properties the renders' validity depends on (2026-09-30):
 *  - The 'ar' stage's source snapshot (source_snapshot_stable) covers every runtime file the page imports. It was a
 *    hand-kept list that went stale twice: translucent-twin.ts and translucent-look-through.ts, which draw every crystal
 *    pixel, were never added, so an edit to them during a run left the run "stable".
 *  - The harness's vite dependency cache is its own (review SPEED-4): the default ar/node_modules/.vite is shared with the
 *    AR app's dev server and the other ar/qa scripts, whose configs differ, and concurrent re-optimisations lost a run. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const harness = await fs.readFile(path.join(root, 'qa/provider-comparison.mjs'), 'utf8');

/** The harness's own importClosure, evaluated on this checkout (the script itself runs a browser at import). */
async function harnessClosure(roots: string[]): Promise<string[]> {
  const start = harness.indexOf('async function importClosure(roots){');
  assert.ok(start >= 0, 'the harness computes its source list');
  const end = harness.indexOf('\n}', start) + 2;
  const make = new Function('fs', 'path', 'root', `${harness.slice(start, end)}\nreturn importClosure;`) as
    (f: typeof fs, p: typeof path, r: string) => (roots: string[]) => Promise<string[]>;
  return make(fs, path, root)(roots);
}

/** Every relative import of a file, by an independent reading (TypeScript import / export ... from). */
async function directImports(name: string): Promise<string[]> {
  const text = await fs.readFile(path.join(root, name), 'utf8');
  return [...text.matchAll(/^\s*(?:import|export)\b[^'"]*?from\s+'(\.[^']+)'/gm)]
    .map(match => path.posix.normalize(path.posix.join(path.posix.dirname(name), match[1]!)));
}

test('the harness snapshots the runtime\'s whole import closure, the crystal and reflection files included', async () => {
  const closure = await harnessClosure(['src/render/renderer.ts', 'src/eyewear/catalog.ts', 'src/render/continuity.ts']);
  for (const file of ['src/render/translucent-twin.ts', 'src/render/translucent-look-through.ts', 'src/render/eyewear-reflection.ts',
    'src/render/lens-material.ts', 'src/render/eyewear-volume.ts', 'src/eyewear/optical-material.ts']) assert.ok(closure.includes(file), file);
  // Closed under the direct imports of each member, read independently.
  for (const file of closure) for (const imported of await directImports(file)) assert.ok(closure.includes(imported), `${file} -> ${imported}`);
  assert.deepEqual(closure, [...closure].sort(), 'sorted, so a report\'s implementation map is stable');
  assert.ok(!/const AR_STAGE_SOURCES=\['src\//.test(harness), 'no hand-kept list');
  assert.ok(harness.includes("const AR_STAGE_SOURCES=stage==='ar'?await importClosure(['src/render/renderer.ts','src/eyewear/catalog.ts','src/render/continuity.ts']):[];"));
  // The page itself imports only these three from src/ (plus three and its own lighting module).
  const page = await fs.readFile(path.join(root, 'qa/provider-comparison-ar.html'), 'utf8');
  assert.deepEqual([...page.matchAll(/from '\.\.\/(src\/[^']+)'/g)].map(match => match[1]).sort(),
    ['src/eyewear/catalog.ts', 'src/render/continuity.ts', 'src/render/renderer.ts']);
});

test('the harness keeps its own vite dependency cache, apart from the dev server\'s node_modules/.vite', () => {
  const declared = /const HARNESS_VITE_CACHE_DIR='([^']+)';/.exec(harness);
  assert.ok(declared, 'the cache folder is named');
  assert.equal(declared[1], 'node_modules/.vite-provider-comparison');
  assert.notEqual(path.posix.normalize(declared[1]!), 'node_modules/.vite');
  assert.ok(harness.includes('createServer({configFile:false,root,cacheDir:path.join(root,HARNESS_VITE_CACHE_DIR),'), 'passed to the server');
});
