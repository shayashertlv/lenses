# Rules that constrain Astra: what to remove, loosen or keep

**Scope/status (2026-10-07):** this is the historical source-code rules audit, not a second current acceptance
specification. PLAN §2's owner-approved development-arview requirements take precedence over §C below. Existing
port decisions are recorded in PORT_REVIEW.md; do not redo them or import superseded restrictions.
Current task rules are AGENTS.md, PLAN §0 and PROGRESS.md; WP6 follows [WP6_FACETS.md](WP6_FACETS.md).

Inventory of 209 rule mentions across the modeler (main), the Blender Studio (branch `codex/perfecto_automation_agentic` plus `stash@{0}`) and the AR runtime, merged into 41 rules. Each was classified against what the AR page actually needs, then checked a second time; the verifier's corrections are applied below.

The audit found that AR supports faceted normals, photo textures on opaque parts and a lens seated into the brow.
The old modelling rules and tools were preventing these behaviours.

## A. Remove or rewrite: these force bad geometry or block the obvious method

| # | Rule | Where | What it does to the model | Change |
|---|---|---|---|---|
| 1 | **Temples must be rounded `tube_along_path` sections, "never square or low-point profiles"**. The two export audits (`faceted_sweep`, `flat_plane_normal_bleed`) also tell Astra to rebuild temples as rounded tubes. | modeler `author.py:61-62`, `agentic/AUTHOR_PROMPT.md:82-84`, `bsa/export.py:84-96, 1229-1245` | Flat or round temples only; the Prada facets are impossible | Drop the shape mandate. Keep only "rounded corners need ≥8 points per 90°" (it prevents a real flicker bug). Allow polygon sections with sharp creases. |
| 2 | **The modeler exporter throws away Astra's normals** and re-smooths everything at 40°. | `modeler/blender/harness.py:310-321`, `modeler/export.py` (no normals passed) | Designed facets and crisp chamfers get blurred, so rule 1 was written to avoid them | Export Blender's own corner normals. |
| 3 | **The helper library can only make round or rectangular sections.** `tube_along_path` is always a rounded rectangle; `sweep_profile` sweeps one constant profile. | `glasses_lib.py:862-885, 808-829` | No faceted, ridged or tapering-section temples | Add polygon sections with sharp edges, per-station sections, and rims with a groove. |
| 4 | **Studio: "Keep unobserved details simple … prioritize silhouette before decorative detail"** and "a band may be a reflection". | stash `blender_agent/instructions.py:49-54` | Astra explained the Prada facets away as lighting | Replace with: "Model every feature a photo shows, at its observed size: facets, ridges, chamfers, the lens groove." |
| 5 | **No photo pixels for textures.** The Studio's only pattern tool paints a two-colour ramp (it never uses photo colour), and the modeler's sandbox gives the program no photos at all. | stash `source_field.py:1-7, 403-406`; modeler `author.py:50-52`, `agentic/executor.py:218-236` | Every tortoiseshell is invented noise | Add the photo-projection tool and mount the photos read-only. Keep the true part of the old rule: no painted shading pretending to be shape. |
| 6 | **Studio never sends your job description to Astra.** It is stored but left out of the prompt. | stash `studio_jobs.py:224, 405-426` | Your written product facts are ignored | Add the description to the prompt as authoritative. |
| 7 | **Clearcoat is silently dropped.** `material_acetate` sets coat 0.4, but the modeler export never writes it. | `glasses_lib.py:1273-1290`, `modeler/export.py:48-87` | Gloss you ask for never reaches AR ("matte, needs shiny") | Export the clearcoat, with a glare check (shiny flat caps glared silver before). |
| 8 | **Materials Astra builds directly in Blender are lost on export** in the modeler: only materials made by the `gl.material_*` helpers survive. | `glasses_lib.py:122-140`, `modeler/export.py:53-54` | Image textures and node patterns vanish | Export any material with an image texture. |
| 9 | **Intake overwrites brow and bottom-rim thickness with one number** for crystal frames, or whenever the thickness reading is untrusted. | `intake_astra.py:516-528, 632-638` | Browline frames get a thin brow equal to the lower rim | Read brow, bottom rim and endpiece separately. |

## B. Loosen: these have a real purpose but block real designs

- **No lens-seating rule anywhere.** The modeler's front is a plate whose holes exactly equal the lens outlines, so lens and rim only touch edge to edge. This is the FT1119 brow problem. Add: "the lens edge runs 0.6–1.0 mm into the rim or brow (acetate groove), or 0.3–0.5 mm behind the metal lip". *(Studio `studio_jobs.py:421`; `AUTHOR_PROMPT.md:115-119`; `glasses_lib.py:504-512, 888-896`)*
- **Nothing ever looks at the lens–brow junction.** Previews are front, quarter and rear only. Add yaw ±30° at pitch ±20° close-ups of the junction to every AR preview.
- **The judges ignore section shape, relief, print and seating.** The evaluator is told "do not penalise cross-section shape". Add these as defects. *(`evaluate.py:62-86`)*
- **Front shape tools:** one rim width all the way round, no browline option. Allow per-side widths and a brow-only front. *(`glasses_lib.py:587-612`)*
- **Side, rear and angled photos are weakened.** The angled photo is held out from Astra by default, and top and rear photos get no fitted camera. Use them all. *(`request.py:34, 50`; `observe.py:42-44`)* **Per your instruction, the generated views count as real photos too**, so the Studio's "generated views are hypotheses" wording goes as well.
- **Studio research filter: "Never infer dimensions, materials, finish or optical properties from photos".** Allow a labelled visual reading from the photos. *(`studio_description.py:166-216`)*
- **Studio output cap: max effort with only 8,192 output tokens per turn.** Raise it for construction turns. *(`studio_jobs.py:545`)*
- **Studio bookkeeping gates** reject work over labels ("supported" vs "assumption", uncited images). Downgrade with a warning instead of rejecting.
- **Studio correction families** have no "section relief" or "lens seating" category, and cap alternatives at 4+4. Add both categories.
- **Studio safe mode bans numpy.** This doesn't block projection (Blender's own bake works), but it does block dense sculpting. Allow numpy without file I/O.
- **"Do not import donor models"**: allow a provider mesh of the same product as a hidden measuring reference, never as delivered geometry.
- **Moving geometry is refused if it changes UVs** (Studio). Accept UV changes caused only by the geometry edit, and warn on stretch.
- **Front wrap:** the only helper wraps around a vertical cylinder, and there is no unwrap helper. Add one.
- **Evidence downsampled to 64/48 points; temples measured from silhouettes only.** Add measured temple top and bottom profiles from the side photo and temple width from the top photo.

## C. Historical live-site requirements (not development-arview gates)

These describe the old live runtime. For development arview, PLAN §2 supersedes limits on mesh naming/count,
opacity, triangles, texture size and file size. Keep applicable coordinate/format validity checks. Live-site
compatibility is reported as a warning; this list does not authorize changes to `ar/` or the root app.

- GLB in metres, +Z toward the viewer, +Y up, baked transforms, origin at the bridge underside.
- Lenses: a separate single-material mesh with partRole `lens` (front-sheet for the modeler; closed solids are fine in the Studio). **Seat it by extending the sheet behind the rim, not by curling its edge.**
- At least one lens and one opaque part. Frame materials must be fully OPAQUE: photo textures are fine, but never alpha-blended.
- Temples need opaque geometry beyond ±45 mm sideways and reaching back past the hinge. Any section shape is fine.
- Plain GLTF: no Draco, KTX2 or meshopt; PNG/JPEG textures up to 2048 px; at most 100k triangles and 8 MB.
- Studio part-visibility tags: on every mesh or none.

## D. Reasonable guards (keep, maybe reword)

These are review candidates from the old code, not blanket hard requirements. Retain only guards justified by
the owner's spec and the implemented, tested tool contract; do not add arbitrary WP5 restrictions from this list.

Closed-manifold parts, the triangle budget, lens base curve, colour from photos not the product name, small branding stays small, part naming, the Docker sandbox, and "room reflections on lenses are not defects".
