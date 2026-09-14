import type {FaceDelegate} from '../performance-stage2/face-timing.ts';

/** Runtime options of the G page, URL-gated. Defaults reproduce the accepted G Combined pipeline
 *  with one owner-approved change (perfecto_17fps, 2026-09-14): the face landmarker runs on the
 *  CPU delegate (XNNPACK) by default. On the owner's laptop (mains, real 1280x720 camera,
 *  GPU/CPU/GPU/CPU sessions) this measured +12 % presented fps, −11 % p95 frame age and no
 *  >100 ms gaps, because the landmarker no longer competes with hair, beauty and readbacks on
 *  Chrome's single GPU-process thread. Landmarks differ from the GPU delegate at float-noise level.
 *  `?face=gpu` restores the previous GPU-then-CPU order; `?hair=640` sends the hair segmenter a
 *  640-max-edge copy of the source (the mask comes back at that size and the frozen compose samples
 *  it); `?tx=0.5` halves Three's transmission target. The last two remain measurement-only options. */
export interface SpeedExperiments {
  faceDelegate: FaceDelegate | null;
  hairMaxEdge: number | null;
  transmissionResolutionScale: number;
}
/** Accepted default since perfecto_17fps: CPU face landmarker; null would mean the pre-2026-09-14 GPU-then-CPU order. */
export const DEFAULT_FACE_DELEGATE: FaceDelegate = 'CPU';
export const SPEED_EXPERIMENTS: SpeedExperiments = {faceDelegate: DEFAULT_FACE_DELEGATE, hairMaxEdge: null, transmissionResolutionScale: 1};

export function parseSpeedExperiments(search: string): SpeedExperiments {
  const params = new URLSearchParams(search);
  const face = params.get('face')?.toLowerCase() ?? null;
  const hair = params.get('hair'), tx = params.get('tx');
  const hairMaxEdge = hair === null ? null : Number(hair);
  const scale = tx === null ? 1 : Number(tx);
  return {
    // `gpu` = previous production order (GPU then CPU fallback, expressed as null); `cpu` or absent = accepted CPU default.
    faceDelegate: face === 'gpu' ? null : DEFAULT_FACE_DELEGATE,
    hairMaxEdge: hairMaxEdge !== null && Number.isInteger(hairMaxEdge) && hairMaxEdge >= 128 && hairMaxEdge <= 4096 ? hairMaxEdge : null,
    transmissionResolutionScale: Number.isFinite(scale) && scale > 0 && scale <= 1 ? scale : 1,
  };
}

export function applySpeedExperimentParams(search: string): SpeedExperiments {
  Object.assign(SPEED_EXPERIMENTS, parseSpeedExperiments(search));
  return SPEED_EXPERIMENTS;
}

/** Describes only deviations from the accepted defaults; an empty string means the accepted pipeline. */
export function describeSpeedExperiments(value: SpeedExperiments = SPEED_EXPERIMENTS): string {
  const parts: string[] = [];
  if (value.faceDelegate !== DEFAULT_FACE_DELEGATE) parts.push(`face landmarker on the previous ${value.faceDelegate ?? 'GPU-then-CPU'} order instead of the accepted CPU delegate`);
  if (value.hairMaxEdge !== null) parts.push(`hair input capped at ${value.hairMaxEdge} px max edge`);
  if (value.transmissionResolutionScale !== 1) parts.push(`lens transmission target at ${value.transmissionResolutionScale}x`);
  return parts.length ? `Speed experiment active (pixels may differ from the accepted pipeline): ${parts.join('; ')}.` : '';
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
