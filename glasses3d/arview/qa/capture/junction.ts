/** Lens-brow junction geometry (pure; port_review/arview.json analysis.capture.junction_crop_method).
 *
 *  Measured once per model on the normalised scene the renderer draws (asset-local metres, transforms baked), from
 *  TRIANGLE slices, never vertex bins (the prototype's junction_geom.py binned vertices and skipped sparse brows
 *  silently) and never by name:
 *  - sides by geometry: 'left' is x < 0 and 'right' x > 0, as g3d_host/spec.py names them; a shield's single lens is
 *    split at x = 0 by the slicing itself (a station only meets the triangles that cross its plane);
 *  - per side, the lens x-range (lens-class vertices on that side of x = 0) is trimmed by 8 % at each end and sampled at
 *    24 stations; each station slices the lens and the non-lens front (triangles whose centroid lies in front of
 *    z = -30 mm, the authored frame-front band) with the plane x = x_i;
 *  - lens top (y_t, z_t) = the highest point of the lens slice; brow bottom y_b = the lowest point of the frame slice
 *    inside the window y >= y_t - 3 mm, |z - z_t| < 6 mm (segments are clipped to the window, so a rim wall that
 *    continues below the window reads as the window floor: `floor` is then true and the overlap is saturated at 3 mm);
 *  - overlap_mm = (y_t - y_b) * 1000: positive = seated (the lens top is inside or behind frame material), negative =
 *    a visible gap; no frame point in the window = 'no frame above' (rimless or nylor tops). The same reading is kept
 *    per frame part (`parts`, `summary.by_part`; part names are labels only): on a browline the lowest point is the
 *    metal eyewire's groove, and the acetate brow's own overlap (the visible cover) is read separately.
 *  The junction box of a side is the AABB of its stations with frame above, padded x ±1.5 mm, y [y_t - 4 mm,
 *  max(y_b, y_t) + 5 mm], z [z_t ∓ 4 mm]; a side with no frame above at any station gets the box of its lens-top
 *  stations instead (`basis: 'lens_top_only'`, reported).
 *  A frame-classed part that rides on the lens surface (a decal: every vertex within 0.3 mm of a lens triangle, and its
 *  area at most 2 % of the lens area; r9's 'Lens inscription' is 0.016 mm off the lens, 15.9 mm², 0.2 %) is not brow:
 *  it is left out of every reading, listed in `lens_decals` and warned. q001's metal eyewire, which holds the lens in its
 *  groove, is up to 0.43 mm off and 9 % of the lens area, so it stays frame. */

export const JUNCTION_STATIONS = 24;
export const JUNCTION_TRIM_FRACTION = 0.08;
export const FRAME_FRONT_MIN_Z_M = -0.030;
export const WINDOW_BELOW_M = 0.003;
export const WINDOW_HALF_DEPTH_M = 0.006;
export const BOX_PAD_X_M = 0.0015;
export const BOX_BELOW_M = 0.004;
export const BOX_ABOVE_M = 0.005;
export const BOX_HALF_DEPTH_M = 0.004;
export const CROP_PAD_PX = 16;
/** A decal on the lens: every vertex within this distance of the lens surface... */
export const DECAL_MAX_LENS_DISTANCE_M = 0.0003;
/** ...and its area at most this fraction of the lens-class area. */
export const DECAL_MAX_AREA_FRACTION = 0.02;
export const CROP_MIN = Object.freeze({width: 96, height: 48});

export type JunctionSide = 'left' | 'right';
export interface JunctionStation {
  readonly x_mm: number;
  /** null when the plane meets no lens triangle (a station inside a lens hole or past a notch). */
  readonly lens_top_y_mm: number | null;
  readonly lens_top_z_mm: number | null;
  readonly brow_bottom_y_mm: number | null;
  readonly overlap_mm: number | null;
  readonly frame_above: boolean;
  /** The frame continued below the window floor: the overlap is saturated at 3 mm. */
  readonly floor: boolean;
  /** The part that holds the lowest frame point (a label for the reader; nothing is decided by name). */
  readonly bottom_part: string | null;
  /** Every frame part in the window, lowest first: a browline's metal eyewire (the groove) and acetate brow (the cover)
   *  read separately here, where the station's own overlap is the lowest of them. */
  readonly parts: readonly JunctionPartReading[];
}
export interface JunctionPartReading {readonly name: string; readonly bottom_y_mm: number; readonly overlap_mm: number; readonly floor: boolean;}
/** A frame part's triangles (asset-local metres) with its name as a label. */
export interface FramePart {readonly name: string; readonly triangles: ArrayLike<number>;}
export interface JunctionPartSummary {
  readonly stations: number; readonly overlap_min_mm: number; readonly overlap_median_mm: number; readonly overlap_max_mm: number;
  /** Stations where this part holds the lowest frame point. */
  readonly lowest_at: number;
}
export interface JunctionBox {readonly min: readonly [number, number, number]; readonly max: readonly [number, number, number];}
export interface JunctionSideReport {
  readonly side: JunctionSide;
  readonly x_sign: -1 | 1;
  /** The side's lens x-range in millimetres (signed), before trimming; null when the side has no lens geometry. */
  readonly lens_x_range_mm: readonly [number, number] | null;
  readonly stations: readonly JunctionStation[];
  readonly summary: {
    readonly stations: number; readonly with_lens: number; readonly frame_above: number; readonly seated: number;
    readonly gap: number; readonly floor: number;
    readonly overlap_min_mm: number | null; readonly overlap_median_mm: number | null; readonly overlap_max_mm: number | null;
    /** The same overlap per frame part, over the stations where that part is in the window. */
    readonly by_part: Readonly<Record<string, JunctionPartSummary>>;
  };
  /** Asset-local metres; null when the side has no lens-top station at all. */
  readonly box_m: JunctionBox | null;
  readonly basis: 'frame_above' | 'lens_top_only' | 'none';
}
/** A frame-classed part riding on the lens surface: left out of the junction (it is not brow). */
export interface LensDecal {
  readonly name: string;
  readonly triangles: number;
  readonly area_mm2: number;
  /** Its area as a fraction of the lens-class area. */
  readonly area_fraction: number;
  /** The farthest of its vertices from the lens surface. */
  readonly max_lens_distance_mm: number;
}
export interface JunctionGeometry {
  readonly method: string;
  readonly sides: readonly JunctionSideReport[];
  /** Frame-classed parts on the lens surface, excluded from every reading above. */
  readonly lens_decals: readonly LensDecal[];
  readonly warnings: readonly string[];
}

const round = (value: number, digits = 3): number => Math.round(value * 10 ** digits) / 10 ** digits;
const mm = (value: number): number => round(value * 1000);

/** The segments where the plane x = `x` cuts a triangle soup (9 numbers per triangle): [y0, z0, y1, z1] per segment.
 *  A vertex exactly on the plane counts on the non-negative side, so a plane through a vertex or an edge yields each
 *  crossing once and never a degenerate triangle twice. */
export function sliceAtX(triangles: ArrayLike<number>, x: number): Float64Array {
  const out: number[] = [];
  const point: number[] = [];
  for (let t = 0; t + 8 < triangles.length; t += 9) {
    point.length = 0;
    for (let e = 0; e < 3; e++) {
      const a = t + e * 3, b = t + ((e + 1) % 3) * 3;
      const da = triangles[a]! - x, db = triangles[b]! - x;
      if ((da < 0) === (db < 0)) continue;
      const f = da / (da - db);
      point.push(triangles[a + 1]! + f * (triangles[b + 1]! - triangles[a + 1]!), triangles[a + 2]! + f * (triangles[b + 2]! - triangles[a + 2]!));
    }
    if (point.length === 4) out.push(point[0]!, point[1]!, point[2]!, point[3]!);
  }
  return Float64Array.from(out);
}

/** Clip the segment (y0, z0)-(y1, z1) to y >= yMin, zMin < z < zMax (Liang-Barsky). Returns the clipped endpoints or
 *  null when nothing of it is inside. */
export function clipSegment(y0: number, z0: number, y1: number, z1: number, yMin: number, zMin: number, zMax: number): [number, number, number, number] | null {
  let t0 = 0, t1 = 1;
  const dy = y1 - y0, dz = z1 - z0;
  for (const [p, q] of [[-dy, y0 - yMin], [-dz, z0 - zMin], [dz, zMax - z0]] as const) {
    if (p === 0) {if (q < 0) return null; continue;}
    const r = q / p;
    if (p < 0) {if (r > t1) return null; if (r > t0) t0 = r;}
    else {if (r < t0) return null; if (r < t1) t1 = r;}
  }
  return [y0 + t0 * dy, z0 + t0 * dz, y0 + t1 * dy, z0 + t1 * dz];
}

/** The non-lens front: triangles whose centroid lies in front of FRAME_FRONT_MIN_Z_M. */
export function frontTriangles(triangles: ArrayLike<number>, minZ = FRAME_FRONT_MIN_Z_M): Float64Array {
  const out: number[] = [];
  for (let t = 0; t + 8 < triangles.length; t += 9) {
    if ((triangles[t + 2]! + triangles[t + 5]! + triangles[t + 8]!) / 3 <= minZ) continue;
    for (let k = 0; k < 9; k++) out.push(triangles[t + k]!);
  }
  return Float64Array.from(out);
}

/** Total area of a triangle soup (square metres). */
export function soupArea(triangles: ArrayLike<number>): number {
  let area = 0;
  for (let t = 0; t + 8 < triangles.length; t += 9) {
    const ux = triangles[t + 3]! - triangles[t]!, uy = triangles[t + 4]! - triangles[t + 1]!, uz = triangles[t + 5]! - triangles[t + 2]!;
    const vx = triangles[t + 6]! - triangles[t]!, vy = triangles[t + 7]! - triangles[t + 1]!, vz = triangles[t + 8]! - triangles[t + 2]!;
    area += Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
  }
  return area;
}

/** Squared distance from point `p` to the triangle at offset `o` of a soup (Ericson, Real-Time Collision Detection 5.1.5). */
export function pointTriangleDistanceSq(p: readonly number[], t: ArrayLike<number>, o: number): number {
  const ax = t[o]!, ay = t[o + 1]!, az = t[o + 2]!;
  const abx = t[o + 3]! - ax, aby = t[o + 4]! - ay, abz = t[o + 5]! - az, acx = t[o + 6]! - ax, acy = t[o + 7]! - ay, acz = t[o + 8]! - az;
  const apx = p[0]! - ax, apy = p[1]! - ay, apz = p[2]! - az;
  const dist = (qx: number, qy: number, qz: number): number => (p[0]! - qx) ** 2 + (p[1]! - qy) ** 2 + (p[2]! - qz) ** 2;
  const d1 = abx * apx + aby * apy + abz * apz, d2 = acx * apx + acy * apy + acz * apz;
  if (d1 <= 0 && d2 <= 0) return dist(ax, ay, az);
  const bpx = apx - abx, bpy = apy - aby, bpz = apz - abz, d3 = abx * bpx + aby * bpy + abz * bpz, d4 = acx * bpx + acy * bpy + acz * bpz;
  if (d3 >= 0 && d4 <= d3) return dist(ax + abx, ay + aby, az + abz);
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) {const v = d1 / (d1 - d3); return dist(ax + v * abx, ay + v * aby, az + v * abz);}
  const cpx = apx - acx, cpy = apy - acy, cpz = apz - acz, d5 = abx * cpx + aby * cpy + abz * cpz, d6 = acx * cpx + acy * cpy + acz * cpz;
  if (d6 >= 0 && d5 <= d6) return dist(ax + acx, ay + acy, az + acz);
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) {const w = d2 / (d2 - d6); return dist(ax + w * acx, ay + w * acy, az + w * acz);}
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
    const w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
    return dist(ax + abx + w * (acx - abx), ay + aby + w * (acy - aby), az + abz + w * (acz - abz));
  }
  const denom = 1 / (va + vb + vc), v = vb * denom, w = vc * denom;
  return dist(ax + abx * v + acx * w, ay + aby * v + acy * w, az + abz * v + acz * w);
}

/** The frame parts that ride on the lens surface (decals): every vertex within `maxDistance` of a lens triangle and an
 *  area of at most `maxAreaFraction` of the lens area. Measured on the triangles themselves (the lens triangles near each
 *  part, binned on a 2 mm grid), never by name. */
export function lensDecals(lens: ArrayLike<number>, parts: readonly FramePart[], maxDistance = DECAL_MAX_LENS_DISTANCE_M,
  maxAreaFraction = DECAL_MAX_AREA_FRACTION): LensDecal[] {
  const lensArea = soupArea(lens), found: LensDecal[] = [];
  if (!(lensArea > 0)) return found;
  const cell = 0.002;
  for (const part of parts) {
    const count = Math.floor(part.triangles.length / 9), area = soupArea(part.triangles);
    if (!count || area > maxAreaFraction * lensArea) continue;
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i + 2 < count * 9; i += 3) for (let a = 0; a < 3; a++) {const v = part.triangles[i + a]!; if (v < lo[a]!) lo[a] = v; if (v > hi[a]!) hi[a] = v;}
    const grid = new Map<string, number[]>(), key = (x: number, y: number, z: number): string => `${x},${y},${z}`;
    const binOf = (value: number, axis: number): number => Math.floor((value - lo[axis]!) / cell);
    for (let t = 0; t + 8 < lens.length; t += 9) {
      const tl = [0, 1, 2].map(a => Math.min(lens[t + a]!, lens[t + 3 + a]!, lens[t + 6 + a]!) - maxDistance);
      const th = [0, 1, 2].map(a => Math.max(lens[t + a]!, lens[t + 3 + a]!, lens[t + 6 + a]!) + maxDistance);
      if ([0, 1, 2].some(a => tl[a]! > hi[a]! || th[a]! < lo[a]!)) continue;
      const b0 = [0, 1, 2].map(a => binOf(Math.max(tl[a]!, lo[a]!), a)), b1 = [0, 1, 2].map(a => binOf(Math.min(th[a]!, hi[a]!), a));
      for (let x = b0[0]!; x <= b1[0]!; x++) for (let y = b0[1]!; y <= b1[1]!; y++) for (let z = b0[2]!; z <= b1[2]!; z++) {
        const k = key(x, y, z); (grid.get(k) ?? grid.set(k, []).get(k)!).push(t);
      }
    }
    if (!grid.size) continue;
    // Every vertex must be within reach; the first one that is not ends the part's test.
    let worst = 0;
    const point = [0, 0, 0];
    for (let i = 0; i + 2 < count * 9 && worst <= maxDistance; i += 3) {
      point[0] = part.triangles[i]!; point[1] = part.triangles[i + 1]!; point[2] = part.triangles[i + 2]!;
      let best = Infinity;
      for (const t of grid.get(key(binOf(point[0], 0), binOf(point[1], 1), binOf(point[2], 2))) ?? []) best = Math.min(best, pointTriangleDistanceSq(point, lens, t));
      worst = Math.max(worst, Math.sqrt(best));
    }
    if (worst > maxDistance) continue;
    found.push({name: part.name, triangles: count, area_mm2: round(area * 1e6, 2), area_fraction: round(area / lensArea, 4),
      max_lens_distance_mm: round(worst * 1000, 4)});
  }
  return found;
}

function median(values: readonly number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/** One station: the lens top and the brow bottom in the plane x = `x`, overall and per frame part. */
export function junctionStation(lens: ArrayLike<number>, frame: readonly FramePart[], x: number): JunctionStation {
  const lensSegments = sliceAtX(lens, x);
  if (!lensSegments.length) {
    return {x_mm: mm(x), lens_top_y_mm: null, lens_top_z_mm: null, brow_bottom_y_mm: null, overlap_mm: null, frame_above: false, floor: false,
      bottom_part: null, parts: []};
  }
  let yTop = -Infinity, zTop = 0;
  for (let i = 0; i < lensSegments.length; i += 2) if (lensSegments[i]! > yTop) {yTop = lensSegments[i]!; zTop = lensSegments[i + 1]!;}
  const floorY = yTop - WINDOW_BELOW_M;
  const parts: JunctionPartReading[] = [];
  let yBottom = Infinity, bottomPart: string | null = null;
  for (const part of frame) {
    let partBottom = Infinity;
    const frameSegments = sliceAtX(part.triangles, x);
    for (let i = 0; i < frameSegments.length; i += 4) {
      const clipped = clipSegment(frameSegments[i]!, frameSegments[i + 1]!, frameSegments[i + 2]!, frameSegments[i + 3]!, floorY,
        zTop - WINDOW_HALF_DEPTH_M, zTop + WINDOW_HALF_DEPTH_M);
      if (clipped) partBottom = Math.min(partBottom, clipped[0], clipped[2]);
    }
    if (!Number.isFinite(partBottom)) continue;
    parts.push({name: part.name, bottom_y_mm: mm(partBottom), overlap_mm: mm(yTop - partBottom), floor: partBottom <= floorY + 1e-9});
    if (partBottom < yBottom) {yBottom = partBottom; bottomPart = part.name;}
  }
  parts.sort((p, q) => p.bottom_y_mm - q.bottom_y_mm);
  const found = Number.isFinite(yBottom);
  return {x_mm: mm(x), lens_top_y_mm: mm(yTop), lens_top_z_mm: mm(zTop), brow_bottom_y_mm: found ? mm(yBottom) : null,
    overlap_mm: found ? mm(yTop - yBottom) : null, frame_above: found, floor: found && yBottom <= floorY + 1e-9, bottom_part: bottomPart, parts};
}

/** The lens-top-vs-brow measurement of both sides, in asset-local metres: `lens` is every lens-class triangle, `frame`
 *  every non-lens triangle (one soup, or named parts; the front band is selected here). */
export function lensTopVsBrow(lens: ArrayLike<number>, frame: ArrayLike<number> | readonly FramePart[], stations = JUNCTION_STATIONS): JunctionGeometry {
  const named: readonly FramePart[] = Array.isArray(frame) && (frame.length === 0 || typeof frame[0] === 'object')
    ? frame as readonly FramePart[] : [{name: 'frame', triangles: frame as ArrayLike<number>}];
  // Decals on the lens surface are not brow: found on the whole parts, then left out of every reading.
  const decals = lensDecals(lens, named), decalNames = new Set(decals.map(decal => decal.name));
  const front = named.filter(part => !decalNames.has(part.name)).map(part => ({name: part.name, triangles: frontTriangles(part.triangles)}))
    .filter(part => part.triangles.length > 0);
  const warnings: string[] = decals.map(decal => `frame part "${decal.name}" rides on the lens (every vertex within ${decal.max_lens_distance_mm} mm of the `
    + `lens surface; ${decal.area_mm2} mm², ${(decal.area_fraction * 100).toFixed(1)} % of the lens area): a decal, not brow, so it is left out of the `
    + 'lens-top-vs-brow reading');
  const sides: JunctionSideReport[] = [];
  for (const [side, sign] of [['left', -1], ['right', 1]] as const) {
    // The side's lens x-range from TRIANGLE extents clipped at x = 0, not from vertices: a shield's single lens has
    // vertices only near its two ends, yet covers both sides up to the bridge.
    let lo = Infinity, hi = -Infinity;
    for (let t = 0; t + 8 < lens.length; t += 9) {
      const a = lens[t]! * sign, b = lens[t + 3]! * sign, c = lens[t + 6]! * sign;
      const top = Math.max(a, b, c);
      if (!(top > 0)) continue;
      lo = Math.min(lo, Math.max(0, Math.min(a, b, c))); hi = Math.max(hi, top);
    }
    if (!(hi > lo)) {
      warnings.push(`${side} side (x ${sign < 0 ? '<' : '>'} 0): no lens geometry, so no junction is measured there`);
      sides.push({side, x_sign: sign, lens_x_range_mm: null, stations: [], summary: {stations: 0, with_lens: 0, frame_above: 0, seated: 0, gap: 0, floor: 0,
        overlap_min_mm: null, overlap_median_mm: null, overlap_max_mm: null, by_part: {}}, box_m: null, basis: 'none'});
      continue;
    }
    const trim = (hi - lo) * JUNCTION_TRIM_FRACTION, a = lo + trim, b = hi - trim;
    const rows: JunctionStation[] = [];
    for (let k = 0; k < stations; k++) rows.push(junctionStation(lens, front, sign * (stations === 1 ? (a + b) / 2 : a + (b - a) * k / (stations - 1))));
    rows.sort((p, q) => p.x_mm - q.x_mm);
    const withLens = rows.filter(row => row.lens_top_y_mm !== null), above = withLens.filter(row => row.frame_above);
    const overlaps = above.map(row => row.overlap_mm!);
    const basisRows = above.length ? above : withLens;
    let box: JunctionBox | null = null;
    if (basisRows.length) {
      const xs = basisRows.map(row => row.x_mm / 1000), yt = basisRows.map(row => row.lens_top_y_mm! / 1000), zt = basisRows.map(row => row.lens_top_z_mm! / 1000);
      const ytop = basisRows.map(row => Math.max(row.lens_top_y_mm!, row.brow_bottom_y_mm ?? -Infinity) / 1000);
      box = {min: [Math.min(...xs) - BOX_PAD_X_M, Math.min(...yt) - BOX_BELOW_M, Math.min(...zt) - BOX_HALF_DEPTH_M],
        max: [Math.max(...xs) + BOX_PAD_X_M, Math.max(...ytop) + BOX_ABOVE_M, Math.max(...zt) + BOX_HALF_DEPTH_M]};
    }
    if (withLens.length && !above.length) {
      warnings.push(`${side} side: no frame material above the lens top at any of ${withLens.length} stations (rimless or nylor top); the crop `
        + 'is framed on the lens top alone');
    }
    if (withLens.length < rows.length) warnings.push(`${side} side: ${rows.length - withLens.length} of ${rows.length} stations meet no lens triangle`);
    const floors = above.filter(row => row.floor).length;
    if (floors) {
      warnings.push(`${side} side: at ${floors} station(s) the frame continues below the 3 mm window under the lens top (the overlap reads `
        + 'the 3 mm window floor, saturated)');
    }
    const perPart = new Map<string, {values: number[]; lowest: number}>();
    for (const row of above) for (const reading of row.parts) {
      const entry = perPart.get(reading.name) ?? {values: [], lowest: 0};
      entry.values.push(reading.overlap_mm); if (row.bottom_part === reading.name) entry.lowest++;
      perPart.set(reading.name, entry);
    }
    const byPart: Record<string, JunctionPartSummary> = {};
    for (const [name, entry] of perPart) {
      byPart[name] = {stations: entry.values.length, overlap_min_mm: Math.min(...entry.values), overlap_median_mm: round(median(entry.values)!),
        overlap_max_mm: Math.max(...entry.values), lowest_at: entry.lowest};
    }
    sides.push({side, x_sign: sign, lens_x_range_mm: sign < 0 ? [mm(-hi), mm(-lo)] : [mm(lo), mm(hi)], stations: rows,
      summary: {stations: rows.length, with_lens: withLens.length, frame_above: above.length,
        seated: overlaps.filter(value => value > 0).length, gap: overlaps.filter(value => value < 0).length, floor: floors,
        overlap_min_mm: overlaps.length ? Math.min(...overlaps) : null,
        overlap_median_mm: overlaps.length ? round(median(overlaps)!) : null,
        overlap_max_mm: overlaps.length ? Math.max(...overlaps) : null, by_part: byPart},
      box_m: box, basis: above.length ? 'frame_above' : withLens.length ? 'lens_top_only' : 'none'});
  }
  return {method: `triangle slices: ${stations} stations per side over the lens x-range trimmed ${JUNCTION_TRIM_FRACTION * 100} % at each end; `
    + `frame = non-lens triangles with centroid z > ${FRAME_FRONT_MIN_Z_M * 1000} mm; brow bottom = lowest frame point with y >= lens top - `
    + `${WINDOW_BELOW_M * 1000} mm and |z - lens top z| < ${WINDOW_HALF_DEPTH_M * 1000} mm; overlap_mm = lens top - brow bottom (positive = seated); `
    + `frame parts riding on the lens (every vertex within ${DECAL_MAX_LENS_DISTANCE_M * 1000} mm of it, area <= ${DECAL_MAX_AREA_FRACTION * 100} % of the `
    + 'lens area) are decals, left out (lens_decals)',
  sides, lens_decals: decals, warnings};
}

/** The 8 corners of a box, as xyz triples (for projectAssetPoints). */
export function boxCorners(box: JunctionBox): Float32Array {
  const out = new Float32Array(24);
  let i = 0;
  for (const x of [box.min[0], box.max[0]]) for (const y of [box.min[1], box.max[1]]) for (const z of [box.min[2], box.max[2]]) {
    out[i++] = x; out[i++] = y; out[i++] = z;
  }
  return out;
}

export interface CropRect {readonly x: number; readonly y: number; readonly width: number; readonly height: number;}
/** The crop around projected points ([x, y, ndcZ] per point, render pixels): their bbox plus CROP_PAD_PX on each side,
 *  grown to at least CROP_MIN around its centre, clamped to the frame. Null when a point is behind the camera or the
 *  box lies entirely outside the frame. */
export function cropRect(projected: ArrayLike<number>, width: number, height: number): {rect: CropRect | null; reason?: string} {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i + 2 < projected.length; i += 3) {
    const x = projected[i]!, y = projected[i + 1]!;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return {rect: null, reason: 'a corner of the junction box is behind the camera'};
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  if (!Number.isFinite(x0)) return {rect: null, reason: 'no junction box points'};
  x0 -= CROP_PAD_PX; y0 -= CROP_PAD_PX; x1 += CROP_PAD_PX; y1 += CROP_PAD_PX;
  if (x1 <= 0 || y1 <= 0 || x0 >= width || y0 >= height) return {rect: null, reason: 'the junction box projects outside the frame'};
  const grow = (lo: number, hi: number, minimum: number, limit: number): [number, number] => {
    if (hi - lo < minimum) {const centre = (lo + hi) / 2; lo = centre - minimum / 2; hi = centre + minimum / 2;}
    if (lo < 0) {hi -= lo; lo = 0;}
    if (hi > limit) {lo -= hi - limit; hi = limit;}
    return [Math.max(0, Math.floor(lo)), Math.min(limit, Math.ceil(hi))];
  };
  const [cx0, cx1] = grow(x0, x1, CROP_MIN.width, width), [cy0, cy1] = grow(y0, y1, CROP_MIN.height, height);
  return {rect: {x: cx0, y: cy0, width: cx1 - cx0, height: cy1 - cy0}};
}

/** The near side of a pose: the side whose projected junction box is larger (for yaw > 0 that is the asset's -X). */
export function nearSide(areas: Readonly<Partial<Record<JunctionSide, number>>>): JunctionSide | null {
  const left = areas.left ?? -1, right = areas.right ?? -1;
  if (left < 0 && right < 0) return null;
  return left >= right ? 'left' : 'right';
}
