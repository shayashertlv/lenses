/** report.json assembly for `arview capture` (pure; WP1_DESIGN.md §6, analysis.capture.proposed_layout).
 *
 *  The report says, in this order of authority:
 *  - `status`: 'captured' | 'captured_with_warnings' | 'refused' | 'unchecked' | 'infrastructure_failed';
 *  - `spec`: the Python validator's full report (glasses3d.g3d_host.spec, the single source of truth for the §2 rows),
 *    or `{available: false, reason}` with a loud warning when it could not run;
 *  - `runtime`: arview's own model report (warnings, fallbacks, lens identification, temples, units, clip, fit scale,
 *    rendered_sha256, its §2 rows) and where its rows disagree with spec.py;
 *  - `live_site_would_fail`: the stricter live site's rejections, the static prediction (spec.py's and arview's rules)
 *    and the runtime observation (the --live-probe verdict of main's unmodified ar/) kept apart, with a warning when
 *    they disagree (liveVerdict);
 *  - `geometry.lens_top_vs_brow`, the `images` (file, pose, background, variant, sha256), `timings`.
 *  Exit codes: 0 captured and spec.py passes; 3 captured but the spec fails or is unverified; 4 refused or unchecked by
 *  arview (nothing rendered), or refused by the capture itself (`capture_refusal`: arview rendered a model whose external
 *  resource could not be read, which §1 refuses); 1 infrastructure failure. A capture warning (a missing crop, a fit fallback, a page error,
 *  a skipped portrait), any warning, error or fallback in the model report (the posed renderer's, or the portrait
 *  renderer's, mirrored into the top-level warnings as 'model: ...' / 'portrait model: ...') makes the status
 *  'captured_with_warnings'; it never changes the exit code by itself. */

export const CAPTURE_REPORT_SCHEMA = 'arview.capture/1';
export type CaptureStatus = 'captured' | 'captured_with_warnings' | 'refused' | 'unchecked' | 'infrastructure_failed';
export const EXIT = Object.freeze({ok: 0, infrastructure: 1, usage: 2, specFailed: 3, refused: 4});
const SPEC_ROWS = ['format', 'units', 'origin', 'orientation', 'transforms', 'geometry', 'components'] as const;

/** The Python validator's outcome: its full validate_spec report, or why it is unavailable. */
export type SpecOutcome = {available: true; report: Record<string, unknown>; command: string[]; ms: number}
  | {available: false; reason: string; command: string[] | null; ms: number};

interface ModelReportLike {
  status?: string; refusal?: readonly string[]; unchecked?: readonly string[];
  spec?: Record<string, {pass: boolean; detail: readonly string[]}>; errors?: readonly string[]; warnings?: readonly string[];
  fallbacks?: readonly unknown[]; lens_identification?: unknown; measurements?: Record<string, unknown>;
  rendered_sha256?: string | null; live_site_would_fail?: readonly string[];
}

export interface CaptureReportInput {
  readonly createdAt: string;
  readonly tool: Record<string, unknown>;
  readonly input: {readonly path: string; readonly bytes: number; readonly sha256: string; readonly [key: string]: unknown};
  readonly request: Record<string, unknown>;
  readonly gpu: Record<string, unknown> | null;
  readonly spec: SpecOutcome | null;
  /** The posed renderer's model report at the end of the run (render-time fallbacks included), else the load's. */
  readonly modelReport: ModelReportLike | null;
  readonly fitting: unknown;
  readonly fitFallback: boolean;
  readonly geometry: unknown;
  readonly poses: readonly unknown[];
  readonly images: readonly {readonly file: string; readonly [key: string]: unknown}[];
  readonly plannedImages: number;
  readonly idPalette: readonly unknown[];
  readonly portrait: unknown;
  readonly liveProbe: unknown;
  readonly warnings: readonly string[];
  readonly errors: readonly string[];
  readonly infrastructureFailure: string | null;
  /** The headless Chromium process: its pid and how it ended (closed, killed and confirmed gone, or still alive). */
  readonly chromiumProcess?: unknown;
  /** Set when the capture itself refuses a model arview rendered although §1 says it must be refused (an external resource
   *  that could not be read): status 'refused', exit 4, whatever arview's own status says. */
  readonly captureRefusal?: string | null;
  readonly timings: Record<string, unknown>;
  readonly implementation: unknown;
  readonly page: Record<string, unknown>;
}

const unique = (values: readonly string[]): string[] => [...new Set(values.filter(value => typeof value === 'string' && value.length))];

/** A software rasteriser (SwiftShader, llvmpipe, WARP / Microsoft Basic Render Driver, ...): its images are not what the
 *  owner's GPU draws, so the capture refuses to report results from it. A missing renderer string cannot be verified and
 *  counts as software too. */
export function softwareRendererReason(renderer: string | null | undefined): string | null {
  if (typeof renderer !== 'string' || !renderer.trim()) return 'the WebGL renderer string is unavailable, so a hardware GPU cannot be verified';
  if (/swiftshader|llvmpipe|softpipe|lavapipe|software|basic render|microsoft basic|\bwarp\b/i.test(renderer)) {
    return `the WebGL renderer is a software rasteriser ("${renderer}")`;
  }
  return null;
}

/** A §2 row's pass flag: spec.py --full emits `{pass, detail}` objects (the shape arview's own report uses too); a bare
 *  boolean is accepted for robustness. Anything else has no verdict to compare (null). */
export function rowPass(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  if (value && typeof value === 'object' && typeof (value as {pass?: unknown}).pass === 'boolean') return (value as {pass: boolean}).pass;
  return null;
}

export interface SpecRowComparison {
  readonly disagreements: {row: string; python: boolean; arview: boolean}[];
  /** Rows both reports gave a verdict for. */
  readonly compared: string[];
  /** Rows that could not be compared, and why (never silently skipped). */
  readonly uncompared: {row: string; reason: string}[];
}

/** Compare arview's own §2 rows with spec.py's, row by row (WP1_DESIGN §1: arview must agree with spec.py). */
export function compareSpecRows(python: Record<string, unknown> | null, model: ModelReportLike | null): SpecRowComparison {
  const result: SpecRowComparison = {disagreements: [], compared: [], uncompared: []};
  const rows = python?.rows, own = model?.spec;
  if (!python || !model) return result;
  if (!rows || typeof rows !== 'object') {result.uncompared.push(...SPEC_ROWS.map(row => ({row, reason: 'spec.py report has no rows object'}))); return result;}
  if (!own) {result.uncompared.push(...SPEC_ROWS.map(row => ({row, reason: 'arview\'s model report has no §2 rows'}))); return result;}
  for (const row of SPEC_ROWS) {
    const pythonPass = rowPass((rows as Record<string, unknown>)[row]), arviewPass = rowPass(own[row]);
    if (pythonPass === null) {result.uncompared.push({row, reason: `spec.py row has no boolean pass (${JSON.stringify((rows as Record<string, unknown>)[row] ?? null)?.slice(0, 80)})`}); continue;}
    if (arviewPass === null) {result.uncompared.push({row, reason: 'arview row has no boolean pass'}); continue;}
    // A row arview failed only as "not checked" (a Format refusal before parsing, an environment limit, a units gate)
    // carries no verdict of arview's own: it is not compared, and the reason says so, never a false disagreement.
    const detail = own[row]?.detail ?? [];
    if (!arviewPass && detail.length && detail.every(text => /^not checked/i.test(String(text)))) {
      result.uncompared.push({row, reason: `arview did not check it: ${String(detail[0]).replace(/^not checked:?\s*/i, '').slice(0, 160)}`}); continue;
    }
    result.compared.push(row);
    if (pythonPass !== arviewPass) result.disagreements.push({row, python: pythonPass, arview: arviewPass});
  }
  return result;
}

/** Where arview's own §2 rows disagree with spec.py's (both reports available). */
export function specDisagreements(python: Record<string, unknown> | null, model: ModelReportLike | null): {row: string; python: boolean; arview: boolean}[] {
  return compareSpecRows(python, model).disagreements;
}

interface PortraitLike {model_report?: ModelReportLike | null}
const fallbackText = (entry: unknown): string => {
  const value = entry as {subsystem?: unknown; detail?: unknown; stage?: unknown} | null;
  return value && typeof value === 'object' ? `${String(value.detail ?? '')}` : String(entry);
};

/** The model's own warnings, errors and fallbacks (the posed renderer's, and the portrait renderer's that the posed one
 *  did not already report), as top-level capture warnings. A §4 FIT SCALE or ORIGIN warning, a §2 row failure the
 *  renderer drew anyway, or a render-time fallback seen only by the portrait must never leave the status 'captured'. */
export function modelWarnings(model: ModelReportLike | null, portrait: unknown): string[] {
  const out: string[] = [];
  if (!model || model.status !== 'rendered') return out;
  const ownWarnings = new Set(model.warnings ?? []), ownErrors = new Set(model.errors ?? []);
  for (const text of model.warnings ?? []) out.push(`model: ${text}`);
  for (const text of model.errors ?? []) out.push(`model error: ${text}`);
  for (const entry of model.fallbacks ?? []) {
    const detail = fallbackText(entry);
    if (![...ownWarnings].some(text => text.includes(detail))) out.push(`model fallback: ${detail}`);
  }
  const other = (portrait as PortraitLike | null)?.model_report;
  if (other && typeof other === 'object') {
    for (const text of other.warnings ?? []) if (!ownWarnings.has(text)) out.push(`portrait model: ${text}`);
    for (const text of other.errors ?? []) if (!ownErrors.has(text)) out.push(`portrait model error: ${text}`);
    const portraitWarnings = other.warnings ?? [];
    for (const entry of other.fallbacks ?? []) {
      const detail = fallbackText(entry);
      if (![...ownWarnings, ...portraitWarnings].some(text => text.includes(detail))) out.push(`portrait model fallback: ${detail}`);
    }
  }
  return out;
}

const PROBE_FAILS = new Set(['refused', 'failed_first_frame', 'handover_refused']);
interface ProbeLike {verdict?: string; text?: string; steps?: {step?: string; ok?: boolean; tracked_poses?: number; guarded?: boolean}[]}

/** The live-site verdict, with the static prediction and the runtime observation kept apart:
 *  - `static_would_fail` / `reasons`: the live site's acceptance rules (RULES_AUDIT §C) as spec.py and arview read them
 *    from the file;
 *  - `runtime_verdict` / `runtime_would_fail`: what main's UNMODIFIED ar/ did with the model (--live-probe): 'loaded',
 *    'refused', 'failed_first_frame', 'handover_refused', 'probe_failed' (no verdict: null), or 'not_probed' (null);
 *  - `would_fail`: true when either says so. The static rules stay in it when the runtime loaded the model, because
 *    some of them (the 8 MB, triangle and texture budgets, Studio delivery checks) are enforced outside main's runtime
 *    on this path; `contradiction` then says loudly that the runtime disagreed, and the capture lists it as a warning. */
export function liveVerdict(reasons: readonly string[], liveProbe: unknown): {would_fail: boolean; static_would_fail: boolean; reasons: string[];
  runtime_verdict: string; runtime_would_fail: boolean | null; verdicts_agree: boolean | null; contradiction: string | null; probe: unknown} {
  const probe = liveProbe && typeof liveProbe === 'object' ? liveProbe as ProbeLike : null;
  const staticFails = reasons.length > 0;
  const runtimeVerdict = probe ? String(probe.verdict ?? 'probe_failed') : 'not_probed';
  const runtimeFails = !probe ? null : PROBE_FAILS.has(runtimeVerdict) ? true : runtimeVerdict === 'loaded' ? false : null;
  const agree = runtimeFails === null ? null : runtimeFails === staticFails;
  let contradiction: string | null = null;
  if (agree === false && runtimeFails === false) {
    const frame = probe?.steps?.find(entry => entry.step === 'first_frame');
    contradiction = `LIVE VERDICTS DISAGREE: main's unmodified ar/ LOADED this model (create ok${frame ? `, ${frame.tracked_poses} poses tracked, first frame `
      + `${frame.guarded ? 'guarded' : 'unguarded'}` : ''}), but the static live-site rules predict a failure: ${reasons.join('; ')}. would_fail stays true on `
      + 'the static rules (some are enforced outside main\'s runtime); runtime_would_fail is false: main\'s runtime itself does not refuse it.';
  } else if (agree === false && runtimeFails === true) {
    contradiction = `LIVE VERDICTS DISAGREE: the static live-site rules pass this model, but main's unmodified ar/ ended '${runtimeVerdict}': `
      + `${String(probe?.text ?? '').slice(0, 400)}. would_fail is true on the runtime.`;
  }
  return {would_fail: staticFails || runtimeFails === true, static_would_fail: staticFails, reasons: [...reasons], runtime_verdict: runtimeVerdict,
    runtime_would_fail: runtimeFails, verdicts_agree: agree, contradiction, probe: liveProbe ?? 'not requested (--live-probe)'};
}

/** The status and exit code of a run. */
export function captureOutcome(input: Pick<CaptureReportInput, 'infrastructureFailure' | 'modelReport' | 'spec' | 'warnings' | 'errors' | 'images' | 'plannedImages'>
  & {readonly portrait?: unknown; readonly captureRefusal?: string | null}): {status: CaptureStatus; exitCode: number} {
  if (input.infrastructureFailure) return {status: 'infrastructure_failed', exitCode: EXIT.infrastructure};
  if (input.captureRefusal) return {status: 'refused', exitCode: EXIT.refused};
  const modelStatus = input.modelReport?.status;
  if (modelStatus === 'refused') return {status: 'refused', exitCode: EXIT.refused};
  if (modelStatus === 'unchecked') return {status: 'unchecked', exitCode: EXIT.refused};
  if (modelStatus !== 'rendered') return {status: 'infrastructure_failed', exitCode: EXIT.infrastructure};
  const specOk = input.spec?.available === true && input.spec.report.ok === true;
  // A model warning or error, or a fallback the renderer took for this model (a units rescale, a missing temple's fixed
  // cap, shadows off, a fit-scale change, ...), means the images are not the plain render either: never a clean 'captured'.
  const warned = input.warnings.length > 0 || input.errors.length > 0 || input.images.length < input.plannedImages
    || modelWarnings(input.modelReport, input.portrait ?? null).length > 0;
  return {status: warned ? 'captured_with_warnings' : 'captured', exitCode: specOk ? EXIT.ok : EXIT.specFailed};
}

/** Assemble report.json. Pure: the same input always gives the same report. */
export function assembleCaptureReport(input: CaptureReportInput): {report: Record<string, unknown>; exitCode: number} {
  const warnings = [...input.warnings];
  const spec = input.spec;
  let specBlock: Record<string, unknown>;
  if (!spec) {
    specBlock = {available: false, reason: 'VALIDATOR UNAVAILABLE: spec.py was not run (the run ended before it was started)'};
    warnings.push('VALIDATOR UNAVAILABLE: the Python spec validator was not run; the §2 verdict below is arview\'s own, not spec.py\'s.');
  } else if (!spec.available) {
    specBlock = {available: false, reason: `VALIDATOR UNAVAILABLE: ${spec.reason}`, command: spec.command, ms: spec.ms};
    warnings.push(`VALIDATOR UNAVAILABLE: ${spec.reason}. The §2 verdict in this report is arview's own runtime check, not spec.py's.`);
  } else {
    specBlock = {available: true, ok: spec.report.ok, rows: spec.report.rows, errors: spec.report.errors, warnings: spec.report.warnings,
      command: spec.command, ms: spec.ms, report: spec.report};
  }
  const model = input.modelReport;
  const comparison = spec?.available ? compareSpecRows(spec.report, model) : {disagreements: [], compared: [], uncompared: []};
  const disagreements = comparison.disagreements;
  if (disagreements.length) {
    warnings.push(`arview's runtime §2 rows disagree with spec.py on ${disagreements.map(d => `${d.row} (spec.py ${d.python ? 'pass' : 'fail'}, arview `
      + `${d.arview ? 'pass' : 'fail'})`).join(', ')}; spec.py is the authority (see runtime.spec_disagreements).`);
  }
  if (comparison.uncompared.length) {
    // One clause per reason (a Format refusal leaves six rows unchecked for one reason).
    const byReason = new Map<string, string[]>();
    for (const entry of comparison.uncompared) byReason.set(entry.reason, [...byReason.get(entry.reason) ?? [], entry.row]);
    warnings.push(`the arview-vs-spec.py §2 cross-check could not compare ${[...byReason].map(([reason, rows]) => `${rows.join(', ')} (${reason})`).join('; ')}.`);
  }
  warnings.push(...modelWarnings(model, input.portrait));
  const measurements = model?.measurements ?? {};
  const runtime = model ? {
    status: model.status, refusal: model.refusal ?? [], unchecked: model.unchecked ?? [], errors: model.errors ?? [],
    warnings: model.warnings ?? [], fallbacks: model.fallbacks ?? [], lens_identification: model.lens_identification ?? null,
    temples: measurements.temples ?? null,
    units: {scale: measurements.units_scale ?? null, authored_width_units: measurements.authored_width_units ?? null,
      bbox_mm: measurements.bbox_mm ?? null, width_mm: measurements.width_mm ?? null},
    clip: measurements.temple_clip ?? null,
    fit_scale: measurements.fit_scale ?? null,
    fitting: input.fitting ?? null, fit_fallback_to_fixed_scale: input.fitFallback,
    rendered_sha256: model.rendered_sha256 ?? null,
    rendered_sha256_matches_input: model.rendered_sha256 ? model.rendered_sha256 === input.input.sha256 : null,
    spec: model.spec ?? null, spec_disagreements: disagreements, spec_rows_compared: comparison.compared,
    spec_rows_uncompared: comparison.uncompared, measurements,
  } : null;
  const pythonLive = spec?.available && Array.isArray(spec.report.live_site_would_fail) ? spec.report.live_site_would_fail as string[] : [];
  const runtimeLive = model?.live_site_would_fail ?? [];
  const reasons = unique([...pythonLive.map(text => `spec.py: ${text}`), ...runtimeLive.map(text => `arview: ${text}`)]);
  const live = liveVerdict(reasons, input.liveProbe ?? null);
  if (live.contradiction) warnings.push(live.contradiction);
  const outcome = captureOutcome({...input, warnings: unique(warnings)});
  const report: Record<string, unknown> = {
    schema: CAPTURE_REPORT_SCHEMA, created_at: input.createdAt, status: outcome.status, exit_code: outcome.exitCode,
    infrastructure_failure: input.infrastructureFailure, capture_refusal: input.captureRefusal ?? null, chromium_process: input.chromiumProcess ?? null,
    tool: input.tool, gpu: input.gpu, input: input.input, request: input.request,
    spec: specBlock, runtime, live_site_would_fail: live,
    geometry: {lens_top_vs_brow: input.geometry ?? null},
    poses: input.poses, images: input.images, planned_images: input.plannedImages, id_palette: input.idPalette,
    portrait: input.portrait ?? null,
    warnings: unique(warnings), errors: unique([...input.errors]),
    timings: input.timings, implementation: input.implementation, page: input.page,
    notes: ['Images are unmirrored; the live webcam page shows a mirror image (src/style.css #mirror scaleX(-1)).',
      'part-id and normals renders are valid for the front and the junction: their temples are cut hard at the renderer\'s clip, '
        + 'where the beauty render fades over the last 5 mm.',
      'The posed captures use synthetic landmarks of the canonical face on flat backgrounds (no face image); the face and head '
        + 'depth occluders still hide what the face would hide. Optional worn +/-50 views shade those same occluder meshes in plain '
        + 'skin, with hair off. The proxy has no anatomical ears; the crop extends past the fixed buried return. AR spreads, returns '
        + 'and clips the arms: use photo comparison and photo_read for product temple length and shape.',
      'Turned part-id renders (|yaw| >= 20) carry temple_reach for the near arm: how far back it is drawn (drawn_to_z_mm) against '
        + 'where its fixed return behind the ear begins (return_start_z_mm); an arm hidden more than 5 mm ahead of that, at its end '
        + '(hidden_before_return_mm) or in a stretch drawn again behind (longest_hidden_run_mm), is a TEMPLE CUT warning (the drawn '
        + 'arm lies inside the head proxy there), never a silent cut. Behind the return start nothing is judged: a drawn_to_z_mm '
        + 'there can be the curled return projected onto a nearer part of the same arm (behind_return_drawn_stations counts them).'],
  };
  return {report, exitCode: outcome.exitCode};
}
