import { Mesh, MeshPhysicalMaterial } from 'three';
import type { Object3D } from 'three';

/** Apply once to a newly loaded owned scene; shared materials need only one conversion. */
export function convertVolumeAttenuationUnits(scene: Object3D, scale = 1): void {
  if (!Number.isFinite(scale) || scale <= 0) throw new Error('Invalid volume length conversion.');
  if (scale === 1) return;
  const materials = new Set<MeshPhysicalMaterial>();
  scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (material instanceof MeshPhysicalMaterial && material.transmission > 0) materials.add(material);
    }
  });
  for (const material of materials) {
    // Three's transmission ray already scales thickness by modelMatrix. Only
    // attenuationDistance still needs the same length conversion here.
    if (Number.isFinite(material.attenuationDistance)) material.attenuationDistance *= scale;
  }
}
