export const FPS_MODES = ['g', 'queries', 'identity', 'combined', 'compose', 'bookkeeping', 'next-combined'] as const;
export type FpsMode = typeof FPS_MODES[number];
export function parseFpsMode(search: string): FpsMode {
  const value=new URLSearchParams(search).get('fps');
  return FPS_MODES.find(mode=>mode===value) ?? 'g';
}
const mode=parseFpsMode(typeof location==='undefined'?'':location.search);
/** Fixed for this page/session. Switching mode starts a new session. */
export function optionsForFpsMode(mode:FpsMode) {
  return Object.freeze({mode,queries:mode==='queries'||mode==='combined',identity:mode==='identity'||mode==='combined',
    cpuCompose:mode==='compose'||mode==='next-combined',bookkeeping:mode==='bookkeeping'||mode==='next-combined'});
}
export const FPS_OPTIONS=optionsForFpsMode(mode);
