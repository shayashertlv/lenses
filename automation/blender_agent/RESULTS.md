# Tom Ford photos-only trial — 2026-09-30

## Post-run audit correction: the final rim has a geometry regression

**The initial recommendation to keep the latest refinement missed a real surface
regression.** The selected model is AR-compatible and its front is watertight, but
those checks did not establish smooth acetate geometry. The first pass already had
fine cap defects; the later subdivision and crown amplified them. The assessment
below that rim definition improved describes the earlier whole-model AR review,
not an endorsement of the final surface quality. This audit did not change or
replace the selected scene or GLB.

The audit opened saved checkpoints in separate background Blender processes. Three
close-ups use the same opaque grey material, camera, softbox and automatic smooth
normals; inherited custom normals, transparent materials and vertex tint therefore
cannot explain the comparison. Source SHA-256 values were unchanged:

- [Before refinement: refine-01](../data/blender_agent/tomford-scratch-001/audit-20260930/frame-neutral-before.png).
- [After SIMPLE subdivision: refine-02](../data/blender_agent/tomford-scratch-001/audit-20260930/frame-neutral-simple.png).
- [Final crowned surface: refine-05](../data/blender_agent/tomford-scratch-001/audit-20260930/frame-neutral-final.png).

At the same interior top-rim locations, `x=45, y=9.8` and `x=45, y=9.9`, raycasts
find a 0.0133 mm change in wrap-corrected front depth before refinement, versus
0.6083 mm in the final mesh over that 0.1 mm interval. These points remain inside
the rim's broad front surface, not across the lens opening. The object transform is
identity and the scene scale is 0.001 metres per numeric unit, so these are numeric
millimetres. The [comparison receipt](../data/blender_agent/tomford-scratch-001/audit-20260930/controlled-frame-comparison.json)
records full sections, raw hit positions, geometric normals and polygon IDs.
The [domain check](../data/blender_agent/tomford-scratch-001/audit-20260930/raycast-domain-check.json)
puts both sample points inside the outer boundary and outside both lens holes,
with at least 1.77 mm to the nearest recorded boundary loop.

Independent geometric-normal checks used Blender's rendered triangle tessellation
for every checkpoint, avoiding the misleading comparison of n-gon normals with
subdivided face normals. Among adjacent front-facing triangles outside the bridge,
the count with an angle above 60 degrees was 79/21,093 before, 747/86,433 after
SIMPLE subdivision, and 999/83,747 in the final mesh. These are diagnostic counts,
not a universal acceptance threshold. The [triangle comparison](../data/blender_agent/tomford-scratch-001/audit-20260930/triangulated-normal-comparison.json)
and opaque close-ups establish a geometric problem independently of reflections.

The second invocation first reported a mixed cap mesh containing 3-, 4-, 5-, 7-,
9-, 13- and 17-sided faces. It copied that topology to remove custom normals and
applied SIMPLE subdivision. Its crown then moved only vertices satisfying
`abs(local_depth - 2.65) < 0.025`; nearby samples outside that band stayed low.
This amplified grooves into ridges instead of producing a continuous crown.
The exact actions are in the
[resume events](../data/blender_agent/tomford-scratch-001/agent/runs/20260930T112741-e24182/events.jsonl),
lines 12–13, 28–33. SIMPLE subdivision is not inherently wrong; applying it to this
irregular cap and selectively displacing its samples was the failure here.

The final frame has no custom normals, no remaining modifier and no reflection
image or bump map: its material links a vertex-color attribute to Principled base
color. Thus the evidence does **not** support saying Astra literally copied photo
streaks into reflection textures. Some bands still are ordinary reflection or
refraction; the opaque comparison establishes that others originate in actual
surface irregularity. A lighting-only interpretation was too broad.

The internal temple metal has a separate shape mismatch. In the side and oblique
reference photos, the broad patterned hinge region narrows into a thin continuous
core with a shaped transition. Astra authored a constant 0.66 by 0.96 mm rounded
rectangular wire along the acetate path and a separate 1.15 by 2.7 by 19 mm beveled
box paddle. It later added diamond-line geometry and lettering without revising
that basic form. Those construction facts remain visible in the final scene;
the precise real core cross-section is still uncertain from photographs alone.
See [first-invocation events](../data/blender_agent/tomford-scratch-001/agent/runs/20260930T104052-5da992/events.jsonl),
lines 64–69 and 176–177, and the
[hinge close-up](../data/blender_agent/tomford-scratch-001/agent/review-06-hinge.png).
The agent opened all six full references early, but the record has no later
matched reference/model close-up comparing the paddle, transition, stem and tip.
Fine engraving consumed attention before the larger component form was verified.

The edit sequence clarifies what helped and what did not:

| Edit | Intended change | Evidence from the saved result |
| --- | --- | --- |
| Initial extruded photo contours and bevel | Rounded crystal rim | The first bevel nearly collapsed locally; the front remained broad and flat-looking. |
| Unclamped 1.05 mm bevel, first-run line 108 | Restore a full radius | [Review 03](../data/blender_agent/tomford-scratch-001/agent/review-03-rounded.png) became dramatically jagged. |
| Native rounded curve profile, line 116 | Replace the failed bevel construction | [Review 04](../data/blender_agent/tomford-scratch-001/agent/review-04-swept.png) removed the worst jaggedness but retained fine rim bands. |
| Long-edge subdivision and analytic custom normals, line 156 | Smooth the bent front | Close-up bands remained; smooth shading did not establish smooth geometry. |
| New rolled lens edges and lower crystal roughness, line 144 | Improve optical edges | Improved the separate lens construction; did not repair the frame cap. |
| Crosshatching and lettering, line 176 | Refine manufacturing details | Added detail to the still simplified box-paddle/wire core. |
| World/camera/lamp changes, lines 200–240 | Recover photographic edge contrast | Same geometry looked very different; these were inspection-lighting experiments, not product-shape corrections. |
| Attempted 0.34 mm crown, line 244 | Round the broad cap | Blender crashed; recovery reported `crown None`. This edit and the unsaved lighting studies were not retained. |
| Lower lens specular, resume line 16 | Reduce AR hotspots | Matched AR views improved lens glare; useful pre-regression checkpoint `refine-01`. |
| SIMPLE subdivision and selective 0.58 mm crown, resume lines 28–32 | Add rim curvature | Neutral close-ups and sections verify worsened corrugation. |
| Vertex edge tint and lens-density colors, resume lines 48–56 | Improve AR depth cues | Material contrast changed; the damaged frame geometry stayed unchanged. |

The [structured edit timeline](../data/blender_agent/tomford-scratch-001/audit-20260930/edit-timeline.json)
includes exact event lines, intended outcomes, visible outcomes, safe-mode
rejections, crash recovery and before/after image paths.

For the next run, the review loop needs a way to separate physical form from
lighting: matched opaque/normal diagnostic close-ups after substantial geometry
edits, followed by the optical material in both Blender and AR. Surface continuity,
cross-sections and local normal changes matter alongside boundary counts. The
agent should compare each important component against reference crops and record
which features are physical boundaries, uncertain interior structure or moving
highlights. A saved edit should remain provisional until the targeted defect and
unrelated features have been compared at usable scale; a later checkpoint is not
automatically the best one. These are inspection and evidence improvements, not
a fixed modeling recipe or a reason to replace the agent's direct editing tools.

## Implemented observation changes and offline validation

The revised runner adds native-resolution reference/render crops, source-linked
visual review notes, compact AR tool responses, and `preview_portrait` for an
existing exact GLB. Shared instructions require alternative physical explanations,
matched geometry/material checks, major component form before decoration, and
rollback of visual regressions. Future runs default to `max` reasoning. The original
trial remains a `high`-effort result; these changes have not had a new paid Astra run.

The new portrait harness renders the existing first-pass and selected final exports
through the actual AR detector, fitting, hair, shadows and renderer on the checked-in
synthetic face. Both passed, with the same camera/pose/crop and runtime-source
comparison key. It preserves 1024 by 1024 native resolution and returns 768 by 376
eye crops under normal runtime and a fixed diagnostic illumination condition.
Only neutral condition labels/images go to the agent; numerical lighting details
remain in the host report. It does not invent another pose of the static face.

- [First-pass eye detail](../data/blender_agent/tomford-scratch-001/portrait-preview-first-v2/condition-a-eye-detail.png).
- [Final eye detail](../data/blender_agent/tomford-scratch-001/portrait-preview-final-v2/condition-a-eye-detail.png).
- [Final full portrait](../data/blender_agent/tomford-scratch-001/portrait-preview-final-v2/condition-a-portrait.png).
- [Final second illumination](../data/blender_agent/tomford-scratch-001/portrait-preview-final-v2/condition-b-eye-detail.png).
- [Validation receipt](../data/blender_agent/tomford-scratch-001/audit-20260930/algorithm-validation.json).

The face-backed comparison makes the later rim ridges, lens blur and transmitted
detail easier to inspect than the prior blank-background thumbnails. It does not
make the final geometry acceptable, replace unclipped temple inspection, or
establish the renderer's lighting as the lighting of the source product photos.

Validation passed: **72 focused Python tests, 41 unittest subtests, 3 portrait QA
JavaScript tests, and both exact-GLB browser captures**. The prior run had nine
`preview_ar` calls, one rejected for invalid rear-view angles and eight successful
captures covering 23 views. All used the old 720 by 480 synthetic-background path.
One representative response shrank from 18,370 to 1,634 JSON characters while
retaining image outputs and full disk diagnostics. Context had grown from 15,667
to 197,395 input tokens in the paid trial; the largest output was only 3,410 tokens,
below the 8,192 ceiling. These measurements motivate better evidence density, not
a claim that raising output limits would have fixed the reconstruction.

The [additional research](RESEARCH.md#post-scratch-trial-research--2026-09-30)
separates source-verified capabilities from anecdotal Astra results. No production
renderer or deployment changed in this follow-up, and no new inference was purchased.

---

The second trial built Tom Ford FT1123-D 26E 49 from an empty Blender scene and
six original product photographs. No donor mesh or previous model was supplied.
Backend `gpt-6-astra` made all product geometry and material edits through community
Blender MCP, using one persistent SDK conversation across two invocations.

**A reviewable, AR-compatible model was produced; photographic fidelity was not
reached.** The refinement softened lens reflections and improved rim/bridge edge
definition. In AR, broad crystal surfaces remain pale/milky, oblique lenses appear
too uniformly brown, and embedded temple cores are less visible than in the photos.
The lenses are geometrically curved: front-surface residual depth after removing
overall tilt is 3.30 mm on the right and 3.28 mm on the left. Geometric curvature
alone did not produce the desired appearance in the AR renderer.

## From-zero readiness and execution

Before the trial, the runner gained explicit factory-empty startup, durable empty
checkpoint/initialization receipts, a total budget shared across resumes, an OS
process lock, and automatic stop checkpoints while MCP remains connected. Native
export now handles ordinary beveled curves and collection render visibility.
The focused checks passed: **61 Python tests plus 41 unittest subtests**, including
real Blender empty save/reopen, native scratch export, budget locking and stop
lifecycle coverage. The live initial scene had zero objects and geometry datablocks.

The first invocation completed 75 responses. A focused continuation used another
16 responses. Total estimated inference cost from reported token usage is
**$14.418763 against the shared $17 ceiling**, with no unresolved reservations.
The next request required a conservative $2.883663 reservation; only $2.581237
remained, so the guard stopped before making that request. This is usage accounting,
not a provider invoice reconciliation.

One native Blender crash occurred during Astra's mesh subdivision. The host restored
Astra's own saved checkpoint in the dedicated Blender session; the SDK conversation
and total budget continued unchanged. The host then supplied an AR-focused refinement
brief and information about the failed operation. Astra chose and performed all
subsequent geometry/material changes. This was an assisted integration trial, not
an uninterrupted autonomous benchmark.

## Selected scratch artifacts

The selected checkpoint is `refine-05-balanced-optics.blend`, copied unchanged to
the final delivery folder. The earlier `agent/scene.blend` belongs to the first pass
and is not the selected final model.

- [Local AR review](http://127.0.0.1:8797/) (requires the review server and local AR app).
- [Final Blender scene](../data/blender_agent/tomford-scratch-001/final/scene.blend).
- [Final GLB](../data/blender_agent/tomford-scratch-001/final/model.glb).
- [Actual AR front](../data/blender_agent/tomford-scratch-001/final/actual-ar-front.png)
  and [oblique view](../data/blender_agent/tomford-scratch-001/final/actual-ar-angle.png).
- [Saved-scene Blender render](../data/blender_agent/tomford-scratch-001/final/blender-render.png).
- [Identity, cost and validation receipt](../data/blender_agent/tomford-scratch-001/trial-result.json).

Final GLB SHA-256:
`f4bab3b64311ee1c6cb9d7c71612f5ff45f07668a6986ceaaf9fb07fa83e8b2a`.
It is 10,066,976 bytes and retains vertex colors and native transmission, volume,
specular and IOR extensions. Exact exported bytes passed the actual AR harness in
front and both oblique views. This is not a real-wearer recording or a phone
performance benchmark; the unoptimized asset is relatively heavy.

The main front mesh has 118,226 vertices and 236,456 triangles, with zero boundary
or nonmanifold edges before and after position welding. Both lens solids have zero
boundary/nonmanifold edges after welding coincident seams. Four tiny frame hardware
details have coincident-edge degeneracies after welding, without open boundaries.
These checks establish specific topology properties, not manufacturing readiness.

All private artifacts are under ignored `data/`. No production publish ran. To
restart this trial's review page with the local AR app already on port 8240:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.review --glb data/blender_agent/tomford-scratch-001/final/model.glb --port 8797
```

---

# Earlier Tom Ford seed-editing trial — 2026-09-30

The backend Astra agent completed a real editing trial in a persistent Blender 5.2
scene through community MCP. The agent chose inspections, camera positions,
close-ups, mesh edits and material changes, saved checkpoints, inspected native
GLB material properties, and reviewed exact exported bytes in the actual AR engine.

**The workflow works; the Tom Ford quality target was not reached.** Matched AR
views show a modest change. Crystal rims and internal metal cores still lack the
photographs' contrast, and lens hotspots remain strong while other lens regions
look uniform. Blender renders are more convincing than the AR result. These findings
do not establish which remaining material, geometry or rendering change would close
the gap, or that another agent run cannot do better.

## Starting point and agent ownership

The initialization subagent recommended the latest existing native `.blend` for
this first editing trial. All six original product photos were supplied. This
isolates the live editor's behavior; it does not compare photos-only construction
against Tripo/Meshy initialization. Generated donors remain a component-specific
option for a subsequent experiment.

The source was TF003, continued job 1, revision r0003. Its lenses were already curved.
Source SHA-256 remained unchanged:
`530be4b8ff225fbc4b23f0b82d467987d517096eab421e0650bd11b6ed4e0a63`.

Astra authored all modeling changes. The host supplied connection, observation,
export and cost-accounting infrastructure, then reviewed the outputs. The host also
provided a verified Blender exporter compatibility finding during continuation;
this was therefore an integration trial with that intervention, not a blind benchmark.

Observed agent changes included:

- A local front-rim crown up to 0.72 mm and successive smoothing of highlight-edge ripples.
- An additional 0.65 mm quadratic lens crown, preserving closed lens geometry and
  moving the etching with the lens surface.
- Crystal/lens tint, transmission, roughness and dielectric reflection adjustments.
- Native glTF volume thickness: frame 0.0052 m, temples 0.0036 m, lenses 0.00165 m.
- Saved inspection cameras, studio lighting, checkpoints and front/rear/detail renders.

## Integration issues found and corrected

The AR classifier previously required canonical metadata to honor crystal frame
roles. The local fix honors explicit native roles, keeps unrelated opaque children
out of lens classification, preserves canonical mixed-material behavior, and handles
native opaque mirror shadows. Full AR regressions pass. No production publish ran.

The first agent-authored Thickness-only node group did not survive Blender 5.2's
shader inlining. The installed exporter recognizes the standard group's Occlusion
socket when bypassing that optimization. Astra corrected the group structure; the
final GLB demonstrably contains `KHR_materials_volume`. The new `inspect_export`
tool and preview material summary expose this kind of failure directly to the agent.

The provisional budget initially retained the maximum reservation for every finished
response. That stopped the first invocation after 22 responses despite only about
$2.04 in reported usage. Validated per-response settlement now releases unused holds;
unknown outcomes keep their reservation. The same conversation and Blender scene
resumed with $17.955590 remaining under the original $20 total ceiling.

## Artifacts and verification

- [Saved Blender scene](../data/blender_agent/tomford-agentic-001/agent/scene.blend).
- [Final native GLB](../data/blender_agent/tomford-agentic-001/agent/ar/final-reviewed-8e685488/model.glb).
- [Unchanged native-export baseline](../data/blender_agent/tomford-agentic-001/agent/ar/unchanged-native-baseline-627a50b5/model.glb).
- [Matched AR comparison report](../data/blender_agent/tomford-agentic-001/matched-comparison-result.json).
- [Usage and identity receipt](../data/blender_agent/tomford-agentic-001/trial-result.json).

The comparison uses identical front, oblique and side poses, current runtime
lighting and a synthetic checker background. It compares the agent's edits against
an unchanged native export, so the export-path change is not credited as an edit.
Both files passed exact-byte AR validation. This is not a real-wearer recording.
The agent separately reviewed five final AR views, including the rear.

Final GLB: 1,677,968 bytes, 28 meshes, 89,916 triangles, both full lens solids.
SHA-256: `3876b72844e8d31a1760fd739066db0cb6266a3f855dca3d8db1adabc3bcec76`.
It contains clearcoat, transmission, volume, specular and IOR extensions.

The two SDK invocations shared one SQLite conversation and one live Blender scene.
There were 45 completed Astra responses. Provider token usage under the verified
tariff totals **$6.103384 estimated inference cost**, with no unresolved response
holds. This is computed usage accounting, not a billing invoice.

Validation: **36 focused Python tests** (plus 31 unittest subtests), **480 AR tests**,
TypeScript checking, native export/cleanup tests in real Blender, and the actual AR
comparison all passed. Run files and model artifacts remain under ignored `data/`.

The local [comparison page](http://127.0.0.1:8796/) serves only hash-pinned snapshots
of the candidate and native baseline to the local AR app. Restart instructions and
server-worker limitations are in [README.md](README.md).
# Second photos-only trial: completed with remaining AR limitations (2026-09-30)

The user-authorized retry built Tom Ford FT1123-D 26E size 49 from an empty live
Blender scene using only the six original photographs. Backend `gpt-6-astra`
authored all product changes through the community Blender MCP. Every successful
request used `reasoning.effort=max`. The construction invocation had 86 responses
with a 25,000-token response ceiling; a three-response closeout used an 8,192-token
ceiling and made no product changes. All invocations retained the same $20 cap.

Estimated inference cost from provider usage is **$15.894722**. The original
HTTP 429 account-limit rejection has a **$1.465363 unresolved safety reservation**,
not a reported charge. Total cost plus this hold is $17.360085. The construction
invocation stopped before its written handoff because the next worst-case request
needed $3.701325 while $3.552678 remained available. The host then requested only
a final review/handoff with the smaller output ceiling, preserving maximum
reasoning and the remaining allowance. No runtime diagnosis or modeling advice
was supplied to the artist during construction or closeout.

## Delivery and independent findings

- [Saved Blender scene](../data/blender_agent/tomford-scratch-002/final/scene.blend).
- [Exact final GLB](../data/blender_agent/tomford-scratch-002/final/model.glb).
- [Final authored-material Blender render](../data/blender_agent/tomford-scratch-002/final/blender-oblique.png).
- [Exact-GLB AR portrait detail](../data/blender_agent/tomford-scratch-002/final/ar-eye-detail.png).
- [Artifact receipt](../data/blender_agent/tomford-scratch-002/final/artifact-receipt.json).

The final GLB is 2,283,500 bytes, SHA-256
`756e43f6e33ba991d07cadcad73a209aed7bbd72ccbb67705d501e068ad39f78`.
It passed six-view AR capture, temple continuity and the portrait check in both
lighting conditions. Independent background-Blender inspection confirmed all 32
product objects visible, no diagnostic override, a packed blade texture, closed
real lens solids with about 3.458 mm nonplanarity, and enclosed continuous temple
cores. The saved delivery scene matches the audited checkpoint's geometry,
materials and restoration state.

The rim repair has a measured effect: an endpiece fold's maximum adjacent geometric
normal change fell from 177.90 to 14.80 degrees, and its boundary edges fell from
24 to zero. The lenses already had real curvature when first constructed; their
geometry did not change during the later optical experiments. Closed topology and
these measurements do not establish photographic accuracy.

**This is not a perfect AR result.** Crystal rim depth/contrast remain weaker than
the references, and the distal gold core is substantially less visible in AR than
in Blender. Hidden hinge details, logo typography and fine finish remain inferred;
small inner-arm/tip inscriptions are omitted. Astra's final review reports these
limitations explicitly.

## What the run exposed

The alpha-blended experiment made the core visible, but it also caused the AR
runtime to leave the acetate shell fixed while splaying the core by up to 17.468 mm.
Opacity changed fitting bounds, continuity and shell overlays. The portrait guard
rejected the variant. Astra autonomously tested an earlier passing control,
isolated failing blended variants, and restored the compatible original acetate
material while retaining its refined geometry. No AR implementation changed during
the experiment. The native crystal transmission path's exclusion of posterior
hardware remains a renderer limitation; fixing it requires separate validation.

The host observation interface also needs correction: compact AR responses omitted
continuity failures, and their detailed JSON paths could not be read by any tool
offered to Astra. Resume additionally duplicated the failed initial brief/photos,
inflating the retry's first input by roughly 13,818 counted tokens. These findings
and proposed narrow fixes are recorded; they were not silently changed mid-run.

## Evidence and verification

Under ignored `data/blender_agent/tomford-scratch-002/analysis/`:

- [Edit analysis](../data/blender_agent/tomford-scratch-002/analysis/edit-findings.md),
  `edit-timeline.json` and `run-audit.json`: exact issued scripts, edits, reviews,
  errors, hashes, timing, requests and usage.
- [AR cause investigation](../data/blender_agent/tomford-scratch-002/analysis/ar-transparency-audit.md):
  controlled artifact comparisons, independent runtime probes and source pointers.
- `geometry/final-scene/geometry-report.json`, `geometry/delivery-identity.json`:
  actual delivery geometry and checkpoint identity checks.
- `flow-gap-fix-plan.json`, `metadata-access-audit.json`, `context-duplication.json`:
  observation and resume defects with proposed regression coverage.
- `final-integrity-audit.json`: frozen source, original references and runtime hashes.

The final evidence index contains three invocations, 90 attempted API requests
(89 successful), 88 tool calls, 35 edit journals, three reviews and 16 preview
receipts, with no artifact-verification warnings. Exact API images are externalized
losslessly; authorization headers and API keys are excluded. Before this post-run
documentation update, all 160 source files and their snapshot copies matched;
all six originals and 12 invocation reference copies matched; all AR/portrait
implementation hashes were unchanged. The closeout preserved the selected scene
and GLB byte-for-byte.

Preflight covered the prompts, budget/resume path, native export, portrait image
delivery and evidence recorder. The full focused suite passed 86 tests and 41
subtests; portrait contracts passed three JavaScript tests. The final ten-test
evidence suite included a real safe-mode MCP/Blender roundtrip with a deliberately
partial failing edit and retained both checkpoints and the original result.

The local [AR review page](http://127.0.0.1:8798/) serves the hash-pinned final GLB
to the existing local AR app. No production publish or live application change ran.
