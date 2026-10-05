/** The model a page or capture renders, and the constants the renderer places it with. arview has no catalog
 *  (WP1_DESIGN §5): one ModelDefinition comes from the ?model= handover (external.ts) or from the caller (the capture
 *  runner, tests). `resolveModelDefinition` turns it into the values the renderer uses and says, as warnings for the
 *  model report, every value it could not use. */

export const GLASSES_METERS_TO_CENTIMETERS = 100;
/** Bridge height uses canonical landmark 168; the front plane keeps the baseline's 6.691763 cm depth. The model's
 *  origin (the bridge underside, PLAN §2) is placed here. */
export const GLASSES_OFFSET_CM = Object.freeze([0, 3.271027, 6.531958919387042] as const);

/** The nearest temple clip depth the renderer can draw: the v3 end blend fades over 15 mm and must end behind -0.03 m
 *  (render/temple-clip.ts). */
export const TEMPLE_CLIP_NEAREST_LOCAL_Z_M = -0.045;
/** The farthest temple clip depth the rear drop, continuity and the clip shader accept. */
export const TEMPLE_CLIP_FARTHEST_LOCAL_Z_M = -0.2;
/** A clip derived from the model's temples never lies further back than this (the Studio's default clip, so long arms
 *  end where the owner judged them; render/temple-geometry.ts deriveTempleClip). It is also the clip of a model with no
 *  temple at all (reported). Without a usable handover the clip is always derived from the model's own temples; the old
 *  fixed ?model= default of -0.11 m (about 4 cm short of the ear on the shipped frames) is gone. */
export const TEMPLE_CLIP_FLOOR_LOCAL_Z_M = -0.14;
/** Lens reflection multiplier range (?lensenv=); outside it the value is ignored with a warning. */
export const LENS_ENV_INTENSITY_RANGE = Object.freeze([0.3, 4] as const);

export interface ModelDefinition {
  /** Address of the GLB: real-size metres, bridge underside at the origin, +Y up, +Z front (PLAN §2). */
  readonly url: string;
  readonly displayName: string;
  /** Where the drawn arms end, in the model's local metres: an override of the clip the renderer derives from the
   *  model's own temples (inside [-0.2, -0.045] m, else ignored with a warning). Absent: derived. */
  readonly templeClipZM?: number;
  /** Reflection (environment map) multiplier of the lens materials; absent leaves the renderer's default. */
  readonly lensEnvIntensity?: number;
  /** Where the bridge origin sits on the canonical face, centimetres; absent means GLASSES_OFFSET_CM. */
  readonly offsetCm?: readonly [number, number, number];
  /** A digest handed over with the model (?sha256=). Only compared with the loaded bytes (a mismatch is a warning): the
   *  report and every verdict bind to the bytes actually rendered, never to this value (WP1_DESIGN.md §3). */
  readonly handoverSha256?: string;
  /** Problems found while building this definition (an unusable handover value, ...), carried into the model report. */
  readonly warnings?: readonly string[];
}

/** A ModelDefinition with every value the renderer needs, and the warnings for each value it could not use. */
export interface ResolvedModel {
  readonly url: string;
  readonly displayName: string;
  /** The usable handed-over clip, or null: the renderer then derives the clip from the model's temples (and it always
   *  cross-checks a handover against them). */
  readonly templeClipZM: number | null;
  readonly lensEnvIntensity?: number;
  readonly offsetCm: readonly [number, number, number];
  readonly handoverSha256?: string;
  readonly warnings: readonly string[];
}

const fileName = (url: string): string => url.split('?')[0]!.split('/').filter(Boolean).at(-1) ?? url;
const metres = (value: number): string => `${value.toFixed(3)} m`;

/** Resolve a definition. A missing or unusable value never throws: it falls back and says so in `warnings`. Only a
 *  definition without an address throws, since there is nothing to load. */
export function resolveModelDefinition(model: ModelDefinition): ResolvedModel {
  if (typeof model?.url !== 'string' || model.url.trim() === '') throw new Error('The model definition has no address.');
  const warnings: string[] = [...(model.warnings ?? [])];
  let displayName = typeof model.displayName === 'string' ? model.displayName.trim() : '';
  if (!displayName) {
    displayName = fileName(model.url);
    warnings.push(`The model has no display name; it is shown by its file name "${displayName}".`);
  }
  // No clip is the normal case: the renderer derives it from the model's own temples (temple-geometry.ts).
  let templeClipZM: number | null = null;
  const clip = model.templeClipZM;
  if (clip !== undefined) {
    if (typeof clip === 'number' && Number.isFinite(clip) && clip >= TEMPLE_CLIP_FARTHEST_LOCAL_Z_M && clip <= TEMPLE_CLIP_NEAREST_LOCAL_Z_M) {
      templeClipZM = clip;
    } else {
      warnings.push(`The temple clip depth ${String(clip)} is outside the drawable range `
        + `[${metres(TEMPLE_CLIP_FARTHEST_LOCAL_Z_M)}, ${metres(TEMPLE_CLIP_NEAREST_LOCAL_Z_M)}] and was ignored; `
        + 'the arms end at the clip derived from the model\'s own temples.');
    }
  }
  let lensEnvIntensity: number | undefined;
  const lensenv = model.lensEnvIntensity;
  if (lensenv !== undefined) {
    const [low, high] = LENS_ENV_INTENSITY_RANGE;
    if (typeof lensenv === 'number' && Number.isFinite(lensenv) && lensenv >= low && lensenv <= high) lensEnvIntensity = lensenv;
    else warnings.push(`The lens reflection multiplier ${String(lensenv)} is outside ${low}-${high} and was ignored; the lenses use the default reflection.`);
  }
  let offsetCm: readonly [number, number, number] = GLASSES_OFFSET_CM;
  const offset = model.offsetCm;
  if (offset !== undefined) {
    if (Array.isArray(offset) && offset.length === 3 && offset.every(value => typeof value === 'number' && Number.isFinite(value))) {
      offsetCm = Object.freeze([offset[0], offset[1], offset[2]] as const);
    } else warnings.push(`The bridge offset ${JSON.stringify(offset)} is not three finite centimetre values and was ignored; the standard offset is used.`);
  }
  const handover = typeof model.handoverSha256 === 'string' ? model.handoverSha256.trim().toLowerCase() : undefined;
  if (handover !== undefined && !/^[0-9a-f]{64}$/.test(handover)) {
    warnings.push(`The handed-over digest "${handover.slice(0, 80)}" is not 64 hexadecimal characters and was ignored.`);
  }
  return Object.freeze({url: model.url, displayName, templeClipZM, offsetCm, warnings: Object.freeze(warnings),
    ...(lensEnvIntensity === undefined ? {} : {lensEnvIntensity}),
    ...(handover !== undefined && /^[0-9a-f]{64}$/.test(handover) ? {handoverSha256: handover} : {})});
}
