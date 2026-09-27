# Five-workstream implementation and measured limits

This pass implements the five workstreams in [the build contract](BUILD_1_TO_5.md). It uses existing product photographs and prioritizes appearance in AR. Physical wearer fit is not an acceptance gate. No Meshy generation, deployment, or publication was performed in this pass.

## Implemented data flow

`job.run_job()` now connects owned-photo intake → automatic articulated refinement → bounded missing-geometry proposals → physical lens membership and face-level repairs → grounded region evidence → shared front/rear optical fitting → actual AR comparison → intrinsic frame proposals → lossless compaction and optional frame LOD → measured delivery report.

Every stage remains resumable and artifact-bound. A changed implementation, photograph, model, export receipt, or measurement cannot silently reuse a completed job. The implementation snapshot now includes the Node simplifier and its installed Meshoptimizer dependency.

### 1. Articulation and shared geometry

- `multiview_intake.run_multiview_intake()` promotes every distinct owned provider/photo view into reconstruction. Previously, side/rear images could reach the AI interpreter without obtaining fitted reconstruction cameras.
- `automatic_articulation.infer_automatic_part_bindings()` derives front, left-temple, right-temple and hinge hypotheses from actual source faces. Names from a generative provider are not trusted as identities.
- `refine_photos._automatic_articulated_run()` fits front cameras, independent per-photo arm states and a shared rest shape. Arm changes need visibility, spatial holdout improvement and an absolute final alignment check. A poor camera/arm explanation is explicitly ambiguous.
- `camera_pose_selection.choose_camera_with_pose_support()` compares frozen front-edge witnesses and absolute temple support. It retains the whole-scene camera when a minor front-only gain loses supported arms, or when the front witness materially regresses. A side visibility swap is allowed; support is compared by count rather than forcing the same arm to remain visible.
- `view_scene` carries the same source-face and photo-hash contract through aperture projection, component composition, membership repair, optical observation and frame texture tracking. Partition transfer uses exact face lineage.
- Exported float32 geometry must be rerendered in multiple views before retention, including the post-completion refinement branch.

**Limit:** front geometry, camera pose and studio reflections can still be ambiguous. Supported arm corrections do not certify the camera or the entire shape. Hinge inference is a manufacturing hypothesis, not recovered engineering CAD.

### 2. Fused faces and missing surfaces

- `face_role_repair.propose_face_roles()` requires visible multi-view aperture consensus, no contrary confident pixels, normal/bend continuation and attachment to an existing optical group. `split_component_ledger()` creates complete/disjoint source-face partitions for fused pieces.
- `physical_group_stage` compares these hypotheses with complete-component repairs and records opaque first-visible geometry in independently eroded lens cores.
- `geometry_completion.propose_missing_geometry()` creates bounded internal lens-hole patches and short frame-curve bridges only with supporting evidence from distinct cameras. Unknown and contradictory pixels do not become positive support.
- `bridge_boundary_curves()` preserves irregular measured end-loop vertices and edges exactly; intermediate rings can be resampled. New triangles are appended without moving existing source triangles or UVs.

**Limit:** a wholly absent component with no observed boundary is not invented. Moving-arm gap completion abstains until both endpoints have an unambiguous common hinge binding. The current constructor does not recover arbitrary concealed cross sections or large missing frame regions.

### 3. Intrinsic frame appearance

- `intrinsic_frame_appearance.run_intrinsic_frame_stage()` tracks the same visible barycentric surface points across photographs, with UV seam protection, exposure normalization and articulated incident directions.
- Removing a baked highlight requires at least three sufficiently distinct observations: agreeing darker views and a brighter observation consistent with the baked texture. Persistent white marks, patterns and logos remain.
- `frame_image_support.build_frame_image_support()` independently replays image-only object-mask consensus, source contrast/alpha and exclusion evidence. Unsupported backdrop/white/transparent samples cannot drive correction or coverage; their texture is preserved.
- `frame_surface_coverage.sample_frame_surface()` uses 32,768 area-stratified surface samples over the complete non-optical area, including untextured, missing-UV, unseen and unsupported surfaces. This fixes the former atlas/triangle-density scoring bug; full/partial same-surface retessellation controls extend through 8,192 triangles. The report separates its raw estimate from the conservative sampling allowance used by the gate.
- `surface_transfer` now preserves actual source vertex colors.

**Limit:** three useful photographs of the same surface are often unavailable. In that case the original texture is retained. Visibility and camera agreement are conditional evidence; they do not independently establish that every projected texel belongs to the photographed frame. Unknown illumination is not solved by selecting a different roughness.

**Measured replay:** `data/build-five/oakley-frame-guarded-v2` finds 162 supported surface samples, a raw area estimate of 0.4944% and a conservative value of zero after a 0.8383-percentage-point sampling allowance. This is far below 60%, even after fixing the representation bug. Zero texture pixels change, and the sealed input is retained byte-for-byte. The result exposes missing reliable multi-view appearance evidence instead of painting guessed albedo.

### 4. Grounded optics and rear response

- `grounded_fit_support` freezes image-derived eligibility before spatial splitting and optimization. Alpha RGB is not treated as a known optical background.
- `rear_correspondence` measures only independently observed rear-object continuation. Missing camera/ray support does not produce a contrast transmission anchor.
- The joint fitter shares manufactured lens material and photo lighting while fitting a separate rear reflection block. Reciprocal transmission and energy accounting are retained.
- Weak-rear-reflection and unrestricted-rear-reflection hypotheses are fitted separately; rear color intervals initialize absorption, without eliminating contrary starts. Both hypotheses survive export and AR comparison.
- `rear_reflection_fraction_rgb` is optional and backward compatible across Python, GLB, CPU reference, camera shaders and shadow shaders. AR cards include a real rear inspection.

**Measured pilot:** the cached Oakley experiment in `data/remaining-repairs/oakley-grounded-rear-v2` completed 72 fits with zero numerical failures and exported six verified GLBs. A weak-rear angular solution converged at 11.514 validation codes with normal transmission `[0.171, 0.038, 0.120]`; the unrestricted solution scored 9.386 but did not converge and retained a competing nearly opaque explanation. Actual AR cards were rendered and visually inspected.

**Limit:** neither candidate exactly matches the product. Unknown illumination/exposure and rear reflection still confound transmission. Preserving a plausible pink transmission hypothesis is a concrete improvement, not proof of recovered optical properties.

### 5. Delivery, conformance and acceptance

- `compact_glb.run_compact_asset()` prunes unreachable storage, deduplicates exact attributes/images, and removes unused frame vertices while retaining bounding-box/sphere witnesses used by AR. Optical coordinates, materials, triangle order and source lineage have strict reload checks.
- `mobile_lod.run_mobile_lod()` proposes bounded frame-only simplification. Optical triangles remain untouched; UVs, colors and authored normals remain source values. Cost-only geometric normals constrain absent-normal/flat-shaded meshes without inventing new smooth shading.
- `delivery_stage.run_delivery_stage()` compares original and packaged assets in the actual AR engine, tests LOD against fixed multi-view image limits, and retains the verified original if the optional LOD regresses.
- `production_validation` separates failure from missing evidence, measures artifact budgets and blocks automatic acceptance on missing physical/independent evidence. Held-out geometry receipts are recomputed from pinned photographs, a complete current-job usage ledger and verified initializer history; intake alone no longer claims imported-model independence. Reencoded training photographs are rejected by decoded pixel identity too.
- `evaluation_reservation` and `evaluation_stage` reserve existing photos before provider/semantic/geometry use, capture all image-consuming stages, verify narrow Meshy request/task/download provenance, and commit the final candidate before scoring. Repaired candidates cannot reuse the same reservation as fresh evaluation. See [request fields and scope](EVALUATION_PHOTOS.md).
- `required_parts.run_required_parts_stage()` measures exclusive visible edge support and hinge-to-tip extent against independent JPEG/alpha support, using frozen source-bound cameras/poses and exact final-corner role correspondence. `replay_required_parts()` recomputes it through the production receipt reader. Missing lineage after LOD, ambiguous poses and unseen tips stay unmeasured.
- Wrong hue, reversed gradients, opaque transmission, absent temples, camera misalignment, changed receipts and budget excesses have explicit controls.

**Actual GPU conformance:** `ar/qa/output/production-optical-groups-viewport-fixed-v2/report.json` passes 80 camera and 39 shadow cases, plus receiver, rear-response, reversed-winding and overflow controls. Maximum RGB error is `5.46e-7`; maximum depth error is `1.12e-7`, below the unchanged `2e-6` threshold. The former depth failure was a QA viewport-rounding error. An independent 150-triangle device probe validated the correction globally; old failures are retained.

**Limit:** engineering thresholds are not statistically calibrated. Without explicit reservation, the job uses all available views. Part corroboration and held-out geometry now have source-bound producers; physical color/transmission and gradient-identification producers remain incomplete, so current real jobs cannot automatically certify a finished product. Successful loading, AI preference and software tests cannot substitute for those missing measurements.

## Independent corpus warning

The reserved RayBan and Invu back/side audit in `data/build-five/heldout-v1` finds minimum foreground IoU 0.405 and 0.623 respectively, with contour p95 errors of 8.82% and 3.68% of width. These are older cached models, not outputs of the new full pipeline. Their historical provider input sets are incomplete, so they are explicitly **not** counted as truly unseen-product proof. This scorer fits a nuisance camera on frozen rigid geometry; it does not yet fit held-out temple articulation, and foreground contrast is not a verified complete silhouette of transparent glasses. The poor scores therefore expose unresolved geometry/camera/articulation disagreement, not a separately measured shape error. These assets cannot be automatically accepted on this evidence.

## Execution evidence

- Final broad Python run: **997 tests, 996 passed and one optional test skipped**, 187.277 seconds. Log: `data/build-five/python-tests-final-v2.log`. After that run, final review fixed an explicit-null pose-contract fallback and interrupted cached-semantic input ownership. The affected suites passed **69 focused tests**: 7 semantic-job integrations, 26 reservation tests, 14 evaluation-stage integrations, 14 required-parts tests and 8 production-validation tests. Logs: `semantic-resume-final-v2.log`, `evaluation-final.log`, `evaluation-stage-resume-final.log`, `required-parts-final.log`, and `validation-final.log` under `data/build-five/`. Earlier runs and their failures remain preserved.
- AR software checks: **415 tests passed**, TypeScript checks passed. Actual GPU conformance is separately listed above.
- Final Miu five-photo replay: `data/automatic-articulation/miu-v4`. Source is stable. Back pitch changes from the old 33.306 degrees to 12.248, with retained arm errors improving from 6.578/6.134 to 0.167/0 pixels. The useful angled +10.928-degree adjustment (1.154→0.177 px) and left +19.220-degree adjustment (1.007→0 px) remain. The intermediate v3 camera policy exposed a real sparse-evidence regression despite passing software tests; v4 fixes it by retaining the prior full-front camera hypothesis when a valid scoring split is unavailable, explicitly without claiming a held-out guard. The shared rest-shape proposal still fails a group nonregression check, so the original GLB is retained.
- Cached Oakley delivery: `data/build-five/oakley-delivery-v3`. Lossless original and LOD compaction both have exact actual-AR pixel parity after preserving bounding witnesses. Original storage drops from **40.43 MB to 24.01 MB**. The bounded LOD reaches 393,170 triangles and 18.95 MB, but fails the fixed local visual threshold (worst 32px tile 6.77 codes versus 4 allowed). It is rejected, preserving the original 641,558-triangle asset. The delivery verdict is **needs_repair**, correctly flagging missing three-view frame support and asset budgets.
- Direct cached Oakley fused-face probe: `data/build-five/topology-probe-v1`, 58 source faces in three supported patches. This proposal count is not a final reconstruction accuracy score.
- Bounded mobile bake experiment: `data/build-five/oakley-normal-bake-pilot/review.md`. Plain simplification (149,978 triangles / 14.01 MB), full source-normal bake (149,978 / 14.02 MB), and visible-contour locks plus bake (149,678 / 13.95 MB) all preserve the 80,542 optical triangles and pass runtime integrity, but all fail the unchanged appearance gate. Their worst 32px tile errors are respectively 8.216, 7.477 and 27.901 codes, against 4.0 allowed. None is accepted. The tests establish achievable storage budgets, not adequate preservation of frame appearance; geometry, tangent/bake compatibility and UV overlap remain unisolated contributors.
- New optical-geometry LOD experiment: `data/build-five/oakley-optical-lod-pilot/review.md`. On the latest full-job candidate, 65,039 optical triangles become 7,999 through fresh group preparation/export with new source bindings; every retained optical position, normal and intrinsic UV is unchanged. This frees a larger frame budget and reaches 149,986 total triangles / 14.53 MB. Actual AR loading and strict receipts pass, but worst-tile error is 5.475 versus 4.0 allowed; normal baking gives 6.52 and visible-contour protection 10.33. None is accepted or added to production. Immutable optical triangle identity is therefore not the technical blocker; the tested reductions still lose local appearance fidelity.

Generated product assets, photos and reports remain private under `data/`.

## Full five-photo Oakley run

`data/build-five/oakley-all-views-job-v2` completed the full chain using the cached initializer and all five original product photographs. There were 960 bounded numerical fits (663 converged), eight exported AR comparison candidates, and two explicit Gemini comparisons with order reversed. Both comparisons chose the same angular green/purple mirror candidate. No new Meshy task was submitted.

The minimum worst-view numerical residual is 19.74 codes, driven by the angled photo. The visually preferred candidate scores 25.05 codes and contains an unconverged local refinement; preference does not erase those defects. I inspected its actual AR card: the broad coating color shift is reproduced, while optical surface/reflection artifacts and frame defects remain. Final validation also fails opaque lens-core coverage (52.09% versus 2% allowed) and optical frame contamination (40.39% versus 2.5%). These diagnostics depend on the masks and fitted cameras; they are not independently measured physical obstruction. The verified packaged asset is 23,874,176 bytes and 641,558 triangles. Its optional LOD is rejected at a worst-tile error of 4.419 versus 4.0 allowed. The full run correctly returns **needs_repair**, never accepted.

Source-pinned parts replay on that final compact GLB succeeds through the production reader: front and right temple have all six extent bins supported; left temple lacks the tip bin and a second useful view, so its state is unmeasured. Receipts: `data/required-parts/oakley-final-v1`. The later camera/frame/acceptance safeguards are checked by dedicated current-source replays and integration tests; the full job's original implementation receipt is preserved instead of rewritten.

`data/build-five/oakley-final-delivery-v1` reruns the current frame guard, area sampling, exact packaging, actual AR renders and required-parts receipt replay on the sealed full-job candidate. It produces the same packaged model hash and the same **needs_repair** verdict; the new part and area measurements are now included in the actual production validation evidence.

## What remains before this is a reliable automation

1. **Geometry and camera ambiguity.** Improve the independently observed foreground/part correspondences feeding `camera_pose_selection`, `automatic_articulation` and `face_role_repair`; validate corrections against the actual side/back photos. Extend `geometry_completion` beyond bounded observed-boundary gaps if recovery of entirely absent components is required. Those extensions are not implemented or demonstrated by the present patch.
2. **Appearance identification.** The current optimizer and AI comparison produce competing plausible explanations. They do not reliably separate material color, studio reflections, exposure and rear radiance. `rear_correspondence` needs reliable real template matches, and independent color/gradient/transmission measurement producers remain missing; their proposed evidence contracts are in `ACCEPTANCE_MEASUREMENTS.md`. Some ordinary JPEG sets cannot provide sufficient physical evidence.
3. **Frame evidence.** Fix camera/foreground correspondence and increase reliable same-surface support before `intrinsic_frame_appearance` can safely modify this product. Oakley's measured 0.4944% raw supported area is inadequate. Lowering the coverage limit would conceal that problem.
4. **Mobile fidelity.** A new geometry/UV/normal reconstruction or baking approach must beat the fixed actual-AR image gate while meeting the size/triangle limits. Both frame-only simplification and the separate optical-reduction pilot failed; neither is a demonstrated solution.
5. **Generalization evidence.** Run genuinely reserved, traceable product/view cases and separate held-out nuisance-camera calibration from scoring pixels. The completed Miu/Oakley development runs and old RayBan/Invu audits do not establish catalog-wide accuracy.

The five implemented paths provide a functioning candidate-generation and rejection pipeline. They do not yet provide a demonstrated way to perfect arbitrary existing-photo reconstructions, and current real products are not certified for automatic acceptance.
