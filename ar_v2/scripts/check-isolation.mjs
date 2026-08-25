/**
 * The isolation boundary, enforced.
 *
 * `core/`, `enroll/`, `track/`, `fit/`, `detect/` and `testkit/` must not import
 * three.js and must not reach for the DOM. They have to run in Node with no
 * browser at all, because that is what makes every accuracy number in this tree
 * reproducible without a camera, a GPU or a person.
 *
 * This is v1's own lesson made mechanical. Its `main.js` / `frame.js` split
 * existed for exactly this reason — so the arithmetic could be driven headless —
 * and it was maintained by discipline alone. Discipline is what fails at 2 a.m.
 *
 * Two passes, and the second one is the one that matters: a source-text
 * blacklist, then an actual `import()` of every built module. See `importPass`
 * for why one without the other is not a check.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

const HEADLESS = ['core', 'enroll', 'track', 'fit', 'detect', 'testkit'];

const FORBIDDEN = [
  { pattern: /from ['"]three/, why: 'imports three.js' },
  { pattern: /\bdocument\s*\./, why: 'touches the DOM' },
  { pattern: /\bwindow\s*\./, why: 'touches window' },
  { pattern: /\bnavigator\s*\./, why: 'touches navigator' },
  { pattern: /new\s+(Image|Worker|OffscreenCanvas)\b/, why: 'constructs a browser object' },
  // `self` is the worker's global. It is not a Node global, so a module that
  // installs `self.onmessage = ...` at import time throws `ReferenceError: self
  // is not defined` the moment anything headless imports it. That is exactly
  // what `enroll.worker.ts` did while this gate printed "intact" and exited 0.
  { pattern: /\bself\s*[.\[=]/, why: 'uses the worker global `self`' },
  { pattern: /\b(localStorage|sessionStorage|importScripts)\b/, why: 'uses browser-only storage or loading' },
  // Deliberately NOT forbidden: `fetch` and `MessageEvent`. Both have been Node
  // globals since 18, so a module using them still runs headless — banning them
  // would turn a "must run in Node" rule into a "must not touch the network"
  // rule, which is a different rule that nobody agreed to, and would fire on
  // code that is fine.
];

/**
 * `detect/mediapipe.ts` is the boundary layer: it names browser-side types in
 * its signatures but takes the runtime module as an argument rather than
 * importing it, which is precisely what the exemption is for.
 */
const ALLOW = new Set(['src/detect/mediapipe.ts']);

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  return statSync(full).isDirectory() ? walk(full) : [full];
});

let failures = 0;
/** Every headless module, ALLOW-listed ones included — the import pass wants them all. */
const modules = [];

for (const area of HEADLESS) {
  const dir = join('src', area);
  let files;
  try {
    files = walk(dir);
  } catch {
    continue;
  }
  for (const file of files) {
    if (!file.endsWith('.ts') || file.endsWith('.d.ts')) continue;
    const rel = relative('.', file).split(sep).join('/');
    modules.push(rel);
    if (ALLOW.has(rel)) continue;

    const lines = readFileSync(file, 'utf8').split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      // A comment about the browser is prose, not use.
      const trimmed = line.trim();
      if (trimmed.startsWith('*') || trimmed.startsWith('//') || trimmed.startsWith('/*')) continue;
      for (const { pattern, why } of FORBIDDEN) {
        if (pattern.test(line)) {
          console.error(`${rel}:${i + 1}  ${why}`);
          console.error(`    ${trimmed}`);
          failures++;
        }
      }
    }
  }
}

/**
 * The check that could not have been fooled: actually import every built
 * headless module in this Node process.
 *
 * Everything above reasons about source text, and text is guessable. The gate
 * was green on `enroll.worker.ts`, which set `self.onmessage` at top level: no
 * pattern matched, so the script printed "isolation boundary intact" and exited 0
 * on the one file in its enforced set that threw `ReferenceError: self is not
 * defined` the instant Node loaded it.
 * A blacklist can only ever catch the browser globals somebody thought of. This
 * pass catches all of them, plus top-level work that needs a DOM, plus a
 * transitive import of something that does — because it runs the loader for
 * real.
 *
 * It reads `dist/`, so it needs a build. When there is no build it SKIPS, and
 * the skip is printed as loudly as a failure would be: a check that quietly does
 * nothing is precisely the bug this file exists to prevent, and it is the bug
 * this file just had.
 */
async function importPass() {
  if (!existsSync('dist')) {
    console.warn('SKIPPED the import pass: no dist/. Only the source-text patterns ran.');
    console.warn('  Build first (`npm run build`) to get the check that actually loads each module.');
    return 0;
  }
  let broken = 0;
  let skipped = 0;
  let loaded = 0;
  for (const rel of modules) {
    const built = resolve('dist', rel.replace(/\.ts$/, '.js'));
    if (!existsSync(built)) { skipped++; continue; }
    try {
      await import(pathToFileURL(built).href);
      loaded++;
    } catch (error) {
      console.error(`${rel}  does not load in Node`);
      console.error(`    ${error?.message ?? error}`);
      broken++;
    }
  }
  if (skipped) {
    console.warn(`SKIPPED ${skipped} module(s) in the import pass: no built .js in dist/.`);
    console.warn('  A stale build hides exactly the failures this pass exists to find.');
  }
  if (!broken) console.log(`  ${loaded} module(s) imported cleanly in Node.`);
  return broken;
}

const broken = await importPass();

if (failures || broken) {
  if (failures) console.error(`\nisolation boundary violated in ${failures} place(s).`);
  if (broken) console.error(`\n${broken} headless module(s) failed to import in Node.`);
  console.error('The headless half of this tree must run in Node with no browser at all.');
  process.exit(1);
}

console.log(`isolation boundary intact: ${HEADLESS.join(', ')} are headless.`);
