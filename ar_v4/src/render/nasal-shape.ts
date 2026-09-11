import { Matrix4, Vector3 } from 'three';

/** Fixed preview assumptions, not measured nose dimensions or a personal fit. */
export const NASAL_SHAPE_ID = 'central-wp020-dp015';
export const NASAL_SHAPE_VERSION = 'raw-nasal-shape-v1';
const WIDTH_GAIN = .2;
const FORWARD_DEPTH_CM = .15;
const MIDLINE_IDS = [168, 6, 197, 195, 5, 4, 1, 19, 94, 2] as const;
const FOOTPRINT = Object.freeze({ xFull: .45, xZero: 1.25, yLowerZero: -2.1, yLowerFull: -1.25,
  yUpperFull: 2.45, yUpperZero: 3.4, zZero: 4.4, zFull: 5.5 });
const GEOMETRY_GUARDS = Object.freeze({ minNormalDot: .5, minAreaRatio: .4,
  maxLocalDisplacementCm: .9, maxCameraDisplacementCm: 1.25, minCameraDepthCm: 1 });

export interface NasalShapeInput {
  readonly surfacePositions: ArrayLike<number>;
  /** Original detector pose. The corrected eyewear pose must not shape the face. */
  readonly rawMatrix: readonly number[];
}
export interface NasalShapeDiagnostics {
  readonly status: 'accepted' | 'rejected';
  readonly rejectionReasons: readonly string[];
  readonly footprintVertexCount: number;
  readonly proposedChangedVertexCount: number;
  readonly outputChangedVertexCount: number;
  readonly outputChangedVertexIndices: readonly number[];
  readonly maxLocalWidthDeltaCm: number;
  readonly maxLocalDepthDeltaCm: number;
  readonly maxLocalDisplacementCm: number;
  readonly maxCameraDisplacementCm: number;
  readonly minNormalDot: number;
  readonly minAreaRatio: number;
  readonly worstNormalTriangle: number | null;
  readonly worstAreaTriangle: number | null;
  readonly checkedTriangleCount: number;
}
export interface NasalShapeResult {
  readonly surfacePositions: Float32Array;
  readonly accepted: boolean;
  readonly diagnostics: NasalShapeDiagnostics;
}
export interface NasalShape {
  apply(input: NasalShapeInput): NasalShapeResult;
}

const smooth = (value: number): number => {
  const t = Math.max(0, Math.min(1, value)); return t * t * (3 - 2 * t);
};
const weightFor = (x: number, y: number, z: number): number =>
  smooth((FOOTPRINT.xZero - Math.abs(x)) / (FOOTPRINT.xZero - FOOTPRINT.xFull))
  * smooth((y - FOOTPRINT.yLowerZero) / (FOOTPRINT.yLowerFull - FOOTPRINT.yLowerZero))
  * smooth((FOOTPRINT.yUpperZero - y) / (FOOTPRINT.yUpperZero - FOOTPRINT.yUpperFull))
  * smooth((z - FOOTPRINT.zZero) / (FOOTPRINT.zFull - FOOTPRINT.zZero));

/**
 * Applies the fixed Option17 shape directly to this pair's raw observed surface.
 * No pixels, RGB refinement, yaw switch, eyewear geometry or prior frame is used.
 * Local width/depth deltas and Float32 operation order preserve the reviewed
 * central-wp020-dp015 generator. Its original shape guards reject as a whole to
 * an owned exact raw copy; they do not establish anatomical accuracy.
 */
export function createNasalShape(canonicalPositions: readonly number[], canonicalIndices: readonly number[]): NasalShape {
  if (canonicalPositions.length !== 1404 || !canonicalPositions.every(Number.isFinite)) throw new Error('Expected finite 468-point canonical face');
  if (!canonicalIndices.length || canonicalIndices.length % 3 !== 0
    || !canonicalIndices.every(id => Number.isInteger(id) && id >= 0 && id < 468)) throw new Error('Invalid canonical triangle topology');
  const canonical = [...canonicalPositions], indices = [...canonicalIndices];
  const weights = Array.from({ length: 468 }, (_, i) => weightFor(canonical[i * 3]!, canonical[i * 3 + 1]!, canonical[i * 3 + 2]!));
  const vertexIndices = weights.flatMap((weight, i) => weight > 0 ? [i] : []);
  const midline = MIDLINE_IDS.map(id => ({ id, y: canonical[id * 3 + 1]! })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < midline.length; i++) if (!(midline[i]!.y > midline[i - 1]!.y)) throw new Error('Invalid central nose ordinate ordering');

  return Object.freeze({ apply(input: NasalShapeInput): NasalShapeResult {
    if (input.surfacePositions.length !== 1404) throw new Error('Expected exact paired 468-point surface');
    const source = Float32Array.from(input.surfacePositions);
    for (let i = 0; i < source.length; i++) {
      if (!Number.isFinite(input.surfacePositions[i]) || source[i] !== input.surfacePositions[i]
        || (i % 3 === 2 && source[i]! >= -GEOMETRY_GUARDS.minCameraDepthCm)) throw new Error('Surface must contain finite original Float32 camera-space coordinates in front of near plane');
    }
    const raw = input.rawMatrix;
    if (raw.length !== 16 || !raw.every(Number.isFinite) || Math.abs(raw[3]!) > 1e-6 || Math.abs(raw[7]!) > 1e-6
      || Math.abs(raw[11]!) > 1e-6 || Math.abs(raw[15]! - 1) > 1e-6) throw new Error('Expected original finite affine detector pose');
    const pose = new Matrix4().fromArray(raw), determinant = pose.determinant();
    if (!Number.isFinite(determinant) || determinant < 1e-6) throw new Error('Detector pose is singular or reflected');
    const inverse = pose.clone().invert();
    if (!inverse.elements.every(Number.isFinite)) throw new Error('Detector pose inverse is invalid');
    const output = source.slice(), current = new Vector3();
    const midlineX = midline.map(({ id }) => current.fromArray(source, id * 3).applyMatrix4(inverse).x);
    const centerX = (y: number): number => {
      if (y <= midline[0]!.y) return midlineX[0]!;
      for (let i = 1; i < midline.length; i++) if (y <= midline[i]!.y) {
        const t = (y - midline[i - 1]!.y) / (midline[i]!.y - midline[i - 1]!.y);
        return midlineX[i - 1]! * (1 - t) + midlineX[i]! * t;
      }
      return midlineX.at(-1)!;
    };
    let maxLocalWidthDeltaCm = 0, maxLocalDepthDeltaCm = 0, maxLocalDisplacementCm = 0, maxCameraDisplacementCm = 0;
    const changed: number[] = [], changedSet = new Set<number>(), reasons = new Set<string>();
    const m = pose.elements;
    for (const i of vertexIndices) {
      const w = weights[i]!, offset = i * 3;
      current.fromArray(source, offset).applyMatrix4(inverse);
      const dx = (current.x - centerX(canonical[offset + 1]!)) * WIDTH_GAIN * w;
      const dz = FORWARD_DEPTH_CM * w;
      const localDisplacement = Math.hypot(dx, dz);
      maxLocalWidthDeltaCm = Math.max(maxLocalWidthDeltaCm, Math.abs(dx));
      maxLocalDepthDeltaCm = Math.max(maxLocalDepthDeltaCm, Math.abs(dz));
      maxLocalDisplacementCm = Math.max(maxLocalDisplacementCm, localDisplacement);
      // Add only the transformed delta to original coordinates: no round-trip drift.
      output[offset] = source[offset]! + m[0]! * dx + m[8]! * dz;
      output[offset + 1] = source[offset + 1]! + m[1]! * dx + m[9]! * dz;
      output[offset + 2] = source[offset + 2]! + m[2]! * dx + m[10]! * dz;
      const cameraDisplacement = Math.hypot(output[offset]! - source[offset]!, output[offset + 1]! - source[offset + 1]!, output[offset + 2]! - source[offset + 2]!);
      maxCameraDisplacementCm = Math.max(maxCameraDisplacementCm, cameraDisplacement);
      if (cameraDisplacement > 0) { changed.push(i); changedSet.add(i); }
      if (![dx, dz, output[offset], output[offset + 1], output[offset + 2]].every(Number.isFinite)) reasons.add('non-finite-proposal');
      if (output[offset + 2]! >= -GEOMETRY_GUARDS.minCameraDepthCm) reasons.add('near-plane');
    }
    if (maxLocalDisplacementCm > GEOMETRY_GUARDS.maxLocalDisplacementCm) reasons.add('local-displacement-limit');
    if (maxCameraDisplacementCm > GEOMETRY_GUARDS.maxCameraDisplacementCm) reasons.add('camera-displacement-limit');
    let minNormalDot = 1, minAreaRatio = 1, worstNormalTriangle: number | null = null, worstAreaTriangle: number | null = null, checkedTriangleCount = 0;
    const a = new Vector3(), b = new Vector3(), c = new Vector3(), originalNormal = new Vector3(), proposedNormal = new Vector3();
    for (let offset = 0; offset < indices.length; offset += 3) {
      const ia = indices[offset]!, ib = indices[offset + 1]!, ic = indices[offset + 2]!;
      if (![ia, ib, ic].some(id => changedSet.has(id))) continue;
      a.fromArray(source, ia * 3); b.fromArray(source, ib * 3).sub(a); c.fromArray(source, ic * 3).sub(a);
      originalNormal.crossVectors(b, c); const originalArea = originalNormal.length();
      if (originalArea < 1e-12) continue; // Existing degeneracy supplies no reliable normal, as in the reviewed generator.
      a.fromArray(output, ia * 3); b.fromArray(output, ib * 3).sub(a); c.fromArray(output, ic * 3).sub(a);
      proposedNormal.crossVectors(b, c); const proposedArea = proposedNormal.length();
      const normalDot = proposedArea > 0 ? originalNormal.dot(proposedNormal) / (originalArea * proposedArea) : -1;
      const areaRatio = proposedArea / originalArea;
      checkedTriangleCount++;
      if (normalDot < minNormalDot) { minNormalDot = normalDot; worstNormalTriangle = offset / 3; }
      if (areaRatio < minAreaRatio) { minAreaRatio = areaRatio; worstAreaTriangle = offset / 3; }
      if (!Number.isFinite(normalDot) || normalDot < GEOMETRY_GUARDS.minNormalDot) reasons.add('triangle-normal');
      if (!Number.isFinite(areaRatio) || areaRatio < GEOMETRY_GUARDS.minAreaRatio) reasons.add('triangle-area');
    }
    const accepted = reasons.size === 0;
    return { surfacePositions: accepted ? output : source, accepted, diagnostics: {
      status: accepted ? 'accepted' : 'rejected', rejectionReasons: [...reasons], footprintVertexCount: vertexIndices.length,
      proposedChangedVertexCount: changed.length, outputChangedVertexCount: accepted ? changed.length : 0,
      outputChangedVertexIndices: accepted ? changed : [], maxLocalWidthDeltaCm, maxLocalDepthDeltaCm,
      maxLocalDisplacementCm, maxCameraDisplacementCm, minNormalDot, minAreaRatio, worstNormalTriangle, worstAreaTriangle, checkedTriangleCount,
    } };
  } });
}
