/**
 * Loading the template mesh outside a browser, and the shared fixtures every
 * test builds on.
 *
 * The template lives in `../ar/assets/face/` during the migration rather than
 * being copied: it is 46 KB and identical, and two copies of a template mesh is
 * two things that can drift. `serve.py` maps the same path for the browser. When
 * `ar/` is retired the file moves here and this constant is the only thing that
 * changes.
 *
 * Memoised because building the basis and the regions costs ~30 ms (Dijkstra
 * over six regions) and a test file that rebuilds it per case spends more time
 * in fixtures than in the code under test.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { parseFaceObj, standardRegions, type FaceMesh, type Region } from '../core/mesh.js';
import { buildAnthropometricBasis } from '../core/shape/anthropometric.js';
import type { ShapeBasis } from '../core/shape/basis.js';

const here = dirname(fileURLToPath(import.meta.url));

/** Candidate locations, in order. The first that exists wins. */
const TEMPLATE_PATHS = [
  resolve(here, '../../assets/face/canonical_face_model.obj'),
  resolve(here, '../../../assets/face/canonical_face_model.obj'),
  resolve(here, '../../../../ar/assets/face/canonical_face_model.obj'),
  resolve(here, '../../../ar/assets/face/canonical_face_model.obj'),
];

let meshCache: FaceMesh | null = null;
let basisCache: ShapeBasis | null = null;
let regionCache: Record<string, Region> | null = null;

export function templatePath(): string {
  for (const p of TEMPLATE_PATHS) {
    try {
      readFileSync(p, 'utf8');
      return p;
    } catch { /* try the next */ }
  }
  throw new Error(
    'canonical_face_model.obj not found. Looked in:\n  ' + TEMPLATE_PATHS.join('\n  '),
  );
}

export function loadTemplateMesh(): FaceMesh {
  if (!meshCache) meshCache = parseFaceObj(readFileSync(templatePath(), 'utf8'));
  return meshCache;
}

export function loadBasis(): ShapeBasis {
  if (!basisCache) basisCache = buildAnthropometricBasis(loadTemplateMesh());
  return basisCache;
}

export function loadRegions(): Record<string, Region> {
  if (!regionCache) regionCache = standardRegions(loadTemplateMesh());
  return regionCache;
}
