# Image apertures and source-component evidence

2026-09-22. This connects the image-only proposals from [the semantic experiment](PHOTO_SEMANTIC_INFERENCE.md) to the complete source components from [the partition stage](FACE_PARTITIONS.md). It supplies measured relationships for physical-group inference; it does not select a lens group or material.

## Image evidence stays independent of generated labels

`reconstruction.aperture_evidence.load_aperture_evidence` loads the two pinned semantic experiment reports for an exact normalized image SHA. It verifies original/normalized snapshots, source normalization, crop transforms, logits, independently recomputed component labels, saved mask bytes, component ledgers and all decoder inventories. No neural model runs, generated-mesh prompt, frame classifier, flipped prediction or display union enters this adapter.

Every coarse component from both unflipped crop policies remains a separate hypothesis, including the three tiny components omitted from SAM prompting in the frozen experiment. Each retained prompt contributes all three separate SAM alternatives. Coarse predictions have a known domain restricted to their source crop; the exterior remains unknown. SAM alternatives have a full native-image prediction domain. “Known” means the model supplied a prediction, not that its semantic label is correct.

Arrays are returned read-only, with domain arrays shared where applicable. Resource limits reject an oversized request instead of selecting a subset. The adapter retains input snapshot hashes and upstream model/code provenance, but does not reload or revalidate the historical model binaries. Report integrity is not semantic accuracy.

All 24 frozen photos load under the adapter: **94 coarse components and 273 SAM alternatives**. White produces no hypotheses. The mug's false positives remain. Largest returned boolean-array budget is 97,843,200 cells/bytes; fifteen independent adapter tests cover identity, normalization, crops, missing alternatives, tiny components, corruption and resource limits.

## Geometry is projected without optical-role filtering

`reconstruction.component_projection.project_components` consumes a complete source mesh, one component label per original triangle, a camera and an exact image grid. Every component is rasterized independently. The sparse result retains working pixel indices, original global source-face indices, barycentric coordinates and nearest depth. Components with no pixel-center hit remain in the inventory with empty arrays; they are not declared absent or discarded.

A separate full-scene first-hit field treats **every surface as opaque**. That field is only a geometric diagnostic. It cannot decide actual visibility through a clear or tinted lens. Exact ownership can also select only one of two coincident source shells, so depth compatibility is recorded separately from source-component ownership.

The complete source inventory comes directly from the GLB, including alpha-zero primitives. It must not be reconstructed from a loader that silently filters invisible materials. Local positions are transformed with each source node's unchanged world matrix. Component identities retain source binding and local component ID alongside global face offsets.

The saved camera hypothesis applies to its recorded normalization:

`camera_position = (source_world_position - saved_center) / saved_extent`

That normalization is reused exactly, including when a more complete geometry inventory is added. Recomputing a new normalization while retaining the old camera would change the hypothesis. Depth differences are converted from those camera-input units to source-extent units using `saved_extent / referenced_source_world_extent`; both quantities and their definitions remain recorded.

The ten historical cameras bind original development models, not the later refined proposals. All ten original-source first-hit replays match their saved face-index arrays exactly. They remain candidate-fitted cameras, not independently calibrated photographic truth. Projection rejects unsupported near-plane crossings and nonfinite/out-of-domain coordinates; it does not silently clip them.

## Denominators distinguish small contained pieces from broad surfaces

For a piece footprint P, an aperture hypothesis A and its known domain K, `measure_component_apertures` records:

- Complete projected support, known support `|P ∩ K|`, and unknown support `|P \ K|`.
- Intersection `|P ∩ A|`, piece support outside the aperture but inside K, and aperture support not explained by that piece.
- Both directional fractions: piece-inside-aperture and aperture-covered-by-piece; IoU and known-area ratio.

A pad contained within a lens outline can have perfect piece-inside-aperture fraction while covering only a small fraction of the aperture. Keeping both denominators exposes that distinction. Empty denominators are null, never a perfect score. Crop exteriors cannot improve a fit by being mislabeled negative evidence.

Every pair of pieces is tested for projected overlap. Positive-overlap pairs retain both containment fractions, IoU, signed and absolute depth-gap quantiles, and the number of samples with each depth ordering. Quantiles use probabilities 0, 0.1, 0.5, 0.9 and 1. A signed median near zero is insufficient: crossing sheets may have large absolute gaps and reverse order across the aperture. Pair depth summaries are also computed within each separate aperture hypothesis. Zero-overlap pairs are counted explicitly; this is zero projected overlap on the declared grid, not evidence that the pieces are unrelated in 3D.

Depth tolerance is explicit and expressed in the declared reference units. The initial fixed corpus policy uses zero, so exact floating-point ties and tiny arithmetic differences remain visible rather than becoming a tuned semantic threshold. Per-piece records additionally report hypothetical opaque blockers and any numerical disagreement with full-scene depth. Neither ordering nor overlap establishes material identity, physical lens count or an accepted merge.

## Fixed experiment and interpretation

The corpus runner binds all five source inventories, both saved views per design, complete image-only alternatives and the original cameras. Native image masks/domains are sampled to the working camera grid at the corresponding source-cell centers. The mapping is explicit and preserves unknown domains. This downsampling is not an inverse of expanding the working geometric raster to native resolution; thin boundaries can be unmeasured at this grid.

No per-product component choice, source optical role, decoder selection, mask union or material fit belongs to the inference input. Diagnostic displays may union masks or show the largest projected components for readability; all original alternatives and components remain in the saved numerical evidence.

Analytic tests cover hidden rear layers, coincident shells, crossing sheets, perspective ray-plane depth, source-face/barycentric lineage, small contained pieces, unknown domains, degenerate/subpixel components and capacity failures. These establish the measured relation definitions.

The frozen runner completed all five sources and ten views in **13.03 seconds**, retaining all **667 source components, 1,033,521 source faces and 128 image-aperture alternatives**. All 318 input/current-code pins stayed unchanged. The first-hit arrays, working masks and known domains, full numerical relations and all-alternative overlays are saved per view. No source optical-role prior enters the component selection, and no component or image alternative is selected.

| Development design | Complete components | Apertures per view | Components with no pixel-center hit, front / angled |
|---|---:|---:|---:|
| RayBan | 99 | 16 | 67 / 68 |
| Miu | 171 | 16 | 118 / 120 |
| Oakley | 162 | 8 | 110 / 98 |
| Invu | 225 | 8 | 219 / 213 |
| Victoria Beckham | 10 | 16 | 1 / 0 |

Zero projected support is common among tiny source fragments and remains explicit. It cannot justify dropping those faces, assigning them a material or declaring them absent. Every image aperture has nonzero support on this working grid. All ten per-view overlay sheets and the combined contact sheet were inspected: broad optical footprints are present, while Miu and VB angled views retain geometry/articulation disagreement. SAM alternatives still include hardware or fragment the mirror/shield region. These are development observations, not independently labeled reconstruction scores.

Report: `data/component-aperture-corpus-v1/report.json`, SHA `c95a7908278305367cb6d8a5161a462f2437c0516d3e319247b6d44cfff992ef`.
Contact sheet: `data/component-aperture-corpus-v1/contact-sheet.png`, SHA `cff6cfc945a7367177de4e1cd20e6502423d26026110e87c1a6b017247d23644`.

Reproduce with the saved private inputs and a fresh output directory:

```powershell
python -m qa.component_aperture_corpus --output data/component-aperture-corpus-replay
```

The 21 projection/relationship tests and 15 adapter tests pass. The final full Python suite ran **655 tests: 654 passed, one optional SAM test skipped**, in 74.66 seconds. Compilation of reconstruction, tests and QA modules passed. These checks establish software behavior, not physical group identity or recovered material accuracy. No AR code changed or renderer qualification was rerun in this phase.

The combined receipt checks 384 files, all 318 current corpus input/code pins, aggregate/per-case/per-view agreement and complete component, aperture and pair inventories: `data/component-aperture-validation-v1/receipt.json`, SHA `4d5b5714885d9b167bd783a305a736f4ef5a280bb179149d658a5d5569176111`.

The follow-up numerical review retains exact component bindings, all 640 aperture relations for its illustrative pieces, and full pair quantiles in `data/component-aperture-corpus-v1/relation-findings.json`; its readable account is `RELATION_FINDINGS.md` in the same directory. All 63 pre-existing corpus artifacts remained unchanged. Representative counterexamples are:

| Measured relation | Consequence for a general rule |
|---|---|
| RayBan component 3 owns no full-scene pixels in either view, yet independently covers thousands of pixels and nearly coincides in projection with component 2 | Opaque first-hit ownership cannot decide which source surfaces belong to a lens. |
| Miu component 47 can be wholly inside an angled aperture while covering less than 4% of that aperture | One-way containment would admit small pieces without explaining the optical surface. |
| Miu component 0 covers about 43–44% of the broad full-image alternatives, versus 81–87% of the corresponding crop alternatives | Image connected components and physical lenses need a many-to-many association; proposal count is not lens count. |
| Oakley components 98 and 101 are each more than 99% contained in broad component 104; they do not overlap one another in front view | Requiring every pair of members in a group to overlap would reject a broad surface assembled from partial pieces. |
| Invu component 150 is only 19/6 working pixels in front/angled views and lies very close to broad component 224 | Small depth separation plus containment cannot by itself establish shared optical identity. |
| VB component 5 overlaps one broad component from the front and the other from the angled view, at substantial rear depth | Source identity is stable while projected association changes; a single view cannot assign a physical lens group. |

These are counterexamples to simple decision rules, not semantic labels inferred by the new module. Miu's near-coextensive pair also has large depth tails despite small central quantiles; “small median gap” must not be described as global coincidence.

## Consequence for the next inference step

The shared support portion described below is now implemented and exercised in [the support-union inference experiment](SUPPORT_UNION_INFERENCE.md). Its twenty fixed branches confirm the distinction between support and physical membership; explicit physical partitions remain unfinished.

The next implementation must separate **optical-support inclusion** from **physical group membership**. A component union can explain predicted aperture pixels without identifying how many physical lenses produced them. A connected image proposal can bridge both lenses; multiple image fragments can describe one shield. Requiring one image component per physical group would therefore introduce the same assumption in another form.

The smallest next inference step is a bounded search for shared source-component support unions across photos, producing competing, executable support hypotheses rather than another measurement-only report. For a fixed image interpretation, score the union on its known domain in each photo; use worst-view agreement and report the complete per-view residuals. Empty support cannot receive a free pass when positive evidence exists. False/reflected apertures remain unexplained evidence, not pixels silently removed to improve the objective. Full/crop and coarse/SAM interpretations remain competing branches, not independent confidence votes.

For an explicit image union, a pixel is known positive if any constituent is positive; it is known negative only if every constituent supplied a prediction there. An unknown crop exterior cannot become negative because another interpretation had a larger image domain. Such unions are derived hypotheses with their constituent IDs preserved; the evidence loader itself continues to return the original alternatives separately.

Search must not prefer fewer source components: fragmentation is a representation detail. Zero-projection pieces remain unknown. Duplicate or wholly contained components can leave the projected union unchanged, so a chosen support set does not prove those memberships. Keep tied/competing memberships and report search limits. Any bounded heuristic must state that its returned winner is conditional on explored candidates; a successful run is not a global-optimum certificate.

Physical partitions are a separate set of hypotheses over that support. Pair containment, absolute depth and order reversals can describe competing attachments, but cannot resolve them using one universal depth cutoff. Broad overlapping pieces can be alternate representations of a lens, separate clip-ons or optical layers. Small rear pieces may be pads, hardware or fragmented optical geometry. A mask-fit improvement alone cannot merge them into one effective interaction. Emit the existing source-bound partition/group declarations only for an explicit candidate partition, retaining its assumptions and unresolved alternatives.

Camera/articulation mismatch, floor reflections and segmentation errors remain competing explanations. Material fitting follows conditional ray composition, where a pixel may include a transmitting lens, a rear temple and an environment reflection. This is necessary for the user's tint/gradient/mirror objective: a scalar aperture score cannot identify absorption or decide whether a spatial color change belongs to the coating or the reflected scene. Unseen source regions, missing photographic evidence and physical ambiguities remain explicit. Independent product/reference data are still needed to evaluate whether the complete automation generalizes.
