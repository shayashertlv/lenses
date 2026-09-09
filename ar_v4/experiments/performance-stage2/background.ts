/** The original native renderer now owns this same-image camera output. */
export function requireSharedCamera(image: ImageData | null, width: number, height: number,
  sourceWidth: number, sourceHeight: number, failureReason: string | null): {pixels: Uint8ClampedArray} {
  if (width !== Math.min(sourceWidth, 1280) || height !== Math.max(1, Math.round(width * sourceHeight / sourceWidth)))
    throw new Error('The saved viewport differs from the accepted camera sizing rule.');
  if (!image || image.width !== width || image.height !== height || image.data.length !== width * height * 4)
    throw new Error(failureReason ?? 'The current pair has no shared native camera pixels.');
  return {pixels: image.data};
}
