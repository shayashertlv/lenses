import {normalizeSpeedOptions} from './speed-options.ts';
export const PIPELINES = ['base', 'source', 'fresh', 'warm', 'copies', 'async', 'overlap', 'combined'] as const;
export type Pipeline = typeof PIPELINES[number];
export const DEFAULT_PIPELINE: Pipeline = 'combined';
/** Owner selection is additive to legacy experiment metadata: it does not mark
 * every selectable experiment or historical evidence record as accepted. */
export const CURRENT_BASE_METADATA = Object.freeze({currentBase: 'combined', ownerSelectedG: true, previousBase: 'base'} as const);
export const PIPELINE_LABELS: Record<Pipeline, string> = {
  base: 'Previous base · Test 2', source: 'A · Reuse camera pixels', fresh: 'B · Fresh-frame scheduling',
  warm: 'C · Prewarm temples', copies: 'D · Fewer image copies', async: 'E · Async GPU downloads',
  overlap: 'F · Overlap inference', combined: 'Current · G Combined',
};
export const PROFILES = {
  base: {options: normalizeSpeedOptions(), mode: 'baseline', detail: 'The previous Test 2 renderer and serial frame scheduling, retained for comparison.'},
  source: {options: normalizeSpeedOptions({reuseSourcePixels:true}), mode: 'baseline', detail: 'Reuse exact owned camera pixels when eligible, preserving the native fallback.'},
  fresh: {options: normalizeSpeedOptions(), mode: 'fresh', detail: 'Process the freshest captured image without waiting for another future callback. One replaceable pending image.'},
  warm: {options: normalizeSpeedOptions({prewarmTemples:true}), mode: 'baseline', detail: 'Warm the nonzero temple path on the first tracked image. Compare first-use pauses in a fresh camera session.'},
  copies: {options: normalizeSpeedOptions({fewerCopies:true}), mode: 'baseline', detail: 'Borrow the owned source internally and retain independent pixels for Hold.'},
  async: {options: normalizeSpeedOptions({asyncReadback:true}), mode: 'baseline', detail: 'Queue native GPU pixel downloads and wait without blocking the page, with a native fallback.'},
  overlap: {options: normalizeSpeedOptions(), mode: 'overlap', detail: 'Overlap next-image inference with current-image preparation. Each image keeps its own pose and mask.'},
  combined: {options: normalizeSpeedOptions({reuseSourcePixels:true,fewerCopies:true,asyncReadback:true,prewarmTemples:true}), mode: 'overlap', detail: 'Current G Combined: pixel reuse, fewer copies, prewarming, async downloads and bounded inference overlap.'},
} as const;
export function usesBaseRenderer(id: Pipeline): boolean { return id === 'base' || id === 'fresh' || id === 'overlap'; }
