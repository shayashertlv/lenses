/**
 * Levenberg-Marquardt, with Nielsen damping.
 *
 * Small and dense on purpose. The large, structured problem — the enrollment
 * bundle — does not come here directly: it eliminates its per-frame blocks by
 * Schur complement first (`enroll/schur.ts`) and hands this file the reduced
 * system, which is tens of parameters rather than a thousand. Everything else
 * that solves in this tree is genuinely small: PnP is 6, the contact seat is 6,
 * the scale ladder is 1.
 *
 * Nielsen's damping update rather than the textbook multiply-by-ten: the
 * textbook version oscillates on problems where one parameter is far worse
 * conditioned than the rest, which is exactly the shape of a bundle carrying
 * both a focal length in pixels and a shape coefficient in standard deviations.
 * Nielsen's rule adapts to the *quality* of the step (the gain ratio) rather
 * than only to its success, and on the enrollment bundle it is the difference
 * between converging in 9 iterations and stalling at 40.
 *
 * Reference: Madsen, Nielsen & Tingleff, "Methods for Non-Linear Least Squares
 * Problems", 2nd ed., section 3.2.
 */

import { ldlt, ldltSolve } from './linalg.js';

export interface LMProblem {
  /** Number of free parameters. */
  readonly n: number;

  /**
   * Accumulate the Gauss-Newton system at the current state.
   *
   * `H` (n x n, row-major) and `g` (n) arrive zeroed. Fill `H = J^T W J` and
   * `g = J^T W r`. Return the current robustified cost. The step solved is
   * `(H + lambda diag(H)) dx = -g`.
   */
  linearize(H: Float64Array, g: Float64Array): number;

  /** Robustified cost at `state (+) delta`, without committing. */
  costAt(delta: Float64Array): number;

  /** Commit `state <- state (+) delta`. */
  apply(delta: Float64Array): void;

  /** Optional: called once per accepted iteration, after `apply`. */
  onIteration?(iter: number, cost: number): void;
}

export interface LMOptions {
  maxIterations: number;
  /** Stop when the largest gradient component falls below this. */
  gradientTolerance: number;
  /** Stop when |dx| < stepTolerance * (|x| + stepTolerance). Scale-free. */
  stepTolerance: number;
  /** Stop when the relative cost decrease falls below this. */
  costTolerance: number;
  initialLambda: number;
  maxLambda: number;
  /** Emitted per iteration when set — the harness uses it to plot convergence. */
  trace?: (iter: number, cost: number, lambda: number, gradMax: number) => void;
}

export const LM_DEFAULTS: LMOptions = {
  maxIterations: 40,
  gradientTolerance: 1e-9,
  stepTolerance: 1e-10,
  costTolerance: 1e-10,
  initialLambda: 1e-4,
  maxLambda: 1e12,
};

export interface LMReport {
  converged: boolean;
  reason: 'gradient' | 'step' | 'cost' | 'max-iterations' | 'lambda' | 'singular';
  iterations: number;
  initialCost: number;
  finalCost: number;
  gradMax: number;
  lambda: number;
  /** Wall-clock, ms. Reported rather than hidden: a scan has a latency budget. */
  ms: number;
}

export function levenbergMarquardt(
  problem: LMProblem, options: Partial<LMOptions> = {},
): LMReport {
  const opt = { ...LM_DEFAULTS, ...options };
  const n = problem.n;
  const started = now();

  const H = new Float64Array(n * n);
  const g = new Float64Array(n);
  const Haug = new Float64Array(n * n);
  const dx = new Float64Array(n);
  const diag = new Float64Array(n);

  let lambda = opt.initialLambda;
  let nu = 2;
  let cost = problem.linearize(H, g);
  const initialCost = cost;
  let reason: LMReport['reason'] = 'max-iterations';
  let iter = 0;
  let gradMax = 0;

  for (const x of g) gradMax = Math.max(gradMax, Math.abs(x));
  if (gradMax < opt.gradientTolerance) {
    return {
      converged: true, reason: 'gradient', iterations: 0,
      initialCost, finalCost: cost, gradMax, lambda, ms: now() - started,
    };
  }

  for (iter = 0; iter < opt.maxIterations; iter++) {
    for (let i = 0; i < n; i++) {
      // Marquardt's scaling: damp proportionally to each parameter's own
      // curvature, not uniformly. Uniform damping on a problem mixing pixels and
      // shape sigmas effectively freezes whichever parameter has the smaller
      // units, and the solve then reports convergence having never moved it.
      diag[i] = Math.max(H[i * n + i], 1e-12);
    }

    let stepAccepted = false;
    while (!stepAccepted) {
      Haug.set(H);
      for (let i = 0; i < n; i++) Haug[i * n + i] += lambda * diag[i];
      for (let i = 0; i < n; i++) dx[i] = -g[i];

      if (!ldlt(Haug, n)) {
        lambda *= nu; nu *= 2;
        if (lambda > opt.maxLambda) { reason = 'singular'; break; }
        continue;
      }
      ldltSolve(Haug, n, dx);

      let dxNorm = 0;
      for (const v of dx) dxNorm += v * v;
      dxNorm = Math.sqrt(dxNorm);
      if (dxNorm < opt.stepTolerance) { reason = 'step'; stepAccepted = false; break; }

      const newCost = problem.costAt(dx);

      // Predicted decrease for the gain ratio: 0.5 * dx^T (lambda*diag*dx - g)
      let predicted = 0;
      for (let i = 0; i < n; i++) predicted += dx[i] * (lambda * diag[i] * dx[i] - g[i]);
      predicted *= 0.5;

      const actual = cost - newCost;
      const rho = predicted > 0 ? actual / predicted : -1;

      if (rho > 0 && Number.isFinite(newCost)) {
        problem.apply(dx);
        const relative = cost > 0 ? actual / cost : 0;
        cost = newCost;
        stepAccepted = true;

        // Nielsen: lambda <- lambda * max(1/3, 1 - (2 rho - 1)^3), nu <- 2
        const factor = Math.max(1 / 3, 1 - Math.pow(2 * rho - 1, 3));
        lambda *= factor;
        nu = 2;

        problem.onIteration?.(iter, cost);
        if (relative < opt.costTolerance) reason = 'cost';
      } else {
        lambda *= nu;
        nu *= 2;
        if (lambda > opt.maxLambda) { reason = 'lambda'; break; }
      }
    }

    if (!stepAccepted) break;

    H.fill(0); g.fill(0);
    cost = problem.linearize(H, g);
    gradMax = 0;
    for (const x of g) gradMax = Math.max(gradMax, Math.abs(x));
    opt.trace?.(iter, cost, lambda, gradMax);

    if (gradMax < opt.gradientTolerance) { reason = 'gradient'; break; }
    if (reason === 'cost') break;
  }

  return {
    converged: reason === 'gradient' || reason === 'step' || reason === 'cost',
    reason,
    iterations: iter,
    initialCost,
    finalCost: cost,
    gradMax,
    lambda,
    ms: now() - started,
  };
}

const now = (): number =>
  (typeof performance !== 'undefined' ? performance.now() : Date.now());

// ------------------------------------------------------- accumulation help

/**
 * Adds one residual block's contribution to `H` and `g`.
 *
 * `J` is `rows x cols` row-major, mapping into parameter columns given by
 * `map` (so a residual touching parameters 3..8 and 40..42 passes those
 * indices). `r` is the residual, already whitened by its own sigma. `weight` is
 * the IRLS weight from the robust loss.
 *
 * Only the lower triangle of `H` is written — `ldlt` reads nothing else, and
 * writing both halves of a 1000x1000 accumulation is a doubling of the inner
 * loop for a number nobody reads.
 */
export function accumulate(
  H: Float64Array, g: Float64Array, n: number,
  J: Float64Array, r: Float64Array, rows: number,
  map: Int32Array, weight: number,
): void {
  const cols = map.length;
  for (let a = 0; a < cols; a++) {
    const ia = map[a];
    if (ia < 0) continue;
    let ga = 0;
    for (let k = 0; k < rows; k++) ga += J[k * cols + a] * r[k];
    g[ia] += weight * ga;

    for (let b = 0; b <= a; b++) {
      const ib = map[b];
      if (ib < 0) continue;
      let h = 0;
      for (let k = 0; k < rows; k++) h += J[k * cols + a] * J[k * cols + b];
      h *= weight;
      // Keep the lower triangle in (row >= col) order regardless of map order.
      if (ia >= ib) H[ia * n + ib] += h;
      else H[ib * n + ia] += h;
    }
  }
}

/** Mirrors the lower triangle into the upper. For inspection and tests only. */
export function symmetrize(H: Float64Array, n: number): void {
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < i; j++) H[j * n + i] = H[i * n + j];
  }
}
