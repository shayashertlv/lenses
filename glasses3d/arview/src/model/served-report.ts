/** The webcam page's view of the model report (glasses3d/plan/archive/20261019/WP1_DESIGN.md §6): what `arview serve` checked before the page opened
 *  (`?report=`, the server's `/__arview/report.json`, schema SERVED_REPORT_SCHEMA, assembled by qa/serve/report.ts), and,
 *  once the camera is open, the renderer's own report (ModelReport) with spec.py's verdict kept beside it.
 *
 *  The page lists, loudest first: problems of the server itself, §2 refusal reasons, every failed §2 row (spec.py's
 *  detail, the authority; arview's own beside it only where it says something else, or alone when spec.py could not
 *  run), where arview and spec.py disagree, the warnings (spec.py's own among them, always), then every fallback
 *  labelled with its subsystem. A fallback is also a warning in the reports; it is listed once, as a fallback. A failed
 *  row's notes ('GLB 2.0, N bytes') are context, never listed as errors. What only the server's Node-side check said
 *  (its cross-check against spec.py, its temple clip receipt, its own failures) is replaced by the renderer's report once
 *  the camera opens. The sha256 line says which bytes the verdicts bind to (the WP2 ledger binds to the RENDERED
 *  sha256; the server's report names the bytes it serves `served_sha256`). Pure: no DOM, so tests run it in Node. */
import {failureDetail, SPEC_ROWS} from './report.ts';
import type {ModelFallback, ModelReport, ReportLine, SpecRow, SpecRowName} from './report.ts';

export const SERVED_REPORT_SCHEMA = 'arview.serve/1';

const ROW_TITLE: Readonly<Record<SpecRowName, string>> = Object.freeze({format: 'Format', units: 'Units', origin: 'Origin',
  orientation: 'Orientation', transforms: 'Transforms', geometry: 'Geometry', components: 'Components'});

/** spec.py's Report.note lines (glasses3d/g3d_host/spec.py): detail that does not fail the row. `GLB 2.0, N bytes`
 *  (_format), `width W units = M mm (metres)` (_units), and `unverified: ...` (fail_unless and the compressed-geometry
 *  downgrades: a measured check that undecoded compressed geometry could change). spec.py does not mark them in its
 *  JSON, so they are recognised by wording (qa/serve-report.test.mjs counts spec.py's note sites, so a new one is
 *  noticed). */
export const SPEC_PY_NOTE_PATTERNS: readonly RegExp[] = Object.freeze([/^GLB 2\.0, \d+ bytes$/, /^width -?[\d.]+ units = -?[\d.]+ mm \(metres\)$/, /^unverified[: ]/]);
/** Whether a line of spec.py's row detail is a note, not a failure (`arviewNotes`: arview's notes on that row, which
 *  are worded as spec.py's). */
export function isSpecPyNote(detail: string, arviewNotes: readonly string[] = []): boolean {
  return arviewNotes.includes(detail) || SPEC_PY_NOTE_PATTERNS.some(pattern => pattern.test(detail));
}
/** spec.py's failure detail for a row (its notes left out). */
export function specPyFailures(row: SpecRow, arviewNotes: readonly string[] = []): string[] {
  return row.detail.filter(detail => !isSpecPyNote(detail, arviewNotes));
}

/** One §2 row as the server decided it: spec.py's verdict when it ran (the authority), else arview's own. */
export interface ServedRow {
  readonly row: SpecRowName;
  readonly title: string;
  readonly pass: boolean | null;
  readonly source: 'spec.py' | 'arview' | 'none';
  readonly spec_py: SpecRow | null;
  readonly arview: SpecRow | null;
  /** Whether arview's own row agrees with spec.py's; null when either has no verdict. */
  readonly agree: boolean | null;
}

/** The part of the server's report the page reads. */
export interface ServedReport {
  readonly schema: typeof SERVED_REPORT_SCHEMA;
  readonly created_at: string;
  readonly model: {readonly url: string; readonly file: string; readonly bytes: number; readonly sha256: string};
  /** SHA-256 of the exact bytes the server hands the page. Not a rendered digest: the renderer re-hashes what it parses
   *  (ModelReport.rendered_sha256), and the WP2 ledger binds to that. */
  readonly served_sha256: string;
  /** arview's status for the file, from the server's Node-side check: 'rendered', 'refused', 'unchecked', or
   *  'not_checked' (the check itself failed or timed out; see errors). */
  readonly status: string;
  readonly refusal: readonly string[];
  readonly unchecked: readonly string[];
  readonly rows: readonly ServedRow[];
  /** Each failed row: `detail` its failures (spec.py's when it ran), `notes` the row's notes (context, not failures). */
  readonly failed_rows: readonly {readonly row: SpecRowName; readonly title: string; readonly source: string; readonly detail: readonly string[]; readonly notes: readonly string[]}[];
  readonly spec: {readonly available: boolean; readonly ok?: boolean; readonly reason?: string};
  /** Problems of the server and of its checks (a validator crash, a digest mismatch, the file changing on disk): listed
   *  first, as errors. */
  readonly errors: readonly string[];
  /** The errors that only the Node-side check produced (it did not complete, it parsed other bytes): superseded by the
   *  renderer's own report once the camera opens. */
  readonly node_check_errors: readonly string[];
  /** The server's own warnings (validator unavailable, external resources). */
  readonly serve_warnings: readonly string[];
  /** What the server's Node-side check said beyond its model report (its §2 cross-check against spec.py, its temple
   *  clip receipt): superseded by the renderer's report once the camera opens. */
  readonly node_check_warnings: readonly string[];
  /** spec.py's own warnings, unprefixed and all of them (the page lists each as 'spec.py: ...' unless arview's report
   *  words it identically). */
  readonly spec_warnings: readonly string[];
  /** All of the above warnings plus every model warning of the Node-side check, each once. */
  readonly warnings: readonly string[];
  readonly fallbacks: readonly ModelFallback[];
  readonly notes: readonly string[];
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const strings = (value: unknown): value is readonly string[] => Array.isArray(value) && value.every(item => typeof item === 'string');

/** Check the shape of a fetched report. Returns the report, or throws with what is wrong (the page lists it as an error;
 *  a malformed report is never shown as if it were clean). */
export function parseServedReport(json: unknown): ServedReport {
  if (!isRecord(json)) throw new Error('the report is not a JSON object');
  if (json.schema !== SERVED_REPORT_SCHEMA) throw new Error(`the report schema is ${JSON.stringify(json.schema)}, not "${SERVED_REPORT_SCHEMA}"`);
  const model = json.model;
  if (!isRecord(model) || typeof model.url !== 'string' || typeof model.sha256 !== 'string' || typeof model.file !== 'string' || typeof model.bytes !== 'number') {
    throw new Error('the report has no model {url, file, bytes, sha256}');
  }
  if (typeof json.served_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(json.served_sha256)) throw new Error('the report has no 64-hex served_sha256');
  if (typeof json.status !== 'string') throw new Error('the report has no status');
  for (const key of ['refusal', 'unchecked', 'errors', 'node_check_errors', 'serve_warnings', 'node_check_warnings', 'spec_warnings', 'warnings', 'notes'] as const) {
    if (!strings(json[key])) throw new Error(`the report's ${key} is not a list of strings`);
  }
  if (!Array.isArray(json.rows) || !Array.isArray(json.failed_rows) || !Array.isArray(json.fallbacks)) throw new Error('the report has no rows, failed_rows or fallbacks list');
  for (const entry of json.failed_rows as unknown[]) {
    if (!isRecord(entry) || typeof entry.title !== 'string' || !strings(entry.detail) || !strings(entry.notes)) throw new Error('a failed row has no title, detail or notes list');
  }
  for (const entry of json.fallbacks as unknown[]) {
    if (!isRecord(entry) || typeof entry.subsystem !== 'string' || typeof entry.detail !== 'string') throw new Error('a fallback has no subsystem or detail');
  }
  if (!isRecord(json.spec) || typeof json.spec.available !== 'boolean') throw new Error('the report has no spec {available}');
  return json as unknown as ServedReport;
}

const fallbackLine = (entry: ModelFallback): ReportLine => ({kind: 'fallback', text: `Fallback · ${entry.subsystem}: ${entry.detail}`});

/** Warnings, then the fallbacks: a warning that is a fallback's detail is listed once, as the fallback. */
function warningAndFallbackLines(warnings: readonly string[], fallbacks: readonly ModelFallback[], skip: ReadonlySet<string>): ReportLine[] {
  const details = new Set(fallbacks.map(entry => entry.detail));
  const lines: ReportLine[] = [];
  for (const text of new Set(warnings)) if (!details.has(text) && !skip.has(text)) lines.push({kind: 'warning', text});
  for (const entry of fallbacks) lines.push(fallbackLine(entry));
  return lines;
}

/** What the page lists before the camera opens: the server's report. */
export function servedReportLines(served: ServedReport): ReportLine[] {
  const lines: ReportLine[] = served.errors.map(text => ({kind: 'error', text}));
  const refused = new Set(served.refusal);
  for (const text of served.refusal) lines.push({kind: 'refusal', text});
  const leading = served.warnings.filter(text => text.startsWith('NOT CHECKED HERE: ') || text.startsWith('VALIDATOR UNAVAILABLE'));
  for (const text of leading) lines.push({kind: 'warning', text});
  for (const row of served.failed_rows) {
    for (const detail of row.detail) {
      if (refused.has(`§2 ${row.title}: ${detail}`)) continue;
      lines.push({kind: 'error', text: `${row.title}: ${detail}${row.source === 'spec.py' ? '' : ` (${row.source})`}`});
    }
  }
  lines.push(...warningAndFallbackLines(served.warnings, served.fallbacks, new Set(leading)));
  return lines;
}

/** What the page lists once the renderer has loaded (or refused) the model: the renderer's own report, with the
 *  server's spec.py verdict beside it, row by row:
 *  - spec.py fails the row: spec.py's failures (unsuffixed when the renderer fails it too; "(spec.py; arview's renderer
 *    passes it)" when it does not), then any failure of the renderer's that spec.py does not state, as "(arview's
 *    renderer)". A row the renderer could not check ("not checked", after a refusal elsewhere) shows spec.py's failure.
 *  - spec.py passes the row and the renderer fails it: the renderer's failures as "(arview's renderer; spec.py passes
 *    it)", and a disagreement warning, which says when spec.py's pass is only "unverified" (compressed geometry it does
 *    not decode). No disagreement line when the renderer refused the model: the refusal says it.
 *  - without spec.py: the renderer's own failures, and VALIDATOR UNAVAILABLE.
 *  Then the warnings: the renderer's, the server's own, and spec.py's (each 'spec.py: ...', unless the renderer words it
 *  identically), then the fallbacks. The server's own errors stay; what only its Node-side check said is dropped (the
 *  renderer has now done that check itself). */
export function renderedReportLines(model: ModelReport, served: ServedReport | null): ReportLine[] {
  const lines: ReportLine[] = [];
  if (served) for (const text of served.errors) if (!served.node_check_errors.includes(text)) lines.push({kind: 'error', text});
  const refused = new Set(model.refusal);
  for (const text of model.refusal) lines.push({kind: 'refusal', text});
  const leading = model.warnings.filter(text => text.startsWith('NOT CHECKED HERE: '));
  for (const text of leading) lines.push({kind: 'warning', text});
  const specRows = served?.spec.available ? new Map(served.rows.map(entry => [entry.row, entry.spec_py])) : null;
  const disagreements: ReportLine[] = [];
  for (const row of SPEC_ROWS) {
    const title = ROW_TITLE[row], own = model.spec[row], notes = model.row_notes?.[row] ?? [];
    const allFailures = own.pass ? [] : failureDetail(model, row);
    const ownFailures = allFailures.filter(detail => !refused.has(`§2 ${title}: ${detail}`));
    // A row the renderer failed only as "not checked" (a refusal elsewhere ended the load) carries no verdict of its own.
    const ownUnchecked = allFailures.length > 0 && allFailures.every(text => /^not checked/i.test(text));
    const python = specRows?.get(row) ?? null;
    if (!python) {
      for (const detail of ownFailures) lines.push({kind: 'error', text: `${title}: ${detail}`});
      continue;
    }
    if (!python.pass) {
      const specFailures = specPyFailures(python, notes);
      const suffix = own.pass ? ' (spec.py; arview\'s renderer passes it)' : '';
      for (const detail of specFailures) if (!refused.has(`§2 ${title}: ${detail}`)) lines.push({kind: 'error', text: `${title}${suffix}: ${detail}`});
      if (!own.pass && !ownUnchecked) {
        for (const detail of ownFailures) if (!specFailures.includes(detail)) lines.push({kind: 'error', text: `${title} (arview's renderer): ${detail}`});
      }
      continue;
    }
    // spec.py passes the row: the renderer's failure is arview's alone.
    if (own.pass || ownUnchecked) continue;
    for (const detail of ownFailures) lines.push({kind: 'error', text: `${title} (arview's renderer; spec.py passes it): ${detail}`});
    if (model.status === 'refused') continue;
    const unverified = python.detail.filter(detail => /^unverified[: ]/.test(detail));
    disagreements.push({kind: 'warning', text: unverified.length
      ? `arview's renderer fails the ${title} row; spec.py passes it only as unverified (${unverified.join('; ')}): spec.py does not decode `
        + 'compressed geometry, so its pass is no verdict on this row.'
      : `arview's renderer fails the ${title} row but spec.py passes it; spec.py is the authority for the §2 rows.`});
  }
  if (served && !served.spec.available) lines.push({kind: 'warning', text: `VALIDATOR UNAVAILABLE: ${served.spec.reason ?? 'spec.py did not run'}; the §2 rows above are arview's own check, not spec.py's.`});
  lines.push(...disagreements);
  const modelWarnings = model.warnings.filter(text => !leading.includes(text));
  const serveWarnings = served ? served.serve_warnings.filter(text => !text.startsWith('VALIDATOR UNAVAILABLE')) : [];
  const specWarnings = served ? served.spec_warnings.filter(text => !modelWarnings.includes(text)).map(text => `spec.py: ${text}`) : [];
  lines.push(...warningAndFallbackLines([...modelWarnings, ...serveWarnings, ...specWarnings], model.fallbacks, new Set()));
  return lines;
}

/** The sha256 line: the served bytes before the camera opens, the RENDERED bytes after (the WP2 ledger binds verdicts to
 *  the rendered sha256), and a loud mismatch when they differ. Null when there is nothing to say. */
export function digestLine(served: ServedReport | null, rendered: string | null): {readonly kind: 'ok' | 'pending' | 'mismatch'; readonly text: string; readonly sha256: string} | null {
  if (rendered) {
    if (served && served.served_sha256 !== rendered) {
      return {kind: 'mismatch', sha256: rendered, text: `Rendered sha256 ${rendered} DIFFERS from the served file's ${served.served_sha256}: verdicts bind to the rendered bytes.`};
    }
    return {kind: 'ok', sha256: rendered, text: `Rendered sha256 ${rendered}${served ? ' (the served file)' : ''}. Verdicts bind to these bytes.`};
  }
  if (served) return {kind: 'pending', sha256: served.served_sha256, text: `Served sha256 ${served.served_sha256}. The renderer re-hashes the bytes it parses when the camera opens.`};
  return null;
}
