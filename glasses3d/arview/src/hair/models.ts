/** The pinned hair segmentation models (exact weights, verified by size and SHA-256 before use). */
import {assetPath} from '../assets.ts';
import {ASSET_PINS} from '../asset-pins.ts';

export type HairModelId = 'hair-only' | 'selfie-multiclass';

export interface HairModel {
  readonly id: HairModelId;
  readonly title: string;
  readonly sha256: string;
  readonly bytes: number;
  readonly labels: readonly string[];
  readonly hairIndex: number;
  readonly inputSize: readonly [number, number];
  readonly urlPath: string;
}

export const HAIR_MODELS: Readonly<Record<HairModelId, HairModel>> = Object.freeze({
  'hair-only': Object.freeze({
    id: 'hair-only', title: 'Hair-only · MediaPipe 512',
    sha256: ASSET_PINS['public/models/hair/hair-only.tflite'], bytes: 781618,
    labels: Object.freeze(['background', 'hair']), hairIndex: 1,
    inputSize: Object.freeze([512, 512] as const), urlPath: assetPath('models/hair/hair-only.tflite'),
  }),
  'selfie-multiclass': Object.freeze({
    id: 'selfie-multiclass', title: 'Multiclass · MediaPipe 256',
    sha256: ASSET_PINS['public/models/hair/selfie-multiclass.tflite'], bytes: 16371837,
    labels: Object.freeze(['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others']), hairIndex: 1,
    inputSize: Object.freeze([256, 256] as const), urlPath: assetPath('models/hair/selfie-multiclass.tflite'),
  }),
});

export const DEFAULT_HAIR_MODEL_ID: HairModelId = 'hair-only';
export const HAIR_MODEL_LIST: readonly HairModel[] = Object.freeze(Object.values(HAIR_MODELS));

export function isHairModelId(value: unknown): value is HairModelId {
  return value === 'hair-only' || value === 'selfie-multiclass';
}

export function getHairModel(value: unknown): HairModel {
  if (!isHairModelId(value)) throw new Error('Unknown pinned hair model.');
  return HAIR_MODELS[value];
}
