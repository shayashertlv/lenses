/** Shared optical identity for geometry and visibility consumers.
 * Canonical zero-transmission mirrors remain optical surfaces. A malformed
 * canonical descriptor throws instead of silently becoming a legacy material.
 *
 * Authored part roles refine the rule for assets that carry canonical optics. The automation exporter writes a
 * `partRole` extra on every node (`frame`, `temple`, `lens`) and on every canonical lens mesh; when such an asset
 * has a material used only by frame or temple parts, that material is never optical whatever its transmission, so
 * crystal and translucent acetate render as frame (hair occlusion, arm clipping, continuity and shadows all treat
 * it as frame). Assets without any canonical descriptor keep the transmission rule unchanged: the shipped legacy
 * catalog carries neither roles nor descriptors and is not touched by the classification.
 */
import {Mesh, MeshPhysicalMaterial} from 'three';
import type {Material, Object3D} from 'three';
import {readMaterialLensAppearance} from './lens-appearance.ts';

/** Explicit nearest-event group contract, distinct from authored front sheets. */
export const EFFECTIVE_OPTICAL_GROUP_PROFILE = 'effective_optical_group_v1_experiment';

/** Node roles the exporter writes for non-optical parts (automation/bsa/export.py PART_ROLE). */
export const FRAME_PART_ROLES: ReadonlySet<string> = new Set(['frame', 'temple']);
export const LENS_PART_ROLE = 'lens';

/** Materials classified as frame by authored roles; keyed by the loaded material object, so a second parse of the
 * same bytes (continuity) classifies its own materials. */
const frameMaterials = new WeakSet<Material>();

export interface AssetMaterialClassification {
  /** Whether any material carries a canonical descriptor; without one no role tag is applied. */
  readonly canonical: boolean;
  /** Transmissive materials owned only by frame/temple parts: rendered by Three's physical transmission, never optical. */
  readonly translucentFrameMaterials: readonly Material[];
  /** Materials used by both a lens-owned and a frame-owned mesh: kept optical (the conservative rule), reported for
   * producers, whose exporter is expected to duplicate such materials. */
  readonly sharedMaterials: readonly Material[];
}

/** The authored role of a mesh. GLTFLoader copies mesh extras onto each primitive Mesh and node extras onto the
 * node object, which is the Mesh itself for a single-primitive node and its parent Group otherwise. */
export function partRoleOf(object: Object3D): unknown {
  for (let current: Object3D | null = object; current; current = current.parent) {
    const role: unknown = current.userData.partRole;
    if (role !== undefined) return role;
  }
  return undefined;
}

/** Classify every material of a loaded asset by the roles of the meshes that use it. Idempotent; call before any
 * consumer of isOpticalMaterial runs on that object graph. Throws on a malformed canonical descriptor. */
export function classifyAssetMaterials(root: Object3D): AssetMaterialClassification {
  const ownership = new Map<Material, {lens: boolean; frame: boolean; unknown: boolean}>();
  let canonical = false;
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const role = partRoleOf(object);
    const lensRole = role === LENS_PART_ROLE, frameRole = typeof role === 'string' && FRAME_PART_ROLES.has(role);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      const descriptor = readMaterialLensAppearance(material) !== null;
      canonical ||= descriptor;
      const owners = ownership.get(material) ?? {lens: false, frame: false, unknown: false};
      owners.lens ||= lensRole || descriptor;
      owners.frame ||= frameRole;
      owners.unknown ||= !lensRole && !frameRole && !descriptor;
      ownership.set(material, owners);
    }
  });
  const translucentFrameMaterials: Material[] = [], sharedMaterials: Material[] = [];
  for (const [material, owners] of ownership) {
    if (owners.lens && owners.frame) sharedMaterials.push(material);
    if (canonical && owners.frame && !owners.lens && !owners.unknown) {
      frameMaterials.add(material);
      if (material instanceof MeshPhysicalMaterial && material.transmission > 0) translucentFrameMaterials.push(material);
    } else frameMaterials.delete(material);
  }
  return {canonical, translucentFrameMaterials, sharedMaterials};
}

/** True for a material that authored roles classified as frame or temple (see classifyAssetMaterials). */
export function isFrameMaterial(material: Material): boolean {
  return frameMaterials.has(material);
}

/** Register a material derived from a classified frame material (a visibility overlay clone, a pass-B twin) as frame.
 * The set is keyed by object, so a clone of a translucent frame material would otherwise be classified by the
 * transmission rule and become optical. Reclassification skips overlay meshes and never removes such a material. */
export function markFrameMaterial(material: Material): void {
  frameMaterials.add(material);
}

/** A physical material that Three renders through its transmission pre-pass but that is frame, not optics. */
export function isTranslucentFrameMaterial(material: Material): boolean {
  return frameMaterials.has(material) && material instanceof MeshPhysicalMaterial && material.transmission > 0;
}

export function isOpticalMaterial(material: Material): boolean {
  if (frameMaterials.has(material)) return false;
  return readMaterialLensAppearance(material) !== null
    || material instanceof MeshPhysicalMaterial && material.transmission > 0;
}
