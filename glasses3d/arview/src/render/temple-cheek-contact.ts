import type {Landmark} from '../face/protocol.ts';
import {canonicalFaceOuterBoundary} from './temple-head-shell.ts';

export const MAX_TEMPLE_CHEEK_VERTICES = 64;
export interface TempleCheekPoint {readonly x: number; readonly y: number;}
export interface TempleCheekContact {
  /** Normalized image coordinates, origin at the top left. Vertices may lie outside a cropped image. */
  readonly polygon: readonly TempleCheekPoint[];
}

const finite = (...values: number[]): boolean => values.every(Number.isFinite);
const cross = (a: TempleCheekPoint, b: TempleCheekPoint, c: TempleCheekPoint): number =>
  (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
const near = (a: TempleCheekPoint, b: TempleCheekPoint): boolean =>
  Math.abs(a.x - b.x) <= 1e-9 && Math.abs(a.y - b.y) <= 1e-9;

function squaredSegmentDistance(p: TempleCheekPoint, a: TempleCheekPoint, b: TempleCheekPoint): number {
  const dx = b.x - a.x, dy = b.y - a.y, lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq));
  return (p.x - a.x - t * dx) ** 2 + (p.y - a.y - t * dy) ** 2;
}

function segmentsMeet(a: TempleCheekPoint, b: TempleCheekPoint, c: TempleCheekPoint, d: TempleCheekPoint): boolean {
  const abC = cross(a, b, c), abD = cross(a, b, d), cdA = cross(c, d, a), cdB = cross(c, d, b);
  if (abC * abD < 0 && cdA * cdB < 0) return true;
  return Math.abs(abC) < 1e-12 && squaredSegmentDistance(c, a, b) < 1e-18
    || Math.abs(abD) < 1e-12 && squaredSegmentDistance(d, a, b) < 1e-18
    || Math.abs(cdA) < 1e-12 && squaredSegmentDistance(a, c, d) < 1e-18
    || Math.abs(cdB) < 1e-12 && squaredSegmentDistance(b, c, d) < 1e-18;
}

/** Reject collapsed and self-crossing outlines rather than manufacturing a foreground region. */
function simplePolygon(polygon: readonly TempleCheekPoint[]): boolean {
  if (polygon.length < 3 || polygon.length > MAX_TEMPLE_CHEEK_VERTICES) return false;
  let twiceArea = 0;
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i]!, b = polygon[(i + 1) % polygon.length]!;
    if (!finite(a.x, a.y) || near(a, b)) return false;
    twiceArea += a.x * b.y - a.y * b.x;
    for (let j = i + 2; j < polygon.length; j++) {
      if (i === 0 && j === polygon.length - 1) continue;
      if (segmentsMeet(a, b, polygon[j]!, polygon[(j + 1) % polygon.length]!)) return false;
    }
  }
  return Number.isFinite(twiceArea) && Math.abs(twiceArea) > 1e-8;
}

/** Canonical topology supplies the exterior contour; observed XY supplies both its coverage and
 * the outer-canthus line that clips off the upper face. The chin selects the lower half-plane,
 * including rolled or mirrored views. A canonical-Y slice would project too low when its lateral
 * endpoints have a different depth from the observed eyes. This is only an eligible region: actual
 * observed-face depth must be in front of a fragment before it may be occluded. There is no shaft
 * cutoff, pose/history state, or eyewear deformation here. */
export class TempleCheekContactEstimator {
  private readonly boundary: readonly number[] | null;

  constructor(canonicalPositions: ArrayLike<number>, canonicalIndices: ArrayLike<number>) {
    let boundary: number[] | null = null;
    try {
      boundary = canonicalFaceOuterBoundary(canonicalPositions, canonicalIndices);
      if (boundary.length < 3 || boundary.length > MAX_TEMPLE_CHEEK_VERTICES) boundary = null;
    } catch {boundary = null;}
    this.boundary = boundary;
  }

  evaluate(landmarks: readonly Landmark[]): TempleCheekContact | null {
    if (!this.boundary) return null;
    const eyeA = landmarks[33], eyeB = landmarks[263], chin = landmarks[152];
    if (!eyeA || !eyeB || !chin || !finite(eyeA.x, eyeA.y, eyeB.x, eyeB.y, chin.x, chin.y)) return null;
    const chinSide = cross(eyeA, eyeB, chin);
    if (!Number.isFinite(chinSide) || Math.abs(chinSide) <= 1e-9) return null;
    const outline: TempleCheekPoint[] = [];
    for (const index of this.boundary) {
      const p = landmarks[index];
      if (!p || !finite(p.x, p.y)) return null;
      if (!outline.length || !near(p, outline.at(-1)!)) outline.push({x: p.x, y: p.y});
    }
    if (outline.length > 1 && near(outline[0]!, outline.at(-1)!)) outline.pop();
    if (!simplePolygon(outline)) return null;
    const polygon: TempleCheekPoint[] = [];
    const add = (p: TempleCheekPoint): void => {if (!polygon.length || !near(p, polygon.at(-1)!)) polygon.push(p);};
    for (let i = 0; i < outline.length; i++) {
      const a = outline[i]!, b = outline[(i + 1) % outline.length]!;
      const da = Math.sign(chinSide) * cross(eyeA, eyeB, a), db = Math.sign(chinSide) * cross(eyeA, eyeB, b);
      const insideA = da >= 0, insideB = db >= 0;
      if (insideA) add(a);
      if (insideA !== insideB) {const t = da / (da - db); add({x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y)});}
    }
    if (polygon.length > 1 && near(polygon[0]!, polygon.at(-1)!)) polygon.pop();
    if (!simplePolygon(polygon)) return null;
    return {polygon};
  }
}
