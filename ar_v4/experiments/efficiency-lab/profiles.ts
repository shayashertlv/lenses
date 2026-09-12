import {normalizeSpeedOptions} from './speed-options.ts';

export const G_COMMIT = 'b9142b2a3b957445f378d8012eea7e27ca68fd0b';
export const PIPELINES = ['g', 'scratch', 'lean', 'deferred', 'temples', 'combined', 'rate12', 'rate10', 'rate8', 'mask', 'publish', 'region', 'lens', 'ui', 'hair-release'] as const;
export type Pipeline = typeof PIPELINES[number];
export const DEFAULT_PIPELINE: Pipeline = 'g';
export const CURRENT_BASE_METADATA = Object.freeze({currentBase: 'g', ownerSelectedG: true,
  baseCommit: G_COMMIT, candidateAccepted: false, study: 'efficiency-lab-v1'});
export const PIPELINE_LABELS: Record<Pipeline, string> = {
  g: 'G · Current baseline', scratch: 'H · Reuse download memory', lean: 'I · Lean camera input',
  deferred: 'J · Later inference start', temples: 'K · Overlap temple downloads', combined: 'L · Combined candidate',
  rate12: 'M · Up to 12 images/s', rate10: 'N · Up to 10 images/s', rate8: 'O · Up to 8 images/s',
  mask: 'P · Lean hair-mask extraction',
  publish: 'Q · Skip repeat image upload', region: 'R · Smaller temple download',
  lens: 'S · Lean temple render', ui: 'T · Lighter statistics', 'hair-release': 'U · Earlier hair processing',
};
const g = {reuseSourcePixels: true, fewerCopies: true, asyncReadback: true, prewarmTemples: true};
const profile = (extra: {poolReadbackScratch?: boolean; asyncTemples?: boolean; cropBranchReadback?: boolean; omitBranchLenses?: boolean}, detail: string,
  leanInputs = false, deferPrefetch = false, captureRateHz: number | null = null,
  hairExtractionMode: 'sdk' | 'direct' = 'sdk', suppressUnchangedPublication = false,
  throttleUi = false, releaseHairWorkerEarly = false) => Object.freeze({options: normalizeSpeedOptions({...g, ...extra}),
  mode: 'overlap' as const, leanInputs, deferPrefetch, captureRateHz, hairExtractionMode, suppressUnchangedPublication, throttleUi, releaseHairWorkerEarly, detail});
export const PROFILES = {
  g: profile({}, 'The unchanged G renderer and its current two-image scheduling. Your reference for every comparison.'),
  scratch: profile({poolReadbackScratch: true}, 'G with reusable private download memory. Captured and held output images stay independently owned.'),
  lean: profile({}, 'G with fewer camera-input allocations and one less full-image copy before hashing.', true),
  deferred: profile({}, 'Start the next image’s inference after the current main image’s graphics submission. Compare freshness as well as update rate.', false, true),
  temples: profile({asyncTemples: true}, 'Submit required rear-temple work before awaiting the main image download, then join both exact results. First-frame warmup remains; simultaneous GPU work is unproven.'),
  combined: profile({poolReadbackScratch: true, asyncTemples: true}, 'H, I, J and K together. This is an experimental combination; it has not replaced G.', true, true),
  rate12: profile({}, 'G processing at most 12 new images per second. The last complete image stays visible between updates. Compare motion smoothness, freshness and hair edges.', false, false, 12),
  rate10: profile({}, 'G processing at most 10 new images per second. Skipped images do no AR work. Detection, pose and any applied hair mask belong to the same image.', false, false, 10),
  rate8: profile({}, 'G processing at most 8 new images per second. A stronger reduction to make the motion tradeoff easy to judge. Resolution and all rendering safeguards stay the same.', false, false, 8),
  mask: profile({}, 'G with fewer temporary arrays when extracting a GPU or float hair mask. No added frame cap. The same model, resolution, pairing and rendering safeguards remain; gains need measurement.', false, false, null, 'direct'),
  publish: profile({}, 'G skips uploading the previous image again when its view is unchanged. New images and hair toggles still appear immediately. Compare completed updates and frame age.', false, false, null, 'sdk', true),
  region: profile({cropBranchReadback: true}, 'G downloads only the required temple region from the rear-temple image. Full rendering resolution and final nose/front checks stay fixed. Compare hair and temple edges as you turn.'),
  lens: profile({omitBranchLenses: true}, 'G omits lenses from the extra rear-temple rendering. The main glasses image stays complete. Compare both frames, hair models, turns and nose/front protection before accepting this candidate.'),
  ui: profile({}, 'G refreshes repeated statistics every half second and avoids unchanged text writes. Every completed frame is still measured; camera, tracking, toggles and measurement transitions remain immediate.', false, false, null, 'sdk', false, true),
  'hair-release': profile({}, 'G with earlier release of the hair worker for the next owned image. The same model, rendering, image pairing and safeguards remain. Compare hair coverage, completed updates and frame age.', false, false, null, 'sdk', false, false, true),
} as const;
/** The unchanged G implementation is imported directly for these paths. */
export function usesBaseRenderer(id: Pipeline): boolean {return id === 'g' || id === 'lean' || id === 'mask' || id === 'publish' || id === 'ui' || id === 'hair-release' || PROFILES[id].captureRateHz !== null;}

/** Focused previews hide unrelated choices without removing them from the lab. */
export function studyPipelines(search: string): readonly Pipeline[] {
  const study = new URLSearchParams(search).get('study');
  return study === 'hair-delivery' ? ['g', 'hair-release'] : study === 'review' ? ['g', 'publish', 'region', 'lens', 'ui'] : study === 'mask' ? ['g', 'mask'] : PIPELINES;
}

/** An explicit preview link may select a candidate; ordinary entry remains G. */
export function initialPipeline(search: string): Pipeline {
  const value = new URLSearchParams(search).get('pipeline');
  return studyPipelines(search).includes(value as Pipeline) ? value as Pipeline : DEFAULT_PIPELINE;
}
