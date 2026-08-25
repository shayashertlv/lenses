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
   * The loss's threshold, in sigmas — where it stops behaving like least
   * squares. Exposed because a caller that varies the loss SHAPE per residual
   * (see `barron`) must keep the SCALE fixed, or the one constant every
   * residual type in this tree shares would quietly mean something different
   * per landmark. `Infinity` for a loss that never turns over.
   */
  readonly scale: number;
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
  scale: Infinity,
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
    scale: delta,
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
 * The case for it is an outlier that is not merely large but *categorically
 * wrong*: a silhouette sample that landed on the ear instead of the nose, or a
 * landmark on a second face. Huber still lets those pull; Cauchy does not. The
 * price is that Cauchy is non-convex and can lock onto a bad basin, so it could
 * only ever run after the first iterations have found the right one.
 *
 * Not yet wired: `enroll/bundle.ts` ramps `huber(4.0)` down to `huber(2.0)`
 * across its outer rounds and never changes loss family, so nothing in the tree
 * constructs a Cauchy. Kept because that ramp is exactly the hook a switch would
 * hang off — the late rounds are the only place it would be safe — and because
 * the non-convexity argument is worth having written down where the loss is,
 * not rediscovered by whoever reaches for it first.
 */
export function cauchy(scale: number): RobustLoss {
  const c2 = scale * scale;
  return {
    name: `cauchy(${scale})`,
    scale,
    eval: (s) => {
      const a = 1 + s / c2;
      return [c2 * Math.log(a), 1 / a];
    },
  };
}

/**
 * Barron's general robust loss (CVPR 2019), as one continuous family.
 *
 * A single shape parameter `alpha` sweeps the whole catalogue this file would
 * otherwise need separate functions for: 2 is L2, 1 is a smooth Huber
 * (Charbonnier), 0 is Cauchy, -2 is Geman-McClure. The reason that matters
 * here is not economy — it is that alpha can be a CONTINUOUS FUNCTION OF
 * SOMETHING ELSE. The tracker schedules it on per-landmark visibility, so a
 * landmark the camera can see is fitted with a Huber-like kernel while one
 * the camera can barely see is fitted with a redescending one, and the
 * transition between those two regimes is smooth rather than a switch.
 *
 * ## Why redescending is the point
 *
 * Huber's influence SATURATES: past its threshold every residual pulls with
 * the same constant force, no matter how wrong it is. That is the correct
 * posture for a noisy measurement and exactly the wrong one for a
 * hallucinated landmark, which is not noisy but WRONG, and which will keep
 * pulling with full force for as long as it is on screen. A redescending
 * loss lets influence fall back toward zero. Measured on this
 * parameterisation at the shipped 2.5-sigma scale, influence (drho * r) at
 * 100 sigma:
 *
 *     huber      2.5000      (constant, forever)
 *     alpha  1   2.4992      (saturating, Huber-like)
 *     alpha  0   0.1248
 *     alpha -2   0.0006      (gone)
 *
 * ## The parameterisation, in this file's convention
 *
 * `eval` takes the SQUARED whitened residual `s` and this family is written
 * so that `rho(s) -> s` and `drho -> 1` as `s -> 0` for EVERY alpha —
 * matching `huber`'s quadratic region exactly. That is what keeps one
 * threshold meaning one thing when the shape varies per residual:
 *
 *     rho(s)  = 2c^2 (|a-2|/a) * expm1( (a/2) * ln( s/(c^2|a-2|) + 1 ) )
 *     drho(s) = ( s/(c^2|a-2|) + 1 ) ^ (a/2 - 1)
 *
 * `expm1` rather than `pow(...) - 1` is load-bearing, not tidiness. The
 * closed form divides by `alpha`, so a schedule sweeping from 1 to -2 passes
 * THROUGH zero, where `pow(x, a/2) - 1` is a difference of two nearly-equal
 * numbers and loses every significant digit. With `expm1` the general branch
 * stays accurate to the last decimal down to `alpha` of 1e-12 and joins the
 * analytic Cauchy limit smoothly from both sides; only alpha EXACTLY zero
 * needs its own branch, and it is there for the division, not for accuracy.
 * `drho` has no singularity at zero at all.
 *
 * The remaining singularity, at alpha = 2, is a `|a-2|` in a denominator.
 * The tracker's schedule lives in [-2, 1], so it never approaches it; the
 * branch exists so a caller asking for plain L2 gets it rather than a NaN.
 */
export function barron(alpha: number, scale: number): RobustLoss {
  const c2 = scale * scale;
  return {
    name: `barron(${alpha}, ${scale})`,
    scale,
    eval: (s) => [barronRho(s, alpha, c2), barronDrho(s, alpha, c2)],
  };
}

/**
 * The family's IRLS weight and cost as free functions, taking the SQUARED
 * scale directly.
 *
 * These exist beside `barron` rather than inside it because the tracker
 * schedules alpha PER CORRESPONDENCE: the solver's inner loop would otherwise
 * have to build a closure per landmark per iteration — some 300 landmarks by
 * 8 iterations by 30 frames a second — to evaluate two lines of arithmetic.
 * `barron` itself is written in terms of these, so there is one
 * implementation of the family and the hot path cannot drift from the tested
 * one.
 */
export function barronDrho(s: number, alpha: number, c2: number): number {
  if (Math.abs(alpha - 2) < 1e-9) return 1;
  const b = Math.abs(alpha - 2);
  return (s / (c2 * b) + 1) ** (alpha / 2 - 1);
}

export function barronRho(s: number, alpha: number, c2: number): number {
  if (Math.abs(alpha - 2) < 1e-9) return s;
  const b = Math.abs(alpha - 2);
  // alpha exactly zero is the only value the general branch cannot take —
  // it divides by alpha. Everything either side of it is handled by expm1;
  // see the header for why that is load-bearing rather than tidy.
  if (alpha === 0) {
    const d = 2 * c2;
    return d * Math.log(s / d + 1);
  }
  return ((2 * c2 * b) / alpha) * Math.expm1((alpha / 2) * Math.log(s / (c2 * b) + 1));
}

/**
 * A scale estimate from the residuals themselves: 1.4826 * MAD.
 *
 * The constant makes MAD a consistent estimator of the standard deviation for a
 * Gaussian, and that requires **signed** residuals — the deviation below is
 * taken about the sample's own median, which sits near zero only when the
 * residuals still carry their sign. The parameter used to be called
 * `absResiduals`, and a caller who honoured that name would have got 0.59 sigma
 * back and no complaint from anything.
 *
 * Do NOT "simplify" this to `1.4826 * median(|r|)`. That identity holds for a
 * scalar Gaussian, and the only absolute-residual arrays this tree produces are
 * 2-D `Math.hypot` norms — Rayleigh distributed, not half-normal — on which the
 * shortcut overestimates sigma by 1.75x.
 *
 * Not yet wired: `enroll/bundle.ts` ramps a fixed `huberStart 4.0 -> huberEnd
 * 2.0` across its outer rounds, so no loss in the tree is re-tuned from the
 * data. Kept because those two constants are precisely what a data-driven scale
 * would replace, and the argument for replacing them — a threshold that is a
 * property of this capture rather than of the machine it was tuned on — is the
 * class of number the v1 audit found 80 of. If all you need is the deviation
 * itself, `core/linalg.ts` already exports a tested `mad()`.
 */
export function robustScale(residuals: number[], floor = 1e-6): number {
  if (residuals.length === 0) return floor;
  const sorted = [...residuals].sort((a, b) => a - b);
  const med = sorted[sorted.length >> 1];
  const dev = sorted.map((r) => Math.abs(r - med)).sort((a, b) => a - b);
  return Math.max(1.4826 * dev[dev.length >> 1], floor);
}
