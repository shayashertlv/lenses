# Architecture review: generalization and lens appearance

2026-09-21. This extends the initial audit in response to the owner's priorities: stop relying on successive product-specific repairs; cover different glasses constructions and photo inputs; make tint, gradients and strongly mirrored lenses accurate in AR.

This is a design and verification contract. Experimental modules in `reconstruction/` establish a few building blocks; they do not yet implement or prove the complete system.

## The central change

The unit of work must be an **eyewear description inferred from evidence**, with explicit components, constraints, appearance and uncertainty. Meshes, Blender node graphs, GLBs and rendered pictures are compiled representations of that description. They must not each become a different source of truth.

The previous versions had several incompatible assumptions: a generator's fused mesh became fixed geometry; a material had different meanings in preview and export; optical identity depended on a transparency number; an empty detector result became a perfect score. These failures are predictable from the contracts, not unique to an individual product.

Before expanding production generation, verify representation coverage, stage invariants, and cross-stage fidelity. Experiments are still required to establish performance, but each experiment must test a stated hypothesis across a family of cases. A new product must not require an ID-specific branch, corrective prompt, or hand-authored mesh script.

## One scene description, several kinds of evidence

The proposed versioned scene description contains:

- **Component graph:** frame members, bridge(s), hinges, temples, pads, optical surfaces and attachments; support/contact/free-edge relationships; material regions and object-local coordinates. Lens identity is semantic, independent of transparency or metalness.
- **Geometry:** curves and cross-sections for slender members, surface patches for optical elements, and controlled freeform residual meshes for shapes the simple representation cannot express. Do not force every product into a single topology template or a fixed vertex correspondence.
- **Articulation:** permanent shape separated from per-photo hinge/temple configuration. Closed and open temples cannot be explained by deforming a single rigid shape differently in each camera.
- **Appearance:** frame texture/roughness and a canonical optical description. Intrinsic spatial variation is separate from view-dependent reflection.
- **Evidence:** image coordinates, normalization transforms, semantic observations, camera hypotheses, confidence and provenance. Unknown geometry or material parameters stay unknown or explicitly inferred.
- **Scale:** measured dimensions where available; otherwise a clearly estimated AR scale. Measurement definitions are part of the contract.

The first generation may come from Meshy or a structured shape prior. Neither is allowed to override contradictory image evidence simply because its mesh is valid. A candidate that cannot be represented faithfully must retain a freeform region or request missing evidence, not be silently collapsed into the closest template.

Observations require at least four states: **present, verified absent, occluded, unknown**. An empty mask alone cannot distinguish an absent lower rim from an invisible rim. Verified absence is evidence against hallucinated components; unknown is not. `reconstruction/evidence.py` now implements these states plus inconsistent evidence, image hashes, coverage and provenance. Its existence does not establish automatic semantic extraction.

Geometry and appearance use one shared image-formation model. They can be optimized in stages to control complexity, but those stages must remain reversible: clear optics and reflective thin frames cannot be segmented independently of material/lighting hypotheses, and specular behavior can constrain surface normals. A material residual that reveals incorrect geometry must be allowed to reopen geometry fitting under the same validation contract.

## Coverage dimensions we must design for

| Axis | Cases that must be represented or explicitly diagnosed | Architectural implication |
| --- | --- | --- |
| Construction | Thick full rim, thin wire, semi-rimless, drilled rimless, single shield, brow bar, double bridge | Component/contact graph; free edges and holes are legitimate; no universal closed-rim requirement. |
| Shape | Round, angular, cat-eye, deep wrap, flat, asymmetric or decorative members | Flexible contour/surface representation; symmetry is a prior, not an unconditional repair. |
| Optics | Clear, solid tint, vertical/multistop gradient, neutral mirror, colored mirror, angular coating, gradient plus mirror | Independent transmission and reflection fields; spatial and angular coordinates kept separate. |
| Frame appearance | Opaque plastic, translucent acetate, metal, rubber, textured/patterned regions, logos | Semantic materials and shared color-space handling; silhouette alone cannot establish completeness. |
| Photo state | Open/folded temples, changed hinge angles, detachable clip-ons/inserts, mixed products/colorways | State consistency and articulation analysis before shared-shape fitting. |
| Imaging | Independent crops, perspective, wide-angle distortion, near-profile occlusion, studio versus lifestyle, transparent background | Per-image camera/visibility model; explicit background assumptions; no arbitrary image stretch. |
| Evidence quality | Tiny product pixels, clipping, blur, white-on-white clear optics, dark-on-dark, strong reflection, shadow | Confidence tied to observable evidence, not image dimensions or segmentation stability alone. |
| AR use | Face backgrounds/skin tones, head turns, bright/dim environment, model scale, mobile simplification | Appearance and geometry validated after export in the actual renderer; gradient stays on the lens. |

Use combinatorial coverage: every axis value, pairwise combinations, and high-risk combinations such as thin rimless + clear lenses + white background, wraparound + colored mirror + oblique views, and gradient + high mirror + dark skin/background. Keep all examples of a physical design in the same development/test split. A few generated test shapes cannot prove this coverage on real photographs.

## Lens color: the rendering contract is currently inconsistent

These are source findings, not speculation:

| Current transition | Evidence relative to the repository root | Problem |
| --- | --- | --- |
| New proof to export | `ar/modeling/native/materials.py:340–341`; `native/stage_export.py:211–213` in the same app | Preview interpolates transmission colors; export interpolates density then exponentiates. With endpoints 0.1 and 1, midpoint values are 0.55 and about 0.316. |
| Strong mirror to export | `ar/modeling/native/materials.py:391`; `native/stage_export.py:177–190` | Coating strength becomes a much smaller metallic value; coating color is mixed into the transmitted body color. |
| Old procedural material to export | `ar_v4/modeling_auto/blender/worker.py:1679`, `:1831–1833` | View-dependent behavior is sampled head-on, metallic is capped and coated lenses are darkened to a fixed target. |
| Gradient to face tint | `ar/src/render/eyewear-shadow.ts:36–125` | Shadow transmission reads color/maps but not the exported vertex-color gradient. |
| Mirroring to face tint | Same shadow code | Reflection-related material properties are not represented in the transmitted tint approximation. |
| Optical classification | `ar/src/render/renderer.ts:387–395` | Several paths recognize lenses through positive material transmission; nearly opaque mirrors still need optical semantics. |
| Quality target | `ar_v4/modeling_auto/blender/worker.py:34` | A universal minimum see-through value of 0.6 conflicts with legitimate dark/high-mirror lenses. |

Therefore changing sampled RGB, increasing reflection intensity or adding another material pass cannot ensure consistent color end to end.

### Canonical lens appearance

Describe the light that passes through the lens separately from the light reflected by its coating. A useful effective model in linear light is:

`observed radiance ≈ transmitted background × T(surface position, angle) + reflected environment × R(surface position, angles, roughness)`

The renderer also accounts for the frame/face occlusion and refraction model. RGB coefficients must respect passive energy accounting; they are not alpha coverage. Dark tint is absorption, not automatically reduced geometric coverage. A strong mirror must not require tinting the wearer's face the reflection color.

Required descriptor fields:

1. Lens-local surface coordinates with a recorded orientation; spatial absorption/transmittance profile and gradient stops. The gradient must remain attached to the lens during head roll and export.
2. Reflection color/strength and roughness separately from absorption; angular coating behavior where supported. Use an effective angular response when the true film stack is unknown; do not label an RGB fit a recovered physical coating.
3. Optical thickness/path convention and IOR; optional front/back asymmetry. Double-sided rendering must not accidentally double-apply tint.
4. Input color space, interpolation domain, linear-light equations, and version. One gradient formula is shared everywhere.
5. Confidence, observed versus inferred parameters, supported renderer profile, and any explicit fallback or capability loss.

The experimental `lens_appearance.py` reference contract covers a subset: lens-local optical-density gradients, separate effective reflection, angular response and energy accounting. The subsequent [conformance experiment](LENS_CONFORMANCE.md) verifies this response through native Blender shader nodes, GLB descriptor transport and an isolated GPU consumer in the AR codebase. It does not validate roughness illumination, full multilayer interference, front/back asymmetry, photochromic activation, polarization or the production AR shader. Those remain capability requirements where relevant; they must not be silently asserted by the prototype.

Compile the same descriptor to (a) proof rendering, (b) exported asset/runtime material, and (c) the transmission used for face tint/shadows. Use standard glTF extensions where they preserve the intended behavior. If a portable material cannot express an effect, retain an explicit high-fidelity AR profile plus a declared portable fallback. Do not quietly weaken mirrors to satisfy a format restriction.

Khronos defines separate [transmission](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_transmission), [volume absorption](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_volume) and [iridescence](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_iridescence). The latter is a thin-film approximation, not a universal prescription for commercial multilayer coatings. [Three.js physical materials](https://threejs.org/docs/pages/MeshPhysicalMaterial.html) expose related capabilities, but the actual loaded/exported material and environment must be verified.

### Inferring lenses from product photos

Do not equate pixel color with lens pigment. Pixel values also contain studio reflections, transmitted background, exposure, white balance, tone mapping and lens orientation. A center-to-edge hue change may be reflection or angle response; a top-to-bottom change may be a physical gradient or a reflected softbox.

Estimate spatial appearance in lens-local coordinates across views. Estimate angular response from changes that track geometry/viewing angle, with constrained lighting hypotheses. Avoid an arbitrarily flexible per-photo environment that could explain away every material error. Reject clipped highlights as color measurements. Material metadata may constrain candidate families without being treated as proof.

Where several material/lighting hypotheses fit equally well, preserve that ambiguity and select a declared AR approximation based on cross-environment appearance. Additional photos or supplier tint/coating information can resolve it. Arbitrary uncalibrated photographs cannot uniquely determine every hidden surface or optical parameter; broad automation must handle this condition explicitly rather than claiming certainty.

## Preventing another repair cycle

Every stage needs a consumer-tested contract:

- Input → observations: semantic coverage and uncertainty. Contrast stability alone is not complete segmentation. Clear optics need boundaries/landmarks, not binary darkness.
- Observations → camera/shape: bounded cameras and articulation; no independent shape per view, no unconstrained X/Y resizing, no camera freedom used to conceal a wrong bridge.
- Shape → materials: preserve component identities, UV/surface coordinates and attachments. Lens segmentation cannot be re-derived from arbitrary post-texture colors.
- Materials → export: numeric roundtrip and rendered equivalence for the same descriptor, lighting, cameras and color transforms.
- Export → AR: lens identity, axes, units, gradient orientation, reflection response and face-transmission behavior survive simplification and loading.
- Measurements → acceptance: missing evidence never becomes zero error; per-component failures cannot be averaged away. A candidate optimized on a view is not independently validated by that same view.

Freeze independent evaluation observations, validity masks and confidence policies before candidate refinement. The optimizer must not be allowed to improve its score by marking inconvenient pixels unknown. Record and bound nuisance fitting (camera, exposure and lighting), use held-out observations, and retain worst-localized as well as aggregate errors.

Tests must have negative controls: remove a temple, fill a lens opening, reverse a gradient, reduce mirror strength, lose one gradient channel, drop a material extension, swap color-space interpretation and omit observation coverage. A test must fail when its promised property is deliberately broken. Independently inspect exported bytes and rendered output; a generator and verifier that share the same bug can agree perfectly.

The repeated-loop policy is bounded and transactional: inspect, propose, apply, render, measure, keep only supported improvements, otherwise restore. Quality debt must not be hidden by moving to the next stage. Stop stagnating loops with a specific diagnosis. Budget exhaustion must not be called successful completion.

## Next implementation order

1. Finish the architectural coverage review and a canonical lens contract with falsification cases. Preserve the historical apps as baselines.
2. Build an isolated appearance comparison harness: known descriptors through proof, export, actual AR material and face transmission under the **same** environments. Include uniform tint, gradients, high mirror and combined cases. This should precede further automatic color tuning.
3. Establish reviewed geometric/component observations and reliable camera fitting on varied development frames. Keep automatic contrast masks as diagnostics only until their semantics are established.
4. Demonstrate constrained geometry refinement on several products with frozen cameras/independent observations or withheld views; then integrate automatic perception and bounded candidate refinement.
5. Freeze the complete pipeline and test unseen products and repeatability without product-specific interventions. Measure appearance after export, not only Blender appearance.

This order prioritizes the cross-stage contracts and the specific lens failure mechanism. The current experimental metric and camera tools do not yet satisfy these milestones, and green unit tests are not evidence that reconstruction across glasses types is complete.
