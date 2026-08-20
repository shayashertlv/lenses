/**
 * Robust losses, as IRLS weights.
 *
 * Every residual in this tree is robustified, and that is a change of posture
 * from v1 rather than a refinement of it. v1 defended itself with *gates* and
 * *clamps*: a sample was refused entirely, or a value was rammed back inside a
 * rail. Both are the same instrument — a hard threshold — and both share its
 * failure: they are correct about the outlier and wrong about everything within
 * a hair of the threshold, and the boundary moves with the wearer.
 *
 * A robust loss says the same thing continuously. A residual at one sigma is
 * worth its full weight; at ten sigma it is worth a tenth of its leverage,
 * without ever being excluded and without a threshold anybody has to defend. In
 * a solve this matters more than in a filter, because an outlier does not merely
 * bias the estimate — it can capture the linearisation and steer the whole
 * bundle into a basin it cannot leave.
 *
 * The one place a hard refusal survives is physical impossibility (a negative
 * depth, an iris three pixels across). Those are not outliers; they are
 * measurements that do not exist.
 */

export interface RobustLoss {
  readonly name: string;
  /**
   * Given the squared, whitened residual norm `s = r^T W r`, returns
   * `[rho(s), rho'(s)]`. `rho'` is the IRLS weight applied to both the hessian
   * and the gradient contribution; `rho` is the cost actually being minimised.
   */
  eval(s: number): [number, number];
}

/** No robustification. Correct when the residual is a prior, not a measurement. */
export const TRIVIAL: RobustLoss = {
  name: 'trivial',
  eval: (s) => [s, 1],
};

/**
 * Huber. Quadratic inside `delta`, linear outside.
 *
 * `delta` is in units of *whitened* residual, so it is a number of sigmas and
 * means the same thing for a landmark in pixels, a silhouette in pixels and a
 * normal in radians. This is the only reason a single constant can be shared
 * across residual types, and it is why every residual in `enroll/residuals/`
 * divides by its own sigma before it is handed to the solver.
 */
export function huber(delta: number): RobustLoss {
  const d2 = delta * delta;
  return {
    name: `huber(${delta})`,
    eval: (s) => {
      if (s <= d2) return [s, 1];
      const r = Math.sqrt(s);
      return [2 * delta * r - d2, delta / r];
    },
  };
}

/**
 * Cauchy. Bounded influence — an arbitrarily bad residual contributes an
 * arbitrarily small gradient.
 *
 * Used where an outlier is not merely large but *categorically wrong*: a
 * silhouette sample that landed on the ear instead of the nose, or a landmark
 * on a second face. Huber still lets those pull; Cauchy does not. The price is
 * that Cauchy is non-convex and can lock onto a bad basin, so it is never used
 * for the first iterations — see `enroll/bundle.ts`, which starts on Huber and
 * tightens.
 */
export function cauchy(scale: number): RobustLoss {
  const c2 = scale * scale;
  return {
    name: `cauchy(${scale})`,
    eval: (s) => {
      const a = 1 + s / c2;
      return [c2 * Math.log(a), 1 / a];
    },
  };
}

/**
 * A scale estimate from the residuals themselves: 1.4826 * MAD.
 *
 * The constant makes MAD a consistent estimator of the standard deviation for a
 * Gaussian. Used to re-tune the loss between outer iterations, so the threshold
 * is a property of this capture rather than a constant somebody picked on one
 * machine — which is precisely the class of number the v1 audit found 80 of.
 */
export function robustScale(absResiduals: number[], floor = 1e-6): number {
  if (absResiduals.length === 0) return floor;
  const sorted = [...absResiduals].sort((a, b) => a - b);
  const med = sorted[sorted.length >> 1];
  const dev = sorted.map((r) => Math.abs(r - med)).sort((a, b) => a - b);
  return Math.max(1.4826 * dev[dev.length >> 1], floor);
}
