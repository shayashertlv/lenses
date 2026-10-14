/** Close worn framing from actual projected eyewear points. No portrait, invented ears or product-specific crop. */
import type {CropRect} from './junction.ts';

export function wornCropRect(projected: ArrayLike<number>, width: number, height: number): CropRect | null {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i + 2 < projected.length; i += 3) {
    const x = projected[i]!, y = projected[i + 1]!, z = projected[i + 2]!;
    if ([x, y, z].every(Number.isFinite) && z >= -1 && z <= 1) {
      minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
  }
  if (!Number.isFinite(minX)) return null;
  // Include some surrounding skin so the disappearance into the head has visible context. This is framing only.
  const spanX = maxX - minX, spanY = maxY - minY;
  const pad = Math.max(12, spanX * .06, spanY * .1);
  const x = Math.max(0, Math.floor(minX - pad)), y = Math.max(0, Math.floor(minY - pad));
  const right = Math.min(width, Math.ceil(maxX + pad)), bottom = Math.min(height, Math.ceil(maxY + pad));
  return right > x && bottom > y ? {x, y, width: right - x, height: bottom - y} : null;
}
