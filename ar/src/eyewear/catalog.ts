/** The frame catalog: the two shipped frames plus one slot for a model handed over by Modeling Auto when the page
 *  opens (see external.ts). The slot is hidden (not enumerable) until a model is registered, so a page without a
 *  handover lists exactly the shipped frames. Dimensions are visual assumptions, not measured fit. */
import {assetPath} from '../assets.ts';

export const GLASSES_METERS_TO_CENTIMETERS = 100;
/** Bridge height uses canonical landmark 168; the front plane keeps the baseline's 6.691763 cm depth. */
export const GLASSES_OFFSET_CM = Object.freeze([0, 3.271027, 6.531958919387042] as const);

export interface EyewearDefinition {
  readonly id: string;
  readonly name: string;
  readonly optionLabel: string;
  readonly description: string;
  readonly finish: string;
  readonly assetUrl: string;
  readonly offsetCm: readonly [number, number, number];
  readonly assumedWidthMm: number;
  /** Where the drawn arm ends, in the original GLB's local metres. Until 2026-09-18 this stopped in front of the ear
   *  hook (-0.105 and -0.110), which left the arm as a straight shaft ending in mid-air about 4 cm short of the ear and
   *  2.5 cm above it: the shipped assets carry the hook, which curves down to y 0.4 cm and inward to |x| 4.9 cm. It now
   *  runs to just inside each asset's own arm end, and the head occluder is what takes the hook away. */
  readonly templeClipLocalZM: number;
}

export const SHIPPED_EYEWEAR = Object.freeze({
  'amber-horizon': Object.freeze({
    id: 'amber-horizon', name: 'Amber Horizon', optionLabel: 'Amber Horizon · Tinted lenses',
    description: 'A rectangular tortoiseshell frame with warm brown gradient lenses.',
    finish: 'Amber tortoiseshell', assetUrl: assetPath('models/amber-horizon.glb'),
    offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145, templeClipLocalZM: -0.140,
  }),
  'tom-ford-clear': Object.freeze({
    id: 'tom-ford-clear', name: 'Tom Ford', optionLabel: 'Tom Ford · Clear lenses',
    description: 'A rounded tortoiseshell frame with clear lenses for a closer look at the eyes and temples.',
    finish: 'Tortoiseshell · Clear lenses', assetUrl: assetPath('models/tom-ford-clear.glb'),
    offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145, templeClipLocalZM: -0.148,
  }),
} satisfies Record<string, EyewearDefinition>);

export const MODELING_AUTO_EYEWEAR_ID = 'modeling-auto';
const UNREGISTERED: EyewearDefinition = Object.freeze({
  id: MODELING_AUTO_EYEWEAR_ID, name: 'Modeling Auto model', optionLabel: 'Modeling Auto model',
  description: 'No Modeling Auto model was handed over on this page.', finish: 'Modeling Auto',
  assetUrl: '', offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145, templeClipLocalZM: -0.11,
});
type Catalog = typeof SHIPPED_EYEWEAR & {readonly [MODELING_AUTO_EYEWEAR_ID]: EyewearDefinition};
export const EYEWEAR: Catalog = Object.defineProperty({...SHIPPED_EYEWEAR}, MODELING_AUTO_EYEWEAR_ID,
  {value: UNREGISTERED, enumerable: false, configurable: true, writable: false}) as Catalog;
export type EyewearId = keyof Catalog;
export const DEFAULT_EYEWEAR_ID: EyewearId = 'amber-horizon';

/** The handed-over model, or null when this page has none. */
export function modelingAutoEyewear(): EyewearDefinition | null {
  return Object.prototype.propertyIsEnumerable.call(EYEWEAR, MODELING_AUTO_EYEWEAR_ID) ? EYEWEAR[MODELING_AUTO_EYEWEAR_ID] : null;
}

/** True for a shipped frame id or the Modeling Auto slot (registered or not): the ids a paired image may carry. */
export function isEyewearId(id: unknown): id is EyewearId {
  return typeof id === 'string' && (Object.hasOwn(SHIPPED_EYEWEAR, id) || id === MODELING_AUTO_EYEWEAR_ID);
}

export function eyewearById(id: string): EyewearDefinition {
  if (!Object.hasOwn(EYEWEAR, id) || (id === MODELING_AUTO_EYEWEAR_ID && !modelingAutoEyewear()))
    throw new Error('This frame is unavailable. Choose another frame.');
  return EYEWEAR[id as EyewearId];
}

export interface ModelingAutoHandover {
  readonly name: string;
  /** Same-origin or loopback address of the prepared GLB: real-size meters, bridge underside at the origin, front toward +Z. */
  readonly assetUrl: string;
  readonly widthMm: number;
  /** Fixed preview endpoint before the rear hook, in the asset's local meters. */
  readonly templeClipLocalZM: number;
  readonly description?: string;
}

const finite = (value: number, low: number, high: number): boolean => Number.isFinite(value) && value >= low && value <= high;
/** The nearest temple clip depth the renderer can draw: the v3 end blend fades over 15 mm and must end behind -0.03 m
 *  (render/temple-clip.ts). A handed-over depth between this and -0.03 m is drawn from here rather than refused per frame. */
export const TEMPLE_CLIP_NEAREST_LOCAL_Z_M = -0.045;

/** Register the handed-over model as this page's Modeling Auto frame and return its definition. */
export function registerModelingAutoEyewear(model: ModelingAutoHandover): EyewearDefinition {
  if (typeof model.assetUrl !== 'string' || model.assetUrl === '') throw new Error('The Modeling Auto model has no asset address.');
  if (!finite(model.widthMm, 60, 250)) throw new Error('The Modeling Auto frame width is unusable.');
  if (!finite(model.templeClipLocalZM, -0.2, -0.03)) throw new Error('The Modeling Auto temple clip depth is unusable.');
  const name = model.name.trim() || 'Modeling Auto model';
  const definition: EyewearDefinition = Object.freeze({
    id: MODELING_AUTO_EYEWEAR_ID, name, optionLabel: `${name} · Modeling Auto`,
    description: model.description ?? `Prepared by Modeling Auto at ${model.widthMm} mm across the front. Preview placement is not measured wearer fit.`,
    finish: 'Modeling Auto · Prepared model', assetUrl: model.assetUrl, offsetCm: GLASSES_OFFSET_CM,
    assumedWidthMm: model.widthMm, templeClipLocalZM: Math.min(model.templeClipLocalZM, TEMPLE_CLIP_NEAREST_LOCAL_Z_M),
  });
  Object.defineProperty(EYEWEAR, MODELING_AUTO_EYEWEAR_ID, {value: definition, enumerable: true, configurable: true, writable: false});
  return definition;
}

/** Forget a registered model; the slot is hidden again. */
export function unregisterModelingAutoEyewear(): void {
  Object.defineProperty(EYEWEAR, MODELING_AUTO_EYEWEAR_ID, {value: UNREGISTERED, enumerable: false, configurable: true, writable: false});
}
