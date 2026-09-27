# Build plan: product photographs to reviewable AR assets

This is the implementation contract for the five requested workstreams. Existing photographs are the only inputs; metric face fit is not an acceptance requirement.

1. **Articulation.** Infer front/left-temple/right-temple source-face bindings and hinges; fit front cameras and per-view temple poses; carry the source-bound articulated scene through all projection and ray consumers. Preserve a canonical rest mesh. Reject ambiguous role assignments and unsupported hinge motion.
2. **Topology.** `face_role_repair.propose_face_roles()` combines visibility, independently proposed aperture consensus, contrary pixels and fitted lens-surface continuation. `split_component_ledger()` converts supported face subsets into virtual components for the existing complete/disjoint partition exporter. `geometry_completion` constructs bounded lens-hole patches and frame-curve gap proposals, requiring multiple photographic views and explicit nonregression before export.
3. **Frame appearance.** Reproject photographed frame surfaces, compare independently observed colors at the same surface locations, remove only supported view-dependent highlights, preserve persistent color/pattern evidence, and export bounded PBR settings with measured coverage.
4. **Optics.** Freeze image-grounded fitting support before optimization; register observed rear-object templates; add uncertain transmission constraints without flattening angular reflection; render rear views alongside front and oblique controls.
5. **Acceptance and delivery.** Source-bound measurable acceptance gates, held-out product/view evaluation, deliberately wrong controls, artifact budgets, complete evidence receipts and a production stage connected to the resumable job. An incomplete measurement produces `needs_review`, never an invented pass. Mobile packaging preserves optical contracts.

Verification is layered: meaningful numerical/lineage regressions; source-frozen real cached runs; actual AR runtime renders; held-out product reports. A code path and a usable product are separate claims. The final report must identify both.

Shared interfaces and file ownership are assigned before edits. Global implementation hashes require a source freeze before long immutable stages. Product data, private models and generated QA remain local. No live application deployment or AR publish is part of this task.
