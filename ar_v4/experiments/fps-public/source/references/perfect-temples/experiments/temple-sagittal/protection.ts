import {Box3, Matrix4, PerspectiveCamera, Vector3, Vector4} from 'three';
import type {Detection} from '../../src/runtime/detector.ts';
import {VIRTUAL_CAMERA} from '../../src/render/projection.ts';
import {GLASSES_METERS_TO_CENTIMETERS} from '../../src/render/eyewear.ts';
import {validateProtection} from './contracts.ts';
import type {PixelRect, ProtectionConfiguration} from './contracts.ts';

const NASAL_AND_EYE_LANDMARKS = [1, 2, 4, 6, 33, 98, 133, 168, 197, 263, 327, 362] as const;
export interface RearBounds { optical: Box3; originalArms: Box3[]; candidateArms: Box3[]; }

export function protectionProjection(eyewearMatrix: readonly number[], offsetCm: readonly [number, number, number], aspect: number): Matrix4 {
  const camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, aspect, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  const asset = new Matrix4().makeTranslation(...offsetCm).scale(new Vector3().setScalar(GLASSES_METERS_TO_CENTIMETERS));
  return camera.projectionMatrix.clone().multiply(new Matrix4().fromArray(eyewearMatrix)).multiply(asset);
}

/** Bounds include hidden optical surfaces: this intentionally ignores depth. */
export function projectBounds(box: Box3, projection: Matrix4, width: number, height: number, margin: number): PixelRect | null {
  if (box.isEmpty() || ![...box.min.toArray(), ...box.max.toArray(), ...projection.elements].every(Number.isFinite)) {
    throw new Error('The projected temple bounds are invalid.');
  }
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
    const clip = new Vector4(x, y, z, 1).applyMatrix4(projection);
    // A near-plane crossing can make a projected AABB unreliable. Fall back.
    if (clip.w <= VIRTUAL_CAMERA.nearCm || clip.z < -clip.w || !clip.toArray().every(Number.isFinite)) {
      throw new Error('The temple bounds cross the projection near plane.');
    }
    const px = (clip.x / clip.w + 1) * width / 2, py = (1 - clip.y / clip.w) * height / 2;
    x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py);
  }
  return clippedRect(x0, y0, x1, y1, width, height, margin);
}

function clippedRect(x0: number, y0: number, x1: number, y1: number, width: number, height: number, margin: number): PixelRect | null {
  const rect = {x0: Math.max(0, Math.floor(x0 - margin)), y0: Math.max(0, Math.floor(y0 - margin)),
    x1: Math.min(width, Math.ceil(x1 + margin)), y1: Math.min(height, Math.ceil(y1 + margin))};
  return rect.x1 > rect.x0 && rect.y1 > rect.y0 ? rect : null;
}

export function createProtection(bounds: RearBounds, eyewearMatrix: readonly number[], offsetCm: readonly [number, number, number],
  landmarks: Detection['landmarks'], width: number, height: number, sourceAspect = width / height): ProtectionConfiguration | null {
  try {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
      || eyewearMatrix.length !== 16 || !eyewearMatrix.every(Number.isFinite)
      || !offsetCm.every(Number.isFinite) || landmarks.length < 468 || !Number.isFinite(sourceAspect) || sourceAspect <= 0) return null;
    const projection = protectionProjection(eyewearMatrix, offsetCm, sourceAspect);
    const optical = projectBounds(bounds.optical, projection, width, height, 4);
    if (!optical) return null;
    const points = NASAL_AND_EYE_LANDMARKS.map(index => landmarks[index]!);
    if (points.some(point => !point || !Number.isFinite(point.x) || !Number.isFinite(point.y))) return null;
    const xs = points.map(point => point.x * width), ys = points.map(point => point.y * height);
    const nasalMargin = Math.max(4, (Math.max(...xs) - Math.min(...xs)) * .08);
    const nasal = clippedRect(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys), width, height, nasalMargin);
    if (!nasal || bounds.originalArms.length !== bounds.candidateArms.length || bounds.originalArms.length > 2) return null;
    const editableRects: PixelRect[] = [];
    for (let index = 0; index < bounds.originalArms.length; index++) {
      const corridor = bounds.originalArms[index]!.clone().union(bounds.candidateArms[index]!);
      const projected = projectBounds(corridor, projection, width, height, 4);
      if (projected) editableRects.push(projected);
    }
    const configuration: ProtectionConfiguration = {method: 'temple-optics-copy-v1', width, height, marginPx: 4,
      protectedRects: [optical, nasal], editableRects};
    validateProtection(configuration, width, height);
    return configuration;
  } catch { return null; }
}

export const containsPixel = (rect: PixelRect, x: number, y: number): boolean => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;

/** Integer selection, with a final authoritative copy under every protection. */
export function composeProtectedPixels(baseline: Uint8ClampedArray, candidate: Uint8ClampedArray,
  protection: ProtectionConfiguration): {pixels: Uint8ClampedArray; changedPixels: number; editablePixels: number; protectedPixels: number} {
  const {width, height} = protection;
  validateProtection(protection, width, height);
  if (baseline.length !== width * height * 4 || candidate.length !== baseline.length) throw new Error('The paired pixel sizes differ.');
  const pixels = baseline.slice();
  let changedPixels = 0, editablePixels = 0, protectedPixels = 0;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const protectedPixel = protection.protectedRects.some(rect => containsPixel(rect, x, y));
    if (protectedPixel) { protectedPixels++; continue; }
    if (!protection.editableRects.some(rect => containsPixel(rect, x, y))) continue;
    editablePixels++;
    const index = (y * width + x) * 4;
    let changed = false;
    for (let channel = 0; channel < 4; channel++) {
      const at = index + channel;
      pixels[at] = candidate[at]!;
      changed ||= pixels[at] !== baseline[at];
    }
    changedPixels += Number(changed);
  }
  // Keep this final copy explicit: no tone mapping, blending or filtering may
  // follow it. It also makes overlapping edit/protection rectangles harmless.
  for (const rect of protection.protectedRects) for (let y = rect.y0; y < rect.y1; y++) {
    const start = (y * width + rect.x0) * 4, end = (y * width + rect.x1) * 4;
    pixels.set(baseline.subarray(start, end), start);
  }
  return {pixels, changedPixels, editablePixels, protectedPixels};
}
