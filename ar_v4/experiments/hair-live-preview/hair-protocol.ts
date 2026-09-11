import {getHairModel} from './models.ts';
import type {HairModel, HairModelId} from './models.ts';
export type HairDelegate = 'CPU' | 'GPU';
export type HairOutputMode = 'full' | 'category-only';

interface HairOutputBase {
  sourceSHA256: string;
  sequence: number;
  model: HairModelId;
  modelSHA256: string;
  labels: readonly string[];
  hairIndex: number;
  width: number;
  height: number;
  category: Uint8Array<ArrayBuffer>;
  inferenceMs: number;
  extractionMs: number;
  delegate?: HairDelegate;
}
export interface HairFullOutput extends HairOutputBase {outputMode?: 'full'; confidence: Float32Array<ArrayBuffer>;}
export interface HairCategoryOutput extends HairOutputBase {outputMode: 'category-only'; confidence?: never;}
export type HairRawOutput = HairFullOutput | HairCategoryOutput;

/** Root supplies detectionSHA256 only after its same-frame face result is ready. */
export type HairSegmentationResult = (HairFullOutput & {categorySHA256: string; confidenceSHA256: string})
  | (HairCategoryOutput & {categorySHA256: string; confidenceSHA256?: never});

export interface HairExpectedPair {sourceSHA256: string; sequence: number; width: number; height: number; delegate?: HairDelegate; outputMode?: HairOutputMode;}
interface Envelope {sessionNonce: string; requestId: number;}
export type HairWorkerRequest = Envelope & (
  | {type: 'initialize'; modelId: HairModelId; delegate?: HairDelegate; outputMode?: HairOutputMode}
  | {type: 'segment'; image: ImageBitmap; sourceSHA256: string; sequence: number}
);
export type HairWorkerResponse = Envelope & (
  | {type: 'ready'; model: HairModelId; modelSHA256: string; labels: string[]; hairIndex: number;
    runningMode: 'IMAGE'; delegate: HairDelegate; outputMode?: HairOutputMode; initializationMs: number}
  | {type: 'result'; output: HairRawOutput}
  | {type: 'error'; message: string}
);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
function check(condition: unknown, message: string): asserts condition { if (!condition) throw new Error(message); }
export function isHash(value: unknown): value is string { return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value); }
export function assertSourceHash(value: unknown): asserts value is string {
  check(isHash(value), 'The hair source identity must be a SHA-256 hash.');
}
export function assertSequence(value: unknown): asserts value is number {
  check(typeof value === 'number' && Number.isSafeInteger(value) && value >= 0, 'The hair frame sequence is invalid.');
}
export function assertDimensions(width: unknown, height: unknown): void {
  check(typeof width === 'number' && typeof height === 'number' && Number.isInteger(width) && Number.isInteger(height)
    && width > 0 && height > 0 && width <= 8192 && height <= 8192 && width * height <= 16_777_216,
  'The hair image dimensions are invalid or exceed the bounded preview size.');
}
export function validNonce(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(value);
}
function validateLabels(value: unknown, model: HairModel): void {
  check(Array.isArray(value) && value.length === model.labels.length
    && value.every((label, index) => label === model.labels[index]), 'The hair class labels differ from the pinned model.');
}
function duration(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) && value >= 0; }

export function validateHairReady(value: unknown, model: HairModel, delegate: HairDelegate = 'CPU', outputMode: HairOutputMode = 'full'): void {
  check(isRecord(value) && value.type === 'ready' && value.model === model.id && value.modelSHA256 === model.sha256
    && value.hairIndex === model.hairIndex && value.runningMode === 'IMAGE' && value.delegate === delegate
    && duration(value.initializationMs), `The hair worker did not initialize the pinned IMAGE/${delegate} model.`);
  validateLabels(value.labels, model);
  check((value.outputMode ?? 'full') === outputMode, 'The hair worker did not acknowledge the requested output mode.');
}

/** Copy and validate the worker boundary; no clamping, resizing or class remapping. */
export function validateHairOutput(value: unknown, model: HairModel, expected: HairExpectedPair & {outputMode: 'category-only'}): HairCategoryOutput;
export function validateHairOutput(value: unknown, model: HairModel, expected: HairExpectedPair & {outputMode?: 'full'}): HairFullOutput;
export function validateHairOutput(value: unknown, model: HairModel, expected: HairExpectedPair): HairRawOutput;
export function validateHairOutput(value: unknown, model: HairModel, expected: HairExpectedPair): HairRawOutput {
  check(isRecord(value), 'The hair worker returned no mask.');
  assertSourceHash(value.sourceSHA256); assertSequence(value.sequence); assertDimensions(value.width, value.height);
  check(value.sourceSHA256 === expected.sourceSHA256 && value.sequence === expected.sequence,
    'The hair mask belongs to another image or frame sequence.');
  check(value.model === model.id && value.modelSHA256 === model.sha256 && value.hairIndex === model.hairIndex,
    'The hair mask belongs to another model.');
  validateLabels(value.labels, model);
  check(value.width === expected.width && value.height === expected.height,
    'The returned hair mask does not match the exact source dimensions.');
  check((value.delegate ?? 'CPU') === (expected.delegate ?? 'CPU'), 'The hair output came from another inference delegate.');
  const outputMode = expected.outputMode ?? 'full';
  check((value.outputMode ?? 'full') === outputMode, 'The hair output came from another output mode.');
  check(value.category instanceof Uint8Array && value.category.length === expected.width * expected.height,
  'The hair mask storage does not match its dimensions.');
  let validCategory = true;
  for (let index = 0; index < value.category.length; index++) if (value.category[index]! >= model.labels.length) validCategory = false;
  check(validCategory, 'The hair mask contains an invalid category.');
  check(duration(value.inferenceMs) && duration(value.extractionMs), 'The hair worker returned an invalid duration.');
  const common: HairOutputBase = {sourceSHA256: value.sourceSHA256, sequence: value.sequence, model: model.id, modelSHA256: model.sha256,
    labels: model.labels, hairIndex: model.hairIndex, width: expected.width, height: expected.height,
    category: value.category.slice(), inferenceMs: value.inferenceMs, extractionMs: value.extractionMs,
    delegate: expected.delegate ?? 'CPU'};
  if (outputMode === 'category-only') {
    check(!('confidence' in value) && !('confidenceSHA256' in value), 'Category-only output must not contain confidence data or a fabricated confidence hash.');
    return {...common, outputMode: 'category-only'};
  }
  check(value.confidence instanceof Float32Array && value.confidence.length === value.category.length,
    'The hair mask storage does not match its dimensions.');
  let validConfidence = true;
  for (let index = 0; index < value.confidence.length; index++) {
    const confidence = value.confidence[index]!; if (!(confidence >= 0 && confidence <= 1)) validConfidence = false;
  }
  check(validConfidence, 'Hair confidence is nonfinite or outside the pinned range.');
  return {...common, outputMode: 'full', confidence: value.confidence.slice()};
}

export function validateHairRequest(value: unknown): HairWorkerRequest {
  check(isRecord(value) && validNonce(value.sessionNonce) && typeof value.requestId === 'number'
    && Number.isSafeInteger(value.requestId) && value.requestId > 0, 'The hair request envelope is invalid.');
  if (value.type === 'initialize') {
    const model = getHairModel(value.modelId);
    check(value.delegate === undefined || value.delegate === 'CPU' || value.delegate === 'GPU', 'Unknown hair inference delegate.');
    check(value.outputMode === undefined || value.outputMode === 'full' || value.outputMode === 'category-only', 'Unknown hair output mode.');
    return {type: 'initialize', sessionNonce: value.sessionNonce, requestId: value.requestId, modelId: model.id,
      delegate: value.delegate ?? 'CPU', outputMode: value.outputMode ?? 'full'};
  }
  check(value.type === 'segment' && typeof ImageBitmap !== 'undefined' && value.image instanceof ImageBitmap,
    'The hair request does not own an ImageBitmap.');
  assertSourceHash(value.sourceSHA256); assertSequence(value.sequence); assertDimensions(value.image.width, value.image.height);
  return {type: 'segment', sessionNonce: value.sessionNonce, requestId: value.requestId, image: value.image,
    sourceSHA256: value.sourceSHA256, sequence: value.sequence};
}

export async function sha256(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
}

/** Canonical little-endian floats match the raw still-study mask receipts. */
export function hashHairMasks(output: HairCategoryOutput): Promise<{categorySHA256: string}>;
export function hashHairMasks(output: HairFullOutput): Promise<{categorySHA256: string; confidenceSHA256: string}>;
export function hashHairMasks(output: HairRawOutput): Promise<{categorySHA256: string; confidenceSHA256?: string}>;
export async function hashHairMasks(output: HairRawOutput): Promise<{categorySHA256: string; confidenceSHA256?: string}> {
  if (output.outputMode === 'category-only') return {categorySHA256: await sha256(output.category.slice())};
  const littleEndian = new Uint8Array(new Uint32Array([0x01020304]).buffer)[0] === 4;
  const confidenceBytes = littleEndian
    ? new Uint8Array(output.confidence.buffer, output.confidence.byteOffset, output.confidence.byteLength).slice()
    : new Uint8Array(output.confidence.length * 4);
  if (!littleEndian) {
    const view = new DataView(confidenceBytes.buffer);
    for (let index = 0; index < output.confidence.length; index++) view.setFloat32(index * 4, output.confidence[index]!, true);
  }
  const [categorySHA256, confidenceSHA256] = await Promise.all([sha256(output.category.slice()), sha256(confidenceBytes)]);
  return {categorySHA256, confidenceSHA256};
}
