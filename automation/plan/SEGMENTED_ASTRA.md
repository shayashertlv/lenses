# Canonical Astra editing stage

This is the runbook for the implemented, opt-in `segmented_astra_session_v1`
stage after a completed [`segmented_ar_v1`](SEGMENTED_AR_JOB.md) job. It replaces
the missing-adapter proposal in [the earlier readiness audit](ASTRA_READINESS_2026_09_23.md).
It does not use the historical Blender editor.

**Validation status, 2026-09-23: ready for full Astra-inclusive testing.**
The scripted main-entry-point run, exact restore/resume, 71 dedicated Astra
tests, 99 existing pipeline tests, 415 AR tests and TypeScript checks passed.
Miu and Oakley completed real edit/observe/review loops with **six paid Astra
calls total**, all successful. Both finished sessions then resumed through the
main entry point without another call. See [test results](ASTRA_TEST_RESULTS_2026_09_23.md)
and the [pinned verification report](../data/segmented-astra-v1/verification.json).
Both models remain best-effort candidates requiring visual review.

## What the stage owns

The authoring state is a verified copy of the reduced canonical **pre-optics
source GLB**, its whole-part optical group map, selected normal policy and
complete optical descriptors. The exact base candidate and export receipt are
copied separately and remain the visual baseline. Compiled optical GLBs are
derived artifacts, not the editable master.

One turn contains a typed, ordered plan of one to six operations. The host
validates it, changes only declared parts or properties, prepares affected
optics again, exports with a fresh strict receipt, performs receipt-aware
compaction, checks delivery budgets and renders the exact result through
`TryOnRenderer`. Only then does it promote a revision. A rejected batch retains
the previous current revision and records the reason. The following turn sees
post-edit observations, or the rejection with the unchanged candidate.

There is no hidden fallback to Blender, another model, a cheaper repair model,
Meshy, Tripo generation, arbitrary Python, shell commands or a model-supplied
filesystem path. The stage has no asset regeneration or texture-painting tool.

## Entry points and prerequisites

Run commands from `automation/` with the same Python environment used for the
completed segmented job. Dependencies and local weights for the base route are
documented in [the segmented job setup](SEGMENTED_AR_JOB.md#runtime-and-asset-boundaries).
This editor also needs the existing `ar/` Node dependencies and Playwright
Chromium because its observations use the actual local renderer. The observer
starts and stops its own local QA server; it does not publish `ar/site/` or
change the live application. Blender is not required.

The base job must contain completed, verified input, optical preparation,
appearance, appearance-review and delivery stages. Its selected candidate must
match delivery and have no delivery-budget violations. Review reasons and
ambiguous role hypotheses are retained; completing those stages does not imply
that their visual judgments were correct.

### Scripted execution: no paid inference

Create a JSON list of plans. This small example requests a part closeup and
then stops with a review recommendation; it demonstrates the protocol without
changing geometry. Part `0` must exist in the selected source inventory.

```json
[
  {
    "note": "Inspect source part 0 before proposing an edit.",
    "operations": [
      {"operation": "inspect", "part_ids": [0], "padding_fraction": 0.15}
    ]
  },
  {
    "note": "End the protocol demonstration with the original candidate retained.",
    "operations": [
      {"operation": "finish", "verdict": "best_effort", "summary": "No geometry edit proposed; visual quality still requires review."}
    ]
  }
]
```

Save it as `plans.json`, then run:

```powershell
python -m reconstruction.segmented_astra_job --base-job data/jobs/example --output data/astra/example-scripted --astra-script plans.json --astra-max-turns 2
```

Scripted plans use the same validation, compilation, rendering and checkpoint
path. They are predetermined host instructions, not evidence of model judgment.
They cannot be combined with paid-mode authorization, a budget or an Astra
dotenv file. The executable option is `--astra-script`, not `--script`.

### Live execution: explicit credential and shared authorization

For a completed job, only the explicitly selected OpenAI credential is needed
by this stage. The command below reads `OPENAI_API_KEY` from the named file;
it does not search for a `.env` file or use the base provider's `--env` option.
Keep credentials out of arguments and committed files.

```powershell
python -m reconstruction.segmented_astra_job --base-job data/jobs/example --output data/astra/example-live --authorize-paid-astra --astra-env .env --astra-api-key-env OPENAI_API_KEY --astra-budget data/astra/shared-budget.json --astra-maximum-calls 10 --astra-max-turns 3 --astra-model gpt-6-astra --astra-reasoning high --astra-max-output-tokens 12000
```

Alternatively omit `--astra-env` and set the explicitly named environment
variable in the invoking process. Missing credentials stop execution. The
model defaults to `gpt-6-astra`; the transport accepts that identifier or an
explicit `gpt-6-astra-...` version, with no model fallback. Reasoning defaults
to `high`; maximum output defaults to 12,000 tokens and must be 256–24,000.

To run the stage immediately after the base route, append `--with-astra` and
the same Astra options to the base command:

```powershell
python -m reconstruction.segmented_job --request request.json --output data/jobs/example --with-astra --astra-output data/astra/example-live --authorize-paid-astra --astra-env .env --astra-budget data/astra/shared-budget.json --astra-maximum-calls 10 --astra-max-turns 3
```

Supply the base job's own provider, semantic-client and local-weight arguments
when it still needs those stages; the example assumes they are completed and
reusable. Astra authorization does not authorize other providers. Standalone
`segmented_astra_job` is the simplest way to operate on an already completed
job without rerunning the base orchestration.

The integrated result retains `base_candidate`, `base_delivery`,
`base_selection` and `base_rendering`. Its current `candidate`, `selection`,
`rendering` and `delivery` refer to the Astra session. Base artifact bytes stay
preserved. Always resolve the candidate path and SHA from the final report;
do not assume a root `candidate.glb` was overwritten.

## Coordinates, IDs and typed tools

Coordinates are **metres, +Y up, +Z front, +X horizontal**, with the canonical
job's bridge-underside origin. A millimetre is `0.001` metres. Pivots and bend
centres must come from the source inventory, not estimated image pixels.
The AR engine still performs face-width fitting; this stage targets appearance.

Part IDs are source-scene traversal ordinals bound to a source SHA and
node/mesh/primitive binding. The editing writer preserves those ordinals across
revisions. Optical group IDs are explicit declarations, not material names or
verified semantics. Use the current snapshot's IDs rather than raw provider
indices, Blender object names or indices in the compacted export.

The exact schema lives in [`segmented_astra_tools.py`](../reconstruction/segmented_astra_tools.py)
and is included in every model request. Objects reject extra fields and require
all schema fields; nullable fields must be supplied as `null` when unused.

| Operation | Required operation-specific fields | Meaning and limits |
| --- | --- | --- |
| `translate` | `part_ids`, `offset_m` | Move selected whole parts; displacement vector length at most 0.003 m. |
| `rotate` | `part_ids`, `axis`, `pivot_m`, `angle_degrees` | Rotate around an observed pivot; at most ±10°, and every moved vertex still has the 0.003 m displacement limit. |
| `local_bend` | `part_ids`, `center_m`, `radius_m`, `offset_m` | Smooth compact-support displacement; radius 0.002–0.08 m, displacement at most 0.003 m and analytic displacement-gradient bound at most 0.35. |
| `frame_material` | `part_ids`, `base_color_linear_rgb`, `roughness`, `metallic` | Opaque parts only. At least one non-null factor. Retains textures and multiplies their colors; does not repaint pixels or remove baked highlights. |
| `optical_appearance` | `group_ids`, `appearance` | Replace complete canonical descriptors for existing groups, preserving uninvolved fields explicitly. |
| `normal_policy` | `policy` | `preserve` uses source normals; `smooth` invokes the existing guarded optical fit. Unsupported fits cannot bypass strict export. |
| `group_membership` | `group_id`, `part_ids` | Replace the members of one existing group with whole source parts. Groups must remain disjoint and nonempty. No triangle splitting or automatic proof of role identity. |
| `inspect` | `part_ids`, `padding_fraction` | Alone in its plan. Request exact-camera closeups; padding 0.02–0.6. Does not mutate the model. |
| `restore` | `revision_id` | Alone. Restore an existing verified `rNNNN` checkpoint, including its authoring state and observation. |
| `finish` | `verdict`, `summary` | Alone. `review_ready` or `best_effort` after viewing the current result. Does not confer host acceptance. |

Geometry editing preserves indices, UVs, image bytes and unrelated attributes;
new selected attributes are appended with explicit proofs. The source must be
a supported single canonical scene with baked identity transforms, explicit
normals, at most 200,000 triangles and 1,000,000 vertices. Skins, morphs and
untracked extensions are refused. The selected source already needs reduction;
the edit loop is not a remesher.

Contacts within 0.00015 m receive additional constraints; rigid motion at those
samples is limited to 0.00001 m, and local bends must leave contact samples
effectively unchanged. Selected parts should include connected hardware when
moving an assembly. Sampled clearance and swept-motion checks can refuse edits
within the numeric limits. They are not a global self-intersection or
watertightness certificate, and do not fix existing defects automatically.

Optical colors are **scene-linear RGB**, not sRGB byte values. Density is
natural-log attenuation: intrinsic transmission is `exp(-density)` before
coating losses. Lens-local `v=0` is bottom and `v=1` is top. A constant density
has one key at `v=0`; otherwise keys increase from 0 to 1. Optional angular
reflectance keys cover 0–90°, with the first RGB equal to
`normal_reflectance_rgb`. The descriptor includes `refractive_index`,
`roughness`, `optical_density_keyframes`, `angular_reflectance_keyframes` and
`rear_reflection_fraction_rgb`. Copy current values for unaffected fields.
The strict `LensAppearance` reader checks physical/structural constraints.

## Observation and compilation contract

`observe_candidate(...)` renders baseline and current candidate under the same
requested cameras, source checker and three explicit lighting environments:
room, broad studio and side studio. Each has front, ±35° obliques, rear asset
inspection and a 25° rolled view: 30 retained raw images per observation.
The usual model input is three baseline/candidate sheets plus one source-part
label sheet; an inspection adds three focused sheets. Original product photos
are supplied separately once, with image IDs and byte hashes.

Rear inspection rotates the asset 180° and hides synthetic face occluders;
other runtime temple logic remains active. It is not a photographed wearer.
CPU part pictures are explicitly opaque geometry labels, with first-hit face
and part rasters, source-bound inventory and known cameras. They do not show
canonical lens optics. Focus images crop the existing AR renders through their
recorded matrices; they do not invent calibrated product-photo cameras.

The host pins source and output bytes, verifies the complete view matrix,
camera/pose/lighting policy, and fingerprints AR sources, QA harness, package
lock, local observer code and runtime versions. Missing or changed images,
inputs or implementation reject the observation. Runtime compatibility remains
separate from photographic similarity.

`compile_authoring(...)` reuses a verified preparation only when its source and
groups remain appropriate. Geometry, group or normal changes obtain fresh
preparation and source bindings. `smooth` may use the existing bounded
failed-preparation recovery; it does not relax export policy. The compiler
calls `write_optical_group_candidate`, verifies preview geometry, then calls
`run_compact_asset(..., optical_receipt=receipt)` for one fresh export. That
path updates the optical binding and compaction receipt and validates the
result. Generic glTF or Blender export is not an equivalent step.

## Budgets, replay and recovery

- Every live session belonging to one authorization must use the **same**
  `--astra-budget` path and the same `--astra-maximum-calls` value. The hard
  ceiling is 10 reserved requests across that ledger, not 10 per product.
  A smaller explicit ceiling is supported. A new ledger must not be used to
  evade the authorized total.
- A request is reserved durably before its HTTP POST. Failed, malformed,
  refused or uncertain attempts remain counted. There are no automatic HTTP
  retries, redirects, alternate-model calls or paid JSON-repair calls.
- `--astra-max-turns` is 1–10 per session, default 3. A model decision to
  inspect, restore or finish consumes a turn/request too. Host compilation
  and local rendering do not call a paid provider. The reservation ceiling
  is not a dollar or token-cost guarantee; receipts retain actual API usage.
- Each request sends a fresh complete host snapshot, exact image inputs and
  typed tool schema using `store:false`. Exactly one `edit_candidate`
  function call is required. There is no hidden conversation state or
  `previous_response_id` chain.
- Repeat the **identical command** to reopen a session. Input, implementation,
  schema, renderer, model, script and turn-budget bindings must still match.
  Completed turns and committed revisions are verified and reused. Saved
  complete Responses results can be replayed locally with matching request,
  payload, response, plan and shared-reservation hashes; replay sends no POST.
- An interrupted local compile/render may retry in a fresh local attempt
  using the saved plan. Previously promoted edits are not applied twice.
  Inspect `state.json`, the turn's `plan.json`, and retained events before
  interpreting a resumed result.
- A reserved request with no valid completed receipt is **uncertain**, even
  if a connection failed. The driver stops with `needs_attention`; rerunning
  does not silently create a replacement paid request. Preserve its files
  and ledger. A separately authorized new attempt needs a new session under
  the same remaining authorization; no in-place retry/reset command exists.
- A changed implementation or request cannot reuse the old session. Keep its
  evidence and start a new output directory. Do not edit pins, delete budget
  reservations, overwrite source assets or manually mark a failed request
  complete to make resume pass.

Useful files in a session output:

| File or directory | Purpose |
| --- | --- |
| `seed.json`, `inputs/` | Pinned copied baseline, receipt, source preparation and photos; initial authoring state and implementation fingerprint. |
| `state.json` | Atomic current revision, verified checkpoint references, turn statuses and events. |
| `turns/turn-NNNN/input.json` | Persisted exact context/images/schema binding for the decision. |
| `turns/turn-NNNN/api/` | Live request, payload, response and receipt. Payload embeds source images; keep these local. |
| `turns/turn-NNNN/plan.json` | Typed returned or scripted plan. |
| `edits/turn-NNNN/attempt-NNNN/` | Local authoring changes, preservation proofs, compilation, actual-AR observations and revision record when promoted. |
| `revisions/r0000/attempt-NNNN/` | Verified initial baseline observation. |
| `report.json` | Exact current candidate/export, preserved baseline, authoring and observation pins, stop reason and review limitations. |

An actual Astra `finish` sets a model review recommendation for the current revision.
Scripted `finish` never sets `model_reviewed_current:true`.
`model_reviewed_current` distinguishes that from stopping at the turn limit
immediately after a host-observed edit. A reported available candidate may
still be the unchanged baseline after rejected operations. All deliveries
remain `accepted:false`, `requires_review:true`, `production_ready:false`.

## Implementation map and evidence

| Module | Main boundary |
| --- | --- |
| [`segmented_astra_job.py`](../reconstruction/segmented_astra_job.py) | CLI, explicit client construction, scripted plans, durable turn orchestration. |
| [`segmented_astra_session.py`](../reconstruction/segmented_astra_session.py) | Verified seed, authoring compilation, atomic revision promotion, restoration and delivery. |
| [`segmented_astra_tools.py`](../reconstruction/segmented_astra_tools.py) | Closed plan schema and semantic validation. |
| [`segmented_astra_geometry.py`](../reconstruction/segmented_astra_geometry.py) | Source-part inventory, bounded geometry and opaque material edits with preservation proofs. |
| [`segmented_astra_observe.py`](../reconstruction/segmented_astra_observe.py) | Exact current-AR observations, labelled source IDs, focus crops and renderer fingerprint. |
| [`segmented_astra_transport.py`](../reconstruction/segmented_astra_transport.py) | Explicit Responses request, shared reservations, strict result parsing and local replay. |

Focused tests can be run without paid calls:

```powershell
python -m unittest discover -s tests -p "test_segmented_astra*.py" -v
```

The observer's final-revision Oakley smoke produced all 30 images with stable
source/renderer hashes. Its 15 baseline-versus-identical-candidate pairs were
byte-identical. [Report](../data/segmented-astra-observer-v1/oakley-smoke/report.json)
and [comparison](../data/segmented-astra-observer-v1/oakley-smoke/comparison-room.png)
are local evidence. A preceding Miu smoke also succeeded. These prove the
observation path. The completed [integrated scripted/live results](ASTRA_TEST_RESULTS_2026_09_23.md)
add real source recompilation, exact rollback/restoration, durable API execution,
post-edit model review and the main-entry-point handover.

The remaining quality limits include incorrect provider geometry, damaged
branding, whole-part lens/frame ambiguity, baked frame lighting, incomplete
reflection/absorption identification and uncalibrated source cameras. This
stage makes supported corrections testable and reversible; it cannot guarantee
perfect reconstruction from ordinary product photos.
