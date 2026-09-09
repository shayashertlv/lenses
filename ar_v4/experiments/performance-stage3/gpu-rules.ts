import {HAIR_ARM_POLICY} from '../hair-arm-preview/compose.ts';
import type {HairArmResult, PixelCheck} from '../hair-arm-preview/compose.ts';
import type {LiveHairArmInput} from '../hair-live-preview/live-mask.ts';
import {validateProtection} from '../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {PixelRect} from '../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import {TEMPLE_CONTINUITY_POLICY} from '../hair-live-preview/continuity.ts';
import type {ContinuityDiagnostics, ProjectedTemplePath} from '../hair-live-preview/continuity.ts';
export {projectTempleContinuity, loadTempleContinuityModel} from '../hair-live-preview/continuity.ts';
export type {TempleContinuityModel} from '../hair-live-preview/continuity.ts';

/** GPU texture dimensions, RGBA8 storage, frame/session ownership and B/C provenance
 * are checked by the GPU owner. This preserves all CPU mask/geometry validation
 * without manufacturing full-colour CPU arrays. */
export type GpuHairRuleInput = Omit<LiveHairArmInput, 'before' | 'background'>;
export const GPU_REGIONS = Object.freeze({OPTICAL:1, NASAL:2, CORRIDOR:4, PROTECTED:3});
export const HAIR_FLAGS = Object.freeze({RESIDUAL:1, ELIGIBLE:2, VISIBLE:4, CHANGED:8, HAIR:16, HALF:32, RGB_RESIDUAL:64});
export interface PackedHairFlags {
  width:number; height:number; bytes:Uint8Array; rowOrder:'bottom-up'|'top-down';
}
export interface ProtectionTestedCounts {protected:number; nose:number; outsideEditable:number; backgroundPreservation:number;}
export interface GpuFlagAnalysis {
  statistics:HairArmResult['statistics']; backgroundReferenceCheck:PixelCheck;
  protectionTested:ProtectionTestedCounts; eligibleResidualIndices:Uint32Array|null; fallbackReason:string|null;
}
export interface FlagContinuityInput {
  width:number; height:number; flags:PackedHairFlags; protection:GpuHairRuleInput['protection']; noseRoi:PixelRect;
  paths:readonly ProjectedTemplePath[]|null; eligibleIndices?:Uint32Array;
}
const check=(value:unknown,message:string):void=>{if(!value) throw new Error(message);};
const isHash=(value:unknown):value is string=>typeof value==='string' && /^[a-f0-9]{64}$/.test(value);
const rectangle=(rect:PixelRect,width:number,height:number):boolean=>!!rect
  && [rect.x0,rect.y0,rect.x1,rect.y1].every(Number.isInteger) && rect.x0>=0 && rect.y0>=0
  && rect.x1<=width && rect.y1<=height && rect.x1>rect.x0 && rect.y1>rect.y0;
const inside=(rect:PixelRect,x:number,y:number):boolean=>x>=rect.x0 && x<rect.x1 && y>=rect.y0 && y<rect.y1;
const emptyStatistics=():HairArmResult['statistics']=>({changedPixels:0,renderResidualPixels:0,eligiblePixels:0,
  eligibleResidualPixels:0,hairEligibleResidualPixels:0,fullReplacementPixels:0,featheredPixels:0,
  protectedCorridorHairResidualPixels:0,hairOverProtectedRenderResidualPixels:0});

export function validateGpuHairInput(input: GpuHairRuleInput): void {
  const {width, height, mask, expectedModel} = input;
  check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0,
    'The accepted output itself is invalid.');
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
  // The GPU owner independently validates authoritative RGBA8 texture dimensions/storage.
  // Integer category coordinates must not overflow the uint shader expression.
  check((2 * width - 1) * mask.width <= 0xffffffff && (2 * height - 1) * mask.height <= 0xffffffff,
    'The GPU category coordinate product exceeds its exact integer range.');
  validateProtection(input.protection, width, height);
  check(rectangle(input.noseRoi, width, height), 'The saved independent nasal ROI is missing or invalid.');
}


/** Same half-open union membership as Test1, including independent overlapping checks. */
export function buildGpuRegionMembership(input:GpuHairRuleInput,storage?:Uint8Array):Uint8Array {
  validateGpuHairInput(input);
  check(HAIR_ARM_POLICY.inwardFeatherRenderPixels===2,'The exact fast feather requires the frozen two-pixel policy.');
  const {width,height}=input;
  check(!storage || storage.length===width*height,'The GPU protection membership dimensions differ.');
  const regions=storage ?? new Uint8Array(width*height);regions.fill(0);
  const add=(rect:PixelRect,bit:number):void=>{
    for(let y=rect.y0;y<rect.y1;y++) for(let index=y*width+rect.x0,end=y*width+rect.x1;index<end;index++) regions[index]=regions[index]!|bit;
  };
  for(const rect of input.protection.protectedRects) add(rect,GPU_REGIONS.OPTICAL);
  for(const rect of input.protection.editableRects) add(rect,GPU_REGIONS.CORRIDOR);
  add(input.noseRoi,GPU_REGIONS.NASAL);return regions;
}
export function validatePackedHairFlags(flags:PackedHairFlags):void {
  check(flags && Number.isInteger(flags.width) && Number.isInteger(flags.height) && flags.width>0 && flags.height>0
    && flags.bytes instanceof Uint8Array && flags.bytes.length===Math.ceil(flags.width/4)*4*flags.height
    && (flags.rowOrder==='bottom-up' || flags.rowOrder==='top-down'),'The compact GPU flag storage/dimensions differ.');
}
/** Callers validate the lease once; this accessor never allocates or flips full rows. */
export function readPackedHairFlag(flags:PackedHairFlags,index:number):number {
  const y=Math.floor(index/flags.width),x=index-y*flags.width;
  return flags.bytes[(flags.rowOrder==='bottom-up' ? flags.height-1-y:y)*Math.ceil(flags.width/4)*4+x]!;
}
export function analyzePackedHairFlags(input:GpuHairRuleInput,flags:PackedHairFlags,referenceMaxDelta:number,regionStorage?:Uint8Array):GpuFlagAnalysis {
  validatePackedHairFlags(flags);
  check(flags.width===input.width && flags.height===input.height,'The GPU flag image belongs to another viewport.');
  check(Number.isInteger(referenceMaxDelta) && referenceMaxDelta>=0 && referenceMaxDelta<=255,'The GPU reference reduction is unavailable.');
  const regions=buildGpuRegionMembership(input,regionStorage), statistics=emptyStatistics(), eligible:number[]=[];
  const reference:PixelCheck={testedPixels:0,changedPixels:0,maxDelta:referenceMaxDelta};
  const tested:ProtectionTestedCounts={protected:0,nose:0,outsideEditable:0,backgroundPreservation:0};
  const rowBytes=Math.ceil(flags.width/4)*4;
  for(let y=0;y<input.height;y++) {
    const row=(flags.rowOrder==='bottom-up' ? input.height-1-y:y)*rowBytes;
    for(let x=0;x<input.width;x++) {
      const index=y*input.width+x, flag=flags.bytes[row+x]!, region=regions[index]!;
      const residual=(flag&1)!==0, eligibleResidual=(flag&2)!==0, visible=(flag&4)!==0, changed=(flag&8)!==0;
      const hair=(flag&16)!==0, half=(flag&32)!==0, rgbResidual=(flag&64)!==0;
      const protectedPixel=(region&3)!==0, corridor=(region&4)!==0;
      check((flag&128)===0 && eligibleResidual===(residual && corridor && !protectedPixel)
        && (!rgbResidual || residual) && (!changed || eligibleResidual && hair && rgbResidual)
        && (!half || eligibleResidual && hair) && (residual || !visible)
        && (residual ? changed || visible : !changed)
        && (eligibleResidual && hair || (!changed && visible===residual)),
      'The compact GPU predicates contradict the accepted region/residual rules.');
      tested.protected+=Number((region&1)!==0);tested.nose+=Number((region&2)!==0);
      tested.outsideEditable+=Number(!corridor);tested.backgroundPreservation+=Number(!residual);
      statistics.renderResidualPixels+=Number(residual);
      if(region===0) {reference.testedPixels++;reference.changedPixels+=Number(residual);}
      if(protectedPixel) {
        if(residual && hair) {statistics.hairOverProtectedRenderResidualPixels++;if(corridor) statistics.protectedCorridorHairResidualPixels++;}
        continue;
      }
      if(!corridor) continue;
      statistics.eligiblePixels++;
      if(!eligibleResidual) continue;
      statistics.eligibleResidualPixels++;eligible.push(index);
      if(!hair) continue;
      statistics.hairEligibleResidualPixels++;
      if(!changed) continue;
      statistics.changedPixels++;if(half) statistics.featheredPixels++;else statistics.fullReplacementPixels++;
    }
    for(let x=input.width;x<rowBytes;x++) check(flags.bytes[row+x]===0,'The GPU flag padding is not empty.');
  }
  check((reference.changedPixels===0)===(referenceMaxDelta===0),'The GPU reference reduction disagrees with compact residual flags.');
  const fallbackReason=reference.testedPixels>0 && reference.changedPixels===0 ? null
    :'The clean native camera pass is not byte-exact outside the saved eyewear bounds.';
  return {statistics:fallbackReason ? emptyStatistics():statistics,backgroundReferenceCheck:reference,protectionTested:tested,
    eligibleResidualIndices:fallbackReason ? null:Uint32Array.from(eligible),fallbackReason};
}
export function changedPixelsAfterContinuity(flags:PackedHairFlags,initialChanged:number,indices:Uint32Array):number {
  validatePackedHairFlags(flags);
  check(Number.isInteger(initialChanged) && initialChanged>=0,'The provisional changed count is invalid.');
  let changed=initialChanged;const seen=new Set<number>();
  for(const index of indices) {
    check(index<flags.width*flags.height && !seen.has(index),'The detached GPU pixel indices are invalid.');seen.add(index);
    const flag=readPackedHairFlag(flags,index);
    check((flag&HAIR_FLAGS.ELIGIBLE)!==0,'A detached pixel is outside the eligible residual.');
    changed+=Number((flag&HAIR_FLAGS.RGB_RESIDUAL)!==0)-Number((flag&HAIR_FLAGS.CHANGED)!==0);
  }
  check(changed>=0 && changed<=flags.width*flags.height,'The final changed count is invalid.');return changed;
}
/** An unavailable/nonzero full-output audit never manufactures passing checks. */
export function successfulGpuProtectionChecks(tested:ProtectionTestedCounts,maxDeltas:readonly number[]):{
  protectedCheck:PixelCheck;noseCheck:PixelCheck;outsideEditableCheck:PixelCheck;backgroundPreservationCheck:PixelCheck;
} {
  check(maxDeltas.length===4 && maxDeltas.every(value=>Number.isInteger(value) && value===0)
    && Object.values(tested).every(value=>Number.isInteger(value) && value>=0),'A final accepted-pixel protection check failed.');
  const value=(testedPixels:number):PixelCheck=>({testedPixels,changedPixels:0,maxDelta:0});
  return {protectedCheck:value(tested.protected),noseCheck:value(tested.nose),
    outsideEditableCheck:value(tested.outsideEditable),backgroundPreservationCheck:value(tested.backgroundPreservation)};
}

// The following association/component algorithm is copied from accepted continuity.ts.
// Only pixel predicates read compact E/V flags; projection, thresholds, iteration and
// double-precision calculations are unchanged. No fabricated RGBA arrays are used.
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

/** Additional indices only; caller copies paired background here before its final guards/checks. */
export function findDetachedTemplePixelsFromFlags(input: FlagContinuityInput): {indices: Uint32Array; diagnostics: ContinuityDiagnostics} {
  const diagnostics: ContinuityDiagnostics = {method: TEMPLE_CONTINUITY_POLICY.method, removedPixels: 0, detachedComponents: 0,
    originalComponents: 0, splitComponents: 0, skippedAmbiguousComponents: 0, eligibleResidualPixels: 0,
    pathLengthsPx: input.paths?.map(path => path.lengthPx) ?? [], unavailableReason: null};
  try {
    const {width, height, flags, protection, noseRoi, paths} = input;
    check(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
      && flags.width === width && flags.height === height, 'Continuity pixel dimensions differ.');
    validatePackedHairFlags(flags);
    validateProtection(protection, width, height);
    check(noseRoi && [noseRoi.x0, noseRoi.y0, noseRoi.x1, noseRoi.y1].every(Number.isInteger)
      && noseRoi.x0 >= 0 && noseRoi.y0 >= 0 && noseRoi.x1 <= width && noseRoi.y1 <= height
      && noseRoi.x1 > noseRoi.x0 && noseRoi.y1 > noseRoi.y0, 'Continuity nasal protection is invalid.');
    check(paths && paths.length === 2 && paths.every(path => Number.isFinite(path.lengthPx) && path.points.length >= 2
      && path.points.every(point => [point.x, point.y, point.radiusPx, point.progressPx].every(Number.isFinite)
        && point.radiusPx >= 0 && point.progressPx >= 0)), 'The current same-arm ordering is unavailable.');
    const nodes = new Map<number, {visible: boolean; association: Association | null}>();
    const add = (index: number): void => {
      if (!Number.isInteger(index) || index < 0 || index >= width * height || nodes.has(index)) return;
      const x = index % width, y = Math.floor(index / width);
      if (inside(noseRoi, x, y) || protection.protectedRects.some(rect => inside(rect, x, y))
        || !protection.editableRects.some(rect => inside(rect, x, y))
        || (readPackedHairFlag(flags, index) & (HAIR_FLAGS.RESIDUAL | HAIR_FLAGS.ELIGIBLE)) !== (HAIR_FLAGS.RESIDUAL | HAIR_FLAGS.ELIGIBLE)) return;
      nodes.set(index, {visible: (readPackedHairFlag(flags, index) & HAIR_FLAGS.VISIBLE) !== 0, association: associate(x + .5, y + .5, paths!)});
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

/** Integer array textures retain top-down uploaded rows. Native/output RGBA8
 * textures and framebuffer/readPixels rows are bottom-up. Dithering, blending,
 * tone mapping and colour-space conversion must be disabled for these passes. */
export const GPU_HAIR_COMMON_GLSL = `
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp usampler2D;
uniform sampler2D uBeauty;
uniform sampler2D uCamera;
uniform usampler2D uCategory;
uniform usampler2D uRegions;
uniform usampler2D uDetached;
uniform ivec2 uSize;
uniform ivec2 uMaskSize;
uniform uint uHairIndex;
uvec4 nativeBytes(sampler2D image, ivec2 top) {
  return uvec4(floor(texelFetch(image,ivec2(top.x,uSize.y-1-top.y),0)*255.0+0.5));
}
uint regionAt(ivec2 top) {return texelFetch(uRegions,top,0).r;}
bool hairAt(ivec2 top) {
  uvec2 p=uvec2(top), size=uvec2(uSize), maskSize=uvec2(uMaskSize);
  uvec2 mapped=min(maskSize-uvec2(1u),((2u*p+uvec2(1u))*maskSize)/(2u*size));
  return texelFetch(uCategory,ivec2(mapped),0).r==uHairIndex;
}
bool halfAt(ivec2 top) {
  if(top.x==0 || top.y==0 || top.x==uSize.x-1 || top.y==uSize.y-1) return true;
  return !hairAt(top+ivec2(-1,0)) || !hairAt(top+ivec2(1,0))
    || !hairAt(top+ivec2(0,-1)) || !hairAt(top+ivec2(0,1));
}
uint maxDelta(uvec4 left,uvec4 right) {
  uvec4 delta=max(left,right)-min(left,right);
  return max(max(delta.r,delta.g),max(delta.b,delta.a));
}
struct HairPixel {uvec4 beauty;uvec4 camera;uvec4 provisional;uint region;uint flags;};
HairPixel hairPixel(ivec2 top) {
  HairPixel value;
  value.beauty=nativeBytes(uBeauty,top);value.camera=nativeBytes(uCamera,top);value.provisional=value.beauty;
  value.region=regionAt(top);
  bool residual=any(notEqual(value.beauty,value.camera));
  bool rgbResidual=any(notEqual(value.beauty.rgb,value.camera.rgb));
  bool eligible=residual && (value.region&4u)!=0u && (value.region&3u)==0u;
  bool hair=hairAt(top),feathered=false;
  if(eligible && hair) {
    feathered=halfAt(top);
    value.provisional.rgb=feathered ? (value.beauty.rgb+value.camera.rgb+uvec3(1u))/2u:value.camera.rgb;
  }
  bool visible=any(notEqual(value.provisional,value.camera));
  bool changed=any(notEqual(value.provisional,value.beauty));
  value.flags=(residual?1u:0u)|(eligible?2u:0u)|(visible?4u:0u)|(changed?8u:0u)
    |(hair?16u:0u)|(feathered?32u:0u)|(rgbResidual?64u:0u);
  return value;
}
`;

/** MRT outputs share ceil(width/4) x height RGBA8UI targets. Each channel is
 * one source pixel. Output1 supplies exact outside-union reference deltas for
 * componentwise max reduction. Padded source columns are explicitly zero. */
export const GPU_HAIR_PACKED_FLAGS_FRAGMENT_GLSL = `#version 300 es
${GPU_HAIR_COMMON_GLSL}
layout(location=0) out uvec4 oFlags;
layout(location=1) out uvec4 oReferenceDeltas;
void main() {
  ivec2 packedPixel=ivec2(gl_FragCoord.xy);int topY=uSize.y-1-packedPixel.y;
  uvec4 packedFlags=uvec4(0u),referenceDeltas=uvec4(0u);
  for(int channel=0;channel<4;channel++) {
    int x=packedPixel.x*4+channel;
    if(x>=uSize.x) continue;
    HairPixel value=hairPixel(ivec2(x,topY));
    packedFlags[channel]=value.flags;
    referenceDeltas[channel]=value.region==0u ? maxDelta(value.beauty,value.camera):0u;
  }
  oFlags=packedFlags;oReferenceDeltas=referenceDeltas;
}
`;

/** Recompute the exact same integer provisional value, apply only eligible
 * detached RGB replacement, then restore the authoritative protections last. */
export const GPU_HAIR_COMPOSE_FRAGMENT_GLSL = `#version 300 es
${GPU_HAIR_COMMON_GLSL}
layout(location=0) out vec4 oColor;
void main() {
  ivec2 top=ivec2(int(gl_FragCoord.x),uSize.y-1-int(gl_FragCoord.y));
  HairPixel value=hairPixel(top);uvec4 finalPixel=value.provisional;
  if(texelFetch(uDetached,top,0).r!=0u && (value.flags&2u)!=0u) finalPixel.rgb=value.camera.rgb;
  finalPixel.a=value.beauty.a;
  if((value.region&3u)!=0u) finalPixel=value.beauty;
  oColor=vec4(finalPixel)/255.0;
}
`;

/** Full-output independent byte comparison. Componentwise max reduction yields
 * exact protected/nose/outside/background maxDelta values; all four must be zero.
 * Tested counts are obtained independently from region membership and R flags. */
export const GPU_HAIR_AUDIT_FRAGMENT_GLSL = `#version 300 es
${GPU_HAIR_COMMON_GLSL}
uniform sampler2D uOutput;
layout(location=0) out uvec4 oGuardDeltas;
void main() {
  ivec2 top=ivec2(int(gl_FragCoord.x),uSize.y-1-int(gl_FragCoord.y));
  uvec4 beauty=nativeBytes(uBeauty,top),camera=nativeBytes(uCamera,top),result=nativeBytes(uOutput,top);
  uint region=regionAt(top),delta=maxDelta(beauty,result);
  oGuardDeltas=uvec4((region&1u)!=0u ? delta:0u,(region&2u)!=0u ? delta:0u,
    (region&4u)==0u ? delta:0u,all(equal(beauty,camera)) ? delta:0u);
}
`;
