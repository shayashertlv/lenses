import type {MPMask} from '@mediapipe/tasks-vision';

export type CategoryExtractionMode = 'sdk' | 'direct';
export type CategoryExtractionPath = 'sdk-copy' | 'direct-byte-copy' | 'direct-float-conversion';
export type CategoryMaskSource = Pick<MPMask, 'width' | 'height' | 'hasUint8Array' |
  'hasFloat32Array' | 'hasWebGLTexture' | 'getAsUint8Array' | 'getAsFloat32Array'>;

export interface CategoryExtractionMetrics {
  mode: CategoryExtractionMode;
  path: CategoryExtractionPath;
  hasUint8: boolean;
  hasFloat32: boolean;
  hasWebGLTexture: boolean;
  /** Getter wall time, including any SDK conversion or deferred GPU work. */
  retrievalMs: number;
  /** Direct float-to-category allocation and conversion wall time. */
  conversionMs: number;
  /** Independent byte-copy allocation and copy wall time. */
  copyMs: number;
  totalMs: number;
  maskPixels: number;
  /** Size returned by the public getter, including cached arrays; not transfer bytes. */
  retrievedArrayBytes: number;
  categoryBytesCopied: number;
  categoryBytesConverted: number;
  ownedCategoryBytesAllocated: number;
  /** Explicit SDK Float32Array.map temporary avoided; not GPU transfer bytes. */
  explicitFloatTemporaryBytesAvoided: number;
  /** Extra byte array avoided compared with SDK conversion plus owned slice. */
  explicitCategoryCopyBytesAvoided: number;
}

export interface CategoryExtractionResult {
  category: Uint8Array<ArrayBuffer>;
  metrics: CategoryExtractionMetrics;
}

/** Call within the MediaPipe result callback. Only the returned category is owned here. */
export function extractCategoryMask(mask: CategoryMaskSource,
  mode: CategoryExtractionMode = 'sdk'): CategoryExtractionResult {
  const start = performance.now();
  if (mode !== 'sdk' && mode !== 'direct') throw new Error('Unknown category extraction mode.');
  const pixels = mask.width * mask.height;
  if (!Number.isSafeInteger(mask.width) || mask.width <= 0 ||
    !Number.isSafeInteger(mask.height) || mask.height <= 0 || !Number.isSafeInteger(pixels)) {
    throw new Error('Category mask dimensions must be positive safe integers.');
  }
  // Capture these before a getter can populate another cached representation.
  const hasUint8 = mask.hasUint8Array(), hasFloat32 = mask.hasFloat32Array(),
    hasWebGLTexture = mask.hasWebGLTexture();
  if (!hasUint8 && !hasFloat32 && !hasWebGLTexture) {
    throw new Error('Category mask has no available representation.');
  }
  const path: CategoryExtractionPath = mode === 'sdk' ? 'sdk-copy' :
    hasUint8 ? 'direct-byte-copy' : 'direct-float-conversion';
  let category: Uint8Array<ArrayBuffer>, retrievalMs = 0, conversionMs = 0, copyMs = 0;
  const retrievalStart = performance.now();
  if (path === 'direct-float-conversion') {
    // Public retrieval still performs any necessary SDK GPU-to-CPU transfer.
    const values = mask.getAsFloat32Array();
    retrievalMs = performance.now() - retrievalStart;
    if (values.length !== pixels) throw new Error('Category float storage differs from mask dimensions.');
    const conversionStart = performance.now();
    category = new Uint8Array(pixels);
    // tasks-vision 1.0.1 uses new Uint8Array(values.map(v => Math.round(255 * v))).
    // Float32Array.map rounds each result to float32 before Uint8 coercion. Keep
    // that step even for out-of-range/nonfinite values; do not clamp or retune.
    for (let i = 0; i < pixels; i++) category[i] = Math.fround(Math.round(255 * values[i]!));
    conversionMs = performance.now() - conversionStart;
  } else {
    // Bytes take priority even when a float/texture representation also exists.
    const values = mask.getAsUint8Array();
    retrievalMs = performance.now() - retrievalStart;
    if (values.length !== pixels) throw new Error('Category byte storage differs from mask dimensions.');
    const copyStart = performance.now();
    category = values.slice();
    copyMs = performance.now() - copyStart;
  }
  return {category, metrics: {
    mode, path, hasUint8, hasFloat32, hasWebGLTexture, retrievalMs, conversionMs, copyMs,
    totalMs: performance.now() - start, maskPixels: pixels,
    retrievedArrayBytes: pixels * (path === 'direct-float-conversion' ? 4 : 1),
    categoryBytesCopied: path === 'direct-float-conversion' ? 0 : pixels,
    categoryBytesConverted: path === 'direct-float-conversion' ? pixels : 0,
    ownedCategoryBytesAllocated: category.byteLength,
    explicitFloatTemporaryBytesAvoided: path === 'direct-float-conversion' ? pixels * 4 : 0,
    explicitCategoryCopyBytesAvoided: path === 'direct-float-conversion' ? pixels : 0,
  }};
}
