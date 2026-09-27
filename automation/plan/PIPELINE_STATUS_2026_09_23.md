# Pipeline status after the remaining-issues repair pass

Goal: existing product photographs to an accurate-looking model for AR. Absolute wearer fit is secondary because the AR engine adjusts model width. This pass improves the implemented reconstruction path; it does not establish a perfect, unattended product reconstruction service.

## Implemented and connected

### 1. Omitted optical fragments

`optical_membership.audit_optical_membership()` measures first-visible source components inside independent lens-aperture proposals across cameras. It checks agreement with a neighboring lens surface, physical adjacency, fragment size, camera diversity and contrary views. A component must pass the complete rule before becoming an additional hypothesis. Fused frame/lens components remain opaque and unresolved.

`physical_group_stage.run_physical_group_stage()` adds these repairs to the existing composition ranking, verified face partition, preparation and camera-transfer path. No source triangles are deleted or reshaped by this repair. The original grouping alternatives remain available.

The cached Oakley omitted fragment passes in both front and angled photographs. Declaring it optical reduces unresolved opaque support inside the conservative lens core from **1,379 to 508 front pixels**, and **209 to 96 angled pixels**. These are conditional projection measurements, not manually labeled ground truth. The repair has better composition rank: minimum usable share **0.892**, versus **0.810** previously; primary-view contamination **0.0153**. Remaining support includes the mixed front assembly and possible registration errors.

An actual AR control with every declared optical material hidden passes runtime checks and visibly removes the former broad purple side patch. Small green/pink bridge and rim remnants remain. Shadows stay enabled, so faint lens-shaped shadows are not evidence of opaque geometry. Control: `data/semantic-repair/oakley-repaired-hidden/repaired-shield.png` and its pinned report.

### 2. Smooth optical normals, with an original-normal control

`smooth_optical_geometry.fit_front_bend()` fits bounded smooth single-surface or paired-skin hypotheses. Paired skins require overlapping coverage, consistent depth ordering, limited separation and agreeing normal directions. `run_smooth_optical_preparation()` preserves positions, triangles, UVs and frame textures exactly; unsupported groups keep their original normals.

The semantic job runs this before fresh optical observations. The AR appearance stage also renders an original-normal control using the same material parameters, so smoothing is not silently treated as proven product curvature. Actual source data and preparation reports remain pinned.

All five original cached groups pass the final construction guards, with 95th held-out surface residuals of **0.44–1.02% of group width**. This measures consistency with the generated mesh, not independent photograph accuracy. Actual AR controls show more coherent highlights. Details and control artifacts: [smooth surface evidence](SMOOTH_OPTICAL_GEOMETRY_2026_09_23.md).

### 3. Grounded color and rear-view evidence

`image_appearance_evidence.ground_semantic_regions()` intersects semantic boxes with independent aperture alternatives and removes uncertain boundaries, strong image edges and proposed reflections. Numerical anchors in `sample_appearance_anchors()` now respect those masks. Source images, masks, engine configuration and cache artifacts are bound by hashes.

`sample_image_appearance_evidence()` measures image color without inventing 3D coordinates. Explicitly labeled rear views can supply bounded display-transmission scenarios for a supported single lens group. `ar_material_search._rear_transmission_proposals()` converts them into energy-feasible alternatives while preserving contrary coating hypotheses.

Oakley's two rear regions contain **57,344 usable pixels** and both measure median RGB **[214, 118, 156]**. The three resulting pink transmission scenarios retain their exposure/reflection assumptions. Miu's approximately **69,900 clipped white pixels** remain physically inconclusive. More white pixels do not establish a transparent material uniquely.

### 4. Shared paired-lens materials

`material_relations.infer_material_relations()` proposes a source-bound manufactured pair only for credible two-lens construction. The joint fitter tries shared density/reflection parameters and an independent alternative. Fitted exports preserve both configurations; measured density proposals can pool evidence across the pair.

On the same frozen VB branch, worst validation error improves from **6.405 to 5.556 codes**, while parameters fall from **37 to 22**. Miu uniform error is essentially unchanged (**3.18199 versus 3.18204**, **11 to 8 parameters**). These scores are conditional on the cached observations, not unseen-product accuracy.

### 5. Bounded mask search

`JointPhotoLensFitPolicy.mask_search_mode='conditional_seed_beam'` retains exhaustive enumeration for small products and uses explicit material/light seeds for larger ones. Every candidate is evaluated on the same complete observation union; a candidate cannot improve its evaluation score merely by selecting an easier mask. The semantic job enables a 16-branch cap and an eight-entry beam. Existing run budgets remain enforced.

The real VB experiment reduces **81 combinations to four branches and 48 starts**, completing the fit in **14.27 seconds** without numerical failures. Its full-union error is much higher than the frozen-branch error because the union includes bad mask alternatives. This is evidence of remaining segmentation/registration problems. The method is a bounded approximate search, not a global optimizer or a complete alternating solver.

### 6. Provider execution

The CLI can continue an authorized asynchronous generation into its split task. Poll-only resumes retain a read-capable split transport. Generation and split share one allowance for newly reserved submissions; existing task polling/downloads do not consume it. An uncertain POST keeps its reservation. The allowance limits estimates, not the provider's eventual invoice.

Provider-only photos now have exact-byte, hash-bound snapshots before submission. New resumes use those owned files. Legacy paid tasks can still be polled when an unsnapshotted original disappeared; the missing image remains an explicit evidence gap. Semantic replay uses verified intake records, including generated photo IDs and normalized paths. Oblique/top labels are normalized without treating an oblique rear photograph as axial back evidence, and more than twelve semantic inputs are rejected before paid initialization.

## Work still required, in priority order

1. **Real articulated multi-view reconstruction.** `structured_refinement.propose_part_bindings()` still validates supplied face/hinge bindings. It does not infer reliable bindings. `fit_front_cameras()`, `fit_temple_poses()`, `fit_shared_geometry()` and `view_scene.pose_scene()` exist, but production `refine_photos`, component projection, region proposals, ray composition and optical observations still use rigid geometry. The next concrete work is automatic front/left-arm/right-arm face hypotheses and hinge anchors, followed by one view-state contract passed through every projection/ray consumer. Otherwise folded or differently opened arms can distort camera and frame estimates.

2. **Fused and missing geometry.** The new membership repair handles supported complete fragments. It cannot safely split Oakley's mixed frame/lens assembly. The next function should generate face-level role probabilities from visibility-aware multi-view image evidence and surface continuity, then feed complete disjoint partitions to the existing verified exporter. Missing bridges, rim sections or temples require a new construction representation: image-supported lens loops, front bend, connected bridge/rim curves and temple centerlines. Cage deformation and smooth normals cannot create missing connectivity.

3. **Intrinsic frame appearance.** `surface_transfer` copies the provider's texture, including baked highlights. It does not recover albedo, metalness or roughness. Implement camera-bound cross-view frame-texel correspondences, protect persistent patterns/logos, separate view-dependent highlight evidence and compare a small set of material hypotheses in AR. A blanket texture recoloring rule would destroy legitimate patterns.

4. **Stronger optical evidence and cleaner evaluation support.** Grounded masks currently constrain anchors and new image evidence; the complete numerical mask union can still contain incorrect alternatives. Add a frozen, image-grounded eligibility domain before bounded fitting, without selecting it to reduce residuals. For physical transmission, implement observed rear-object correspondences and bounded registration before calling `estimate_transmission_from_contrast()`. The generated model's texture is not an independent rear template. The integrated experiment rejected the three rear-transmission scenarios as too transparent/weakly mirrored; simple proxy proposals have therefore not solved the front/reflection versus rear/transmission tradeoff. Jointly refit absorption and angular reflection with rear-view nuisance terms, and extend AR cards to rear views instead of relying only on front, oblique and rolled front views. Clipped or unobserved appearance must remain ambiguous.

5. **A genuine acceptance gate.** Current outputs remain `accepted:false`. Define measurable contour, optical-membership, material and artifact limits on an unseen-product corpus with held-out views. Include wrong hue, reversed gradient, opaque lenses, missing temple and bad camera controls. AI render ranking is a useful relative preference, not a calibrated pass/fail score. The system needs to distinguish usable results from products requiring intervention.

6. **Input, performance and deployment coverage.** Transparent PNG photographs still lack an alpha-aware geometry-observation branch and may be excluded from refinement. The repaired Oakley GLB is approximately 40 MB, so dense provider geometry still needs mobile size/performance testing and constrained simplification. Repeated candidate export/geometry verification also takes substantial time. A concrete optimization is to render descriptor variants against one pinned geometry asset, export the chosen variant once, then verify that the exported render matches the tested descriptor render; geometry-changing alternatives need their own asset. The pre-existing AR shadow-depth conformance failure remains unresolved. This pass did not publish `ar/site` or change the live Python deployment.

The next highest-value milestone is automatic articulated geometry and face-level frame/lens separation on a held-out product set. More unbounded AI calls or a larger material search will not repair those missing capabilities.

## Reproduction and local evidence

The final Python run completed **845 tests in 118.505 seconds: 844 passed, one skipped, no failures**. This includes real orchestration transitions with mocked HTTP, snapshot integrity/recovery, bounded-search controls and geometry/GLB roundtrips. It does not replace visual accuracy evaluation.

The final physical-group replay selected the repaired Oakley hypothesis automatically at rank zero and successfully bridged all three alternatives. The source-preserving partition SHA-256 is `2ddb3e71defd6b5fe4fc8a7790b8ded27257fe2ab31d58e747a8ca13ac75dfaa`; the report is `data/semantic-repair/oakley-membership-final/report.json`.

The integrated refit completed **120 starts over four retained branches**, with zero numerical failures and five optimizer runs still unresolved at the 60-evaluation cap. All five material families exported. All **eight AR comparison assets passed the actual runtime harness**. The first reviewer response used the explicit image label `candidate-A` instead of its short form `A`; the parser now accepts those two exact forms while still rejecting duplicate, unknown or missing identities. Its final change passes fourteen focused comparison/search tests, including two new protocol regressions.

Two actual review calls were used in total; the first response and all rendered assets were reused during recovery. The review orders selected different normal variants of the same fitted material. The final result is **`comparison_unresolved_baseline_retained`**, not a stable visual winner or an accepted model. The final model and review receipts are `data/semantic-repair/oakley-review-final/candidate-appearance.glb` and `report.json`. Its card is `data/semantic-repair/oakley-integrated/appearance/renders/material-0751272d84c1083783fa.png`. Broad side-patch membership is corrected, but frame/rim defects and uneven optical appearance remain visible. No new Meshy generation or splitting task was submitted in this repair pass.

- `python -m unittest discover -s tests -q`
- `python -m qa.repaired_membership --help`
- `python -m qa.remaining_pipeline_repairs --help`
- `python -m qa.shared_material_pilot --help`
- `python -m qa.smooth_optical_geometry --help`

The integrated repaired Oakley artifacts are under `data/semantic-repair/oakley-integrated`. Pair and bounded-mask experiments are under `data/remaining-repairs`. Grounded-image evidence is under `data/semantic-repair/{vb,miu,oakley}-grounded-evidence`. Source-only smooth-normal controls are under `data/smooth-optical-geometry`. All product data and models remain local, outside Git.
