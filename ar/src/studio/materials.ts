import {Mesh, MeshPhysicalMaterial, MeshStandardMaterial, SRGBColorSpace} from 'three';
import type {Material, Object3D} from 'three';
import type {GLTF} from 'three/addons/loaders/GLTFLoader.js';
import {readMaterialLensAppearance} from '../eyewear/lens-appearance.ts';
import {VOLUME_UNIT_SCALE_KEY} from '../render/eyewear-volume.ts';
import type {MaterialProperty, StudioPreview} from './protocol.ts';

const ID = 'lensesStudioMaterialId', KEYS = 'lensesStudioEditableKeys', BASELINE = 'lensesStudioBaseline';
export interface StudioMaterial {id: string; name: string; editable_keys: MaterialProperty[]}
/** Original glTF indices survive Material.clone() through userData, including role splits and arm overlays. */
export function tagStudioMaterials(gltf: GLTF): void {
  const json = gltf.parser.json as {extensionsUsed?: string[]; materials?: {extensions?: Record<string, unknown>}[]};
  gltf.scene.traverse(object => {
    if (!(object instanceof Mesh)) return;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      const index = gltf.parser.associations.get(material)?.materials;
      if (index === undefined) continue;
      const ext = json.materials?.[index]?.extensions ?? {};
      const keys: MaterialProperty[] = [];
      if (material instanceof MeshStandardMaterial && !readMaterialLensAppearance(material) && !json.extensionsUsed?.includes('LENSES_lens_appearance')) {
        keys.push('base_color', 'metallic', 'roughness');
        if (material instanceof MeshPhysicalMaterial) {
          if (material.transmission > 0) keys.push('transmission');
          if (ext.KHR_materials_ior) keys.push('ior');
          if (ext.KHR_materials_clearcoat) keys.push('clearcoat', 'clearcoat_roughness');
          if (ext.KHR_materials_iridescence) {
            keys.push('iridescence', 'iridescence_ior');
            if (!material.iridescenceThicknessMap) keys.push('iridescence_thickness');
          }
          if (ext.KHR_materials_volume) keys.push('attenuation_color', 'attenuation_distance');
        }
      }
      material.userData[ID] = String(index); material.userData[KEYS] = keys;
      if (material instanceof MeshStandardMaterial) material.userData[BASELINE] ??= {
        base_color: {hex: '#' + material.color.getHexString(SRGBColorSpace), linear: material.color.toArray()},
        ...(material instanceof MeshPhysicalMaterial ? {
          attenuation_color: {hex: '#' + material.attenuationColor.getHexString(SRGBColorSpace), linear: material.attenuationColor.toArray()},
          thickness_min: material.iridescenceThicknessRange[0],
        } : {}),
      };
    }
  });
}

export function studioMaterialBindings(root: Object3D): Map<string, Set<Material>> {
  const result = new Map<string, Set<Material>>();
  root.traverse(object => {
    if (!(object instanceof Mesh)) return;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      const id: unknown = material.userData[ID];
      if (typeof id !== 'string') continue;
      if (!result.has(id)) result.set(id, new Set());
      result.get(id)!.add(material);
    }
  });
  return result;
}

export function studioMaterials(root: Object3D): StudioMaterial[] {
  return [...studioMaterialBindings(root)].map(([id, materials]) => {
    const material = [...materials][0]!;
    return {id, name: material.name, editable_keys: [...material.userData[KEYS] ?? []]};
  }).sort((a, b) => Number(a.id) - Number(b.id));
}

/** Validate every edit before touching any material. Distances arrive in glTF metres; AR uses centimetres. */
export function applyStudioMaterials(root: Object3D, edits: StudioPreview['edits'], distanceScale = 1): string[] {
  const bindings = studioMaterialBindings(root);
  const changed = new Set<string>();
  const snapshot = (m: MeshPhysicalMaterial) => JSON.stringify([m.color.toArray(), m.metalness, m.roughness, m.transmission,
    m.ior, m.clearcoat, m.clearcoatRoughness, m.iridescence, m.iridescenceIOR, m.iridescenceThicknessRange,
    m.attenuationColor?.toArray(), m.attenuationDistance, m.userData[VOLUME_UNIT_SCALE_KEY]]);
  for (const [id, edit] of Object.entries(edits)) {
    const materials = bindings.get(id);
    if (!materials) throw new Error(`Material ${id} is absent from this model.`);
    for (const material of materials) for (const key of Object.keys(edit)) {
      if (!(material.userData[KEYS] as string[] | undefined)?.includes(key)) throw new Error(`Material ${id} does not support live ${key} edits.`);
    }
  }
  for (const [id, edit] of Object.entries(edits)) for (const item of bindings.get(id)!) {
    const material = item as MeshPhysicalMaterial;
    const before = snapshot(material);
    const baseline = material.userData[BASELINE] as {base_color: {hex: string; linear: number[]}; attenuation_color?: {hex: string; linear: number[]}; thickness_min?: number};
    for (const [key, value] of Object.entries(edit)) {
      switch (key as MaterialProperty) {
        case 'base_color':
          if (value === baseline.base_color.hex) material.color.fromArray(baseline.base_color.linear);
          else material.color.setStyle(value as string, SRGBColorSpace);
          break;
        case 'attenuation_color':
          if (value === baseline.attenuation_color?.hex) material.attenuationColor.fromArray(baseline.attenuation_color.linear);
          else material.attenuationColor.setStyle(value as string, SRGBColorSpace);
          break;
        case 'metallic': material.metalness = value as number; break;
        case 'roughness': material.roughness = value as number; break;
        case 'transmission': material.transmission = value as number; break;
        case 'ior': material.ior = value as number; break;
        case 'clearcoat': material.clearcoat = value as number; break;
        case 'clearcoat_roughness': material.clearcoatRoughness = value as number; break;
        case 'iridescence': material.iridescence = value as number; break;
        case 'iridescence_ior': material.iridescenceIOR = value as number; break;
        case 'iridescence_thickness': material.iridescenceThicknessRange = [Math.min(baseline.thickness_min ?? 100, value as number), value as number]; break;
        case 'attenuation_distance':
          material.attenuationDistance = value === 0 ? Infinity : (value as number) * distanceScale;
          material.userData[VOLUME_UNIT_SCALE_KEY] = distanceScale;
          break;
      }
    }
    if (snapshot(material) !== before) {material.needsUpdate = true; changed.add(id);}
  }
  return [...changed];
}
