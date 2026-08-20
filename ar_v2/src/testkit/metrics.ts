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
  n: number;
}

export function distribution(values: number[]): Distribution {
  const clean = values.filter((v) => Number.isFinite(v));
  if (clean.length === 0) return { median: NaN, p90: NaN, worst: NaN, mean: NaN, n: 0 };
  return {
    median: percentile(clean, 0.5),
    p90: percentile(clean, 0.9),
    worst: Math.max(...clean.map(Math.abs)),
    mean: clean.reduce((a, b) => a + b, 0) / clean.length,
    n: clean.length,
  };
}

export const fmt = (d: Distribution, dp = 2): string =>
  `${d.median.toFixed(dp)} / ${d.p90.toFixed(dp)} / ${d.worst.toFixed(dp)}`;

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
