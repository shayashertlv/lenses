import {CompositionOutputPool} from './output-pool.ts';
import type {CompositionOutputLease} from './output-pool.ts';
import {HAIR_ARM_POLICY} from '../../hair-arm-preview/compose.ts';
import type {HairArmResult, PixelCheck} from '../../hair-arm-preview/compose.ts';
import type {LiveHairArmInput as HairArmInput} from '../../hair-live-preview/live-mask.ts';
import {validateProtection} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {PixelRect} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

const OPTICAL = 1, NASAL = 2, CORRIDOR = 4, PROTECTED = OPTICAL | NASAL;
const check = (value: unknown, message: string): void => { if (!value) throw new Error(message); };
const isHash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const emptyCheck = (): PixelCheck => ({testedPixels: 0, changedPixels: 0, maxDelta: 0});
const emptyStatistics = (): HairArmResult['statistics'] => ({changedPixels: 0, renderResidualPixels: 0, eligiblePixels: 0,
  eligibleResidualPixels: 0, hairEligibleResidualPixels: 0, fullReplacementPixels: 0, featheredPixels: 0,
  protectedCorridorHairResidualPixels: 0, hairOverProtectedRenderResidualPixels: 0});
const rectangle = (rect: PixelRect, width: number, height: number): boolean => !!rect
  && [rect.x0, rect.y0, rect.x1, rect.y1].every(Number.isInteger) && rect.x0 >= 0 && rect.y0 >= 0
  && rect.x1 <= width && rect.y1 <= height && rect.x1 > rect.x0 && rect.y1 > rect.y0;
const differs = (a: Uint8ClampedArray, b: Uint8ClampedArray, offset: number): boolean =>
  a[offset] !== b[offset] || a[offset + 1] !== b[offset + 1] || a[offset + 2] !== b[offset + 2] || a[offset + 3] !== b[offset + 3];
const delta = (a: Uint8ClampedArray, b: Uint8ClampedArray, offset: number): number => Math.max(
  Math.abs(a[offset]! - b[offset]!), Math.abs(a[offset + 1]! - b[offset + 1]!),
  Math.abs(a[offset + 2]! - b[offset + 2]!), Math.abs(a[offset + 3]! - b[offset + 3]!));
const record = (value: PixelCheck, difference: number): void => {
  value.testedPixels++;
  if (difference) { value.changedPixels++; value.maxDelta = Math.max(value.maxDelta, difference); }
};

/** Compile overlapping rectangles once, keeping optical and independent nasal checks distinct. */
function regionsFor(input: HairArmInput, storage?: Uint8Array): Uint8Array {
  const regions = storage ?? new Uint8Array(input.width * input.height);
  if (storage) regions.fill(0);
  const add = (rect: PixelRect, bit: number): void => {
    for (let y = rect.y0; y < rect.y1; y++) {
      const end = y * input.width + rect.x1;
      for (let index = y * input.width + rect.x0; index < end; index++) regions[index] = regions[index]! | bit;
    }
  };
  for (const rect of input.protection.protectedRects) add(rect, OPTICAL);
  for (const rect of input.protection.editableRects) add(rect, CORRIDOR);
  add(input.noseRoi, NASAL);
  return regions;
}

function validate(input: HairArmInput): void {
  const {width, height, before, background, mask, expectedModel} = input;
  check(background instanceof Uint8ClampedArray && background.length === before.length, 'Clean camera output dimensions differ.');
  for (const pair of [input.pair, input.geometryPair]) check(pair && isHash(pair.sourceSHA256) && isHash(pair.detectionSHA256)
    && ['tom-ford-clear', 'amber-horizon'].includes(pair.eyewearModel), 'The image/geometry identity is invalid.');
  check(input.pair.sourceSHA256 === input.geometryPair.sourceSHA256 && input.pair.detectionSHA256 === input.geometryPair.detectionSHA256
    && input.pair.eyewearModel === input.geometryPair.eyewearModel, 'The saved geometry belongs to a different image, detection, or frame model.');
  check(mask && mask.sourceSHA256 === input.pair.sourceSHA256 && mask.detectionSHA256 === input.pair.detectionSHA256,
    'The hair mask belongs to another image/detection pair.');
  check(mask.outputMode === undefined || mask.outputMode === 'full' || mask.outputMode === 'category-only', 'The hair output mode is invalid.');
  check(expectedModel && isHash(expectedModel.sha256) && mask.model === expectedModel.id && mask.modelSHA256 === expectedModel.sha256
    && isHash(mask.categorySHA256) && (mask.outputMode === 'category-only' || isHash(mask.confidenceSHA256)),
  'The pinned segmentation model or mask identity differs.');
  check(JSON.stringify(mask.labels) === JSON.stringify(expectedModel.labels) && mask.hairIndex === expectedModel.hairIndex
    && Number.isInteger(mask.hairIndex) && mask.hairIndex >= 0 && mask.hairIndex < mask.labels.length
    && mask.labels[mask.hairIndex]?.toLowerCase().includes('hair'), 'The category/hair label mapping differs.');
  check(Number.isInteger(mask.width) && Number.isInteger(mask.height) && mask.width > 0 && mask.height > 0
    && mask.category instanceof Uint8Array && mask.category.length === mask.width * mask.height
    && (mask.outputMode === 'category-only' ? mask.confidence === undefined && mask.confidenceSHA256 === undefined
      : mask.confidence instanceof Float32Array && mask.confidence.length === mask.category.length), 'The saved mask storage/dimensions differ.');
  // Preserve validation ordering even if both arrays are invalid; no callbacks or repeated scans.
  let validCategory = true, validConfidence = true;
  for (let index = 0; index < mask.category.length; index++) {
    if (mask.category[index]! >= mask.labels.length) validCategory = false;
    if (mask.outputMode !== 'category-only') {
      const confidence = mask.confidence[index]!;
      if (!(confidence >= 0 && confidence <= 1)) validConfidence = false;
    }
  }
  check(validCategory, 'An output category exceeds the model labels.');
  check(validConfidence, 'Hair confidence is nonfinite or outside its pinned range.');
  validateProtection(input.protection, width, height);
  check(rectangle(input.noseRoi, width, height), 'The saved independent nasal ROI is missing or invalid.');
}

export interface ReviewComposeStats {
  requested: boolean; used: boolean; scanRestrictionUsed: boolean;
  residualScannedPixels: number; compositionScannedPixels: number; backgroundScannedPixels: number;
  finalAuditScannedPixels: number; outputInitialCopyPixels: number;
  outputBufferBytesAllocated: number; outputBufferReused: boolean; outputPoolFallbackReason: string | null;
  outputPooledLeases: number; outputOwnershipCopyBytes: number; spanBytesAllocated: number; spanStorageReused: boolean;
  wordFallbackReason: string | null; fallbackReason: string | null;
}
export const emptyReviewComposeStats = (requested = false): ReviewComposeStats => ({
  requested, used: false, scanRestrictionUsed: false, residualScannedPixels: 0, compositionScannedPixels: 0,
  backgroundScannedPixels: 0, finalAuditScannedPixels: 0, outputInitialCopyPixels: 0,
  outputBufferBytesAllocated: 0, outputBufferReused: false, outputPoolFallbackReason: null,
  outputPooledLeases: 0, outputOwnershipCopyBytes: 0, spanBytesAllocated: 0, spanStorageReused: false,
  wordFallbackReason: null, fallbackReason: null,
});
export interface ReviewHairArmResult extends HairArmResult {
  outputLease: CompositionOutputLease; reviewCompose: ReviewComposeStats;
  regions: Uint8Array | null; eligibleResidualIndices: Uint32Array | null;
  wordComparisonRequested: boolean; wordComparisonUsed: boolean; wordComparedPixels: number;
}
export interface HairProtectionChecks {
  protectedCheck: PixelCheck; noseCheck: PixelCheck; outsideEditableCheck: PixelCheck; backgroundPreservationCheck: PixelCheck;
}

export interface CompositionAllocationStats {
  regionBytesAllocated: number;
  coordinateBytesAllocated: number;
  regionsReused: boolean;
  coordinatesReused: boolean;
}

/** Private scratch only. A returned membership view is borrowed until the next
 * compose call using this owner; finish must complete its final audit first.
 * Output pixels belong to a separate explicit lease; weights are independently owned. */
export class ReviewCompositionScratch {
  private regions = new Uint8Array(0);
  private rowBounds = new Uint32Array(0);
  private spanAllocation = {spanBytesAllocated: 0, spanStorageReused: false};
  private xs = new Uint32Array(0);
  private ys = new Uint32Array(0);
  private maskWidth = 0;
  private maskHeight = 0;
  private allocation: CompositionAllocationStats = {regionBytesAllocated: 0, coordinateBytesAllocated: 0,
    regionsReused: false, coordinatesReused: false};

  get lastSpanAllocation(): {spanBytesAllocated: number; spanStorageReused: boolean} { return {...this.spanAllocation}; }

  acquireRowBounds(input: HairArmInput): Uint32Array {
    const spanStorageReused = this.rowBounds.length === input.height * 2;
    if (!spanStorageReused) this.rowBounds = new Uint32Array(input.height * 2);
    this.spanAllocation = {spanStorageReused, spanBytesAllocated: spanStorageReused ? 0 : this.rowBounds.byteLength};
    return rowBoundsFor(input, this.rowBounds);
  }

  get lastAllocation(): CompositionAllocationStats { return {...this.allocation}; }

  acquire(width: number, height: number, maskWidth: number, maskHeight: number): {
    regions: Uint8Array; xs: Uint32Array; ys: Uint32Array;
  } {
    const regionsReused = this.regions.length === width * height;
    const coordinatesReused = this.xs.length === width && this.ys.length === height
      && this.maskWidth === maskWidth && this.maskHeight === maskHeight;
    if (!regionsReused) this.regions = new Uint8Array(width * height);
    let coordinateBytesAllocated = 0;
    if (this.xs.length !== width) { this.xs = new Uint32Array(width); coordinateBytesAllocated += this.xs.byteLength; }
    if (this.ys.length !== height) { this.ys = new Uint32Array(height); coordinateBytesAllocated += this.ys.byteLength; }
    if (!coordinatesReused) {
      for (let x = 0; x < width; x++) this.xs[x] = Math.min(maskWidth - 1, Math.floor((x + .5) * maskWidth / width));
      for (let y = 0; y < height; y++) this.ys[y] = Math.min(maskHeight - 1, Math.floor((y + .5) * maskHeight / height)) * maskWidth;
      this.maskWidth = maskWidth; this.maskHeight = maskHeight;
    }
    this.allocation = {regionBytesAllocated: regionsReused ? 0 : this.regions.byteLength,
      coordinateBytesAllocated, regionsReused, coordinatesReused};
    return {regions: this.regions, xs: this.xs, ys: this.ys};
  }

  clear(): void {
    this.rowBounds = new Uint32Array(0); this.spanAllocation = {spanBytesAllocated: 0, spanStorageReused: false};
    this.regions = new Uint8Array(0); this.xs = new Uint32Array(0); this.ys = new Uint32Array(0);
    this.maskWidth = this.maskHeight = 0;
    this.allocation = {regionBytesAllocated: 0, coordinateBytesAllocated: 0, regionsReused: false, coordinatesReused: false};
  }
}

/** Build one conservative span per row containing every saved region. Pixels
 * outside it still receive complete RGBA residual/reference checks; only the
 * category, membership and feather work is restricted to the span. */
function rowBoundsFor(input: HairArmInput, bounds = new Uint32Array(input.height * 2)): Uint32Array {
  for (let y = 0; y < input.height; y++) { bounds[y * 2] = input.width; bounds[y * 2 + 1] = 0; }
  for (const rect of [...input.protection.protectedRects, ...input.protection.editableRects, input.noseRoi]) {
    for (let y = rect.y0; y < rect.y1; y++) {
      bounds[y * 2] = Math.min(bounds[y * 2]!, rect.x0);
      bounds[y * 2 + 1] = Math.max(bounds[y * 2 + 1]!, rect.x1);
    }
  }
  for (let y = 0; y < input.height; y++) if (bounds[y * 2 + 1] === 0) bounds[y * 2 + 1] = input.width;
  return bounds;
}

export interface ReviewHairOptions {
  outputPool?: CompositionOutputPool;
  signal?: AbortSignal;
  collectEligibleResidualIndices?: boolean;
  /** Diagnostic weight maps remain enabled by default; live rendering never consumes them. */
  collectWeights?: boolean;
  scratch?: ReviewCompositionScratch;
}

/** Same frozen category rule and two-pixel inward feather; no full-frame distance field. */
export function composeHairArmsReview(input: HairArmInput, options: ReviewHairOptions = {}): ReviewHairArmResult {
  const {width, height, before, background} = input;
  check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
    && before instanceof Uint8ClampedArray && before.length === width * height * 4, 'The accepted output itself is invalid.');
  if (options.signal?.aborted) throw new DOMException('Composition was cancelled before claiming output.', 'AbortError');
  const collectWeights = options.collectWeights !== false;
  const outputLease = (options.outputPool ?? new CompositionOutputPool()).acquire(before);
  const reviewCompose = {...emptyReviewComposeStats(true), outputInitialCopyPixels: width * height,
    outputBufferBytesAllocated: outputLease.bytesAllocated, outputBufferReused: outputLease.reused,
    outputPoolFallbackReason: outputLease.fallbackReason, outputPooledLeases: outputLease.pooledLeases};
  Object.defineProperty(reviewCompose, 'outputOwnershipCopyBytes', {enumerable: true, get: () => outputLease.copiedBytes});
  const result: ReviewHairArmResult = {pixels: outputLease.pixels, outputLease, reviewCompose, weights: new Float32Array(collectWeights ? width * height : 0), fallbackReason: null,
    backgroundReferenceCheck: emptyCheck(), statistics: emptyStatistics(), regions: null, eligibleResidualIndices: null,
    wordComparisonRequested: true, wordComparisonUsed: false, wordComparedPixels: 0};
  const eligibleResidualIndices: number[] | null = options.collectEligibleResidualIndices ? [] : null;
  let referenceValidated = false;
  try {
    validate(input);
    check(HAIR_ARM_POLICY.inwardFeatherRenderPixels === 2, 'The exact fast feather requires the frozen two-pixel policy.');
    const {mask} = input, category = mask.category, hairIndex = mask.hairIndex;
    const scratch = options.scratch?.acquire(width, height, mask.width, mask.height);
    const regions = regionsFor(input, scratch?.regions); result.regions = regions;
    const xs = scratch?.xs ?? new Uint32Array(width), ys = scratch?.ys ?? new Uint32Array(height);
    if (!scratch) {
      for (let x = 0; x < width; x++) xs[x] = Math.min(mask.width - 1, Math.floor((x + .5) * mask.width / width));
      for (let y = 0; y < height; y++) ys[y] = Math.min(mask.height - 1, Math.floor((y + .5) * mask.height / height)) * mask.width;
    }
    const statistics = result.statistics, reference = result.backgroundReferenceCheck;
    // Word equality preserves alpha and is independent of endianness. Arbitrary
    // caller subarrays and shared storage retain the original byte comparisons.
    const wordComparison = before.byteOffset % 4 === 0 && background.byteOffset % 4 === 0
      && before.buffer instanceof ArrayBuffer && background.buffer instanceof ArrayBuffer;
    const beforeWords = wordComparison ? new Uint32Array(before.buffer, before.byteOffset, width * height) : null;
    const backgroundWords = wordComparison ? new Uint32Array(background.buffer, background.byteOffset, width * height) : null;
    const rowBounds = options.scratch?.acquireRowBounds(input) ?? rowBoundsFor(input);
    Object.assign(reviewCompose, options.scratch?.lastSpanAllocation
      ?? {spanBytesAllocated: rowBounds.byteLength, spanStorageReused: false});
    if (!wordComparison) reviewCompose.wordFallbackReason = 'Unaligned or shared source storage; using exact four-byte comparisons.';
    const scanBackground = (start: number, end: number): void => {
      reviewCompose.backgroundScannedPixels += end - start;
      reference.testedPixels += end - start;
      for (let index = start; index < end; index++) {
        const offset = index * 4;
        const residual = beforeWords ? beforeWords[index] !== backgroundWords![index] : differs(before, background, offset);
        if (residual) {
          statistics.renderResidualPixels++; reference.changedPixels++;
          reference.maxDelta = Math.max(reference.maxDelta, delta(before, background, offset));
        }
      }
    };
    for (let y = 0; y < height; y++) {
      const x0 = rowBounds[y * 2]!, x1 = rowBounds[y * 2 + 1]!;
      scanBackground(y * width, y * width + x0);
      reviewCompose.compositionScannedPixels += x1 - x0;
      for (let x = x0; x < x1; x++) {
        const index = y * width + x, offset = index * 4, region = regions[index]!;
        const residual = beforeWords ? beforeWords[index] !== backgroundWords![index] : differs(before, background, offset);
        if (!region) record(reference, residual ? delta(before, background, offset) : 0);
        statistics.renderResidualPixels += Number(residual);
        if (region & PROTECTED) {
          if (residual && category[ys[y]! + xs[x]!] === hairIndex) {
            statistics.hairOverProtectedRenderResidualPixels++;
            if (region & CORRIDOR) statistics.protectedCorridorHairResidualPixels++;
          }
          continue;
        }
        if (!(region & CORRIDOR)) continue;
        statistics.eligiblePixels++;
        if (!residual) continue;
        statistics.eligibleResidualPixels++;
        eligibleResidualIndices?.push(index);
        if (category[ys[y]! + xs[x]!] !== hairIndex) continue;
        statistics.hairEligibleResidualPixels++;
        // At radius two, the clipped Manhattan transform is .5 at a four-neighbour
        // category/image edge and 1 elsewhere. Only actual eligible residuals need it.
        const weight = x === 0 || y === 0 || x === width - 1 || y === height - 1
          || category[ys[y]! + xs[x - 1]!] !== hairIndex || category[ys[y]! + xs[x + 1]!] !== hairIndex
          || category[ys[y - 1]! + xs[x]!] !== hairIndex || category[ys[y + 1]! + xs[x]!] !== hairIndex ? .5 : 1;
        for (let channel = 0; channel < 3; channel++)
          result.pixels[offset + channel] = Math.round(before[offset + channel]! * (1 - weight) + background[offset + channel]! * weight);
        if (!differs(before, result.pixels, offset)) continue;
        if (collectWeights) result.weights[index] = weight;
        statistics.changedPixels++;
        if (weight === 1) statistics.fullReplacementPixels++; else statistics.featheredPixels++;
      }
      scanBackground(y * width + x1, (y + 1) * width);
    }
    reviewCompose.used = true;
    reviewCompose.residualScannedPixels = width * height;
    reviewCompose.scanRestrictionUsed = reviewCompose.compositionScannedPixels < width * height;
    // Report completed scans even when the following reference check rejects
    // the image; invalid inputs rejected before scanning report no word work.
    result.wordComparisonUsed = wordComparison;
    result.wordComparedPixels = wordComparison ? width * height : 0;
    check(reference.testedPixels > 0 && reference.changedPixels === 0,
      'The clean native camera pass is not byte-exact outside the saved eyewear bounds.');
    referenceValidated = true;
    if (eligibleResidualIndices) result.eligibleResidualIndices = Uint32Array.from(eligibleResidualIndices);
    // Preserve the authoritative final accepted copy, including overlapping guards.
    for (const rect of [...input.protection.protectedRects, input.noseRoi]) for (let y = rect.y0; y < rect.y1; y++) {
      const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
      result.pixels.set(before.subarray(start, end), start);
    }
  } catch (error) {
    result.pixels.set(before); result.weights.fill(0); result.statistics.changedPixels = 0;
    // Frozen composition does not begin statistics until the reference passes.
    if (!referenceValidated) result.statistics = emptyStatistics();
    result.fallbackReason = error instanceof Error ? error.message : String(error);
  }
  reviewCompose.fallbackReason = result.fallbackReason;
  return result;
}
