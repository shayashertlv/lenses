import {isRecord, validateHairRequest} from '../../hair-live-preview/hair-protocol.ts';
import type {HairExpectedPair as OriginalExpectedPair, HairRawOutput as OriginalRawOutput,
  HairSegmentationResult as OriginalSegmentationResult, HairWorkerRequest as OriginalWorkerRequest,
  HairWorkerResponse as OriginalWorkerResponse} from '../../hair-live-preview/hair-protocol.ts';
import type {CategoryExtractionMode, CategoryExtractionMetrics} from './extraction.ts';
import type {HairWorkerTiming} from './delivery.ts';
import {RGBA8_FALLBACK_REASONS} from './rgba8-extraction.ts';
import type {Rgba8FallbackReason} from './rgba8-extraction.ts';

export const CATEGORY_EXTRACTION_PROTOCOL = 'hair-category-extraction-v1';
export type {CategoryExtractionMode, CategoryExtractionMetrics} from './extraction.ts';
export type HairExpectedPair = OriginalExpectedPair & {categoryExtractionMode: CategoryExtractionMode};
export type HairRawOutput = OriginalRawOutput & {categoryExtraction: CategoryExtractionMetrics; workerTiming?: HairWorkerTiming};
export type HairSegmentationResult = OriginalSegmentationResult & {categoryExtraction: CategoryExtractionMetrics; workerTiming?: HairWorkerTiming};
export type HairWorkerRequest = Extract<OriginalWorkerRequest, {type: 'initialize'}>
  | (Extract<OriginalWorkerRequest, {type: 'segment'}> & {categoryExtractionMode: CategoryExtractionMode});
export type HairWorkerResponse = Extract<OriginalWorkerResponse, {type: 'error'}>
  | (Extract<OriginalWorkerResponse, {type: 'ready'}> & {categoryExtractionProtocol: typeof CATEGORY_EXTRACTION_PROTOCOL})
  | (Omit<Extract<OriginalWorkerResponse, {type: 'result'}>, 'output'> & {output: HairRawOutput});

export function assertCategoryExtractionMode(value: unknown): asserts value is CategoryExtractionMode {
  if (value !== 'sdk' && value !== 'direct' && value !== 'rgba8') throw new Error('Unknown category extraction mode.');
}

/** Optional for historical fixtures; present extensions must describe an owned span. */
export function validateHairWorkerTiming(value: unknown, inferenceMs: number, extractionMs: number): HairWorkerTiming | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value) || typeof value.inputValidationMs !== 'number' || !Number.isFinite(value.inputValidationMs)
    || value.inputValidationMs < 0 || typeof value.totalMs !== 'number' || !Number.isFinite(value.totalMs)
    || value.totalMs < 0 || value.totalMs + 1e-6 < value.inputValidationMs + inferenceMs + extractionMs)
    throw new Error('Invalid hair worker timing span.');
  return {inputValidationMs: value.inputValidationMs, totalMs: value.totalMs};
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
  const gpuBytes = mode === 'rgba8' && path === 'rgba8-readback';
  const converted = directFloat || gpuBytes;
  const expectedPath = mode === 'rgba8' ? gpuBytes ? 'rgba8-readback' : 'rgba8-sdk-fallback'
    : mode === 'sdk' ? 'sdk-copy' : directFloat ? 'direct-float-conversion' : 'direct-byte-copy';
  if (path !== expectedPath) throw new Error('The category extraction path does not match its requested mode and original representation.');
  const pixels = expected.width * expected.height;
  let rgba8: Partial<CategoryExtractionMetrics> = {};
  if (mode === 'rgba8') {
    if (typeof value.rgba8WorkMs !== 'number' || !Number.isFinite(value.rgba8WorkMs) || value.rgba8WorkMs < 0
      || typeof value.rgba8ResourcesReused !== 'boolean' || value.rgba8ReadbackBytes !== (gpuBytes ? pixels * 4 : 0))
      throw new Error('Invalid RGBA8 extraction measurements.');
    const reason = value.rgba8FallbackReason;
    if (gpuBytes ? (reason !== null || hasUint8 || hasFloat32 || !hasWebGLTexture || expected.outputMode === 'full')
      : !RGBA8_FALLBACK_REASONS.includes(reason as Rgba8FallbackReason))
      throw new Error('Invalid RGBA8 extraction disposition.');
    if (reason === 'not-gpu-only' && !hasUint8 && !hasFloat32 && hasWebGLTexture)
      throw new Error('RGBA8 fallback does not match the original representation.');
    if (reason === 'full-output' && expected.outputMode !== 'full')
      throw new Error('RGBA8 full-output fallback does not match the request.');
    rgba8 = {rgba8WorkMs: value.rgba8WorkMs, rgba8ReadbackBytes: value.rgba8ReadbackBytes as number,
      rgba8ResourcesReused: value.rgba8ResourcesReused, rgba8FallbackReason: reason as Rgba8FallbackReason | null};
  } else if (Object.keys(value).some(key => key.startsWith('rgba8'))) throw new Error('Unexpected RGBA8 extraction fields.');
  if (value.maskPixels !== pixels || value.ownedCategoryBytesAllocated !== pixels
    || value.retrievedArrayBytes !== (converted ? pixels * 4 : pixels)
    || value.categoryBytesCopied !== (converted ? 0 : pixels)
    || value.categoryBytesConverted !== (converted ? pixels : 0)
    || value.explicitFloatTemporaryBytesAvoided !== (converted ? pixels * 4 : 0)
    || value.explicitCategoryCopyBytesAvoided !== (converted ? pixels : 0)) throw new Error('Invalid category extraction dimensions or allocation counters.');
  const durations = ['retrievalMs', 'conversionMs', 'copyMs', 'totalMs'] as const;
  for (const key of durations) if (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] < 0)
    throw new Error('Invalid category extraction duration.');
  const retrievalMs = value.retrievalMs as number, conversionMs = value.conversionMs as number;
  const copyMs = value.copyMs as number, totalMs = value.totalMs as number;
  if (totalMs + 1e-6 < retrievalMs + conversionMs + copyMs || totalMs > extractionMs + 1e-6)
    throw new Error('Category extraction durations exceed their owning span.');
  if (rgba8.rgba8WorkMs !== undefined && rgba8.rgba8WorkMs > totalMs + 1e-6)
    throw new Error('RGBA8 extraction work exceeds its owning span.');
  if ((converted && copyMs !== 0) || (!converted && conversionMs !== 0))
    throw new Error('Category extraction durations do not match the executed path.');
  return {mode, path: expectedPath, hasUint8, hasFloat32, hasWebGLTexture,
    retrievalMs, conversionMs, copyMs, totalMs, maskPixels: pixels, ownedCategoryBytesAllocated: pixels,
    retrievedArrayBytes: converted ? pixels * 4 : pixels, categoryBytesCopied: converted ? 0 : pixels,
    categoryBytesConverted: converted ? pixels : 0,
    explicitFloatTemporaryBytesAvoided: converted ? pixels * 4 : 0,
    explicitCategoryCopyBytesAvoided: converted ? pixels : 0, ...rgba8};
}
