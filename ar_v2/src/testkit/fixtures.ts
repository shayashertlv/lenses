/**
 * Loading the template mesh outside a browser, and the shared fixtures every
 * test builds on.
 *
 * The template lives in this tree's own `assets/face/`. It used to live in
 * the sibling v1 checkout and be reached across, on the reasoning that 46 KB
 * is not worth duplicating and two copies of a template mesh is two things
 * that can drift — which was true, and cost more than it saved: the harness
 * required a second checkout to be present, and the four-entry fallback ladder
 * that made it work is exactly what kept anyone from noticing. One path now,
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
// One path, and deliberately one. This was a four-entry ladder that ended in
// two `../ar` fallbacks, so the harness kept working when the template was not
// where this tree thought it was — which is the failure mode a fallback is
// supposed to prevent and this one caused: it made the tree's dependence on
// the sibling checkout invisible. `dist/src/testkit` and `src/testkit` are the
// same depth, so one relative path serves the built and the source tree alike.
const TEMPLATE_PATHS = [
  // src/testkit/ and dist/src/testkit/ are NOT the same depth — the build adds
  // a level — so two entries are load-bearing and the pair is not a fallback
  // ladder. The two that WERE a ladder both ended in the sibling checkout, and
  // those are gone.
  resolve(here, '../../assets/face/canonical_face_model.obj'),
  resolve(here, '../../../assets/face/canonical_face_model.obj'),
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
