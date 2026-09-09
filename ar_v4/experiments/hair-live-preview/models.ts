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

/** Exact weights already reviewed in the isolated still comparison. */
export const HAIR_MODELS: Readonly<Record<HairModelId, HairModel>> = Object.freeze({
  'hair-only': Object.freeze({
    id: 'hair-only', title: 'Hair-only · MediaPipe 512',
    sha256: '2628cf3ce5f695f604cbea2841e00befcaa3624bf80caf3664bef2656d59bf84', bytes: 781618,
    labels: Object.freeze(['background', 'hair']), hairIndex: 1,
    inputSize: Object.freeze([512, 512] as const), urlPath: '/hair-preview-models/hair-only.tflite',
  }),
  'selfie-multiclass': Object.freeze({
    id: 'selfie-multiclass', title: 'Multiclass · MediaPipe 256',
    sha256: 'c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0', bytes: 16371837,
    labels: Object.freeze(['background', 'hair', 'body-skin', 'face-skin', 'clothes', 'others']), hairIndex: 1,
    inputSize: Object.freeze([256, 256] as const), urlPath: '/hair-preview-models/selfie-multiclass.tflite',
  }),
});

export const HAIR_MODEL_LIST: readonly HairModel[] = Object.freeze(Object.values(HAIR_MODELS));
export const HAIR_RUNTIME = Object.freeze({package: '@mediapipe/tasks-vision', version: '1.0.1',
  runningMode: 'IMAGE', delegate: 'CPU', outputConfidenceMasks: true, outputCategoryMask: true} as const);

export function isHairModelId(value: unknown): value is HairModelId {
  return value === 'hair-only' || value === 'selfie-multiclass';
}

export function getHairModel(value: unknown): HairModel {
  if (!isHairModelId(value)) throw new Error('Unknown pinned hair model.');
  return HAIR_MODELS[value];
}

export const hairModelById = getHairModel;
