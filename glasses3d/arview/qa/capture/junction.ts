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
 *  stations instead (`basis: 'lens_top_only'`, reported). */

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
export interface JunctionGeometry {
  readonly method: string;
  readonly sides: readonly JunctionSideReport[];
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
  const front = named.map(part => ({name: part.name, triangles: frontTriangles(part.triangles)})).filter(part => part.triangles.length > 0);
  const warnings: string[] = [];
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
    + `${WINDOW_BELOW_M * 1000} mm and |z - lens top z| < ${WINDOW_HALF_DEPTH_M * 1000} mm; overlap_mm = lens top - brow bottom (positive = seated)`,
  sides, warnings};
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
