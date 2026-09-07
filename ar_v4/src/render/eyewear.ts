export const GLASSES_METERS_TO_CENTIMETERS = 100;
/** Bridge height uses canonical 168; front plane retains the baseline's 6.691763 cm depth. */
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
}

/** Local, self-contained assets. Dimensions are visual assumptions, not measured fit. */
export const EYEWEAR = Object.freeze({
  'amber-horizon': Object.freeze({
    id: 'amber-horizon', name: 'Amber Horizon', optionLabel: 'Amber Horizon · Tinted lenses',
    description: 'A rectangular tortoiseshell frame with warm brown gradient lenses.',
    finish: 'Amber tortoiseshell', assetUrl: '/models/amber-horizon.glb',
    offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145,
  }),
  'tom-ford-clear': Object.freeze({
    id: 'tom-ford-clear', name: 'Tom Ford', optionLabel: 'Tom Ford · Clear lenses',
    description: 'A rounded tortoiseshell frame with clear lenses for a closer look at the eyes and temples.',
    finish: 'Tortoiseshell · Clear lenses', assetUrl: '/models/tom-ford-clear.glb',
    offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145,
  }),
} satisfies Record<string, EyewearDefinition>);

export type EyewearId = keyof typeof EYEWEAR;
export const DEFAULT_EYEWEAR_ID: EyewearId = 'amber-horizon';

export function eyewearById(id: string): EyewearDefinition {
  if (!Object.hasOwn(EYEWEAR, id)) throw new Error('This frame is unavailable. Choose another frame.');
  return EYEWEAR[id as EyewearId];
}
