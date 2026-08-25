/**
 * Deterministic randomness.
 *
 * `Math.random()` appears nowhere in this tree, and that is a hard rule rather
 * than a preference. A harness whose fixtures change between runs cannot tell a
 * regression from a reseed, and v1 learned this the expensive way: two of its
 * three suites had to be rebuilt around seeded noise after a "flaky" jitter
 * check turned out to be measuring the seed.
 *
 * mulberry32: 32-bit state, passes gjrand and PractRand to well past any length
 * this harness will draw, and is four lines. The quality bar for a fixture
 * generator is "no structure a solver could accidentally exploit", not
 * cryptographic.
 */

export interface Rng {
  /** Uniform [0, 1). */
  next(): number;
  /** Uniform [lo, hi). */
  range(lo: number, hi: number): number;
  /** Standard normal, Box-Muller. */
  normal(): number;
  /** Normal truncated to +/- `limit` sigma. Resampled, not clamped: clamping
   *  piles mass on the bound and a population generated that way has spikes at
   *  exactly the extremes a bound-checking test is looking at. */
  truncatedNormal(limit: number): number;
  /** A fresh independent stream, for a sub-generator that must not disturb the
   *  parent's sequence. This is what lets a test add a camera geometry without
   *  changing the faces every other test draws. */
  fork(salt: number): Rng;
}

/**
 * The seed a `fork(salt)` stream actually runs on, exposed as a plain function
 * so a caller can derive a sub-seed without holding an `Rng`.
 *
 * This is how a campaign seed is folded into a domain that already has its own
 * base seed: `deriveSeed(base, campaignSeed)` gives a new base that is (a) a
 * deterministic function of both, (b) different from `base` for every salt
 * except the single degenerate value 0xffffffff (`salt + 1` wraps to 0 and the
 * mix returns `base` unchanged — callers that accept arbitrary seeds must
 * reject it, and `acrossSeeds` in metrics.ts does), and (c) exactly the value
 * `createRng(base).fork(salt)` would run on, so the two spellings cannot
 * disagree.
 */
export function deriveSeed(base: number, salt: number): number {
  return (base ^ Math.imul(salt + 1, 0x9e3779b1)) >>> 0;
}

export function createRng(seed: number): Rng {
  let a = seed >>> 0;
  let spare: number | null = null;

  const next = (): number => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  const normal = (): number => {
    if (spare !== null) { const s = spare; spare = null; return s; }
    let u = 0, v = 0, s = 0;
    do {
      u = next() * 2 - 1;
      v = next() * 2 - 1;
      s = u * u + v * v;
    } while (s >= 1 || s === 0);
    const f = Math.sqrt((-2 * Math.log(s)) / s);
    spare = v * f;
    return u * f;
  };

  const rng: Rng = {
    next,
    range: (lo, hi) => lo + next() * (hi - lo),
    normal,
    truncatedNormal: (limit) => {
      for (let i = 0; i < 64; i++) {
        const x = normal();
        if (Math.abs(x) <= limit) return x;
      }
      return 0;
    },
    fork: (salt) => createRng(deriveSeed(seed, salt)),
  };
  return rng;
}
