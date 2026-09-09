import {validateProtection} from '../temple-sagittal/contracts.ts';
import type {PixelRect, ProtectionConfiguration} from '../temple-sagittal/contracts.ts';

export interface PairIdentity {sourceSHA256: string; detectionSHA256: string; eyewearModel: string;}
export interface HairMask {
  sourceSHA256: string; detectionSHA256: string; model: string; modelSHA256: string;
  categorySHA256: string; confidenceSHA256: string; labels: readonly string[]; hairIndex: number;
  category: Uint8Array; confidence: Float32Array; width: number; height: number;
}
export interface HairArmInput {
  width: number; height: number; before: Uint8ClampedArray; background: Uint8ClampedArray;
  pair: PairIdentity; geometryPair: PairIdentity; mask: HairMask;
  expectedModel: {id: string; sha256: string; labels: readonly string[]; hairIndex: number};
  protection: ProtectionConfiguration; noseRoi: PixelRect;
}
export interface PixelCheck {testedPixels: number; changedPixels: number; maxDelta: number;}
export interface HairArmResult {
  pixels: Uint8ClampedArray; weights: Float32Array; fallbackReason: string | null;
  backgroundReferenceCheck: PixelCheck;
  statistics: {changedPixels: number; renderResidualPixels: number; eligiblePixels: number; eligibleResidualPixels: number;
    hairEligibleResidualPixels: number; fullReplacementPixels: number; featheredPixels: number;
    protectedCorridorHairResidualPixels: number; hairOverProtectedRenderResidualPixels: number;};
}
export const HAIR_ARM_POLICY = Object.freeze({method: 'hair-arm-camera-copy-v1', inwardFeatherRenderPixels: 2,
  categoryRule: 'category winner equals pinned hairIndex', background: 'exact native sRGB camera pass',
  confidence: 'validated and preserved, not used as alpha or depth',
  protection: 'all saved protected rectangles plus the saved nasal ROI copied from accepted last'});
const check = (value: unknown, message: string): void => { if (!value) throw new Error(message); };
const isHash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
export const inside = (rect: PixelRect, x: number, y: number): boolean => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;
const rectangle = (rect: PixelRect, width: number, height: number): boolean => !!rect
  && [rect.x0, rect.y0, rect.x1, rect.y1].every(Number.isInteger) && rect.x0 >= 0 && rect.y0 >= 0
  && rect.x1 <= width && rect.y1 <= height && rect.x1 > rect.x0 && rect.y1 > rect.y0;
const differs = (a: Uint8ClampedArray, b: Uint8ClampedArray, offset: number): boolean =>
  a[offset] !== b[offset] || a[offset + 1] !== b[offset + 1] || a[offset + 2] !== b[offset + 2] || a[offset + 3] !== b[offset + 3];

export function comparePixels(before: Uint8ClampedArray, after: Uint8ClampedArray, width: number, height: number,
  include: (x: number, y: number) => boolean = () => true): PixelCheck {
  check(before.length === width * height * 4 && after.length === before.length, 'Comparison sizes differ.');
  const result: PixelCheck = {testedPixels: 0, changedPixels: 0, maxDelta: 0};
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (!include(x, y)) continue; result.testedPixels++;
    const offset = (y * width + x) * 4; let maxDelta = 0;
    for (let channel = 0; channel < 4; channel++) maxDelta = Math.max(maxDelta, Math.abs(before[offset + channel]! - after[offset + channel]!));
    if (maxDelta) result.changedPixels++; result.maxDelta = Math.max(result.maxDelta, maxDelta);
  }
  return result;
}

/** Category boundaries are a heuristic display feather, not physical strand alpha. */
export function inwardWeights(category: Uint8Array, maskWidth: number, maskHeight: number, hairIndex: number,
  width: number, height: number): Float32Array {
  const distance = new Uint16Array(width * height), weights = new Float32Array(width * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const mx = Math.min(maskWidth - 1, Math.floor((x + .5) * maskWidth / width));
    const my = Math.min(maskHeight - 1, Math.floor((y + .5) * maskHeight / height));
    if (category[my * maskWidth + mx] !== hairIndex) continue;
    const index = y * width + x;
    const left = x ? distance[index - 1]! : 0, above = y ? distance[index - width]! : 0;
    distance[index] = Math.min(3, Math.min(left, above) + 1);
  }
  for (let y = height - 1; y >= 0; y--) for (let x = width - 1; x >= 0; x--) {
    const index = y * width + x; if (!distance[index]) continue;
    const right = x < width - 1 ? distance[index + 1]! : 0, below = y < height - 1 ? distance[index + width]! : 0;
    distance[index] = Math.min(distance[index]!, Math.min(right, below) + 1);
    weights[index] = Math.min(1, distance[index]! / HAIR_ARM_POLICY.inwardFeatherRenderPixels);
  }
  return weights;
}

/** Pure, owned, fail-closed late composition. It never retains another frame's mask. */
export function composeHairArms(input: HairArmInput): HairArmResult {
  const {width, height, before, background} = input;
  check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
    && before instanceof Uint8ClampedArray && before.length === width * height * 4, 'The accepted output itself is invalid.');
  const result: HairArmResult = {pixels: before.slice(), weights: new Float32Array(width * height), fallbackReason: null,
    backgroundReferenceCheck: {testedPixels: 0, changedPixels: 0, maxDelta: 0},
    statistics: {changedPixels: 0, renderResidualPixels: 0, eligiblePixels: 0, eligibleResidualPixels: 0,
      hairEligibleResidualPixels: 0, fullReplacementPixels: 0, featheredPixels: 0,
      protectedCorridorHairResidualPixels: 0, hairOverProtectedRenderResidualPixels: 0}};
  try {
    check(background instanceof Uint8ClampedArray && background.length === before.length, 'Clean camera output dimensions differ.');
    for (const pair of [input.pair, input.geometryPair]) check(pair && isHash(pair.sourceSHA256) && isHash(pair.detectionSHA256)
      && ['tom-ford-clear', 'amber-horizon'].includes(pair.eyewearModel), 'The image/geometry identity is invalid.');
    check(input.pair.sourceSHA256 === input.geometryPair.sourceSHA256 && input.pair.detectionSHA256 === input.geometryPair.detectionSHA256
      && input.pair.eyewearModel === input.geometryPair.eyewearModel, 'The saved geometry belongs to a different image, detection, or frame model.');
    const {mask, expectedModel} = input;
    check(mask && mask.sourceSHA256 === input.pair.sourceSHA256 && mask.detectionSHA256 === input.pair.detectionSHA256,
      'The hair mask belongs to another image/detection pair.');
    check(expectedModel && isHash(expectedModel.sha256) && mask.model === expectedModel.id && mask.modelSHA256 === expectedModel.sha256
      && isHash(mask.categorySHA256) && isHash(mask.confidenceSHA256), 'The pinned segmentation model or mask identity differs.');
    check(JSON.stringify(mask.labels) === JSON.stringify(expectedModel.labels) && mask.hairIndex === expectedModel.hairIndex
      && Number.isInteger(mask.hairIndex) && mask.hairIndex >= 0 && mask.hairIndex < mask.labels.length
      && mask.labels[mask.hairIndex]?.toLowerCase().includes('hair'), 'The category/hair label mapping differs.');
    check(Number.isInteger(mask.width) && Number.isInteger(mask.height) && mask.width > 0 && mask.height > 0
      && mask.category instanceof Uint8Array && mask.confidence instanceof Float32Array
      && mask.category.length === mask.width * mask.height && mask.confidence.length === mask.category.length, 'The saved mask storage/dimensions differ.');
    check(mask.category.every(value => value < mask.labels.length), 'An output category exceeds the model labels.');
    check(mask.confidence.every(value => Number.isFinite(value) && value >= 0 && value <= 1), 'Hair confidence is nonfinite or outside its pinned range.');
    validateProtection(input.protection, width, height);
    check(rectangle(input.noseRoi, width, height), 'The saved independent nasal ROI is missing or invalid.');
    const protects = (x: number, y: number): boolean => inside(input.noseRoi, x, y) || input.protection.protectedRects.some(rect => inside(rect, x, y));
    const corridor = (x: number, y: number): boolean => input.protection.editableRects.some(rect => inside(rect, x, y));
    result.backgroundReferenceCheck = comparePixels(before, background, width, height, (x, y) => !protects(x, y) && !corridor(x, y));
    check(result.backgroundReferenceCheck.testedPixels > 0 && result.backgroundReferenceCheck.changedPixels === 0,
      'The clean native camera pass is not byte-exact outside the saved eyewear bounds.');
    const weights = inwardWeights(mask.category, mask.width, mask.height, mask.hairIndex, width, height);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const index = y * width + x, offset = index * 4, protectedPixel = protects(x, y), inCorridor = corridor(x, y);
      const residual = differs(before, background, offset), hair = weights[index]! > 0;
      result.statistics.renderResidualPixels += Number(residual);
      if (protectedPixel) {
        result.statistics.hairOverProtectedRenderResidualPixels += Number(residual && hair);
        result.statistics.protectedCorridorHairResidualPixels += Number(residual && hair && inCorridor); continue;
      }
      if (!inCorridor) continue; result.statistics.eligiblePixels++;
      if (!residual) continue; result.statistics.eligibleResidualPixels++;
      if (!hair) continue; result.statistics.hairEligibleResidualPixels++;
      const weight = weights[index]!;
      for (let channel = 0; channel < 3; channel++) result.pixels[offset + channel] = Math.round(before[offset + channel]! * (1 - weight) + background[offset + channel]! * weight);
      result.pixels[offset + 3] = before[offset + 3]!;
      if (!differs(before, result.pixels, offset)) continue;
      result.weights[index] = weight; result.statistics.changedPixels++;
      if (weight === 1) result.statistics.fullReplacementPixels++; else result.statistics.featheredPixels++;
    }
    // Nothing is rendered or color-converted after this authoritative final copy.
    for (const rect of [...input.protection.protectedRects, input.noseRoi]) for (let y = rect.y0; y < rect.y1; y++) {
      const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
      result.pixels.set(before.subarray(start, end), start);
    }
  } catch (error) { result.pixels.set(before); result.weights.fill(0); result.statistics.changedPixels = 0;
    result.fallbackReason = error instanceof Error ? error.message : String(error); }
  return result;
}
