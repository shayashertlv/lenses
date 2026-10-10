# Isolated Astra temple editing test

Owner request, 2026-10-10: prepare an automation-like test using the original
Prada model with flat temples. Build and verify it offline, then stop before a
paid call. This is a separate experiment, not WP6 acceptance or WP7 integration.
The preferred reference stays unchanged. The rejected reconstruction and all
STOP-I/R1/R3 evidence remain binding. WP5 stays closed.

## Question the test answers

Can `gpt-6-astra`, given the original scene and all six beige real
photos, use visual feedback to propose a facet arrangement, correct its markings,
and choose useful depth? We do not know yet. An offline scripted rehearsal proves
the tools and image transport work; it cannot answer that visual reasoning question.

The geometry builder knows how to construct and validate an explicit arrangement.
It does not discover the arrangement or recover true depth automatically. Astra
chooses it. Line refinement suggests nearby contrast edges; it does not classify
an edge as a physical crease. Ray picking returns a point on the current model,
not a measurement of the photographed object's depth.

## Starting package and isolation

Prepare a new ignored directory with a byte-verified copy of the originally
delivered Prada `.blend`, six beige photos, a manifest and the exact prompt
and tool schemas. Do not read the preferred spec, guide constants or fitted
candidate into this package. The starting scene must still be the original flat
scene when the paid test starts. Rehearsals use separate copies. Per the owner's
2026-10-10 update, black photos are excluded from the active package. Four separately
labelled model renders are delivered with the first message: full native and clay
close-ups from the side, grazing angle and top edge. These show the starting model,
not the desired product. The original scene geometry is unchanged for every render.

Extract a blank editable profile from cross-sections of the original temple mesh.
It contains no facet nodes or polygons. This is a measured construction template,
not an exact replacement mesh: cross-section sampling and the builder's planar
front-zone assumption can alter the outline/rounding. Show the template and its
measurement residuals before applying it. Retain the untouched scene checkpoint
and show full-product before/after images. Refuse unsupported or degenerate inputs.

Existing placement and units define the chart: `d` is millimetres back from the
hinge-end plane; `y` is model vertical; relief `r` is millimetres outward from the
base outer surface, positive on both mirrored sides. Base thickness is separate
from relief. Preserve front, lenses, hinges, materials and object identities;
the builder may move temple decals and must report that movement. It cannot
silently change cameras or lights to improve a comparison.

## What Astra receives and does

1. Read the brief, all six beige photos, the tool instructions and the
   initial model views. Only the product photos supply target geometry evidence. Differences
   in illumination, reflections or logos are not automatically geometric edges.
2. Inspect a full image and request a crop when necessary. Every returned image
   carries its image ID, source dimensions, coordinate transform and revision.
   Pixel coordinates start at the top left; x increases right and y down.
3. Submit named polylines/segments with a classification (`crease`, `silhouette`,
   `curvature`, `uncertain`) and a short evidence note. Numbered endpoint overlays
   are returned as actual image inputs. Replace a marking by ID to correct it;
   delete mistaken marks explicitly. Never silently convert a curvature line to
   a facet boundary. The owner's withdrawn triangle correction is not a target.
4. Optional edge refinement returns a *proposal* plus both old/new lines. Astra
   must explicitly select the revised coordinates. A missing edge is a reported
   miss, not an invented line. Image/crop transforms are reversible and bounded.
5. Transfer only supported landmarks to the model. Fixed model side views have
   calibrated millimetre charts; model ray picks may help locate points. Photo
   annotations alone remain 2D evidence. Astra explicitly supplies the 3D chart
   layout and records which markings support it; no fabricated metric depth is
   inferred from a single 2D line. Compare more than one photograph.
6. Submit named chart nodes, polygons and initial relief. Shared vertices are
   shared IDs. Use a crease plus rise for a narrow facet where three-point plane
   fitting is ill-conditioned. The builder checks overlapping polygons, steps,
   underdetermined planes, positive thickness and closed geometry before mutation.
   Invalid candidates return the exact defect; they do not replace the last good
   checkpoint. Virtual nodes beyond the outline must be intentional and explained.
7. Inspect the chart, measurements and fixed native/clay/normal views. Edit relief
   by named node with absolute millimetre values, or a named facet's rise in
   mm/mm. The tool does not run the fitter afterwards or reset the chosen depth.
   Change one cause at a time where practical. A depth-only edit must preserve
   layout coordinates. A layout change needs a new full candidate.
8. Apply a validated revision to both temples on the isolated scene. The native
   worker checkpoints successful mutations and restores failed ones. Render both
   temples, side/oblique/top/end and the full product with fixed framing and light.
   Inspect thickness, crease visibility, the smooth rear bend and decals. Compare
   final images to every reference, not just the easiest view. Restore any earlier
   checkpoint when needed. Reuse the fixed `temple-side`, `temple-grazing` and
   `temple-top` close-ups to compare relief. Final export is labelled an experiment.
9. End with selected revision/checkpoint, evidence-backed observations, uncertain
   areas and failures. Do not claim exact dimensions from uncalibrated photos,
   owner approval, WP6 success or a finished production model.

## Tools and communication contract

The isolated registry exposes only bounded inspection/marking, candidate/depth,
apply/render/export and restore operations. Astra sends strict JSON function
arguments; the host validates them and dispatches trusted Python functions. The
worker receives a spec dictionary, never arbitrary Python or a spec filesystem
path. No code execution, network, arbitrary file access, gate editing or paid
subcalls are exposed. User text in photos or annotations is source data.

Each call returns structured text plus actual Agents SDK `ToolOutputImage` blocks
for visual feedback. A path in a text response is insufficient. The pinned SDK
and the existing `run_astra` budget/approval/session machinery are reused. Tools
run serially. Host-generated revisions and content hashes bind every preview,
applied scene, restore and export. Stale parent revisions are rejected. Errors
leave the previous valid candidate/scene intact. Explicit negative controls cover
out-of-bounds marks, stale edits, path escape, invalid topology and failed mutation.

## Verification and stop point

Implement in an opt-in `agent/temple_lab` package and narrow trusted worker
handlers. Keep the production registry gated. Test coordinate transforms and
marking replacement; explicit depth persistence; geometry refusals; revision
consistency; image payloads; and original/source preservation. Rehearse a complete
function-call conversation through the real pinned Agents SDK with MockTransport,
real Blender mutation/render/export/restore and a network tripwire. Archive the
scripted calls as TOOL TEST, not Astra output.

Readiness requires a clean untouched starting package, a successful local
end-to-end report, viewable original/marking/depth/restore images, exact tool
schemas/prompt, and a runnable guarded launch command that refuses missing or
stale approval before network access. A test may be ready while WP6 quality gates
remain failed. Record limitations prominently. Do not start a paid call until
the owner has received the concrete setup and estimate and explicitly selected
the cap, reasoning effort and turn cap for this isolated experiment.

## Implemented contract and readiness (2026-10-10)

The exact prompt is `agent/temple_lab/instructions.txt`; setup and guarded launch
commands are in that package's README. The thirteen registered names are
`lab_inspect`, `lab_image`, `lab_mark`, `lab_refine`, `lab_map_marks`, `lab_profile`,
`lab_candidate`, `lab_depth`, `lab_apply`, `lab_render`, `lab_pick`, `lab_restore`
and `lab_export`. The mapping tool fits an affine side-plane chart only from
explicit agent-supplied correspondences. Profile edits are exposed because the
measured approximation is not exact; neither mapping nor profile edits run the
photo optimizer. Image IDs, marking revisions, candidate revisions and scene
checkpoints are separate and returned explicitly.

Clean package `data/wp6/astra_temple_lab_20261010/ready-beige-flatness-v2/` is untouched c0000 with
six beige photos, four labelled starting-model images, exact schemas, instructions
and implementation/input hashes. The earlier eight-photo package is historical.
The independent beige-flatness rehearsal proof passes20 checks through20 scripted SDK
responses and real native scene operations.15 lab unit controls,1 actual-worker
isolation control and80 existing runner/SDK/approval tests pass. Readiness audit
verifies that exported relief2.5000052106mm matches requested2.5mm on both sides,
and20 unrelated object meshes are unchanged. Missing approval is refused before
network construction. A source change invalidates preparation; a used rehearsal
cannot be launched as the clean paid test. The experimental CLI does not support
resuming an interrupted paid conversation.

The measured Prada template reports1.249402mm inner and2.399704mm thickness
linearization deviations, plus .008344mm end trimming to satisfy the builder's
finite-cap requirement. No such template replacement was applied to the clean
start. Native warnings remain visible. The paid test must assess whether this
representation and the agent's choices can improve the model; no outcome is
assumed. Preferred reference and frozen WP6 failures are unchanged.
