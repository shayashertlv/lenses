/** Volume absorption units. `KHR_materials_volume` attenuation distances are metres in the asset; the eyewear asset is
 * scaled to centimetres in the scene, and Three scales a material's `thickness` through the model matrix while it reads
 * `attenuationDistance` in world units. Without a conversion the absorption exponent thickness / distance is a hundred
 * times the authored one and a translucent front or a legacy volume lens renders black. The conversion is applied once
 * per material and recorded, so the shadow keeps computing with the authored ratio. */
import {Mesh, MeshPhysicalMaterial} from 'three';
import type {Material, Object3D} from 'three';

/** userData key holding the factor applied to `attenuationDistance` (absent = authored units). */
export const VOLUME_UNIT_SCALE_KEY = 'attenuationDistanceScale';

/** Multiply every finite volume attenuation distance under `root` by `scale` (idempotent). Returns the converted materials. */
export function applyVolumeAttenuationScale(root: Object3D, scale: number): MeshPhysicalMaterial[] {
  if (!Number.isFinite(scale) || scale <= 0) throw new Error('The volume attenuation scale must be a positive finite number.');
  const converted: MeshPhysicalMaterial[] = [];
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    for (const material of (Array.isArray(object.material) ? object.material : [object.material]) as Material[]) {
      if (!(material instanceof MeshPhysicalMaterial) || !(material.transmission > 0)) continue;
      if (!Number.isFinite(material.attenuationDistance) || material.attenuationDistance <= 0) continue;
      if (material.userData[VOLUME_UNIT_SCALE_KEY] !== undefined) continue;
      material.attenuationDistance *= scale;
      material.userData[VOLUME_UNIT_SCALE_KEY] = scale;
      converted.push(material);
    }
  });
  return converted;
}

/** The authored (asset-unit) attenuation distance of a material, undoing the scene-unit conversion when one was applied. */
export function authoredAttenuationDistance(material: MeshPhysicalMaterial): number {
  const scale: unknown = material.userData[VOLUME_UNIT_SCALE_KEY];
  return typeof scale === 'number' && scale > 0 ? material.attenuationDistance / scale : material.attenuationDistance;
}

/** Beer-Lambert absorption of the material's volume at normal incidence: attenuationColor ^ (thickness /
 * authoredAttenuationDistance), per channel in linear RGB, both lengths in asset units. [1, 1, 1] for an infinite
 * attenuation distance or a zero thickness. Shared by the face shadow (eyewear-shadow.ts) and the pass-B camera
 * transmission twin (translucent-twin.ts), so both absorb exactly what Three's transmission absorbs. */
export function volumeAttenuationRgb(material: MeshPhysicalMaterial): [number, number, number] {
  const thickness = Math.max(0, Number.isFinite(material.thickness) ? material.thickness : 0);
  const distance = authoredAttenuationDistance(material);
  const exponent = Number.isFinite(distance) && distance > 0 ? thickness / distance : 0;
  const channels = ['r', 'g', 'b'] as const;
  return channels.map(channel => {
    const attenuation = material.attenuationColor[channel];
    const bounded = Number.isFinite(attenuation) ? Math.min(1, Math.max(0, attenuation)) : 1;
    return exponent > 0 ? bounded ** exponent : 1;
  }) as [number, number, number];
}
