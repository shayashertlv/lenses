/** Test fixtures from the live site's public folder, read read-only from ../../ar/public (arview's Vite publicDir):
 *  the canonical face and the two GLBs the live site ships. They are fixtures, not a catalog: no runtime code lists
 *  them. The clip depths are the live catalog's values (ar/src/eyewear/catalog.ts at 75ae133). */
import type {ModelDefinition} from '../src/eyewear/catalog.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';

export const AR_PUBLIC = new URL('../../../ar/public/', import.meta.url);

/** A file under ar/public, by its site path ('models/x.glb' or '/models/x.glb'). */
export function publicFile(path: string): URL {return new URL(path.replace(/^\/+/, ''), AR_PUBLIC);}

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
