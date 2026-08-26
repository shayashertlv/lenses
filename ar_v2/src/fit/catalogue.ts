/**
 * The eyewear catalogue: what each asset is, and what nothing in its geometry
 * can say about it.
 *
 * ## Why a table and not a derivation
 *
 * Absolute scale is not recoverable from a mesh. An arbitrary-unit model carries
 * shape and nothing else, so no amount of measurement puts its size back — and
 * a frame whose width is a placeholder cannot honestly be compared against a
 * wearer's head, because the comparison is then one estimate against another.
 * v1's audit found exactly that: nine of its eleven entries carried a 140 mm
 * placeholder and its width verdict was solving against it.
 *
 * So every row states its width and **where that width came from**, and the
 * answer travels all the way to `FrameAsset.dimensionSource`. Two rows say
 * `cad`; eight say `assumed`, and `assumed` is the ledger's worst class on
 * purpose — `stated` is a choice, `assumed` is a hole.
 *
 * ## What is portable from v1, and what is not
 *
 * The orientation quaternions and the placeholder width are v1's, kept with
 * their derivations because they were measured rather than eyeballed: the
 * Tripo pair's `orient` came from the asset's own lens-centroid symmetry axis,
 * checked by the residual height difference between the two lenses going to
 * zero. Re-deriving them here would produce the same numbers from the same
 * geometry, and pretending otherwise would be worse provenance, not better.
 *
 * What is NOT portable is anything about how a frame is drawn. v1's per-asset
 * `pbr`, `crystal` and lens-material corrections belong to its renderer and are
 * ported in `render/`, not here — `fit/` is headless and must stay so.
 *
 * ## The part names are declared because pipelines disagree, not because it is
 * convenient
 *
 * navigator says `Temple_L`. khronos says `EarhookLeft`. Tripo says
 * `tripo_part_3`. Meshy says nothing at all — one fused mesh, 106k triangles,
 * no node name and no material name. There is no naming convention to infer, so
 * the names are declared where they exist and left EMPTY where they do not, and
 * an empty list makes `frameFromMesh` refuse. A refusal that names the parts the
 * file actually has is worth more than a layout guessed off a bounding box.
 *
 * ## What derives today, and what each row can prove about itself
 *
 * **All ten wear. What differs is what they can say about their own arms**, and
 * that is what `FrameAsset.earRestSource` carries out of here. The tiers are set
 * by `frameFromMesh`, measured per asset — see `deriveArmRest` for the
 * slope-ratio table that separates the last two:
 *
 *     navigator     temple named; its bend walked directly       MEASURED
 *     aviator x2    arms welded into the shell, found by knee    derived
 *     horizon x2    same                                         derived
 *     crystal x2    eight `tripo_part_N`, arms found by knee     derived
 *     meshy         one fused mesh, arms found by knee           derived
 *     khronos       an EARHOOK: no rest point exists             assumed
 *     shield-golden a WRAP: no rest point exists                 assumed
 *
 * This used to read "one of ten, and that is the honest state of this
 * catalogue" — true when a rest point could only come from a part called
 * `temple`. The nine refusals were correct answers to the question being asked;
 * the question changed. What has NOT changed is that two of these are wraps
 * whose arms do not rest on anything, and for those the reach and height come
 * from the wearer's own ear rather than from the asset. `earRestSource` says
 * `assumed` and the interface repeats it in words.
 *
 * The stage-8 measurement day is still owed, and it is now only about WIDTH:
 * eight rows carry an estimated front width, which is the one quantity no
 * amount of geometry can recover.
 */

import type { CatalogueEntry } from './frame-from-mesh.js';

/**
 * The placeholder width, in millimetres.
 *
 * `assumed`. 140 mm is squarely inside the adult range — total front widths run
 * roughly 125-150 mm — which is exactly why it went unnoticed in v1: it renders
 * plausibly and it is wrong by an unknown amount on every asset carrying it.
 * The sensitivity, from v1's own audit: the width verdict compares this number
 * against the wearer's head, so +-10 mm of assumption moves the comparison by
 * +-6.5% against the mean face, where the whole span between the verdict's
 * narrow and wide edges is about 20%. The assumption alone can decide the
 * answer over a third of that band.
 *
 * Retiring it is one number per asset — the product's total front width, or its
 * `A□DBL` marking plus the endpieces — measured with a rule or read off a
 * supplier's spec. Data entry, not geometry.
 */
export const ASSUMED_WIDTH_MM = 140;

/** Temple part names seen across the catalogue, for the rows that have them. */
const TEMPLE_NAMES = ['temple', 'earhook'];
/**
 * Lens part names, matched against node AND material name.
 *
 * Measured over all ten GLBs: this matches the 15 real lens parts with zero
 * false positives, and misses two assets entirely (`meshy-glasses`,
 * `crystal-parts`) because neither names a lens anywhere. The miss is silent by
 * construction — a matcher returning nothing looks exactly like a frame with no
 * lenses — so those two rows declare an empty list rather than relying on the
 * matcher to come up empty, and `frameFromMesh` refuses them by name.
 *
 * Deliberately NOT "declares KHR_materials_transmission". That test false-matches
 * `Frame_Acetate_Translucent` on horizon-sage, which is a translucent FRAME, and
 * `nose_pads` on khronos, which are the nose pads.
 */
const LENS_NAMES = ['lens'];

/**
 * Every asset this tree can load, with its provenance.
 *
 * `base.obj` is absent: `mesh-io.ts` reads GLB and only GLB, and the OBJ path
 * v1 carried existed for one asset that nothing measures.
 */
export const CATALOGUE: readonly CatalogueEntry[] = [
  {
    id: 'navigator',
    name: 'Navigator (black acetate)',
    file: 'assets/glasses/navigator.glb',
    // Authored rather than scanned, and it shows in everything the other rows
    // have to correct: axis-aligned, in metres at life size, 147.5 mm across
    // with 140 mm temples, and materials that say what they mean. Rendering it
    // at the size it was modelled is the only honest input for true-size fit.
    realWidthMm: null,
    widthSource: 'cad',
    orient: null,
    // Not weighed. A mid-weight acetate front with acetate temples; the mass
    // enters the seat as a gravity term, where the population sweep put the
    // catalogue's range at 20-42 g. Retiring this needs a kitchen scale.
    massG: 24,
    bridgeType: 'pads',
    provenance: 'navigator.glb, authored in metres at life size; mass assumed',
    parts: { temple: TEMPLE_NAMES, lens: LENS_NAMES },
  },
  {
    id: 'khronos',
    name: 'Sunglasses (Khronos)',
    file: 'assets/glasses/sunglasses-khronos.glb',
    // Also authored at life size — 150.5 mm across, its author's number.
    realWidthMm: null,
    widthSource: 'cad',
    orient: null,
    massG: 28,
    bridgeType: 'pads',
    provenance: 'sunglasses-khronos.glb, authored in metres at life size; mass assumed',
    // Named, and it still refuses: `EarhookLeft` descends monotonically from the
    // hinge, so there is no bend to put an ear rest at. It is a sports wrap
    // whose arm hooks AROUND the ear rather than resting on it, and this tree
    // has no model of that contact. See `findBend`.
    parts: { temple: TEMPLE_NAMES, lens: LENS_NAMES },
  },
  {
    id: 'aviator-tortoiseshell',
    name: 'Tortoiseshell aviator (AI scan)',
    file: 'assets/glasses/aviator-tortoiseshell.glb',
    realWidthMm: 137.5,
    widthSource: 'assumed',
    // No orient: Blender's -Y front and +Z up become glTF's +Z and +Y under the
    // exporter's own axis conversion, which is already this tree's convention.
    orient: null,
    massG: 24,
    bridgeType: 'pads',
    provenance: 'Meshy image-to-3D, 1.95M tris decimated to 106k; width 137.5 mm is a VISUAL ESTIMATE by the owner of the gold/tortoiseshell metal aviator eyeglasses (stated range 135-140 mm, 2026-08-26), not a caliper reading -- the range is wider than the 4 mm band the width verdict grades against, so widthSource stays assumed',
    // Two parts only — `Frame` and `Lenses`. The temples are welded into the
    // frame shell, so there is nothing to find a bend on.
    parts: { temple: [], lens: LENS_NAMES },
  },
  {
    id: 'aviator-amber',
    name: 'Amber aviator (AI scan)',
    file: 'assets/glasses/aviator-amber.glb',
    realWidthMm: 138.5,
    widthSource: 'assumed',
    orient: null,
    massG: 24,
    bridgeType: 'pads',
    provenance: 'Meshy image-to-3D, 1.32M tris decimated to 106k; width 138.5 mm is a VISUAL ESTIMATE by the owner of the clear yellow acetate aviator eyeglasses (stated range 135-142 mm, 2026-08-26), not a caliper reading -- the range is wider than the 4 mm band the width verdict grades against, so widthSource stays assumed',
    parts: { temple: [], lens: LENS_NAMES },
  },
  {
    id: 'horizon-amber',
    name: 'Amber Horizon (sunglasses, AI scan)',
    file: 'assets/glasses/horizon-amber.glb',
    realWidthMm: 137.5,
    widthSource: 'assumed',
    orient: null,
    massG: 26,
    bridgeType: 'pads',
    provenance: 'Meshy image-to-3D; width 137.5 mm is a VISUAL ESTIMATE by the owner of the brown tortoiseshell square sunglasses (stated range 135-140 mm, 2026-08-26), not a caliper reading -- the range is wider than the 4 mm band the width verdict grades against, so widthSource stays assumed',
    parts: { temple: [], lens: LENS_NAMES },
  },
  {
    id: 'horizon-sage',
    name: 'Sage Horizon (sunglasses, AI scan)',
    file: 'assets/glasses/horizon-sage.glb',
    realWidthMm: 130,
    widthSource: 'assumed',
    orient: null,
    massG: 26,
    bridgeType: 'pads',
    provenance: 'Meshy image-to-3D, translucent acetate front; width 130 mm is a VISUAL ESTIMATE by the owner of the green transparent round sunglasses (stated range 125-135 mm, 2026-08-26), not a caliper reading -- the range is wider than the 4 mm band the width verdict grades against, so widthSource stays assumed',
    parts: { temple: [], lens: LENS_NAMES },
  },
  {
    id: 'shield-golden',
    name: 'Golden Shield (mirrored sunglasses, AI scan)',
    file: 'assets/glasses/shield-golden.glb',
    realWidthMm: 143,
    widthSource: 'assumed',
    orient: null,
    massG: 30,
    // A wrap, and the row whose comment was wrong twice over. It was recorded
    // here as having "no distinct nose pads at all", with `derivePads` finding
    // "20 + 17 inward faces against a floor of 20" and refusing on the side
    // with too few. Re-measured through the same orient-and-scale pipeline the
    // app uses, it finds **2764 + 2631 inward faces and 804 mm2 of contact** —
    // the LARGEST pad patch in the catalogue. Its pads have always derived; the
    // refusal it actually hit was the temple's, further down `frameFromMesh`.
    //
    // The stage-8 gate that was written on this ("a run where all eleven assets
    // produce pads is a run that failed") therefore never tested anything, and
    // it is not restated here. What is real about this asset is that its arms
    // wrap rather than rest: `earRestSource` is `assumed`, its slope ratio is
    // 3.2-3.4 against the 7 a rest needs, and that IS a falsifiable claim —
    // `tests/asset.test.ts` fails if it ever reads `derived`.
    bridgeType: 'saddle',
    provenance: 'Meshy image-to-3D, 196k tris; a wrap with no pads; width 143 mm is a VISUAL ESTIMATE by the owner of the Black Shield sunglasses (oversized fit) (stated range 140-145+ mm, 2026-08-26), not a caliper reading -- the range is wider than the 4 mm band the width verdict grades against, so widthSource stays assumed',
    parts: { temple: TEMPLE_NAMES, lens: LENS_NAMES },
  },
  {
    id: 'crystal-parts',
    name: 'Crystal acetate (parts scan)',
    file: 'assets/glasses/crystal-parts.glb',
    realWidthMm: ASSUMED_WIDTH_MM,
    widthSource: 'assumed',
    /**
     * Tripo exports this one 42.7 degrees off axis — about 40 of yaw plus 5.3
     * of roll. v1 measured it from the asset's own geometry rather than by eye:
     * the two lens parts are symmetric, so the vector between their centroids
     * IS the width axis, and the vector from the temples' centroid to the
     * lenses' IS the forward axis. An orthonormal basis from those two,
     * inverted, gives this quaternion, and the check that the axes were right is
     * that the residual height difference between the two lenses falls from
     * 0.036 to 0.00000.
     */
    orient: [-0.126799, -0.341476, -0.003033, 0.931293],
    massG: 24,
    bridgeType: 'pads',
    provenance: "Tripo parts scan; orient measured off the asset's lens symmetry (v1); width is the placeholder",
    // Eight parts, all `tripo_part_N`, and the exporter left
    // `KHR_materials_volume` in `extensionsUsed` while declaring it on no
    // material at all. Nothing here names a temple or a lens.
    parts: { temple: [], lens: [] },
  },
  {
    id: 'crystal-lenses',
    name: 'Crystal acetate with lenses (parts scan)',
    file: 'assets/glasses/glasses01-with-lenses.glb',
    realWidthMm: ASSUMED_WIDTH_MM,
    widthSource: 'assumed',
    /**
     * The same rig and the same problem as `crystal-parts`. The width axis is
     * exact — the two lens meshes are symmetric — and which way is UP is the
     * hard half. v1's note is worth carrying because two plausible references
     * both fail: the model's own +Y (every part sits on y = 0, as though the
     * exporter set it on the ground) leaves the front raked at 20.7 degrees, and
     * aiming by the temples' long axis gives 24.7, because a worn temple runs
     * level but this one hooks down at the tip. Up is chosen instead to put the
     * frame front's own plane — the flattest thing in the asset — at 7.2
     * degrees of pantoscopic tilt, the value measured off `crystal-parts.glb`:
     * the same frame on the same rig, so the number is this frame's own.
     *
     * The check is what that constraint does NOT determine: solving it alone
     * brings the bounding box to 140.0 x 45.9 x 141.3 mm against the sister
     * scan's 140.0 x 46.9 x 141.2, and lands within a degree of that scan's
     * quaternion.
     */
    orient: [-0.127356, -0.338112, -0.006472, 0.932426],
    massG: 24,
    bridgeType: 'pads',
    provenance: "Tripo parts scan with lenses; orient measured off the frame front's rake (v1); width is the placeholder",
    // The lenses ARE named (`lens_tripo_part_0/1`, material `LensGlass`); the
    // frame parts are not.
    parts: { temple: [], lens: LENS_NAMES },
  },
  {
    id: 'meshy',
    name: 'Acetate (AI scan)',
    file: 'assets/glasses/meshy-glasses.glb',
    realWidthMm: ASSUMED_WIDTH_MM,
    widthSource: 'assumed',
    orient: null,
    massG: 24,
    bridgeType: 'pads',
    provenance: 'Meshy image-to-3D, one fused mesh; width is the placeholder',
    // One part, 106,246 triangles, no node name, no material name, no
    // extensions. The hard case for every part-based method in this tree, and
    // the reason the lists here are declared rather than matched.
    parts: { temple: [], lens: [] },
  },
];

export function catalogueEntry(id: string): CatalogueEntry | null {
  return CATALOGUE.find((e) => e.id === id) ?? null;
}
