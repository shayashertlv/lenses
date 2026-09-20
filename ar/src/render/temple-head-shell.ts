/** Join the face perimeter to the posterior head as one closed depth volume. This static shell is
 * expressed in canonical head centimetres; attach it directly to the rigid head pose at unit scale. */
import {Float32BufferAttribute, SphereGeometry, Vector3} from 'three';
import type {BufferGeometry} from 'three';
import {ConvexGeometry} from 'three/addons/geometries/ConvexGeometry.js';
import {TEMPLE_HEAD_VOLUME} from './temple-terminal-fit.ts';
import {TEMPLE_HEAD_FIT, templeHeadFitX} from './temple-head-fit.ts';

/** Mesh boundaries are edges used by one triangle. Choose the loop with the largest XY area,
 * so a mesh with eye/mouth holes still contributes only its exterior facial perimeter. */
export function canonicalFaceOuterBoundary(positions: ArrayLike<number>, indices: ArrayLike<number>): number[] {
  if (positions.length < 9 || positions.length % 3 !== 0 || indices.length < 3 || indices.length % 3 !== 0) {
    throw new Error('The head shell requires a canonical triangle mesh.');
  }
  for (let i = 0; i < positions.length; i++) if (!Number.isFinite(positions[i])) {
    throw new Error('The canonical head perimeter contains non-finite coordinates.');
  }
  const edges = new Map<string, {a: number; b: number; count: number}>();
  for (let triangle = 0; triangle < indices.length; triangle += 3) {
    for (let corner = 0; corner < 3; corner++) {
      const a = indices[triangle + corner]!, b = indices[triangle + (corner + 1) % 3]!;
      if (!Number.isInteger(a) || !Number.isInteger(b) || a < 0 || b < 0
        || a >= positions.length / 3 || b >= positions.length / 3 || a === b) {
        throw new Error('The canonical head perimeter contains invalid triangle indices.');
      }
      const key = a < b ? `${a},${b}` : `${b},${a}`, edge = edges.get(key);
      if (edge) {edge.count++; if (edge.count > 2) throw new Error('The canonical head surface is not manifold.');}
      else edges.set(key, {a, b, count: 1});
    }
  }
  const neighbours = new Map<number, number[]>();
  for (const {a, b, count} of edges.values()) if (count === 1) {
    neighbours.set(a, [...(neighbours.get(a) ?? []), b]);
    neighbours.set(b, [...(neighbours.get(b) ?? []), a]);
  }
  if (!neighbours.size || [...neighbours.values()].some(values => values.length !== 2)) {
    throw new Error('The canonical head surface has no closed exterior boundary.');
  }
  const remaining = new Set(neighbours.keys());
  let outer: number[] = [], largestArea = 0;
  while (remaining.size) {
    const start = remaining.values().next().value!, loop: number[] = [];
    let previous = -1, current = start;
    do {
      if (!remaining.delete(current)) throw new Error('The canonical head boundary loops overlap.');
      loop.push(current);
      const next = neighbours.get(current)!.find(index => index !== previous)!;
      previous = current; current = next;
    } while (current !== start);
    let twiceArea = 0;
    for (let i = 0; i < loop.length; i++) {
      const a = loop[i]! * 3, b = loop[(i + 1) % loop.length]! * 3;
      twiceArea += positions[a]! * positions[b + 1]! - positions[a + 1]! * positions[b]!;
    }
    if (Math.abs(twiceArea) > largestArea) {outer = loop; largestArea = Math.abs(twiceArea);}
  }
  if (outer.length < 3 || largestArea < 1e-8) throw new Error('The canonical head exterior boundary has no area.');
  return outer;
}

/** Only exterior facial vertices are included: a protruding nose or inner eye landmark must not
 * pull the posterior depth shell forward. The convex join closes the gap between the open observed
 * face and the old ellipsoid, through which a shaft could previously disappear and then reappear. */
export function createTempleHeadShell(positions: ArrayLike<number>, indices: ArrayLike<number>): BufferGeometry {
  const points = canonicalFaceOuterBoundary(positions, indices).map(index => new Vector3(
    positions[index * 3]!, positions[index * 3 + 1]!, positions[index * 3 + 2]!));
  const sphere = new SphereGeometry(1, 24, 16);
  try {
    const samples = sphere.getAttribute('position'), scale = TEMPLE_HEAD_VOLUME.scaleCm, center = TEMPLE_HEAD_VOLUME.centerCm;
    for (let i = 0; i < samples.count; i++) points.push(new Vector3(
      samples.getX(i) * scale[0] + center[0], samples.getY(i) * scale[1] + center[1], samples.getZ(i) * scale[2] + center[2]));
  } finally {sphere.dispose();}
  const geometry = new ConvexGeometry(points);
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}

/** Fit only the anterior shell. Split crossing facets first so the entire posterior volume,
 * including interpolated triangle interiors, remains unchanged. Shared edge intersections use
 * an ordered cache: duplicated triangle corners cannot introduce cracks through rounding. */
export function createTempleHeadShellFit(geometry: BufferGeometry): {set(ratio: number): void; reset(): void} {
  const source = geometry.getAttribute('position'), index = geometry.getIndex();
  type Point = readonly [number, number, number];
  let polygons: Point[][] = [];
  for (let i = 0; i < (index?.count ?? source.count); i += 3) polygons.push([0, 1, 2].map(c => {
    const vertex = index ? index.getX(i + c) : i + c;
    return [source.getX(vertex), source.getY(vertex), source.getZ(vertex)] as Point;
  }));
  for (const plane of [TEMPLE_HEAD_FIT.posteriorZCm, TEMPLE_HEAD_FIT.anteriorZCm]) {
    const cache = new Map<string, Point>(), next: Point[][] = [];
    const intersect = (a: Point, b: Point): Point => {
      if (a[2] === plane) return a; if (b[2] === plane) return b;
      const ka = a.join(','), kb = b.join(','), low = ka < kb ? a : b, high = ka < kb ? b : a;
      const key = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
      let point = cache.get(key);
      if (!point) {const t = (plane - low[2]) / (high[2] - low[2]);
        point = [Math.fround(low[0] + t * (high[0] - low[0])), Math.fround(low[1] + t * (high[1] - low[1])), plane];
        cache.set(key, point);}
      return point;
    };
    for (const polygon of polygons) {
      if (!polygon.some(p => p[2] < plane) || !polygon.some(p => p[2] > plane)) {next.push(polygon); continue;}
      for (const side of [-1, 1]) {
        const clipped: Point[] = [];
        const add = (p: Point): void => {if (!clipped.length || clipped.at(-1)!.some((v, i) => v !== p[i])) clipped.push(p);};
        for (let i = 0; i < polygon.length; i++) {
          const a = polygon[i]!, b = polygon[(i + 1) % polygon.length]!, insideA = side * (a[2] - plane) >= 0,
            insideB = side * (b[2] - plane) >= 0;
          if (insideA) add(a); if (insideA !== insideB) add(intersect(a, b));
        }
        if (clipped.length > 1 && clipped[0]!.every((v, i) => v === clipped.at(-1)![i])) clipped.pop();
        if (clipped.length >= 3) next.push(clipped);
      }
    }
    polygons = next;
  }
  const vertices: number[] = [];
  for (const polygon of polygons) for (let i = 1; i + 1 < polygon.length; i++) vertices.push(...polygon[0]!, ...polygon[i]!, ...polygon[i + 1]!);
  geometry.setIndex(null); geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3));
  geometry.deleteAttribute('normal'); geometry.computeVertexNormals(); geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  const canonical = geometry.getAttribute('position').array.slice(), positions = geometry.getAttribute('position');
  let applied = 1;
  const set = (ratio: number): void => {
    templeHeadFitX(0, 0, ratio); if (ratio === applied) return;
    for (let i = 0; i < positions.count; i++) positions.setX(i, templeHeadFitX(canonical[i * 3]!, canonical[i * 3 + 2]!, ratio));
    positions.needsUpdate = true; geometry.computeVertexNormals(); geometry.computeBoundingBox(); geometry.computeBoundingSphere(); applied = ratio;
  };
  return {set, reset: () => set(1)};
}
