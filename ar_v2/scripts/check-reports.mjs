/**
 * A committed report must describe the code that exists.
 *
 * `reports/*.txt` are this tree's instruments: enrolment accuracy, where the
 * frame comes to rest, what happens past 40 degrees of yaw, what the scanned
 * face hides. They are generated, checked in, and then quoted — by
 * `README.md`, by `docs/`, and by every session that reads them instead of
 * re-running them. Nothing has ever checked that the file still describes the
 * tree it sits in.
 *
 * It had drifted. Measured 2026-08-25, regenerating all three prose-headed
 * reports at their declared seed:
 *
 *     enroll.txt   reproduces EXACTLY, every accuracy digit; only the `ms`
 *                  column moves, and that is the machine, not the code.
 *     seat.txt     stale. `standard` descent median 1.66 -> 6.94 mm, wide-pads
 *                  4.58 -> 12.77, pad load 0.86 -> 0.70, and a whole COLUMN
 *                  renamed ("tilt advice deg" -> "pad tilt deg").
 *     track.txt    stale. Rotation error on the smoothed arm 1.93 -> 3.25 deg
 *                  at 15 degrees of yaw and 2.21 -> 5.46 at 45, while `lost`
 *                  frames went 15/3/2/4/25 -> 0 across the board and jitter
 *                  improved 1.686 -> 1.052 mm.
 *
 * `docs/NEXT-SESSION.md` said all three were stale. Two were. The one it named
 * first was the one that was fine, which is its own argument for a gate over a
 * recollection.
 *
 * ## What is hashed, and why it is two hashes rather than one
 *
 * Hashing the report's own text does not work: every report carries a wall
 * clock or a per-solve `ms` column, so a regenerated copy never matches byte
 * for byte on a different machine. Hashing the constants the header names does
 * not work either. Of the six this comment used to list, four — keyframes 24,
 * rounds 3, fieldPriorScale 8, TYPICAL_VARIANCE_FACTOR 1.9 — held their values
 * right through the drift, so a gate on them would have been green the whole
 * way, which by this tree's own rule makes it a bug rather than a check. The
 * other two, SIDEWALL_BAND_MM and VERTEX_SEAT_SIGMA_MM, are not constants of
 * this tree at all: they were exports of `fit/bearing.ts`, which left the
 * working tree at `f9c9093` and was never a tracked file. And seat.txt names
 * none of the six. The conclusion holds; the premise it was written on did
 * not.
 *
 * So two:
 *
 *  - **`source`** — sha-256 over the report generator's transitive local import
 *    graph, each file transpiled with `removeComments: true`, plus the template
 *    mesh the fixtures load. Comments in this tree carry the measurements and
 *    change constantly; the code under them does not. Cheap: a fifth of a
 *    second, so it runs on every `npm test`.
 *  - **`canary`** — sha-256 of the report's own generator run, in the report's
 *    own configuration, at `subjects: 1`, with timings stripped. Exact for that
 *    configuration, and expensive: 2 to 30 seconds a report and roughly a
 *    minute for all four, measured 2026-09-01 on three machines and varying
 *    15-20% between them — so it runs only when `source` has already drifted.
 *
 * The two together give the honest answer without the cost. A drifted `source`
 * with a matching `canary` means the code moved and the numbers THE CANARY CAN
 * SEE did not. This comment used to say it meant the report was still true, and
 * that was a claim neither hash could support: the canary ran a fixed
 * `{ seed: 11, subjects: 1 }` rather than the report's own options, so for
 * `occlusion` — regenerated as the whole five-seed campaign — the campaign
 * seeds and the replication-population rule were unreachable and the across-seed
 * spread was degenerate, one realisation where the median, the min and the max
 * are the same number, so rewriting any of them was certified rather than
 * caught. The
 * seed half of that is fixed below; the population half is not, and
 * `canaryOptions` says exactly what one subject still cannot see. A drifted
 * `canary` means the committed numbers describe code that no longer exists, and
 * that fails the build.
 *
 * Regenerate and re-stamp with `npm run report:<name>`.
 */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, resolve, relative } from 'node:path';
import ts from 'typescript';

/**
 * The four instruments, and where each one's generated body begins.
 *
 * `bodyStartsWith` is the first line the generator emits. Everything above it
 * in the committed file is hand-written preamble and is preserved across a
 * regeneration — it is the part that says which realisation this is and why,
 * and no generator produces it.
 */
const REPORTS = [
  { name: 'seat', entry: 'src/testkit/report-seat.ts', fn: 'runSeatReport', bodyStartsWith: 'SEAT — ', realisation: 'seed 11', options: { seed: 11 } },
  { name: 'track', entry: 'src/testkit/report-track.ts', fn: 'runTrackReport', bodyStartsWith: 'TRACKING — ', realisation: 'seed 11', options: { seed: 11 } },
  { name: 'enroll', entry: 'src/testkit/report-enroll.ts', fn: 'runEnrollReport', bodyStartsWith: 'ENROLLMENT ACCURACY', realisation: 'seed 11', options: { seed: 11 } },
  // The occlusion instrument runs the whole five-seed campaign by default and
  // its committed copy IS that campaign, so it is regenerated without a seed —
  // and, since 2026-09-01, so is its canary. A canary is a fingerprint of the
  // code rather than a replication of the result, but it has to fingerprint the
  // code the committed body RAN: pinning this one to seed 11 took the other
  // branch of `singleSeed` (report-occlusion.ts:1047) and left four fifths of
  // the campaign out of the hash.
  { name: 'occlusion', entry: 'src/testkit/report-occlusion.ts', fn: 'runOcclusionReport', bodyStartsWith: 'OCCLUSION — ', realisation: 'campaign', options: {} },
];

/** The one file the fixtures read off disk. Every number depends on it. */
const TEMPLATE = 'assets/face/canonical_face_model.obj';

/**
 * The stamp, and what each of its three hashes is for.
 *
 *   source  the generator's transitive import graph, comments stripped. Cheap,
 *           runs every time, and answers "did the code that produced this
 *           report change?"
 *   canary  a one-subject run of the generator itself, in the report's own
 *           configuration. Expensive, runs only when `source` has drifted, and
 *           answers "did the change move the numbers this configuration can
 *           see, or was it a comment?" — `canaryOptions` names the ones it
 *           cannot see.
 *   body    a hash of the COMMITTED BYTES below the preamble.
 *
 * **`body` was missing and its absence was the hole this gate exists to close.**
 * `check()` compared `source` and `canary` — both of which describe the CODE —
 * and never once read the report it was checking. So the one edit a report
 * cannot survive, somebody changing a number in it by hand, passed silently:
 * the generator was untouched, both code hashes matched, and the gate printed
 * "unchanged" over a figure that had been typed. A gate guarding published
 * numbers that never looks at the published numbers is the shape of defect this
 * tree is named after.
 *
 * The hash is taken over the body with `\r` stripped, because git checks these
 * files out CRLF and the generator writes LF — comparing them raw would fail
 * every report on a fresh clone and teach everyone to ignore this gate.
 */
const PROVENANCE =
  /^\[provenance\] (.+?) source=([0-9a-f]{16}) canary=([0-9a-f]{16})(?: body=([0-9a-f]{16}))?$/;

/** The committed body, normalised, as the stamp hashes it. */
function bodyHashOf(lines, bodyAt) {
  const body = lines.slice(bodyAt).join('\n').replace(/\r/g, '');
  return createHash('sha256').update(body).digest('hex').slice(0, 16);
}

// ------------------------------------------------------------- source hash

/**
 * Every `.ts` the entry point reaches through relative imports.
 *
 * Bare specifiers are not followed: there are none in this tree's headless
 * half, and following `node:fs` would hash the platform.
 */
function importGraph(entry) {
  const seen = new Set();
  const queue = [resolve(entry)];
  while (queue.length) {
    const file = queue.pop();
    if (seen.has(file) || !existsSync(file)) continue;
    seen.add(file);
    const src = readFileSync(file, 'utf8');
    // `from './x.js'` and `from '../y/z.js'` — the extension is `.js` because
    // the emitted code is Node ESM, and the file on disk is `.ts`.
    for (const m of src.matchAll(/from\s+['"](\.[^'"]+)\.js['"]/g)) {
      queue.push(resolve(dirname(file), `${m[1]}.ts`));
    }
  }
  return [...seen].sort();
}

function sourceHash(entry) {
  const h = createHash('sha256');
  for (const file of importGraph(entry)) {
    // Comments in this tree are long, they carry the reasoning and the
    // measurements, and they are edited constantly. Stripping them is what
    // makes this hash mean "the code changed" rather than "somebody wrote down
    // what they measured". TypeScript's own transpiler does it, so the answer
    // does not depend on a regex meeting a divide sign.
    const stripped = ts.transpileModule(readFileSync(file, 'utf8'), {
      compilerOptions: { removeComments: true, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    h.update(relative(process.cwd(), file).replace(/\\/g, '/'));
    h.update(stripped);
  }
  h.update(readFileSync(TEMPLATE));
  return h.digest('hex').slice(0, 16);
}

// ------------------------------------------------------------- canary hash

/**
 * Timings are the machine, not the code.
 *
 * `enroll.txt` reproduces every accuracy digit at the same seed and still
 * differs from its committed copy, because its `ms` column ran 392 on the
 * machine that made it and 557 here. Strip anything that is a duration before
 * hashing, or the gate reports a drift on every different laptop.
 */
function stripTimings(text) {
  // **The columns first, on unmodified geometry.** The free-text rules below
  // rewrite line LENGTHS, and the column work is positional — it reads the
  // character span of each column off the table's own underline. Running the
  // text rules first meant a literal `45 ms` in a cell, or a header whose last
  // word before `ms` ended in a digit, shortened the header out from under the
  // span and desynchronised every offset on that table.
  const lines = text.split('\n');

  // The per-row `ms` columns in seat.txt and enroll.txt are BARE integers under
  // an `ms` header, so no unit can find them and their position has to.
  //
  // **Blank the whole span, not digit-for-digit.** Replacing each `\d` with a
  // `-` erases the VALUE and keeps its WIDTH, and the column is right-aligned,
  // so a 99 ms solve hashed as ` --` and a 114 ms one as `---`. That is a
  // machine-speed bit inside a hash whose entire job is to be independent of
  // the machine: the seat canary took four distinct values in ten consecutive
  // runs of the same build (11f0e9f2, dab9f30e, dd50cac2, 99203b68), one of
  // which was the committed stamp. So `npm test` failed the reports gate at
  // random on an unchanged tree, and passed it at random on a changed one -
  // which is worse than a gate that cannot fail, because it trains its reader
  // to re-run until it goes green.
  //
  // Positionally, and not "the second-from-last integer on the line". That was
  // the first version and it is the shape of blunt instrument this whole gate
  // exists to avoid: it rewrites any row ending in two integers, so a real data
  // column that happened to sit there would be silently blanked and the canary
  // would stop being able to see it change. These tables underline themselves,
  // so the separator row gives the exact character span of every column, and
  // the header above it says which span is the clock.
  // **The column's WIDTH is the clock too, so the column has to go entirely.**
  //
  // Blanking the span digit-for-digit erased the value and kept the width, and
  // that was fixed by blanking the whole span instead. It was not enough, and
  // the residue is the same defect one level out: `table()` sizes every column
  // to its widest cell and the `ms` header is two characters, so a run whose
  // slowest solve reads 99 emits a TWO-dash column and one that reads 101 emits
  // three. Different underline, different padding on every row, different hash,
  // on identical code. The 2-wide case never even reached the blanking: the
  // separator was matched with `-{3,}`, so a 2-dash run failed the line test,
  // `span` stayed null, and the raw milliseconds went into the canary.
  //
  // A width test cannot be written against a fixture that fixes the width,
  // which is why the landed fix looked complete: its fast-machine fixture
  // substituted `99` right-aligned INSIDE a three-wide span, so it exercised
  // jitter within a width and could not see jitter between widths.
  //
  // So match a run of any width, and CUT the column out — header, underline and
  // every row — along with the two-space gutter in front of it. Every other
  // column's layout is independent of this one's width, so what survives the cut
  // is the same string whatever the clock read.
  const GUTTER = 2;

  // Any run width. `-{3,}` was the bug — a two-digit clock emits a two-dash
  // column — and `-{2,}` is the mirror of it: a table carrying any
  // one-character column would fail the test outright and its clock would go
  // into the hash raw. A data row of single-dash placeholders does match this,
  // and is harmless: a one-character slice of the line above cannot read as
  // `ms`, so it yields no columns and is skipped. Nothing is reset by it, which
  // is the point of identifying spans in a separate pass.
  const SEPARATOR = /^-+(\s+-+)+\s*$/;

  // A trailing `ms` word, not the exact string, so renaming the header to
  // `solve ms` does not silently revert this gate to hashing the clock. The
  // word boundary is what keeps `rms` out.
  const IS_CLOCK = /(^|\s)ms$/;

  // **Identified in one pass, applied in another.** The header is read from the
  // ORIGINAL text: cutting in place while a previous table's span was still
  // live meant two tables with no blank line between them corrupted the second,
  // whose header had already been cut as a row of the first.
  const cuts = new Map();
  for (let i = 1; i < lines.length; i++) {
    if (!SEPARATOR.test(lines[i])) continue;
    const header = lines[i - 1];
    const spans = [];
    for (const m of lines[i].matchAll(/-{2,}/g)) {
      const cell = header.slice(m.index, m.index + m[0].length).trim();
      if (IS_CLOCK.test(cell)) spans.push([Math.max(0, m.index - GUTTER), m.index + m[0].length]);
    }
    if (!spans.length) continue;
    // The header, the underline, and every row down to the blank line that ends
    // the table. Two tables with no blank line between them overlap here, and
    // that resolves itself: separators are walked in order, so the second
    // table's own spans overwrite the entries the first claimed for its lines.
    for (let j = i - 1; j < lines.length; j++) {
      if (j > i && lines[j].trim() === '') break;
      cuts.set(j, spans);
    }
  }
  // Right to left, so an earlier column's offsets are still valid after a later
  // one has been removed. Every clock column goes, not just the last: the old
  // loop reassigned a single `span` per match, so a table with two of them kept
  // the first one's raw values and its width.
  for (const [j, spans] of cuts) {
    let line = lines[j];
    for (let k = spans.length - 1; k >= 0; k--) {
      line = line.slice(0, spans[k][0]) + line.slice(spans[k][1]);
    }
    lines[j] = line;
  }

  return lines.join('\n')
    .replace(/Wall clock:.*$/gm, 'Wall clock: -')
    .replace(/\b\d+(\.\d+)?\s*ms\b/g, '- ms');
}

/**
 * The configuration the canary is taken at: the committed report's own options,
 * at one subject.
 *
 * **The options are the report's, not the gate's.** This was a flat
 * `{ seed: 11, subjects: 1 }` until 2026-09-01, and for `seat`, `track` and
 * `enroll` that is their own `options` copied out by hand. For `occlusion` it
 * was not: its committed copy is the five-seed campaign, so it is regenerated
 * with no seed at all, and a forced seed made `runOcclusionReport` take the
 * other branch of `singleSeed` (report-occlusion.ts:1047). `CAMPAIGN_SEEDS`,
 * the replication-population rule and the across-seed `spread()` — degenerate
 * at one realisation, where the median, the min and the max are the same number
 * — were unreachable in the hash that certified them. Measured on a patched
 * mirror of `dist/`: dropping two campaign seeds left the old canary at
 * b219d011d08b41bb bit for bit and moves this one to 9aa044c1290368d1;
 * replacing the across-seed median with a midrange, likewise, and it moves to
 * ba1f095b3033d019. Both edits rewrite all fourteen headline lines of the
 * committed body. The price is the campaign: 25 s against 5, paid only after
 * `source` has drifted.
 *
 * **What one subject still cannot see, precisely.** `subjects: 1` stays, because
 * the population IS the wall clock — the committed bodies run 6 (seat, track),
 * 8 (enroll) and 10 (occlusion's first seed), and a canary at those costs
 * minutes. So this hash does not cover:
 *
 *   - the DEFAULT subject count itself. It lives in the generator, so changing
 *     it drifts `source` and moves the committed body, and it cannot move a
 *     canary that overrides it. Measured: seat's `subjects: 6` -> 4 leaves the
 *     canary at ee27de8918f05e53 while what a canary at seat's FULL options
 *     would have seen moves c6fc0ed78c0a1fa0 -> 0473fac54972e79f. Those two are
 *     hashed the way this canary hashes, through `stripTimings`; they are not
 *     the `body=` stamp, which is taken over the raw committed bytes and so
 *     embeds the ms columns — three runs of one unchanged build hash three
 *     different values, which is why no fixed pair can be quoted for it.
 *   - anything reachable only past population slot 0 — `HVID_GROUP_MEANS[1..3]`
 *     (synthetic.ts:200), the per-slot rejection retry, and the pooled-iris
 *     ablation's index rule (report-occlusion.ts:894-896), which at three
 *     subjects names all three — so a change to WHICH subjects it names is
 *     invisible, though a change to HOW MANY is not.
 *   - the sample size inside `distribution()`: the canary aggregates 3 subjects
 *     (1 sampled + the two named extremes) where the bodies aggregate 8 to 12.
 *   - `Math.min(4, fullCount)` (report-occlusion.ts:1055). At one subject both
 *     arms are 1, so the replication-population rule stays dead even with all
 *     five seeds live — measured, `min(4)` -> `min(9)` leaves this canary at
 *     6274324995583b84.
 *
 * Nothing in this gate catches those; they surface when somebody regenerates
 * the report and the body moves. That is why `check()` reports what the canary
 * ran rather than that the report is true.
 *
 * One more axis, because it bounds everything above: `source` is hashed from
 * `src/` and the canary is imported from `dist/`, and `npm run check:reports`
 * does not build. So the canary fingerprints whatever the last `npm run build`
 * emitted, and its answer is only as fresh as that. `npm test` builds first.
 */
function canaryOptions(report) {
  return { ...report.options, subjects: 1 };
}

async function canaryHash(report) {
  const mod = await import(`file://${resolve('dist/src/testkit/', `report-${report.name}.js`)}`);
  const text = mod[report.fn](canaryOptions(report));
  return createHash('sha256').update(stripTimings(text)).digest('hex').slice(0, 16);
}

// ------------------------------------------------------------------- modes

function readReport(name) {
  const report = REPORTS.find((r) => r.name === name);
  const path = `reports/${name}.txt`;
  const text = readFileSync(path, 'utf8');
  const lines = text.split(/\r?\n/);
  const stamped = PROVENANCE.exec(lines[0]);
  const bodyAt = report ? lines.findIndex((l) => l.startsWith(report.bodyStartsWith)) : -1;
  return { path, text, lines, stamped, bodyAt };
}

async function write(name) {
  const report = REPORTS.find((r) => r.name === name);
  if (!report) {
    console.error(`unknown report "${name}" — one of: ${REPORTS.map((r) => r.name).join(', ')}`);
    process.exit(1);
  }
  const { path, lines, stamped } = readReport(name);
  const bodyAt = lines.findIndex((l) => l.startsWith(report.bodyStartsWith));
  if (bodyAt < 0) {
    console.error(`${path}: no line starts with "${report.bodyStartsWith}" — cannot tell the ` +
      'hand-written preamble from the generated body, so nothing was written.');
    process.exit(1);
  }
  // The preamble is hand-written and says which realisation this is. Keep it;
  // drop only a previous provenance line.
  const preamble = lines.slice(stamped ? 1 : 0, bodyAt);

  const mod = await import(`file://${resolve('dist/src/testkit/', `report-${name}.js`)}`);
  const body = mod[report.fn](report.options);
  const source = sourceHash(report.entry);
  const canary = await canaryHash(report);

  // Hashed exactly as `check` will read it back: the body as written, with any
  // carriage returns removed. `body` here is a single string that may itself
  // contain newlines, which is why this normalises rather than joining lines.
  const bodyHash = createHash('sha256')
    .update(body.replace(/\r/g, '')).digest('hex').slice(0, 16);

  writeFileSync(path, [
    `[provenance] ${report.realisation} source=${source} canary=${canary} body=${bodyHash}`,
    ...preamble,
    body,
  ].join('\n'), 'utf8');
  console.log(
    `wrote ${path} — ${report.realisation}, source ${source}, canary ${canary}, body ${bodyHash}`,
  );
}

async function check() {
  let failures = 0;
  let unstamped = 0;
  for (const report of REPORTS) {
    const { path, stamped, lines, bodyAt } = readReport(report.name);
    if (!stamped) {
      console.log(`  ${path}  no provenance line — run \`npm run report:${report.name}\``);
      unstamped++;
      continue;
    }
    const [, realisation, source, canary, body] = stamped;

    // **The committed bytes first, before either code hash.**
    //
    // The other two answer "did the generator change?". This one answers "is
    // this file still what the generator wrote?", and it is the only question
    // that can catch a number somebody typed. It runs first because a
    // hand-edited report is stale no matter what the code is doing.
    if (!body) {
      console.log(
        `  ${path}  stamped before body hashing existed — re-stamp it with ` +
        `\`npm run report:${report.name}\``,
      );
      unstamped++;
      continue;
    }
    if (bodyAt < 0) {
      console.error(
        `  ${path}  no line starts with "${report.bodyStartsWith}", so the ` +
        'hand-written preamble cannot be told from the generated body.',
      );
      failures++;
      continue;
    }
    const liveBody = bodyHashOf(lines, bodyAt);
    if (liveBody !== body) {
      console.error(
        `  ${path}  EDITED. The committed body is not what the generator wrote ` +
        `(body ${body} -> ${liveBody}). A published number that was typed rather ` +
        'than measured is the one thing this gate exists to refuse. Regenerate ' +
        `with \`npm run report:${report.name}\`, or restore the file.`,
      );
      failures++;
      continue;
    }

    const live = sourceHash(report.entry);
    if (live === source) {
      console.log(`  ${path}  ${realisation}, source ${source} — unchanged`);
      continue;
    }
    // The cheap hash drifted. Only now is the expensive one worth running.
    const liveCanary = await canaryHash(report);
    if (liveCanary === canary) {
      console.log(
        `  ${path}  source ${source} -> ${live}, and the canary did not move ` +
        `(${canary}). The canary runs the options this gate would regenerate ` +
        'this report at, at one subject, so the change did not reach anything ' +
        'it runs — which is not the same as the report being true: a change ' +
        'that only shows at the committed population is outside it. Regenerate ' +
        `and re-stamp with \`npm run report:${report.name}\` when convenient.`,
      );
      continue;
    }
    console.error(
      `  ${path}  STALE. The code this report describes has changed and so have ` +
      `its numbers (canary ${canary} -> ${liveCanary}).`,
    );
    failures++;
  }
  if (unstamped) {
    console.log(`\n${unstamped} report(s) carry no provenance and cannot be checked.`);
  }
  if (failures) {
    console.error(
      `\n${failures} report(s) describe code that no longer exists. A committed ` +
      'instrument that reads on a tree it does not measure is worse than no ' +
      'instrument: it is quoted. Regenerate with `npm run report:<name>`.',
    );
    process.exit(1);
  }
  console.log(`reports: ${REPORTS.length - unstamped} checked, none stale`);
}

// Run only when this file IS the command, so a test can import the pure pieces
// below without the gate running (and rebuilding, and taking a minute) as a
// side effect of the import.
if (process.argv[1] && resolve(process.argv[1]).endsWith('check-reports.mjs')) {
  const [mode, name] = process.argv.slice(2);
  if (mode === '--write') await write(name);
  else await check();
}

export { stripTimings, importGraph, sourceHash, canaryOptions, REPORTS };
