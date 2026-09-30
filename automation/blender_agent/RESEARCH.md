# Live Blender agent: research and architecture, 2026-09-30

This records the requested GitHub/social research, initializer comparison and AR
compatibility audit. It describes a local prototype and proposed next steps, not a
deployed service or a verified improved model. Existing application and renderer
files were not modified by this experiment.

Subsequent implementation: the [local studio](STUDIO.md) now provides job intake,
progress, recovery and shared native-material controls in the latest AR and 3D
viewers. The historical architecture notes below describe the earlier experiment;
production worker deployment and multi-user service integration remain future work.

## Execution model

One OpenAI Agents SDK session controls one persistent Blender scene through an
existing MCP server. Astra inference runs on OpenAI's service. The SDK, MCP process,
Blender and saved job artifacts run on the modeling worker. Individual model/tool
messages are interactions within that ongoing job, not construction-program rebuilds.

The prototype's worker is this Windows PC. A server deployment would put the same
components on a dedicated modeling worker, with the application submitting queued
jobs and displaying progress. Each concurrent job needs isolated scene/conversation
state. Save both `.blend` checkpoints and SDK conversation state for recovery. The
chosen MCP needs a GUI graphics context for screenshots; a Linux worker can provide
a virtual display. Production queueing, worker deployment, job supervision and UI
integration are not implemented here. The live Lenses/Railway entry point stays intact.

## Existing MCP choices

| Project | Verified capability | Assessment |
| --- | --- | --- |
| [Community MCP for Blender](https://github.com/ahujasid/mcp-for-blender) | Persistent scene edits through Blender Python, viewport images, object inspection, API lookup, native export | Selected for the first connection test; package 2.1.3, commit `60d2a31b4632a7bc178f3dd636f7e68dfb5c8ae4` |
| [Blender Lab MCP](https://www.blender.org/lab/mcp-server/) | Live Python, area screenshots, focus, scene summaries, API/manual lookup, renders; Blender >=5.1 | Strong alternative; distinguish its live tools from its separate headless CLI tools |
| [blend-ai](https://github.com/HoldMyBeer-gg/blend-ai) | Explicit persistent vertex/edge/face selections and mesh operations, materials/modifiers, screenshots/export | Useful alternative if typed selection tools improve precision; advertised sculpt tools do not implement brush strokes, and MCP undo is absent |
| [glonorce/Blender_mcp](https://github.com/glonorce/Blender_mcp) | Multi-angle and object/gap-focused inspection | Useful inspection capabilities, without evidence of superior eyewear outcomes |
| [blender-research-mcp](https://github.com/Haiyang-Bian/blender-research-mcp) | Detailed selection sets, local transforms, distance validation, rollback | Precision-oriented but more restrictive and primarily validated on Blender 4.2.23 |
| [Astra Blender Harness](https://github.com/AleBrito124356/astra-blender-harness) | Existing local studio, upstream MCP, checkpoints and phased refinement | Not selected: custom LiteLLM phase loop, three-reference limit, older MCP pin; its validation used scripted providers, not real paid Astra modeling |

The community server can already edit individual vertices, material inputs and
modifiers. Python is the command mechanism inside Blender, not a requirement to
regenerate the whole model. Scene changes persist; temporary Python variables do not.
No verified Astra-specific eyewear/tiny-object benchmark was found. A connector named
after Astra does not establish better modeling quality.

The standard Agents SDK uses Responses and supports local MCP and image results.
Keep MCP image content forwarding enabled, serialize mutations and avoid automatic
mutation retries. [Official SDK guidance](https://developers.openai.com/api/docs/guides/agents/integrations-observability).

Social evidence supports iteration, but not guaranteed accuracy:

- [Astra hospital scene](https://www.reddit.com/r/OpenAI/comments/1wem6s7/chatgpt_blender_mcp_built_this_from_scratch/): author reports extended construction/detailing/lighting work; not an optical-material benchmark.
- [Detailed tower](https://www.reddit.com/r/TopologyAI/comments/1wm9wab/gpt6_astra_is_directing_the_sculpting_process_in/): reference-based modeling report linking to Blender Lab; no accessible exported-asset benchmark.
- [Critical topology report](https://www.reddit.com/r/OpenAI/comments/1we95z2/blender_mcp_is_impressive_but_not_that_useable_yet/): useful counterexample to universally perfect results.
- [Polished demonstration](https://www.linkedin.com/posts/jerrod-lew_gpt-6-astra-can-create-amazing-3d-models-activity-7502110526278107137-nogZ): includes Magnific post-processing, so final image quality is not proof of mesh quality.

Direct X posts were inaccessible; secondary summaries were not counted as verified
demonstrations. The user's own successful desktop-MCP result in this AR viewer remains
important evidence that a good exported result is possible.

## Images, generated mesh, or hybrid

For the immediate Tom Ford editing experiment, start with the existing native
TF003 continued r0003 `.blend`. That isolates whether the new live agent can improve
the two remaining appearance problems. It does not settle the general initializer.

For the general pipeline, let the agent choose component by component:

| Component | Initial preference | Reason to choose differently |
| --- | --- | --- |
| Smooth lenses | Native editable geometry | Retain generated lenses when complete boundaries, curvature and normals are already useful |
| Crystal frame/temples | Native volume and material | A donor with correct walls, cross-sections and internal structure can still save work |
| Opaque acetate | Compare native and donor | Preserve useful shape/texturing; reconstruct if most contours require correction |
| Complex opaque sports frame | Generated donor is promising | Replace inaccurate regions instead of preserving them for their own sake |
| Shield lens | Inspect generated curvature first | Rebuild if ripples, folds or boundary errors dominate |
| Hardware, logos, temples | Selective donor or independent parts | Rebuild fused, malformed or merely painted details |

A generated mesh saves initial 3D inference when it is good. When it is bad, Astra
must infer the correct product and undo the generator's interpretation. Native
construction has more initial reasoning work but can provide easier independent
edits afterward. Neither is automatically cheaper overall.

Repository evidence: cached Tripo examples had roughly 1.94 million triangles and
connected opaque geometry. Ordinary loose-part separation was insufficient. Later
segmentation did produce editable lenses on the tested products, so lens rebuilding
is not always necessary. It also missed a thin Miu optical strip, showing why selecting
only the obvious two regions is insufficient. Five later products loaded in AR but
were not accepted as production-ready. See the [API comparison](../plan/API_COMPARISON_RESULTS_2026_09_23.md),
[parts experiment](../plan/PARTS_EXPERIMENT_RESULTS_2026_09_23.md) and
[end-to-end results](../plan/SEGMENTED_AR_RESULTS_2026_09_23.md).

Downstream work the initializer comparison must include:

- Find all lens/front/back/edge surfaces and preserve hardware visible through them.
- Distinguish semantic segmentation from actual physical component separation.
- Seat curved lenses in the real three-dimensional frame aperture without gaps.
- Avoid global remeshing that destroys thin rims, holes, UVs or hardware.
- Preserve useful opaque textures; avoid baking photographed reflections/background into transparent parts.
- Maintain component identity through topology changes; old vertex indices can become invalid.
- Separate camera perspective and temple articulation from shape errors.
- Use supplied dimensions; image-derived millimeters are estimates.
- Inspect the exact exported asset in AR, not just Blender.

Transparent eyewear particularly favors physical reconstruction: a painted gold line
inside an opaque generated temple does not become a real embedded wire when the
material is made transparent. Native geometry can still look wrong if its rim section
is flat or uniformly thin, so native construction is not a quality guarantee.

Current provider options warrant future comparison, not assumed success. Tripo
documents [semantic/geometric segmentation](https://developers.tripo3d.ai/en/docs/mesh-segment).
Meshy's single-image API now documents [Smart Topology / meshy-t2](https://docs.meshy.ai/en/api/image-to-3d)
with separated parts and requested 100–15,000 faces; the cached old Meshy trial does
not evaluate it. Its multi-image documentation does not list T2. Meshy's
[Auto Split](https://docs.meshy.ai/en/api/auto-split) can alter cut geometry and textures
for printing, making it a poor automatic default for delicate AR eyewear.

Compare three initializers with the same agent and final evaluation: photos only,
generated mesh repair, and generated guide/selective donor. Include generation,
segmentation, repairs, reasoning, renders, export and human intervention in time/cost.
Test crystal, opaque acetate, rimless and shield/sports examples, with repeated runs.
Measure final quality under matched total budget and total work to an agreed quality
threshold. Record how much donor geometry survives.

## AR audit: actual requirements versus our choices

Ordinary native glTF solids already skip canonical surface validation. The custom
front-sheet path and the custom closed/multipart effective-group path are separate
opt-in representations. The earlier assumption that every lens needs a front-cap
conversion was too restrictive.

A read-only test against current TypeScript confirmed:

1. A closed native lens retained all 12 box triangles and its exact native material.
2. Explicitly tagged crystal frame material was misclassified as optical without a
   canonical descriptor.
3. A 1 mm object translation triggered the fitting code's identity-transform rejection.

The highest-value corrections to evaluate are:

- Honor explicit frame/temple/lens roles even without canonical metadata; preserve
  the legacy heuristic for untagged catalog assets. Current gate:
  [optical-material.ts](../../ar/src/eyewear/optical-material.ts), `classifyAssetMaterials`.
- Normalize valid static glTF transforms on import, preserving world shape,
  materials and UVs. Current fitting rejection: [rear-drop.ts](../../ar/src/render/rear-drop.ts),
  `createRearDrop`. Handle shared geometry and negative scale correctly.
- Keep native materials native. Copying an old canonical descriptor causes the
  custom runtime to replace the lens material, potentially discarding the agent's
  native shader choices. See [lens-material.ts](../../ar/src/render/lens-material.ts).

Finite geometry, valid indices and practical resource bounds remain useful checks.
Front-facing normals, single-valued surfaces and intrinsic vertical UVs are specific
to the front-sheet shader. Limits on mixed optical interfaces and alpha composition
have real purposes within that algorithm; simply deleting their checks can produce
incorrect results. They are not universal browser restrictions.

Native Three transmission uses rasterized screen-space approximations rather than
arbitrary ray tracing, but closed lenses are not inherently rejected or flattened.
Our canonical lens path deliberately samples transmitted content without refraction;
canonical crystal look-through also removes refraction/roughness blur. These choices
can affect apparent depth independently of geometry. Good native output is therefore
plausible without declaring the entire viewer incapable of the desired appearance.

Recommended trial direction: native glTF materials and full lens solids, explicit
part/placement metadata, exact-file AR previews. Keep canonical optics optional.
Verify crystal/hardware, face/hair occlusion, strong side views, nested transforms,
negative scale, mirrors and untagged catalog regressions before adopting changes.
The implementation now honors native role tags and handles opaque native lens
shadows. The full AR suite passes 480 tests. Export copies bake static transforms;
the general AR importer has not been changed to accept arbitrary transforms.

## Correction about the current Tom Ford lenses

The current TF003 continued r0003 lenses are geometrically curved. The old pilot's
`base_curve=0` diagnosis does not describe this revision. Actual exported geometry
has about 26.94 degrees horizontal and 16.94 degrees vertical normal variation,
0.83 mm RMS deviation from a fitted plane and 2.92 mm residual range. Export reported
no clamped normals. The current program uses base curve 4 and retains that sag when
adding horizontal wrap.

The current delivery instead removes the 1.65 mm closed lens thickness, retaining
only the front surface. Its canonical material has zero physical thickness and
undistorted transmitted content. Those are credible contributors to a flat appearance,
along with illumination and reflections; they are not proof of the exact visual cause.
Newer reflection code differs from the saved pose sweep, so a fresh current-runtime
render is needed to separate those effects.

## Verified prototype status

`agent.py` uses standard `Agent`, `Runner.run`, `SQLiteSession` and `MCPServerStdio`.
The dedicated Blender 5.2 session opened a copy of the latest Tom Ford source. Tests
verified persistent scene state, real viewport images delivered to the next SDK input,
saved review images and conversation persistence. Those initial SDK round-trips used
a scripted test model. A subsequent **real paid Astra trial completed** against the
persistent Tom Ford scene, with direct mesh/material edits and actual AR images.
It produced a modest AR improvement and did not reach the photographic quality
target. [The measured result and remaining issues](RESULTS.md) include the artifacts,
integration interventions, verification and approximately $6.10 in inference usage.

The restrictive front-cap adapter draft was removed from the active package and kept
under ignored research files. The implemented native handover retains full evaluated
meshes and native material features, verifies scene invariance, and renders exact GLB
bytes through the actual AR engine. `inspect_export` exposes the material extensions
that survived export. Server deployment is not implemented. A completed conversation
or compatible export does not establish a perfect model.

## Post-scratch-trial research — 2026-09-30

Two additional researchers reviewed public source code, papers, GitHub issues and
firsthand social discussions after the photos-only trial. No verified Astra-specific
backend demonstrated superior reconstruction of transparent eyewear or accurate
submillimetre product details. This is a bounded search finding, not a claim that
no such system exists. Our next improvements therefore retain the standard SDK and
community MCP and target the failures observed in our own run.

### Source-verified implementation ideas

| Source | Verified capability | Relevant use and limit |
| --- | --- | --- |
| [ViSculpt](https://github.com/sig-pku/ViSculpt), [paper](https://arxiv.org/html/2608.24169v1) | Localized visual editing, surface raycasting and real Blender sculpt strokes; Blender 5.1/5.2 support. | Borrow localization and before/after inspection. Its LangGraph/SAM3 system is not verified Astra integration or an eyewear benchmark. The paper reports occlusion/topology limits and geometrically invalid results that can look acceptable. |
| [Blender Agent Bridge](https://github.com/CallMeJones/blender-agent-bridge) | [Object-bounds inspection cameras](https://github.com/CallMeJones/blender-agent-bridge/blob/main/addon/claude_blender/inspection_render.py), multiple views and restoration of render/camera state. | Useful tightly framed, repeatable diagnostics. Its [reference intake](https://github.com/CallMeJones/blender-agent-bridge/blob/main/docs/REFERENCE_IMAGE_INTAKE.md) is limited to clean masks/backgrounds, not arbitrary transparent-photo understanding. |
| [blender-ai-mcp](https://github.com/PatrykIti/blender-ai-mcp) | Scene relationships, measurements, view visibility diagnostics and reference comparisons. | Borrow bounded measurements and visibility checks; do not import its entire workflow router. |
| [Blender Lab MCP](https://github.com/bpy-dev/blender-mcp) | Saved-file headless tools, runtime API lookup and a published benchmark setup. | Possible server deployment mechanics; its benchmark does not give visual judge feedback during generation or prove a quality advantage for our task. |
| [BlenderAlchemy](https://github.com/ianhuang0630/BlenderAlchemyOfficial) | Visual edit-generator/evaluator experiments for geometry, materials and lighting. | Controlled candidate comparison is useful; its script-search framework would replace the live-scene approach the owner requested. |
| [Astra Blender Harness](https://github.com/AleBrito124356/astra-blender-harness) | Backend API clients, upstream MCP, review phases, checkpoints and camera coverage checks. | Its [validation record](https://github.com/AleBrito124356/astra-blender-harness/blob/main/docs/validation.md) says real Blender tests used a scripted provider and no paid model call. This proves integration, not Astra modeling quality. |
| [blender-astra-mcp](https://github.com/mohakmalviya/blender-astra-mcp) | Compact discovery, batched operations and image returns. | Its reported payload savings concern transforms and exclude reasoning/history/images; not reconstruction quality. |

### Evidence for the observation changes

[3DHarnessBench](https://arxiv.org/html/2609.06535v1) found that unrestricted camera
access alone can worsen results: informative framing, useful view diversity and
measurements matter. Its strongest conditions include access to target-mesh
measurements that commerce photographs do not provide, and it does not test Astra.
Our adaptation is to give each inspection a concrete question, clear framing and a
controlled comparison, rather than rewarding the number of screenshots.

[DiffTrans](https://arxiv.org/html/2603.00413v1) treats transparent reconstruction as
coupled geometry, material and environment estimation. It uses calibrated multiview
evidence, masks/environment information and smoothness constraints; it is not a
drop-in solution for six unrelated product photographs. The practical inference is
that image brightness is not surface height. Compare geometry under neutral shading
and through sections, then compare the authored optics under fixed lighting.

[VIGA](https://arxiv.org/html/2601.11109v1) supports active visibility tools,
generator/verifier separation and compact memory of edits and visual feedback.
[Thinking in Blender](https://arxiv.org/html/2606.02580v1) separates reconstruction
factors into verifiable stages and supports reverting unsuccessful edits. Neither
establishes Astra eyewear performance. A short independent review is a candidate for
a future budgeted evaluation; it has not been added as an unmetered second model.

The [bridge evidence-review guidance](https://github.com/CallMeJones/blender-agent-bridge/blob/main/skills/blender-reference-modeling/references/evidence-review.md)
also cautions against mismatched views and treating detail as improved proportions.
Its [context design](https://github.com/CallMeJones/blender-agent-bridge/blob/main/docs/CONTEXT_AND_DOCS_ENGINE.md)
suggests compact persistent facts and bounded outputs. Our exact billing guard stays
authoritative; a rough token estimator does not replace it.

### Social evidence and operational cautions

A firsthand [Astra aircraft project](https://www.reddit.com/r/aigamedev/comments/1wnbn6k/gpt6_astra_ultra_blender_mcp_godot_built_this_3d/)
reports sustained Blender/Godot work but also substantial human direction and a
non-production result. Other firsthand reports describe [poor reference reconstruction](https://www.reddit.com/r/codex/comments/1wizmhg/how_people_get_so_good_3d_models_with_astra/)
and [creation from imagination outperforming photo reproduction](https://www.reddit.com/r/OpenAI/comments/1we95z2/blender_mcp_is_impressive_but_not_that_useable_yet/).
These are anecdotes, not controlled model/platform comparisons. Searches of X did
not yield directly inspectable proof of a superior autonomous Astra backend.

Upstream issue reports include [commands completing after a timeout](https://github.com/ahujasid/blender-mcp/issues/279)
and [Windows/WSL screenshot path mismatches](https://github.com/ahujasid/blender-mcp/issues/189).
They do not prove our pinned setup has the same bugs. They motivate checking live
state after an uncertain mutation rather than blindly repeating it.

### Decisions for this implementation

- Keep `gpt-6-astra`, the standard Runner, native export and the live Blender scene.
- Add native-pixel reference/render crops and concise evidence-linked review notes.
- Require alternative physical explanations for photo lines, purposeful inspections,
  matched before/after checks and rollback of visible regressions.
- Check component profiles and transitions before decorative details. For Tom Ford,
  the full internal temple core needs isolated and assembled inspection.
- Add actual-AR static-face evidence and closeups of the exact exported GLB. Preserve
  source pose and matching conditions; do not invent oblique views of a fixed portrait.
- Keep full diagnostics on disk and return compact tool summaries with images.
- Default future runs to `max` effort, supported by the [official Astra model page](https://developers.openai.com/api/docs/models/gpt-6-astra).
  The prior run used `high`; no quality improvement from `max` has yet been measured.

The [official model guidance](https://developers.openai.com/api/docs/guides/latest-model)
supports explicit task-specific instructions and evaluation. Stronger wording alone
is not evidence of a better model. These changes must be evaluated on a new bounded
run before attributing a reconstruction-quality gain to them.
