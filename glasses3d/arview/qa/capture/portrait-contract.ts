/** The portrait's pure contract (port of 75ae133:ar/qa/portrait-preview-contract.mjs): the eye-detail framing, the
 *  face-a pin, and the console triage. The old manifest validator is gone: it hard-threw on a `width_mm` the renderer
 *  never read (PORT_REVIEW §7), and the capture pins face-a itself. */

/** The only face image arview uses: its pinned qa/fixtures/face-a.jpg (StyleGAN2 synthetic, 1024x1024, 552034 bytes). */
import {ASSET_PINS} from '../../src/asset-pins.ts';
export const FACE_A_SHA256 = ASSET_PINS['qa/fixtures/face-a.jpg'];
export const FACE_A_RELATIVE_PATH = 'qa/fixtures/face-a.jpg';

interface Point {readonly x: number; readonly y: number;}
export interface EyeCrop {readonly x: number; readonly y: number; readonly width: number; readonly height: number;}

/** QA capture framing from landmarks 33/263, independent of model geometry and lighting: 2.15x the eye span wide, 1.05x
 *  high, shifted down 0.1 span, clamped inside the image. Throws for invalid input or an unsuitable eye span. */
export function eyeCrop(landmarks: readonly (Point | undefined)[], width: number, height: number): EyeCrop {
  const a = landmarks?.[33], b = landmarks?.[263];
  if (![width, height].every(n => Number.isInteger(n) && n > 0)
      || !a || !b || ![a.x, a.y, b.x, b.y].every(Number.isFinite)) throw new Error('Invalid portrait landmarks or dimensions');
  const span = Math.hypot((a.x - b.x) * width, (a.y - b.y) * height);
  if (span < width * .08 || span > width * .8) throw new Error('Portrait eye span is unsuitable for a detail capture');
  const cropWidth = Math.min(width, Math.ceil(span * 2.15));
  const cropHeight = Math.min(height, Math.ceil(span * 1.05));
  const x = Math.max(0, Math.min(width - cropWidth, Math.floor((a.x + b.x) * width / 2 - cropWidth / 2)));
  const y = Math.max(0, Math.min(height - cropHeight, Math.floor((a.y + b.y) * height / 2 + span * .1 - cropHeight / 2)));
  return {x, y, width: cropWidth, height: cropHeight};
}

/** MediaPipe emits this known successful CPU initialization notice through stderr: informational. Every other console
 *  error stays an error (the capture reports it). */
export function consoleLevel(type: string, text: string): string {
  return type === 'error' && text === 'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.' ? 'info' : type;
}
