import { VIRTUAL_CAMERA } from './projection.ts';

/** Frame-local RGB evidence for a limited nasal contour correction; not a scan. */
export interface BoundaryImage { data: Uint8ClampedArray; width: number; height: number }
type V2 = [number, number];
type Edge = { a: number; b: number; triangles: number[] };
export interface BoundaryDecision {
  y: number; x: number; side: number; a: number; b: number; t: number;
  target: number | null; shift: number; score: number; accepted: boolean; reason: string;
}
const distance = (a: V2, b: V2): number => Math.hypot(a[0] - b[0], a[1] - b[1]);
const cross = (a: V2, b: V2, c: V2): number =>
  (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);

export function refineLocalNasalBoundary(
  source: Float32Array, canonical: readonly number[], indices: readonly number[], image: BoundaryImage,
): { positions: Float32Array; decisions: BoundaryDecision[]; changedVertices: number;
  maxShiftPx: number; vertexShifts: { index: number; shift: number }[]; rejectedGeometry: boolean } {
  const positions = source.slice(), decisions: BoundaryDecision[] = [];
  const result = { positions, decisions, changedVertices: 0, maxShiftPx: 0,
    vertexShifts: [] as { index: number; shift: number }[], rejectedGeometry: false };
  const { width, height, data } = image;
  if (source.length !== 1404 || canonical.length !== source.length || data.length !== width * height * 4
    || width < 64 || height < 64 || indices.length % 3 !== 0) return result;
  const focal = height / (2 * Math.tan(VIRTUAL_CAMERA.verticalFovDegrees * Math.PI / 360));
  const xy: V2[] = [], eligible: boolean[] = [];
  for (let i = 0; i < 468; i++) {
    const x = source[3 * i]!, y = source[3 * i + 1]!, z = source[3 * i + 2]!;
    if (![x, y, z].every(Number.isFinite) || z >= -1) return result;
    xy.push([width / 2 + focal * x / -z, height / 2 - focal * y / -z]);
    eligible.push(Math.abs(canonical[3 * i]!) < 1.6 && canonical[3 * i + 1]! > -1.6
      && canonical[3 * i + 1]! <= canonical[168 * 3 + 1]! && canonical[3 * i + 2]! > 4);
  }
  const faceWidth = Math.max(...xy.map(p => p[0])) - Math.min(...xy.map(p => p[0]));
  if (faceWidth < 64) return result;
  const band = Math.max(6, Math.min(24, Math.round(faceWidth * .06)));
  const step = Math.max(2, Math.round(faceWidth / 140));
  const edgeMap = new Map<string, Edge>(), triangles: [number, number, number][] = [], areas: number[] = [];
  for (let i = 0; i < indices.length; i += 3) {
    const tri: [number, number, number] = [indices[i]!, indices[i + 1]!, indices[i + 2]!];
    if (tri.some(v => !Number.isInteger(v) || v < 0 || v >= 468)) return result;
    const n = triangles.length; triangles.push(tri); areas.push(cross(xy[tri[0]]!, xy[tri[1]]!, xy[tri[2]]!));
    for (let j = 0; j < 3; j++) {
      const a = tri[j]!, b = tri[(j + 1) % 3]!, key = a < b ? `${a}/${b}` : `${b}/${a}`;
      const e = edgeMap.get(key) ?? { a, b, triangles: [] }; e.triangles.push(n); edgeMap.set(key, e);
    }
  }
  const edges = [...edgeMap.values()];
  const silhouetteEdges = edges.filter(e => e.triangles.length === 2
    && areas[e.triangles[0]!]! * areas[e.triangles[1]!]! < 0);
  const nasalEdges = silhouetteEdges.filter(e => eligible[e.a] && eligible[e.b]);
  const sampleCache = new Map<number, { color: V2; light: number; spread: number }>();
  const sample = (x: number, y: number): { color: V2; light: number; spread: number } | null => {
    const cx = Math.round(x), cy = Math.round(y);
    if (cx < 1 || cx >= width - 1 || cy < 1 || cy >= height - 1) return null;
    const key = cy * width + cx, cached = sampleCache.get(key); if (cached) return cached;
    const colors: V2[] = []; let light = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const offset = ((cy + dy) * width + cx + dx) * 4;
      const r = data[offset]!, g = data[offset + 1]!, b = data[offset + 2]!;
      colors.push([(r - g) / Math.max(32, r + g), (b - g) / Math.max(32, b + g)]);
      light += (r + g + b) / 27;
    }
    const color: V2 = [colors.reduce((s, v) => s + v[0], 0) / 9, colors.reduce((s, v) => s + v[1], 0) / 9];
    const value = { color, light, spread: Math.sqrt(colors.reduce((s, v) => s + distance(v, color) ** 2, 0) / 9) };
    sampleCache.set(key, value); return value;
  };
  const connectedTrend = (start: number, end: number, y: number, separation: number): number | null => {
    const direction = Math.sign(end - start); if (!direction || Math.abs(end - start) < 6) return null;
    const colors: V2[] = [];
    for (let x = start; direction * (end - x) >= 0; x += direction * 3) {
      const point = sample(x, y); if (!point || point.light < 45) return null;
      const previous = colors.at(-1);
      if (previous && distance(previous, point.color) > Math.min(.035, .5 * separation)) return null;
      colors.push(point.color);
    }
    // A region may have a smooth chromatic trend; a second color boundary or
    // nonlinear eye/skin excursion along the connection is not supported.
    const count = colors.length, center = (count - 1) / 2;
    const mean: V2 = [colors.reduce((s, v) => s + v[0], 0) / count, colors.reduce((s, v) => s + v[1], 0) / count];
    const denominator = colors.reduce((s, _, i) => s + (i - center) ** 2, 0);
    if (denominator < 1) return null;
    const slope: V2 = [0, 0];
    colors.forEach((v, i) => { slope[0] += (i - center) * (v[0] - mean[0]) / denominator; slope[1] += (i - center) * (v[1] - mean[1]) / denominator; });
    const residual = Math.sqrt(colors.reduce((s, v, i) => s + distance(v, [mean[0] + (i - center) * slope[0], mean[1] + (i - center) * slope[1]]) ** 2, 0) / count);
    return residual <= .018 ? residual : null;
  };
  const nearestDepth = (x: number, y: number): number => {
    let closest = Infinity;
    for (let i = 0; i < triangles.length; i++) {
      const [a, b, c] = triangles[i]!, area = areas[i]!;
      if (Math.abs(area) < 1e-7) continue;
      const wa = cross([x, y], xy[b]!, xy[c]!) / area;
      const wb = cross(xy[a]!, [x, y], xy[c]!) / area, wc = 1 - wa - wb;
      if (Math.min(wa, wb, wc) < -1e-5) continue;
      closest = Math.min(closest, 1 / (wa / -source[3 * a + 2]! + wb / -source[3 * b + 2]! + wc / -source[3 * c + 2]!));
    }
    return closest;
  };
  for (const e of nasalEdges) {
    const p = xy[e.a]!, q = xy[e.b]!;
    if (Math.abs(q[1] - p[1]) < step) continue;
    const tri = triangles[e.triangles[0]!]!, other = tri.find(v => v !== e.a && v !== e.b)!;
    const v = xy[other]!, side = -Math.sign(v[0] - (p[0] + (v[1] - p[1]) * (q[0] - p[0]) / (q[1] - p[1])));
    if (!side) continue;
    for (let y = Math.ceil(Math.min(p[1], q[1]) / step) * step; y <= Math.max(p[1], q[1]); y += step) {
      const t = (y - p[1]) / (q[1] - p[1]), x = p[0] + t * (q[0] - p[0]);
      const d = 1 / ((1 - t) / -source[3 * e.a + 2]! + t / -source[3 * e.b + 2]!);
      if (d > nearestDepth(x, y) + .02) continue;
      const decision: BoundaryDecision = { y, x, side, a: e.a, b: e.b, t, target: null,
        shift: 0, score: 0, accepted: false, reason: 'no adjacent background evidence' };
      decisions.push(decision);
      // Full-mesh outline only locates a background seed. It is never the target.
      let extreme = x;
      for (const edge of edges) {
        const a = xy[edge.a]!, b = xy[edge.b]!;
        if ((a[1] > y) === (b[1] > y) || Math.abs(a[1] - b[1]) < 1e-6) continue;
        const xx = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]);
        if (side * (xx - extreme) > 0) extreme = xx;
      }
      if (side * (extreme - x) > band * 2) continue;
      const seedX = extreme + side * (band + 6), bg = sample(seedX, y), beyond = sample(seedX + side * 6, y);
      const fg = sample(x - side * (band + 4), y);
      if (!bg || !beyond || !fg || Math.min(bg.light, beyond.light, fg.light) < 45
        || distance(bg.color, beyond.color) > .025 || bg.spread > .015 || fg.spread > .025) continue;
      const anchorSeparation = distance(fg.color, bg.color);
      if (anchorSeparation < .05) continue;
      decision.reason = 'no reliable chromatic transition';
      let best: { x: number; score: number } | null = null;
      for (let offset = -band + 2; offset <= band - 2; offset++) {
        const candidate = x + offset;
        const inside = sample(candidate - side * 4, y), outside = sample(candidate + side * 4, y);
        const inner = sample(candidate - side * 8, y);
        if (!inside || !outside || !inner || Math.min(inside.light, outside.light, inner.light) < 45) continue;
        const separation = distance(inside.color, outside.color);
        if (separation < .045 || distance(inner.color, inside.color) > .035
          || inside.spread > .02 || outside.spread > .02) continue;
        // The candidate's outer side must connect chromatically to the external
        // seed, rather than merely ending at a pupil, skin shadow, or crease.
        const bgTrend = connectedTrend(candidate + side * 4, seedX, y, separation);
        const fgTrend = connectedTrend(candidate - side * 4, x - side * (band + 4), y, separation);
        if (bgTrend === null || fgTrend === null) continue;
        const score = separation - 2 * (bgTrend + fgTrend) - inside.spread - outside.spread;
        if (!best || score > best.score) best = { x: candidate, score };
      }
      if (!best) continue;
      // Treat two pixels as uncertainty in either direction. An edge already
      // within that range must not acquire an artificial inward bias.
      const delta = best.x - x;
      decision.shift = Math.sign(delta) * Math.max(0, Math.abs(delta) - 2);
      decision.target = x + decision.shift; decision.score = best.score;
      decision.reason = 'awaiting connected row support';
    }
  }
  const supported = decisions.filter(d => d.target !== null);
  for (const d of supported) {
    const neighbors = supported.filter(n => n.side === d.side && Math.abs(n.y - d.y) <= 2.1 * step
      && Math.abs(n.shift - d.shift) <= 3 && (n.a === d.a || n.a === d.b || n.b === d.a || n.b === d.b));
    if (neighbors.length >= 3) { d.accepted = true; d.reason = 'local color boundary connected to background'; }
  }
  const approved = supported.filter(d => d.accepted), support = new Map<number, { sum: number; max: number }>();
  for (const d of approved) for (const [i, w] of [[d.a, 1 - d.t], [d.b, d.t]] as const) {
    const s = support.get(i) ?? { sum: 0, max: 0 }; s.sum += w; s.max = Math.max(s.max, w); support.set(i, s);
  }
  const ids = [...support].filter(([, s]) => s.sum >= .75 && s.max >= .35).map(([i]) => i);
  if (!ids.length) return result;
  const n = ids.length, matrix = Array.from({ length: n }, (_, i) => Array.from({ length: n + 1 }, (_, j) => i === j ? .2 : 0));
  for (const d of approved) {
    const terms = [[ids.indexOf(d.a), 1 - d.t], [ids.indexOf(d.b), d.t]] as const;
    for (const [a, wa] of terms) if (a >= 0) {
      matrix[a]![n]! += wa * d.shift;
      for (const [b, wb] of terms) if (b >= 0) matrix[a]![b]! += wa * wb;
    }
  }
  // Small regularized endpoint fit. Unsupported vertices retain exact source XYZ.
  for (let i = 0; i < n; i++) {
    let pivot = i; for (let j = i + 1; j < n; j++) if (Math.abs(matrix[j]![i]!) > Math.abs(matrix[pivot]![i]!)) pivot = j;
    [matrix[i], matrix[pivot]] = [matrix[pivot]!, matrix[i]!];
    const divisor = matrix[i]![i]!; if (Math.abs(divisor) < 1e-8) return result;
    for (let j = i; j <= n; j++) matrix[i]![j]! /= divisor;
    for (let k = 0; k < n; k++) if (k !== i) {
      const factor = matrix[k]![i]!; for (let j = i; j <= n; j++) matrix[k]![j]! -= factor * matrix[i]![j]!;
    }
  }
  const shifts = matrix.map(row => row[n]!);
  const constraints: { a: number; b: number; wa: number; wb: number; low: number; high: number }[] = [];
  // Include original visible incident silhouettes even when their other endpoint
  // lies outside nasal eligibility. Fitting endpoints must not extrapolate large
  // motions into a rejected or unobserved row.
  for (const edge of silhouetteEdges) {
    const a = ids.indexOf(edge.a), b = ids.indexOf(edge.b); if (a < 0 && b < 0) continue;
    const p = xy[edge.a]!, q = xy[edge.b]!;
    if (Math.abs(q[1] - p[1]) < 1e-6) continue;
    for (let y = Math.ceil(Math.min(p[1], q[1])); y <= Math.max(p[1], q[1]); y++) {
      const t = (y - p[1]) / (q[1] - p[1]), x = p[0] + t * (q[0] - p[0]);
      const d = 1 / ((1 - t) / -source[3 * edge.a + 2]! + t / -source[3 * edge.b + 2]!);
      if (d > nearestDepth(x, y) + .02) continue;
      const supported = approved.filter(v => v.a === edge.a && v.b === edge.b && Math.abs(v.y - y) <= step / 2);
      const target = supported.length ? supported.reduce((s, v) => s + v.shift, 0) / supported.length : null;
      constraints.push({ a, b, wa: a < 0 ? 0 : 1 - t, wb: b < 0 ? 0 : t,
        low: target === null ? -2 : Math.min(0, target), high: target === null ? 2 : Math.max(0, target) });
    }
  }
  // Project onto row bounds; zero displacement is always feasible. This is a
  // deterministic bounded fit, not a search over desired rendered occlusion.
  for (let pass = 0; pass < 64; pass++) for (const c of constraints) {
    const value = (c.a < 0 ? 0 : shifts[c.a]! * c.wa) + (c.b < 0 ? 0 : shifts[c.b]! * c.wb);
    const error = value - Math.max(c.low, Math.min(c.high, value)), norm = c.wa ** 2 + c.wb ** 2;
    if (norm < 1e-8) continue;
    if (c.a >= 0) shifts[c.a]! -= error * c.wa / norm;
    if (c.b >= 0) shifts[c.b]! -= error * c.wb / norm;
  }
  if (constraints.some(c => {
    const value = (c.a < 0 ? 0 : shifts[c.a]! * c.wa) + (c.b < 0 ? 0 : shifts[c.b]! * c.wb);
    return value < c.low - .1 || value > c.high + .1;
  })) return { ...result, rejectedGeometry: true };
  for (let i = 0; i < n; i++) {
    const shift = shifts[i]!;
    if (!Number.isFinite(shift) || Math.abs(shift) > band) return { ...result, positions: source.slice(),
      changedVertices: 0, maxShiftPx: 0, vertexShifts: [], rejectedGeometry: true };
    if (Math.abs(shift) < .5) continue;
    const index = ids[i]!; positions[index * 3]! += shift * -source[index * 3 + 2]! / focal;
    result.vertexShifts.push({ index, shift }); result.changedVertices++; result.maxShiftPx = Math.max(result.maxShiftPx, Math.abs(shift));
  }
  // Validate what will actually reach the GPU, after small-shift suppression and
  // Float32 storage, which can differ from the fitted double-precision values.
  const finalShifts = ids.map(i => (positions[3 * i]! - source[3 * i]!) * focal / -source[3 * i + 2]!);
  if (constraints.some(c => {
    const value = (c.a < 0 ? 0 : finalShifts[c.a]! * c.wa) + (c.b < 0 ? 0 : finalShifts[c.b]! * c.wb);
    return value < c.low - .1 || value > c.high + .1;
  })) {
    positions.set(source); result.changedVertices = 0; result.maxShiftPx = 0;
    result.vertexShifts = []; result.rejectedGeometry = true; return result;
  }
  const normal = (buffer: Float32Array, tri: readonly number[]): [number, number, number] => {
    const [a, b, c] = tri as [number, number, number];
    const u = [0, 1, 2].map(k => buffer[3 * b + k]! - buffer[3 * a + k]!);
    const v = [0, 1, 2].map(k => buffer[3 * c + k]! - buffer[3 * a + k]!);
    return [u[1]! * v[2]! - u[2]! * v[1]!, u[2]! * v[0]! - u[0]! * v[2]!, u[0]! * v[1]! - u[1]! * v[0]!];
  };
  for (const tri of triangles) if (tri.some(i => ids.includes(i))) {
    const before = normal(source, tri), after = normal(positions, tri), oldSize = Math.hypot(...before), size = Math.hypot(...after);
    const dot = before.reduce((s, v, i) => s + v * after[i]!, 0) / (oldSize * size);
    if (!Number.isFinite(dot) || dot < .5 || size < .4 * oldSize) {
      positions.set(source); result.changedVertices = 0; result.maxShiftPx = 0; result.vertexShifts = []; result.rejectedGeometry = true; break;
    }
  }
  return result;
}
