/** Shared optical identity for geometry and visibility consumers.
 * Canonical zero-transmission mirrors remain optical surfaces. A malformed
 * canonical descriptor throws instead of silently becoming a legacy material.
 *
 * Authored `partRole` extras identify native glTF frame, temple and lens meshes independently of their optical
 * shader. Crystal is frame even when it transmits; a native opaque mirror can still be a lens. Untagged materials
 * keep the shipped catalog's transmission rule. Native material instances shared across different roles are
 * separated without changing their PBR properties; a canonical descriptor remains authoritative.
 */
import {Mesh, MeshPhysicalMaterial} from 'three';
import type {Material, Object3D} from 'three';
import {readMaterialLensAppearance} from './lens-appearance.ts';
import {partRoleOf, partVisibilityOf, validatePartVisibility} from './part-visibility.ts';
export {partRoleOf} from './part-visibility.ts';

/** Explicit nearest-event group contract, distinct from authored front sheets. */
export const EFFECTIVE_OPTICAL_GROUP_PROFILE = 'effective_optical_group_v1_experiment';

/** Node roles the exporter writes for non-optical parts (automation/bsa/export.py PART_ROLE). */
export const FRAME_PART_ROLES: ReadonlySet<string> = new Set(['frame', 'temple']);
export const LENS_PART_ROLE = 'lens';

/** Materials classified as frame by authored roles; keyed by the loaded material object, so a second parse of the
 * same bytes (continuity) classifies its own materials. */
const frameMaterials = new WeakSet<Material>();
const lensMaterials = new WeakSet<Material>();

export interface AssetMaterialClassification {
  /** Whether any material carries a canonical descriptor; role classification is independent of this flag. */
  readonly canonical: boolean;
  /** Transmissive materials owned only by frame/temple parts: rendered by Three's physical transmission, never optical. */
  readonly translucentFrameMaterials: readonly Material[];
  /** Original materials shared by structural and other owners, including front/arm owners. Native instances are
   * separated before visibility hooks; conflicting canonical ownership remains optical. */
  readonly sharedMaterials: readonly Material[];
}

/** The authored role of a mesh. GLTFLoader copies mesh extras onto each primitive Mesh and node extras onto the
 * node object, which is the Mesh itself for a single-primitive node and its parent Group otherwise. A child mesh
 * is a separate part, so another Mesh's role does not propagate into it. */

/** The existing front zone: supported shaft clipping/fading cannot start ahead of -30 mm. */
export const AUTHORED_FRAME_FRONT_MIN_Z_M = -.03;

/** Only a separately authored, baked front is exempt from arm visibility. Older `frame` tags can name an entire
 * frame including long arms, so the role alone is insufficient. The shaders use mesh-local metres; nonidentity
 * transforms inside the asset retain the legacy rule rather than certifying an incorrectly positioned front. */
export function isAuthoredFrontFrame(object: Object3D, root: Object3D): boolean {
  if (!(object instanceof Mesh)) return false;
  const profile = partVisibilityOf(object);
  if (profile) return profile.region === 'front';
  if (!(object instanceof Mesh) || partRoleOf(object) !== 'frame') return false;
  const position = object.geometry.getAttribute('position');
  if (!position || position.itemSize < 3 || position.count === 0) return false;
  let reachedRoot = false;
  for (let current: Object3D | null = object; current; current = current.parent) {
    const identity = current.matrixAutoUpdate
      ? current.position.lengthSq() === 0 && current.quaternion.x === 0 && current.quaternion.y === 0
        && current.quaternion.z === 0 && current.quaternion.w === 1
        && current.scale.x === 1 && current.scale.y === 1 && current.scale.z === 1
      : current.matrix.elements.every((value, index) => value === (index % 5 === 0 ? 1 : 0));
    if (!identity) return false;
    if (current === root) {reachedRoot = true; break;}
  }
  if (!reachedRoot) return false;
  for (let i = 0; i < position.count; i++) {
    const x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    if (![x, y, z].every(Number.isFinite) || z < AUTHORED_FRAME_FRONT_MIN_Z_M) return false;
  }
  return true;
}

/** Classify every material of a loaded asset by the roles of the meshes that use it. Idempotent; call before any
 * consumer of isOpticalMaterial runs on that object graph. Throws on a malformed canonical descriptor. */
export function classifyAssetMaterials(root: Object3D): AssetMaterialClassification {
  validatePartVisibility(root);
  type Role = 'frame' | 'temple' | 'temple_left' | 'temple_right' | 'lens' | 'unknown';
  type Use = {mesh: Mesh; index: number};
  const ownership = new Map<Material, {descriptor: boolean; uses: Map<Role, Use[]>}>();
  let canonical = false;
  // Validate every descriptor before changing material assignments. A malformed later mesh must not leave a
  // partially separated asset behind. Classification runs before the renderer installs material shader hooks.
  root.traverse(object => {
    if (!(object instanceof Mesh) || object.userData.templeVisibilityOverlay === true) return;
    const role = partRoleOf(object);
    const profile = partVisibilityOf(object);
    const owner: Role = profile ? profile.region === 'front' ? 'frame' : profile.region : role === LENS_PART_ROLE ? 'lens'
      : typeof role === 'string' && FRAME_PART_ROLES.has(role)
        ? isAuthoredFrontFrame(object, root) ? 'frame' : 'temple' : 'unknown';
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material];
    const descriptors = materials.map(material => readMaterialLensAppearance(material) !== null);
    if (profile && profile.region !== 'lens' && descriptors.some(Boolean)) throw new Error('LENSES_part_visibility: structural parts cannot carry canonical lens descriptors.');
    const canonicalGroups = materials.length > 1 && descriptors.some(Boolean);
    for (const [index, material] of materials.entries()) {
      const descriptor = descriptors[index]!;
      canonical ||= descriptor;
      const owners = ownership.get(material) ?? {descriptor, uses: new Map<Role, Use[]>()};
      // Legacy mixed meshes identify their optical material groups with descriptors. A mesh-level lens tag must
      // not turn their other (frame) groups into native lenses; retain those groups' previous material rule.
      const materialOwner = canonicalGroups && owner === 'lens' && !descriptor ? 'unknown' : owner;
      const uses = owners.uses.get(materialOwner) ?? [];
      uses.push({mesh: object, index}); owners.uses.set(materialOwner, uses);
      ownership.set(material, owners);
    }
  });
  const translucentFrameMaterials: Material[] = [], sharedMaterials: Material[] = [];
  for (const [material, owners] of ownership) {
    const structural = owners.uses.has('frame') || owners.uses.has('temple') || owners.uses.has('temple_left') || owners.uses.has('temple_right');
    if (structural && (owners.descriptor || owners.uses.size > 1)) sharedMaterials.push(material);
    frameMaterials.delete(material); lensMaterials.delete(material);
    if (owners.descriptor) continue;
    // Keep the original on untagged owners when present, preserving their legacy interpretation. Otherwise retain
    // it on the lens. Each additional role gets one instance, shared by all meshes with that role. Textures and
    // geometry are retained; native tint, transmission, volume and other PBR properties are copied unchanged.
    // Compact fronts and arms need distinct instances even when both are structural: a material shader hook on
    // an arm must not also remove the front's inner rim from the physical lens input.
    const retained: Role = owners.uses.has('unknown') ? 'unknown' : owners.uses.has('lens') ? 'lens'
      : owners.uses.has('frame') ? 'frame' : owners.uses.has('temple') ? 'temple' : owners.uses.has('temple_left') ? 'temple_left' : 'temple_right';
    for (const [role, uses] of owners.uses) {
      const assigned = role === retained ? material : material.clone();
      if (assigned !== material) {
        assigned.onBeforeCompile = material.onBeforeCompile;
        assigned.customProgramCacheKey = material.customProgramCacheKey;
        for (const {mesh, index} of uses) {
          if (Array.isArray(mesh.material)) {
            const materials = mesh.material.slice(); materials[index] = assigned; mesh.material = materials;
          } else mesh.material = assigned;
        }
      }
      if (role === 'frame' || role === 'temple' || role === 'temple_left' || role === 'temple_right') {
        frameMaterials.add(assigned);
        if (assigned instanceof MeshPhysicalMaterial && assigned.transmission > 0) translucentFrameMaterials.push(assigned);
      } else if (role === 'lens') lensMaterials.add(assigned);
    }
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
  lensMaterials.delete(material);
}

/** A physical material that Three renders through its transmission pre-pass but that is frame, not optics. */
export function isTranslucentFrameMaterial(material: Material): boolean {
  return frameMaterials.has(material) && material instanceof MeshPhysicalMaterial && material.transmission > 0;
}

export function isOpticalMaterial(material: Material): boolean {
  if (readMaterialLensAppearance(material) !== null) return true;
  if (frameMaterials.has(material)) return false;
  return lensMaterials.has(material) || material instanceof MeshPhysicalMaterial && material.transmission > 0;
}
