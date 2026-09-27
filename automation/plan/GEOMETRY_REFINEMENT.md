# Shared multiview refinement

2026-09-21. Implemented development stage, not a complete photo-to-model generator or an appearance acceptance gate.

## Purpose

The historical initializer fixes most frame geometry before image comparisons. This stage permits bounded corrections to that geometry using one shared 3D field. It starts with a static, embedded GLB facing +Z with Y up, and distinct product photographs with optional angle priors. Existing geometry, units, component partitioning and articulation are inherited; it does not create missing members or establish correct topology.

There are no product-specific fitting constants. One policy runs on full rims, wire/clear-lens cases and shield-like models. Failing cases remain failures; a valid exported file is not a quality pass.

## Evidence and fitting contract

1. Reject duplicate file bytes or decoded pixels, unsupported labels, unrecorded EXIF rotation, and nonempty output directories. Pin input files, source code, dependency versions, working RGB pixels, cameras and typed observations.
2. Estimate camera hypotheses from background contrast using full geometric footprints and compacted declared opaque parts. Photo identity is separate from its optional view prior, so labels can repeat. Unknown views test eight yaw directions at two pitch seeds, then optimize the best seed basin within the same evaluation budget. The seeds and unknown provenance remain reported; this is not semantic view identification or exhaustive ambiguity analysis. Material roles are hypotheses: the saved Miu Miu asset even labels a silicone piece as a lens. Choosing the lowest residual from the same photo is inference, not independent validation.
3. Rasterize visible surface points with perspective-correct barycentrics. Search bounded distances along their image normals for RGB edges. Multiple strong peaks stay ambiguous; missing peaks supply no coordinates. Shadows/reflections can still be selected, so these remain candidate-dependent image edges with unknown semantic coverage.
4. Save edge positions in original pixel-center coordinates. Normals transform as covectors under resizing; scalar normal uncertainty transforms with them. The tangent direction remains unconstrained. A point selected along a contour is not automatically an identified 2D/3D landmark.
5. Fit one trilinear cage against all usable views, leaving cameras and observations fixed. Weighted source-surface similarity moments remove global translation, infinitesimal rotation and uniform scale from the fitting field. The optimizer enforces that gauge internally. Smoothness/size priors are explicit; they are excluded from data-only local Jacobian rank diagnostics.
6. Retain only correspondence improvements with per-view nonregression. When labeled groups exist, each group also requires nonincreasing RMS and at most one sigma increase at any frozen point. Current automatic edges have no anatomical group labels, so localized component quality is unmeasured. Any withheld constraints used for retention are selection validation, not an untouched test.
7. Check the continuous field gradient bound separately from exported triangle validity. A smooth injective field can still invert a thin triangle when sampled only at its vertices. Generic backtracking halves the entire field, reevaluating the identical retention policy and export checks at every trial. This is bounded to six reductions and never weakens a check.
8. Export in the original GLB coordinate system, preserving UVs, indices, textures, materials, metadata and transforms. Transport normals and tangents analytically. Check float32 triangle collapse/inversion and report sampled midpoint approximation error. Reload the exported GLB and rerasterize using the original normalization and frozen cameras.
9. Report selected photo-edge to candidate-boundary mean/p95/max distances. Mean and p95 must not regress beyond a stated 0.25 working-pixel sampling allowance to keep the proposal filename. Rejected results use `rejected-proposal.glb`; every run retains `quality_verdict=unmeasured`.

## Reproduce

```powershell
python -m reconstruction.refine_photos --model C:/path/model.glb --photo front=C:/path/front.jpg --photo angled=C:/path/angled.jpg --output data/refinement/new-run --resolution 320 --camera-evaluations 160
python scripts/refinement_corpus.py --manifest data/refinement-corpus.json --output data/refinement/new-corpus-run --resolution 320 --camera-evaluations 160
python -m unittest discover -s tests -q
```

The private corpus manifest is ignored by Git. Its format is documented in the batch script and can point to other products without changing fitting code. It uses the same settings for every case, preserves failures and emits `summary.json`. Use `--photo detail-1:angled=path` to separate identity from a repeated prior or `--photo detail-1:unknown=path` when the angle is unspecified. The [job flow](JOBS.md) normalizes and records EXIF/color transforms before invoking this stage.

`optimization-field.json` describes the optimizer result. When export backtracking chooses a smaller field, `field.json` describes the actual exported candidate, while `export_backtracking` records every tested scale and reassessment. Optimizer/gauge/data-rank diagnostics in `deformation_fit` describe the original optimization result; they must not be relabeled as measurements of a later scaled field.

## Limits that prevent acceptance

- Contour selection depends on the starting mesh; missing geometry and arbitrary details outside the search radius are not recovered.
- Camera uncertainty, semantic segmentation, per-photo articulation and optional dimensions are not solved by this stage. Inherited physical scale is preserved, not verified.
- Different photo files do not guarantee useful depth information. Parallel/antiparallel fitted camera axes are diagnosed; adequate axis separation is only a necessary condition, not proof of identifiable shape.
- Continuous injectivity does not certify discrete global self-intersections, rim/lens contact or gaps between differently tessellated surfaces. Midpoint errors are samples, not a global approximation bound.
- A trilinear cage is continuous but generally not continuously differentiable across cell boundaries. Transported vertex normals do not prove a smooth optical surface; mirror-sensitive lens geometry needs a separate surface/normal validation contract.
- One-sided distances to selected edges can match the wrong boundary or hide a local defect. Independent semantic component evidence, bidirectional checks and held-out products remain necessary.
- This stage preserves existing materials. Accurate gradient and mirror inference from ordinary photos remains unfinished. A bounded canonical production AR rendering profile now exists, but generic photo-conditioned material assignment and optical-layer compatibility remain separate requirements.

The tests include deliberately ambiguous/missing edges, unmeasured tangential motion, perspective Jacobians, hidden localized regression, similarity drift, a continuously valid field that flips a discrete triangle, and an actual synthetic two-photo CLI run. They establish these contracts; they do not establish universal reconstruction quality.
