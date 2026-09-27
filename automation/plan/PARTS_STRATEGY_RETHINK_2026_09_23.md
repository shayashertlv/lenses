# Rethinking lens and frame identification — 23 September 2026

This is a source/code review and proposed experiment contract. The new approaches below have **not** been run on our glasses. No paid requests or production changes were made for this review. It refines the next-experiment recommendation in the [generation comparison](API_COMPARISON_RESULTS_2026_09_23.md).

## Decision

Preserve a useful generated mesh and infer **semantic labels for its faces**. Feed those labels to our existing material-partition exporter. Test the inexpensive documented Tripo segmentation service before building a large custom segmentation stack. Keep render-based face labeling as the controlled alternative, and parts-first generation as a separate comparison if existing-geometry labeling fails.

Physical disassembly and reconnection are unnecessary for changing materials. A connected mesh can contain different material regions. Rebuilding a lens is a separate operation, justified only when correctly labeled geometry still looks wrong under optical materials.

A per-pixel transmission texture on one shared material is another possible representation. It is not the first choice here: AR's `isOpticalMaterial()` and several visibility/shadow consumers classify whole materials. Our existing face-to-material partition fits that runtime contract without changing those consumers; it also supports separate temple identities.

The first experiment must distinguish three causes: wrong face labels, wrong lens geometry, and wrong optical appearance. Improving one does not prove the other two are solved.

## What our existing evidence actually establishes

- Both saved Tripo requests explicitly set `generate_parts=false`. This was appropriate for the textured-generation comparison, but means that comparison did not test native parts generation.
- Exact-position connectivity makes each Tripo result a single component. Component grouping therefore cannot discover its lens/frame boundaries.
- `reconstruction/face_role_repair.py::propose_face_roles()` repairs bounded patches around existing optical groups. It does not discover complete lens groups from scratch.
- `reconstruction/automatic_articulation.py::infer_automatic_part_bindings()` also relies on separate component cores. Raw Tripo temple binding needs face-level roles too.
- `reconstruction/mesh_components.py` defaults to one million faces; both Tripo models contain approximately 1.94 million. Raising that limit would not solve semantic grouping. A new path must budget memory explicitly and avoid this component-only prerequisite.
- `reconstruction/partition_glb.py::partition_glb_bytes()` already preserves source triangle occurrences and vertex attributes while emitting material primitives. Its limit is four million source faces. `verify_partition_glb_bytes()` verifies that preservation. This is the useful downstream foundation.

## API and released-code findings

| Candidate | Verified capability | Practical consequence |
| --- | --- | --- |
| [Tripo mesh segmentation](https://developers.tripo3d.ai/en/docs/mesh-segment) | `/v3/mesh/segment`, `v2.0-20260430` beta accepts an existing asset/task and optionally a colored reference mask. | First service to test on our saved assets. Docs do not promise original triangles, UVs, transforms or texture preservation. Reference-image camera and color-to-part mapping are unspecified. |
| [Tripo native parts](https://developers.tripo3d.ai/en/docs/generation-multiview-to-model/standard) | `generate_parts=true` with `texture=false`, `pbr=false`; omit `quad` and `smart_low_poly`. | Generate geometry with parts, inspect it, then [texture selected parts](https://developers.tripo3d.ai/en/docs/models-texture). This changes generation conditions and may change the shape. Part boundaries are not guaranteed to match optical boundaries. |
| [GeoSAM2](https://github.com/VAST-AI-Research/GeoSAM2) | Propagates a mask/click across known mesh renders and produces face labels. Released inference code and weights; Linux setup, CPU supported but slow according to README. | Closely matches the architecture we need. Requires integration and original-face correspondence checks; is not a proven eyewear solution or verified hosted API. |
| [SAMesh](https://github.com/gtangg12/samesh) | Mesh rendering, SAM2 masks and lifting to 3D parts. | Another released implementation reference. Class-agnostic partitions still need optical semantics. Documented setup uses CUDA. |
| [SAM 3 on fal](https://fal.ai/models/fal-ai/sam-3/image-rle/api) | Image masks from text, points and boxes; mask/RLE output. | Can segment our controlled renders through the existing FAL account. Our code must perform multiview fusion and 3D correspondence. Validate mask decoding against overlays. |
| [Hunyuan3D-Part / P3-SAM](https://github.com/Tencent-Hunyuan/Hunyuan3D-Part) | Segmentation can return face labels separately from XPart completion. | Research alternative. Public demo cleans the input; source exposes a no-clean option for a custom GPU wrapper. Geometry-only evidence may miss boundaries visible primarily in textures. |
| [Hunyuan part service on fal](https://fal.ai/models/fal-ai/hunyuan-3d/v3.1/part/api) | FBX input, at most 30,000 faces, FBX parts output. | Not a direct replacement for our nearly two-million-face GLBs. Simplifying to its input limit and transferring labels back adds another uncertainty. |
| [Rodin BANG](https://docs.hyper3d.ai/en/api-specification/bang) | Instructed decomposition API, including optional material generation. | Generative decomposition, not a documented lossless partition. The [authors' paper](https://arxiv.org/html/2507.21493v1) reports geometry drift and detail loss. Lower priority for retaining thin eyewear detail. |
| [Meshy Auto Split](https://docs.meshy.ai/en/api/auto-split) | Rebuilds printable parts, reinforces thin cut regions, drops input textures; requires supported Meshy task lineage. | Poor match for preserving AR appearance. Existing texture transfer cannot undo geometry that was changed by splitting. |
| [OmniPart](https://github.com/HKU-MMLab/OmniPart) | Generates parts jointly from an image plus a 2D part-ID mask. Released code and author demo. | Most relevant alternative generator: define lens/frame regions before generation. Still generates a new asset, with no demonstrated fidelity on our eyewear. Research hosting is not a production service guarantee. |

Tripo also lists Smart Segmentation pricing, but its linked public documentation did not provide a request contract. Do not invent one from a route name or substitute it silently for the documented segmentation endpoint.

GeoSAM2 is an architecture reference, not a drop-in preservation adapter. Its [mesh loader/fusion helpers](https://github.com/VAST-AI-Research/GeoSAM2/blob/main/utils/inference_utils.py) load a flattened mesh using `processed=False` rather than Trimesh's `process=False`, expand face corners, and export colored geometry without an original GLB occurrence ledger. Face correspondence must be established explicitly. Its defaults can fill unobserved faces and remove small regions: disable postprocessing and opposite-view auto-segmentation for an initial audit. The [voting extension](https://github.com/VAST-AI-Research/GeoSAM2/blob/main/utils/mode_ext.py) also requires C++/OpenMP compilation. The inspected path did not reveal a mandatory CUDA call, but Windows CPU execution was not tested. At Tripo density, five samples per face across twelve views already imply roughly 1.4 GB for the integer view-link array alone; chunked processing and explicit memory measurements are necessary. Do not launch an unrestricted full-resolution run based on the README's CPU statement.

For P3-SAM, the [public demo](https://huggingface.co/spaces/tencent/Hunyuan3D-Part/blob/main/app.py) returns labels for its cleaned mesh. A custom wrapper can use `AutoMask.predict_aabb(..., clean_mesh_flag=False, post_process=False)` and verify vertex/face arrays before consuming labels. Its model uses geometry/normals rather than original texture color. Its [license](https://github.com/Tencent-Hunyuan/Hunyuan3D-Part/blob/main/LICENSE) also restricts territories, including use/display of outputs; this is not an unrestricted production dependency.

## The architectural alternative

Render the **generated model itself** from controlled virtual cameras. Ask a segmentation model to identify lenses and frame in those renders. Record the source triangle ID at each rendered pixel, so labels can be placed back onto original triangles directly.

This removes product-photo camera estimation from the initial face-labeling problem. It does not remove photos from validation: the generated object can still be wrong. Photos remain independent evidence for outline, construction, temple shape and optical appearance.

Suggested labels are optical-left, optical-right or optical-shield, front-frame, left-temple, right-temple, pads/other-frame, and unknown. Labels encode physical role; changing reflections or a bright rectangle must not create new physical parts.

Use multiple front, rear and oblique views. Texture RGB proposes semantic regions; depth, normal continuity and geometry constrain them. A mask on a front photo is not enough: temples can appear through clear lenses, and an absent generated lens would make a temple the first surface inside its aperture. Unseen/contradictory faces remain unknown until another observation or justified geometric constraint resolves them.

An AI model can propose semantic seeds and decide whether a visible bright patch plausibly represents a reflection. It should not directly dictate triangle membership or claim measured RGB transmission from an unknown studio environment. Persist masks, alternatives and evidence so the decision can be checked.

## Function-level implementation boundary

These are **proposed new functions**, not claims of implemented behavior:

| Function | Contract and reuse |
| --- | --- |
| `prepare_part_label_probe(source, method, settings)` | Freeze original bytes/hash, original face occurrence IDs, input photos and task lineage. Separate paid-call reservations from retries using the existing provider harness conventions. |
| `submit_tripo_segmentation(task_id, ref_image=None)` | Pin v2; first probe uses detailed granularity and `split_by_connectivity=false`. A mask-guided probe omits those ignored controls. No completion, retexture or decimation in this probe. |
| `audit_segmentation_correspondence(source, segmented)` | Compare world-space surface, component transforms, triangle occurrence correspondence, attributes and texture hashes. Establish whether labels can map exactly back to source. If only approximate transfer is possible, require distance/normal/visibility agreement and preserve unknowns near thin layers; nearest-point matching alone is insufficient. |
| `render_material_evidence(source, cameras)` | Matching RGB, depth, normals and integer original-face IDs; identical camera transforms and pixel grids. Include primitive/node occurrence identity. Disable blending, tone mapping and interpolation for ID buffers. Use `Raster.face_index` as the reference behavior; benchmark the high-face-count implementation separately. |
| `propose_render_regions(renders, prompts)` | Existing `OfflineSAM2RegionEngine.predict()` or hosted SAM3; preserve alternate masks, negatives and provenance. SAM accepts RGB, not arbitrary appended depth channels. Automatic semantic seeding is a separate measured stage. |
| `lift_region_votes(face_ids, masks, depth, normals)` | Aggregate visible surface support by face and view. Track contradictions and coverage; grazing/subpixel observations get less weight. Do not infer hidden ownership from background masks. |
| `solve_material_face_labels(votes, adjacency, geometry)` | Constrained face labels with unknown output. Respect thin geometry, sheet pairing and frame contacts; avoid global nearest-label fill or aggressive small-component merging. Resolve labels at original geometry resolution, including boundaries. |
| `compile_material_regions(source, labels)` | Convert labels to complete declarations and call `partition_glb_bytes()` plus `verify_partition_glb_bytes()`. Use `partition_optical_groups.declarations_for_partition()` for fresh group bindings and `prepare_optical_groups.run_optical_group_preparation()` for the optical consumer. Unknown faces remain explicitly unaccepted. |
| `audit_material_assignment(source, candidate, reviewed_masks)` | Evaluate visible lens coverage, false optical frame area, unknown area, preserved geometry/textures and actual AR rendering. Audit the opposite side and interior, not only the front silhouette. |

Face labels can also feed `view_scene.bind_parts()` for articulated photo observations after hinge locations are established. Old component ordinals and camera/mask bindings must be translated or regenerated after partitioning; do not silently reuse them.

## Smallest decisive experiment

1. **Freeze the existing Oakley and rimless Miu meshes.** Establish independently reviewed lens/frame annotations on several controlled renders, including rear/oblique and held-out camera views. This is benchmark annotation, not proposed manual labor for every production product. Keep the automatic-prompt and manually guided conditions separate.
2. **Run one automatic Tripo segmentation per model.** Inspect the full assembled result and correspondence. The current [price](https://developers.tripo3d.ai/en/pricing) is 40 credits ($0.40) each: $0.80 for two. If labels are good, reuse them and stop building competing segmenters.
3. **If automatic boundaries fail, test controlled guidance.** At most one color-mask-guided Tripo request per model adds $0.80. Its unspecified camera contract makes this an experiment. In parallel, a render-mask-to-original-face prototype gives a deterministic correspondence baseline. Initially reviewed masks can establish whether better segmentation would even solve the visible problem; automate the masks only after that diagnostic works.
4. **Apply diagnostic optics while holding geometry fixed.** Keep all frame textures and positions. Use a neutral clear Miu lens and a controlled tinted Oakley shield before optimizing photographed coating. Check nose pads and rear temples remain visible and opaque. Use the existing effective optical group handling so front/back lens sheets do not simply double the tint.
5. **Branch only on the observed failure.** Good geometry + bad labels calls for better labeling. Good labels + warped reflections calls for a smooth-lens geometry experiment. Missing bridge/temple geometry calls for better generation. Good shape + wrong tint/mirror behavior calls for the appearance stage. A native-parts Tripo comparison is the first alternative generation test; OmniPart is a later explicitly masked comparison.

For native parts, detailed multiview geometry is currently 20 base + 20 detailed + 20 parts = 60 credits per product, with HD texturing a separate 20-credit operation. Inspect geometry/parts before paying to texture it. Existing Tripo/FAL credentials cover the first probes; no new vendor subscription is necessary.

Set pass criteria before inference: suggested engineering thresholds are at least 98% reviewed visible lens-interior coverage and no more than 0.5% reviewed visible frame area incorrectly optical. Also require zero visible frame holes or lost thin structures, report boundary/unknown errors separately, and verify unchanged source triangles/attributes for the preservation route. These are proposed thresholds, not measured results, and aggregate scores cannot override an obvious bridge or temple defect.

Do not infer catalog readiness from two difficult products. Add at least a thick full-rim example and a half-rim example from existing photos after the narrow test succeeds. Final delivery still needs boundary-aware simplification to the current 150,000-triangle/15,000,000-byte policy and actual AR checks. Do not aggressively simplify first merely to fit a segmentation service's limits.

## What remains uncertain

No checked source proves reliable automatic optical labeling for our rimless Miu or mirrored Oakley. Exact lens curvature, transmission and coating are not uniquely determined by these studio images. A vision model can narrow hypotheses, but segmentation and part generation do not resolve that ambiguity. The controlled experiment above can establish which stage needs work without another broad rewrite or claiming that an API success response means the product is correct.
