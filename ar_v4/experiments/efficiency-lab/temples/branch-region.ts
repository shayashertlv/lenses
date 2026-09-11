import {validateProtection} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';
import type {PixelRect, ProtectionConfiguration} from '../../../references/perfect-temples/experiments/temple-sagittal/contracts.ts';

export interface BranchRegionMetrics {requested: boolean; used: boolean; rectangle: PixelRect | null;
  fullBytes: number; readBytes: number; fallback: string | null;}
export const emptyBranchRegionMetrics = (requested = false): BranchRegionMetrics =>
  ({requested, used: false, rectangle: null, fullBytes: 0, readBytes: 0, fallback: null});

/** Full-width top-down band containing every editable-minus-protected pixel.
 * The draw, viewport, MSAA, transmission and all protections remain unchanged. */
export function editableRowBand(protection: ProtectionConfiguration): PixelRect | null {
  const {width, height} = protection;
  validateProtection(protection, width, height);
  let y0 = height, y1 = 0;
  for (let y = 0; y < height; y++) {
    const protectedSpans = protection.protectedRects.filter(rect => rect.y0 <= y && y < rect.y1)
      .map(rect => [rect.x0, rect.x1] as const).sort((a, b) => a[0] - b[0]);
    for (const edit of protection.editableRects) {
      if (y < edit.y0 || y >= edit.y1) continue;
      let cursor = edit.x0;
      for (const [start, end] of protectedSpans) {
        if (end <= cursor) continue;
        if (start > cursor || start >= edit.x1) break;
        cursor = Math.max(cursor, end);
        if (cursor >= edit.x1) break;
      }
      if (cursor < edit.x1) {y0 = Math.min(y0, y); y1 = y + 1; break;}
    }
  }
  return y1 > y0 ? {x0: 0, y0, x1: width, y1} : null;
}

/** Independent full-sized composition input. Bytes outside the band come from
 * baseline and must never be represented as an original raw branch diagnostic. */
export function readBranchRegion(native: HTMLCanvasElement, baseline: ImageData,
  rectangle: PixelRect | null): {pixels: ImageData; readCalls: number; readBytes: number} {
  const {width, height} = native;
  if (baseline.width !== width || baseline.height !== height || baseline.data.length !== width * height * 4)
    throw new Error('The branch region does not match the owned baseline dimensions.');
  if (rectangle && (!Object.values(rectangle).every(Number.isInteger) || rectangle.x0 !== 0
    || rectangle.x1 !== width || rectangle.y0 < 0 || rectangle.y1 > height || rectangle.y1 <= rectangle.y0))
    throw new Error('The branch region is not a valid full-width row band.');
  const output = baseline.data.slice();
  if (!rectangle) return {pixels: new ImageData(output, width, height), readCalls: 0, readBytes: 0};
  const gl = native.getContext('webgl2');
  if (!gl || gl.isContextLost()) throw new Error('The branch region framebuffer is unavailable.');
  const rows = rectangle.y1 - rectangle.y0, stride = width * 4, bottom = new Uint8Array(rows * stride);
  const read = gl.getParameter(gl.READ_FRAMEBUFFER_BINDING) as WebGLFramebuffer | null;
  const pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING) as WebGLBuffer | null;
  const keys = [gl.PACK_ALIGNMENT, gl.PACK_ROW_LENGTH, gl.PACK_SKIP_PIXELS, gl.PACK_SKIP_ROWS];
  const values = keys.map(key => Number(gl.getParameter(key)));
  try {
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null); gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
    keys.forEach(key => gl.pixelStorei(key, key === gl.PACK_ALIGNMENT ? 1 : 0));
    gl.readPixels(0, height - rectangle.y1, width, rows, gl.RGBA, gl.UNSIGNED_BYTE, bottom);
    const error = gl.getError();
    if (error !== gl.NO_ERROR || gl.isContextLost()) throw new Error(`Branch region readback failed (GL 0x${error.toString(16)}).`);
    for (let y = 0; y < rows; y++) output.set(bottom.subarray((rows - y - 1) * stride, (rows - y) * stride),
      (rectangle.y0 + y) * stride);
  } finally {
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, read); gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack);
    keys.forEach((key, index) => gl.pixelStorei(key, values[index]!));
  }
  return {pixels: new ImageData(output, width, height), readCalls: 1, readBytes: bottom.byteLength};
}
