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
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const HEADLESS = ['core', 'enroll', 'track', 'fit', 'detect', 'testkit'];

const FORBIDDEN = [
  { pattern: /from ['"]three/, why: 'imports three.js' },
  { pattern: /\bdocument\s*\./, why: 'touches the DOM' },
  { pattern: /\bwindow\s*\./, why: 'touches window' },
  { pattern: /\bnavigator\s*\./, why: 'touches navigator' },
  { pattern: /new\s+(Image|Worker|OffscreenCanvas)\b/, why: 'constructs a browser object' },
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

for (const area of HEADLESS) {
  const dir = join('src', area);
  let files;
  try {
    files = walk(dir);
  } catch {
    continue;
  }
  for (const file of files) {
    if (!file.endsWith('.ts')) continue;
    const rel = relative('.', file).split(sep).join('/');
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

if (failures) {
  console.error(`\nisolation boundary violated in ${failures} place(s).`);
  console.error('The headless half of this tree must run in Node with no browser at all.');
  process.exit(1);
}

console.log(`isolation boundary intact: ${HEADLESS.join(', ')} are headless.`);
