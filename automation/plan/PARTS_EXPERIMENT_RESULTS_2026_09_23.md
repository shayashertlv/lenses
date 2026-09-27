# Parts and optics: completed experiments, 23 September 2026

**Decision:** use segmentation of a retained textured Tripo generation as the next pipeline route. Lens appearance can be replaced on both tested products without reconstructing a lens or physically reconnecting pieces. This is a demonstrated route on Oakley and rimless Miu, not a catalog-wide accuracy claim. Automatic identification still needs an uncertainty/review path, and final photographic appearance is unfinished.

Ten paid inference requests completed: six Tripo jobs and four SAM3 calls. Tripo receipts report **280 credits ($2.80 nominal equivalent)**; SAM3 is **$0.02 estimated**, not invoice-confirmed. There were no paid retries or uncertain submissions. Original generation costs are excluded. Requests, results, immutable artifact hashes and costs are in [the experiment ledger](../data/part-probes-v1/experiment-ledger.json).

## What actually worked

![Separation experiment](../data/part-probes-v1/summary/lens-separation.png)

The right column deliberately uses clear diagnostic materials. It does not claim to recover Oakley's purple/green mirror coating. Standard glTF renders demonstrate lens selection independently of the custom AR renderer; the compact assets were also loaded separately through the actual AR engine.

| Experiment | Oakley | Rimless Miu | Decision |
| --- | --- | --- | --- |
| Segment existing textured model | 19 parts; parts 0/1 cover the shield | 16 parts; parts 0/1 cover lenses | Best tested base |
| Generate native parts from the original four photos | 28 parts; lens regions have long notches resembling temple profiles (their photographic cause is unproven) | 13 parts; plausible broad lenses | No demonstrated advantage; reject Oakley result as preferred base |
| SAM3 on front/angled model renders | Finds shield in both views | Front returns no mask; angled finds both lenses | Use multiple known render cameras; never rely on front alone |
| Segment with colored guidance from those masks | 6 parts; continuous shield in part 1 | 6 parts; broad lenses 1/2, but separate thin upper arc 3 | Fallback, not an automatic improvement |
| Clear material on selected auto parts | Both shield halves transmit | Both lenses transmit; hardware remains opaque | Rebuilding/reconnecting is unnecessary for these candidates |
| Per-part simplification, storage compaction, optical preparation | 119,983 triangles; 6.16 MB | 126,123 triangles; 5.45 MB | Both pass actual AR loading and synthetic settled fitting |

The guided Miu control is an important counterexample. Selecting only its two large lens parts leaves an opaque upper arc. Adding part 3 to the appropriate optical group removes the arc. The visible-mask voting rule did not select that strip. Therefore "the API returned parts" and "we found two large masks" are insufficient acceptance criteria.

Evidence: [SAM3 masks](../data/render-mask-probes-v1/contact-sheet.png), [visible part hypotheses](../data/part-mask-correspondence-v1/visible-part-hypotheses.json), [guided Miu controls](../data/part-probes-v1/guided-optics-controls/renders/miu__raw__checker__comparison.png), and per-part images under `data/part-probes-v1/audits/`.

## The Oakley white rectangle: a corrected diagnosis

![Reflection controls](../data/part-probes-v1/summary/reflection-cause.png)

We tested competing causes instead of treating a bright patch as proof of failed separation:

1. Ray tests located real nose-support geometry behind the shield. Making that hardware black did **not** remove the patch. Spatial overlap had not established causation.
2. Disabling optical reflection at every incidence angle removed the patch completely while preserving lens geometry and all frame materials. A fixed diagnostic ROI went from **661 near-white pixels to zero**.
3. Increasing optical roughness from 0.05 to 0.4 spread the highlight instead of removing it.
4. The existing bounded smooth-normal fit passed its unchanged guards (quadratic surface, holdout p95 0.0080 versus 0.02 limit). It retained positions, indices and material records. The reflection became more regular but remained a bright rectangle; this is not a removal fix.

The current AR renderer creates a `RoomEnvironment` in `ar/src/render/renderer.ts` and supplies its PMREM to canonical optical materials. The counterfactuals establish that this visible patch is a live optical reflection, not an opaque lens fragment requiring deletion. A rectangular highlight can be an expected reflection of the synthetic lighting environment. Final material evaluation must separate that chosen environment from the original product's coating. Disabling reflection entirely is a diagnostic, not the correct final Oakley material.

Evidence: [reflection ROI](../data/part-probes-v1/summary/reflection-roi.json), [actual AR reflection run](../data/part-probes-v1/reflection-ar-check/report.json), [smooth-normal comparison](../data/part-probes-v1/optics/oakley-smooth-reflection/comparison.json). The failed black-hardware controls remain in `final-ar-check/`; they are not fixes.

## Geometry, labels and storage checks

Segmentation preserved triangle count for both products. It did **not** preserve world coordinates or UVs bit for bit: parts acquire local float32 coordinates/translations and repacked texture atlases. Strict matching therefore failed for most faces.

`qa.part_probe_audit.bounded_correspondence()` instead verified all three corners and winding under a unique global face correspondence. All auto and guided segmentation faces mapped bijectively to the retained originals, with no unmatched, ambiguous or multiply used source faces. Maximum measured corner errors were below **6.8e-8 in provider world units**. These are bounded approximate correspondences, not exact geometry equality. The saved original-face labels could be used to partition the original mesh later; this experiment's delivered candidates use the provider's segmented meshes, so original-atlas label transfer is **not yet integrated**.

Known-camera rasterization connected the SAM masks to those labels. CPU/GPU silhouette IoU was 98.01–99.00%, with disagreement within two pixels of the boundary. Visible votes selected auto parts 0/1 on both products. This validates the coordinate chain sufficiently for this experiment; it does not label unseen surfaces or prove that all faces of a selected part are optical.

Simplification retained original attribute/image bytes but changed triangle indices. At five tested views, minimum silhouette IoU against the segmented source was **99.91% Oakley / 99.68% Miu**. Largest sampled optical p95 deviations were about **0.0219 / 0.0185 mm** at an arbitrary 145 mm display width. These are sample comparisons to generated geometry, not physical measurements or a global distance bound.

**Order matters:** simplify → compact unused raw vertex records → prepare optical groups → compact the final export. Preparing optics before raw compaction kept hundreds of thousands of unused optical vertices. Fresh preparation is required after changing triangle topology. No optical/runtime limits were weakened.

See [LOD measurements and reproduction](../data/part-probes-v1/lod/README.md). The actual AR fixtures use synthetic face landmarks and a controlled checker background, not a real wearer. An apparent checker-preview anomaly was checked numerically: the source and bare AR images are byte-identical, and the final background retains 32-pixel transitions. [Pixel verification](../data/part-probes-v1/checker-debug/pixel-verification.json).

## Reusable functions now exercised

| Stage | Function/tool | Status |
| --- | --- | --- |
| Bounded provider experiment, retained task inputs, guided fallback | `qa.part_probe.prepare`, `prepare_guided`, `submit` | Six real jobs completed; request/receipt recovery recorded |
| Render-image lens evidence | `qa.render_mask_probe.prepare`, `submit`, `poll`, `review` | Four real SAM3 responses retained, including the empty result |
| Triangle lineage | `qa.part_probe_audit.bounded_correspondence` | Complete bounded bijection verified on all four segmentation outputs |
| Mask-to-part evidence | `qa.part_mask_correspondence.raster_source`, `score_parts`, `propose_visible_parts` | Camera-calibrated visible hypotheses, not automatic whole-part acceptance |
| Geometry reduction | `qa.part_lod_probe.run` | Both products measured across five views |
| Raw and prepared storage cleanup | `reconstruction.compact_glb.run_compact_asset` | Active semantics verified; optical receipts resealed |
| Canonical placement and reviewed group declaration | `qa.part_optics_probe.canonicalize`, `run` | Oakley `[[0,1]]`; Miu `[[0],[1]]`; width recorded as display convention |
| Standard material / hardware / reflection controls | `standard_control`, `plain_frame_control`, `appearance_control` in `qa.part_optics_probe` | Causal tests above; source assets retained |
| Optional smooth optical normals | `reconstruction.smooth_optical_geometry.run_smooth_optical_preparation` | Supported Oakley fit; not a demonstrated photo-appearance improvement |
| Real runtime handover | `python -m qa.provider_comparison --manifest ... --output ... --ar-check` | Compact candidates accepted by current AR loader; synthetic fitting ready |

The preparer and material experiments still consume **reviewed explicit group declarations**. The visible classifier agrees on the main auto parts, but no production stage silently promotes its hypothesis into accepted lens identity. These QA helpers have not been wired into `reconstruction.job` as a fully automatic Tripo route.

## What remains, concretely

1. **Promote the successful route into the job state machine.** Retain the textured generation task, request segmentation once, verify output hashes/correspondence, build role evidence, then perform reduction/compaction before optical preparation. Reuse the existing grouped exporter; do not add a physical detach/reconnect stage. The helpers above are the tested building blocks, not a second geometry architecture.
2. **Close the semantic edge/hidden-surface gap.** Combine known-camera votes with part adjacency and boundary continuity. Preserve alternative groups for strips like Miu part 3; inspect additional rendered views when evidence conflicts. A mask of the shield includes rear hardware when viewed through it, so projected overlap alone cannot turn that hardware transparent. Accepting every large near-lens part is also unsafe.
3. **Recover coating and frame appearance on these new groups.** Exercise the existing photo/semantic appearance stages against this successful segmentation. Clear Miu is a useful first target. Oakley needs a colored, angle-dependent mirror model; copying the purple/green pixels or permanently suppressing reflection would encode the wrong behavior. Evaluate a fixed material under several explicit lighting environments so the synthetic room's white rectangle is not mistaken for product color. Current neutral controls establish editability, not material recovery.
4. **Validate beyond these two designs.** Include full-rim acetate, metal aviator, tinted gradient, and semi-rimless examples. Use held-out product views where available. Inspect lens boundaries, bridges, thin temples, logos, baked frame highlights and rear view. A successful loader and high source-to-LOD IoU cannot detect a provider's originally wrong silhouette.
5. **Check real delivery.** Both assets meet the current triangle/byte budgets, but mobile GPU timing, actual face visibility, occlusion and visual appearance still need evaluation. Synthetic fit readiness is not proof of those qualities.

The next expensive uncertainty is final appearance/generalization, not whether these two lenses can be separated. A new parts API is not justified by the present evidence. Guided segmentation is available as a measured fallback; native parts generation was a worse Oakley starting point in this test.

## Inspect and reproduce

Working neutral AR candidates:

- [Oakley, 119,983 triangles, 6,161,808 bytes](../data/part-probes-v1/optics/oakley-lod-compact-neutral/compact/compact.glb)
- [Miu, 126,123 triangles, 5,450,100 bytes](../data/part-probes-v1/optics/miu-lod-compact-neutral/compact/compact.glb)

Both are diagnostic candidates, not final approved product models. [Actual AR results](../data/part-probes-v1/final-ar-check/report.json) contain asset hashes, loader status and pose outcomes.

Read-only/local reproduction (fresh renderer output directory):

```powershell
python -m qa.part_probe_summary
python -m qa.provider_comparison --manifest data/part-probes-v1/reflection-ar-manifest.json --output data/part-probes-v1/reflection-ar-replay --ar-check
python -m unittest discover -s tests -p 'test_part*probe*.py' -q
python -m unittest discover -s tests -p 'test_part_mask_correspondence.py' -q
python -m unittest qa.test_part_probe_audit -q
```

All **20 focused tests pass** (9 probe/canonicalization, 4 mask correspondence, 7 adversarial geometry audit). Browser runs establish the reported runtime outcomes. Tests do not establish visual correctness. No live deployment, provider-key changes or repository commits were made. New Python tools, data and this report stay in the local automation directory; AR modifications for this experiment are confined to the provider-comparison QA harness.
