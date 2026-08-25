/**
 * Every exported constant must answer for itself.
 *
 * v1's discipline, made mechanical. It scans `src/` for exported constants and
 * fails if one has no **row** in `docs/CONSTANTS.md`.
 *
 * A row, not a mention. This used to be `LEDGER.includes(name)`, and a substring
 * test says "documented" about a constant that only ever appears inside some
 * other row's prose. `SOFT_VERDICT` was exactly that: no row of its own, one
 * sentence in the `ADVICE_CONFIDENCE` row — and that sentence had the comparison
 * backwards (`ADVICE_CONFIDENCE` is 0.45, `SOFT_VERDICT` is 0.6). The only
 * mention of a number in the whole ledger was wrong about it, and both checks
 * that could have noticed were satisfied.
 *
 * What it deliberately does NOT do is fail on the number of `stated` entries.
 * v1 got this exactly right and the reasoning is worth repeating: driving that
 * count to zero by writing better sentences is precisely the dishonesty the
 * ledger exists to prevent. The count is reported; a reviewer decides.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const LEDGER = readFileSync('docs/CONSTANTS.md', 'utf8');

/**
 * The classes the ledger's own legend defines.
 *
 * `assumed` is here because the ledger uses it — on the temple-reach row, the
 * one whose prose calls it *"the highest-leverage number in the tree"*. The
 * class-mix regex used to list five classes and that row matched none of them,
 * so the single most consequential number in the ledger was silently absent from
 * the count the ledger exists to produce. Which is why an unrecognised class is
 * now a FAILURE rather than a row that quietly does not count: the next new word
 * must not be able to vanish the same way.
 */
const CLASSES = new Set(['physics', 'derived', 'published', 'measured', 'stated', 'assumed']);

/**
 * Names that are structural rather than tuning: types, factories, pure
 * conversions, and the enumerations that only give a thing a name.
 *
 * Anything with a real row in the ledger does not need to be here — the row is
 * the better answer — so this list is only for constants that genuinely have no
 * provenance to state.
 */
const EXEMPT = new Set([
  'CV_TO_GL', 'MM_TO_SCENE', 'GRAVITY_N_PER_G', 'FACE_MODEL_VERSION',
  'MESH_VERTEX_COUNT', 'MESH_LANDMARK_COUNT', 'FIRST_IRIS_INDEX',
  'DETECTOR_LANDMARK_COUNT', 'INTRINSICS_FREE_F', 'INTRINSICS_FIXED',
  'TRIVIAL', 'LM_DEFAULTS', 'POPULATION_DEFAULTS', 'CAPTURE_DEFAULTS',
  // `CATALOGUE` is a data table, not a tuning constant — the same case as
  // `TEST_FRAMES`. Every number IN it that could be otherwise carries its own
  // ledger row (`ASSUMED_WIDTH_MM`) or its own docstring on the entry.
  'BEATS', 'TEST_FRAMES', 'CATALOGUE', 'VARIANTS', 'ROLL_PREROTATION',
  'UNCERTAINTY_DEFAULTS', 'SEAT_DEFAULTS', 'BUNDLE_DEFAULTS',
  'KEYFRAME_DEFAULTS', 'TRACKER_DEFAULTS', 'PNP_DEFAULTS', 'SNAP_DEFAULTS',
  'CALIBRATION_DEFAULTS',
  'DISPLACEMENT_PRIORS', 'SKIN', 'IRIS', 'POPULATION_HVID', 'ID1_CARD',
  'PLAUSIBLE', 'CAMERA_LADDER', 'COVERAGE_THRESHOLDS', 'PD_PLAUSIBLE_MM',
  // `ID1_CARD` was here until 2026-08-25, exempting a constant deleted with
  // `enroll/card.ts` in f9c9093. An exemption for a name that no longer exists
  // is the same defect as an orphaned ledger row, in the one list nobody sweeps.
  'TRANSLATION_SMOOTHING', 'ROTATION_SMOOTHING', 'LM', 'EPS',
  // A userData KEY, not a number — it names an ownership flag on a scene node.
  'CACHED_BY_CALLER',
]);

// ------------------------------------------------------------------ the ledger

/**
 * The ledger's rows, parsed as a table rather than grepped.
 *
 * Structural parsing is what makes the duplicate and unknown-class checks
 * possible at all: both need to know which cell is which, and a regex that
 * matches anywhere on the line cannot. It also drops the legend table at the top
 * of the file, which the old class-mix regex counted as five extra rows — the
 * reported mix was one too many in every class.
 */
const rows = [];
const unparsed = [];
LEDGER.split('\n').forEach((line, i) => {
  const s = line.trim();
  if (!s.startsWith('|') || !s.endsWith('|')) return;
  // Split on pipes the row did not escape. A `why` cell routinely wants a
  // literal `|` — a sweep written `1 | 3 | 5`, a type written `number | null` —
  // and a plain `.split('|')` turns that row into five cells, which the
  // four-cell test below then drops on the floor. Silently: the row vanishes,
  // its constant has no provenance, and the gate stays green. That is the exact
  // failure this file exists to prevent, so the split has to understand `\|`.
  const cells = s.slice(1, -1)
    .split(/(?<!\\)\|/)
    .map((c) => c.trim().replace(/\\\|/g, '|'));
  if (cells.every((c) => /^:?-{3,}:?$/.test(c))) return; // separator
  if (cells[0] === 'constant') return;                   // header
  if (cells.length !== 4) {
    // FEWER than four cells is a different table — the two-column legend at the
    // top of the file, for one. MORE than four is a four-column row that split
    // on a pipe somebody forgot to escape, and that is the case worth shouting
    // about: it looks like a documented constant and counts as an undocumented
    // one.
    if (cells.length > 4) unparsed.push({ line: i + 1, cells: cells.length });
    return;
  }
  rows.push({
    name: cells[0].replace(/`/g, ''),
    cls: cells[2].replace(/`/g, ''),
    line: i + 1,
  });
});

let failures = 0;

// A row that looks like a constant but does not parse is worse than a missing
// row, because a missing row fails the ledger check below and this one does
// not. Name it rather than skipping it.
for (const u of unparsed) {
  console.error(
    `docs/CONSTANTS.md:${u.line}: a row naming a constant did not parse ` +
    `(${u.cells} cells, expected 4).`,
  );
  console.error('    An unescaped `|` in the `why` cell is the usual cause — write it `\\|`.');
  failures++;
}

// One constant, one row. `SKIN.hookStiffnessNPerMm` had two that contradicted
// each other — one saying it has six times LESS leverage than the temple reach,
// the other calling its absence "the largest modelling error in this file" —
// and a checker that only asks whether a name APPEARS cannot see that at all.
const byName = new Map();
for (const row of rows) {
  if (!byName.has(row.name)) byName.set(row.name, []);
  byName.get(row.name).push(row.line);
}
for (const [name, lines] of byName) {
  if (lines.length > 1) {
    console.error(`docs/CONSTANTS.md: \`${name}\` has ${lines.length} rows (lines ${lines.join(', ')})`);
    console.error('    Two rows for one constant means at least one of them is stale.');
    failures++;
  }
}

// An unrecognised class token is a failure, not a silent zero. See CLASSES.
for (const row of rows) {
  if (!CLASSES.has(row.cls)) {
    console.error(`docs/CONSTANTS.md:${row.line}  \`${row.name}\` has class '${row.cls}', which is not one of: ${[...CLASSES].join(', ')}`);
    failures++;
  }
}

// ------------------------------------------------------------------ the source

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  return statSync(full).isDirectory() ? walk(full) : [full];
});

const found = [];
for (const file of walk('src')) {
  if (!file.endsWith('.ts')) continue;
  const rel = relative('.', file).split(sep).join('/');
  const text = readFileSync(file, 'utf8');
  // `export const NAME` where NAME is SCREAMING_SNAKE — the shape a tuning
  // constant takes in this tree. Functions and types are not constants.
  const pattern = /^export const ([A-Z][A-Z0-9_]*)\s*[:=]/gm;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    found.push({ name: match[1], file: rel });
  }
}

// A row named `SKIN.stiffnessNPerMm` documents the export `SKIN`: the ledger
// answers for a bag of numbers one field at a time, which is the more useful
// granularity, so a dotted prefix counts.
const rowNames = [...byName.keys()];
const documented = (name) =>
  byName.has(name) || rowNames.some((r) => r.startsWith(`${name}.`));

const missing = found.filter((c) => !EXEMPT.has(c.name) && !documented(c.name));

// The class mix, reported not gated.
const counts = {};
for (const row of rows) {
  if (CLASSES.has(row.cls)) counts[row.cls] = (counts[row.cls] ?? 0) + 1;
}

console.log(`constants ledger: ${found.length} exported constants scanned, ${rows.length} ledger rows`);
console.log('  ' + Object.entries(counts)
  .sort((a, b) => b[1] - a[1])
  .map(([k, v]) => `${v} ${k}`)
  .join(' · '));

if (counts.stated) {
  console.log(
    `  ${counts.stated} are 'stated' — somebody chose them. That number is reported,`,
  );
  console.log("  not gated: driving it to zero by writing better sentences would be worse.");
}

if (missing.length) {
  console.error('\nno row in docs/CONSTANTS.md:');
  for (const c of missing) console.error(`  ${c.name}  (${c.file})`);
  console.error('\nA constant with no provenance is a constant nobody can review.');
  failures += missing.length;
}

if (failures) process.exit(1);
