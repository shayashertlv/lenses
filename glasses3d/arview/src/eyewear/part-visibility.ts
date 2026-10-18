/** Optional authored ownership for runtime visibility (`LENSES_part_visibility` node extras: front, temple_left,
 *  temple_right, lens). Absent metadata keeps the positional asset path.
 *
 *  The regions are optional metadata (WP1_DESIGN.md §1): they are validated once at load (classifyAssetMaterials), and
 *  malformed or inconsistent profiles are ignored for the whole asset, never a throw: one message per problem, which
 *  the model report records as a 'part visibility' fallback (classifyAssetMaterials, model/normalize.ts). A
 *  region must agree with the decided lens/frame class (part-class.ts), which the lens identification reads from the
 *  glTF JSON, not from a raw `partRole`, and a temple region must lie on its side of X (temple_left at x < 0,
 *  temple_right at x > 0: sides are judged by geometry, WP1_DESIGN.md §3). */
import {Box3, Mesh} from 'three';
import type {BufferAttribute, Object3D} from 'three';
import {meshClassOf} from './part-class.ts';

export const PART_VISIBILITY_KEY = 'LENSES_part_visibility';
/** Where an ignored profile is kept (evidence only): no reader looks at this key, so every consumer takes the
 *  positional path for the whole asset, the near-arm overlays (which copy userData) included. */
export const IGNORED_PART_VISIBILITY_KEY = 'LENSES_part_visibility_ignored';
export type PartVisibilityRegion = 'front' | 'temple_left' | 'temple_right' | 'lens';
export interface PartVisibility {readonly version: 1; readonly region: PartVisibilityRegion;}
const REGIONS: readonly string[] = ['front', 'temple_left', 'temple_right', 'lens'];

/** A profile value, or 'malformed'. */
function readProfile(value: unknown): PartVisibility | 'malformed' {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 'malformed';
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== 2 || row.version !== 1 || typeof row.region !== 'string' || !REGIONS.includes(row.region)) return 'malformed';
  return {version: 1, region: row.region as PartVisibilityRegion};
}
function profileOwner(object: Object3D): Object3D | null {
  if (object.userData[PART_VISIBILITY_KEY] !== undefined) return object;
  const parent = object.parent;
  // GLTFLoader represents a multi-primitive authored node as one immediate Group of Mesh primitives.
  // Never let a model-wide or distant grouping silently supply every physical part's ownership.
  return parent && !(parent instanceof Mesh) && parent.userData[PART_VISIBILITY_KEY] !== undefined ? parent : null;
}
/** The object's profile, or null. A malformed value reads as null here (validatePartVisibility reports it and ignores
 *  the asset's profiles); this reader never throws. */
export function partVisibilityOf(object: Object3D): PartVisibility | null {
  const value = profileOwner(object)?.userData[PART_VISIBILITY_KEY];
  if (value === undefined) return null;
  const profile = readProfile(value);
  return profile === 'malformed' ? null : profile;
}
export function isProfiledTemple(object: Object3D): boolean {
  const region = partVisibilityOf(object)?.region;
  return region === 'temple_left' || region === 'temple_right';
}
export function profileTempleSide(object: Object3D): 0 | 1 | null {
  const region = partVisibilityOf(object)?.region;
  return region === 'temple_left' ? 0 : region === 'temple_right' ? 1 : null;
}

/** Move every profile under `root` to IGNORED_PART_VISIBILITY_KEY, so the whole asset takes the positional path. */
export function ignorePartVisibility(root: Object3D): void {
  root.traverse(object => {
    if (object.userData[PART_VISIBILITY_KEY] === undefined) return;
    object.userData[IGNORED_PART_VISIBILITY_KEY] = object.userData[PART_VISIBILITY_KEY];
    delete object.userData[PART_VISIBILITY_KEY];
  });
}

/** Whether `root` carries usable profiles. Inconsistent optional metadata (malformed values, mixed profiled and
 *  unprofiled parts, model-root profiles, conflicting node/primitive regions, a region that disagrees with the decided
 *  class) is ignored for the whole asset: each problem goes to `warn`, the profiles are moved aside
 *  (ignorePartVisibility) and the result is false. The class check needs classified meshes (classifyAssetMaterials
 *  runs this after registering the classes); an unclassified mesh is not class-checked.
 *
 *  Static geometry, baked transforms and finite XYZ positions are §2 Geometry/Transforms refusals of the one load-time
 *  check (model/normalize.ts, WP1_DESIGN.md §1), which runs before this; they are not re-checked here. */
export function validatePartVisibility(root: Object3D, warn?: (message: string) => void): boolean {
  const meshes: Mesh[] = []; let declared = false;
  root.traverse(object => {
    if (object.userData.templeVisibilityOverlay === true) return;
    declared ||= object.userData[PART_VISIBILITY_KEY] !== undefined;
    if (object instanceof Mesh) meshes.push(object);
  });
  if (!declared) return false;
  const problems: string[] = [];
  if (!meshes.length) problems.push('no rendered mesh parts');
  root.traverse(object => {
    if (object.userData.templeVisibilityOverlay === true) return;
    const value = object.userData[PART_VISIBILITY_KEY];
    if (value !== undefined && readProfile(value) === 'malformed') {
      problems.push(`expected {version: 1, region: front|temple_left|temple_right|lens} and nothing else on "${object.name}"`);
    }
  });
  for (const mesh of meshes) {
    const owner = profileOwner(mesh), profile = partVisibilityOf(mesh);
    if (!owner) {problems.push(`mixed profiled and unprofiled parts; missing "${mesh.name}"`); continue;}
    if (owner === root && mesh !== root) problems.push('model-root metadata cannot replace per-part profiles');
    if (mesh.userData[PART_VISIBILITY_KEY] !== undefined && mesh.parent && !(mesh.parent instanceof Mesh)
        && mesh.parent.userData[PART_VISIBILITY_KEY] !== undefined) {
      const parent = readProfile(mesh.parent.userData[PART_VISIBILITY_KEY]), own = readProfile(mesh.userData[PART_VISIBILITY_KEY]);
      if (parent === 'malformed' || own === 'malformed' || parent.region !== own.region) {
        problems.push(`conflicting node and primitive regions on "${mesh.name}"`);
      }
    }
    const decided = meshClassOf(mesh);
    if (profile && decided !== undefined && (profile.region === 'lens') !== (decided === 'lens')) {
      problems.push(`"${mesh.name}" region '${profile.region}' conflicts with its identified class '${decided}'`);
    }
    // Every consumer maps temple_left to the negative-X side (clip cutoff, continuity, terminal return): a region on the
    // other half of X (the anatomical left is +X) would be drawn with the wrong side's shape. Sides are judged by
    // geometry, so such profiles are ignored and position decides.
    const position = mesh.geometry.getAttribute('position') as BufferAttribute | undefined;
    if (profile && (profile.region === 'temple_left' || profile.region === 'temple_right') && position && position.itemSize >= 3) {
      const box = new Box3().setFromBufferAttribute(position);
      const centre = (box.min.x + box.max.x) / 2, left = profile.region === 'temple_left';
      if (!box.isEmpty() && (left ? !(centre < 0) : !(centre > 0))) {
        problems.push(`"${mesh.name}" region '${profile.region}' lies at x ${(centre * 1000).toFixed(1)} mm, but legacy region temple_left must lie at x < 0 (wearer's right) and `
          + "temple_right at x > 0 (wearer's left; legacy region IDs are kept readable)");
      }
    }
  }
  if (problems.length) {
    for (const problem of [...new Set(problems)]) warn?.(`${PART_VISIBILITY_KEY} ignored for the whole asset: ${problem}.`);
    ignorePartVisibility(root);
    return false;
  }
  return true;
}
