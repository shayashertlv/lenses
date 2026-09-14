import type {HairArmInput, HairMask as FullHairMask} from '../hair-arm-preview/compose.ts';

/** Live occlusion uses category winners. Confidence remains available for held diagnostics. */
export type HairMask = (FullHairMask & {outputMode?: 'full'})
  | (Omit<FullHairMask, 'confidence' | 'confidenceSHA256'> & {
    outputMode: 'category-only'; confidence?: never; confidenceSHA256?: never;
  });
export type LiveHairArmInput = Omit<HairArmInput, 'mask'> & {mask: HairMask};
