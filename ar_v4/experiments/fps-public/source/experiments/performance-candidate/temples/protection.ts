import {validateProtection} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {PixelRect, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

export {createProtection, protectionProjection, projectBounds, containsPixel}
  from '../../../references/perfect-temples/experiments/temple-sagittal/protection.ts';

type Span = [number, number];

/** Exact half-open union; touching/overlapping rectangles must count only once. */
function rowSpans(rectangles: readonly PixelRect[], y: number): Span[] {
  const sorted = rectangles.filter(rect => y >= rect.y0 && y < rect.y1)
    .map(rect => [rect.x0, rect.x1] as Span).sort((a, b) => a[0] - b[0]);
  const spans: Span[] = [];
  for (const span of sorted) {
    const previous = spans.at(-1);
    if (previous && span[0] <= previous[1]) previous[1] = Math.max(previous[1], span[1]);
    else spans.push(span);
  }
  return spans;
}

/** Same integer selection and final protection as the accepted full-screen scan. */
export function composeProtectedPixels(baseline: Uint8ClampedArray, candidate: Uint8ClampedArray,
  protection: ProtectionConfiguration): {pixels: Uint8ClampedArray; changedPixels: number; editablePixels: number; protectedPixels: number} {
  const {width, height} = protection;
  validateProtection(protection, width, height);
  if (baseline.length !== width * height * 4 || candidate.length !== baseline.length) throw new Error('The paired pixel sizes differ.');
  const pixels = baseline.slice();
  let changedPixels = 0, editablePixels = 0, protectedPixels = 0;
  for (let y = 0; y < height; y++) {
    const protectedSpans = rowSpans(protection.protectedRects, y);
    for (const [start, end] of protectedSpans) protectedPixels += end - start;
    const copySpan = (start: number, end: number): void => {
      editablePixels += end - start;
      for (let x = start; x < end; x++) {
        const index = (y * width + x) * 4;
        let changed = false;
        for (let channel = 0; channel < 4; channel++) {
          const at = index + channel;
          pixels[at] = candidate[at]!;
          changed ||= pixels[at] !== baseline[at];
        }
        changedPixels += Number(changed);
      }
    };
    for (const [start, end] of rowSpans(protection.editableRects, y)) {
      let cursor = start;
      for (const [protectedStart, protectedEnd] of protectedSpans) {
        if (protectedEnd <= cursor) continue;
        if (protectedStart >= end) break;
        if (protectedStart > cursor) copySpan(cursor, Math.min(end, protectedStart));
        cursor = Math.max(cursor, protectedEnd);
        if (cursor >= end) break;
      }
      if (cursor < end) copySpan(cursor, end);
    }
  }
  // This remains the final authoritative operation, including overlapping guards.
  for (const rect of protection.protectedRects) for (let y = rect.y0; y < rect.y1; y++) {
    const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
    pixels.set(baseline.subarray(start, end), start);
  }
  return {pixels, changedPixels, editablePixels, protectedPixels};
}
