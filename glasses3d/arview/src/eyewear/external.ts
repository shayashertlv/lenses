/** A model handed over by URL:
 *    ?model=<glb url>&name=<label>&clip=<temple clip z, meters>&width=<frame width, mm>&sha256=<asset digest>&lensenv=<0.3-4>
 *  The asset is real-size metres with the bridge underside at the origin and its front toward +Z (PLAN §2). It becomes
 *  this page's one ModelDefinition (there is no catalog). The digest is only compared with the bytes actually loaded (a
 *  mismatch is a report warning); nothing is pinned, and the temple continuity model reads the rendered scene itself
 *  (WP1_DESIGN.md §3). The address must be a plain http(s) URL on this page's origin or on loopback. A value that cannot
 *  be used is never dropped silently: it becomes a warning in the definition, which the renderer copies into its model
 *  report (range checks for clip and lensenv are resolveModelDefinition's, catalog.ts). */
import type {ModelDefinition} from './catalog.ts';

export const MAX_EXTERNAL_NAME_LENGTH = 120;
export const DEFAULT_EXTERNAL_NAME = 'Handed-over model';
/** The frame widths the handover describes; width is descriptive only (fit width is measured from the lenses). */
export const EXTERNAL_WIDTH_RANGE_MM = Object.freeze([60, 250] as const);
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

export interface ExternalModel {
  readonly url: string;
  readonly name: string;
  /** Temple clip depth handed over, metres, or null when absent or not a number (range is checked when resolved). */
  readonly clipZM: number | null;
  /** Frame width the export was scaled to, millimetres, or null when absent or unusable. Descriptive only. */
  readonly widthMm: number | null;
  /** SHA-256 of the asset bytes, or null when absent or malformed; compared with the loaded bytes, never trusted. */
  readonly sha256: string | null;
  /** Lens reflection multiplier handed over, or null when absent or not a number (range is checked when resolved). */
  readonly lensEnvIntensity: number | null;
  /** Every handover value that was present but could not be read. */
  readonly warnings: readonly string[];
}

function optionalNumber(params: URLSearchParams, key: string, what: string, warnings: string[]): number | null {
  const value = params.get(key);
  if (value === null || value.trim() === '') return null;
  const number = Number(value);
  if (Number.isFinite(number)) return number;
  warnings.push(`The ${what} "${value.slice(0, 40)}" (?${key}=) is not a number and was ignored.`);
  return null;
}

/** Null when the page carries no model; throws when the model address is unusable (a page error, not model acceptance). */
export function parseExternalModel(search: string, pageOrigin: string): ExternalModel | null {
  const params = new URLSearchParams(search);
  const model = params.get('model');
  if (model === null || model.trim() === '') return null;
  let url: URL;
  try { url = new URL(model, pageOrigin); }
  catch { throw new Error('The model address is not a valid URL.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) throw new Error('The model address must be a plain http(s) URL.');
  if (url.origin !== pageOrigin && !LOOPBACK_HOSTS.has(url.hostname)) throw new Error('The model must be served from this computer.');
  const warnings: string[] = [];
  const name = (params.get('name') ?? '').trim().slice(0, MAX_EXTERNAL_NAME_LENGTH) || DEFAULT_EXTERNAL_NAME;
  const clipZM = optionalNumber(params, 'clip', 'temple clip depth', warnings);
  let widthMm = optionalNumber(params, 'width', 'frame width', warnings);
  const [low, high] = EXTERNAL_WIDTH_RANGE_MM;
  if (widthMm !== null && (widthMm < low || widthMm > high)) {
    warnings.push(`The frame width ${widthMm} mm (?width=) is outside ${low}-${high} mm and was ignored.`);
    widthMm = null;
  }
  const lensEnvIntensity = optionalNumber(params, 'lensenv', 'lens reflection multiplier', warnings);
  const rawDigest = params.get('sha256');
  const digest = (rawDigest ?? '').trim().toLowerCase();
  const sha256 = /^[0-9a-f]{64}$/.test(digest) ? digest : null;
  if (rawDigest !== null && rawDigest.trim() !== '' && sha256 === null) {
    warnings.push('The model digest (?sha256=) is not 64 hexadecimal characters and was ignored.');
  }
  return Object.freeze({url: url.href, name, clipZM, widthMm, sha256, lensEnvIntensity, warnings: Object.freeze(warnings)});
}

export function describeExternalModel(model: ExternalModel): string {
  const size = model.widthMm === null ? '' : `, ${model.widthMm} mm across the front`;
  return `Model "${model.name}"${size} is loaded on this page. `
    + 'Placement uses the estimated bridge underside, not measured fit.';
}

/** The page's ModelDefinition for a handed-over model. A supplied digest is carried along to be compared with the
 *  loaded bytes; nothing is registered or pinned. */
export function modelFromExternal(model: ExternalModel): ModelDefinition {
  return Object.freeze({url: model.url, displayName: model.name, warnings: model.warnings,
    ...(model.clipZM === null ? {} : {templeClipZM: model.clipZM}),
    ...(model.lensEnvIntensity === null ? {} : {lensEnvIntensity: model.lensEnvIntensity}),
    ...(model.sha256 === null ? {} : {handoverSha256: model.sha256})});
}
