/** Viewer-owned pinned fixtures, bootstrapped from immutable Git objects by npm run assets.
 *  The canonical face is public; model fixtures stay in qa/fixtures, outside the page's public directory.
 *  Clip depths preserve the source snapshot's values (ar/src/eyewear/catalog.ts at 75ae133). */
import type {ModelDefinition} from '../src/eyewear/catalog.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';

export const AR_PUBLIC = new URL('../public/', import.meta.url);

/** A viewer-owned fixture, addressed by its historical site path. */
export function publicFile(path: string): URL {
  const relative = path.replace(/^\/+/, '');
  return new URL(relative, relative.endsWith('.glb') ? new URL('../qa/fixtures/', import.meta.url) : AR_PUBLIC);
}

export interface ShippedModel extends ModelDefinition {
  readonly id: string;
  /** Site path, as the page fetches it. */
  readonly url: string;
  readonly templeClipZM: number;
  readonly offsetCm: readonly [number, number, number];
}

export const SHIPPED_MODELS: readonly ShippedModel[] = Object.freeze([
  Object.freeze({id: 'amber-horizon', displayName: 'Amber Horizon', url: '/models/amber-horizon.glb',
    templeClipZM: -0.140, offsetCm: GLASSES_OFFSET_CM}),
  Object.freeze({id: 'tom-ford-clear', displayName: 'Tom Ford', url: '/models/tom-ford-clear.glb',
    templeClipZM: -0.148, offsetCm: GLASSES_OFFSET_CM}),
]);

export function shippedModel(id: string): ShippedModel {
  const model = SHIPPED_MODELS.find(entry => entry.id === id);
  if (!model) throw new Error(`No shipped fixture ${id}`);
  return model;
}
