# External research: what should change in the reconstruction approach

Reviewed 23 September 2026 against the current Oakley result. Scope remains existing product photographs, appearance for AR, and no required new capture protocol. This is research and an experiment proposal; no external model has been executed or adopted in this pass.

## Most relevant findings

### Eyewear specialists encounter the same difficult cases

Fittingbox's photo-to-3D input guide requires front and 90-degree side images and explicitly lists sports frames, folded glasses, thin/rimless frames and strong reflections among unsupported cases. This overlaps the difficult conditions in our development cases. It does not establish that their other production services cannot handle them. [Official photo guidelines](https://fittingbox.com/en/resources/help-center/guidelines-3d-from-photo).

Its published StudioLab workflow uses parametric/NURBS construction, meshing at a chosen resolution, and artist texturing. That is evidence for clean, category-specific construction, but not evidence of a fully automatic catalog-photo algorithm we can copy. [Geometry workflow](https://fittingbox.com/en/resources/blog/expert-talks-different-modelisation-processes-for-a-perfect-geometry).

Their renderer team also describes transmission-aware lens materials and per-pixel depth peeling for overlapping layers. This supports retaining the purpose of our optical renderer while investigating its inputs and output; it does not validate our implementation or fitted parameters. [Rendering discussion](https://fittingbox.com/en/resources/blog/expert-talks-realistic-rendering-in-virtual-try-on).

GlassOn's detailed guide requires aligned front/left/right views, white backgrounds and controlled lighting. Its headline photo count is therefore not a demonstration on arbitrary existing images. [Input guide](https://glasson.io/guideline.html).

### Eyeglass-specific reconstruction supports structural priors

The 2024 thin-frame reconstruction paper fits a predefined template using landmarks, camera estimation and free-form deformation. It explicitly restricts its topology, recommends near-frontal views, and reports quantitative synthetic evaluation plus controlled real qualitative examples. It does not recover mirrored lens materials or demonstrate a single-shield sports constructor. No usable linked implementation was found in this review. [Full paper](https://arxiv.org/html/2408.05402v1).

The ECCV 2020 supplement identifies a concrete contour failure: nearest-edge matching can confuse inner and outer rims; direction-compatible matching reduces the error. The official repository is currently a README/teaser with installation, dataset and usage TODOs. It is not a runnable dependency. [Supplement](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123700375-supp.pdf), [repository](https://github.com/wang-yating/EyeglassesReconstruction).

A 2025 SPIE paper on self-supervised multiview eyeglass frames was located, but readable primary method/results and code were not verified. No recommendation relies on its title. [Publisher](https://www.spiedigitallibrary.org/conference-proceedings-of-spie/13539/1353911/Self-supervised-multiview-reconstruction-of-eyeglasses-frames-with-thin-structures/10.1117/12.3057662.short).

### Learned material maps are useful hypotheses

GAINS reports sparse-view inverse rendering with 4–32 cameras, combining geometric priors with segmentation, intrinsic-image decomposition and diffusion material priors. Its public implementation is worth studying for frame appearance. This is not demonstrated catalog-sunglass transmission recovery or a direct mesh/GLB replacement. The repository contains both an MIT license and inherited Gaussian-Splatting research/noncommercial terms; dependencies require review before reuse. [Project](https://patrickbail.github.io/gains/), [code](https://github.com/patrickbail/gains), [inherited license](https://github.com/patrickbail/gains/blob/master/LICENSE.md).

RGB2X provides runnable image-to-material inference and weights, originally targeting interior scenes. Applying it to small glossy frames is a domain-transfer experiment. A predicted albedo or normal map remains a prior, not measured truth. [Official code](https://github.com/zheng95z/rgbx).

MatSpray provides a particularly relevant working design: predict per-view material maps, project/fuse them on the reconstructed object, then refine consistency. Its current code uses a Gaussian representation and noncommercial research terms; its documented predictor is SVD DiffusionRenderer, with an explicit warning about incompatibility with the Cosmos variant. Borrowing the design for a frame-only experiment is distinct from installing it as our production pipeline. [Official code and requirements](https://github.com/cgtuebingen/MatSpray).

MatMart separates material prediction for observed photographs from generation in missing regions, conditioning completion on already baked material. This is useful for preserving product marks. It assumes known geometry; runnable released code/weights were not verified during this review. [Primary paper](https://openaccess.thecvf.com/content/CVPR2026/papers/Wu_MatMart_Material_Reconstruction_of_3D_Objects_via_Diffusion_CVPR_2026_paper.pdf), [authors' publication index](https://github.com/alibaba/Taobao3D).

More specialized transparent-object reconstruction does not automatically match our input contract. Through the Looking Glass uses sparse photographs but requires a known environment map. This is not available in our catalog images. [Authors' project](https://cseweb.ucsd.edu/~viscomp/projects/CVPR20Transparent/).

### A stronger general generator is a comparison candidate

Microsoft TRELLIS.2 exposes geometry plus base color, roughness, metallic and opacity generation; it supports open/non-manifold surfaces and exports GLB. Its documented example is single-image conditioned, and exported transparency is initially disabled. Code/model are MIT, with separately licensed dependencies; the official setup requires Linux and at least 24 GB NVIDIA GPU memory. Opacity output is not recovered lens transmission/coating physics. Treat it as a competing initializer, with all other photos used to check product identity. [Official repository](https://github.com/microsoft/TRELLIS.2), [representation paper](https://arxiv.org/html/2512.14692v1).

## The architectural gap in our code

This conclusion is our inference from the sources and code, not a published result on our products.

- `structured_refinement.fit_shared_geometry()` deforms the existing mesh with a 3×3×3 cage; it retains connectivity.
- `smooth_optical_geometry` fits smooth effective normals to the existing geometric surface; it does not replace the silhouette, depth or group membership.
- `optical_surface` retains source contours, holes and discontinuities in its envelope construction.
- `geometry_completion.propose_missing_geometry()` fills only bounded observed gaps.

These are useful repair mechanisms. They cannot construct a clean manufactured shield when the initializer's underlying structure is wrong. A category-specific constructor is a materially different proposal from adding more smoothing or another material search.

## Next experiment, before a broad rewrite

First use fixed-camera diagnostic renders of the current Oakley: component IDs, unlit flat color, geometric versus shading normals, neutral optics, and controlled lighting. This distinguishes geometry/group/normal problems from deliberate reflected light shapes. Rectangular highlights alone do not prove broken geometry.

Then compare a deliberately narrow new shield candidate against the current candidate. Proposed interfaces below do not exist yet:

1. `propose_construction_hypotheses(photos, semantic_report)` proposes shield versus dual-lens structures, preserving ambiguity. It cannot convert an uncertain AI label into a fixed fact.
2. `extract_typed_contours(photos, aperture_evidence)` records ordered lens perimeters, frame boundaries and hinge/temple landmarks, with uncertainty and direction-compatible correspondence. Test these on the photographs before fitting 3D geometry.
3. `fit_constructed_shield(contours, camera_seeds, policy)` fits a continuous low-dimensional curved surface and photographed outline directly. Generate positions and normals from the same surface. Do not fit only the generated mesh. Keep unsupported curvature alternatives when the photographs cannot distinguish them.
4. `assemble_constructed_candidate(shield, support_frame, temples)` exports separate physical parts, controlled tessellation and fresh optical provenance. Reuse current articulation, optical export and AR-render checks where their contracts apply; old source-face IDs cannot be copied onto new topology.
5. `compare_construction_candidates(candidates, photos, renderer)` compares contour alignment, occlusion, controlled reflection continuity and polygon count. Existing development photos provide a development comparison, not newly independent validation. Require an obvious improvement before expanding construction to more frame families.

For frame appearance, evaluate an intrinsic-material predictor only on verified frame regions of fixed geometry. Compare predicted maps across visible surface correspondences, preserve observed logos/patterns, and render bounded alternatives. Lens pixels remain under the separate optical model. A prettier generated texture is not sufficient evidence of correct product appearance.

For optics, keep the constructed geometry fixed and test a bounded shared-lighting hypothesis against bounded per-photo lighting. Then deliberately retain materially different lens descriptions that explain the input photos similarly and render them under identical neutral AR lighting. Disagreement diagnoses remaining material ambiguity. Learned-prior coverage must be reported separately from observed multi-view measurement coverage.

TRELLIS.2 can be tested as a separate initializer benchmark if useful. Keep its output independent from the new shield construction experiment so improvements have an identifiable cause.

The immediate decision is whether clean structural construction fixes the Oakley's actual failure. Neither the papers nor vendor claims establish that outcome yet. The existing renderer, intake, articulation, provenance and evaluation infrastructure can be retained while this experiment answers it.

## Update: external APIs and licensed assets

Subsequent user direction excludes Fittingbox. The deeper audit and selected two-key, six-generation test are in [API comparison contract](API_COMPARISON_CONTRACT_2026_09_23.md). That document supersedes the initial provider routes below: current Rodin Gen-2.5 is available through fal, while direct Tripo is needed for its newest texture controls.

The user paused implementation to reconsider alternatives. The construction and material experiments remain paused. Research below supersedes the immediate experiment recommendation above: compare alternative initializers before investing further in custom repairs. No new provider jobs, uploads, purchases or account registrations were made during this review.

### Rodin Gen-2.5

The official API accepts 1–5 images with direction labels, returns GLB with PBR, and exposes `geometry_instruct_mode=faithful` plus `texture_delight=true`. These directly address fidelity and baked lighting. Explicitly choose Gen-2.5; do not rely on the default generation. First-image material conditioning makes primary-view selection consequential. Input alpha preservation is not recovered lens transmission. Flow: `POST /api/v2/rodin`, poll `/api/v2/status`, then `/api/v2/download` at `api.hyper3d.com`. Direct API access is associated with its Business plan; credit pricing and optional packages need checking at execution time. [API specification](https://docs.hyper3d.ai/en/api-specification/rodin-gen2-5), [quick start](https://docs.hyper3d.ai/en/get-started/quick-start), [plans](https://hyper3d.ai/pricing).

### Tripo

The H-series API accepts 2–4 images in named front/left/back/right slots and returns a downloadable GLB. Front is mandatory; inputs should have compatible object poses and lighting. Pin geometry `v3.1-20260211` and texture `v3.5-20260815`; the latter supports `delight=true` to remove baked lighting. Face-count controls are available. Standard documented PBR channels do not establish recovered optical properties. Flow: `POST https://openapi.tripo3d.ai/v3/generation/multiview-to-model`, poll `GET /v3/tasks/{task_id}`, download `output.model_url`. Basic textured multiview is currently listed at 30 credits, $0.30; extra quality/processing costs more. [Generation](https://developers.tripo3d.ai/en/docs/generation-multiview-to-model/standard), [task query](https://developers.tripo3d.ai/en/docs/task-query), [pricing](https://developers.tripo3d.ai/en/pricing).

### TRELLIS.2 hosted on fal

`fal-ai/trellis-2` accepts `image_url` and returns `model_glb.url`; fal's SDK/HTTP queue removes the need for a local CUDA machine. Listed prices are $0.25/$0.30/$0.35 at 512/1024/1536 resolution. The schema also mentions a multi-image input, but its callable endpoint was not verified: do not claim the single-image endpoint accepts multiple photos. Opacity and generic PBR generation remain distinct from physically recovered lenses. [API](https://fal.ai/models/fal-ai/trellis-2/api), [pricing guide](https://fal.ai/learn/devs/trellis-2-image-to-3d-prompt-guide).

### Exact-product asset licensing

Fittingbox advertises 195,000+ eyewear assets and GLB/glTF conversion for other rendering solutions. An exact brand/model/color match could bypass reconstruction while retaining our AR engine. Our SKU coverage, export licensing, material compatibility and delivery automation remain unverified. Its public availability API is not proof of unrestricted asset-download access. Its photo-digitization guidelines exclude sports styles, thin/rimless frames and strong reflections, so that service cannot be presumed to solve our difficult inputs. [Assets/export](https://fittingbox.com/en/digital-frames/solutions/3d-assets), [availability API](https://fittingbox.com/en/resources/help-center/api-reference), [photo limitations](https://fittingbox.com/en/resources/help-center/guidelines-3d-from-photo).

### Bounded implementation path if selected

Start with the current Oakley and ordinary Miu frame: one generation per product per alternative provider, against the cached Meshy baseline. A third thin-metal product would broaden coverage before claiming general reliability. Use compatible source photographs, pin settings/seeds and save exact provider input ledgers. Render raw downloaded models before downstream repairs; otherwise our processing can hide or introduce provider differences. Compare all photographed views, frame/lens separation, smooth lens geometry/normals, both temples, marks/colors, triangles and file size. Development photos are not independent holdouts.

The first benchmark needs no initializer rewrite: `reconstruction.initializer.resolve_initial_model()` already accepts `kind: existing_glb`. Import each result there, retain provider receipts separately, and do not misrepresent imported origins as verified Meshy history. Only integrate a winning provider after visible improvement. Proposed adapter functions would be `prepare_request`, `submit`, `poll`, and `download`, with saved task IDs preventing duplicate paid submissions; these new adapters are not implemented.

Recommendation: test Tripo and Rodin first, with hosted TRELLIS.2 as a cheap third baseline. Independently investigate licensed exact-product coverage. None of the reviewed documentation demonstrates perfect appearance for our glasses; texture de-lighting alone does not solve mirrored or gradient lens optics.
