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

/** Directories whose text is checked. `dist/` is generated; `vendor/` is not ours. */
const SCANNED = ['src', 'tests', 'scripts'];
const LOOSE = ['index.html', 'serve.py', 'package.json'];

const FORBIDDEN = [
  { pattern: /\.\.[/\\]ar[/\\]/, why: 'reaches into the sibling v1 checkout' },
  { pattern: /\bar[/\\](assets|vendor)\b/, why: "names v1's assets or vendor tree" },
  // The identifier in use, not the English word: "A SIBLING of `headNode`, not
  // a child" is scene-graph prose, not a path into another checkout.
  { pattern: /\bSIBLING\s*[=/]/, why: 'resolves a path into a sibling checkout' },
  { pattern: /SHARED_ROOTS/, why: 'serves paths from outside this tree' },
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
    else if (/\.(ts|mjs|js|html|py|json)$/.test(name)) out.push(full);
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
