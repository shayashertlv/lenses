/** QA registration only: never require an original arm cross-section beyond its physical tip. */
import {Mesh, Texture} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {profileTempleSide, validatePartVisibility} from '../src/eyewear/part-visibility.ts';
import {TEMPLE_CLIP_NEAREST_LOCAL_Z_M} from '../src/eyewear/catalog.ts';
import {verifyPinnedGeometryBytes} from '../src/render/continuity.ts';

export const DEFAULT_TEMPLE_CUTOFF_M = -.14;
export const TIP_INTERIOR_GUARD_M = 1e-6;

export function selectQATempleCutoff(root) {
  if (!validatePartVisibility(root)) return {policy: 'legacy_fixed_cutoff', cutoff_m: DEFAULT_TEMPLE_CUTOFF_M,
    default_cutoff_m: DEFAULT_TEMPLE_CUTOFF_M, side_bounds: null, geometry_modified: false};
  const sides = ['temple_left', 'temple_right'].map(region => ({region, min_z_m: Infinity, max_z_m: -Infinity, parts: []}));
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const side = profileTempleSide(object); if (side === null) return;
    const bounds = sides[side], geometry = object.geometry, positions = geometry.getAttribute('position'), index = geometry.getIndex();
    if (Array.isArray(object.material) && geometry.groups.some(group => !Number.isInteger(group.materialIndex)
        || group.materialIndex < 0 || !object.material[group.materialIndex])) throw Error('QA temple cutoff: invalid material group');
    const groups = Array.isArray(object.material) ? geometry.groups
      : [{start: 0, count: index?.count ?? positions.count}];
    let referenced = false;
    for (const group of groups) {
      if (!Number.isInteger(group.start) || !Number.isInteger(group.count) || group.start < 0 || group.count < 0
          || group.start % 3 || group.count % 3 || group.start + group.count > (index?.count ?? positions.count)) {
        throw Error('QA temple cutoff requires complete original triangles');
      }
      for (let offset = group.start; offset < group.start + group.count; offset++) {
        const vertex = index ? index.getX(offset) : offset;
        if (!Number.isInteger(vertex) || vertex < 0 || vertex >= positions.count) throw Error('QA temple cutoff: invalid triangle index');
        const z = positions.getZ(vertex);
        bounds.min_z_m = Math.min(bounds.min_z_m, z); bounds.max_z_m = Math.max(bounds.max_z_m, z); referenced = true;
      }
    }
    if (referenced) bounds.parts.push(object.name);
  });
  if (sides.some(side => !Number.isFinite(side.min_z_m) || side.max_z_m <= side.min_z_m)) {
    throw Error('QA temple cutoff requires actual geometry for both explicitly authored temple sides');
  }
  // One shared plane must lie inside BOTH physical arms. This is not a continuity repair:
  // the unchanged runtime still checks every original cross-section, including interior gaps.
  const cutoff = Math.max(DEFAULT_TEMPLE_CUTOFF_M, ...sides.map(side => side.min_z_m + TIP_INTERIOR_GUARD_M));
  if (cutoff > TEMPLE_CLIP_NEAREST_LOCAL_Z_M || sides.some(side => cutoff >= side.max_z_m)) throw Error('QA temple cutoff: original arm span is outside the supported range');
  return {policy: 'explicit_profile_arm_bounds', cutoff_m: cutoff, default_cutoff_m: DEFAULT_TEMPLE_CUTOFF_M,
    tip_interior_guard_m: TIP_INTERIOR_GUARD_M, side_bounds: sides, geometry_modified: false,
    continuity_validation: 'Unchanged runtime 33 original-triangle section samples per side, ending at the shared cutoff; not exhaustive topology validation'};
}

/** Parse hash-pinned geometry without texture decoding. The renderer independently verifies the same pin. */
export async function loadQATempleCutoff(assetUrl, signal) {
  const response = await fetch(assetUrl, {signal});
  if (!response.ok) throw Error('QA temple cutoff could not load the pinned original GLB');
  const bytes = await response.arrayBuffer(), identity = await verifyPinnedGeometryBytes(assetUrl, bytes, true);
  const gltf = await new GLTFLoader().register(() => ({name: 'QATempleCutoffGeometryOnly', loadTexture: async () => new Texture()})).parseAsync(bytes, '');
  try {return {...selectQATempleCutoff(gltf.scene), model_sha256: identity.sha256};}
  finally {
    const resources = new Set();
    for (const scene of gltf.scenes) scene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      resources.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        resources.add(material); for (const value of Object.values(material)) if (value instanceof Texture) resources.add(value);
      }
    });
    for (const resource of resources) resource.dispose();
  }
}
