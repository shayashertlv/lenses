/** Numerical overlap checks for authored front_sheet_v1 surfaces.
 * Each mesh must be a single-valued z(x,y) sheet; separate meshes may cross or
 * occupy different depths. Coplanar interfaces with positive overlapping area
 * have undefined multiplicity/order and are rejected. Shared edges/vertices,
 * disconnected regions and holes are permitted. This is not a closed-volume,
 * manifoldness, normal, UV or semantic-component certificate.
 *
 * Call after finite-position/index/front-winding validation, in the same baked
 * coordinate system for every mesh. An XY BVH avoids exhaustive triangle pairs.
 */
import type {Mesh} from 'three';

type Point = readonly [number, number, number];
type Point2 = readonly [number, number];
interface Bounds {minX: number; minY: number; maxX: number; maxY: number}
interface Triangle extends Bounds {
  points: readonly [Point, Point, Point];
  normal: Point;
  spatialScale: number;
  coordinateMagnitude: number;
  mesh: number;
  index: number;
}
interface Node extends Bounds {
  count: number;
  triangles?: readonly Triangle[];
  left?: Node;
  right?: Node;
}

function bounds(items: readonly Bounds[]): Bounds {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const item of items) {
    minX = Math.min(minX, item.minX); minY = Math.min(minY, item.minY);
    maxX = Math.max(maxX, item.maxX); maxY = Math.max(maxY, item.maxY);
  }
  return {minX, minY, maxX, maxY};
}

function build(triangles: Triangle[]): Node {
  const box = bounds(triangles), count = triangles.length;
  if (count <= 8) return {...box, count, triangles};
  const xAxis = box.maxX - box.minX >= box.maxY - box.minY;
  triangles.sort((a, b) => xAxis ? a.minX + a.maxX - b.minX - b.maxX : a.minY + a.maxY - b.minY - b.maxY);
  const middle = Math.floor(count / 2);
  return {...box, count, left: build(triangles.slice(0, middle)), right: build(triangles.slice(middle))};
}

function overlaps(a: Bounds, b: Bounds): boolean {
  return Math.min(a.maxX, b.maxX) > Math.max(a.minX, b.minX)
    && Math.min(a.maxY, b.maxY) > Math.max(a.minY, b.minY);
}

function signedDistance(a: Point2 | Point, b: Point2 | Point, p: Point2 | Point): number {
  return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
}

/** Clip CCW triangle A against B, then measure translated shoelace area. */
function intersectionArea(a: Triangle, b: Triangle): number {
  let polygon: Point2[] = a.points.map(point => [point[0], point[1]]);
  for (let edge = 0; edge < 3 && polygon.length; edge++) {
    const start = b.points[edge]!, end = b.points[(edge + 1) % 3]!;
    const next: Point2[] = [];
    let previous = polygon[polygon.length - 1]!, previousDistance = signedDistance(start, end, previous);
    for (const current of polygon) {
      const distance = signedDistance(start, end, current);
      if ((distance >= 0) !== (previousDistance >= 0)) {
        const t = previousDistance / (previousDistance - distance);
        next.push([previous[0] + t * (current[0] - previous[0]), previous[1] + t * (current[1] - previous[1])]);
      }
      if (distance >= 0) next.push(current);
      previous = current; previousDistance = distance;
    }
    polygon = next;
  }
  if (polygon.length < 3) return 0;
  const origin = polygon[0]!;
  let twiceArea = 0;
  for (let i = 1; i + 1 < polygon.length; i++) twiceArea += signedDistance(origin, polygon[i]!, polygon[i + 1]!);
  return Math.abs(twiceArea) * .5;
}

function samePlane(a: Triangle, b: Triangle, tolerance: number): boolean {
  const onPlane = (points: readonly Point[], plane: Triangle): boolean => points.every(point => {
    const origin = plane.points[0], normal = plane.normal;
    return Math.abs(normal[0] * (point[0] - origin[0]) + normal[1] * (point[1] - origin[1])
      + normal[2] * (point[2] - origin[2])) <= tolerance;
  });
  return onPlane(a.points, b) && onPlane(b.points, a);
}

function pairs(a: Node, b: Node, visit: (a: Triangle, b: Triangle) => void): void {
  if (!overlaps(a, b)) return;
  if (a.triangles && b.triangles) {
    for (const first of a.triangles) for (const second of b.triangles) if (overlaps(first, second)) visit(first, second);
  } else if (!a.triangles && (b.triangles || a.count >= b.count)) {
    pairs(a.left!, b, visit); pairs(a.right!, b, visit);
  } else {
    pairs(a, b.left!, visit); pairs(a, b.right!, visit);
  }
}

function within(node: Node, visit: (a: Triangle, b: Triangle) => void): void {
  if (node.triangles) {
    for (let i = 0; i < node.triangles.length; i++) for (let j = 0; j < i; j++) {
      const a = node.triangles[i]!, b = node.triangles[j]!;
      if (overlaps(a, b)) visit(a, b);
    }
  } else {
    within(node.left!, visit); within(node.right!, visit); pairs(node.left!, node.right!, visit);
  }
}

export function validateCanonicalOpticalTopology(meshes: readonly Mesh[]): void {
  const collections: Triangle[][] = [];
  for (let mesh = 0; mesh < meshes.length; mesh++) {
    const geometry = meshes[mesh]!.geometry, position = geometry.getAttribute('position'), indices = geometry.getIndex();
    const count = indices?.count ?? position?.count ?? 0;
    if (!position || count < 3 || count % 3) throw new Error('Canonical optical topology requires complete validated triangles.');
    const triangles: Triangle[] = [];
    for (let offset = 0; offset < count; offset += 3) {
      const points = [0, 1, 2].map(corner => {
        const index = indices?.getX(offset + corner) ?? offset + corner;
        return [position.getX(index), position.getY(index), position.getZ(index)] as Point;
      }) as [Point, Point, Point];
      const [a, b, c] = points, u = b.map((v, i) => v - a[i]!), v = c.map((value, i) => value - a[i]!);
      const cross: Point = [u[1]! * v[2]! - u[2]! * v[1]!, u[2]! * v[0]! - u[0]! * v[2]!, u[0]! * v[1]! - u[1]! * v[0]!];
      const length = Math.hypot(...cross);
      if (!(cross[2] > 0) || !Number.isFinite(length)) throw new Error('Canonical optical topology requires finite +Z-wound triangles.');
      triangles.push({points, normal: [cross[0] / length, cross[1] / length, cross[2] / length], mesh, index: offset / 3,
        spatialScale: Math.max(...[0, 1, 2].map(axis => Math.max(a[axis]!, b[axis]!, c[axis]!) - Math.min(a[axis]!, b[axis]!, c[axis]!))),
        coordinateMagnitude: Math.max(...points.flatMap(point => point.map(Math.abs))),
        minX: Math.min(a[0], b[0], c[0]), minY: Math.min(a[1], b[1], c[1]),
        maxX: Math.max(a[0], b[0], c[0]), maxY: Math.max(a[1], b[1], c[1])});
    }
    collections.push(triangles);
  }
  if (!collections.length) return;
  const trees = collections.map(build);
  // The area threshold excludes arithmetic slivers along shared edges. Plane
  // tolerance allows float32-authored copies to agree; it does not merge layers.
  const check = (a: Triangle, b: Triangle): void => {
    const xyScale = Math.max(a.maxX - a.minX, a.maxY - a.minY, b.maxX - b.minX, b.maxY - b.minY);
    const areaTolerance = xyScale * xyScale * 1e-12;
    const planeTolerance = Math.max(Math.max(a.spatialScale, b.spatialScale) * 1e-7,
      Math.max(a.coordinateMagnitude, b.coordinateMagnitude) * Number.EPSILON * 64);
    if (a.mesh !== b.mesh && !samePlane(a, b, planeTolerance)) return;
    if (intersectionArea(a, b) <= areaTolerance) return;
    const where = `mesh ${a.mesh} triangle ${a.index}, mesh ${b.mesh} triangle ${b.index}`;
    if (a.mesh === b.mesh) throw new Error(`Canonical optical sheet is not single-valued z(x,y): positive-area XY triangle overlap (${where}).`);
    throw new Error(`Canonical optical sheets have coincident coplanar patches with undefined layer order (${where}).`);
  };
  for (let i = 0; i < trees.length; i++) {
    within(trees[i]!, check);
    for (let j = 0; j < i; j++) pairs(trees[i]!, trees[j]!, check);
  }
}
