/**
 * How a reconstruction is graded.
 *
 * Three rules, all of them reactions to something v1's harness could not see:
 *
 *  1. **Per region, not per mesh.** A millimetre on the forehead and a
 *     millimetre on the nasal sidewall are not the same millimetre. A
 *     whole-mesh RMS is dominated by the cheeks and the jaw — the largest,
 *     easiest, most irrelevant areas — and can improve while the nose gets
 *     worse.
 *
 *  2. **Distributions, not means.** Every number is reported as median / p90 /
 *     worst across the population. A try-on that is excellent on average and
 *     unusable for one wearer in twenty is unusable, and a mean hides exactly
 *     that.
 *
 *  3. **Rigid alignment without scale.** The bundle's gauge is free — it can
 *     place the model anywhere as long as the poses compensate — so a rigid
 *     alignment is required before comparing, or the error reported is mostly
 *     gauge. But scale is *not* removed, because scale is precisely what the
 *     ruler is responsible for and hiding it would make the iris bug in
 *     `enroll/scale.ts` invisible to this harness. Removing scale here is the
 *     single easiest way to make these tests unable to fail.
 */

import { rigidAlign } from '../enroll/detector-bias.js';
import { measure, type FaceMesh, type Region } from '../core/mesh.js';
import { percentile } from '../core/linalg.js';
import type { FaceModel } from '../core/facemodel.js';
import type { SyntheticSubject } from './synthetic.js';

export interface RegionError {
  region: string;
  rmsMm: number;
  medianMm: number;
  p90Mm: number;
  maxMm: number;
  vertices: number;
}

export interface ShapeComparison {
  perRegion: Record<string, RegionError>;
  wholeMeshRmsMm: number;
  /** Scale error as a percentage: positive means the model is too large. */
  scaleErrorPct: number;
  /** The fit-relevant scalars, signed, in mm (or radians for the angle). */
  measurementError: {
    templeWidth: number;
    noseWidth: number;
    nasalRootDepth: number;
    bridgeStandoff: number;
    noseProtrusion: number;
    bridgeHeight: number;
    sidewallAngleRad: number;
  };
  /** True PD in mm and the model's, if it produced one. */
  pdErrorMm: number | null;
}

export function compareToTruth(
  model: FaceModel, subject: SyntheticSubject, regions: Record<string, Region>,
  mesh: FaceMesh,
): ShapeComparison {
  const V = mesh.vertexCount;
  const aligned = rigidAlign(model.positions, subject.positions, V);

  const errors = new Float64Array(V);
  for (let i = 0; i < V; i++) {
    errors[i] = Math.hypot(
      aligned[i * 3] - subject.positions[i * 3],
      aligned[i * 3 + 1] - subject.positions[i * 3 + 1],
      aligned[i * 3 + 2] - subject.positions[i * 3 + 2],
    );
  }

  const perRegion: Record<string, RegionError> = {};
  for (const [name, region] of Object.entries(regions)) {
    const vals: number[] = [];
    for (const i of region.members) if (region.weight[i] >= 0.5) vals.push(errors[i]);
    perRegion[name] = summarise(name, vals);
  }
  perRegion.whole = summarise('whole', Array.from(errors));

  // Scale: the ratio of a long, well-conditioned span. Temple width rather than
  // a bounding box, because a bounding box is set by whichever single vertex
  // drifted furthest.
  const truth = measure(subject.positions);
  const got = measure(model.positions);
  const scaleErrorPct = ((got.templeWidth - truth.templeWidth) / truth.templeWidth) * 100;

  // True PD: the synthetic iris centres sit on the eye-corner midpoints.
  const mid = (a: number, b: number, c: number) =>
    (subject.positions[a * 3 + c] + subject.positions[b * 3 + c]) / 2;
  const truePd = Math.hypot(
    mid(133, 33, 0) - mid(362, 263, 0),
    mid(133, 33, 1) - mid(362, 263, 1),
    mid(133, 33, 2) - mid(362, 263, 2),
  );

  return {
    perRegion,
    wholeMeshRmsMm: perRegion.whole.rmsMm,
    scaleErrorPct,
    measurementError: {
      templeWidth: got.templeWidth - truth.templeWidth,
      noseWidth: got.noseWidth - truth.noseWidth,
      nasalRootDepth: got.nasalRootDepth - truth.nasalRootDepth,
      bridgeStandoff: got.bridgeStandoff - truth.bridgeStandoff,
      noseProtrusion: got.noseProtrusion - truth.noseProtrusion,
      bridgeHeight: got.bridgeHeight - truth.bridgeHeight,
      sidewallAngleRad: got.sidewallAngle - truth.sidewallAngle,
    },
    pdErrorMm: model.pdMm === null ? null : model.pdMm - truePd,
  };
}

function summarise(region: string, vals: number[]): RegionError {
  if (vals.length === 0) {
    return { region, rmsMm: NaN, medianMm: NaN, p90Mm: NaN, maxMm: NaN, vertices: 0 };
  }
  let sum = 0;
  for (const v of vals) sum += v * v;
  return {
    region,
    rmsMm: Math.sqrt(sum / vals.length),
    medianMm: percentile(vals, 0.5),
    p90Mm: percentile(vals, 0.9),
    maxMm: Math.max(...vals),
    vertices: vals.length,
  };
}

// -------------------------------------------------------------- aggregation

export interface Distribution {
  median: number;
  p90: number;
  worst: number;
  mean: number;
  /** How many values were finite. */
  n: number;
  /** How many were not — the reason the four above are NaN, when they are. */
  nonFinite: number;
}

/**
 * Summary statistics over cells, where a cell that failed poisons the summary.
 *
 * **This used to filter the failures out and summarise the survivors**, which is
 * the exact policy `acrossSeeds` rejects thirty lines below in its own words: *a
 * figure that failed at one seed in five is a finding, not an outlier to
 * discard*. The same argument applies a level down, and more sharply, because
 * this is the function every published table goes through: a regression that
 * makes some cells diverge removes them from the median, the p90 AND the worst,
 * so every column improves at once and the table reports the run as better than
 * the one before it. The failure is invisible in the only place anyone looks.
 *
 * So a single non-finite value takes the whole summary to NaN, and `nonFinite`
 * says how many there were. NaN in a printed cell is loud and nobody mistakes it
 * for a measurement; a quietly narrowed sample is not, and they do.
 *
 * **Nothing in the tree relies on the old behaviour**, which is why this could
 * change without moving a committed number: instrumented across all four
 * reports at their committed configurations, 781 calls to this function filtered
 * exactly zero cells. The filter was not doing work, it was waiting to hide some.
 *
 * `n` still counts the finite values, so `report-track`'s "(N frames)" and
 * `report-occlusion`'s "over N flips" keep their meaning. A caller that
 * genuinely wants a summary over survivors must now say so at its own call site,
 * where the decision is visible, rather than inheriting it from here.
 */
export function distribution(values: number[]): Distribution {
  const clean = values.filter((v) => Number.isFinite(v));
  const nonFinite = values.length - clean.length;
  if (clean.length === 0 || nonFinite > 0) {
    return { median: NaN, p90: NaN, worst: NaN, mean: NaN, n: clean.length, nonFinite };
  }
  return {
    median: percentile(clean, 0.5),
    p90: percentile(clean, 0.9),
    worst: Math.max(...clean.map(Math.abs)),
    mean: clean.reduce((a, b) => a + b, 0) / clean.length,
    n: clean.length,
    nonFinite: 0,
  };
}

export const fmt = (d: Distribution, dp = 2): string =>
  `${d.median.toFixed(dp)} / ${d.p90.toFixed(dp)} / ${d.worst.toFixed(dp)}`;

// -------------------------------------------------------- seed replication

/**
 * A figure measured once per campaign seed.
 *
 * Everything a replication claim needs and nothing it does not: the seeds (so
 * the sweep can be re-run), the per-seed values (so the spread can be shown,
 * not just summarised), and the median, which is the campaign's standard
 * estimator — a median of independent realisations is robust to one bad draw
 * in a way a mean is not, and "one bad draw" is precisely what a single-run
 * figure cannot distinguish itself from.
 */
export interface SeedSweep {
  seeds: number[];
  /** `figure(seed)`, in the same order as `seeds`. */
  values: number[];
  /** Median of `values` — the number a campaign publishes. */
  median: number;
  min: number;
  max: number;
}

/** The campaign's standard seed list: 1..n. Distinct small integers — the RNG
 *  fold decorrelates them — and never the degenerate 0xffffffff (see
 *  `deriveSeed` in random.ts) or the unseeded default. */
export function campaignSeeds(n: number): number[] {
  if (!Number.isInteger(n) || n < 1) {
    throw new Error(`campaignSeeds wants a positive integer count, got ${n}`);
  }
  return Array.from({ length: n }, (_, i) => i + 1);
}

/**
 * Run a figure at N independent seeds and report the per-seed values.
 *
 * **Nothing calls this, and the reason is its SHAPE rather than neglect.** It
 * takes `figure(seed) => number`, so it runs the whole realisation once per
 * FIGURE. Every campaign in this tree runs it once per SEED and then reads a
 * dozen figures out of each run — `report-occlusion` builds a `SeedRun[]` and
 * pulls loss rates, crawl, boundary error and depth error from the same five
 * runs. Using this would re-run a 74-second campaign once per column.
 *
 * So the signature that would actually fit is
 * `acrossSeeds(seeds, run: (seed) => T, figures: Record<string, (t: T) => number>)`
 * — one realisation per seed, many extractors over it — and that is the change
 * to make the day somebody wants it, not a caller forced onto this one.
 *
 * What is worth keeping either way is the VALIDATION below: duplicate seeds
 * silently narrow a spread while claiming N replicates, and 0xffffffff aliases
 * the unseeded historical run through the fork mix. Both are refused loudly.
 * Today that validation is exercised only by this function's own unit tests, so
 * "the campaign uses `acrossSeeds`" is a statement about intent and not about
 * code.
 *
 * `seeds` is either a count (expanded via `campaignSeeds`) or an explicit
 * list. The list is validated rather than trusted: duplicate seeds would
 * silently narrow the spread while claiming N replicates, and the seed
 * 0xffffffff aliases the unseeded historical run through the fork mix — both
 * are refused loudly because a sweep containing either is not measuring what
 * its caller thinks it is.
 *
 * A non-finite value from any seed poisons the summary statistics to NaN
 * instead of being filtered out: a figure that failed at one seed in five is a
 * finding, not an outlier to discard.
 */
export function acrossSeeds(
  seeds: number | readonly number[],
  figure: (seed: number) => number,
): SeedSweep {
  const list = typeof seeds === 'number' ? campaignSeeds(seeds) : [...seeds];
  if (list.length === 0) throw new Error('acrossSeeds wants at least one seed');
  const seen = new Set<number>();
  for (const s of list) {
    if (!Number.isInteger(s) || s < 0 || s > 0xfffffffe) {
      throw new Error(`seed ${s} is not an integer in [0, 0xfffffffe]`);
    }
    if (seen.has(s)) {
      throw new Error(`seed ${s} appears twice — ${list.length} runs but fewer realisations`);
    }
    seen.add(s);
  }

  const values = list.map((s) => figure(s));
  const poisoned = values.some((v) => !Number.isFinite(v));
  return {
    seeds: list,
    values,
    median: poisoned ? NaN : percentile(values, 0.5),
    min: poisoned ? NaN : Math.min(...values),
    max: poisoned ? NaN : Math.max(...values),
  };
}

/** A fixed-width table, because a report nobody can read is a report nobody reads. */
export function table(headers: string[], rows: (string | number)[][]): string {
  const all = [headers, ...rows.map((r) => r.map(String))];
  const widths = headers.map((_, c) => Math.max(...all.map((r) => String(r[c] ?? '').length)));
  const line = (cells: string[]) =>
    cells.map((c, i) => (i === 0 ? c.padEnd(widths[i]) : c.padStart(widths[i]))).join('  ');
  return [
    line(headers),
    widths.map((w) => '-'.repeat(w)).join('  '),
    ...rows.map((r) => line(r.map(String))),
  ].join('\n');
}
