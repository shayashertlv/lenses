/** QA registration: the temple clip exactly as the renderer derives it. The rule lives in src
 *  (src/render/temple-geometry.ts, the port of this file's stash rule): temples by position per side whatever their
 *  opacity (or by valid LENSES_part_visibility regions), each arm tip measured on its own, the one shared plane just
 *  inside the SHORTER arm and never behind it, floored at -0.14 m and clamped to [-0.2, -0.045] m. A side without a
 *  temple is reported, never a throw; the renderer gives it the fixed cap. */
import {validatePartVisibility} from '../src/eyewear/part-visibility.ts';
import {TEMPLE_CLIP_FLOOR_LOCAL_Z_M} from '../src/eyewear/catalog.ts';
import {deriveTempleClip, templeInventory, templeLoadFallbacks, TEMPLE_TIP_GUARD_M} from '../src/render/temple-geometry.ts';

export const DEFAULT_TEMPLE_CUTOFF_M = TEMPLE_CLIP_FLOOR_LOCAL_Z_M;
export const TIP_INTERIOR_GUARD_M = TEMPLE_TIP_GUARD_M;

/** The receipt for a normalised scene: the derived clip, its source and every side's bounds, and `load_fallbacks`: the
 *  fallbacks the renderer reports from the temples alone (temple-geometry.ts templeLoadFallbacks: a missing temple's fixed
 *  cap, the default clip, continuity off without any temple), word for word. `handoverZM` is the clip handed over with the
 *  model (the renderer's ModelDefinition.templeClipZM), or null. Malformed or inconsistent profiles are optional metadata:
 *  they are ignored for the whole asset (position decides) and the receipt says why. */
export function selectQATempleCutoff(root, handoverZM = null) {
  const profileWarnings = [];
  validatePartVisibility(root, message => profileWarnings.push(message));
  const inventory = templeInventory(root), clip = deriveTempleClip(inventory, handoverZM);
  return {policy: inventory.profiled ? 'authored_temple_regions' : 'positional_temples', cutoff_m: clip.clipZM, source: clip.source,
    shorter_side: clip.shorterSide, default_cutoff_m: DEFAULT_TEMPLE_CUTOFF_M, tip_interior_guard_m: TIP_INTERIOR_GUARD_M,
    side_bounds: inventory.sides.map(side => ({side: side.side, present: side.present, source: side.source, min_z_m: side.tipZM,
      max_z_m: side.rootZM, parts: [...side.parts], ...(side.missing ? {missing: side.missing} : {})})),
    warnings: [...inventory.warnings, ...clip.warnings], load_fallbacks: templeLoadFallbacks(inventory, clip), geometry_modified: false,
    ...(profileWarnings.length ? {profile_warnings: profileWarnings} : {})};
}
