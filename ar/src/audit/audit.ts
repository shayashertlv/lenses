/** Hold & audit (explicit action): the held frame is drawn without hair, without eyewear, with hair and undropped,
 *  each read back once; the CPU reference compose and continuity pass run on the same before/background/mask, and the
 *  four protection checks are applied to the GPU output. The result carries the counts, the rectangles and lossless
 *  images of the held frame; nothing is stored or sent anywhere unless the page downloads it. */
import type {Detection} from '../face/protocol.ts';
import type {CategoryMask, HairMask} from '../hair/protocol.ts';
import type {PixelRect} from '../render/protection.ts';
import {GUARD_METHOD} from '../render/renderer.ts';
import type {FrameTimings, TryOnRenderer, WidthFitReport} from '../render/renderer.ts';
import {checkHairProtection, composeHairArms, findDetachedTemplePixels} from './reference.ts';
import type {HairArmInput, HairModelContract, HairProtectionChecks, PairIdentity} from './reference.ts';

/** `differentPixels` counts any RGB difference; `differentPixelsOver8` counts differences a viewer could notice (max channel delta > 8). */
export interface PixelDifference {differentPixels: number; differentPixelsOver8: number; maxDelta: number;}
export interface Audit {
  schema: 'ar-audit-v1'; createdAt: string; sequence: number; width: number; height: number;
  guard: {method: string; guarded: boolean; safeFallback: boolean; protectedRects: PixelRect[]; editableRects: PixelRect[]; noseRoi: PixelRect | null};
  hairApplied: boolean;
  /** Whether the held frame drew a mask reused from an earlier frame, and whether it was moved with the head. */
  maskReuse: {carried: boolean; warped: boolean};
  checks: HairProtectionChecks | null; checkError: string | null;
  reference: {method: string; fallbackReason: string | null; changedPixels: number | null; continuityRemovedPixels: number | null; error: string | null};
  /** The continuity cut on the held frame: mesh-local z per arm from which the arm was removed, or null. */
  cut: {negative: number | null; positive: number | null; continuity: boolean};
  /** Which pipeline drew the held frame: the width fit's mode, state and applied ratio (`original` and ratio 1 are the
   *  shipped geometry), so two audits stay comparable. */
  widthFit: WidthFitReport;
  afterVsBefore: PixelDifference; afterVsReference: PixelDifference | null;
  /** Pixels inside the protected rectangles and the nasal ROI that differ between the posed-drop and the undropped render. */
  dropInsideProtected: PixelDifference | null;
  pair: PairIdentity; detection: Detection;
  mask: {model: string; modelSHA256: string; categorySHA256: string; width: number; height: number; hairIndex: number} | null;
  images: {sourcePngDataUrl: string; beforePngDataUrl: string; afterPngDataUrl: string; backgroundPngDataUrl: string; referencePngDataUrl: string | null; maskPngDataUrl: string | null;
    undroppedPngDataUrl: string; dropDiffPngDataUrl: string; hairDiffPngDataUrl: string};
  timings: {rendersMs: number; readbackMs: number; composeMs: number; checksMs: number; encodeMs: number; totalMs: number};
}
export interface HeldFrame {
  frame: HTMLCanvasElement; detection: Detection; pair: PairIdentity; model: HairModelContract;
  /** The paired mask, or null when none was available. */
  mask: HairMask | null;
  /** The mask as the live render used it (null when hair was not drawn). */
  gpuMask: CategoryMask | null;
  /** The mask was computed on another frame (`?hairframes=`); the CPU reference compose is then skipped. */
  maskCarried?: boolean;
  /** The carried mask was moved to this frame by the head's motion. */
  maskWarped?: boolean;
}

const png = (image: ImageData): string => {
  const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
  const context = canvas.getContext('2d', {alpha: false, colorSpace: 'srgb'}); if (!context) throw new Error('PNG encoding unavailable.');
  context.putImageData(image, 0, 0); const url = canvas.toDataURL('image/png'); canvas.width = canvas.height = 0; return url;
};
function difference(a: Uint8ClampedArray, b: Uint8ClampedArray): PixelDifference {
  let differentPixels = 0, differentPixelsOver8 = 0, maxDelta = 0;
  for (let index = 0; index < a.length; index += 4) {
    const d = Math.max(Math.abs(a[index]! - b[index]!), Math.abs(a[index + 1]! - b[index + 1]!), Math.abs(a[index + 2]! - b[index + 2]!));
    if (d > 0) {differentPixels++; if (d > 8) differentPixelsOver8++; if (d > maxDelta) maxDelta = d;}
  }
  return {differentPixels, differentPixelsOver8, maxDelta};
}
/** Full-frame map of where two renders differ: white for a noticeable difference (> 8), grey for any other, black for none. */
function differenceMap(a: Uint8ClampedArray, b: Uint8ClampedArray, width: number, height: number): ImageData {
  const image = new ImageData(width, height);
  for (let index = 0, pixel = 0; index < a.length; index += 4, pixel += 4) {
    const d = Math.max(Math.abs(a[index]! - b[index]!), Math.abs(a[index + 1]! - b[index + 1]!), Math.abs(a[index + 2]! - b[index + 2]!));
    const v = d > 8 ? 255 : d > 0 ? 96 : 0;
    image.data[pixel] = image.data[pixel + 1] = image.data[pixel + 2] = v; image.data[pixel + 3] = 255;
  }
  return image;
}
function differenceInside(a: Uint8ClampedArray, b: Uint8ClampedArray, width: number, rects: readonly PixelRect[]): PixelDifference {
  let differentPixels = 0, differentPixelsOver8 = 0, maxDelta = 0;
  for (const rect of rects) for (let y = rect.y0; y < rect.y1; y++) for (let x = rect.x0; x < rect.x1; x++) {
    const index = (y * width + x) * 4;
    const d = Math.max(Math.abs(a[index]! - b[index]!), Math.abs(a[index + 1]! - b[index + 1]!), Math.abs(a[index + 2]! - b[index + 2]!));
    if (d > 0) {differentPixels++; if (d > 8) differentPixelsOver8++; if (d > maxDelta) maxDelta = d;}
  }
  return {differentPixels, differentPixelsOver8, maxDelta};
}

/** Draws the held frame's variants, reads each back once, and applies the reference compose and checks to the GPU output.
 *  Leaves the display in the live state. */
export function runAudit(renderer: TryOnRenderer, held: HeldFrame, live: FrameTimings, sequence: number): Audit {
  const {frame, detection, pair, model, mask, gpuMask} = held;
  const started = performance.now();
  const {width, height} = renderer.renderSize;
  const protection = renderer.protection, noseRoi = renderer.noseRoi;
  let rendersMs = 0, readbackMs = 0;
  const draw = (variant: Parameters<TryOnRenderer['render']>[1]): ImageData => {
    const t0 = performance.now(); renderer.render(gpuMask, variant); rendersMs += performance.now() - t0;
    const t1 = performance.now(); const image = renderer.readback(); readbackMs += performance.now() - t1; return image;
  };
  const before = draw({hair: false}), background = draw({eyewear: false}), after = draw({hair: true});
  // The posed drop against an undropped render: any difference inside the protected pixels is a deviation from the undropped baseline.
  const undropped = draw({hair: false, drop: false, guard: false});
  const guardedRects = protection ? [...protection.protectedRects, ...(noseRoi ? [noseRoi] : [])] : [];
  const dropInsideProtected = guardedRects.length ? differenceInside(before.data, undropped.data, width, guardedRects) : null;
  renderer.render(gpuMask);
  let reference: Audit['reference'] = {method: 'composeHairArms + findDetachedTemplePixels (CPU reference on the GPU before/background)', fallbackReason: null, changedPixels: null, continuityRemovedPixels: null, error: null};
  let referencePixels: Uint8ClampedArray | null = null, regions: Uint8Array | null = null, checks: HairProtectionChecks | null = null, checkError: string | null = null;
  let composeMs = 0, checksMs = 0;
  if (mask && protection && noseRoi) {
    const input: HairArmInput = {width, height, before: before.data, background: background.data, pair, mask, expectedModel: model, protection, noseRoi,
      geometryPair: {sourceSHA256: pair.sourceSHA256, detectionSHA256: pair.detectionSHA256, eyewearModel: renderer.eyewear.id}};
    const t0 = performance.now();
    // The CPU reference composes a frame's own mask only; a reused one is skipped explicitly (its identity alone would not
    // refuse it when two consecutive frames are byte-identical). The four protection checks still run.
    if (held.maskCarried) reference = {...reference, fallbackReason: 'The mask was reused from an earlier frame; the CPU reference composes only a frame\'s own mask.'};
    else try {
      const result = composeHairArms(input, {collectWeights: false, collectEligibleResidualIndices: true});
      referencePixels = result.pixels; regions = result.regions;
      reference = {...reference, fallbackReason: result.fallbackReason, changedPixels: result.statistics.changedPixels};
      // The reference continuity pass on its own compose: detached distal remnants revert to the clean camera.
      if (!result.fallbackReason) {
        const continuity = findDetachedTemplePixels({before: before.data, background: background.data, after: referencePixels, width, height, protection, noseRoi,
          paths: renderer.paths, ...(result.eligibleResidualIndices ? {eligibleIndices: result.eligibleResidualIndices} : {})});
        for (const index of continuity.indices) {const offset = index * 4; for (let channel = 0; channel < 3; channel++) referencePixels[offset + channel] = background.data[offset + channel]!;}
        reference = {...reference, continuityRemovedPixels: continuity.indices.length};
      }
    } catch (error) {reference = {...reference, error: error instanceof Error ? error.message : String(error)};}
    composeMs = performance.now() - t0;
    const t1 = performance.now();
    try {checks = checkHairProtection(input, after.data, regions);} catch (error) {checkError = error instanceof Error ? error.message : String(error);}
    checksMs = performance.now() - t1;
  } else checkError = !mask ? 'No paired hair mask on the held frame.' : 'The protection or nasal region is unavailable on the held frame.';
  const encodeStart = performance.now();
  let maskPng: string | null = null;
  if (mask) {
    const image = new ImageData(mask.width, mask.height);
    for (let index = 0; index < mask.category.length; index++) {const v = mask.category[index] === mask.hairIndex ? 255 : 0; image.data[index * 4] = image.data[index * 4 + 1] = image.data[index * 4 + 2] = v; image.data[index * 4 + 3] = 255;}
    maskPng = png(image);
  }
  const images: Audit['images'] = {sourcePngDataUrl: frame.toDataURL('image/png'), beforePngDataUrl: png(before), afterPngDataUrl: png(after), backgroundPngDataUrl: png(background),
    referencePngDataUrl: referencePixels ? png(new ImageData(new Uint8ClampedArray(referencePixels), width, height)) : null, maskPngDataUrl: maskPng,
    undroppedPngDataUrl: png(undropped), dropDiffPngDataUrl: png(differenceMap(before.data, undropped.data, width, height)), hairDiffPngDataUrl: png(differenceMap(after.data, before.data, width, height))};
  const encodeMs = performance.now() - encodeStart;
  return {
    schema: 'ar-audit-v1', createdAt: new Date().toISOString(), sequence, width, height,
    guard: {method: GUARD_METHOD, guarded: live.guarded, safeFallback: live.safeFallback, protectedRects: protection ? [...protection.protectedRects] : [], editableRects: protection ? [...protection.editableRects] : [], noseRoi},
    hairApplied: live.hairApplied, maskReuse: {carried: held.maskCarried === true, warped: held.maskWarped === true}, checks, checkError, reference,
    cut: {negative: live.cutNegativeZ, positive: live.cutPositiveZ, continuity: live.continuity},
    widthFit: renderer.widthFit,
    afterVsBefore: difference(after.data, before.data), afterVsReference: referencePixels ? difference(after.data, referencePixels) : null, dropInsideProtected,
    pair: {...pair}, detection: structuredClone(detection),
    mask: mask ? {model: mask.model, modelSHA256: mask.modelSHA256, categorySHA256: mask.categorySHA256, width: mask.width, height: mask.height, hairIndex: mask.hairIndex} : null,
    images, timings: {rendersMs, readbackMs, composeMs, checksMs, encodeMs, totalMs: performance.now() - started},
  };
}
