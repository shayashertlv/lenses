# Astra integration readiness — 2026-09-23

> Historical pre-implementation audit. The canonical stage described below as
> missing has since been implemented; [SEGMENTED_ASTRA.md](SEGMENTED_ASTRA.md)
> supersedes this document for current commands, tools and recovery behavior.
> [Integrated live validation has passed](ASTRA_TEST_RESULTS_2026_09_23.md). The original findings and measurements
> below remain unchanged, including the refusal of a generic Blender round trip.

**Decision at this audit checkpoint: the segmented pipeline was not ready for a full test with
Astra editing included.** The provider/material pipeline works, the current
assets load in AR, and the OpenAI account can access the configured Astra model.
The missing piece is a tested editing session for the current asset contract.
The historical Blender agent is not that session.

This is an integration finding, not evidence that Astra cannot improve the
models. No paid inference request was made during this review. No deployment,
catalog change, or replacement of the five retained candidates occurred.

## What exists and what was verified

| Area | Current evidence | Meaning |
| --- | --- | --- |
| Current orchestration | `reconstruction.job.main` dispatches `segmented_ar_v1` to `segmented_job.run_segmented_job`; its last stages are appearance review and delivery | No Astra stage, session, command flag, or editing checkpoint is connected |
| Five retained jobs | All 53 completed stage inventories verified, strict selected GLB receipts pass, all 60 selected AR image hashes verified | Existing results are intact; none contains an Astra stage |
| Current Python contracts | 85 focused tests and 8 subtests passed | Generation recovery, grouping, optical preparation, materials, review and delivery contracts pass |
| AR code | 415 tests passed; TypeScript check passed | Local runtime regression checks pass |
| Hardened historical editor | 80 host tests and 4 real Blender tests passed; one existing expected failure for clear-rimless photo segmentation | Tool safety/rollback regression checks pass; the legacy lens extractor still has that known limit |
| Receipt-aware compaction | 8 tests passed, including optical remapping and tampering refusal | Supported one-shot compaction can accompany each fresh optical export |
| Fresh actual AR observation | Exact selected Miu and Oakley bytes loaded again through the current renderer, two optical meshes each; front/angled/back under room and broad lighting | Runtime/observation baseline, not product-accuracy acceptance |
| Blender round trip | Actual Blender 5.2 import/export on private copies, with extras both enabled and disabled | It cannot serve as a lossless editing bridge |
| OpenAI access | Read-only `GET /v1/models/gpt-6-astra` and `/v1/models/gpt-5-nano` both returned 200 with the exact requested ID | Existing credentials suffice for access; this does not test a fresh paid multimodal/tool request |
| Historical Astra | Real older INVU receipts and edited sessions exist | They used Meshy, replaced parametric lenses, and legacy materials; they are not tests of these five segmented candidates |

Evidence: [current-job audit](../data/astra-readiness-2026-09-23/current-job-audit.json),
[reproducible read-only audit](../data/astra-readiness-2026-09-23/audit_jobs.py),
[OpenAI access check](../data/astra-readiness-2026-09-23/openai-access.json),
[Blender round-trip audit](../data/segmented-blender-audit-v1/roundtrip-audit.json),
[fresh AR evidence](../data/segmented-blender-audit-v1/fresh-ar-evidence.json),
and [verification summary](../data/astra-readiness-2026-09-23/verification.json).

## Why the existing Astra command is incompatible

1. **Preparation changes the starting problem.**
   `ar/modeling/native/agent_prepare.run` calls the old scan segmenter, cuts out
   inferred scan lenses and builds new parametric lenses. `Session.start` needs
   its `prepare/master.blend`, `prepare.json`, `prepare.npz`, a `frame` object
   and old lens-role labels. Our segmented asset already has retained lens
   geometry and source-bound optical groups. Running old preparation would not
   test Astra editing the current result.
2. **A plain Blender round trip loses the material contract.**
   Both Miu and Oakley lose `LENSES_lens_appearance` and the document-level
   `effectiveOpticalGroups` declaration. `export_extras=True` retains some
   per-node identity but cannot restore those missing descriptors. Blender
   sees approximate alpha/PBR fallback, not the canonical AR response.
3. **It also changes face/attribute identity.**
   Miu changes from 126,123 to 126,111 triangles; Oakley from 119,983 to 119,947.
   The removed position-triangles have retained duplicate counterparts: this
   probe does not demonstrate newly opened holes. Nevertheless, indices,
   vertex splits, some normals, node order and exact optical receipts change.
   Copying JSON extras back after export is therefore insufficient.
4. **Units, axes and materials differ.**
   The old worker uses millimetres, -Y front, +Z up and a front-centre origin.
   Current canonical assets use metres, +Z front, +Y up and a bridge origin.
   `stage_export.run` performs the legacy conversion, re-bakes/decimates and
   writes legacy lens materials. `validate_mirror_glb` rejects the original
   current assets because it only recognizes legacy KHR transmission.
5. **The observer and instructions describe the old workflow.**
   `agent.observe.package` uses Blender lighting/materials. `BatchDriver` and
   `AstraDriver` instruct the model to rebuild lenses and never finish with
   default clear lenses. Clear lenses are appropriate for Miu. They also
   privilege thin-film controls and simplified clearance rules, whereas the
   current system supports effective angular RGB response, retained multipart
   groups and legitimate front hardware. Those prompts must remain legacy-only.
6. **Recovery is not a current-pipeline journal.**
   Old paid receipts avoid automatic reposting, but reconstructing a driver
   starts at turn 1 and collides with retained receipts. There is no complete
   request/model/photo/tool-version binding and applied-step replay connected
   to `segmented_job.Journal`. Restarting a full job must not buy the same turn
   again or apply an edit twice.

## Corrections made during this review

- Added `agent.input_contract.require_legacy_scan` at legacy intake, provider
  continuation and preparation, including the native preparation entry point.
  Canonical optical candidates are refused before that incompatible route can
  cut lenses or submit a retexture. Raw provider scans still pass this metadata
  guard; passing it is not a geometry-quality verdict.
- Hardened legacy tool argument validation: finite numbers, actual JSON objects,
  and exactly three coordinates for vector tools. Previously `[2]` could
  broadcast into a three-axis move and infinity could reach the worker.
- Added tool/time/model budget checks before paid driver requests and bounded
  repair calls. Existing request reservations remain authoritative; no implicit
  retry was added.
- Added a final local observation receipt binding the finished master, source
  photos and final images. Same-turn edit-and-accept and unobserved last-turn
  edits cannot masquerade as a reviewed acceptance. This observer is still
  the legacy Blender observer, not a canonical AR acceptance test.
- Added mutation rollback protection and export refusal for an unresolved
  mutation. A tool can fail after partly changing the Blender scene, including
  after deleting an old lens. A failed operation must not leave that partial
  scene available as a successful result.
- Updated both entry-point READMEs, the segmented job contract, and the old
  modeling instructions/history to distinguish the workflows and historical
  paid runs. Added `requirements-segmented.txt` for the missing current-route
  dependencies. See [setup and commands](SEGMENTED_AR_JOB.md).

These changes harden and clarify existing tools. They do not implement the
missing segmented Astra editor or establish product quality.

## Concrete missing implementation

The functions below are **proposed, not present or callable**. This is the
smallest useful editing route to build; selecting existing material candidates
alone would not test Astra's ability to repair the geometry.

Use the reduced, canonical **pre-optics** `source.glb`, its explicit part/group
map, the selected normal hypothesis and material descriptors as authoritative
editing state. Keep the selected final GLB as the baseline compiled output.
Repeatedly importing exported optical clones as new source parts would corrupt
identity. Blender may be an auxiliary viewer, but its generic importer/exporter
must not own the asset's authoritative arrays.

| Proposed function | Existing pieces to reuse | Required behavior |
| --- | --- | --- |
| `segmented_astra_session.create_session(base_job, output)` | `Journal`, `verified`, strict optical reader, selected preparation reports | Snapshot exact photos, pre-optics source, groups, normal policy, selected materials, baseline GLB, renderer/tool/prompt versions; retain baseline and every accepted revision |
| `segmented_astra_tools.inspect_parts(state)` | `mesh.load_glb_bytes`, known-camera render evidence | Stable part/group IDs, canonical-metre bounds, roles and ambiguities, source-photo IDs, labelled views and requested closeups; no guessing IDs from pixels |
| `segmented_astra_tools.apply_geometry_edit(state, edit)` | `CageField.transform`, `gradient_bound`, deformation accessor/math helpers | New selected-primitive writer for bounded translation/rotation and smooth displacement; explicit target IDs and metre units; preserve untouched primitives, UV/material/image bytes and seam anchors; emit old/new binding and displacement receipts |
| `segmented_astra_tools.set_optical_appearance(state, group_ids, descriptor)` | `LensAppearance.from_dict`, strict optical exporter, `appearance_control` pattern | Expose density/gradient, roughness, angular color and optional rear-response controls; no painting photographic reflection rectangles into the lens; clear remains valid |
| `segmented_astra_tools.set_frame_material(state, part_ids, material)` | `plain_frame_control` pattern and material validators | Explicit opaque targets; preserve textures unless a separately tested texture operation is requested; do not silently erase logos/detail |
| `segmented_astra_session.compile_candidate(state)` | `run_optical_group_preparation`, guarded normal alternatives, `write_optical_group_candidate`, receipt-aware `run_compact_asset`, `read_optical_group_candidate` | Rebind changed geometry/groups to the new source hash, regenerate optical coordinates/normals as required and generate a fresh receipt; never reuse old array hashes after an edit |
| `segmented_astra_observe.observe_candidate(state)` | `render_appearance`, actual AR harness, source snapshots | Render candidate and unchanged baseline with identical cameras, source images and lighting; include front, both obliques, rear, rolled, silhouettes and affected-region closeups; bind every image to model hash and renderer revision |
| `segmented_astra_session.apply_batch` / `restore` | Existing batch concept and durable reservation pattern | Speculative revision, ordered validated operations, compile, strict/runtime validation, observation, then atomic promotion; failure restores the previous valid revision; interrupted/reserved calls never repost automatically |
| `segmented_astra_job.run` | `segmented_job` review/delivery stages | Explicit opt-in stage before final delivery, separate durable inference budget, replayable receipts and a completed-run no-call resume; last edits receive final host validation even when the model budget is exhausted |

`deform_glb` is useful reference code, not a drop-in selected-part editor: it
visits all active meshes, can normalize unchanged normals, clones bindings and
does not check global self-intersections. For a first smooth cage edit, require
a displacement-gradient bound below one, valid triangles, fixed seam controls
and cross-part collision/contact checks. A rigid move does not prove a bend.
Destructive delete/fill/remesh operations should be unavailable until they have
their own lineage/contact tests. Role reassignment can reuse the verified
partition/declaration adapters but must preserve competing assignments such as
VB's disputed thin edge.

Pre-optics compaction should happen before establishing the editing ledger;
otherwise vertex selections need their own verified remap, since the existing
index maps cover nodes/meshes/materials and ordinary-frame vertex IDs can change.
Post-optics compaction
also has an existing supported path: call `run_compact_asset` on a fresh
uncompacted export with its trusted `optical_receipt`, then use the returned
model and resealed `compact.export.json` together. It verifies both sides,
remaps group bindings and retains the parent receipt plus active-semantics
proof. Raw compaction without the receipt and repeated/nested optical
compaction are refused. This is the path current appearance candidates use.
Bound file size and keep the previous candidate if a revision cannot be
compiled within the delivery budget.

The new prompt must describe the actual tool catalog and current state:
existing product photos, appearance as the objective, wearer sizing performed
by the AR engine, camera/lighting uncertainty, legitimate clear and rimless
designs, shared paired-lens materials where supported, and `needs_review` for
unresolved evidence. It must not infer physical dimensions from the 145 mm
display convention, remove hardware merely because it projects over a lens,
or assert that one photographed highlight is intrinsic lens color.

## Gate before a paid full-pipeline test

All of these must pass on private copies of at least Miu and Oakley:

1. A no-edit session preserves the exact baseline output and group identity.
2. A real bounded geometry change affects only the requested parts; a material
   change preserves geometry and the relevant frame textures.
3. Every edited candidate gets a fresh strict optical check and fresh actual-AR
   renders. A malformed descriptor, invalid normal field, wrong source hash,
   missing group or triangle/file-budget violation cannot be promoted.
4. Restore returns the exact earlier candidate. Failed/interrupted edits leave
   no partially promoted state. Replaying a completed run makes zero API calls;
   uncertain requests and already-applied batches are never duplicated.
5. A fake Astra transport exercises inspect → geometry edit → material edit →
   observe → correction/restore → finish. A cap immediately after an edit still
   produces final validation and images. This needs actual geometry and AR
   integration, not just mocked-success tool responses.
6. The exported bytes, observed bytes and delivered bytes agree. `finish` is
   an agent decision; host integrity, optical/runtime compatibility and human
   assessment remain separate fields. An agent `accept` is not ground truth.

After those gates, a bounded paid experiment can answer whether Astra improves
the models. Start from these cached provider assets so it tests the editor
without regenerating them; then run all five from requests with the stage
enabled. Compare against the unchanged baseline with the same render settings.
The comparison must include ordinary clear/tinted/gradient glasses as well as
mirrored shields. Set call/time/output-token limits before starting and record
actual usage. There is intentionally no purported full-Astra command here:
the required entry point does not exist yet.

## API contract checked against current documentation

Keep `gpt-6-astra` on the Responses API with the tested `high` reasoning effort,
explicit strict/closed function schemas and host-side validation. Forced
function selection is supported. Schema validity does not establish that the
requested physical edit is sensible.
[OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
and [function calling](https://developers.openai.com/api/docs/guides/function-calling).

For a stateful tool conversation, preserve replayable response items alongside
tool results. With `store:false`, current API behavior includes encrypted
reasoning content automatically; the old explicit
`include:["reasoning.encrypted_content"]` remains compatible. The existing
fresh-request batch design instead supplies its complete host observation and
edit log each turn. Those are different conversation designs and should not
be mixed accidentally.
[OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

The review verified model access, not a new paid request with the future
segmented tool catalog. Provider availability and account limits can change;
saved historical responses do not replace that eventual live test.

## Quality questions a successful integration will still leave

Coarse/generated frame and shield geometry, damaged branding, VB's boundary
assignment, and INVU/Oakley front/rear mirror appearance remain real issues.
The current source-to-render comparisons do not match photographic camera and
illumination, and the five products are development examples rather than a
held-out accuracy corpus. Astra must be allowed to leave an unsupported edit
unmade. A successful editing loop would make these hypotheses testable; it
would not guarantee perfect reconstruction from existing product photographs.
