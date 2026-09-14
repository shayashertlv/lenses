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
  /** Fixed preview endpoint before the rear hook, in original GLB local meters. */
  readonly templeClipLocalZM: number;
  /** Trial import conversion: finite volume attenuation lengths must use the AR scene's centimeters. */
  readonly volumeAttenuationScale?: number;
  /** Trial assets with authored optical/frame roles can apply temple controls to crystal arms. */
  readonly semanticTempleParts?: boolean;
}

/** Local, self-contained assets. Dimensions are visual assumptions, not measured fit. */
export const EYEWEAR = Object.freeze({
  'amber-horizon': Object.freeze({
    id: 'amber-horizon', name: 'Amber Horizon', optionLabel: 'Amber Horizon · Tinted lenses',
    description: 'A rectangular tortoiseshell frame with warm brown gradient lenses.',
    finish: 'Amber tortoiseshell', assetUrl: '/models/amber-horizon.glb',
    offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145, templeClipLocalZM: -0.105,
  }),
  'tom-ford-clear': Object.freeze({
    id: 'tom-ford-clear', name: 'Tom Ford', optionLabel: 'Tom Ford · Clear lenses',
    description: 'A rounded tortoiseshell frame with clear lenses for a closer look at the eyes and temples.',
    finish: 'Tortoiseshell · Clear lenses', assetUrl: '/models/tom-ford-clear.glb',
    offsetCm: GLASSES_OFFSET_CM, assumedWidthMm: 145, templeClipLocalZM: -0.110,
  }),
} satisfies Record<string, EyewearDefinition>);

/** Trial assets for the original / testing ground, separate from accepted temple-correction models. */
export const TEST_EYEWEAR = Object.freeze({
  'test_': Object.freeze({
    id: 'test_', name: 'test_', optionLabel: 'test_',
    description: 'The supplied crystal frame with warm brown lenses and champagne gold hardware.',
    finish: 'Translucent crystal · Warm brown lenses', assetUrl: '/models/test_.glb',
    // Align this asset's central bridge underside and foremost Z to the existing preview anchors.
    offsetCm: Object.freeze([0, 2.246026971687794, 6.7552434368019104] as const),
    assumedWidthMm: 145, templeClipLocalZM: -0.11223284517414868,
    volumeAttenuationScale: GLASSES_METERS_TO_CENTIMETERS,
    semanticTempleParts: true,
  }),
} satisfies Record<string, EyewearDefinition>);

export const TESTING_GROUND_EYEWEAR = Object.freeze({...EYEWEAR, ...TEST_EYEWEAR});
export type EyewearId = keyof typeof TESTING_GROUND_EYEWEAR;
export const DEFAULT_EYEWEAR_ID: EyewearId = 'amber-horizon';

export function eyewearById(id: string): EyewearDefinition {
  if (!Object.hasOwn(TESTING_GROUND_EYEWEAR, id)) throw new Error('This frame is unavailable. Choose another frame.');
  return TESTING_GROUND_EYEWEAR[id as EyewearId];
}
