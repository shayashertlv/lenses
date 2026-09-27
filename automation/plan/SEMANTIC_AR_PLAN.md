Proposed implementation: semantic interpretation and AR-targeted reconstruction

23 September 2026. Scope: existing product photographs to visually faithful glasses in the existing AR renderer. Face-width fitting remains the AR engine's responsibility. This is a function-level design and a small API feasibility experiment; the proposed production stages below are not implemented or validated end to end.

**The change in approach.** A multimodal model supplies product understanding: which regions are lenses, which lines belong to rear temples, what kind of tint/coating is plausible, and what visible defects distinguish candidates. Numerical code supplies coordinates, sampled colors, mesh transformations and material parameters. The actual AR renderer supplies the final comparison images. The model chooses among bounded interpretations and edits; it does not generate unrestricted Blender programs or certify its own work.

The current AR renderer has a useful fixed target: [renderer.ts:454](C:/Users/Shay/PycharmProjects/lenses/ar/src/render/renderer.ts:454) constructs a `RoomEnvironment`, with lens environment intensity based on 0.8. Therefore the target can be a material that preserves the product's apparent tint, gradient, transparency and coating behavior in this actual renderer. Recovering the photographer's exact studio or the physical coating chemistry is unnecessary for that target. Test several face/background colors and head poses so the result is not optimized to one screenshot.

**A real first experiment.** [qa/semantic_material_probe.py](C:/Users/Shay/PycharmProjects/lenses/automation/qa/semantic_material_probe.py) made three fresh Gemini 3.8 Flash calls, one per product, using the same frozen prompt and five original photographs each. No product names, previous diagnoses, generated models, material facts or quality scores were sent. The complete [receipts and results](C:/Users/Shay/PycharmProjects/lenses/automation/data/semantic-material-probe-v1/summary.json) record 22,267 tokens and 25.422 seconds of API time, with three HTTP-200/schema-valid responses.

| Product, identified locally after inference | Image-only proposal |
|---|---|
| Victoria Beckham | Warm brown/tan gradient, denser toward the top; mirror unlikely. |
| Miu Miu | Clear lenses; rimless, gold-tone frame and patterned temple tips. |
| Oakley | One shield with green/magenta/cyan angular mirror appearance; also notes rose-pink appearance from the back. |

This establishes that an inexpensive semantic call can supply useful hypotheses on these development cases. It does not establish general reliability, accurate mask coordinates, correct physical causes, or improved generated assets. The Oakley response overclaims that the photographs confirm interference physics; that claim must not become pipeline data. Its second interpretation also combines tint with a mirror, demonstrating that the production schema should separate absorption from coating rather than use mutually exclusive names.

A second, separate experiment used [qa/semantic_material_review_probe.py](C:/Users/Shay/PycharmProjects/lenses/automation/qa/semantic_material_review_probe.py) to compare existing VB AR renders against its five original photographs. Captions, candidate names, scores and previous diagnoses were excluded. Two independent calls reversed candidate order. Both preferred the brown-tinted material over the chrome-like material, at medium uncalibrated confidence; both also flagged stippling and the small render size. The current photo residual had favored the chrome-like result. The [blind comparison receipts](C:/Users/Shay/PycharmProjects/lenses/automation/data/semantic-material-probe-v1/blind-review/summary.json) therefore support investigating a semantic selection signal, without proving general judge accuracy. These were existing renders, not newly improved assets. All five calls completed successfully, consuming 43,247 tokens and 36.72 seconds of API time in total.

Google documents multi-image input and structured image interpretation in its [image understanding](https://ai.google.dev/gemini-api/docs/image-understanding) and [structured output](https://ai.google.dev/gemini-api/docs/structured-output) guides. Account availability was checked before the experiment. API capability is not evidence of task accuracy.

**One structured product description, before mesh fitting.**

New `reconstruction/photo_semantics.py`:

```python
def infer_product_hypotheses(photos, *, client, policy) -> ProductHypotheses: ...
def validate_product_hypotheses(raw, image_manifest) -> ProductHypotheses: ...
def ground_region_proposals(photos, hypotheses, *, region_engine) -> PhotoEvidence: ...
```

`ProductHypotheses` contains construction (pair/shield, rim type), part/landmark proposals, per-photo visible/occluded states, and separate appearance fields:

```text
absorption: clear / uniform / gradient / uncertain
absorption_hue: qualitative observations and supporting image IDs
gradient_direction: darker at top / bottom / none / uncertain
coating: ordinary / colored mirror / uncertain
coating_variation: constant / angular / spatial / uncertain
front_back_observations: observed differences, without asserting their cause
regions: aperture / frame / rear hardware / reflection / clean sample / uncertain
alternatives: at most two supported interpretations, plus contrary baseline
```

Every region refers to image ID, source hash, coordinate convention and evidence. Model certainty is ordinal and uncalibrated. Product facts supplied by the user remain distinct and take precedence. In particular, a model's mirror guess must not be inserted into `lens_facts.mirror_coating`, which currently hard-filters families.

The model initially proposes coarse locations. Existing SAM and aperture code generate numbered mask/curve alternatives. A bounded follow-up can select alternatives or `none_supported`, using original photos and overlays. Code refines coordinates against local image edges and checks boundaries/known domains. The model's boxes are not precision masks. Preserve an explicit transform between normalized images and the original-pixel evidence contract in `evidence.py`; do not silently mix EXIF-rotated and original grids.

**Geometry: separate camera, articulation and shared shape.**

New `reconstruction/structured_refinement.py` and `reconstruction/view_scene.py`:

```python
def propose_part_bindings(scene, photo_evidence, coarse_cameras,
                          *, maximum_hypotheses=3) -> list[PartBinding]: ...
def fit_front_cameras(scene, binding, evidence, camera_seeds,
                      *, policy) -> CameraSet: ...
def fit_temple_poses(scene, binding, evidence, cameras,
                     *, policy) -> dict[str, ViewState]: ...
def pose_scene(rest_scene, binding, view_state) -> PosedScene: ...
def fit_shared_geometry(scene, binding, evidence, cameras, view_states,
                         *, policy) -> StructuredFitResult: ...
def assess_geometry_candidate(exported_scene, evidence, result,
                               *, policy) -> GeometryAssessment: ...
```

`PartBinding` binds source face IDs to front assembly, each temple, lens groups and unresolved faces. It includes shared hinge origins/axes and retains face lineage. Initial bindings reuse the component inventory and current physical-group hypotheses. Ambiguous source components can be presented in numbered 3D renders for semantic interpretation, with projected visibility checked numerically. A rear temple lying inside a lens aperture must remain a temple.

`fit_front_cameras` fits front-frame and lens-boundary evidence first, excluding temples and image reflections from the camera objective. True landmarks include identifiable bridge/hinge attachments; silhouette extrema are curves, not fixed 3D point correspondences. Keep several coarse orientation seeds where needed.

`fit_temple_poses` rotates each arm about its shared hinge independently per photograph. For a rest vertex X, hinge H, axis a and photo angle theta, `X_photo = H + R(a, theta) * (X - H)`. Fit those poses with rest geometry fixed. Only then unlock the existing bounded cage for shared residual shape correction. Alternate these blocks for at most three initial experimental rounds. Use robust, role-normalized boundary/landmark losses and attachment/shape regularization; one large lens must not outweigh an incorrect bridge.

Changing photo pose must reach every consumer: `component_projection.project_components`, `region_proposals.run_region_stage`, composition diagnostics and `optical_group_observations.project_effective_group_fields` must all consume `pose_scene(...)`. Otherwise the geometry objective and material-sampling rays describe different objects. Export one canonical rest model, not a mesh separately distorted for each photograph.

Missing topology is a separate failure class. `classify_geometry_failure(assessment)` returns pose, small-shape, topology or insufficient-evidence. For topology, `evaluate_initializer_candidates(candidates, evidence)` compares at most two candidates under the same frozen observations. No unsupported provider correction prompt is assumed. If neither contains a usable bridge/temple, stop with an explicit unresolved result. This policy bounds wasted work; it is not a claim that arbitrary missing topology has been solved.

**Appearance: interpretation determines what to measure; code measures it.**

New `reconstruction/appearance_anchors.py`:

```python
def sample_appearance_anchors(observations, semantic_regions,
                              *, policy) -> AppearanceAnchors: ...
def compile_material_priors(hypotheses, anchors,
                            *, declared_facts) -> MaterialPriors: ...

def estimate_transmission_from_contrast(
    photo_linear, lens_patch, rear_template, coordinate_fields,
    template_uncertainty, *, policy
) -> ContrastTransmission: ...
```

For tint, intersect supported clean-region proposals with actual lens membership, visibility and source pixels. Record joint RGB statistics by intrinsic lens height, sample counts, view and pixel IDs. Exclude known foreground and rear hardware from this clean-color estimator. Normalize using a measured credible backdrop/illuminant hypothesis and initialize density with existing `optical_density_from_linear_transmission`. Use vector color statistics, not unrelated per-channel minima. Missing top/bottom support yields broad ranges, not invented endpoints.

For colored mirrors, keep a separate reflection-chromaticity target. A green/purple reflection must not automatically become green/purple absorption. Existing back photos may constrain transmitted appearance more usefully than another frontal photograph; the Oakley probe explicitly revealed that opportunity. Front/back differences alone do not prove coating asymmetry because lighting is uncalibrated.

Do not set shader RGB to the observed photographic RGB. The latter includes its backdrop and illumination. Numeric anchors initialize or constrain an effective material whose behavior is then rendered over several backgrounds.

**Use rear hardware as a transparency clue, when a credible reference exists.** The current clean-color fitter excludes rear temples. Those pixels can support a separate contrast estimator: over a small patch, model `image(x) = transmission * rear_content(x) + a + g*x`. Transmission scales the rear object's contrast; the constant/planar term absorbs approximately smooth reflected light. Solve per linear-RGB channel, retaining an uncertainty interval rather than a single exact transmission value.

The missing quantity is the unobstructed rear-content template. Prefer a comparable segment of the same material in the same photo, outside the lens, with compatible lighting and orientation. A different photo needs exposure/white-balance uncertainty. Geometry proposes correspondences; image evidence verifies them with a small registration/refraction allowance. Generated mesh textures are not independent measurements of the rear object. If no credible template exists, this estimator returns unavailable.

Reject clipped patches, weak rear contrast, unstable registration, substantial within-patch tint variation, and sharp reflections coinciding with the rear edge. Return the transmission interval, support pixels, registration residual, smooth-reflection residual and source provenance. Feed the interval as a soft constraint on the material evaluated at the patch's intrinsic height and viewing angle. A visible temple alone is not a quantitative transmission measurement.

Validate first on synthetic rear stripes/temples with transmission 0.85, 0.35 and 0.05, colored tints, and independently moving/brightening reflections. Template uncertainty should widen the result, while flat white backgrounds, clipping, bad registration and coincident reflection edges should produce unavailable or low-support results. Then inspect real Miu temple crossings and VB patches. This is an additional identifiable signal under explicit assumptions, not a solved universal reflection/transmission separation method.

**A specific correction to the existing optical fitter.**

Current `_Model.predict()` allows independent RGB environment radiance, letting coating color migrate into disposable photographic lighting. Add a bounded `semantic_softbox` branch:

```text
environment(photo, pixel) = illuminant_rgb(photo) *
    (base_scalar(photo) + sum(amplitude_k(photo) * softbox_basis_k(pixel)))
```

Anchor illuminant chromaticity to credible neutral evidence, or state neutral studio lighting as a competing prior. Softbox bases are feathered reflection-region proposals, with nonnegative amplitudes shared by both lenses in a photo. They explain observed highlights during fitting and never enter the exported material. Retain the unrestricted lighting branch as an alternative.

Changes in `photo_lens_fit.py`: `_Model.__init__` adds blocks; `initial` consumes measured anchors; `predict` evaluates the bounded light field; `penalties` adds supported color/gradient priors. In `joint_photo_lens_fit.py`, `_JointModel` shares the new photographic blocks and counts priors once. Extend `fit_joint_photo_lens_candidates(..., appearance_priors=None)` and `run_joint_photo_lens_stage(..., appearance_prior_report=None)` with pinned inputs. Begin in finite-difference mode; optimize analytic derivatives only after visual benefit is demonstrated.

Initialize absorption from clean patches, fit reflection-light amplitudes, then perform a short joint update. A proposed ordinary brown tint starts with the existing ordinary-reflection response, not a free chrome coating. The full-image residual still reports the bright rectangle; it simply cannot dictate intrinsic tint by itself.

**Choose materials by their actual AR result.**

New `reconstruction/ar_material_search.py`:

```python
def propose_material_candidates(anchors, priors, *, existing_candidates,
                                policy) -> list[MaterialCandidate]: ...
def compare_rendered_candidates(photos, blind_cards, *, client,
                                policy) -> AppearanceComparisons: ...
def select_ar_material(candidates, anchors, comparisons,
                       *, policy) -> MaterialSelection: ...
```

Add `renderMaterialCards(manifest)` to the existing private AR QA harness. Start with six to eight candidates: current winner, best supported tint/mirror alternatives, and a small deterministic parameter grid around measured anchors. Reuse `LensAppearance`, its density/angle keyframes and `write_optical_group_candidate`; no new optical representation is needed for the first test.

Render the actual assets with identical lighting, white/gray and several synthetic face backgrounds, an eye-like contrast pattern, and front/angled/rolled poses. The comparison model sees blind IDs and the original photos, with a constrained output vocabulary: wrong hue, too silver, gradient missing/reversed, too opaque/clear, mirror missing, reflection baked into tint, neither supported. It does not get photo scores or our preferred candidate.

Introduce an explicit `semantic_ar_v1` selection mode alongside `select_appearance`. Combine measured clean-region agreement, stated product facts, semantic consistency, transmission/reflectance probes and blinded render comparisons. Preserve contrary candidates and uncertainty; an ordinal model judgment is not a probability or a final acceptance certificate. A global improvement in photo residual alone cannot override a clear visual contradiction. At most two bounded parameter-refinement rounds are allowed in the initial experiment.

Avoid repainting frames from a verbal color label. Preserve provider texture/pattern and make optional numeric corrections only on confidently grounded frame regions. Interpretations such as 'marbled acetate' also need visual verification.

**Make additional views affordable.**

Semantic region selection should remove many irrelevant alternatives before fitting, but a scalable solver is still required. Add `fit_conditional_materials(observations, priors, policy)`: alternate continuous material/lighting updates with conditional per-region mask-state selection, using the existing `select_photo_mask_states` where its disjoint-state assumptions hold. Preserve a bounded beam for overlapping/ambiguous regions instead of enumerating the full product across photos. Compare against exhaustive fits on small fixtures; report local-search status honestly. Freeze evaluation domains separately so a shrinking selected mask cannot manufacture improvement.

**Job integration and build order.**

The proposed stages in `run_job` are `input → photo_semantics → initializer → surface_transfer → structured_refinement → physical_groups → appearance_anchors → semantic_material_candidates → ar_comparison → export`. Existing receipts/checkpoints remain. New reports include the semantic provider model/version, prompt/schema hash, source image hashes and supported/uncertain claims. Stored declared facts and model-inferred priors never share the same authority.

Implement in small slices with clear stop conditions:

1. Image-only interpretation and blind ranking of existing materials. Exercised by the five calls above; no mesh regeneration. The VB order-reversal check passed. Other material families and unseen product identities still need independent checks before the judge controls automatic selection.
2. A cached-geometry appearance prototype for the three cases: numeric anchors, bounded illumination and AR cards. Keep geometry fixed to isolate the change. Show actual exported renders, not just better residuals. Test moving/brightening a synthetic softbox, colored coating under neutral light, head roll, different transmitted backgrounds, and a deliberately wrong semantic prior. Add the contrast-transmission estimator only where a credible rear template exists, following its synthetic checks.
3. A cached-geometry pose prototype: independently marked photo contours/landmarks as a diagnostic reference, then the identical solver using automatic evidence. Compare current cameras, semantic front cameras, articulation, and shared refinement separately. If reference observations cannot improve the Miu alignment without harming the other lens, the solver/geometry is the problem; do not blame the VLM.
4. Freeze the resulting prompt, algorithms and policies and evaluate new product identities without intervening per product. Only then integrate the mode as a supported automatic path, address asset-size/mobile constraints and decide whether a representation extension is warranted.

This design has implementable operations and experiments that can reject wrong assumptions cheaply. The semantic probe improves the evidence for pursuing it. It does not yet prove that the pipeline can produce perfect glasses across arbitrary photographs or correct every generator topology failure.
