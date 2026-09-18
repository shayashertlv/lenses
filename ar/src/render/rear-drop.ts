/** Pose-driven rear drop: an authored preview shear that lowers the posterior opaque arm shafts when the head pitches
 *  down and faces the camera. Not a mechanical hinge or a measured wearer fit. Owns cloned geometry only; original
 *  buffers and material hooks stay untouched.
 *
 *  It also owns the experimental width fit's lateral arm spread (face-width.ts), because both deformations write the
 *  same cloned buffers: every change restores the original storage first, so they are applied together in one pass and
 *  neither can overwrite the other. Spread 0 leaves the drop exactly as it was before the fit existed. */
import {
  Box3, BufferAttribute, InterleavedBufferAttribute, MathUtils, Matrix4, Mesh,
  MeshPhysicalMaterial, Vector3,
} from 'three';
import type {BufferGeometry, Material, Object3D} from 'three';
import {TEMPLE_BLEND_LENGTH_LOCAL_M} from './temple-clip.ts';
import {
  armSpreadSlope, MAX_ARM_SPREAD_M, spreadArmX, SPREAD_HINGE_ROUND_M, SPREAD_PIVOT_RANGE_M, WIDTH_FIT_METHOD,
} from './face-width.ts';

export const REAR_DROP_METHOD = 'temple-rear-drop-v1';
export interface RearDropConfiguration {
  readonly method: typeof REAR_DROP_METHOD;
  readonly dropM: number;
}
export function validateRearDrop(value: RearDropConfiguration): void {
  if (!value || value.method !== REAR_DROP_METHOD || !Number.isFinite(value.dropM) || value.dropM < 0 || value.dropM > .03) {
    throw new Error('The rear-drop configuration is invalid.');
  }
}

export const REAR_DROP_PARAMETERS = Object.freeze({
  maximumDropM: .03, defaultDropM: .02, proximalGuardM: .015, lateralMinM: .045,
  pitchStartDegrees: 8, pitchFullDegrees: 28,
  headYawStartDegrees: 10, headYawEndDegrees: 25,
  viewBearingStartDegrees: 20, viewBearingEndDegrees: 40,
});

type Attribute = BufferAttribute | InterleavedBufferAttribute;
interface GeometryRecord {
  original: BufferGeometry;
  clone: BufferGeometry;
  referenced: Uint8Array;
  fixed: Uint8Array;
  opaque: Uint8Array;
  lens: Uint8Array;
}
interface MeshRecord {mesh: Mesh; geometry: GeometryRecord; toRoot: Matrix4}

function validateDrop(dropM: number): void {
  if (!Number.isFinite(dropM) || dropM < 0 || dropM > REAR_DROP_PARAMETERS.maximumDropM) {
    throw new Error('Rear drop must be finite and between 0 and 0.03 meters.');
  }
}
/** The lateral arm spread in metres per arm — the width fit's and the manual bend's together; 0 is the original
 *  geometry. */
function validateSpread(spreadM: number): void {
  if (!Number.isFinite(spreadM) || Math.abs(spreadM) > MAX_ARM_SPREAD_M) {
    throw new Error(`The arm spread must be finite and within ±${MAX_ARM_SPREAD_M} meters.`);
  }
}

/** Where the frame front ends and the temple shaft begins, in root-local metres. Walking back from the front, the
 *  first 1 mm slice of the band the spread may move (|x| > lateralMinM) whose vertical extent has collapsed to a bar —
 *  rims and endpieces are tall there, a shaft is not — and which stays collapsed for the next 20 mm, so the sliver of
 *  rim that grazes the band at the very front cannot be mistaken for a shaft. Measured on the shipped assets it lands
 *  at z -0.014 (Amber Horizon, lens rear -0.0107) and -0.013 (Tom Ford, lens rear -0.0144): a millimetre or two behind
 *  the endpiece, which is where a hinge is. Null when the asset has no such boundary; the caller then keeps the rear
 *  drop's own start plane and the bend pivots there, as it did before 2026-09-18. */
export const HINGE_SLICE_M = 0.001, HINGE_SHAFT_MAX_HEIGHT_M = 0.015;
export const HINGE_SHAFT_RUN_SLICES = 20, HINGE_SHAFT_RUN_MIN_SLICES = 5, HINGE_MIN_SPAN_M = 0.05;
export function armShaftStartZM(extents: ReadonlyMap<number, {readonly low: number; readonly high: number}>): number | null {
  const height = (slice: number): number | null => {
    const extent = extents.get(slice);
    return extent ? extent.high - extent.low : null;
  };
  for (const slice of [...extents.keys()].sort((a, b) => b - a)) {
    if ((height(slice) ?? Infinity) > HINGE_SHAFT_MAX_HEIGHT_M) continue;
    // An empty slice is no evidence either way — a coarsely tessellated shaft has gaps — but a tall one ends the run.
    let populated = 1, tall = false;
    for (let i = 1; i <= HINGE_SHAFT_RUN_SLICES && !tall; i++) {
      const next = height(slice - i);
      if (next === null) continue;
      if (next > HINGE_SHAFT_MAX_HEIGHT_M) tall = true; else populated++;
    }
    if (!tall && populated >= HINGE_SHAFT_RUN_MIN_SLICES) return slice * HINGE_SLICE_M;
  }
  return null;
}

/** Authored preview shear, not a mechanical hinge or measured wearer fit. */
export function rearDropCurve(z: number, startZM: number, cutoffZM: number, dropM: number) {
  validateDrop(dropM);
  if (![z, startZM, cutoffZM].every(Number.isFinite) || startZM <= cutoffZM) {
    throw new Error('The rear-drop span is invalid.');
  }
  const u = MathUtils.clamp((startZM - z) / (startZM - cutoffZM), 0, 1);
  return {
    loweringM: dropM * u * u * (3 - 2 * u),
    // J maps a tangent to (tx, ty + dyDz*tz, tz).
    dyDz: dropM * 6 * u * (1 - u) / (startZM - cutoffZM),
  };
}

/** Head and camera must agree on down; values stored by a caller are actual drop. */
export function rearDropForPose(rawMatrix: readonly number[], maxDropM: number = REAR_DROP_PARAMETERS.defaultDropM): number {
  validateDrop(maxDropM);
  if (rawMatrix.length !== 16 || !rawMatrix.every(Number.isFinite)) throw new Error('The rear-drop pose is invalid.');
  const matrix = new Matrix4().fromArray(rawMatrix);
  if (Math.abs(matrix.determinant()) < 1e-12) throw new Error('The rear-drop pose is singular.');
  const cameraLocal = new Vector3().applyMatrix4(matrix.clone().invert());
  const cameraLength = cameraLocal.length();
  const forwardLength = Math.hypot(rawMatrix[8]!, rawMatrix[9]!, rawMatrix[10]!);
  const horizontalForwardLength = Math.hypot(rawMatrix[8]!, rawMatrix[10]!);
  if (cameraLength < 1e-12 || forwardLength < 1e-12) throw new Error('The rear-drop viewing direction is invalid.');
  const sin = (degrees: number) => Math.sin(MathUtils.degToRad(degrees));
  const headDown = -rawMatrix[9]! / forwardLength;
  const viewDown = cameraLocal.y / cameraLength;
  const down = MathUtils.smoothstep(Math.min(headDown, viewDown),
    sin(REAR_DROP_PARAMETERS.pitchStartDegrees), sin(REAR_DROP_PARAMETERS.pitchFullDegrees));
  const headLateral = horizontalForwardLength > 1e-12 ? Math.abs(rawMatrix[8]!) / horizontalForwardLength : 1;
  const frontalHead = 1 - MathUtils.smoothstep(headLateral,
    sin(REAR_DROP_PARAMETERS.headYawStartDegrees), sin(REAR_DROP_PARAMETERS.headYawEndDegrees));
  const frontalView = 1 - MathUtils.smoothstep(Math.abs(cameraLocal.x) / cameraLength,
    sin(REAR_DROP_PARAMETERS.viewBearingStartDegrees), sin(REAR_DROP_PARAMETERS.viewBearingEndDegrees));
  return maxDropM * down * frontalHead * frontalView;
}

function restoreAttribute(destination: Attribute, source: Attribute): void {
  if (destination instanceof InterleavedBufferAttribute && source instanceof InterleavedBufferAttribute) {
    destination.data.array.set(source.data.array);
    destination.data.needsUpdate = true;
  } else if (destination instanceof BufferAttribute && source instanceof BufferAttribute) {
    destination.array.set(source.array);
    destination.needsUpdate = true;
  } else throw new Error('The rear-drop geometry clone has incompatible attributes.');
}

const isLens = (material: Material): boolean => material instanceof MeshPhysicalMaterial && material.transmission > 0;

/**
 * Install on loaded assets before visibility creates its sharing overlays.
 * Owns cloned geometry only. Original buffers and material hooks remain untouched.
 * Bounds are root-local; the caller applies its asset/pose projection externally.
 */
export function createRearDrop(root: Object3D, cutoffZM: number, spreadPivotM = 0) {
  if (!Number.isFinite(cutoffZM) || cutoffZM < -.2 || cutoffZM > -.03) throw new Error('The rear-drop endpoint is invalid.');
  if (!Number.isFinite(spreadPivotM) || spreadPivotM < SPREAD_PIVOT_RANGE_M.min || spreadPivotM > SPREAD_PIVOT_RANGE_M.max) {
    throw new Error('The arm-spread pivot is out of range.');
  }
  const records = new Map<BufferGeometry, GeometryRecord>();
  const meshes: MeshRecord[] = [];
  let lensRearZM = Infinity, dropM = 0, spreadM = 0, disposed = false;
  root.updateWorldMatrix(true, true);
  if (Math.abs(root.matrixWorld.determinant()) < 1e-12) throw new Error('The rear-drop root transform is singular.');
  const rootInverse = root.matrixWorld.clone().invert();
  try {
    root.traverse(object => {
      if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
      const original: BufferGeometry = object.geometry;
      const position = original.getAttribute('position');
      if (!position || position.itemSize !== 3) throw new Error('Rear-drop assets require three-component positions.');
      let record = records.get(original);
      if (!record) {
        record = {original, clone: original.clone(), referenced: new Uint8Array(position.count), fixed: new Uint8Array(position.count),
          opaque: new Uint8Array(position.count), lens: new Uint8Array(position.count)};
        records.set(original, record);
      }
      const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
      const index = original.getIndex();
      for (const [materialIndex, material] of materials.entries()) {
        const lens = isLens(material), opaque = !lens && !material.transparent;
        const groups = Array.isArray(object.material) ? original.groups.filter(group => group.materialIndex === materialIndex)
          : [{start: 0, count: index?.count ?? position.count}];
        for (const group of groups) for (let i = group.start; i < group.start + group.count; i++) {
          const vertex = index ? index.getX(i) : i;
          if (!Number.isInteger(vertex) || vertex < 0 || vertex >= position.count) throw new Error('The rear-drop mesh index is invalid.');
          const x = position.getX(vertex), y = position.getY(vertex), z = position.getZ(vertex);
          if (![x, y, z].every(Number.isFinite)) throw new Error('The rear-drop mesh contains invalid positions.');
          record.referenced[vertex] = 1;
          if (!opaque) record.fixed[vertex] = 1;
          if (lens) {record.lens[vertex] = 1; lensRearZM = Math.min(lensRearZM, z);}
          if (opaque) record.opaque[vertex] = 1;
        }
      }
      const toRoot = rootInverse.clone().multiply(object.matrixWorld);
      if (!toRoot.elements.every((value, index) => Math.abs(value - (index % 5 === 0 ? 1 : 0)) <= 1e-12)) {
        throw new Error('Rear-drop curve thresholds require identity mesh transforms inside the asset root.');
      }
      meshes.push({mesh: object, geometry: record, toRoot});
    });
    if (!Number.isFinite(lensRearZM)) throw new Error('Rear drop requires physical lens geometry to protect the optical front.');
    const startZM = lensRearZM - REAR_DROP_PARAMETERS.proximalGuardM;
    if (startZM <= cutoffZM) throw new Error('The rear-drop start must be forward of the accepted cap.');
    // The bend pivots at the hinge, which is further forward than the drop's start: the drop is a shear of the whole
    // arm and keeps its guard, the bend is a hinge and belongs where the frame front ends.
    const extents = new Map<number, {low: number; high: number}>();
    for (const record of records.values()) {
      const p = record.original.getAttribute('position');
      for (let i = 0; i < p.count; i++) {
        if (record.opaque[i] !== 1 || record.fixed[i] === 1 || Math.abs(p.getX(i)) <= REAR_DROP_PARAMETERS.lateralMinM) continue;
        const y = p.getY(i), slice = Math.floor(p.getZ(i) / HINGE_SLICE_M);
        const extent = extents.get(slice);
        if (extent) {extent.low = Math.min(extent.low, y); extent.high = Math.max(extent.high, y);}
        else extents.set(slice, {low: y, high: y});
      }
    }
    const hingeZM = armShaftStartZM(extents);
    const requested = (hingeZM ?? startZM) - spreadPivotM;
    const spreadStartZM = requested > cutoffZM + HINGE_MIN_SPAN_M ? requested : startZM;
    if (spreadStartZM <= cutoffZM + SPREAD_HINGE_ROUND_M) throw new Error('The arm-spread pivot must be forward of the accepted cap.');
    // Everything behind the frontmost of the two planes may be deformed; the drop's own curve is flat in front of its
    // start, so the shaft between the hinge and that start carries the bend alone.
    const deformableStartZM = Math.max(startZM, spreadStartZM);
    const opticalBounds = new Box3(), originalArmBounds = [new Box3(), new Box3()];
    let candidateArmBounds = [new Box3(), new Box3()];
    const eligible = (record: GeometryRecord, vertex: number): boolean => {
      const p = record.original.getAttribute('position');
      return record.opaque[vertex] === 1 && record.fixed[vertex] === 0
        && Math.abs(p.getX(vertex)) > REAR_DROP_PARAMETERS.lateralMinM && p.getZ(vertex) < deformableStartZM;
    };
    let affectedVertexCount = 0;
    for (const record of records.values()) for (let i = 0; i < record.opaque.length; i++) affectedVertexCount += Number(eligible(record, i));
    if (!affectedVertexCount) throw new Error('The asset has no eligible posterior opaque shaft.');
    const point = new Vector3();
    for (const {geometry, toRoot} of meshes) {
      const p = geometry.original.getAttribute('position');
      for (let i = 0; i < p.count; i++) {
        point.fromBufferAttribute(p, i).applyMatrix4(toRoot);
        // Protect every referenced point that the curve cannot deform, including
        // proximal shafts, small-X pads/bridge and other transparent materials.
        if (geometry.referenced[i] && !eligible(geometry, i)) opticalBounds.expandByPoint(point);
        if (eligible(geometry, i)) originalArmBounds[p.getX(i) < 0 ? 0 : 1]!.expandByPoint(point);
      }
    }
    const updateCandidateBounds = () => {
      candidateArmBounds = [new Box3(), new Box3()];
      for (const {geometry, toRoot} of meshes) {
        const p = geometry.clone.getAttribute('position');
        for (let i = 0; i < p.count; i++) if (eligible(geometry, i)) {
          candidateArmBounds[p.getX(i) < 0 ? 0 : 1]!.expandByPoint(point.fromBufferAttribute(p, i).applyMatrix4(toRoot));
        }
      }
    };
    for (const {mesh, geometry} of meshes) mesh.geometry = geometry.clone;
    updateCandidateBounds();
    /** Both deformations in one pass over the restored original storage: the lateral spread of the width fit and the
     *  pose-driven drop. They are shears along the same axis (both a function of z), so their tangent maps add and the
     *  normal correction is the sum of the two terms. */
    const applyShape = (drop: number, spread: number): void => {
      if (disposed) throw new Error('Rear drop is disposed.');
      validateDrop(drop); validateSpread(spread);
      if (drop === dropM && spread === spreadM) return;
      for (const record of records.values()) {
        // Restore raw storage first: interleaved attributes may share one buffer.
        for (const name of ['position', 'normal', 'tangent']) {
          const source = record.original.getAttribute(name), destination = record.clone.getAttribute(name);
          if (source && destination) restoreAttribute(destination, source);
        }
        if (drop > 0 || spread !== 0) {
          const originalPosition = record.original.getAttribute('position'), position = record.clone.getAttribute('position');
          const originalNormal = record.original.getAttribute('normal'), normal = record.clone.getAttribute('normal');
          const originalTangent = record.original.getAttribute('tangent'), tangent = record.clone.getAttribute('tangent');
          for (let i = 0; i < position.count; i++) {
            if (!eligible(record, i)) continue;
            const x = originalPosition.getX(i), z = originalPosition.getZ(i);
            const curve = rearDropCurve(z, startZM, cutoffZM, drop);
            const dxDz = spread === 0 ? 0 : Math.sign(x) * armSpreadSlope(z, spreadStartZM, cutoffZM, spread);
            if (spread !== 0) position.setX(i, spreadArmX(x, z, spreadStartZM, cutoffZM, spread));
            if (drop > 0) position.setY(i, originalPosition.getY(i) - curve.loweringM);
            if ((curve.dyDz !== 0 || dxDz !== 0) && normal && originalNormal) {
              point.set(originalNormal.getX(i), originalNormal.getY(i),
                originalNormal.getZ(i) - curve.dyDz * originalNormal.getY(i) - dxDz * originalNormal.getX(i)).normalize();
              normal.setXYZ(i, point.x, point.y, point.z);
            }
            if ((curve.dyDz !== 0 || dxDz !== 0) && tangent && originalTangent) {
              point.set(originalTangent.getX(i) + dxDz * originalTangent.getZ(i),
                originalTangent.getY(i) + curve.dyDz * originalTangent.getZ(i), originalTangent.getZ(i)).normalize();
              tangent.setXYZ(i, point.x, point.y, point.z);
            }
          }
        }
        record.clone.computeBoundingBox();
        record.clone.computeBoundingSphere();
      }
      dropM = drop; spreadM = spread;
      updateCandidateBounds();
    };
    return {
      get opticalBounds(): Box3 {return opticalBounds.clone();},
      get originalArmBounds(): Box3[] {return originalArmBounds.filter(bounds => !bounds.isEmpty()).map(bounds => bounds.clone());},
      get candidateArmBounds(): Box3[] {return candidateArmBounds.filter(bounds => !bounds.isEmpty()).map(bounds => bounds.clone());},
      get diagnostics() {
        return {method: 'posterior-y-preview-curve-v1', dropM, spreadM, spreadStartZM, hingeZM, widthFitMethod: WIDTH_FIT_METHOD, startZM, cutoffZM, lensRearZM,
          fadeLengthM: TEMPLE_BLEND_LENGTH_LOCAL_M, affectedVertexCount, sourceGeometryCount: records.size,
          originalZPreserved: true, originalXPreservedWithoutWidthFit: spreadM === 0, opticalFrontPreserved: true,
          meshTransformIdentityChecked: true, protectionIncludesAllUndeformedReferencedVertices: true,
          interpretation: 'Authored posterior Y curve, composed with the width fit\'s lateral arm spread; neither is a true hinge or a measured wearer fit.'};
      },
      /** The arm spread of the width fit now in the geometry (metres per arm; 0 is the original geometry). */
      get spreadM(): number {return spreadM;},
      /** The plane the spread pivots about: the asset's own hinge, moved back by `?templepivot=`. */
      get spreadStartZM(): number {return spreadStartZM;},
      /** Where this asset's frame front ends, or null when its cross-section has no such boundary. */
      get hingeZM(): number | null {return hingeZM;},
      get dropM(): number {return dropM;},
      setDrop(value: number): void {applyShape(value, spreadM);},
      /** The width fit's lateral arm spread; keeps the posed drop. */
      setSpread(value: number): void {applyShape(dropM, value);},
      /** Both at once, so a frame that changes both rebuilds the cloned buffers once. */
      setShape(drop: number, spread: number): void {applyShape(drop, spread);},
      dispose(): void {
        if (disposed) return;
        disposed = true;
        const originalsByClone = new Map([...records.values()].map(record => [record.clone, record.original]));
        // Include overlays added after installation, plus originals removed from root.
        root.traverse(object => {
          if (object instanceof Mesh) {
            const original = originalsByClone.get(object.geometry);
            if (original) object.geometry = original;
          }
        });
        for (const {mesh, geometry} of meshes) if (mesh.geometry === geometry.clone) mesh.geometry = geometry.original;
        for (const record of records.values()) record.clone.dispose();
      },
    };
  } catch (error) {
    for (const {mesh, geometry} of meshes) if (mesh.geometry === geometry.clone) mesh.geometry = geometry.original;
    for (const record of records.values()) record.clone.dispose();
    throw error;
  }
}
