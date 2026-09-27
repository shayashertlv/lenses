# GitHub research for the glasses reconstruction automation

Research date: **2026-09-25**. Scope: existing product photographs and optional dimensions to editable, efficient glasses assets for the current AR renderer. This is a source-reviewed shortlist, not an inference benchmark. No dependencies, model weights or datasets were installed; no paid calls or application changes were made.

The four supporting dossiers assess **51 distinct repositories in their candidate tables**, with deeper implementation/license checks on the most relevant options and additional dataset/research exclusions. The twelve priorities below are the recommended starting points.

## Recommendation

The strongest opportunities are **better part boundaries, photo-constrained geometry fitting, and independently measured quality** around the existing Tripo pipeline. GeoSAM2 is the most interesting new segmentation experiment. HQ-SAM/HQ-SAM2 and PyTorch3D address different parts of the fidelity problem. Mitsuba 3 is the strongest optical research tool, while glTF-Validator provides a smaller, practical delivery check.

Keep the current generation, immutable source, stage receipts, explicit budgets, canonical optical descriptors and actual-AR rendering as the comparison baseline. There is no verified drop-in repository in this research that turns arbitrary sparse eyewear photos into a product-accurate, transparent, mobile-ready GLB.

This conclusion is based on the current [automation README](../README.md), [segmented job contract](SEGMENTED_AR_JOB.md), [five-product results](SEGMENTED_AR_RESULTS_2026_09_23.md), [provider comparison](API_COMPARISON_RESULTS_2026_09_23.md), and direct inspection of the local segmentation, fitting, evaluation, simplification and authoring code. The five-product report identifies coarse frames/hardware, ambiguous optical boundaries, baked highlights and mirror/front-rear mismatch as remaining limits. It explicitly distinguishes AR compatibility from product fidelity.

## Ranked shortlist

Priority reflects expected relevance to our observed problems, implementation burden and availability. It is an engineering judgment, not a measured ranking. “Pilot” means compare a bounded adapter against frozen current outputs before considering adoption.

| Priority | Repository | What it could do for us | Decision and main limitation |
|---|---|---|---|
| 1 | [VAST-AI-Research/GeoSAM2](https://github.com/VAST-AI-Research/GeoSAM2) | Propagate a mask through rendered views and return mesh face labels, helping separate optical regions from frame/hardware without regenerating shape. | **Pilot.** Released code and weights; automatic seed generation and source-face remapping are our work. Thin/transparent geometry remains an acknowledged weakness. |
| 2 | [SysCV/sam-hq](https://github.com/SysCV/sam-hq) | Test more precise image boundaries around rims and lens openings using HQ-SAM/HQ-SAM2. | **Pilot.** Compare with our existing lens detector and SAM2 under identical prompts; improved generic masks do not prove correct lens identity. |
| 3 | [facebookresearch/pytorch3d](https://github.com/facebookresearch/pytorch3d) | Differentiable silhouette/landmark fitting for bridge width, rims and temple shape across photos. | **Prototype after camera/mask controls.** Optimization framework, not an eyewear reconstruction model or optical renderer. Native build compatibility needs checking separately. |
| 4 | [mitsuba-renderer/mitsuba3](https://github.com/mitsuba-renderer/mitsuba3) | Controlled optical reference scenes and inverse fitting of transmission/reflection hypotheses. | **Research pilot.** Requires camera/light assumptions and an explicit translation to our optical descriptor. Sparse studio photos can remain ambiguous. |
| 5 | [KhronosGroup/glTF-Validator](https://github.com/KhronosGroup/glTF-Validator) | Independent standards validation of GLB structure, accessors, textures and supported extensions. | **Near-term addition.** Complements our strict reader; it cannot certify the custom lens profile or visual accuracy. |
| 6 | [prs-eth/Marigold](https://github.com/prs-eth/Marigold) | Intrinsic-image hypotheses for separating opaque frame color from illumination and baked highlights. | **Frame-only pilot.** Use IID models specifically. Inferred albedo is fallible; generic monocular depth is not reliable lens geometry. |
| 7 | [cvg/LightGlue](https://github.com/cvg/LightGlue) with DISK and [PoseLib](https://github.com/PoseLib/PoseLib) | Match reliable frame/hardware details and test camera hypotheses. | **Small experiment.** Explicitly choose DISK; the example's SuperPoint extractor has different restrictions. Reflections, plain backgrounds and repeating rims can yield false matches. |
| 8 | [facebookresearch/vggt](https://github.com/facebookresearch/vggt) | Generate alternative camera/depth proposals from multiple photographs. | **Conditional pilot with VGGT-1B-Commercial.** The example still loads noncommercial VGGT-1B. Pin the commercial checkpoint explicitly; rigid-scene assumptions conflict with differently opened temples. |
| 9 | [cvat-ai/cvat](https://github.com/cvat-ai/cvat) | Reviewed masks and landmarks for a fixed evaluation corpus. | **Use when labeling the benchmark.** Evaluation annotations must stay outside production inference and reserved-photo inputs. |
| 10 | [voxel51/fiftyone](https://github.com/voxel51/fiftyone) | Review source photos, masks, part labels and candidate renders by product/failure type. | **Useful evaluation tooling.** Import our reports; do not duplicate the job journal or treat a dashboard as an evaluator. |
| 11 | [DLR-RM/BlenderProc](https://github.com/DLR-RM/BlenderProc) | Synthetic known-camera examples and deliberate defects for testing our measurements. | **Offline QA experiment.** Needs separate Blender environment; synthetic performance does not establish unseen-product accuracy. |
| 12 | [wgsxm/PartCrafter](https://github.com/wgsxm/PartCrafter) | Generate separate part meshes as an alternative initializer. | **Later comparison.** New geometry can invent details and lose source lineage; default background-removal dependencies require separate attention. |

## What the first integrations would look like

### GeoSAM2: source-bound face labels

Its released inference path takes twelve rendered color/depth/normal views plus one seed mask and produces labels on mesh faces. The [model card](https://huggingface.co/VAST-AI/GeoSAM2) identifies the checkpoint, rendering convention and remaining thin/transparent-surface problems. Both repository and checkpoint declare Apache-2.0; training code is not released. The documented environment is Linux, Python 3.10+, PyTorch 2.3+, CUDA preferred and Blender 4+; CPU is supported but slow. Treat WSL/Linux as the initial experiment environment, not a proven native-Windows installation.

Our proposed adapter would generate seeds automatically on those exact rendered views, ingest label arrays, establish an explicit face mapping, and construct partitions through our existing lineage-preserving code. Catalog-photo masks cannot be used as though aligned to a synthetic render. It would feed `part_role_inference` and preserve independent-view role checks. Upstream mesh loading/export is not a texture-preserving round trip: its diagnostic colored mesh must never replace the retained textured source. Preserve unknown/raw labels because upstream post-processing can remove small components. See the [inference implementation](https://github.com/VAST-AI-Research/GeoSAM2/blob/main/inference.py).

Freeze prompts, views and post-processing across products. Record any manual seed or per-product tuning as an intervention. Acceptance for the pilot means fewer omitted optical faces and less frame contamination with complete source mapping, not simply more part IDs.

### HQ-SAM/HQ-SAM2: improve boundaries while preserving alternatives

Integrate as an optional observation backend beside `reconstruction/region_engine.py` and `aperture_evidence.py`. The current SAM2 interface preserves three decoder alternatives, original-grid boolean masks, explicit prompts and uncalibrated predicted scores. An adapter must declare any output-count differences rather than fabricate three equivalent candidates.

Compare lens detector alone, existing SAM2 refinement and HQ refinement against independently reviewed contours on exactly the same photographs. Report per-family omissions and contamination, not only whole-image overlap. Missing one clear Miu lens and VB's ambiguous optical strip are useful existing regression cases. The [upstream repository](https://github.com/SysCV/sam-hq) supplies the implementation/checkpoint entry points; the [vision research dossier](research_vision_fitting_2026_09_25.md) records versions, licenses and platform constraints.

A useful preliminary adapter is **official Meta SAM2**: the current region engine imports Ultralytics' implementation. Meta's code/checkpoints are Apache-2.0; Ultralytics has separate AGPL-3.0 terms. Measure output parity before switching. This is a dependency simplification opportunity, not an assertion that private use violates a license. [Meta SAM2](https://github.com/facebookresearch/sam2), [Ultralytics license](https://github.com/ultralytics/ultralytics/blob/main/LICENSE).

### PyTorch3D: bounded shared-shape fitting

Use its differentiable rendering and geometry operations to prototype a small fitting backend behind the existing camera/structured-refinement code. Start with reliable contours and landmarks, neutral component renders, fixed topology, bounded geometry parameters and separate per-photo temple articulation. Do not expose arbitrary vertex motion as the initial optimizer.

The first question is whether shared rim/bridge parameters improve withheld observations without the camera absorbing the shape error. Preserve independent camera witnesses, contact constraints and the current best candidate. The library's [BSD-3-Clause code](https://github.com/facebookresearch/pytorch3d/blob/main/LICENSE) is available, but matching its compiled extensions to our Windows/Python/PyTorch versions is a setup experiment. Prefer a separate environment. Final optical appearance remains an actual-AR test.

### Mitsuba 3: test optical explanations

Build a tiny reference scene around retained lens geometry with known lighting/background. Fit only a few material parameters first; then test how camera distance and illumination alter the fitted explanation. This directly addresses the current mirror mismatch and clear-versus-bright-reflection ambiguity.

Mitsuba offers differentiable light transport, CPU and NVIDIA GPU paths, and documented Windows support. Its current [license](https://github.com/mitsuba-renderer/mitsuba3/blob/d22318ca55d64049acc00edafe14f0e4f5d6fe23/LICENSE) is BSD-style with an additional enhancement contribution clause; record that exact text instead of relying on a simplified license badge. There are no required pretrained weights for the proposed reference-scene use.

Any promising parameters must be converted explicitly to `LensAppearance`, compiled with a new receipt and compared through `TryOnRenderer`. A physically richer offline image is not evidence that our runtime can reproduce it. See [optics details](research_optics_assets_2026_09_25.md).

### Marigold IID: conservative frame relighting

Use the intrinsic decomposition checkpoints to propose where illumination contaminates opaque frame color. Cross-check multiple views and exclude lenses, logos, uncertain boundaries and unsupported regions. Keep source textures and every proposed alternative. The integration belongs near `intrinsic_frame_appearance.py` and `frame_image_support.py`; changing `frame_material` multipliers alone cannot remove baked highlights.

The [code repository](https://github.com/prs-eth/Marigold) is Apache-2.0, but the IID weights have their own CreativeML Open RAIL++-M terms. Keep output color spaces explicit: [IID Lighting](https://huggingface.co/prs-eth/marigold-iid-lighting-v1-1) uses linear outputs; [IID Appearance](https://huggingface.co/prs-eth/marigold-iid-appearance-v1-1) albedo is sRGB. This is a material hypothesis source, not measured reflectance or a transparent-lens solver.

## Repositories already helping us

| Existing tool | Evidence in our code | Useful extension |
|---|---|---|
| [zeux/meshoptimizer](https://github.com/zeux/meshoptimizer) | `qa/part_lod_probe.mjs` already calls `simplifyWithAttributes`, uses border locks, locks extrema and retains source attributes/images. | Strengthen semantic seam/thin-feature locks and compare exact final silhouettes/normals under the triangle budget. “Adopt meshoptimizer” would repeat existing work. |
| [mantasu/glasses-detector](https://github.com/mantasu/glasses-detector) | Pinned offline lens weights and aperture-evidence route; frame-head weights are also already retained locally. | Evaluate existing frame-head proposals first, without a new model download; retain failure and coverage reporting. |
| SAM2 through [Ultralytics](https://github.com/ultralytics/ultralytics) | Offline region engine with explicit weights, prompts and alternative masks. | Compare an [official Meta SAM2](https://github.com/facebookresearch/sam2) adapter under identical inputs, then HQ-SAM2. |
| [isl-org/Open3D](https://github.com/isl-org/Open3D) | Pinned segmented dependency and mesh-processing experiments. | Reuse existing geometry utilities; investigate a new library only for a specific missing operation. |

## Why several attractive projects are lower priority

The supporting dossiers screen generators, segmenters, camera models, inverse renderers, datasets and delivery tools individually. These exclusions matter because a polished example or permissive code badge can hide a mismatch.

- **PartField, PartPacker, nvdiffrast and nvdiffrec:** useful methods, but the inspected licenses have noncommercial restrictions. They are not cleared default dependencies for this commercial workflow. Pin actual license text; research labels do not automatically make company R&D permitted.
- **VGGT-Omega:** newer camera research is available, but the inspected noncommercial terms also restrict outputs/results. The explicitly commercial VGGT checkpoint is a different candidate.
- **EfficientLoFTR and IntrinsicAnything:** inspected current licenses require organizational/project registration. Do not carry forward older permissive-license descriptions without checking the selected revision.
- **PartCrafter and TripoSG launchers:** their own permissive license does not cover every checkpoint they load; BRIA RMBG-1.4 is a specific default dependency to replace with approved masks/preprocessing before a commercial experiment.
- **UltraShape:** model-card and repository license descriptions differ. Its handling of thin geometry is also a concern. Defer until the intended code/checkpoint combination is clear.
- **SAMesh:** no root license was found in the inspected upstream tree. Public source alone is not a reuse license.
- **TRELLIS.2, Hunyuan3D and other generators:** useful controlled baselines, but a new mesh still needs part identity, optical preparation, simplification and product-fidelity evaluation. Our local comparison already includes TRELLIS.2; availability of local code does not establish superiority over the retained Tripo output.
- **TSGS and TRAN-D:** relevant transparent-object research with restrictive licensing and different capture/output assumptions. TSGS can extract a mesh; rejecting it merely because it uses splats would be inaccurate. Neither provides our canonical eyewear material contract.
- **EyeglassesReconstruction and MEGANE:** relevant domain research, but this search did not establish a ready sparse-product-photo-to-GLB package. The inspected ECCV 2020 repository has TODOs instead of a runnable release.
- **Generic image-quality/semantic scores:** attractive outputs can still have a wrong bridge, missing temple, filled lens or copied reflection. Keep component geometry and optical evidence separate.

Exact license sources and checkpoint caveats are linked in the specialist dossiers below. These are adoption filters for the inspected versions; the selected revision and every required asset must be pinned at implementation time.

## Suggested experiment order

| Step | Scope | Evidence needed to continue |
|---|---|---|
| 1. Freeze evaluation | Existing five products for development; separately reserved unseen designs/photos. Review outlines and visibility, retaining uncertainty. | Evaluation data cannot enter provider, material or geometry inference. Defect-injected controls demonstrate that scores detect important errors. |
| 2. Boundary/part pilot | HQ masks and GeoSAM2 as separate experiments on identical retained source geometry. No paid regeneration needed. | Fewer omissions/contamination and better contour tails; exact face lineage; no hidden per-product intervention. |
| 3. Camera/shape pilot | First LightGlue or commercial VGGT seeds, then a bounded PyTorch3D fit. | Independent camera witnesses and withheld-view geometry improve together; temples retain separate articulation. |
| 4. Appearance pilot | Frame-only Marigold hypotheses; controlled Mitsuba lens scenes. | Improvement survives multiple views, lighting setups and actual-AR rendering; ambiguous fits remain marked uncertain. |
| 5. Delivery hardening | glTF-Validator plus stronger existing meshoptimizer constraints. | Fresh optical receipts validate after geometry changes; export/reduction preserve appearance and thin features at phone display scale. |

The shortest useful engineering work is the validator adapter. The highest-value research work is the boundary/part comparison. Shared geometry fitting and inverse optics are larger experiments. GPU fit, runtime and quality gains were not measured in this research, so the report does not assign fabricated speedups or success percentages.

## Supporting dossiers

- [Reconstruction, generators and 3D segmentation](research_reconstruction_2026_09_25.md)
- [Image segmentation, camera fitting and differentiable geometry](research_vision_fitting_2026_09_25.md)
- [Optics, frame materials, mesh reduction and glTF delivery](research_optics_assets_2026_09_25.md)
- [Evaluation, annotations, datasets and inspection](research_evaluation_2026_09_25.md)

The dossiers record code/weight availability, licenses, compute/platform requirements, maintenance observations, exact integration points and alternatives to defer. Repository activity was checked where available; GitHub API rate limiting prevented a uniform fresh metadata snapshot for every candidate. No ranking is based on star counts. Browser-accessible primary repositories, model cards and license files are the supporting sources.
