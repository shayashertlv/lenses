# Effective optical groups in reconstruction jobs

Implemented locally, 2026-09-22. The reconstruction job now accepts explicit effective-group preparation and joint fitting. Its default remains the existing front-sheet path. A separate source-preserving preparer, strict grouped fit-input adapter and profile-specific preview exporter connect the existing observer, joint optimizer and experimental AR representation. Completing any stage remains `quality_verdict: unmeasured`, `accepted: false`, with no selected material. See [job commands and declaration format](JOBS.md).

This integration reuses the existing photographic optimizer, recovery machinery and fixed policies. Automatic IDs derive from source node/mesh/primitive bindings; no product-specific rules were added.

This integration alone does **not** achieve the requested automation. Whole-source-part membership cannot resolve the known Miu mixed semantic part; no supported face-level partition is implemented here, and its tiny silicone group remains unobserved. The completed independent and joint VB material experiments both produced **zero photo-policy-passing candidates**; runtime compatibility of neutral or diagnostic GLBs does not change those results ([measured joint result](JOINT_PHOTO_EVALUATION.md)). Optional dimensions remain `supplied_unapplied`. Semantic partition/identity and photographic composition/articulation still need generic inference and independent evidence. Five archived designs, source names and hand-labelled declarations are development evidence, never a fallback product catalogue or an all-glasses solution.

## Identity and input contract

The explicit job options preserve the current front-sheet defaults:

```python
run_job(...,
    optical_profile='front_sheet_v1',
    optical_grouping=None,             # 'explicit_declarations' or 'source_part_hypotheses'
    optical_group_declarations=None)   # Path, required only for explicit declarations
```

CLI equivalents: `--optical-profile`, `--optical-grouping`, `--optical-group-declarations`. Grouped fitting requires `--lens-candidates --lens-fit-mode joint`; unsupported combinations fail before writing. The effective-group profile requires an explicit grouping mode. These are immutable job settings; the photo request schema in `input_bundle.py` is unchanged.

An explicit declaration is a versioned manifest containing the retained candidate SHA-256, common coordinate-frame declaration, unique group IDs, member IDs, and source bindings `{node_index, mesh_index, primitive_index}`. Include the observed source-part index as a checked convenience field, not the sole identity key. Each member names a complete source primitive instance; it cannot select arbitrary triangles, erase components, or move vertices. Each source member belongs to at most one group. Preserve declaration provenance and mark identity unverified; a caller's assertion of verification is not independent evidence.

`source_part_hypotheses` is a separate automatic mode. It may reuse `_lens_parts` and its complete selection ledger to propose one group per selected source part. Its IDs should derive from source bindings, not product names. A fused mesh remains one uncertain group; several objects sharing a material do not automatically become one lens. Retain rejected/unselected records and unresolved semantic coverage. This mode makes the stage usable without a per-product declaration, but does not solve optical identity.

Bind declarations to the **actual chosen geometry** after refinement. If their SHA names the initializer but the retained refinement has another SHA or changed bindings, refuse grouped preparation with a recorded mismatch. Do not silently transfer a declaration by part order/name. Supporting such a transfer would require a separate, verified deformation/instance mapping. Preserve the geometric candidate and report the optical stage unavailable. Never submit a replacement provider task to resolve this mismatch.

## Module changes

| Module/API | Implemented scope |
|---|---|
| New `reconstruction/prepare_optical_groups.py` | Add `run_optical_group_preparation(model, output, *, grouping_mode, declarations=None) -> dict`. Capture source bytes once, load those bytes, validate the complete membership inventory, call `prepare_optical_group` per group, then `write_optical_group_candidate` for one neutral diagnostic GLB. Save every preparation record/array hash and the export receipt. Unsupported members prevent a complete candidate; none are dropped. |
| `optical_groups.py` / bound source decoder | Reuse the existing preparation API. Extract source positions, connectivity and authored normals from the same captured GLB. Bake actual world transforms; transform supplied normals with the recorded inverse transpose and normalization policy. If normals must be derived, retain that explicit hypothesis. Derive one referenced-vertex Y coordinate across the entire group. Do not import a QA script into core. |
| `prepare_optics.py` | Keep the old entry point and front-sheet behavior. Dispatch from the job by explicit profile; do not replace maximum-Z preparation silently or infer the profile from success/failure. |
| `optical_group_validation.py` / `optical_group_asset.py` | Align the Python runtime-compatibility checks described below. Validate actual exported float32 attributes again, not just their source doubles. Continue using `load_glb_bytes` and the existing receipt/declaration bindings. |
| New grouped fit-input loader, called by `photo_lens_stage.load_optical_fit_inputs` | Dispatch only on an explicit versioned preparation profile; keep legacy front-sheet receipts on their existing path. For groups, pin preparation, source, neutral GLB, export receipt and every saved primitive array; verify member/group inventory and actual exported attributes before calling `build_optical_group_observations`. Return `profile`, prepared groups, observations and the merged immutable pins alongside the existing context fields. Unknown profiles fail. |
| `joint_photo_lens_stage.py` | Keep `run_joint_photo_lens_stage(...)`, its lock, checkpoint and ensemble machinery. Bind profile/group inventory in its request. Use a small profile-specific preview-export function: front sheets use `write_optical_candidate`; groups use `write_optical_group_candidate`. Each grouped preview must contain all groups from one complete joint candidate. |
| `job.py` | Pin profile, grouping mode, declaration bytes/hash and policies in settings before stage execution. Add the grouped preparation dispatch and explicit profile to stage summaries. Keep geometry `candidate.glb` separate from diagnostic optical previews. Never promote a fitted preview to the final accepted material. |

The distinct preparation contract uses `status: prepared_optical_group_candidate`, `optical_profile: effective_optical_group_v1_experiment`, source/model/export pins and a complete group/member inventory. Neutral descriptors are diagnostic controls. Saved declaration content, coordinate frame, source membership and identity provenance must agree with the normalized exported groups; an updated outer file hash cannot validate a contradictory declaration. Member order is retained as provenance but is not physical group identity. The existing front-sheet `parts/surfaces` receipt is not reinterpreted as grouped geometry.

The grouped observer already collapses multipart source-region priors to unique group IDs and returns the fitter's `surface_binding` schema. Continue using its actual exported UVs/normals, original camera normalization and fixed sample coordinates. Keep unsupported stacks, depth ties, missing support and invalid coordinates as unknown observations. Preserve all upstream raw SAM alternatives; the existing fitter-input policy uses the three retained hypothesis intersection masks, not all fifteen raw masks as independent evidence. Shared-image splits and unique-pixel ownership must remain unchanged.

For each joint preview, assign its complete `candidate.groups[group_id].appearance` set to the same prepared group records. Read the resulting GLB back and verify that its group/member inventory and geometry/UV/normal/index hashes match the neutral asset used for fitting. Descriptor changes must not change coordinates. Retain the candidate ID, whole family assignment, fit SHA and neutral geometry binding. Ranking diagnostic previews is not uncertainty certification or independent final validation.

## Python and TypeScript geometry compatibility

The Python export/reader runtime wrapper now implements the two checks added to the TypeScript importer:

1. Every triangle rejects an interpolated normal field whose convex hull contains zero. Positive per-corner normalization preserves this containment. The exact predicate rejects antiparallel pairs; accepts rank-three triples; for rank two checks the cyclic cross-product signs; and accepts rank-one directions only when their sign is uniform. This prevents undefined GPU `normalize(0)` even on an isolated triangle.
2. Identical coincident triangles with a uniform corresponding normal sign no longer require the old acute-cone sufficient condition. The global nonvanishing proof allows valid obtuse fields. Retessellated varying fields still require a real proof and remain unsupported by the current checker.

Both changes use bounded arithmetic/work accounting. The wrapper checks **one shared pre-quantization group-height pair**, using exact half-plane feasibility and actual endpoint witnesses. Independent per-vertex endpoint intervals are insufficient. These are compatibility checks against closed float32 rounding enclosures; neither implementation claims unique source recovery or exact inverse IEEE tie-breaking. Coplanar V-field equality remains exact, with no area epsilon or welding. Raw rational intermediates are checked and charged before reduction, including cancellation cases.

Python's analysis predicate and the AR import capacity remain distinct. `optical_group_runtime.validate_effective_optical_runtime` applies the pinned browser bounds (eight groups, 500,000 triangles and the other declared limits) and reports policy/work identity. The exporter and reader apply it to actual float32 attributes. New receipts add `runtime_contract_validation`; older receipts remain readable after fresh validation. Offline export is not an actual render test or a mobile performance guarantee.

The new shared Python/browser fixtures include 19 explicit normal, height and coincidence cases plus 96 integer-normal triples checked independently by linear feasibility. All 115 agree. This is bounded counterexample coverage, not a proof of general cross-language equivalence. It supersedes the scope of the earlier 100-case receipt, which lacked the new normal-hull rule and valid obtuse duplicate. Existing evidence remains attached to its historical source hashes.

## Pins, attempts and recovery

Capture an explicit declaration once; parse, hash and snapshot those same bytes under the job. Include that immutable snapshot in the stage inventory and journal settings hash. Recheck original declarations when still present, with the same changed-original policy as photos/models. Auto-generated hypotheses must bind their source SHA, complete source-part ledger, grouping algorithm version and policy.

Retain the existing job recovery distinction: a completed stage is reused only after exact inventory, settings and source checks; an interrupted job-stage fit starts a new attempt. The standalone joint stage can resume its verified optimizer checkpoints within its original output directory. Do not introduce cross-attempt checkpoint copying during this adoption. Group membership, profile, actual exported coordinates and all observation arrays must participate in the joint request/checkpoint identity.

The joint stage reuses its reparse-point guards, OS lock, captured-byte loaders, regenerated-input comparisons and output containment checks. Unknown extra files, changed arrays, altered pointers or altered output GLBs fail before reuse/export. Source/implementation changes during work invalidate the result. Grouped standalone recovery depends on immutable snapshots; optional original model/declaration files are checked separately whenever present, before and after fitting. Removing an original therefore does not change recovery identity; changing one rejects reuse. A missing, unsupported or ambiguous group never receives a guessed clear material to make the stage complete.

Reports should distinguish `preparation_status`, `fit_status`, `preview_export_status` and separately observed runtime compatibility. Preserve `selected_material: null`, `accepted: false`, `quality_verdict: unmeasured`, unapplied optional dimensions, and unresolved semantic/camera/articulation evidence. The user's eventual photos-only automation still needs those capabilities; this adoption only connects an effective representation to existing job stages.

## Meaningful verification

1. Run a small actual job/stage with two synthetic photos and a static source containing one multipart closed lens group plus opaque frame geometry. Exercise real preparation, grouped sampling, a bounded joint fit and exported-preview reload; a deterministic test segmentation engine may supply masks. Verify preserved source bytes/frame attributes, complete group membership, exact preview geometry bindings and candidate-only quality.
2. Test explicit declarations and automatic source-part hypotheses separately. Reject initializer/retained-candidate SHA mismatch, stale node/mesh bindings, conflicting group IDs, duplicated membership and an unresolved required member. Include disconnected geometry and two groups sharing one source material.
3. Test observation preservation: same native pixel selection/split ledger across export/reload, multipart-prior deduplication, all hypothesis masks retained, and explicit unknowns for two groups on one ray, opaque stops and depth ties. Do not count unsupported pixels as clear transmission.
4. Interrupt before input commit, during one optimizer start and after preview export but before stage receipt. Verify immutable old attempts, permitted checkpoint reuse only in the bound standalone stage, and byte-identical completed reuse with no inference/export repeated.
5. Tamper with the declaration, model, primitive NPZ, descriptor, preview, receipt pointer and self-consistently rehashed saved observations. Check rejection against regenerated bound inputs. Retain junction/symlink and concurrent-lock regressions.
6. Run shared Python/browser normal-hull, height-feasibility and coincidence counterexamples, then the actual five-design grouped corpus with fixed policy. Run the actual TryOnRenderer controlled optical tests and one production-page legacy smoke. Record source hashes and limitations; no product-specific thresholds or success-count filtering.

The implemented milestone is a reproducible, resumable grouped diagnostic job. Thirteen preparer tests, ten grouped-job tests and nine grouped-stage tests exercise actual preparation, sampling, a bounded joint fit and strict preview reload, plus declaration/array/source mutations and recovery. The small end-to-end job fixture uses controlled camera estimates and deterministic segmentation; it is software integration evidence. Sixty optical-group-focused tests cover the exporter, observer, exact predicates and resource checks. [Current measured corpus and regression evidence](PROGRESS.md) records the final source freeze.

Automatic physical group discovery, multilayer photographic inversion, articulation estimation, calibrated material identification and a final product-quality acceptance gate remain separate work. The actual AR group's previously recorded strict light-depth failure also remains unresolved; no numerical threshold was changed by job adoption.
