import {
  Box3, BufferAttribute, InterleavedBufferAttribute, MathUtils, Matrix4, Mesh,
  MeshPhysicalMaterial, Vector3,
} from 'three';
import type {BufferGeometry, Material, Object3D} from 'three';
import {TEMPLE_BLEND_LENGTH_LOCAL_M} from '../../src/render/temple-clip.ts';

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
export function createRearDrop(root: Object3D, cutoffZM: number) {
  if (!Number.isFinite(cutoffZM) || cutoffZM < -.2 || cutoffZM > -.03) throw new Error('The rear-drop endpoint is invalid.');
  const records = new Map<BufferGeometry, GeometryRecord>();
  const meshes: MeshRecord[] = [];
  let lensRearZM = Infinity, dropM = 0, disposed = false;
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
    const opticalBounds = new Box3(), originalArmBounds = [new Box3(), new Box3()];
    let candidateArmBounds = [new Box3(), new Box3()];
    const eligible = (record: GeometryRecord, vertex: number): boolean => {
      const p = record.original.getAttribute('position');
      return record.opaque[vertex] === 1 && record.fixed[vertex] === 0
        && Math.abs(p.getX(vertex)) > REAR_DROP_PARAMETERS.lateralMinM && p.getZ(vertex) < startZM;
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
    return {
      get opticalBounds(): Box3 {return opticalBounds.clone();},
      get originalArmBounds(): Box3[] {return originalArmBounds.filter(bounds => !bounds.isEmpty()).map(bounds => bounds.clone());},
      get candidateArmBounds(): Box3[] {return candidateArmBounds.filter(bounds => !bounds.isEmpty()).map(bounds => bounds.clone());},
      get diagnostics() {
        return {method: 'posterior-y-preview-curve-v1', dropM, startZM, cutoffZM, lensRearZM,
          fadeLengthM: TEMPLE_BLEND_LENGTH_LOCAL_M, affectedVertexCount, sourceGeometryCount: records.size,
          originalXAndZPreserved: true, opticalFrontPreserved: true,
          meshTransformIdentityChecked: true, protectionIncludesAllUndeformedReferencedVertices: true,
          interpretation: 'Authored posterior Y curve; not a true hinge or measured wearer fit.'};
      },
      setDrop(value: number): void {
        if (disposed) throw new Error('Rear drop is disposed.');
        validateDrop(value);
        if (value === dropM) return;
        for (const record of records.values()) {
          // Restore raw storage first: interleaved attributes may share one buffer.
          for (const name of ['position', 'normal', 'tangent']) {
            const source = record.original.getAttribute(name), destination = record.clone.getAttribute(name);
            if (source && destination) restoreAttribute(destination, source);
          }
          if (value > 0) {
            const originalPosition = record.original.getAttribute('position'), position = record.clone.getAttribute('position');
            const originalNormal = record.original.getAttribute('normal'), normal = record.clone.getAttribute('normal');
            const originalTangent = record.original.getAttribute('tangent'), tangent = record.clone.getAttribute('tangent');
            for (let i = 0; i < position.count; i++) {
              if (!eligible(record, i)) continue;
              const curve = rearDropCurve(originalPosition.getZ(i), startZM, cutoffZM, value);
              position.setY(i, originalPosition.getY(i) - curve.loweringM);
              if (curve.dyDz !== 0 && normal && originalNormal) {
                point.set(originalNormal.getX(i), originalNormal.getY(i), originalNormal.getZ(i) - curve.dyDz * originalNormal.getY(i)).normalize();
                normal.setXYZ(i, point.x, point.y, point.z);
              }
              if (curve.dyDz !== 0 && tangent && originalTangent) {
                point.set(originalTangent.getX(i), originalTangent.getY(i) + curve.dyDz * originalTangent.getZ(i), originalTangent.getZ(i)).normalize();
                tangent.setXYZ(i, point.x, point.y, point.z);
              }
            }
          }
          record.clone.computeBoundingBox();
          record.clone.computeBoundingSphere();
        }
        dropM = value;
        updateCandidateBounds();
      },
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
