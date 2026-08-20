/**
 * The free-form nose patch: a per-vertex scalar displacement along the surface
 * normal, over the nose region only.
 *
 * This is the part of the model that actually recovers *this person's* nose,
 * and it is deliberately not a shape basis. A basis — anthropometric or PCA —
 * can only produce noses that are combinations of noses it was built from, and
 * the nose is exactly where that fails: it is small, high-curvature, and carries
 * the population's most between-group variation, so a low-rank basis smooths it
 * toward the mean. v1's entire nose problem is the limiting case of that (a
 * rank-zero basis: the average nose, always).
 *
 * ## Why scalar-along-normal rather than free 3D
 *
 * Free 3D displacement has three times the parameters and a *gauge freedom*:
 * sliding a vertex along the surface changes the parameters and not the surface,
 * so the normal equations carry a null space the damping has to hide. Every
 * millimetre of that null space is a millimetre the solver spends not measuring
 * the nose. Displacement along the normal has no tangential direction to slide
 * in, so the parameterisation is minimal and the Hessian is honest.
 *
 * What it gives up is the ability to move a feature *sideways* — a nose bent off
 * the midline. That is not a loss, because the basis has an explicit
 * `noseDeviation` mode for exactly that, and the division of labour is the right
 * one: the basis places the nose, the field shapes its surface.
 *
 * ## The normals are frozen within an inner solve
 *
 * The normal a displacement rides on is computed from the basis shape and held
 * fixed while the linear system is solved, then recomputed. Strictly the normal
 * depends on the displacement, so the true jacobian has a second term. In
 * practice the displacements are millimetres on a surface whose curvature radius
 * is centimetres, the neglected term is under a percent, and including it makes
 * the system dense (a vertex's normal depends on its neighbours' displacements).
 * The outer loop recovers the exactness; `tests/displacement.test.ts` measures
 * the difference between the frozen-normal solve and a finite-difference exact
 * one, so this is a bounded approximation rather than an unexamined one.
 */

import { computeVertexNormals, type FaceMesh, type Region } from '../mesh.js';

export interface DisplacementField {
  /** Mesh vertex index for each parameter. */
  readonly vertices: Uint32Array;
  /** Parameter index for each mesh vertex, or -1. */
  readonly slotOf: Int32Array;
  /** Region weight per parameter (the feather at the rim). */
  readonly weight: Float64Array;
  /** Unit normal per parameter, 3 per slot. Refreshed by `refreshNormals`. */
  readonly normals: Float64Array;
  /** The scalar displacement per parameter, mm. This is the state. */
  readonly values: Float64Array;
  /** Neighbour lists restricted to the field, for the Laplacian prior. */
  readonly neighbours: Int32Array;
  readonly neighbourStart: Uint32Array;
  readonly dim: number;
}

export function createDisplacementField(mesh: FaceMesh, region: Region): DisplacementField {
  const vertices = region.members;
  const dim = vertices.length;

  const slotOf = new Int32Array(mesh.vertexCount).fill(-1);
  for (let s = 0; s < dim; s++) slotOf[vertices[s]] = s;

  const weight = new Float64Array(dim);
  for (let s = 0; s < dim; s++) weight[s] = region.weight[vertices[s]];

  // Neighbour lists inside the field. A vertex on the rim has fewer, which is
  // correct — the Laplacian there is one-sided and pulls the rim toward the
  // interior, which is the same thing the feather weight is doing.
  const lists: number[] = [];
  const start = new Uint32Array(dim + 1);
  for (let s = 0; s < dim; s++) {
    start[s] = lists.length;
    const v = vertices[s];
    for (let a = mesh.adjacencyStart[v]; a < mesh.adjacencyStart[v + 1]; a++) {
      const t = slotOf[mesh.adjacency[a]];
      if (t >= 0) lists.push(t);
    }
  }
  start[dim] = lists.length;

  return {
    vertices,
    slotOf,
    weight,
    normals: new Float64Array(dim * 3),
    values: new Float64Array(dim),
    neighbours: Int32Array.from(lists),
    neighbourStart: start,
    dim,
  };
}

/** Recomputes the normals the field rides on, from a given shape. */
export function refreshNormals(
  field: DisplacementField, mesh: FaceMesh, positions: Float64Array,
): void {
  const all = computeVertexNormals(positions, mesh.indices, mesh.vertexCount);
  for (let s = 0; s < field.dim; s++) {
    const v = field.vertices[s];
    field.normals[s * 3] = all[v * 3];
    field.normals[s * 3 + 1] = all[v * 3 + 1];
    field.normals[s * 3 + 2] = all[v * 3 + 2];
  }
}

/** Adds the field's displacement into `positions`, in place. */
export function applyDisplacement(field: DisplacementField, positions: Float64Array): void {
  for (let s = 0; s < field.dim; s++) {
    const d = field.values[s] * field.weight[s];
    if (d === 0) continue;
    const v = field.vertices[s] * 3;
    positions[v] += field.normals[s * 3] * d;
    positions[v + 1] += field.normals[s * 3 + 1] * d;
    positions[v + 2] += field.normals[s * 3 + 2] * d;
  }
}

/**
 * d(vertex position) / d(slot value) — the normal, scaled by the region weight.
 * Written into `out[off..off+2]`.
 */
export function displacementJacobian(
  field: DisplacementField, slot: number, out: Float64Array, off = 0,
): void {
  const w = field.weight[slot];
  out[off] = field.normals[slot * 3] * w;
  out[off + 1] = field.normals[slot * 3 + 1] * w;
  out[off + 2] = field.normals[slot * 3 + 2] * w;
}

// ------------------------------------------------------------------- priors

export interface DisplacementPriors {
  /**
   * Weight on the uniform Laplacian, per mm of curvature.
   *
   * This is what keeps the field a *surface* and not a spray of independent
   * points. Without it, a vertex seen in only two frames gets a displacement
   * that explains its own two observations and disagrees with its neighbours by
   * millimetres — which under a nose pad is a spike, and a contact solver
   * landing on a spike produces a seat that is stable, wrong, and impossible to
   * diagnose from the render.
   */
  smoothness: number;
  /**
   * Weight pulling the field toward zero, i.e. toward the basis shape.
   *
   * Small on purpose. Its job is only to keep the field from wandering where
   * there is no data at all (a vertex nothing observed), not to argue with
   * observations. Set too high it silently reproduces v1: an average nose that
   * the data was not allowed to move.
   */
  magnitude: number;
}

export const DISPLACEMENT_PRIORS: DisplacementPriors = {
  // Both derived in `docs/CONSTANTS.md` from the target: a 1 mm bump across one
  // edge (~2.5 mm on this mesh) should cost about as much as 0.35 mm of
  // landmark reprojection error, which is the measured landmark noise floor.
  smoothness: 2.2,
  magnitude: 0.06,
};

/**
 * Accumulates the prior terms into a normal-equation system.
 *
 * `map` gives the global parameter index of each slot (or -1 if the slot is
 * held fixed this pass). Only the lower triangle of `H` is written.
 *
 * ## The full outer product, and the bug that came from not writing it
 *
 * Each Laplacian row is one residual touching `1 + degree` parameters, so its
 * contribution to `H` is the **complete** rank-one outer product `J J^T` over
 * those indices — including every neighbour-against-neighbour pair. An earlier
 * version of this function wrote only the diagonal and the centre-to-neighbour
 * terms, on the reasoning that the rest was "a second-order effect on a prior".
 *
 * That reasoning is wrong, and the way it is wrong is worth keeping: a
 * truncated `J J^T` is **not positive semi-definite**. The field's `ldlt`
 * factorisation therefore failed, `solveField` took its early return, and the
 * displacement field never moved — for every wearer, in every run. The visible
 * symptom was nothing at all: the bundle converged, the reprojection error was
 * normal, the report printed plausible numbers, and the ablation "with field"
 * versus "without field" showed no difference *because there was no
 * difference*. It was found only because the field's own RMS readout was
 * 0.00 mm, which is a number that should be impossible on a real face.
 *
 * Two lessons, both of them v1's lessons arriving in new clothes: a check that
 * cannot fail is not a check (the ablation), and a solver that gives up must say
 * so out loud (hence `factorised` in the return).
 */
export function accumulateDisplacementPriors(
  field: DisplacementField, priors: DisplacementPriors,
  H: Float64Array, g: Float64Array, n: number, map: Int32Array,
): number {
  let cost = 0;

  // Scratch for one row's (index, jacobian) pairs. Degree on this mesh is ~6.
  const idx = new Int32Array(32);
  const jac = new Float64Array(32);

  // Laplacian: for each slot, r = w * (x_s - mean(x_neighbours)).
  for (let s = 0; s < field.dim; s++) {
    const a0 = field.neighbourStart[s], a1 = field.neighbourStart[s + 1];
    const deg = a1 - a0;
    if (deg === 0 || deg + 1 > idx.length) continue;

    const inv = 1 / deg;
    const w = priors.smoothness;

    let r = field.values[s];
    for (let a = a0; a < a1; a++) r -= field.values[field.neighbours[a]] * inv;
    const rw = r * w;
    cost += rw * rw;

    let m = 0;
    if (map[s] >= 0) { idx[m] = map[s]; jac[m] = w; m++; }
    for (let a = a0; a < a1; a++) {
      const gt = map[field.neighbours[a]];
      if (gt < 0) continue;
      idx[m] = gt; jac[m] = -w * inv; m++;
    }

    for (let p = 0; p < m; p++) {
      g[idx[p]] += jac[p] * rw;
      for (let q = 0; q < m; q++) {
        // Lower triangle only, and `+=` because two entries of this row can map
        // to the same parameter in principle.
        const i = idx[p], j = idx[q];
        if (i >= j) H[i * n + j] += jac[p] * jac[q];
      }
    }
  }

  // Magnitude.
  for (let s = 0; s < field.dim; s++) {
    const gs = map[s];
    if (gs < 0) continue;
    const w = priors.magnitude;
    const r = field.values[s] * w;
    cost += r * r;
    g[gs] += w * r;
    H[gs * n + gs] += w * w;
  }

  return cost;
}

/** RMS and peak of the field, mm — the readout that says how much of the nose
 *  came from data rather than from the basis. */
export function displacementStats(field: DisplacementField): {
  rmsMm: number; maxMm: number; activeSlots: number;
} {
  let sum = 0, max = 0, active = 0;
  for (let s = 0; s < field.dim; s++) {
    const v = field.values[s] * field.weight[s];
    sum += v * v;
    if (Math.abs(v) > max) max = Math.abs(v);
    if (Math.abs(v) > 0.05) active++;
  }
  return {
    rmsMm: field.dim ? Math.sqrt(sum / field.dim) : 0,
    maxMm: max,
    activeSlots: active,
  };
}
