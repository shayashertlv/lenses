#!/usr/bin/env node
/**
 * Self-containment, enforced.
 *
 * This tree spent its whole life borrowing. `serve.py` mapped `/vendor/` and
 * `/assets/` into `../ar`, and `testkit/fixtures.ts` walked a four-entry ladder
 * that ended in two `../ar` fallbacks — so the face template, the detector
 * binary, the eyewear assets and every browser library came from a sibling
 * checkout that nothing in this repository guaranteed was there. It ran on
 * exactly one machine on earth and no gate noticed.
 *
 * The assets have moved here and the vendor tree is fetched here. This check is
 * what stops that quietly coming undone, and it has to be a STANDING gate
 * rather than a one-off proof: the moment somebody needs an eyewear asset in a
 * hurry, `../ar/assets/glasses/navigator.glb` is right there on disk and works
 * on their machine. A property proved once at the start of a migration and
 * re-proved at the end can regress silently for everything in between.
 *
 * Two passes, and — as in `check-isolation.mjs` — the second is the one that
 * matters.
 *
 *  1. TEXT. No source, script, page or server file may name the sibling.
 *  2. PHYSICAL. Every asset path the app and the harness actually fetch must
 *     resolve to a real file UNDER THIS DIRECTORY. A text pass cannot see a
 *     path that is assembled at run time, and the whole failure being guarded
 *     against is a file that is missing rather than mis-spelled.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');

/**
 * Directories whose text is checked. `dist/` is generated; `vendor/` is not ours.
 *
 * **`docs/` is scanned, and it was the hole.** This gate covered `src tests
 * scripts` plus three loose files, so the two places that actually still pointed
 * at the sibling checkout were both invisible to it: `README.md:346` told every
 * new reader that `vendor/` and `assets/` "are served from `../ar/` during the
 * migration" — the dependency backwards and false since stage 1 — and
 * `docs/OPEN-QUESTIONS.md` pointed at `ar/assets/glasses/`. The gate exists to
 * stop this tree needing a sibling on disk, and the file most able to make
 * somebody go and fetch one was the file it did not read.
 *
 * Prose is the easiest place for a stale path to survive precisely because
 * nothing executes it. That makes it worth MORE scrutiny here, not less.
 */
const SCANNED = ['src', 'tests', 'scripts', 'docs'];
const LOOSE = ['index.html', 'serve.py', 'package.json', 'README.md', 'ATTRIBUTION.md'];

const FORBIDDEN = [
  { pattern: /\.\.[/\\]ar[/\\]/, why: 'reaches into the sibling v1 checkout' },
  { pattern: /\bar[/\\](assets|vendor)\b/, why: "names v1's assets or vendor tree" },
  // The identifier in use, not the English word: "A SIBLING of `headNode`, not
  // a child" is scene-graph prose, not a path into another checkout.
  { pattern: /\bSIBLING\s*[=/]/, why: 'resolves a path into a sibling checkout' },
  // The identifier IN USE, not a mention of it — the same narrowing the
  // `SIBLING` pattern above already got, and for a sharper reason.
  //
  // `serve.py` records in a comment that `ec9c315` deleted this mapping and took
  // the server construction with it, which is the only place that regression is
  // written down. The bare-name pattern fired on that sentence and failed the
  // build. A gate that forbids writing the HISTORY of the thing it removed makes
  // the removal unexplainable, and the next person to touch `main()` is then
  // reading a file that cannot tell them why it looks the way it does.
  //
  // An assignment, a subscript or a member access is use. A word in prose is not.
  { pattern: /\bSHARED_ROOTS\s*[=[.]/, why: 'serves paths from outside this tree' },
];

/**
 * Files that may name the sibling, with the reason.
 *
 * Kept deliberately short and deliberately explicit: an exemption list that
 * grows is this check being negotiated with rather than enforced.
 */
const EXEMPT = new Set([
  join('scripts', 'check-selfcontained.mjs'), // the patterns above are its subject
]);

let failures = 0;

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|mjs|js|html|py|json|md)$/.test(name)) out.push(full);
  }
  return out;
}

const files = [
  ...SCANNED.filter((d) => existsSync(join(ROOT, d))).flatMap((d) => walk(join(ROOT, d))),
  ...LOOSE.map((f) => join(ROOT, f)).filter(existsSync),
];

for (const file of files) {
  const rel = relative(ROOT, file);
  if (EXEMPT.has(rel)) continue;
  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, i) => {
    for (const { pattern, why } of FORBIDDEN) {
      if (pattern.test(line)) {
        console.error(`${rel.split(sep).join('/')}:${i + 1}  ${why}`);
        console.error(`  ${line.trim().slice(0, 110)}`);
        failures++;
      }
    }
  });
}

/**
 * The paths the running app and the harness actually open.
 *
 * Listed rather than discovered, because the point is to fail when one of them
 * goes missing — and a list built by scanning for what exists cannot do that.
 * Keep it in step with `index.html`, `app/main.ts`, `detect/mediapipe.ts` and
 * `testkit/fixtures.ts`.
 */
const REQUIRED = [
  ['assets/face/canonical_face_model.obj', 'the face template — every test and report loads it'],
  ['assets/models/face_landmarker.task', 'the detector binary'],
  ['assets/samples/face-a.jpg', 'the still source main.ts falls back to with no camera'],
  ['vendor/three/three.module.js', 'the renderer'],
  ['vendor/three/addons/loaders/GLTFLoader.js', 'the asset loader'],
  ['vendor/mediapipe/vision_bundle.mjs', 'the detector runtime'],
  ['vendor/mediapipe/wasm/vision_wasm_internal.wasm', 'the detector wasm'],
];

let missing = 0;
for (const [rel, why] of REQUIRED) {
  if (!existsSync(join(ROOT, rel))) {
    console.error(`MISSING  ${rel}  — ${why}`);
    missing++;
  }
}

if (missing) {
  console.error(
    `\n${missing} required file(s) are not under ${ROOT}.`
    + '\nIf vendor/ is the gap, run: node scripts/fetch-vendor.mjs',
  );
}

if (failures || missing) {
  if (failures) {
    console.error(`\nself-containment violated in ${failures} place(s).`);
    console.error('This tree must serve, build and test with no sibling checkout on disk.');
  }
  process.exit(1);
}

console.log(
  `self-contained: ${files.length} files name no sibling, `
  + `${REQUIRED.length} required assets present.`,
);
