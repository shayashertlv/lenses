/** The report `arview serve` hands the webcam page at /__arview/report.json (pure; WP1_DESIGN.md §6). Schema
 *  SERVED_REPORT_SCHEMA; the page reads it through src/model/served-report.ts.
 *
 *  It says, in this order of authority:
 *  - `rows` / `failed_rows`: the seven §2 rows, spec.py's verdict and detail (glasses3d.g3d_host.spec --full, the single
 *    source of truth), arview's own row beside it, and whether they agree (compareSpecRows: a row arview failed only as
 *    "not checked" is uncompared, never a false disagreement). Without spec.py the rows are arview's, with a loud
 *    VALIDATOR UNAVAILABLE warning. A failed row's `detail` is its failures; its notes ('GLB 2.0, N bytes', spec.py's
 *    "unverified: ..." lines) are in `notes`, never shown as failures;
 *  - `status`, `refusal`, `unchecked`, `fallbacks`, `lens_identification`, `measurements`: arview's load front-end and
 *    normalisation pass run in Node on the very bytes served (src/model/check.ts; textures are not decoded there, so a
 *    texture the browser cannot decode shows only in the renderer's report after Open camera), plus the temple clip the
 *    renderer will derive (qa/temple-cutoff.mjs) and the fallbacks it will report from the temples alone (a missing
 *    temple's fixed cap, the default clip, continuity off: temple-geometry.ts templeLoadFallbacks, word for word);
 *  - `served_sha256`: the SHA-256 of the bytes the server hands the page. It is NOT a rendered digest: nothing has been
 *    drawn when this report is made; the renderer re-hashes what it parses (its own report's rendered_sha256, which the
 *    WP2 ledger binds verdicts to);
 *  - `errors`: problems of the server and of the checks themselves (a check that crashed or timed out, digests that
 *    disagree, the file changing on disk); `node_check_errors` names those that only the Node-side check produced;
 *  - `serve_warnings`: the server's own warnings; `node_check_warnings`: what the Node-side check said beyond its model
 *    report (its cross-check against spec.py, the temple clip receipt); `spec_warnings`: spec.py's own warnings, all of
 *    them; `warnings`: all of those plus every model warning (each fallback's detail is a warning too), spec.py's as
 *    'spec.py: ...' unless arview words one identically, each once. Nothing is dropped silently. */
import {compareSpecRows} from '../capture/report.ts';
import type {SpecOutcome} from '../capture/report.ts';
import {SERVED_REPORT_SCHEMA, specPyFailures} from '../../src/model/served-report.ts';

export {SERVED_REPORT_SCHEMA};
const SPEC_ROWS = ['format', 'units', 'origin', 'orientation', 'transforms', 'geometry', 'components'] as const;
type SpecRowName = typeof SPEC_ROWS[number];
const ROW_TITLE: Readonly<Record<SpecRowName, string>> = Object.freeze({format: 'Format', units: 'Units', origin: 'Origin',
  orientation: 'Orientation', transforms: 'Transforms', geometry: 'Geometry', components: 'Components'});

interface Row {pass: boolean; detail: string[]}
interface ModelReportLike {
  status?: string; refusal?: readonly string[]; unchecked?: readonly string[];
  spec?: Record<string, {pass: boolean; detail: readonly string[]}>; row_notes?: Partial<Record<string, readonly string[]>>;
  errors?: readonly string[]; warnings?: readonly string[];
  fallbacks?: readonly {subsystem: string; detail: string; stage?: string}[]; lens_identification?: unknown; measurements?: Record<string, unknown>;
  rendered_sha256?: string | null; live_site_would_fail?: readonly string[];
}

export interface ServeReportInput {
  readonly createdAt: string;
  readonly model: {readonly path: string; readonly file: string; readonly bytes: number; readonly sha256: string; readonly url: string;
    readonly selfContained: boolean; readonly externalUris: readonly string[]; readonly servedResources: readonly string[];
    readonly unreadableResources: readonly string[];
    /** Readable, but at a URL that is one of the server's own paths: not served (an error in `errors`). */
    readonly unservableResources?: readonly string[];
    /** Served, but outside the GLB's folder (a warning). */
    readonly outsideResources?: readonly string[];
    readonly containerError: string | null};
  readonly pageUrl: string;
  readonly reportUrl: string;
  readonly handover: Record<string, unknown>;
  readonly spec: SpecOutcome | null;
  /** arview's Node-side model report, or null with `arviewError` saying why there is none. */
  readonly arview: ModelReportLike | null;
  readonly arviewError: string | null;
  /** The temple clip receipt (qa/temple-cutoff.mjs selectQATempleCutoff), or null. */
  readonly templeClip: Record<string, unknown> | null;
  /** A problem of the Node-side check that did not stop its report (the temple clip receipt failed). */
  readonly nodeCheckWarnings?: readonly string[];
  readonly serveWarnings: readonly string[];
  /** Problems of the server itself (a missing public binary, the file changing while the server started). */
  readonly serveErrors?: readonly string[];
  readonly server: Record<string, unknown>;
  readonly build: Record<string, unknown>;
  readonly publicFiles: Record<string, unknown>;
}

const unique = (values: readonly string[]): string[] => [...new Set(values.filter(value => typeof value === 'string' && value.length > 0))];
function asRow(value: unknown): Row | null {
  if (!value || typeof value !== 'object' || typeof (value as {pass?: unknown}).pass !== 'boolean') return null;
  const detail = (value as {detail?: unknown}).detail;
  return {pass: (value as {pass: boolean}).pass, detail: Array.isArray(detail) ? detail.map(String) : []};
}

export function assembleServeReport(input: ServeReportInput): Record<string, unknown> {
  const errors: string[] = [...input.serveErrors ?? []], nodeCheckErrors: string[] = [];
  const serveWarnings: string[] = [...input.serveWarnings], nodeCheckWarnings: string[] = [...input.nodeCheckWarnings ?? []];
  const spec = input.spec, python = spec?.available ? spec.report : null, model = input.arview;
  let specBlock: Record<string, unknown>;
  if (!spec) {
    specBlock = {available: false, reason: 'spec.py was not run'};
    serveWarnings.unshift('VALIDATOR UNAVAILABLE: spec.py was not run; the §2 rows below are arview\'s own check, not spec.py\'s.');
  } else if (!spec.available) {
    specBlock = {available: false, reason: spec.reason, command: spec.command, ms: spec.ms};
    serveWarnings.unshift(`VALIDATOR UNAVAILABLE: ${spec.reason}. The §2 rows below are arview's own check, not spec.py's.`);
  } else {
    specBlock = {available: true, ok: spec.report.ok, rows: spec.report.rows, errors: spec.report.errors, warnings: spec.report.warnings,
      live_site_would_fail: spec.report.live_site_would_fail, command: spec.command, ms: spec.ms, report: spec.report};
    const pythonSha = (spec.report as {sha256?: unknown}).sha256;
    if (typeof pythonSha === 'string' && pythonSha !== input.model.sha256) {
      errors.push(`spec.py checked bytes with sha256 ${pythonSha}, not the served ${input.model.sha256}: the file changed while the server started; restart it.`);
    }
  }
  if (input.arviewError) nodeCheckErrors.push(`arview's check of the model did not complete: ${input.arviewError}`);
  if (model?.rendered_sha256 && model.rendered_sha256 !== input.model.sha256) {
    nodeCheckErrors.push(`arview's check parsed bytes with sha256 ${model.rendered_sha256}, not the served ${input.model.sha256}.`);
  }
  errors.push(...nodeCheckErrors);

  const comparison = python ? compareSpecRows(python, model) : {disagreements: [], compared: [], uncompared: []};
  const rows = SPEC_ROWS.map(row => {
    const pythonRow = python ? asRow((python.rows as Record<string, unknown> | undefined)?.[row]) : null;
    const ownRow = asRow(model?.spec?.[row]);
    const compared = comparison.compared.includes(row);
    const agree = compared ? !comparison.disagreements.some(entry => entry.row === row) : null;
    const source = pythonRow ? 'spec.py' : ownRow ? 'arview' : 'none';
    return {row, title: ROW_TITLE[row], pass: pythonRow ? pythonRow.pass : ownRow ? ownRow.pass : null, source, spec_py: pythonRow, arview: ownRow, agree};
  });
  const failedRows = rows.filter(entry => entry.pass === false).map(entry => {
    const ownNotes = [...model?.row_notes?.[entry.row] ?? []];
    const row = entry.source === 'spec.py' ? entry.spec_py! : entry.arview!;
    const detail = entry.source === 'spec.py' ? specPyFailures(row, ownNotes) : row.detail.filter(text => !ownNotes.includes(text));
    return {row: entry.row, title: entry.title, source: entry.source === 'spec.py' ? 'spec.py' : 'arview, spec.py unavailable', detail,
      notes: row.detail.filter(text => !detail.includes(text))};
  });
  if (comparison.disagreements.length) {
    nodeCheckWarnings.push(`arview's own §2 rows disagree with spec.py on ${comparison.disagreements.map(d => `${d.row} (spec.py ${d.python ? 'pass' : 'fail'}, arview `
      + `${d.arview ? 'pass' : 'fail'})`).join(', ')}; spec.py is the authority.`);
  }
  if (comparison.uncompared.length && python && model) {
    const byReason = new Map<string, string[]>();
    for (const entry of comparison.uncompared) byReason.set(entry.reason, [...byReason.get(entry.reason) ?? [], entry.row]);
    nodeCheckWarnings.push(`the arview-vs-spec.py §2 cross-check could not compare ${[...byReason].map(([reason, names]) => `${names.join(', ')} (${reason})`).join('; ')}.`);
  }
  for (const text of (input.templeClip?.warnings as unknown[] | undefined) ?? []) nodeCheckWarnings.push(`temple clip: ${String(text)}`);

  const modelWarnings = [...model?.warnings ?? []];
  const specWarnings = python && Array.isArray(python.warnings) ? unique((python.warnings as unknown[]).map(String)) : [];
  const pythonLive = python && Array.isArray(python.live_site_would_fail) ? (python.live_site_would_fail as unknown[]).map(String) : [];
  const liveReasons = unique([...pythonLive.map(text => `spec.py: ${text}`), ...(model?.live_site_would_fail ?? []).map(text => `arview: ${text}`)]);
  const status = model?.status ?? 'not_checked';
  return {
    schema: SERVED_REPORT_SCHEMA, created_at: input.createdAt,
    model: {path: input.model.path, file: input.model.file, bytes: input.model.bytes, sha256: input.model.sha256, url: input.model.url,
      self_contained: input.model.selfContained, external_uris: [...input.model.externalUris], served_resources: [...input.model.servedResources],
      unreadable_resources: [...input.model.unreadableResources], unservable_resources: [...input.model.unservableResources ?? []],
      outside_resources: [...input.model.outsideResources ?? []], container_error: input.model.containerError},
    page_url: input.pageUrl, report_url: input.reportUrl, handover: input.handover,
    served_sha256: input.model.sha256,
    status, verdict: {spec_ok: python ? python.ok === true : null, authority: python ? 'spec.py' : 'arview, spec.py unavailable',
      arview_status: status},
    refusal: [...model?.refusal ?? []], unchecked: [...model?.unchecked ?? []],
    rows, failed_rows: failedRows,
    spec_rows: {compared: comparison.compared, uncompared: comparison.uncompared, disagreements: comparison.disagreements},
    errors: unique(errors), node_check_errors: unique(nodeCheckErrors),
    serve_warnings: unique(serveWarnings), node_check_warnings: unique(nodeCheckWarnings), spec_warnings: specWarnings,
    warnings: unique([...serveWarnings, ...nodeCheckWarnings, ...modelWarnings,
      ...specWarnings.filter(text => !modelWarnings.includes(text)).map(text => `spec.py: ${text}`)]),
    fallbacks: (model?.fallbacks ?? []).map(entry => ({subsystem: entry.subsystem, detail: entry.detail, stage: entry.stage ?? 'load'})),
    spec: specBlock,
    arview: model ?? {error: input.arviewError ?? 'not run'},
    lens_identification: model?.lens_identification ?? null,
    measurements: model?.measurements ?? {},
    temple_clip: input.templeClip,
    live_site_would_fail: {would_fail: liveReasons.length > 0, reasons: liveReasons, probe: 'static (spec.py and arview rules; arview capture --live-probe runs main\'s ar/)'},
    server: input.server, build: input.build, public_files: input.publicFiles,
    notes: ['Before Open camera the page lists this report: spec.py, and arview\'s load and normalisation run in Node on the served bytes '
        + '(textures not decoded), with the fallbacks the renderer reports from the temples alone (a missing temple\'s fixed cap, the default '
        + 'clip, continuity off). After Open camera it lists the renderer\'s own report, which adds what only drawing finds (texture decoding, '
        + 'shadows, hair, continuity), with spec.py\'s verdict and warnings kept beside it; what only the Node-side check said is then dropped.',
      'The page shows a mirror image (src/style.css #mirror scaleX(-1)); captures are unmirrored.',
      'The model is served with its sha256 (X-Arview-Sha256, Repr-Digest; served_sha256 here); the page compares it with the bytes the renderer '
        + 'parses, and verdicts bind to the rendered sha256.',
      'The server re-hashes the file on disk whenever the page, the model or this report is fetched: a file re-exported to the same path is an '
        + 'error here (restart arview serve to judge it).',
      'Nothing here blocks a render except a §2 refusal (WP1_DESIGN.md §1).'],
  };
}
