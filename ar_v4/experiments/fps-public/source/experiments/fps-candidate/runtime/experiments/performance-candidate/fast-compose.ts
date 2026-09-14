import {isPairedIdentity} from '../../frame-identity.ts';
import {HAIR_ARM_POLICY} from '../hair-arm-preview/compose.ts';
import type {HairArmResult, PixelCheck} from '../hair-arm-preview/compose.ts';
import type {LiveHairArmInput as HairArmInput} from '../hair-live-preview/live-mask.ts';
import {validateProtection} from '../../../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {PixelRect} from '../../../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

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
  for (const pair of [input.pair, input.geometryPair]) check(pair && isPairedIdentity(pair.sourceIdentity, pair.detectionIdentity)
    && ['tom-ford-clear', 'amber-horizon'].includes(pair.eyewearModel), 'The image/geometry identity is invalid.');
  check(input.pair.sourceIdentity === input.geometryPair.sourceIdentity && input.pair.detectionIdentity === input.geometryPair.detectionIdentity
    && input.pair.eyewearModel === input.geometryPair.eyewearModel, 'The saved geometry belongs to a different image, detection, or frame model.');
  check(mask && mask.sourceIdentity === input.pair.sourceIdentity && mask.detectionIdentity === input.pair.detectionIdentity,
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

export interface CpuCompositionStats {
  requested: true;
  used: boolean;
  wordComparisonUsed: boolean;
  auditedReferencePixels: number;
  backgroundOnlyPixels: number;
  rgbWritePixels: number;
  finalAuditPixels: number;
}
export interface FastHairArmResult extends HairArmResult {
  regions: Uint8Array | null; eligibleResidualIndices: Uint32Array | null;
  cpuComposition?: CpuCompositionStats;
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
 * Final pixels and weights are never pooled or borrowed from this storage. */
export class CompositionScratch {
  private regions = new Uint8Array(0);
  private xs = new Uint32Array(0);
  private ys = new Uint32Array(0);
  private maskWidth = 0;
  private maskHeight = 0;
  private allocation: CompositionAllocationStats = {regionBytesAllocated: 0, coordinateBytesAllocated: 0,
    regionsReused: false, coordinatesReused: false};

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
    this.regions = new Uint8Array(0); this.xs = new Uint32Array(0); this.ys = new Uint32Array(0);
    this.maskWidth = this.maskHeight = 0;
    this.allocation = {regionBytesAllocated: 0, coordinateBytesAllocated: 0, regionsReused: false, coordinatesReused: false};
  }
}

export interface FastHairOptions {
  collectEligibleResidualIndices?: boolean;
  /** Diagnostic weight maps remain enabled by default; live rendering never consumes them. */
  collectWeights?: boolean;
  scratch?: CompositionScratch;
  /** Isolated round-two candidate. The default retains the preceding exact path. */
  cpuCompose?: boolean;
}

/** Same frozen category rule and two-pixel inward feather; no full-frame distance field. */
export function composeHairArmsFast(input: HairArmInput, options: FastHairOptions = {}): FastHairArmResult {
  if (options.cpuCompose) return composeHairArmsCpu(input, options);
  const {width, height, before, background} = input;
  check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
    && before instanceof Uint8ClampedArray && before.length === width * height * 4, 'The accepted output itself is invalid.');
  const collectWeights = options.collectWeights !== false;
  const result: FastHairArmResult = {pixels: before.slice(), weights: new Float32Array(collectWeights ? width * height : 0), fallbackReason: null,
    backgroundReferenceCheck: emptyCheck(), statistics: emptyStatistics(), regions: null, eligibleResidualIndices: null};
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
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const index = y * width + x, offset = index * 4, region = regions[index]!;
      const residual = differs(before, background, offset);
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
  return result;
}

/** Same complete per-frame reference scan, category/feather rule and counters.
 * This candidate makes the common background-only branch small and uses local
 * counters plus endian-independent RGBA word equality. It deliberately retains
 * the full confidence/category validation and the independent final audit.
 * Unaligned caller views use the preceding byte implementation in full. */
function composeHairArmsCpu(input: HairArmInput, options: FastHairOptions): FastHairArmResult {
  const {width, height, before, background} = input;
  check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
    && before instanceof Uint8ClampedArray && before.length === width * height * 4, 'The accepted output itself is invalid.');
  const performanceStats: CpuCompositionStats = {requested: true, used: false, wordComparisonUsed: false,
    auditedReferencePixels: 0, backgroundOnlyPixels: 0, rgbWritePixels: 0, finalAuditPixels: 0};
  if (before.byteOffset % 4 !== 0 || (background instanceof Uint8ClampedArray && background.byteOffset % 4 !== 0)) {
    const result = composeHairArmsFast(input, {...options, cpuCompose: false});
    // No optimized scan or writes occurred. Keep its counters zero even when
    // the preceding byte path attempted writes and then rejected the frame.
    result.cpuComposition = performanceStats;
    return result;
  }
  const collectWeights = options.collectWeights !== false, length = width * height;
  const result: FastHairArmResult = {pixels: before.slice(), weights: new Float32Array(collectWeights ? length : 0), fallbackReason: null,
    backgroundReferenceCheck: emptyCheck(), statistics: emptyStatistics(), regions: null, eligibleResidualIndices: null,
    cpuComposition: performanceStats};
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
    const beforeWords = new Uint32Array(before.buffer, before.byteOffset, length);
    const backgroundWords = new Uint32Array(background.buffer, background.byteOffset, length);
    const pixels = result.pixels, weights = result.weights;
    let referenceTested = 0, referenceChanged = 0, referenceMax = 0;
    let renderResidualPixels = 0, eligiblePixels = 0, eligibleResidualPixels = 0, hairEligibleResidualPixels = 0;
    let changedPixels = 0, fullReplacementPixels = 0, featheredPixels = 0;
    let protectedCorridorHairResidualPixels = 0, hairOverProtectedRenderResidualPixels = 0;
    performanceStats.used = performanceStats.wordComparisonUsed = true;
    for (let y = 0, index = 0; y < height; y++) {
      const maskRow = ys[y]!;
      for (let x = 0; x < width; x++, index++) {
        const region = regions[index]!, residual = beforeWords[index] !== backgroundWords[index];
        if (residual) renderResidualPixels++;
        // Every background pixel is checked, including alpha. Counts and maximum
        // error remain complete even when one earlier pixel already failed.
        if (region === 0) {
          referenceTested++;
          if (residual) {
            referenceChanged++;
            const difference = delta(before, background, index * 4);
            if (difference > referenceMax) referenceMax = difference;
          }
          continue;
        }
        if (region & PROTECTED) {
          if (residual && category[maskRow + xs[x]!] === hairIndex) {
            hairOverProtectedRenderResidualPixels++;
            if (region & CORRIDOR) protectedCorridorHairResidualPixels++;
          }
          continue;
        }
        // The remaining nonzero membership is exactly an editable corridor;
        // OPTICAL/NASAL overlap always took the protected branch above.
        eligiblePixels++;
        if (!residual) continue;
        eligibleResidualPixels++;
        eligibleResidualIndices?.push(index);
        if (category[maskRow + xs[x]!] !== hairIndex) continue;
        hairEligibleResidualPixels++;
        const edge = x === 0 || y === 0 || x === width - 1 || y === height - 1
          || category[maskRow + xs[x - 1]!] !== hairIndex || category[maskRow + xs[x + 1]!] !== hairIndex
          || category[ys[y - 1]! + xs[x]!] !== hairIndex || category[ys[y + 1]! + xs[x]!] !== hairIndex;
        const offset = index * 4;
        if (edge) {
          // Byte sums are exact integers. (a+b+1) >>> 1 equals Math.round(a*.5+b*.5)
          // for every channel pair, including odd sums; alpha stays accepted.
          pixels[offset] = (before[offset]! + background[offset]! + 1) >>> 1;
          pixels[offset + 1] = (before[offset + 1]! + background[offset + 1]! + 1) >>> 1;
          pixels[offset + 2] = (before[offset + 2]! + background[offset + 2]! + 1) >>> 1;
        } else {
          pixels[offset] = background[offset]!;
          pixels[offset + 1] = background[offset + 1]!;
          pixels[offset + 2] = background[offset + 2]!;
        }
        // The residual may be alpha-only, or a half blend may round back to the
        // accepted RGB. Both cases retain the old changed/weight semantics.
        if (pixels[offset] === before[offset] && pixels[offset + 1] === before[offset + 1]
          && pixels[offset + 2] === before[offset + 2]) continue;
        changedPixels++;
        if (edge) {featheredPixels++; if (collectWeights) weights[index] = .5;}
        else {fullReplacementPixels++; if (collectWeights) weights[index] = 1;}
      }
    }
    performanceStats.auditedReferencePixels = length;
    performanceStats.backgroundOnlyPixels = referenceTested;
    performanceStats.rgbWritePixels = hairEligibleResidualPixels;
    result.backgroundReferenceCheck = {testedPixels: referenceTested, changedPixels: referenceChanged, maxDelta: referenceMax};
    result.statistics = {changedPixels, renderResidualPixels, eligiblePixels, eligibleResidualPixels, hairEligibleResidualPixels,
      fullReplacementPixels, featheredPixels, protectedCorridorHairResidualPixels, hairOverProtectedRenderResidualPixels};
    check(referenceTested > 0 && referenceChanged === 0,
      'The clean native camera pass is not byte-exact outside the saved eyewear bounds.');
    referenceValidated = true;
    if (eligibleResidualIndices) result.eligibleResidualIndices = Uint32Array.from(eligibleResidualIndices);
    for (const rect of [...input.protection.protectedRects, input.noseRoi]) for (let y = rect.y0; y < rect.y1; y++) {
      const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
      result.pixels.set(before.subarray(start, end), start);
    }
  } catch (error) {
    result.pixels.set(before); result.weights.fill(0); result.statistics.changedPixels = 0;
    if (!referenceValidated) result.statistics = emptyStatistics();
    result.fallbackReason = error instanceof Error ? error.message : String(error);
  }
  return result;
}

/** Audit actual final pixels in one pass, including any later bounded continuity correction. */
export function checkHairProtection(input: HairArmInput, after: Uint8ClampedArray, regions: Uint8Array | null = null): HairProtectionChecks {
  const {width, height, before, background} = input;
  check(before.length === width * height * 4 && after.length === before.length && background.length === before.length,
    'Comparison sizes differ.');
  const membership = regions ?? regionsFor(input);
  check(membership.length === width * height, 'The final protection membership dimensions differ.');
  // Equality is independent of byte order. Unaligned caller subarrays retain the
  // byte path; constructing an invalid Uint32 view must never skip an audit.
  const aligned = before.byteOffset % 4 === 0 && after.byteOffset % 4 === 0 && background.byteOffset % 4 === 0;
  const beforeWords = aligned ? new Uint32Array(before.buffer, before.byteOffset, membership.length) : null;
  const afterWords = aligned ? new Uint32Array(after.buffer, after.byteOffset, membership.length) : null;
  const backgroundWords = aligned ? new Uint32Array(background.buffer, background.byteOffset, membership.length) : null;
  let opticalTested = 0, nasalTested = 0, outsideTested = 0, backgroundTested = 0;
  let opticalChanged = 0, nasalChanged = 0, outsideChanged = 0, backgroundChanged = 0;
  let opticalMax = 0, nasalMax = 0, outsideMax = 0, backgroundMax = 0;
  for (let index = 0; index < membership.length; index++) {
    const offset = index * 4, region = membership[index]!;
    const optical = (region & OPTICAL) !== 0, nasal = (region & NASAL) !== 0, outside = (region & CORRIDOR) === 0;
    const clean = beforeWords ? beforeWords[index] === backgroundWords![index] : !differs(before, background, offset);
    opticalTested += Number(optical); nasalTested += Number(nasal); outsideTested += Number(outside); backgroundTested += Number(clean);
    const changed = beforeWords ? beforeWords[index] !== afterWords![index] : differs(before, after, offset);
    if (!changed || !(optical || nasal || outside || clean)) continue;
    const difference = delta(before, after, offset);
    if (optical) { opticalChanged++; if (difference > opticalMax) opticalMax = difference; }
    if (nasal) { nasalChanged++; if (difference > nasalMax) nasalMax = difference; }
    if (outside) { outsideChanged++; if (difference > outsideMax) outsideMax = difference; }
    if (clean) { backgroundChanged++; if (difference > backgroundMax) backgroundMax = difference; }
  }
  return {
    protectedCheck: {testedPixels: opticalTested, changedPixels: opticalChanged, maxDelta: opticalMax},
    noseCheck: {testedPixels: nasalTested, changedPixels: nasalChanged, maxDelta: nasalMax},
    outsideEditableCheck: {testedPixels: outsideTested, changedPixels: outsideChanged, maxDelta: outsideMax},
    backgroundPreservationCheck: {testedPixels: backgroundTested, changedPixels: backgroundChanged, maxDelta: backgroundMax},
  };
}
