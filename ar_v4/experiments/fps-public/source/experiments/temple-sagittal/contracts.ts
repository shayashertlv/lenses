export const REAR_DROP_METHOD = 'temple-rear-drop-v1';
export interface RearDropConfiguration {
  readonly method: typeof REAR_DROP_METHOD;
  readonly dropM: number;
}

export function validateRearDrop(value: RearDropConfiguration): void {
  if (!value || value.method !== REAR_DROP_METHOD || !Number.isFinite(value.dropM) || value.dropM < 0 || value.dropM > .03) {
    throw new Error('The saved rear-drop configuration is invalid.');
  }
}

export interface PixelRect { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number; }
export interface ProtectionConfiguration {
  readonly method: 'temple-optics-copy-v1';
  readonly width: number;
  readonly height: number;
  readonly marginPx: number;
  readonly protectedRects: readonly PixelRect[];
  readonly editableRects: readonly PixelRect[];
}

export function validateProtection(value: ProtectionConfiguration, width: number, height: number): void {
  if (!value || value.method !== 'temple-optics-copy-v1' || value.width !== width || value.height !== height
    || !Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
    || !Number.isFinite(value.marginPx) || value.marginPx < 3 || value.marginPx > 64
    || !Array.isArray(value.protectedRects) || value.protectedRects.length < 2 || value.protectedRects.length > 8
    || !Array.isArray(value.editableRects) || value.editableRects.length > 4) {
    throw new Error('The saved temple protection is invalid.');
  }
  for (const rect of [...value.protectedRects, ...value.editableRects]) {
    if (!rect || ![rect.x0, rect.y0, rect.x1, rect.y1].every(Number.isInteger)
      || rect.x0 < 0 || rect.y0 < 0 || rect.x1 > width || rect.y1 > height || rect.x1 <= rect.x0 || rect.y1 <= rect.y0) {
      throw new Error('The saved temple protection rectangle is invalid.');
    }
  }
}
