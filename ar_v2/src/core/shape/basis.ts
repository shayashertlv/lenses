/**
 * The identity shape model, as an interface.
 *
 * ## Why this is an interface and not just FLAME
 *
 * The design called for FLAME 2023 Open (CC-BY-4.0), and that is still the
 * right answer for maximum fidelity — see `flame.ts`, which is a working loader
 * waiting for the asset. But the asset requires a human to accept a licence on
 * the MPI site, and a build that cannot run without it is a build that cannot be
 * tested. So the shape model is a *slot*, and the tree ships with a basis it can
 * construct from the template mesh alone (`anthropometric.ts`).
 *
 * That turned out to be more than a stopgap, and the reasoning is worth stating
 * because it is a genuine disagreement with the plan:
 *
 *  - A 300-component PCA basis learned from head scans spends most of its
 *    capacity on variation eyewear does not care about — skull breadth behind
 *    the ears, jaw and chin, lip shape. For *fit*, roughly a dozen numbers
 *    matter, and eleven of them are about the nose and the eye line.
 *  - PCA bases are notoriously poor at the nose specifically: it is
 *    high-curvature and low-variance in a whole-head basis, so the leading
 *    components smooth it toward the mean. That is the exact failure this
 *    project exists to fix, and swapping one average nose for a slightly better
 *    average nose does not fix it.
 *  - The free-form displacement field (`displacement.ts`) is what actually
 *    recovers this person's nose. The basis only has to get close enough that
 *    the displacement field is solving for millimetres rather than centimetres.
 *
 * So: an interpretable low-dimensional basis for the head, a free-form field for
 * the nose. FLAME drops into the first slot without touching the second.
 */

import type { FaceMesh } from '../mesh.js';

export interface ShapeBasis {
  readonly name: string;
  /** Number of coefficients. */
  readonly dim: number;
  readonly vertexCount: number;
  /** The mean shape, 3 * vertexCount, mm. */
  readonly mean: Float64Array;
  /**
   * Mode `k` as a displacement field, 3 * vertexCount, mm per unit coefficient.
   * Modes are scaled so that a coefficient of 1.0 is one standard deviation of
   * that trait in the adult population, which is what makes the prior sigma
   * below a plain 1 and the bounds a plain +/-3.
   */
  readonly modes: Float64Array[];
  /** Prior standard deviation per coefficient. */
  readonly sigma: Float64Array;
  /** Human-readable name per coefficient, for readouts and for debugging. */
  readonly labels: string[];
}

/** Writes `mean + sum_k coeff[k] * modes[k]` into `out` (3 * vertexCount). */
export function evaluateBasis(
  basis: ShapeBasis, coeffs: ArrayLike<number>, out: Float64Array,
): Float64Array {
  out.set(basis.mean);
  for (let k = 0; k < basis.dim; k++) {
    const c = coeffs[k];
    if (c === 0) continue;
    const m = basis.modes[k];
    for (let i = 0; i < out.length; i++) out[i] += c * m[i];
  }
  return out;
}

/**
 * d(vertex i) / d(coeff k) — which for a linear basis is just the mode, read at
 * that vertex. Present as a function anyway so a non-linear basis (a corrective
 * or a neural decoder) can be dropped in without every residual changing.
 */
export function basisJacobian(
  basis: ShapeBasis, vertexIndex: number, k: number, out: Float64Array, off = 0,
): void {
  const m = basis.modes[k];
  const i3 = vertexIndex * 3;
  out[off] = m[i3];
  out[off + 1] = m[i3 + 1];
  out[off + 2] = m[i3 + 2];
}

/**
 * Orthonormalises a set of generator fields in the plain Euclidean inner
 * product over all vertex coordinates, then rescales each to the magnitude of
 * its own *unique* contribution.
 *
 * Why bother: two generators built by hand — "the face is wider" and "the
 * temples are wider" — overlap heavily, and a near-parallel pair puts a nearly
 * singular block in the middle of the normal equations. LM survives it by
 * damping, which is to say by refusing to move either one, and the solve then
 * reports convergence having explained the width with neither. Gram-Schmidt in
 * a declared priority order fixes the conditioning and costs only that later
 * modes mean "this trait, beyond what the earlier ones already explained" —
 * which is what a coefficient in any PCA basis means too.
 */
export function orthonormalizeModes(generators: Float64Array[]): {
  modes: Float64Array[]; scales: number[];
} {
  const modes: Float64Array[] = [];
  const scales: number[] = [];

  for (const raw of generators) {
    const v = new Float64Array(raw);
    for (const e of modes) {
      let dot = 0;
      for (let i = 0; i < v.length; i++) dot += v[i] * e[i];
      for (let i = 0; i < v.length; i++) v[i] -= dot * e[i];
    }
    let norm = 0;
    for (let i = 0; i < v.length; i++) norm += v[i] * v[i];
    norm = Math.sqrt(norm);
    if (norm < 1e-9) {
      // Fully explained by earlier modes. Keeping it would be a rank deficiency
      // the damping would hide, so it is dropped and the caller is told by the
      // scale of zero.
      scales.push(0);
      modes.push(new Float64Array(v.length));
      continue;
    }
    for (let i = 0; i < v.length; i++) v[i] /= norm;
    modes.push(v);
    scales.push(norm);
  }

  // Re-apply each generator's own magnitude, so coefficient 1.0 stays "one
  // standard deviation of this trait's unique part".
  const out = modes.map((e, k) => {
    const m = new Float64Array(e.length);
    for (let i = 0; i < e.length; i++) m[i] = e[i] * scales[k];
    return m;
  });
  return { modes: out, scales };
}

/** Drops zero-scale (fully dependent) modes. */
export function pruneBasis(basis: ShapeBasis): ShapeBasis {
  const keep: number[] = [];
  for (let k = 0; k < basis.dim; k++) {
    let n = 0;
    for (const v of basis.modes[k]) n += v * v;
    if (n > 1e-12) keep.push(k);
  }
  if (keep.length === basis.dim) return basis;
  return {
    name: basis.name,
    dim: keep.length,
    vertexCount: basis.vertexCount,
    mean: basis.mean,
    modes: keep.map((k) => basis.modes[k]),
    sigma: Float64Array.from(keep, (k) => basis.sigma[k]),
    labels: keep.map((k) => basis.labels[k]),
  };
}

export interface BasisSource {
  build(mesh: FaceMesh): ShapeBasis;
}
