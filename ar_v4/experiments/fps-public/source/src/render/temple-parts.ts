import { Mesh, MeshPhysicalMaterial } from 'three';
import type { Material, Object3D } from 'three';

const OPTICAL_ROLES = new Set(['lens_left', 'lens_right']);
const FRAME_ROLES = new Set([
  'frame', 'rim_left', 'rim_right', 'bridge', 'temple_left', 'temple_right',
  'hardware', 'pad_left', 'pad_right',
]);

/** Use authored part roles only for explicitly opted-in trial assets. */
export function createTemplePartPolicy(root: Object3D): {isLens(material: Material): boolean} {
  const ownership = new Map<Material, {optical: boolean; frame: boolean; unknown: boolean}>();
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const role: unknown = object.userData.part_role;
    const optical = typeof role === 'string' && OPTICAL_ROLES.has(role);
    const frame = typeof role === 'string' && FRAME_ROLES.has(role);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      const owners = ownership.get(material) ?? {optical: false, frame: false, unknown: false};
      owners.optical ||= optical;
      owners.frame ||= frame;
      owners.unknown ||= !optical && !frame;
      ownership.set(material, owners);
    }
  });
  return Object.freeze({
    isLens(material: Material): boolean {
      const owners = ownership.get(material);
      // A material shared with either optical lens must never acquire frame cuts.
      if (owners?.optical) return true;
      if (owners?.frame && !owners.unknown) return false;
      // Missing or ambiguous semantics retain the original conservative rule.
      return material instanceof MeshPhysicalMaterial && material.transmission > 0;
    },
  });
}
