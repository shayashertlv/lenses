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
 * not work either — all six of them (keyframes 24, rounds 3, fieldPriorScale 8,
 * TYPICAL_VARIANCE_FACTOR 1.9, SIDEWALL_BAND_MM, VERTEX_SEAT_SIGMA_MM) still
 * hold the values seat.txt declares, and seat.txt is stale anyway. A gate built
 * on them would have been green through the whole drift, which by this tree's
 * own rule makes it a bug rather than a check.
 *
 * So two:
 *
 *  - **`source`** — sha-256 over the report generator's transitive local import
 *    graph, each file transpiled with `removeComments: true`, plus the template
 *    mesh the fixtures load. Comments in this tree carry the measurements and
 *    change constantly; the code under them does not. Cheap: a fifth of a
 *    second, so it runs on every `npm test`.
 *  - **`canary`** — sha-256 of the report's own generator run at `subjects: 1`,
 *    with timings stripped. Exact, and expensive (3 to 31 seconds), so it runs
 *    only when `source` has already drifted.
 *
 * The two together give the honest answer without the cost. A drifted `source`
 * with a matching `canary` means the code moved and the numbers did not — the
 * report is still true, and it says so rather than failing. A drifted `canary`
 * means the committed numbers describe code that no longer exists, and that
 * fails the build.
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
  // its committed copy IS that campaign, so it is regenerated without a seed.
  // Its CANARY is still a single seed, because a canary is a fingerprint of the
  // code and not a replication of the result.
  { name: 'occlusion', entry: 'src/testkit/report-occlusion.ts', fn: 'runOcclusionReport', bodyStartsWith: 'OCCLUSION — ', realisation: 'campaign', options: {} },
];

/** The seed the canary is taken at, for every report. */
const SEED = 11;

/** The one file the fixtures read off disk. Every number depends on it. */
const TEMPLATE = 'assets/face/canonical_face_model.obj';

/**
 * The stamp, and what each of its three hashes is for.
 *
 *   source  the generator's transitive import graph, comments stripped. Cheap,
 *           runs every time, and answers "did the code that produced this
 *           report change?"
 *   canary  a one-subject run of the generator itself. Expensive, runs only
 *           when `source` has drifted, and answers "did the change move the
 *           numbers, or was it a comment?"
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
  const lines = text
    .replace(/Wall clock:.*$/gm, 'Wall clock: -')
    .replace(/\b\d+(\.\d+)?\s*ms\b/g, '- ms')
    .split('\n');

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
  let span = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^-{3,}(\s+-{3,})+\s*$/.test(line)) {
      span = null;
      for (const m of line.matchAll(/-{3,}/g)) {
        const cell = (lines[i - 1] ?? '').slice(m.index, m.index + m[0].length).trim();
        if (cell === 'ms') span = [m.index, m.index + m[0].length];
      }
      continue;
    }
    if (line.trim() === '') { span = null; continue; }
    if (span) {
      lines[i] = line.slice(0, span[0])
        + '-'.repeat(Math.min(span[1], line.length) - Math.min(span[0], line.length))
        + line.slice(span[1]);
    }
  }
  return lines.join('\n');
}

async function canaryHash(report) {
  const mod = await import(`file://${resolve('dist/src/testkit/', `report-${report.name}.js`)}`);
  const text = mod[report.fn]({ seed: SEED, subjects: 1 });
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
        `  ${path}  source ${source} -> ${live}, but the numbers did not move ` +
        `(canary ${canary}). The report is still true; re-stamp it with ` +
        `\`npm run report:${report.name}\` when convenient.`,
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

export { stripTimings, importGraph, sourceHash, REPORTS };
