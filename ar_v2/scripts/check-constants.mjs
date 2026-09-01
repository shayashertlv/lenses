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
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
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
  'TRIVIAL', 'POPULATION_DEFAULTS', 'CAPTURE_DEFAULTS',
  // `CATALOGUE` is a data table, not a tuning constant — the same case as
  // `TEST_FRAMES`. Every number IN it that could be otherwise carries its own
  // ledger row (`ASSUMED_WIDTH_MM`) or its own docstring on the entry.
  'BEATS', 'TEST_FRAMES', 'CATALOGUE', 'VARIANTS', 'ROLL_PREROTATION',
  'UNCERTAINTY_DEFAULTS', 'SEAT_DEFAULTS', 'BUNDLE_DEFAULTS',
  'KEYFRAME_DEFAULTS', 'TRACKER_DEFAULTS', 'PNP_DEFAULTS', 'SNAP_DEFAULTS',
  'CALIBRATION_DEFAULTS',
  'DISPLACEMENT_PRIORS', 'SKIN', 'IRIS', 'POPULATION_HVID',
  'PLAUSIBLE', 'CAMERA_LADDER', 'COVERAGE_THRESHOLDS', 'PD_PLAUSIBLE_MM',
  // `ID1_CARD` sat here until 2026-08-26, exempting a constant deleted with
  // `enroll/card.ts`, and the comment saying so sat two lines below the entry
  // it described. `LM_DEFAULTS` was a second, from the deleted `core/lm.ts`.
  // Both are gone, and the sweep below is what stops the next one lasting a
  // year: an exemption for a name that no longer exists is the same defect as
  // an orphaned ledger row, and until now this was the one list nobody swept.
  // `TRANSLATION_SMOOTHING` and `ROTATION_SMOOTHING` sat here until 2026-09-01,
  // against this docstring's own rule: both have carried provenance in the
  // ledger the whole time. They needed the exemption anyway, because their one
  // row named both of them in a single name cell, and `documented()` matches a
  // whole name or a dotted prefix — not a comma. The row is now two rows and the
  // exemption is gone. A packed name cell is a silent exemption; write one row
  // per exported name.
  'LM', 'EPS',
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
// The section a row sits under. A `## ` line is the ledger's claim about WHERE
// the numbers below it live, and this loop used to throw every one of them away
// on the line below — which is how two headings could name files that do not
// exist, and twenty-five rows could sit under a file that does not declare
// them, with the gate green throughout.
let heading = null;
LEDGER.split('\n').forEach((line, i) => {
  const s = line.trim();
  if (s.startsWith('## ')) {
    heading = { text: s.slice(3).trim(), line: i + 1 };
    return;
  }
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
    heading,
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

  // **And the clause form, which this gate could not see.**
  //
  // `export { FOO }` and `export { foo as BAR }` put a constant on the public
  // surface without ever writing `export const` at the start of a line, so a
  // constant declared plainly and re-exported needed no ledger row at all —
  // the one shape of hole that lets a number reach the public API with no
  // provenance and no complaint. Type-only clauses are excluded: a
  // `export { type X }` names a type, and types are not constants.
  const clause = /^export\s*\{([^}]*)\}/gm;
  while ((match = clause.exec(text)) !== null) {
    for (const part of match[1].split(',')) {
      const spec = part.trim();
      if (!spec || spec.startsWith('type ')) continue;
      const name = (/\bas\s+([A-Za-z_$][\w$]*)/.exec(spec) ?? [, spec])[1].trim();
      if (/^[A-Z][A-Z0-9_]*$/.test(name)) found.push({ name, file: rel });
    }
  }
}

// A row named `SKIN.stiffnessNPerMm` documents the export `SKIN`: the ledger
// answers for a bag of numbers one field at a time, which is the more useful
// granularity, so a dotted prefix counts.
const rowNames = [...byName.keys()];
const documented = (name) =>
  byName.has(name) || rowNames.some((r) => r.startsWith(`${name}.`));

const missing = found.filter((c) => !EXEMPT.has(c.name) && !documented(c.name));

/**
 * The other direction, which this gate could not see in either half.
 *
 * `docs/NEXT-SESSION.md` carried the limitation as a standing warning — "it
 * cannot see an orphaned ROW, or an orphaned EXEMPTION; sweep both by hand when
 * you delete a constant" — and a warning that asks for a manual sweep is a
 * warning that gets skipped. It had already been skipped twice: `ID1_CARD` sat
 * in the exemption list for a constant deleted a day earlier, with the comment
 * announcing its removal two lines underneath it, and `LM_DEFAULTS` outlived
 * `core/lm.ts` entirely.
 *
 * Neither is dangerous on its own. What they are is DEAD PROVENANCE, and a
 * ledger nobody can trust to be current is a ledger nobody reads.
 *
 * A row may legitimately document a field of a bag (`SKIN.stiffnessNPerMm`), so
 * a row is orphaned only when the name before its first dot is exported by
 * nothing and exempted by nothing.
 */
/**
 * The two sweeps below are about THIS repository's own bookkeeping, so they run
 * only when the tree being scanned is this one.
 *
 * `EXEMPT` and the ledger are baked into this script and into `docs/`, but the
 * gate's own tests (`tests/pipeline.test.ts`) copy it into a temp directory
 * holding a two-file `src/` and a four-line ledger, to check the FORMAT rules —
 * duplicate rows, unknown classes, a missing row. In a tree like that every one
 * of the thirty-odd exemptions is orphaned by construction, and the sweeps would
 * fail a fixture that is testing something else entirely.
 *
 * `src/core/` is the sentinel because it is the one directory this tree cannot
 * be itself without, and no fixture has ever created it.
 */
const REAL_TREE = existsSync('src/core');

const exported = new Set(found.map((c) => c.name));
const orphanExemptions = REAL_TREE
  ? [...EXEMPT].filter((name) => !exported.has(name))
  : [];

// **Not "is it exported" — "does the name appear in the source at all".**
//
// The first version of this sweep tested exportedness and flagged 23 rows, all
// of them fine: the ledger legitimately documents module-PRIVATE constants
// (`TYPICAL_VARIANCE_FACTOR`, `AXIS_WEIGHT`), fields of a spec
// (`FrameSpec.templeReachMm`), and quantities named in prose rather than in
// code ("verdict thresholds", "region radii"). A gate that fires on two dozen
// correct rows is a gate that gets switched off, so the test is the weakest one
// that still catches the thing it is for: a row whose name occurs NOWHERE in
// `src/`, which is what a row for a deleted constant looks like.
//
// Prose rows are skipped outright — a row title has to look like an identifier
// before its absence means anything.
const allSource = [...walk('src')]
  .filter((f) => f.endsWith('.ts'))
  .map((f) => readFileSync(f, 'utf8'))
  .join('\n');
const orphanRows = !REAL_TREE ? [] : rowNames.filter((row) => {
  const root = row.split('.')[0].trim();
  if (!/^[A-Za-z_$][\w$]*$/.test(root)) return false;
  if (exported.has(root) || EXEMPT.has(root)) return false;
  return !new RegExp(`\\b${root}\\b`).test(allSource);
});

/**
 * A heading that names the wrong file.
 *
 * The rows are only half the ledger. A `## ` heading says WHERE the numbers
 * under it live, and a reader who follows one to a file that was renamed, or
 * deleted, or never existed at all, has been sent nowhere by the document whose
 * entire job is provenance. It is the same defect as an orphaned row, and it is
 * the one the parser could not see.
 *
 * Two rules, both deliberately weak:
 *
 *   - every `.ts` path a heading names must exist;
 *   - a row whose title is an identifier must be DECLARED in one of them.
 *
 * Declared, not mentioned. The pattern is anchored at column 0 and wants a
 * value keyword, so a constant named in a comment is not a home, a
 * function-local `const` that happens to share the name is not a home, and
 * neither is a `type` or an `interface` of the same name. What the ledger
 * legitimately does that this cannot judge, it skips rather than guesses at:
 * prose titles ("verdict thresholds"), titles naming two constants at once, and
 * fields of a bag or a spec (`FrameSpec.templeReachMm`), which no line regex
 * can find a declaration for. It judges SCREAMING_SNAKE titles only, and that
 * is a rule rather than an accident: a one-word lowercase title like `floorPx`
 * is an identifier by shape, and would take any unrelated module's column-0
 * `const floorPx` as its home. That is the same retreat the sweep above made,
 * for the reason stated there — a gate that fires on correct rows is a gate
 * that gets switched off. On the tree that first ran it, it fired sixteen times
 * and every one was a real defect.
 *
 * The hole it cannot close: a heading may list several files, so widening one
 * to cover the file a row actually lives in silences the row without moving it,
 * and blesses every future row filed there. The failure message says so.
 *
 * A heading whose grammar this cannot parse is the same hole reachable by
 * typo — an ASCII hyphen where the file uses an em dash, a parenthetical — so a
 * heading that mentions a `.ts` file and yields no path is reported rather than
 * skipped. The date banner mentions none and stays silent.
 */
const headingPaths = (h) => h.text
  .split('—')[0]   // "core/head.ts — the back of the head, to hide the arms"
  .split(',')      // "track/pnp.ts, track/tracker.ts"
  .map((p) => p.trim().replace(/`/g, ''))
  .filter((p) => p.endsWith('.ts'));

const declares = (text, name) => new RegExp(
  `^(?:export\\s+)?(?:declare\\s+)?(?:const|let|var)\\s+${name}\\b`,
  'm',
).test(text);

const sourceText = new Map();
if (REAL_TREE) {
  for (const file of walk('src')) {
    if (!file.endsWith('.ts')) continue;
    sourceText.set(relative('.', file).split(sep).join('/'), readFileSync(file, 'utf8'));
  }
}

const deadHeadings = new Map();
const unreadableHeadings = new Map();
const misfiled = [];
if (REAL_TREE) {
  for (const row of rows) {
    if (!row.heading) continue;
    const paths = headingPaths(row.heading);
    if (paths.length === 0) {
      // A date banner names no file and is not a section. A heading that names
      // one and still parses to nothing is a grammar this cannot read, and
      // silence there switches the check off for a whole section.
      if (row.heading.text.includes('.ts')) {
        if (!unreadableHeadings.has(row.heading.text)) {
          unreadableHeadings.set(row.heading.text, row.heading.line);
        }
      }
      continue;
    }
    // Against the walked source map, not `existsSync`: the filesystem is
    // case-insensitive on Windows and case-sensitive on Linux, and a ledger
    // that fails two different ways on two platforms is worse than either.
    const absent = paths.filter((p) => !sourceText.has(`src/${p}`));
    if (absent.length) {
      // The heading is already the failure. Naming its rows one by one would
      // report one defect eight times.
      for (const p of absent) if (!deadHeadings.has(p)) deadHeadings.set(p, row.heading.line);
      continue;
    }
    const root = row.name.split('.')[0].trim();
    if (!/^[A-Z][A-Z0-9_]*$/.test(root)) continue;
    const homes = [...sourceText]
      .filter(([, text]) => declares(text, root))
      .map(([file]) => file);
    if (homes.length === 0) continue;
    if (homes.some((h) => paths.includes(h.replace(/^src\//, '')))) continue;
    misfiled.push({ row, homes });
  }
}

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

if (orphanExemptions.length) {
  console.error('\nexempted, but exported by nothing:');
  for (const name of orphanExemptions) console.error(`  ${name}`);
  console.error(
    '\nAn exemption outlived the constant it excused. Delete it — while it is '
    + 'there, the next constant to take that name inherits a silence nobody chose.',
  );
  failures += orphanExemptions.length;
}

if (orphanRows.length) {
  console.error('\nledger rows for constants that no longer exist:');
  for (const name of orphanRows) console.error(`  ${name}`);
  console.error(
    '\nA row describing a deleted constant is provenance for nothing, and it '
    + 'reads exactly like provenance for something.',
  );
  failures += orphanRows.length;
}

if (deadHeadings.size) {
  console.error('\nledger headings naming a file that does not exist:');
  for (const [path, line] of deadHeadings) {
    console.error(`  docs/CONSTANTS.md:${line}  ${path}`);
  }
  console.error(
    '\nA section heading is the ledger\'s claim about where a number lives. '
    + 'Pointed at a renamed or deleted file, it sends the next reader nowhere.',
  );
  failures += deadHeadings.size;
}

if (unreadableHeadings.size) {
  console.error('\nledger headings that name a .ts file but do not parse as one:');
  for (const [text, line] of unreadableHeadings) {
    console.error(`  docs/CONSTANTS.md:${line}  ## ${text}`);
  }
  console.error(
    '\nA path, then optionally a comma-separated second path, then optionally an '
    + 'em dash and a gloss. Anything else switches this check off for the whole '
    + 'section, silently, which is why it is a failure instead.',
  );
  failures += unreadableHeadings.size;
}

if (misfiled.length) {
  console.error('\nledger rows filed under a heading that does not declare them:');
  for (const m of misfiled) {
    console.error(
      `  docs/CONSTANTS.md:${m.row.line}  ${m.row.name}  under '${m.row.heading.text}'`
      + `  — declared in ${m.homes.join(', ')}`,
    );
  }
  console.error(
    '\nMove the row under a heading that names its file, or open one. Adding the '
    + 'file to the heading instead silences the row and blesses the next one.',
  );
  failures += misfiled.length;
}

if (failures) process.exit(1);
