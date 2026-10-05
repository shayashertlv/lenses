/** Explicit authored ownership for runtime visibility. Absent metadata retains the legacy asset path. */
import {Mesh} from 'three';
import type {Object3D} from 'three';

export const PART_VISIBILITY_KEY = 'LENSES_part_visibility';
export type PartVisibilityRegion = 'front' | 'temple_left' | 'temple_right' | 'lens';
export interface PartVisibility {readonly version: 1; readonly region: PartVisibilityRegion;}

/** Primitive groups inherit node extras; another Mesh is a distinct part boundary. */
function inherited(object: Object3D, key: string): unknown {
  for (let current: Object3D | null = object; current; current = current.parent) {
    if (current !== object && current instanceof Mesh) return undefined;
    if (current.userData[key] !== undefined) return current.userData[key];
  }
  return undefined;
}
export function partRoleOf(object: Object3D): unknown {return inherited(object, 'partRole');}
function profileOwner(object: Object3D): Object3D | null {
  if (object.userData[PART_VISIBILITY_KEY] !== undefined) return object;
  const parent = object.parent;
  // GLTFLoader represents a multi-primitive authored node as one immediate Group of Mesh primitives.
  // Never let a model-wide or distant grouping silently supply every physical part's ownership.
  return parent && !(parent instanceof Mesh) && parent.userData[PART_VISIBILITY_KEY] !== undefined ? parent : null;
}
export function partVisibilityOf(object: Object3D): PartVisibility | null {
  const value = profileOwner(object)?.userData[PART_VISIBILITY_KEY];
  if (value === undefined) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${PART_VISIBILITY_KEY}: expected a versioned object on ${object.name}`);
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== 2 || row.version !== 1 || typeof row.region !== 'string' || !['front', 'temple_left', 'temple_right', 'lens'].includes(row.region)) {
    throw new Error(`${PART_VISIBILITY_KEY}: unsupported version/region or extra fields on ${object.name}`);
  }
  return {version: 1, region: row.region as PartVisibilityRegion};
}
export function isProfiledTemple(object: Object3D): boolean {
  const region = partVisibilityOf(object)?.region;
  return region === 'temple_left' || region === 'temple_right';
}
export function profileTempleSide(object: Object3D): 0 | 1 | null {
  const region = partVisibilityOf(object)?.region;
  return region === 'temple_left' ? 0 : region === 'temple_right' ? 1 : null;
}

/** Validate all original primitives before any material/geometry mutation. No names or coordinate thresholds assign roles. */
export function validatePartVisibility(root: Object3D): boolean {
  const meshes: Mesh[] = []; let declared = false;
  root.traverse(object => {
    if (object.userData.templeVisibilityOverlay === true) return;
    declared ||= object.userData[PART_VISIBILITY_KEY] !== undefined;
    if (object instanceof Mesh) meshes.push(object);
  });
  const profiles = meshes.map(partVisibilityOf);
  if (!declared) return false;
  if (!meshes.length) throw new Error(`${PART_VISIBILITY_KEY}: no rendered mesh parts`);
  meshes.forEach((mesh, index) => {
    const profile = profiles[index];
    if (!profile) throw new Error(`${PART_VISIBILITY_KEY}: mixed profiled and unprofiled parts; missing ${mesh.name}`);
    if (profileOwner(mesh) === root && mesh !== root) throw new Error(`${PART_VISIBILITY_KEY}: model-root metadata cannot replace per-part profiles`);
    if (mesh.userData[PART_VISIBILITY_KEY] !== undefined && mesh.parent && !(mesh.parent instanceof Mesh)
        && mesh.parent.userData[PART_VISIBILITY_KEY] !== undefined && partVisibilityOf(mesh.parent)?.region !== profile.region) {
      throw new Error(`${PART_VISIBILITY_KEY}: conflicting node and primitive regions on ${mesh.name}`);
    }
    const role = mesh.userData.partRole ?? (mesh.parent && mesh.parent !== root && !(mesh.parent instanceof Mesh) ? mesh.parent.userData.partRole : undefined);
    if ((profile.region === 'lens' && role !== 'lens') || (profile.region !== 'lens' && role !== 'frame' && role !== 'temple')) {
      throw new Error(`${PART_VISIBILITY_KEY}: ${mesh.name} region conflicts with partRole`);
    }
    if ((mesh as Mesh & {isSkinnedMesh?: boolean}).isSkinnedMesh || Object.keys(mesh.geometry.morphAttributes).length || (mesh as Mesh & {isInstancedMesh?: boolean}).isInstancedMesh) {
      throw new Error(`${PART_VISIBILITY_KEY}: realize static geometry for ${mesh.name}`);
    }
    for (let current: Object3D | null = mesh; current; current = current.parent) {
      if (current.matrixAutoUpdate) current.updateMatrix();
      if (!current.matrix.elements.every((value, i) => Number.isFinite(value) && Math.abs(value - (i % 5 === 0 ? 1 : 0)) <= 1e-12)) {
        throw new Error(`${PART_VISIBILITY_KEY}: bake authored transforms for ${mesh.name}`);
      }
      if (current === root) break;
    }
    const positions = mesh.geometry.getAttribute('position');
    if (!positions || positions.itemSize !== 3 || !positions.count) throw new Error(`${PART_VISIBILITY_KEY}: missing XYZ geometry on ${mesh.name}`);
    for (let i = 0; i < positions.count; i++) {
      if (![positions.getX(i), positions.getY(i), positions.getZ(i)].every(Number.isFinite)) throw new Error(`${PART_VISIBILITY_KEY}: nonfinite geometry on ${mesh.name}`);
    }
  });
  return true;
}
