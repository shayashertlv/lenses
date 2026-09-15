/** A model prepared by Modeling Auto, handed over by URL:
 *    ?model=<glb url>&name=<label>&clip=<temple clip z, meters>&width=<frame width, mm>&sha256=<asset digest>
 *  The asset is already real-size meters with the bridge underside at the origin and its front toward +Z, one physical
 *  material per part, the same convention as the shipped frames. It becomes this page's Modeling Auto frame; with its
 *  digest the temple continuity model reads its own geometry. */
import {registerModelingAutoEyewear} from './catalog.ts';
import type {EyewearDefinition} from './catalog.ts';
import {registerPinnedGeometry} from '../render/continuity.ts';

export const MAX_EXTERNAL_NAME_LENGTH = 120;
export const DEFAULT_EXTERNAL_CLIP_ZM = -0.11;
export const DEFAULT_EXTERNAL_WIDTH_MM = 145;
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

export interface ExternalModel {
  readonly url: string;
  readonly name: string;
  /** Temple clip depth reported by the export, meters, or null when absent or unusable. */
  readonly clipZM: number | null;
  /** Frame width the export was scaled to, millimeters, or null when absent or unusable. */
  readonly widthMm: number | null;
  /** SHA-256 of the asset bytes, or null when absent or malformed; without it continuity is unavailable for the model. */
  readonly sha256: string | null;
}

function optionalNumber(value: string | null, low: number, high: number): number | null {
  if (value === null || value.trim() === '') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= low && number <= high ? number : null;
}

/** Null when the page carries no model; throws when the model address is unusable. */
export function parseExternalModel(search: string, pageOrigin: string): ExternalModel | null {
  const params = new URLSearchParams(search);
  const model = params.get('model');
  if (model === null || model.trim() === '') return null;
  let url: URL;
  try { url = new URL(model, pageOrigin); }
  catch { throw new Error('The model address is not a valid URL.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) throw new Error('The model address must be a plain http(s) URL.');
  if (url.origin !== pageOrigin && !LOOPBACK_HOSTS.has(url.hostname)) throw new Error('The model must be served from this computer.');
  const name = (params.get('name') ?? '').trim().slice(0, MAX_EXTERNAL_NAME_LENGTH) || 'Modeling Auto model';
  const digest = (params.get('sha256') ?? '').trim().toLowerCase();
  return Object.freeze({url: url.href, name, clipZM: optionalNumber(params.get('clip'), -0.2, -0.03),
    widthMm: optionalNumber(params.get('width'), 60, 250), sha256: /^[0-9a-f]{64}$/.test(digest) ? digest : null});
}

export function describeExternalModel(model: ExternalModel): string {
  const size = model.widthMm === null ? '' : `, ${model.widthMm} mm across the front`;
  const continuity = model.sha256 ? '' : ' Its digest was not supplied, so the temple continuity cut is unavailable for it.';
  return `Modeling Auto model "${model.name}"${size}, is selected on this page; the shipped frames remain available. `
    + `Placement uses the estimated bridge underside, not measured fit.${continuity}`;
}

/** Register the model as this page's Modeling Auto frame and pin its geometry for continuity. */
export function installExternalModel(model: ExternalModel): EyewearDefinition {
  const definition = registerModelingAutoEyewear({name: model.name, assetUrl: model.url,
    widthMm: model.widthMm ?? DEFAULT_EXTERNAL_WIDTH_MM, templeClipLocalZM: model.clipZM ?? DEFAULT_EXTERNAL_CLIP_ZM});
  if (model.sha256) registerPinnedGeometry(model.url, model.sha256);
  return definition;
}
