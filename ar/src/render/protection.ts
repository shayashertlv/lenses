/** The protection geometry of a posed frame: the optical rectangle (every referenced vertex the rear drop cannot
 *  deform, projected with a margin) and the nasal rectangle (nose and inner-eye landmarks) are protected; the arm
 *  corridors (original plus dropped arm bounds) are the only editable region. The renderer's stencil guard marks these
 *  rectangles, and the audit checks them on read-back pixels. Bounds include hidden optical surfaces: this
 *  intentionally ignores depth. */
import {Box3, Matrix4, PerspectiveCamera, Vector3, Vector4} from 'three';
import type {Detection} from '../face/protocol.ts';
import {VIRTUAL_CAMERA} from './projection.ts';
import {GLASSES_METERS_TO_CENTIMETERS} from '../eyewear/catalog.ts';

export interface PixelRect { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number; }
export const PROTECTION_METHOD = 'temple-optics-copy-v1';
export interface ProtectionConfiguration {
  readonly method: typeof PROTECTION_METHOD;
  readonly width: number;
  readonly height: number;
  readonly marginPx: number;
  readonly protectedRects: readonly PixelRect[];
  readonly editableRects: readonly PixelRect[];
}

export function validateProtection(value: ProtectionConfiguration, width: number, height: number): void {
  if (!value || value.method !== PROTECTION_METHOD || value.width !== width || value.height !== height
    || !Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
    || !Number.isFinite(value.marginPx) || value.marginPx < 3 || value.marginPx > 64
    || !Array.isArray(value.protectedRects) || value.protectedRects.length < 2 || value.protectedRects.length > 8
    || !Array.isArray(value.editableRects) || value.editableRects.length > 4) {
    throw new Error('The temple protection is invalid.');
  }
  for (const rect of [...value.protectedRects, ...value.editableRects]) {
    if (!rect || ![rect.x0, rect.y0, rect.x1, rect.y1].every(Number.isInteger)
      || rect.x0 < 0 || rect.y0 < 0 || rect.x1 > width || rect.y1 > height || rect.x1 <= rect.x0 || rect.y1 <= rect.y0) {
      throw new Error('The temple protection rectangle is invalid.');
    }
  }
}

export const containsPixel = (rect: PixelRect, x: number, y: number): boolean => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;

const NASAL_AND_EYE_LANDMARKS = [1, 2, 4, 6, 33, 98, 133, 168, 197, 263, 327, 362] as const;
export interface RearBounds { optical: Box3; originalArms: Box3[]; candidateArms: Box3[]; }

export function protectionProjection(eyewearMatrix: readonly number[], offsetCm: readonly [number, number, number], aspect: number): Matrix4 {
  const camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, aspect, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  const asset = new Matrix4().makeTranslation(...offsetCm).scale(new Vector3().setScalar(GLASSES_METERS_TO_CENTIMETERS));
  return camera.projectionMatrix.clone().multiply(new Matrix4().fromArray(eyewearMatrix)).multiply(asset);
}

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
    const configuration: ProtectionConfiguration = {method: PROTECTION_METHOD, width, height, marginPx: 4,
      protectedRects: [optical, nasal], editableRects};
    validateProtection(configuration, width, height);
    return configuration;
  } catch { return null; }
}

/** The independent nasal region (nose landmarks with a margin), protected alongside the optical rectangle. */
export function nasalRoi(detection: Detection, width: number, height: number): PixelRect | null {
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
