/** QA registration: the temple clip exactly as the renderer derives it. The rule lives in src
 *  (src/render/temple-geometry.ts, the port of this file's stash rule): temples by position per side whatever their
 *  opacity (or by valid LENSES_part_visibility regions), each arm tip measured on its own, the one shared plane just
 *  inside the SHORTER arm and never behind it, floored at -0.14 m and clamped to [-0.2, -0.045] m. A side without a
 *  temple is reported, never a throw; the renderer gives it the fixed cap. */
import {Mesh, Texture} from 'three';
import {checkModel} from '../src/model/check.ts';
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

/** The cutoff for the model at `assetUrl`, read the way the renderer reads it: the load front-end and the one
 *  normalisation pass (model/check.ts = TryOnRenderer.create's load minus drawing), so the same §2 refusals, the units
 *  rescale (a millimetre file is measured in metres) and the decided classes apply, with no pin and no rule of its own
 *  (WP1_DESIGN.md §1, §3). Texture pixels are never decoded. A refused model throws with the report's refusal; the
 *  receipt carries the SHA-256 of the bytes read and the report's warnings and fallbacks (an ignored part-visibility
 *  profile among them), never silently. */
export async function loadQATempleCutoff(assetUrl, signal) {
  const response = await fetch(assetUrl, signal ? {signal} : {});
  if (!response.ok) throw Error('QA temple cutoff could not load the original GLB');
  const bytes = await response.arrayBuffer();
  const {report, scene} = await checkModel(bytes, {url: assetUrl, renderer: null, signal,
    plugins: [() => ({name: 'QATempleCutoffGeometryOnly', loadTexture: async () => new Texture()})]});
  if (report.status === 'unchecked') throw Error(`QA temple cutoff: the model could not be checked here, no verdict on the file (${report.unchecked.join('; ')})`);
  if (report.status !== 'rendered' || !scene) throw Error(`QA temple cutoff: the model was refused (${report.refusal.join('; ')})`);
  try {
    return {...selectQATempleCutoff(scene), model_sha256: report.rendered_sha256, model_status: report.status,
      model_warnings: report.warnings, model_fallbacks: report.fallbacks};
  }
  finally {
    const resources = new Set();
    scene.traverse(object => {
      if (!(object instanceof Mesh)) return;
      resources.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        resources.add(material); for (const value of Object.values(material)) if (value instanceof Texture) resources.add(value);
      }
    });
    for (const resource of resources) resource.dispose();
  }
}
