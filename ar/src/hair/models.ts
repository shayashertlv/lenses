/** The pinned hair segmentation models (exact weights, verified by size and SHA-256 before use). */
import {assetPath} from '../assets.ts';

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
    sha256: '2628cf3ce5f695f604cbea2841e00befcaa3624bf80caf3664bef2656d59bf84', bytes: 781618,
    labels: Object.freeze(['background', 'hair']), hairIndex: 1,
    inputSize: Object.freeze([512, 512] as const), urlPath: assetPath('models/hair/hair-only.tflite'),
  }),
  'selfie-multiclass': Object.freeze({
    id: 'selfie-multiclass', title: 'Multiclass · MediaPipe 256',
    sha256: 'c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0', bytes: 16371837,
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
