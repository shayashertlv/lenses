/** The model report (WP1_DESIGN.md §1, §5): what arview did with one GLB, and what violates the owner's spec (PLAN §2).
 *
 *  - `spec` holds the seven §2 rows (format, units, origin, orientation, transforms, geometry, components), each
 *    {pass, detail[]}, worded and decided as `glasses3d/g3d_host/spec.py` decides them (the single source of truth;
 *    arview's runtime checks must agree with it on shared fixtures).
 *  - A §2 refusal (the §1 table: unreadable file, no scene, missing resource, non-finite positions, bad indices, no
 *    triangles, skinned/morph/instanced meshes, non-identity transforms, no lens) ends the load: `status` is
 *    'refused' and `refusal` lists every reason. The load still checks every row it can first, so the report is whole.
 *  - Everything else renders. Every fallback is a `fallbacks[]` entry AND a `warnings[]` sentence, never silent.
 *  - `errors` lists every failed row's detail ('<row>: <detail>', spec.py's wording). Most row failures write no warning,
 *    so a display shows both (reportLines: refusal reasons, failed rows, then warnings), never the warnings alone.
 *  - `live_site_would_fail` lists what the stricter live site (RULES_AUDIT §C) would reject; it never blocks a render.
 *  - `rendered_sha256` is the SHA-256 of the exact GLB bytes parsed: the WP2 ledger binds to it, never to a handover
 *    value. */
import type {LensIdentificationReport} from '../eyewear/lens-identification.ts';

export const MODEL_REPORT_SCHEMA = 'arview.model-report/1';
export const SPEC_ROWS = Object.freeze(['format', 'units', 'origin', 'orientation', 'transforms', 'geometry', 'components'] as const);
export type SpecRowName = typeof SPEC_ROWS[number];
export interface SpecRow {readonly pass: boolean; readonly detail: readonly string[];}
/** One fallback: the subsystem that fell back and what it does instead. `stage` says whether it happened while loading
 *  or later (a render-time fallback such as a twin that could not be built, or a failed audit). */
export interface ModelFallback {readonly subsystem: string; readonly detail: string; readonly stage: 'load' | 'render';}
export type ModelStatus = 'rendered' | 'refused';

export interface ModelReport {
  readonly schema: typeof MODEL_REPORT_SCHEMA;
  readonly url: string;
  readonly displayName: string;
  readonly status: ModelStatus;
  /** The §2 refusal reasons ('§2 <Row>: ...'); empty when the model rendered. */
  readonly refusal: readonly string[];
  readonly spec: Readonly<Record<SpecRowName, SpecRow>>;
  /** Every failed row's detail as '<row>: <detail>', worded as spec.py's `errors` (a failed row lists all its detail).
   *  Many failures (Orientation, implausible Units, Components, texture references) write no warning of their own:
   *  whoever shows the report shows these too (reportLines). */
  readonly errors: readonly string[];
  /** Every warning, in the order the load met them (the model definition's own first), each once. */
  readonly warnings: readonly string[];
  readonly fallbacks: readonly ModelFallback[];
  /** The lens identification block (WP1_DESIGN.md §2), or null when the load ended before identification. */
  readonly lens_identification: LensIdentificationReport | null;
  /** Measured numbers (millimetres unless the key says otherwise): bbox_mm, width_mm, triangles, textures,
   *  bridge_underside_mm, units_scale, file_bytes, ... */
  readonly measurements: Readonly<Record<string, unknown>>;
  readonly rendered_sha256: string | null;
  readonly live_site_would_fail: readonly string[];
}

const ROW_TITLE: Readonly<Record<SpecRowName, string>> = Object.freeze({format: 'Format', units: 'Units', origin: 'Origin',
  orientation: 'Orientation', transforms: 'Transforms', geometry: 'Geometry', components: 'Components'});

/** The load ended on a §2 refusal. `report` is the whole report (status 'refused'). */
export class ModelRefusal extends Error {
  readonly report: ModelReport;
  constructor(report: ModelReport) {
    super(report.refusal.join('; ') || 'The model was refused.');
    this.name = 'ModelRefusal';
    this.report = report;
  }
}

const unique = (values: readonly string[]): string[] => [...new Set(values)];

/** Collects one model's report while it loads and renders. Every mutation is append-only. */
export class ModelReportBuilder {
  readonly url: string;
  readonly displayName: string;
  private readonly rows = new Map<SpecRowName, {pass: boolean; detail: string[]}>(SPEC_ROWS.map(row => [row, {pass: true, detail: []}]));
  private readonly refusals: string[] = [];
  private readonly warningList: string[] = [];
  private readonly fallbackList: ModelFallback[] = [];
  private readonly liveSite: string[] = [];
  private readonly measured: Record<string, unknown> = {};
  private lensIdentification: LensIdentificationReport | null = null;
  private sha256: string | null = null;

  constructor(url: string, displayName: string, definitionWarnings: readonly string[] = []) {
    this.url = url; this.displayName = displayName;
    for (const warning of definitionWarnings) this.fallback('definition', warning);
  }
  /** Fail `row` with `message` (render anyway). */
  fail(row: SpecRowName, message: string): void {const entry = this.rows.get(row)!; entry.pass = false; entry.detail.push(message);}
  /** A detail line that does not fail the row. */
  note(row: SpecRowName, message: string): void {this.rows.get(row)!.detail.push(message);}
  /** A §1-table refusal: fails `row` and records the reason. The load throws once it has checked what it can. */
  refuse(row: SpecRowName, message: string): void {this.fail(row, message); this.refusals.push(`§2 ${ROW_TITLE[row]}: ${message}`);}
  /** Rows a refusal kept from being checked fail with `why`, as the Python validator words it. */
  notChecked(rows: readonly SpecRowName[], why: string): void {for (const row of rows) this.fail(row, `not checked: ${why}`);}
  warn(message: string): void {this.warningList.push(message);}
  /** A fallback is a warning too, never silent. */
  fallback(subsystem: string, detail: string, stage: ModelFallback['stage'] = 'load'): void {
    if (!this.fallbackList.some(entry => entry.subsystem === subsystem && entry.detail === detail)) this.fallbackList.push({subsystem, detail, stage});
    this.warn(detail);
  }
  wouldFailLive(message: string): void {this.liveSite.push(message);}
  measure(key: string, value: unknown): void {this.measured[key] = value;}
  measurement(key: string): unknown {return this.measured[key];}
  setLensIdentification(report: LensIdentificationReport): void {this.lensIdentification = report;}
  setSha256(value: string): void {this.sha256 = value;}
  get renderedSha256(): string | null {return this.sha256;}
  get refused(): boolean {return this.refusals.length > 0;}
  rowPassed(row: SpecRowName): boolean {return this.rows.get(row)!.pass;}
  get warnings(): readonly string[] {return unique(this.warningList);}

  snapshot(status: ModelStatus = this.refused ? 'refused' : 'rendered'): ModelReport {
    const spec = Object.fromEntries(SPEC_ROWS.map(row => {
      const entry = this.rows.get(row)!;
      return [row, Object.freeze({pass: entry.pass, detail: Object.freeze([...entry.detail])})];
    })) as Record<SpecRowName, SpecRow>;
    const errors = SPEC_ROWS.flatMap(row => spec[row].pass ? [] : spec[row].detail.map(detail => `${row}: ${detail}`));
    return structuredClone({schema: MODEL_REPORT_SCHEMA, url: this.url, displayName: this.displayName, status,
      refusal: [...this.refusals], spec, errors, warnings: unique(this.warningList),
      fallbacks: this.fallbackList.map(entry => ({...entry})), lens_identification: this.lensIdentification,
      measurements: this.measured, rendered_sha256: this.sha256, live_site_would_fail: unique(this.liveSite)}) as ModelReport;
  }
  /** Throw the refusal when one was recorded. */
  throwIfRefused(): void {if (this.refused) throw new ModelRefusal(this.snapshot('refused'));}
}

/** One line of the report as the webcam page lists it. */
export interface ReportLine {readonly kind: 'refusal' | 'error' | 'warning'; readonly text: string;}

/** What the webcam page lists for a loaded or refused model (WP1_DESIGN.md §6: the report's errors and warnings):
 *  the refusal reasons, then every failed §2 row's detail ('Orientation: temple left missing ...'; most row failures
 *  write no warning of their own, and a fallback such as the rear drop's must never be the only trace of the geometry
 *  defect that caused it), then the warnings. A failed row's detail that only repeats a refusal reason is not listed
 *  twice. */
export function reportLines(report: ModelReport): ReportLine[] {
  const refused = new Set(report.refusal);
  const lines: ReportLine[] = report.refusal.map(text => ({kind: 'refusal', text}));
  for (const row of SPEC_ROWS) {
    const entry = report.spec[row];
    if (entry.pass) continue;
    for (const detail of entry.detail) {
      if (!refused.has(`§2 ${ROW_TITLE[row]}: ${detail}`)) lines.push({kind: 'error', text: `${ROW_TITLE[row]}: ${detail}`});
    }
  }
  for (const text of report.warnings) lines.push({kind: 'warning', text});
  return lines;
}
