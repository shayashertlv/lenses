import type {Detection} from '../../src/runtime/detector.ts';
import type {HairMask} from './live-mask.ts';
import type {PixelRect} from '../temple-sagittal/contracts.ts';

export type LiveVariant = 'accepted' | 'hair';
export interface OwnedPixels {width: number; height: number; pixels: Uint8ClampedArray;}

/** Same independent landmark ROI used by the frozen six-portrait study. */
export function liveNasalRoi(detection: Detection, width: number, height: number): PixelRect | null {
  if (!detection.matrix || detection.landmarks.length !== 478 || !Number.isInteger(width) || !Number.isInteger(height)
    || width <= 0 || height <= 0) return null;
  const points = [1, 2, 4, 6, 98, 168, 197, 327].map(index => detection.landmarks[index]!);
  if (points.some(point => !point || !Number.isFinite(point.x) || !Number.isFinite(point.y))) return null;
  const margin = Math.max(3, width * .006);
  const x0 = Math.max(0, Math.floor(Math.min(...points.map(point => point.x)) * width - margin));
  const y0 = Math.max(0, Math.floor(Math.min(...points.map(point => point.y)) * height - margin));
  const x1 = Math.min(width, Math.ceil(Math.max(...points.map(point => point.x)) * width + margin));
  const y1 = Math.min(height, Math.ceil(Math.max(...points.map(point => point.y)) * height + margin));
  return x1 > x0 && y1 > y0 ? {x0, y0, x1, y1} : null;
}

export function copyHairMask<T extends HairMask>(mask: T): T {
  return {...mask, labels: [...mask.labels], category: mask.category.slice(),
    ...(mask.outputMode === 'category-only' ? {} : {confidence: mask.confidence.slice()})};
}

/** Only current-pair bytes exist; reads and toggles never borrow caller storage. */
export class OwnedLiveOutputs {
  private before: OwnedPixels | null = null;
  private after: OwnedPixels | null = null;
  replace(width: number, height: number, before: Uint8ClampedArray, after: Uint8ClampedArray = before): void {
    this.clear();
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
      || before.length !== width * height * 4 || after.length !== before.length) throw new Error('The live output pair dimensions differ.');
    this.before = {width, height, pixels: before.slice()}; this.after = {width, height, pixels: after.slice()};
  }
  copy(variant: LiveVariant): OwnedPixels | null {
    const value = variant === 'accepted' ? this.before : this.after;
    return value ? {...value, pixels: value.pixels.slice()} : null;
  }
  clear(): void { this.before = null; this.after = null; }
}
