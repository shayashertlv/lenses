# Plan: widen what the modeler can build (2026-09-27)

Two limits keep the modeler from handling every kind of glasses: (1) rules the live AR runtime imposes on every
asset, inherited through the export contract, and (2) the constructions the Blender helper library can express.
This plan is grounded in a line-level reading of `ar/src`, `bsa/export.py`, `bsa/contract.py` and
`modeler/blender/glasses_lib.py` (three read-only investigations, 2026-09-27; line numbers cite the working tree,
which carries uncommitted edits in continuity.ts, catalog.ts, external.ts, rear-drop.ts and temple-clip.ts).
Nothing below is implemented yet. Every runtime item is proposed as a change to `ar/src` in the working tree
with tests and a QA page; publishing to the live `/ar/` route stays a separate owner decision.

## 0. Facts that change the plan

* **Lens identity is one material test.** `isOpticalMaterial` (ar/src/eyewear/optical-material.ts:12-15): a
  material is a lens if it carries a valid `LENSES_lens_appearance` descriptor OR is a MeshPhysicalMaterial with
  transmission > 0. Nine runtime modules consume it; no node name or partRole is consulted anywhere. The two
  shipped catalog frames (amber-horizon.glb, tom-ford-clear.glb) carry no descriptor and no partRole: they rely
  entirely on the transmission fallback, are single-node double-sided meshes, and are SHA-pinned
  (continuity.ts:15-18). Every BSA/modeler export carries descriptors on lens materials and `partRole` extras on
  every node and canonical mesh (bsa/export.py:884-891).
* **A transmissive frame is not merely "treated as a lens"; it is refused or unviewable.** Canonical assets are
  refused at export (export.py:841-843, contract.py:215-220) and at load (lens-material.ts:223 "mixed canonical
  and legacy optical interfaces"); transmissive TEMPLES also break rear-drop (rear-drop.ts:151) so the model
  cannot be viewed at all. Alpha blending is worse, not better: canonical load refuses translucent frame
  materials (lens-material.ts:224), rear-drop treats transparent vertices as fixed and throws without an opaque
  shaft (rear-drop.ts:129,194), temple-visibility throws without an opaque frame (temple-visibility.ts:141),
  shadows cast at full strength (eyewear-shadow.ts:261), and per-object sorting self-overlaps.
* **Three's transmission prepass is currently off for canonical assets** (lens-layers.ts:67 sets installed
  canonical materials to transmission 0). Any transmissive non-lens material brings it back once per render
  call, including in stencil pass B where scene.background is null (renderer.ts:734), and DoubleSide transmissive
  objects get their back faces re-rendered into the prepass (three.module.js:18048-18060), the tint-compounding
  the exporter removes from lenses.
* **The continuity model has no thickness threshold.** At 33 z-planes from (lens rear − 15 mm) to the clip depth
  it needs ONE opaque, non-transparent edge crossing per side with |x| > 45 mm (continuity.ts:103-123). A 1.5 mm
  wire and a 1.3 mm hexagonal tube pass (probe through the real code). What fails: an arm that does not reach the
  clip plane, a rearmost section wholly inside |x| ≤ 45 mm (a tip curling inward), a hinge gap ≥ the 3.6 mm
  station pitch, a folded arm, transparent temples, a lens rear within 15 mm of the clip. A continuity failure is
  non-fatal: the clip falls back to `templeEndMaximumZM = clip + 25 mm` and only the hair-endpoint tracker is
  lost (renderer.ts:73,275-285,378-379). The claim in evaluate.py:233 that it "cuts the temples short" is wrong.
* **The clip depth is per asset in the runtime but fixed at −0.14 m everywhere the automation feeds it**:
  the archeck harness page (provider-comparison-ar.html:64), bsa/tryon.py:27, modeler/job.py:534 and
  modeler/tryon.py:48. `gl.set_temple_clip_z` lands in materials.json declarations and is read by nothing.
* **Lens front sheets are the runtime's canonical contract**, not a producer whim: validateCanonicalLensSurface
  rejects any vertex normal with nz ≤ 0 and any backward triangle (lens-material.ts:61,78), optical-topology
  rejects XY self-overlap (:150), and the far-to-near peel multiplies T once per optical surface with a hard cap
  of 4 layers (lens-layers.ts:15,128-145). The M0 streaks came from face-averaged normals, not from the solid.
* **The GLB writer already has a frosted lens edge ring** for clear lenses (`split_edge_ring`, export.py:265-382,
  814-837, used by BSA S8) that the modeler never wires (modeler/export.py sets no `edge_ring`).
* **The contract judges watertightness on the merged part after welding at 1 µm** (contract.py:119-150,
  257-282). Verified by running it: disjoint or interpenetrating closed shells in one part pass; only shells
  touching with coincident vertices fail (non-manifold / misoriented). Booleans are not needed for the contract.
* **The 45 mm lateral rule exists in five copies plus three GLSL literals** pinned equal by a test
  (face-width.test.ts:180-182); lowering it would pull nose pads and bridge hardware into arm treatment.

## 1. Runtime rules

### 1A. Lens identity and translucent frames

Goal: crystal, translucent and laminated frames render as such; nothing changes for the two shipped assets or
for any existing export.

**Mechanism (all options reduce to it).** A classification pass over the loaded asset that tags each material's
role before the consumers run, and `isOpticalMaterial` consulting the tag before the transmission test. The tag
source is the `partRole` extras the exporter already writes (mesh extras on canonical lens meshes, node extras
on every node; GLTFLoader puts node extras on the Mesh for single-primitive nodes and on a Group otherwise, so
the pass walks `object.parent`). Rule: when an asset carries ANY descriptor, materials used only by non-lens
parts are tagged `frame` and are never optical, whatever their transmission; assets with no descriptor keep the
transmission fallback unchanged. The same pass must run in `loadTempleContinuityModel`, which parses the bytes a
second time (continuity.ts:144-147). A material shared by a lens mesh and a non-lens mesh throws (the exporter
already duplicates shared materials, so BSA/modeler output never trips it).

**Runtime changes, in order.**

1. `optical-material.ts`: `classifyAssetMaterials(root)` returning a WeakMap material → role; `isOpticalMaterial`
   checks the map first. Tests: tagged transmissive frame → false; untagged transmissive → true (shipped path);
   descriptor without transmission → true; malformed descriptor still throws.
2. `lens-material.ts:223-225`: replace the "mixed canonical and legacy" and "translucent frame" throws with the
   tag: a canonical asset may carry transmissive OR alpha-blended frame materials once tagged. lens-material.test.ts:73-82
   is rewritten to assert the new truth table.
3. `renderer.ts:733-736`: stencil pass B hides every transmissive non-lens mesh too (or draws it only in pass A),
   otherwise its transmitted background is the clear colour, not the camera.
4. `eyewear-shadow.ts:197-269`: a transmissive non-lens caster gets a lighter shadow (reuse
   `lensShadowTransmission` with the frame alpha label), not the opaque 16 % caster.
5. `temple-visibility.ts:288-291, 308`: the overlay clone of a transmissive frame material must not inherit
   transmission (it enters the transmissive list with renderOrder 1 and re-triggers the prepass); mark the prepass
   as lens input as today.
6. Producer side, in step: contract.py:114-116/215 and export.py:841-843 change from "frame/temple must be
   opaque" to "frame/temple must not carry a descriptor and must be single-sided when transmissive"; the writer
   forces `doubleSided:false` on transmissive frames (the same back-face compounding it removes from lenses);
   glasses_lib `material_acetate(translucency=...)`, `material_pbr` docstring, author rule 44, DESIGN.md prose.
7. A QA page (`ar/qa/translucent-frame.html`) rendering a synthetic crystal frame with canonical lenses in the
   real renderer, plus an archeck fixture, because no harness renders a translucent frame today
   (README.md:118-121 lists it as unsupported).

**What this does not solve, and must be said to the owner:** temple-clip and hair-occlusion blend camera RGB over
a fragment that already transmitted the camera (double background) on translucent temples; the fix is a shader
change in those wrappers, which I would schedule only after the front-only case is accepted in the live mirror.
Recommended first delivery: translucent FRONTS with opaque temples (the common catalog case for crystal acetate
is a translucent front with matching temples; the temples come second).

**Risk:** medium. Shipped assets are unaffected by construction (no descriptor → old path); provider-comparison.mjs
source pins change (provenance only). The rendering outcome is unverified until the QA page exists; the owner's
verdict on a real crystal product decides.

**Not doing:** alpha blending as the translucency mechanism (see facts); a URL flag (the interpretation would
live in the link, not in the SHA-pinned bytes, and a stale link hard-fails at rear-drop.ts:151); node-name rules
(names are the weakest contract; partRole is already required on canonical meshes).

### 1B. Lens edges and thickness

Goal: rimless and semi-rimless lenses show a polished or frosted edge; lens thickness reads at the rim.

1. **Wire the existing frosted edge ring** (no runtime change, low risk): `gl.lens_edge_ring(lens, width_mm,
   transmission, base, roughness)` records a declaration on the lens object; modeler/export.py `assemble` sets
   `parts[lens]["edge_ring"] = {"width_mm", "material"}` with a canonical descriptor from
   `bsa.lens.edge_ring_appearance`; bsa.export cuts the front sheet in place and emits the band as a second
   canonical primitive with the silhouette unchanged. Consequence: `optical_meshes_detected` counts two meshes per
   lens (the incumbent rule uses ≥ 1, unaffected). Limit: it is a 0.3-0.5 mm band on the front face, not
   thickness; it does not appear at the lens edge under yaw. Requires the lens object without vertex colours and
   with 2-D or no UVs (assemble already drops UVs).
2. **Opaque wall band as a frame component** (no runtime change, low risk, appearance caveat): `gl.lens_edge_wall`
   registers the wall quads of `lens_solid` as a closed thin ring (small radial depth so it is a 2-manifold, no
   coincident vertices with the frame shell) in the `frame` part with a light-grey matte material. The runtime
   treats it as frame: hair/clip hooks are inert at z > −0.02 / −0.09, it sits inside `opticalBounds` (protected,
   not spread), casts the 16 % frame shadow. Caveat: on a rimless product a grey band can read as a thin rim; use
   only where the product shows a polished edge, and keep it ≤ 1 mm.
3. **Not doing:** relaxing front_sheet_v1 (every extra surface under a pixel is another peel layer, T applied
   twice, overflow at the fifth layer; breaks lens-material.test.ts:48-62 and the runtime contract); the
   effective-group profile for thickness (documented experiment, ~45 MiB, no thickness transport, wall renders
   as lens at grazing incidence); the legacy solid (loses every canonical optic incl. the angular mirror table).

### 1C. Temple continuity and clip depth

Goal: short, curled and cable temples are not rejected for reasons the wearer never sees.

1. **Per-asset clip depth through the automation** (no runtime change, low risk): compute the clip from the
   asset (rearmost opaque z at |x| > 45 mm minus a 5 mm margin, clamped to [−0.2, −0.045]) in modeler/export.py
   at export time; write it into the archeck manifest per case (archeck.py:68, the harness passes unknown case
   keys through; provider-comparison-ar.html:64 reads `entry.clip_zm ?? -.14`), into the job manifest
   (`mounting.temple_clip_z_m_recommended`, the handover string) and into modeler/tryon.py and bsa/tryon.py.
   Consequence to state: a shallow clip (e.g. −0.105) draws the arm only to clip + 25 mm with a 5 mm dissolve and
   disables the inward terminal return, which needs clip ≤ about −0.13 (temple-terminal-fit.ts:52-54); the gate
   changes meaning from "reaches −0.14" to "reaches its own declared end". Stored verdict rows keep their
   meaning (their assets reach −0.14).
2. **Offline continuity prediction in the contract** (low risk): arm reach per side vs the intended clip, and no
   z-gap ≥ 3.6 mm at |x| > 45 mm between (lens rear − 15 mm) and the clip; reported as a check so the author sees
   the reason without the harness. New failing check rejects the same assets earlier, nothing else.
3. **Fix the evaluator's runtime note** (evaluate.py:233): a continuity failure disables the hair-endpoint
   tracker; it does not cut the temple. Protocol v2.3 (text change recorded; stored answers stay v2.2).
4. **Runtime, later, medium risk:** generate continuity stations only down to the drawn end (templeEndMaximumZM)
   when the arm is shorter than the clip, keeping `model.cutoffZM` for the spread ramp so centrelines stay on the
   drawn shaft (continuity.ts:171, face-width.ts:273-280, rear-drop.ts:237-239). Shipped assets reach past
   their caps, so continuity.test.ts:36,50,90,94 stay green if trimming applies only to short arms.
5. **Not doing:** lowering the 45 mm constant (five copies, three GLSL literals, pinned by a test; pulls nasal
   hardware into arm treatment; only helps fronts under ~92 mm); sampling stations along the arm's path (every
   consumer, including the clip and hair shaders, cuts on original z); trimming `model.cutoffZM` itself (breaks
   temple-terminal-fit.test.ts:165 and face-width.test.ts:393-517 and throws in the endpoint tracker).

Also correct the claim I made earlier: thin wire temples are NOT rejected by the runtime; the failures are
reach, inward tips, hinge gaps, folds and transparency.

## 2. Helper library

Doctrine first, because it removes the most failed turns: **no booleans.** The contract passes overlapping closed
shells in one part; only exact coincident contacts fail. Rule for the author: build fronts, endpieces, hinges and
pads as interpenetrating closed solids, offset any face-to-face contact by ≥ 0.05 mm, and never call `gl.boolean`
on bevelled plates (Blender's exact union produced non-manifold fronts twice, two paid turns). `gl.boolean` stays
for cutters only and warns in its docstring.

| helper | what it builds | design | tests |
|---|---|---|---|
| `gl.rounded_plate(outer, holes, z_front, thickness, edge_round, name, part)` | the classic acetate front with a convex moulded section (the VB "planar section" complaint) | `plate_with_holes` + bevel clamped to 0.45·min(thickness, rim width), 4 segments, harden normals, optional one level of subdivision with creased hole rings; guard: refuse when the rim between two holes is thinner than 2·edge_round | closed, outward, no degenerate faces at export; section profile measured on a rim cut |
| `gl.unwrap_outline(outline_mm, radius)` and `wrap_cylinder(..., outline_source="photo")` | face-form wrap that still matches the front photo | the front photo shows the PROJECTION of a wrapped front; map x_proj → x' = R·asin(x_proj/R) before building so that after `wrap_cylinder(R)` the silhouette projects back onto the measured outline; document the identity in the rules | wrap then project: outline error < 0.2 mm for R = 88 and 130 mm |
| `path_frames(..., frames="parallel")` (default) | curled and cable temples, pad arms with vertical runs | rotation-minimising frames: propagate the side vector by projecting the previous side onto the plane ⟂ the new tangent; `frames="fixed_up"` keeps today's behaviour; `tube_along_path` and open `sweep_profile` use it | a helical path keeps constant section size (today it collapses); existing temple test unchanged |
| `gl.bevel` thin-part guard | bevel on thin geometry produced degenerate faces the exporter cannot repair on closed shells | clamp width to 0.4·(smallest bbox extent), note when clamped | box 2 mm thick with 1 mm bevel exports closed |
| `gl.material_pattern(name, kind, colours, scale_mm, seed)` + `gl.assign_pattern(obj, material, axis)` | tortoise, marble, laminated stripes as textures instead of faceted palettes | generate a 1024² PNG in numpy (smoothed noise thresholded into blobs with soft edges; stripes for laminates), `material_pbr(texture_path=...)` with planar UVs (`uv_planar`); the export path already writes `base_color_texture` (contract: ≤ 2048 px, JPEG/PNG) | texture reaches the GLB, contract passes, lens colour metric unaffected |
| `gl.lens_edge_ring(lens, width_mm, ...)` | rimless edge (1B.1) | declaration on the object → `edge_ring` in assemble | round trip into two canonical primitives; silhouette unchanged |
| `gl.lens_edge_wall(lens, depth_mm, material)` | polished lens edge (1B.2) | closed ring from the lens_solid wall quads, frame part | closed, no coincident vertices with the sheet |
| `gl.shield_lens(outline, wrap_radius, sag_fn, ...)` | wrap shields with a nose notch | the construction both shield authors wrote by hand (lens_solid + z displacement), as one call with the wrap kept ≤ 6-base and the notch cut before lifting | contract front-sheet rule passes; nz ≥ 0.02 everywhere |
| `gl.set_temple_clip_z` | per-asset clip (1C.1) | recommendation flows to the manifest/handover/archeck instead of dying in declarations | dry run shows the value in the manifest and the harness manifest |

Author-facing changes: the rules gain the no-boolean doctrine, the wrap identity, the contract limits already
added, and one line per new helper; `helper_reference` picks the signatures up automatically.

## 3. Order, effort, decisions

1. **Now, no runtime change, low risk (about a day):** 1B.1 edge ring, 1C.1 per-asset clip, 1C.2 contract
   prediction, 1C.3 note, all helpers in section 2, tests, a seeded dry run per new helper.
2. **Then, runtime, medium risk (two to three days plus your live verdicts):** 1A steps 1-7 on a branch of the
   AR working tree with the QA page; first delivery translucent fronts with opaque temples; the two shipped
   assets byte-identical; `cd ar && npm test` green plus the QA harness; owner check on a real crystal product.
3. **Later, runtime, medium:** 1C.4 station trimming for short arms; translucent temples (wrapper shader fix).

Decisions that are yours: whether runtime changes may land in the AR working tree at all (the task said the
production deployment stays untouched; the working tree already carries uncommitted AR edits), and which crystal
or translucent product to use as the first live test.
