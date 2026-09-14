import type {FaceDelegate} from '../performance-stage2/face-timing.ts';

/** Opt-in, URL-gated speed experiments for the G page. Every default reproduces G exactly.
 *  These change pixels (landmarks, hair edges or lens refraction) and are for measurement,
 *  not promotion. `?face=cpu` forces the face landmarker onto the CPU delegate; `?hair=640`
 *  sends the hair segmenter a 640-max-edge copy of the source (the mask comes back at that
 *  size and the frozen compose samples it); `?tx=0.5` halves Three's transmission target. */
export interface SpeedExperiments {
  faceDelegate: FaceDelegate | null;
  hairMaxEdge: number | null;
  transmissionResolutionScale: number;
}
export const SPEED_EXPERIMENTS: SpeedExperiments = {faceDelegate: null, hairMaxEdge: null, transmissionResolutionScale: 1};

export function parseSpeedExperiments(search: string): SpeedExperiments {
  const params = new URLSearchParams(search);
  const face = params.get('face')?.toLowerCase() ?? null;
  const hair = params.get('hair'), tx = params.get('tx');
  const hairMaxEdge = hair === null ? null : Number(hair);
  const scale = tx === null ? 1 : Number(tx);
  return {
    faceDelegate: face === 'cpu' ? 'CPU' : face === 'gpu' ? 'GPU' : null,
    hairMaxEdge: hairMaxEdge !== null && Number.isInteger(hairMaxEdge) && hairMaxEdge >= 128 && hairMaxEdge <= 4096 ? hairMaxEdge : null,
    transmissionResolutionScale: Number.isFinite(scale) && scale > 0 && scale <= 1 ? scale : 1,
  };
}

export function applySpeedExperimentParams(search: string): SpeedExperiments {
  Object.assign(SPEED_EXPERIMENTS, parseSpeedExperiments(search));
  return SPEED_EXPERIMENTS;
}

export function describeSpeedExperiments(value: SpeedExperiments = SPEED_EXPERIMENTS): string {
  const parts: string[] = [];
  if (value.faceDelegate) parts.push(`face landmarker forced to the ${value.faceDelegate} delegate`);
  if (value.hairMaxEdge !== null) parts.push(`hair input capped at ${value.hairMaxEdge} px max edge`);
  if (value.transmissionResolutionScale !== 1) parts.push(`lens transmission target at ${value.transmissionResolutionScale}x`);
  return parts.length ? `Speed experiment active (pixels may differ from G): ${parts.join('; ')}.` : '';
}

/** Downscale the owned source for the hair segmenter when an edge cap is active; otherwise the original canvas. */
export function hairInputBitmap(canvas: HTMLCanvasElement, maxEdge: number | null | undefined): Promise<ImageBitmap> {
  const scale = maxEdge ? Math.min(1, maxEdge / Math.max(canvas.width, canvas.height)) : 1;
  if (scale >= 1) return createImageBitmap(canvas);
  const small = document.createElement('canvas');
  small.width = Math.max(1, Math.round(canvas.width * scale)); small.height = Math.max(1, Math.round(canvas.height * scale));
  const context = small.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'});
  if (!context) throw new Error('The hair input canvas is unavailable.');
  context.drawImage(canvas, 0, 0, small.width, small.height);
  return createImageBitmap(small).finally(() => {small.width = small.height = 0;});
}
