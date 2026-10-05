/** The measured §2 rows (units, origin, orientation), ported line for line from `glasses3d/g3d_host/spec.py`
 *  (`_units`, `_orientation`, `_origin`, `_temple_points`, `plane_points`), so arview and the Python validator decide
 *  the same files the same way. Pure and three-free: the caller hands over world-space metres.
 *
 *  Calibrated on 8 real models (2026-10-05): the Studio deliveries have their bridge underside within 0.02 mm of the
 *  origin and temples reaching -143..-173 mm; real temple roots sit +4.0..+18.0 mm above the lens centre, lens halves
 *  within 0.16 mm of each other, temple slopes -9.4..+4.7 degrees. */

export const WIDTH_PASS_M = Object.freeze([0.095, 0.20] as const);
export const WIDTH_TYPICAL_M = Object.freeze([0.115, 0.170] as const);
/** The wider ranges arview rescales from (WP1_DESIGN.md §1): an X span of 60-250 units is millimetres, 6-25 centimetres. */
export const MILLIMETRE_SPAN = Object.freeze([60, 250] as const);
export const CENTIMETRE_SPAN = Object.freeze([6, 25] as const);
export const ORIGIN_TOL_MM = 1.0;
export const ORIGIN_WARN_MM = 0.25;
export const ORIGIN_SLICE_X_MM = Object.freeze([-1.0, -0.5, 0.0, 0.5, 1.0] as const);
export const FRONT_BAND_MM = 25.0;
export const TEMPLE_X_MM = 45.0;
export const TEMPLE_REACH_Z_MM = -60.0;
export const TEMPLE_ROOT_Z_MM = Object.freeze([-40.0, -15.0] as const);
export const TEMPLE_ROOT_PLANES_MM = Object.freeze([-35.0, -30.0, -25.0, -20.0] as const);
export const UP_WARN_MM = 2.0;
export const ROLL_TOL_MM = 3.0;
export const PITCH_Z_MM = Object.freeze([-90.0, -30.0] as const);
export const PITCH_PLANES_MM = Object.freeze(Array.from({length: 11}, (_, i) => -85 + 5 * i));
export const PITCH_MIN_SPAN_MM = 15.0;
export const PITCH_MAX_DEG = 15.0;
export const LENS_FRONT_FRACTION = 0.3;

/** Where a measured row writes (ModelReportBuilder, or a prefixing wrapper). */
export interface RowSink {
  fail(row: 'units' | 'origin' | 'orientation', message: string): void;
  note(row: 'units' | 'origin' | 'orientation', message: string): void;
  warn(message: string): void;
  measure(key: string, value: unknown): void;
}

/** One class of geometry: unique referenced vertices (xyz) and triangles (9 numbers each), world metres. */
export interface PointSet {readonly vertices: Float64Array; readonly triangles: Float64Array;}
export interface MeasuredGeometry {readonly lens: PointSet; readonly frame: PointSet;}

const round = (value: number, digits: number): number => {const f = 10 ** digits; return Math.round(value * f) / f;};
const fixed = (value: number, digits: number): string => value.toFixed(digits);
const signed = (value: number, digits: number): string => (value >= 0 ? '+' : '') + value.toFixed(digits);
const mid = (values: ArrayLike<number>): number => {
  let low = Infinity, high = -Infinity;
  for (let i = 0; i < values.length; i++) {const v = values[i]!; if (v < low) low = v; if (v > high) high = v;}
  return (low + high) / 2;
};
/** Column `axis` of an xyz-flattened array. */
function column(points: Float64Array, axis: number): Float64Array {
  const out = new Float64Array(points.length / 3);
  for (let i = 0; i < out.length; i++) out[i] = points[i * 3 + axis]!;
  return out;
}
function concat(a: Float64Array, b: Float64Array): Float64Array {const out = new Float64Array(a.length + b.length); out.set(a); out.set(b, a.length); return out;}
function bounds(points: Float64Array): {lo: [number, number, number]; hi: [number, number, number]} {
  const lo: [number, number, number] = [Infinity, Infinity, Infinity], hi: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < points.length; i += 3) for (let a = 0; a < 3; a++) {
    const v = points[i + a]!; if (v < lo[a]!) lo[a] = v; if (v > hi[a]!) hi[a] = v;
  }
  return {lo, hi};
}

/** Points where the triangles' edges cross the plane coord[axis] == value, plus vertices on the plane (spec.py
 *  `plane_points`): sampling the surface, not the vertices, keeps measurements independent of tessellation. */
export function planePoints(triangles: Float64Array, axis: number, value: number): Float64Array {
  const out: number[] = [];
  for (let t = 0; t < triangles.length; t += 9) {
    const c0 = triangles[t + axis]! - value, c1 = triangles[t + 3 + axis]! - value, c2 = triangles[t + 6 + axis]! - value;
    if (Math.min(c0, c1, c2) > 0 || Math.max(c0, c1, c2) < 0) continue;
    const c = [c0, c1, c2];
    for (const [a, b] of [[0, 1], [1, 2], [2, 0]] as const) {
      const ca = c[a]!, cb = c[b]!;
      if (ca * cb < 0) {
        const s = ca / (ca - cb);
        for (let k = 0; k < 3; k++) out.push(triangles[t + a * 3 + k]! + s * (triangles[t + b * 3 + k]! - triangles[t + a * 3 + k]!));
      }
      if (ca === 0) for (let k = 0; k < 3; k++) out.push(triangles[t + a * 3 + k]!);
    }
  }
  return Float64Array.from(out);
}

/** Non-lens points on one side (sign * x > 45 mm) with z strictly inside `zRangeMm`, metres (spec.py `_temple_points`):
 *  the vertices when at least 3 span `minSpanMm` in z, else one point per sample plane (the middle of its
 *  cross-section), so a coarse box temple with no vertex in the slab is still measured. */
export function templePoints(frame: PointSet, sign: number, zRangeMm: readonly [number, number], planesMm: readonly number[], minSpanMm = 0): Float64Array {
  const tx = TEMPLE_X_MM / 1000, zlo = zRangeMm[0] / 1000, zhi = zRangeMm[1] / 1000;
  const picked: number[] = [];
  let zmin = Infinity, zmax = -Infinity;
  for (let i = 0; i < frame.vertices.length; i += 3) {
    const x = frame.vertices[i]!, z = frame.vertices[i + 2]!;
    if (x * sign > tx && z > zlo && z < zhi) {picked.push(x, frame.vertices[i + 1]!, z); zmin = Math.min(zmin, z); zmax = Math.max(zmax, z);}
  }
  if (picked.length >= 9 && (zmax - zmin) * 1000 >= minSpanMm) return Float64Array.from(picked);
  const mids: number[] = [];
  for (const z of planesMm) {
    const points = planePoints(frame.triangles, 2, z / 1000), xs: number[] = [], ys: number[] = [];
    for (let i = 0; i < points.length; i += 3) if (points[i]! * sign > tx) {xs.push(points[i]!); ys.push(points[i + 1]!);}
    if (xs.length) mids.push(mid(xs), mid(ys), z / 1000);
  }
  return Float64Array.from(mids);
}

/** Least-squares slope dy/dz of a point set (np.polyfit(z, y, 1)[0]). */
function slopeYZ(points: Float64Array): number {
  const n = points.length / 3;
  let mz = 0, my = 0;
  for (let i = 0; i < n; i++) {mz += points[i * 3 + 2]!; my += points[i * 3 + 1]!;}
  mz /= n; my /= n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {const dz = points[i * 3 + 2]! - mz; num += dz * (points[i * 3 + 1]! - my); den += dz * dz;}
  return num / den;
}

export interface UnitsResult {
  /** Overall X width in file units. */
  readonly width: number;
  /** The rescale arview applies: 1 (metres or implausible), 0.001 (millimetres) or 0.01 (centimetres). */
  readonly scale: 1 | 0.001 | 0.01;
  /** False when the width is no plausible frame width in any unit (no rescale; origin/orientation are not checked). */
  readonly plausible: boolean;
}

/** bbox_mm and width_mm of a point set, scaled by `scale` (the units rescale) first. */
export function measureBounds(all: Float64Array, sink: Pick<RowSink, 'measure'>, scale = 1): number {
  const {lo, hi} = bounds(all), width = (hi[0] - lo[0]) * scale;
  sink.measure('bbox_mm', {min: lo.map(v => round(v * scale * 1000, 2)), max: hi.map(v => round(v * scale * 1000, 2))});
  sink.measure('width_mm', round(width * 1000, 2));
  return width;
}

/** spec.py `_units` (same pass/fail and wording), plus the wider rescale ranges of WP1_DESIGN.md §1. */
export function unitsRow(all: Float64Array, sink: RowSink): UnitsResult {
  const width = measureBounds(all, sink);
  const [low, high] = WIDTH_PASS_M;
  if (width >= low && width <= high) {
    sink.note('units', `width ${fixed(width, 4)} units = ${fixed(width * 1000, 1)} mm (metres)`);
    if (width < WIDTH_TYPICAL_M[0] || width > WIDTH_TYPICAL_M[1]) {
      sink.warn(`overall width ${fixed(width * 1000, 1)} mm is outside the usual ${fixed(WIDTH_TYPICAL_M[0] * 1000, 0)}-`
        + `${fixed(WIDTH_TYPICAL_M[1] * 1000, 0)} mm: check the scale`);
    }
    return {width, scale: 1, plausible: true};
  }
  if (width >= MILLIMETRE_SPAN[0] && width <= MILLIMETRE_SPAN[1]) {
    sink.fail('units', `width ${fixed(width, 1)} units: millimetre-scaled (expected metres, about ${fixed(width / 1000, 3)})`);
    return {width, scale: 0.001, plausible: true};
  }
  if (width >= CENTIMETRE_SPAN[0] && width <= CENTIMETRE_SPAN[1]) {
    sink.fail('units', `width ${fixed(width, 2)} units: centimetre-scaled (expected metres, about ${fixed(width / 100, 3)})`);
    return {width, scale: 0.01, plausible: true};
  }
  sink.fail('units', `width ${Number(width.toPrecision(4))} units is not a plausible frame width in metres (${low}-${high})`);
  return {width, scale: 1, plausible: false};
}

/** spec.py `_orientation`. */
export function orientationRow(g: MeasuredGeometry, sink: RowSink): void {
  const all = concat(g.lens.vertices, g.frame.vertices), {lo, hi} = bounds(all);
  const ext = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]] as const;
  if (!(ext[1] < ext[0] && ext[1] < ext[2])) {
    sink.fail('orientation', `Y is not the thinnest axis (extent mm x ${fixed(ext[0] * 1000, 1)}, y ${fixed(ext[1] * 1000, 1)}, `
      + `z ${fixed(ext[2] * 1000, 1)}): not +Y up`);
  }
  const tx = TEMPLE_X_MM / 1000, reach = TEMPLE_REACH_Z_MM / 1000, Vf = g.frame.vertices, Vl = g.lens.vertices;
  const temples: Record<string, number | null> = {}, missing: string[] = [];
  for (const [side, sign] of [['left', -1], ['right', 1]] as const) {
    let zmin = Infinity;
    for (let i = 0; i < Vf.length; i += 3) if (Vf[i]! * sign > tx) zmin = Math.min(zmin, Vf[i + 2]!);
    const found = Number.isFinite(zmin);
    temples[side] = found ? round(zmin * 1000, 2) : null;
    if (!found || zmin > reach) {
      const where = sign < 0 ? 'x < -45 mm' : 'x > +45 mm';
      const got = found ? `it reaches only z ${fixed(zmin * 1000, 1)} mm` : 'none there';
      sink.fail('orientation', `temple ${side} missing: no non-lens geometry at ${where} reaches z <= ${TEMPLE_REACH_Z_MM.toFixed(0)} mm `
        + `(${got}); temples must extend along -Z`);
      missing.push(side);
    }
  }
  sink.measure('temple_reach_z_mm', temples);
  let sideMaxZ = -Infinity, sideCount = 0;
  for (let i = 0; i < Vf.length; i += 3) if (Math.abs(Vf[i]!) > tx) {sideCount++; sideMaxZ = Math.max(sideMaxZ, Vf[i + 2]!);}
  if (missing.length === 2 && sideCount && sideMaxZ >= -reach) {
    sink.fail('orientation', `geometry at |x| > 45 mm extends to z +${(sideMaxZ * 1000).toFixed(0)} mm: the model faces backwards (not +Z forward)`);
  }
  if (Vl.length) {
    const lensCy = mid(column(Vl, 1));
    const roots: number[] = [];
    for (const sign of [-1, 1]) {
      const P = templePoints(g.frame, sign, TEMPLE_ROOT_Z_MM, TEMPLE_ROOT_PLANES_MM);
      if (P.length) {let sum = 0; for (let i = 1; i < P.length; i += 3) sum += P[i]!; roots.push(sum / (P.length / 3));}
    }
    if (roots.length) {
      const dy = roots.reduce((a, b) => a + b, 0) / roots.length - lensCy;
      sink.measure('temple_roots_above_lens_centre_mm', round(dy * 1000, 2));
      if (dy <= 0) {
        sink.fail('orientation', `not +Y up: the temple roots (|x| > 45 mm, z -40..-15 mm) sit ${fixed(-dy * 1000, 1)} mm BELOW the lens `
          + 'centre (real frames: +4 to +18 mm above); the model is upside down or mirrored in Y');
      } else if (dy * 1000 < UP_WARN_MM) {
        sink.warn(`the temple roots sit only ${fixed(dy * 1000, 1)} mm above the lens centre (real frames: +4 to +18 mm): check +Y up`);
      }
    } else if (!missing.length) sink.warn('+Y up not measured: no non-lens geometry at |x| > 45 mm between z -40 and -15 mm');
    const leftY: number[] = [], rightY: number[] = [];
    for (let i = 0; i < Vl.length; i += 3) {if (Vl[i]! < 0) leftY.push(Vl[i + 1]!); else if (Vl[i]! > 0) rightY.push(Vl[i + 1]!);}
    if (leftY.length && rightY.length) {
      const d = mid(rightY) - mid(leftY);
      sink.measure('lens_halves_dy_mm', round(d * 1000, 3));
      if (Math.abs(d) * 1000 > ROLL_TOL_MM) {
        sink.fail('orientation', `rolled: the right lens half's centre is ${signed(d * 1000, 1)} mm higher than the left's (must be within `
          + `${ROLL_TOL_MM.toFixed(0)} mm; real frames <= 0.16 mm)`);
      }
    } else sink.warn('roll not measured: lens geometry on one side of x = 0 only');
    const lens = bounds(Vl), lc = [0, 1, 2].map(a => (lens.lo[a]! + lens.hi[a]!) / 2);
    sink.measure('lens_bbox_centre_mm', lc.map(v => round(v * 1000, 2)));
    if (lc[2]! < hi[2] + LENS_FRONT_FRACTION * (lo[2] - hi[2])) {
      sink.fail('orientation', `lenses sit at z ${fixed(lc[2]! * 1000, 1)} mm, behind the front (z ${fixed(hi[2] * 1000, 1)} mm): not +Z forward`);
    }
  } else sink.warn('+Y up, roll and lens position not measured: no measurable lens geometry');
  const slopes: Record<string, number> = {};
  for (const [side, sign] of [['left', -1], ['right', 1]] as const) {
    if (missing.includes(side)) continue;
    const P = templePoints(g.frame, sign, PITCH_Z_MM, PITCH_PLANES_MM, PITCH_MIN_SPAN_MM);
    let zmin = Infinity, zmax = -Infinity;
    for (let i = 2; i < P.length; i += 3) {zmin = Math.min(zmin, P[i]!); zmax = Math.max(zmax, P[i]!);}
    if (P.length < 6 || (zmax - zmin) * 1000 < PITCH_MIN_SPAN_MM) {
      sink.warn(`pitch not measured on the ${side}: too little temple between z -90 and -30 mm`);
      continue;
    }
    const slope = Math.atan(slopeYZ(P)) * 180 / Math.PI;
    slopes[side] = round(slope, 2);
    if (Math.abs(slope) >= PITCH_MAX_DEG) {
      sink.fail('orientation', `pitched: the ${side} temple's straight segment (z -90..-30 mm) slopes ${signed(slope, 1)} deg (must be under `
        + `${PITCH_MAX_DEG.toFixed(0)}; real frames -9.4 to +4.7)`);
    }
  }
  if (Object.keys(slopes).length) sink.measure('temple_slope_deg', slopes);
}

/** spec.py `_origin`: frame AND lens triangles sliced at x in {-1, -0.5, 0, 0.5, 1} mm; the lowest crossing in the front
 *  25 mm is the bridge underside. Its x is judged on the lens-class bounding box (the bridge sits between the lenses);
 *  only when no lens geometry spans x = 0 is it judged on the front region (its surface clipped at the band plane), so
 *  opened temples and one-sided ornaments never move it. `excluded` counts primitives left out after geometry
 *  failures (for the message).
 *
 *  WP1_DESIGN.md §1: an origin off by more than 1 mm, or unverifiable, renders anyway with a LOUD warning and the
 *  origin row failed; every failure here is therefore also a warning (the webcam page shows the warnings). */
export function originRow(g: MeasuredGeometry, sink: RowSink, excluded = 0): void {
  const all = concat(g.lens.vertices, g.frame.vertices), {lo, hi} = bounds(all);
  const bandLo = hi[2] - FRONT_BAND_MM / 1000;
  const failOrigin = (message: string): void => {
    sink.fail('origin', message);
    // No render claim: the same warning stands on a model refused for another row.
    sink.warn(`ORIGIN: ${message}. The delivered file violates §2 Origin; arview never moves a model (it is drawn where it was authored).`);
  };
  const cut = (triangles: Float64Array): number[] => {
    const out: number[] = [];
    for (const x of ORIGIN_SLICE_X_MM) {
      const points = planePoints(triangles, 0, x / 1000);
      for (let i = 0; i < points.length; i += 3) if (points[i + 2]! >= bandLo) out.push(points[i]!, points[i + 1]!, points[i + 2]!);
    }
    return out;
  };
  const frameCut = cut(g.frame.triangles), lensCut = cut(g.lens.triangles), band = [...frameCut, ...lensCut];
  if (!band.length) {
    const why = excluded ? ` (${excluded} primitive(s) excluded after geometry failures)` : '';
    failOrigin(`origin unverifiable: no frame or lens surface crosses x = 0 (+/-1 mm) within the front ${FRONT_BAND_MM.toFixed(0)} mm, `
      + `so the bridge underside cannot be measured${why}`);
  } else {
    let y0 = Infinity;
    for (let i = 1; i < band.length; i += 3) y0 = Math.min(y0, band[i]!);
    let lensMin = Infinity;
    for (let i = 1; i < lensCut.length; i += 3) lensMin = Math.min(lensMin, lensCut[i]!);
    const surface = lensCut.length && lensMin <= y0 ? 'lens' : 'frame';
    let zLo = Infinity, zHi = -Infinity;
    for (let i = 0; i < band.length; i += 3) if (band[i + 1]! <= y0 + 0.002) {zLo = Math.min(zLo, band[i + 2]!); zHi = Math.max(zHi, band[i + 2]!);}
    const dz = zLo - 0.001 <= 0 && 0 <= zHi + 0.001 ? 0 : Math.min(Math.abs(zLo), Math.abs(zHi));
    sink.measure('bridge_underside_mm', {y: round(y0 * 1000, 3), z_range: [round(zLo * 1000, 2), round(zHi * 1000, 2)], surface});
    if (Math.abs(y0) * 1000 > ORIGIN_TOL_MM) failOrigin(`bridge underside is at y ${signed(y0 * 1000, 2)} mm (must be 0 +/- ${ORIGIN_TOL_MM.toFixed(1)} mm)`);
    else if (Math.abs(y0) * 1000 > ORIGIN_WARN_MM) sink.warn(`bridge underside is at y ${signed(y0 * 1000, 2)} mm (allowed, but not exact)`);
    if (dz * 1000 > ORIGIN_TOL_MM) {
      failOrigin(`origin z is ${fixed(dz * 1000, 2)} mm outside the bridge underside's depth (${fixed(zLo * 1000, 1)}..${fixed(zHi * 1000, 1)} mm)`);
    }
  }
  // x: judged on the lens-class bounding box. The front region (vertices at or ahead of the band plane plus the surface
  // clipped at it, so independent of tessellation) and the whole model are reported; the front region is judged only
  // when no lens geometry spans x = 0.
  const frontPoints = Array.from(planePoints(concat(g.frame.triangles, g.lens.triangles), 2, bandLo));
  for (let i = 0; i < all.length; i += 3) if (all[i + 2]! >= bandLo) frontPoints.push(all[i]!, all[i + 1]!, all[i + 2]!);
  const fx = frontPoints.length ? mid(column(Float64Array.from(frontPoints), 0)) : null;
  if (fx !== null) sink.measure('front_centre_x_mm', round(fx * 1000, 3));
  const whole = (lo[0] + hi[0]) / 2;
  const lens = g.lens.vertices.length ? bounds(g.lens.vertices) : null;
  let xc: number | null, judged: 'lenses' | 'front';
  if (lens && lens.lo[0] < 0 && 0 < lens.hi[0]) {
    xc = (lens.lo[0] + lens.hi[0]) / 2; judged = 'lenses';
    sink.measure('lens_centre_x_mm', round(xc * 1000, 3));
  } else {
    xc = fx; judged = 'front';
    sink.warn(`origin x judged on the front region (z >= ${fixed(bandLo * 1000, 1)} mm): no lens geometry spans x = 0`);
  }
  sink.measure('origin_x_judged_on', judged);
  if (xc === null) failOrigin('origin x unverifiable: no lens geometry spans x = 0 and no surface reaches the front band');
  else if (Math.abs(xc) * 1000 > ORIGIN_TOL_MM) {
    const what = judged === 'lenses' ? 'the lenses are' : `the front (z >= ${fixed(bandLo * 1000, 1)} mm) is`;
    failOrigin(`${what} centred at x ${signed(xc * 1000, 2)} mm, not on the x = 0 plane (must be within ${ORIGIN_TOL_MM.toFixed(1)} mm)`);
  } else if (judged === 'lenses') {
    const off = ([['front region', fx], ['whole model', whole]] as const).flatMap(([name, value]) =>
      value !== null && Math.abs(value) * 1000 > ORIGIN_TOL_MM ? [`the ${name} is centred at x ${signed(value * 1000, 2)} mm`] : []);
    if (off.length) sink.warn(`${off.join(' and ')} although the lenses are centred at x ${signed(xc * 1000, 2)} mm (asymmetric temples or ornaments?)`);
  }
}
