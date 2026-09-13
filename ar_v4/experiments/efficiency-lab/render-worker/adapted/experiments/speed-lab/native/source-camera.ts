import type {OwnedSourceFrame} from '../speed-options.ts';
import {sourcePixelsOpaque} from '../speed-options.ts';

/** No pixel transform is inferred from a hash. The caller retains the exact
 * opaque sRGB image that was hashed, at the unchanged native viewport size. */
export function sourceCameraEligibility(source: OwnedSourceFrame | undefined, width: number, height: number): string | null {
  if (!source) return 'No owned source RGBA was supplied.';
  if (!source.isCurrent()) throw new DOMException('The owned source frame is stale.', 'AbortError');
  if (source.colorSpace !== 'srgb' || source.rgba.colorSpace !== 'srgb') return 'Source color space is not explicit sRGB.';
  if (source.canvas.width !== width || source.canvas.height !== height || source.rgba.width !== width || source.rgba.height !== height
    || source.rgba.data.length !== width * height * 4) return 'Source and native viewport dimensions differ.';
  if (!sourcePixelsOpaque(source.rgba)) return 'Source pixels are not fully opaque.';
  return null;
}
