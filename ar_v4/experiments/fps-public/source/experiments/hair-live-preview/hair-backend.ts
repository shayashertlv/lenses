import type {HairDelegate} from './hair-protocol.ts';

export interface HairBackend {
  requested: HairDelegate;
  active: HairDelegate | null;
  renderer: string | null;
  fallbackReason: string | null;
}

/** Software WebGL has no demonstrated advantage over the exact CPU path. */
export function chooseHairDelegate(renderer: string | null, webglAvailable = true): HairDelegate {
  if (!webglAvailable || /swiftshader|llvmpipe|lavapipe|software|basic render driver/i.test(renderer ?? '')) return 'CPU';
  return 'GPU';
}

/** Probe only a disposable local context; no accepted scene or graphics state is changed. */
export function detectHairBackend(): HairBackend {
  const canvas = document.createElement('canvas');
  let gl: WebGL2RenderingContext | null = null, renderer: string | null = null;
  try {
    gl = canvas.getContext('webgl2');
    if (gl) {
      const debug = gl.getExtension('WEBGL_debug_renderer_info');
      const value: unknown = gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
      renderer = typeof value === 'string' ? value : null;
    }
    return {requested: chooseHairDelegate(renderer, !!gl), active: null, renderer, fallbackReason: null};
  } catch {
    return {requested: 'CPU', active: null, renderer, fallbackReason: 'Graphics acceleration could not be inspected.'};
  } finally {
    try { gl?.getExtension('WEBGL_lose_context')?.loseContext(); } catch { /* Optional probe cleanup cannot block the mirror. */ }
    canvas.width = canvas.height = 0;
  }
}
