/** Public side names always identify the wearer. Internal geometry arrays stay in X order. */
export type WearerSide = 'left' | 'right';
export const SIDE_CONVENTION = "wearer: +X left, -X right";
export const WEARER_SIDES = Object.freeze([['right', -1], ['left', 1]] as const);
export const SIDE_NAMES: readonly [WearerSide, WearerSide] = Object.freeze(['right', 'left'] as const);
export const wearerSide = (index: 0 | 1): WearerSide => SIDE_NAMES[index];
