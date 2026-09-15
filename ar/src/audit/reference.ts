/** The CPU reference the audit holds the GPU output against: the accepted category compose (a two-pixel inward
 *  feather of the hair mask over the eyewear's arm corridors, protected rectangles copied from the render last), its
 *  four protection checks, and the continuity pass that removes detached distal remnants. The live pipeline never runs
 *  this; it is what the GPU path must agree with on a held frame. */
import type {HairMask} from '../hair/protocol.ts';
import {isEyewearId} from '../eyewear/catalog.ts';
import {validateProtection} from '../render/protection.ts';
import type {PixelRect, ProtectionConfiguration} from '../render/protection.ts';
import type {ProjectedTemplePath} from '../render/continuity.ts';

export interface PairIdentity {sourceSHA256: string; detectionSHA256: string; eyewearModel: string;}
export interface HairModelContract {id: string; sha256: string; labels: readonly string[]; hairIndex: number;}
export interface HairArmInput {
  width: number; height: number; before: Uint8ClampedArray; background: Uint8ClampedArray;
  pair: PairIdentity; geometryPair: PairIdentity; mask: HairMask;
  expectedModel: HairModelContract;
  protection: ProtectionConfiguration; noseRoi: PixelRect;
}
export interface PixelCheck {testedPixels: number; changedPixels: number; maxDelta: number;}
export interface HairArmStatistics {
  changedPixels: number; renderResidualPixels: number; eligiblePixels: number; eligibleResidualPixels: number;
  hairEligibleResidualPixels: number; fullReplacementPixels: number; featheredPixels: number;
  protectedCorridorHairResidualPixels: number; hairOverProtectedRenderResidualPixels: number;
}
export interface HairArmResult {
  pixels: Uint8ClampedArray; weights: Float32Array; fallbackReason: string | null;
  backgroundReferenceCheck: PixelCheck; statistics: HairArmStatistics;
  regions: Uint8Array | null; eligibleResidualIndices: Uint32Array | null;
}
export interface HairProtectionChecks {
  protectedCheck: PixelCheck; noseCheck: PixelCheck; outsideEditableCheck: PixelCheck; backgroundPreservationCheck: PixelCheck;
}
export const HAIR_ARM_POLICY = Object.freeze({method: 'hair-arm-camera-copy-v1', inwardFeatherRenderPixels: 2,
  categoryRule: 'category winner equals pinned hairIndex', background: 'exact native sRGB camera pass',
  protection: 'all protected rectangles plus the nasal ROI copied from the render last'});

const OPTICAL = 1, NASAL = 2, CORRIDOR = 4, PROTECTED = OPTICAL | NASAL;
const check = (value: unknown, message: string): void => { if (!value) throw new Error(message); };
const isHash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const emptyCheck = (): PixelCheck => ({testedPixels: 0, changedPixels: 0, maxDelta: 0});
const emptyStatistics = (): HairArmStatistics => ({changedPixels: 0, renderResidualPixels: 0, eligiblePixels: 0,
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
function regionsFor(input: HairArmInput): Uint8Array {
  const regions = new Uint8Array(input.width * input.height);
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
    && isEyewearId(pair.eyewearModel), 'The image/geometry identity is invalid.');
  check(input.pair.sourceSHA256 === input.geometryPair.sourceSHA256 && input.pair.detectionSHA256 === input.geometryPair.detectionSHA256
    && input.pair.eyewearModel === input.geometryPair.eyewearModel, 'The geometry belongs to a different image, detection, or frame model.');
  check(mask && mask.sourceSHA256 === input.pair.sourceSHA256 && mask.detectionSHA256 === input.pair.detectionSHA256,
    'The hair mask belongs to another image/detection pair.');
  check(expectedModel && isHash(expectedModel.sha256) && mask.model === expectedModel.id && mask.modelSHA256 === expectedModel.sha256
    && isHash(mask.categorySHA256), 'The pinned segmentation model or mask identity differs.');
  check(JSON.stringify(mask.labels) === JSON.stringify(expectedModel.labels) && mask.hairIndex === expectedModel.hairIndex
    && Number.isInteger(mask.hairIndex) && mask.hairIndex >= 0 && mask.hairIndex < mask.labels.length
    && mask.labels[mask.hairIndex]?.toLowerCase().includes('hair'), 'The category/hair label mapping differs.');
  check(Number.isInteger(mask.width) && Number.isInteger(mask.height) && mask.width > 0 && mask.height > 0
    && mask.category instanceof Uint8Array && mask.category.length === mask.width * mask.height, 'The mask storage/dimensions differ.');
  let validCategory = true;
  for (let index = 0; index < mask.category.length; index++) if (mask.category[index]! >= mask.labels.length) validCategory = false;
  check(validCategory, 'An output category exceeds the model labels.');
  validateProtection(input.protection, width, height);
  check(rectangle(input.noseRoi, width, height), 'The independent nasal ROI is missing or invalid.');
}

export interface ComposeOptions {
  collectEligibleResidualIndices?: boolean;
  /** Diagnostic weight map (default on). */
  collectWeights?: boolean;
}

/** The accepted category compose: category winner equals the pinned hair index, two-pixel inward feather, protected
 *  rectangles copied from the render last. Fail-closed: any validation failure returns the render unchanged. */
export function composeHairArms(input: HairArmInput, options: ComposeOptions = {}): HairArmResult {
  const {width, height, before, background} = input;
  check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
    && before instanceof Uint8ClampedArray && before.length === width * height * 4, 'The render itself is invalid.');
  const collectWeights = options.collectWeights !== false;
  const result: HairArmResult = {pixels: before.slice(), weights: new Float32Array(collectWeights ? width * height : 0), fallbackReason: null,
    backgroundReferenceCheck: emptyCheck(), statistics: emptyStatistics(), regions: null, eligibleResidualIndices: null};
  const eligibleResidualIndices: number[] | null = options.collectEligibleResidualIndices ? [] : null;
  let referenceValidated = false;
  try {
    validate(input);
    check(HAIR_ARM_POLICY.inwardFeatherRenderPixels === 2, 'The exact fast feather requires the frozen two-pixel policy.');
    const {mask} = input, category = mask.category, hairIndex = mask.hairIndex;
    const regions = regionsFor(input); result.regions = regions;
    const xs = new Uint32Array(width), ys = new Uint32Array(height);
    for (let x = 0; x < width; x++) xs[x] = Math.min(mask.width - 1, Math.floor((x + .5) * mask.width / width));
    for (let y = 0; y < height; y++) ys[y] = Math.min(mask.height - 1, Math.floor((y + .5) * mask.height / height)) * mask.width;
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
      'The clean camera pass is not byte-exact outside the eyewear bounds.');
    referenceValidated = true;
    if (eligibleResidualIndices) result.eligibleResidualIndices = Uint32Array.from(eligibleResidualIndices);
    // Preserve the authoritative final copy, including overlapping guards.
    for (const rect of [...input.protection.protectedRects, input.noseRoi]) for (let y = rect.y0; y < rect.y1; y++) {
      const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
      result.pixels.set(before.subarray(start, end), start);
    }
  } catch (error) {
    result.pixels.set(before); result.weights.fill(0); result.statistics.changedPixels = 0;
    // The compose does not begin statistics until the reference passes.
    if (!referenceValidated) result.statistics = emptyStatistics();
    result.fallbackReason = error instanceof Error ? error.message : String(error);
  }
  return result;
}

/** Audit final pixels in one pass: protected, nasal, outside-editable and clean-background pixels must equal the render. */
export function checkHairProtection(input: HairArmInput, after: Uint8ClampedArray, regions: Uint8Array | null = null): HairProtectionChecks {
  const {width, height, before, background} = input;
  check(before.length === width * height * 4 && after.length === before.length && background.length === before.length,
    'Comparison sizes differ.');
  const membership = regions ?? regionsFor(input);
  check(membership.length === width * height, 'The protection membership dimensions differ.');
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

/* ------------------------------------------------------------------------------------------------------------------ *
 * Continuity reference: remove only newly disconnected distal remnants on one originally connected projected arm.   *
 * ------------------------------------------------------------------------------------------------------------------ */
export const TEMPLE_CONTINUITY_POLICY = Object.freeze({method: 'same-arm-detached-tip-v1',
  minimumPathPixels: 8, minimumOriginalPixels: 6, minimumProximalPixels: 3, minimumLongitudinalGapPx: 1, associationMarginPx: 2,
  interpretation: 'Remove only newly disconnected distal remnants on one originally connected projected arm; not hair dilation or measured depth.'});
export interface ContinuityInput {before: Uint8ClampedArray; background: Uint8ClampedArray; after: Uint8ClampedArray;
  width: number; height: number; protection: ProtectionConfiguration; noseRoi: PixelRect;
  paths: readonly ProjectedTemplePath[] | null;
  /** Optional sparse candidates; each is independently rechecked against all guards and real residual. */
  eligibleIndices?: Uint32Array;}
export interface ContinuityDiagnostics {method: string; removedPixels: number; detachedComponents: number;
  originalComponents: number; splitComponents: number; skippedAmbiguousComponents: number;
  eligibleResidualPixels: number; pathLengthsPx: number[]; unavailableReason: string | null;}
const inside = (rect: PixelRect, x: number, y: number): boolean => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;
const residual = (left: Uint8ClampedArray, right: Uint8ClampedArray, offset: number): boolean =>
  left[offset] !== right[offset] || left[offset + 1] !== right[offset + 1] || left[offset + 2] !== right[offset + 2] || left[offset + 3] !== right[offset + 3];

interface Association {side: number; progress: number; distance: number;}
function associate(x: number, y: number, paths: readonly ProjectedTemplePath[]): Association | null {
  const perSide: Association[] = [];
  for (const path of paths) {
    if (path.lengthPx < TEMPLE_CONTINUITY_POLICY.minimumPathPixels) continue;
    let best: Association | null = null;
    for (let index = 0; index < path.points.length - 1; index++) {
      const a = path.points[index]!, b = path.points[index + 1]!, dx = b.x - a.x, dy = b.y - a.y;
      const denominator = dx * dx + dy * dy; if (denominator < 1e-12) continue;
      const amount = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / denominator));
      const distance = Math.hypot(x - a.x - amount * dx, y - a.y - amount * dy);
      const radius = a.radiusPx + amount * (b.radiusPx - a.radiusPx) + TEMPLE_CONTINUITY_POLICY.associationMarginPx;
      if (distance > radius || best && best.distance <= distance) continue;
      best = {side: path.side, progress: a.progressPx + amount * (b.progressPx - a.progressPx), distance};
    }
    if (best) perSide.push(best);
  }
  perSide.sort((a, b) => a.distance - b.distance);
  if (!perSide[0] || perSide[1] && perSide[1].distance - perSide[0].distance < 1) return null;
  return perSide[0];
}

/** Additional indices only; the caller copies the paired background there before its final guards/checks. */
export function findDetachedTemplePixels(input: ContinuityInput): {indices: Uint32Array; diagnostics: ContinuityDiagnostics} {
  const diagnostics: ContinuityDiagnostics = {method: TEMPLE_CONTINUITY_POLICY.method, removedPixels: 0, detachedComponents: 0,
    originalComponents: 0, splitComponents: 0, skippedAmbiguousComponents: 0, eligibleResidualPixels: 0,
    pathLengthsPx: input.paths?.map(path => path.lengthPx) ?? [], unavailableReason: null};
  try {
    const {width, height, before, background, after, protection, noseRoi, paths} = input;
    check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
      && before.length === width * height * 4 && background.length === before.length && after.length === before.length,
    'Continuity pixel dimensions differ.');
    validateProtection(protection, width, height);
    check(noseRoi && [noseRoi.x0, noseRoi.y0, noseRoi.x1, noseRoi.y1].every(Number.isInteger)
      && noseRoi.x0 >= 0 && noseRoi.y0 >= 0 && noseRoi.x1 <= width && noseRoi.y1 <= height
      && noseRoi.x1 > noseRoi.x0 && noseRoi.y1 > noseRoi.y0, 'Continuity nasal protection is invalid.');
    check(paths && paths.length === 2 && paths.every(path => Number.isFinite(path.lengthPx) && path.points.length >= 2
      && path.points.every(point => [point.x, point.y, point.radiusPx, point.progressPx].every(Number.isFinite)
        && point.radiusPx >= 0 && point.progressPx >= 0)), 'The same-arm ordering is unavailable.');
    const nodes = new Map<number, {visible: boolean; association: Association | null}>();
    const add = (index: number): void => {
      if (!Number.isInteger(index) || index < 0 || index >= width * height || nodes.has(index)) return;
      const x = index % width, y = Math.floor(index / width);
      if (inside(noseRoi, x, y) || protection.protectedRects.some(rect => inside(rect, x, y))
        || !protection.editableRects.some(rect => inside(rect, x, y)) || !residual(before, background, index * 4)) return;
      nodes.set(index, {visible: residual(after, background, index * 4), association: associate(x + .5, y + .5, paths!)});
    };
    if (input.eligibleIndices) for (const index of input.eligibleIndices) add(index);
    else for (const rect of protection.editableRects) for (let y = rect.y0; y < rect.y1; y++) for (let x = rect.x0; x < rect.x1; x++) add(y * width + x);
    diagnostics.eligibleResidualPixels = nodes.size;
    const adjacent = (index: number): number[] => {
      const x = index % width, y = Math.floor(index / width), result: number[] = [];
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if ((!dx && !dy) || x + dx < 0 || x + dx >= width || y + dy < 0 || y + dy >= height) continue;
        result.push((y + dy) * width + x + dx);
      }
      return result;
    };
    const seen = new Set<number>(), remove: number[] = [];
    for (const seed of nodes.keys()) {
      if (seen.has(seed)) continue;
      const original = [seed]; seen.add(seed);
      for (let cursor = 0; cursor < original.length; cursor++) for (const neighbor of adjacent(original[cursor]!)) {
        if (!nodes.has(neighbor) || seen.has(neighbor)) continue; seen.add(neighbor); original.push(neighbor);
      }
      diagnostics.originalComponents++;
      if (original.length < TEMPLE_CONTINUITY_POLICY.minimumOriginalPixels) continue;
      const side = nodes.get(seed)!.association?.side;
      if (side === undefined || original.some(index => nodes.get(index)!.association?.side !== side)) {
        diagnostics.skippedAmbiguousComponents++; continue;
      }
      const originalSet = new Set(original), visibleSeen = new Set<number>();
      const pieces: {indices: number[]; min: number; max: number}[] = [];
      for (const start of original) {
        if (visibleSeen.has(start) || !nodes.get(start)!.visible) continue;
        const indices = [start]; visibleSeen.add(start);
        for (let cursor = 0; cursor < indices.length; cursor++) for (const neighbor of adjacent(indices[cursor]!)) {
          if (!originalSet.has(neighbor) || visibleSeen.has(neighbor) || !nodes.get(neighbor)!.visible) continue;
          visibleSeen.add(neighbor); indices.push(neighbor);
        }
        const progress = indices.map(index => nodes.get(index)!.association!.progress);
        pieces.push({indices, min: Math.min(...progress), max: Math.max(...progress)});
      }
      if (pieces.length < 2) continue; diagnostics.splitComponents++;
      pieces.sort((a, b) => a.min - b.min);
      const proximal = pieces[0]!;
      if (proximal.indices.length < TEMPLE_CONTINUITY_POLICY.minimumProximalPixels) continue;
      for (const piece of pieces.slice(1)) {
        if (piece.min <= proximal.max + TEMPLE_CONTINUITY_POLICY.minimumLongitudinalGapPx) continue;
        const hiddenBetween = original.some(index => {const node = nodes.get(index)!;
          return !node.visible && node.association!.progress > proximal.max && node.association!.progress < piece.min;});
        if (!hiddenBetween) continue;
        remove.push(...piece.indices); diagnostics.detachedComponents++;
      }
    }
    diagnostics.removedPixels = remove.length;
    return {indices: new Uint32Array(remove), diagnostics};
  } catch (error) {
    diagnostics.unavailableReason = error instanceof Error ? error.message : String(error);
    return {indices: new Uint32Array(), diagnostics};
  }
}
