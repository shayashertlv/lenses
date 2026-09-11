import {isRecord, validateHairRequest} from '../../hair-live-preview/hair-protocol.ts';
import type {HairExpectedPair as OriginalExpectedPair, HairRawOutput as OriginalRawOutput,
  HairSegmentationResult as OriginalSegmentationResult, HairWorkerRequest as OriginalWorkerRequest,
  HairWorkerResponse as OriginalWorkerResponse} from '../../hair-live-preview/hair-protocol.ts';
import type {CategoryExtractionMode, CategoryExtractionMetrics} from './extraction.ts';

export const CATEGORY_EXTRACTION_PROTOCOL = 'hair-category-extraction-v1';
export type {CategoryExtractionMode, CategoryExtractionMetrics} from './extraction.ts';
export type HairExpectedPair = OriginalExpectedPair & {categoryExtractionMode: CategoryExtractionMode};
export type HairRawOutput = OriginalRawOutput & {categoryExtraction: CategoryExtractionMetrics};
export type HairSegmentationResult = OriginalSegmentationResult & {categoryExtraction: CategoryExtractionMetrics};
export type HairWorkerRequest = Extract<OriginalWorkerRequest, {type: 'initialize'}>
  | (Extract<OriginalWorkerRequest, {type: 'segment'}> & {categoryExtractionMode: CategoryExtractionMode});
export type HairWorkerResponse = Extract<OriginalWorkerResponse, {type: 'error'}>
  | (Extract<OriginalWorkerResponse, {type: 'ready'}> & {categoryExtractionProtocol: typeof CATEGORY_EXTRACTION_PROTOCOL})
  | (Omit<Extract<OriginalWorkerResponse, {type: 'result'}>, 'output'> & {output: HairRawOutput});

export function assertCategoryExtractionMode(value: unknown): asserts value is CategoryExtractionMode {
  if (value !== 'sdk' && value !== 'direct') throw new Error('Unknown category extraction mode.');
}

/** Extend the original boundary without bypassing any pinned input checks. */
export function validateHairCostRequest(value: unknown): HairWorkerRequest {
  const message = validateHairRequest(value);
  if (message.type === 'initialize') return message;
  const mode = isRecord(value) ? value.categoryExtractionMode : undefined;
  assertCategoryExtractionMode(mode);
  return {...message, categoryExtractionMode: mode};
}

/** Only validated scalar extraction measurements cross into a retained result.
 * These describe the exact requested path; no timing is a GPU speed claim. */
export function validateCategoryExtractionMetrics(value: unknown, expected: HairExpectedPair,
  extractionMs: number): CategoryExtractionMetrics {
  if (!isRecord(value) || value.mode !== expected.categoryExtractionMode) throw new Error('The category extraction mode does not match the owned request.');
  const {mode, path, hasUint8, hasFloat32, hasWebGLTexture} = value;
  assertCategoryExtractionMode(mode);
  if (typeof hasUint8 !== 'boolean' || typeof hasFloat32 !== 'boolean' || typeof hasWebGLTexture !== 'boolean'
    || !(hasUint8 || hasFloat32 || hasWebGLTexture)) throw new Error('Invalid category extraction representation.');
  const directFloat = mode === 'direct' && !hasUint8;
  const expectedPath = mode === 'sdk' ? 'sdk-copy' : directFloat ? 'direct-float-conversion' : 'direct-byte-copy';
  if (path !== expectedPath) throw new Error('The category extraction path does not match its requested mode and original representation.');
  const pixels = expected.width * expected.height;
  if (value.maskPixels !== pixels || value.ownedCategoryBytesAllocated !== pixels
    || value.retrievedArrayBytes !== (directFloat ? pixels * 4 : pixels)
    || value.categoryBytesCopied !== (directFloat ? 0 : pixels)
    || value.categoryBytesConverted !== (directFloat ? pixels : 0)
    || value.explicitFloatTemporaryBytesAvoided !== (directFloat ? pixels * 4 : 0)
    || value.explicitCategoryCopyBytesAvoided !== (directFloat ? pixels : 0)) throw new Error('Invalid category extraction dimensions or allocation counters.');
  const durations = ['retrievalMs', 'conversionMs', 'copyMs', 'totalMs'] as const;
  for (const key of durations) if (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] < 0)
    throw new Error('Invalid category extraction duration.');
  const retrievalMs = value.retrievalMs as number, conversionMs = value.conversionMs as number;
  const copyMs = value.copyMs as number, totalMs = value.totalMs as number;
  if (totalMs + 1e-6 < retrievalMs + conversionMs + copyMs || totalMs > extractionMs + 1e-6)
    throw new Error('Category extraction durations exceed their owning span.');
  if ((directFloat && copyMs !== 0) || (!directFloat && conversionMs !== 0))
    throw new Error('Category extraction durations do not match the executed path.');
  return {mode, path: expectedPath, hasUint8, hasFloat32, hasWebGLTexture,
    retrievalMs, conversionMs, copyMs, totalMs, maskPixels: pixels, ownedCategoryBytesAllocated: pixels,
    retrievedArrayBytes: directFloat ? pixels * 4 : pixels, categoryBytesCopied: directFloat ? 0 : pixels,
    categoryBytesConverted: directFloat ? pixels : 0,
    explicitFloatTemporaryBytesAvoided: directFloat ? pixels * 4 : 0,
    explicitCategoryCopyBytesAvoided: directFloat ? pixels : 0};
}
