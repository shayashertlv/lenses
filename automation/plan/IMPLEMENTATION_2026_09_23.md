# Implementation and what still prevents faithful reconstruction

Scope: existing product photographs to visually faithful eyewear in the existing AR renderer. The AR engine handles wearer-width scaling. No generated examples or assets were published to the live application.

## What is implemented

`reconstruction/photo_semantics.py` provides `infer_product_hypotheses`, `validate_product_hypotheses` and `rebind_product_hypotheses`. Absorption and coating are separate fields. Evidence binds source hashes, pixel grids and photo identities. Rebinding a JPEG interpretation to a job PNG requires exact decoded-pixel identity. Qualitative color words never become RGB shader values. Coarse semantic regions remain proposals; the planned SAM proposal-selection refinement is not implemented.

`reconstruction/semantic_transport.py` supplies an explicitly configured `GeminiSemanticClient`. Successful requests are content-addressed and reusable; uncertain requests are not silently resubmitted. Call reservations enforce a durable budget. Credentials are absent from request URLs and sanitized in stored results. Blind review labels are included in request identity.

`reconstruction/appearance_anchors.py` measures pixels where semantic clean regions intersect actual optical observations, excludes reflected/rear/clipped content, deduplicates shared pixels across mask alternatives, and reports conditional optical-density intervals. Local gradient coordinates come from exported lens geometry. `compile_material_priors` keeps inferred preferences separate from declared facts. `estimate_transmission_from_contrast` implements local linear-RGB regression against an independently supplied rear-content template plus a smooth reflected-light term. That estimator passes controlled tests, but no automatic real-photo template correspondence is implemented.

`photo_lens_fit._Model` and `joint_photo_lens_fit._JointModel` accept the source-bound priors. The new `semantic_softbox` field has fixed hypothesized illuminant chromaticity and bounded scalar reflection amplitudes; photographic blocks are shared across groups. Density priors initialize fitting and enter a robust soft objective, with contrary starts retained. The joint stage pins priors in its request and checkpoints. Finite differences are required for this new branch. Prior-assisted validation is explicitly conditional because the priors can use pixels from the nominal held-out split.

`reconstruction/ar_material_search.py` keeps the photo winner, tint and mirror alternatives, measured density variants, and a clear-lens hypothesis when supported. It does not manufacture color from the model's words. Unobserved density endpoints retain an explicit regularization toward an existing fitted tint. All candidates are exported through the existing verified optical-group writer with unchanged geometry.

`ar/qa/semantic-material-cards.{mjs,html}` renders the actual AR engine at front, angled and rolled poses over white, light-skin and dark-skin backgrounds, with synthetic eyes testing transmitted contrast. The model sees original photos and unlabeled render cards, then sees the same candidates in reverse order. It receives no numerical scores, material names or our preferred result.

Selection distinguishes three cases: a consistent exact winner; consistent rejection of the baseline but an ambiguous plausible set (numeric evidence supplies a declared tie-break); or unresolved comparison with the baseline retained. Repeated model reviews are not independent human validation. No output is marked accepted.

`reconstruction/semantic_appearance_stage.py` connects these operations for cached jobs. `job.run_job` also integrates an opt-in semantic mode: evidence and priors before fitting, AR comparison after export, then copies the selected GLB. Completed stages resume without spending again. Provider-only back/side photographs are included in semantic interpretation for new jobs and normalized into stage snapshots. Their presence does not yet give them numerical optical observations.

`reconstruction/view_scene.py` and `structured_refinement.py` implement source-face bindings, shared hinge definitions, per-photo temple rotations, front-only camera fitting, temple-pose fitting, shared cage proposals, residual checks and failure classification. This is a working solver with explicitly supplied bindings/correspondences. It is not wired into production ray consumers, and it does not infer reliable hinges or missing topology automatically.

## What the experiments say

The first image-only probes inferred brown gradient VB, clear Miu and colored-mirror Oakley. That was evidence for semantic interpretation, not evidence of complete reconstruction.

On real cached observations, VB supplied five supported density anchors per lens group. Miu supplied none because clean, unclipped support was too sparse. Oakley's ordinary-coating density samples were withheld from mirror hypotheses. These failures are useful: clipped white pixels and mirror reflections must not be treated as direct absorption measurements.

A frozen VB observation-branch experiment performed 12 fits in 8.84 seconds. The tint's worst-region validation error changed from 6.486 to 6.405 codes under bounded semantic lighting; mirror changed from 4.357 to 4.341. Tint fits converged; mirror fits reached the 30-evaluation cap. The mirror still wins the photographic residual. The lighting change alone does not solve material selection. All configurations used the same sampled branch and split; this is not an exhaustive rerun of the old fit. Four fitted candidates were exported with geometry verified unchanged.

Eight VB materials were then rendered and reviewed in both orders. Both reviews rejected the chrome-like baseline, but exact brown-tint ranking changed with order. After correcting the renderer defect below, the new selection picked a measured brown-gradient proposal from the consistently plausible pool using its numerical anchor agreement. It reports that ambiguity rather than claiming unanimous agreement on tint strength.

Miu's six-candidate run retained its existing clear baseline: the reviewers agreed that several clear candidates were plausible but did not identify one stable exact winner. This is an honest unchanged result, not an accuracy improvement claim.

Oakley's five-candidate run selected an existing angular-mirror alternative instead of the neutral/silver baseline. A follow-up changed every candidate's label and position, including the middle candidate that simple reversal leaves fixed; the same physical candidate won. Its green/purple color behavior is closer, but the model is visibly incomplete: hard purple/colored fragments and faceted patches remain. A relative material preference does not establish a faithful finished shield.

The structured-geometry synthetic experiment recovered independently varying arm poses across two views with a maximum role RMS of approximately 5.56e-8 pixels and an unchanged shared rest mesh. This proves the solver handles its controlled articulated case. It does not prove automatic geometry recovery from product photos. Actual Miu inspection found 12 disconnected components, including an isolated arm fragment, which invalidates the shortcut of assigning one connected component to each temple.

## An unexpected rendering defect, now corrected

The higher-resolution cards showed a triangle-edge web across the lenses. A QA-only normal-flattening control left the web intact. Omitting nearest-event depth equality only from final display removed it in all nine VB pose/background combinations, keeping geometry and optical descriptors unchanged.

The correction in `ar/src/render/lens-material.ts` retains exact nearest-event filtering during optical composition. Final display instead copies the already-composited opaque result with ordinary scene depth testing. Repeated overlapping optical surfaces copy the same RGB, so the final copy is idempotent. The former check compared the single-sample nearest-depth field with the final multisampled display raster and exposed triangle boundaries. The live published `ar/site` has not been rebuilt or deployed.

Relevant AR unit and type checks pass. The full production numerical conformance run passes camera color/depth, transmission, overflow and receiver checks, but still fails its pre-existing shadow-depth tolerance case (approximately 4.10345e-6 versus a 2e-6 limit, matching the saved previous failure). The display correction does not change shadow arithmetic. See the generated reports for the precise coverage rather than interpreting a passing browser load as appearance validation.

The new GPU display regression covers 15 dense, closed, multipart, overlapping-group and opaque-stop conditions with 4x MSAA. It checks 460,784 interior pixels, including internal triangle boundaries: zero failures after the correction. Restoring the former gate yields 3,131 failing pixels, with a maximum linear-RGB error of 0.82975. External silhouettes are excluded because the single-sample reference does not describe partial MSAA edge coverage. The complete Python run reports 792 tests, one skipped, with no failures; the later comparison-order change also passes all 11 focused comparison/selection tests. All 414 AR unit tests pass, and the TypeScript check passes.

Ten bounded review API calls in this implementation turn consumed 130,837 reported tokens. Prior interpretation probes were reused; no new 3D-generation provider task was submitted. One attempted reuse with a different cache budget was rejected locally before an API request; the additional position experiment used a new explicitly bounded cache. Cached candidates and previous evidence were preserved.

## The strongest remaining changes

1. **Audit complete optical membership before optimizing materials.** This is now an observed failure, not speculation. A control render hides every declared Oakley optical material, yet a purple shield-shaped side patch and smaller colored remnants remain opaque. The fitted shader cannot affect those omitted surfaces. `audit_optical_membership(grounded_lens_regions, posed_face_projections, declared_faces)` should identify persistent non-optical front-surface support inside clean lens regions across views, retain source-face provenance, and require correction or an unresolved result. Pure missing pieces can be added through explicit group alternatives; mixed frame/lens primitives need face partitioning. Assigning a whole fused front primitive to the lens would make the frame transparent. The saved [structural audit](C:/Users/Shay/PycharmProjects/lenses/automation/data/semantic-render-diagnostic/oakley-no-optics-final/optical-membership-audit.json) distinguishes the observed defect from unverified component assignments. Its general automatic gate is not implemented.

2. **Replace unsupported geometry with eyewear construction rules.** A dense generator mesh plus a small cage cannot restore a missing bridge, separate fused lens/frame surfaces or make a smooth optical surface. A concrete hybrid is `fit_front_curve_network` for paired lens loops or a shield, `fit_front_bend` from oblique/back contours, `sweep_bridge_and_rim` constrained to connected attachment points, and `fit_temple_centerline` from side evidence. Preserve usable generated ornaments and textured frame pieces. Compare a small set of construction hypotheses. This changes the representation so correct connectivity can be built, instead of hoping deformation repairs it. This proposal still requires an experiment with independent contours before becoming the default.

3. **Pair the material parameters of a manufactured lens pair.** The current joint fitter shares lighting, but each lens has independent density and reflectance. Misregistration can therefore become left/right tint asymmetry. Introduce `infer_material_relations` and an optional shared material-parameter block for paired groups; compare shared and independent hypotheses on fixed support. A shield naturally remains one group. The near lens can then constrain its poorly observed partner without forcing pixel-level bilateral symmetry.

4. **Extract independent transmission information from back views and rear objects.** Current numerical observations exclude incidence at or above 90 degrees. Oakley's pink rear appearance reaches semantic interpretation but does not constrain fitted absorption. Add a rear-view transmission observation model with separate reflection hypotheses, instead of simply flipping angles. For temple crossings, implement `find_rear_template_correspondences` against observed same-material segments, bounded exposure/refraction registration, then call the tested contrast estimator. Generated textures are not valid independent rear templates. This targets the remaining reflection-strength/transparency ambiguity directly.

5. **Separate frame albedo from photographic highlights.** The retained provider texture can already contain bright studio reflections. Lighting it again in AR duplicates them. Use grounded cross-view correspondences to identify view-dependent highlights, retain persistent pattern/markings, and compare a small number of frame finish hypotheses in the real renderer. Paint-like color replacement would destroy marbling and branding; this needs texture provenance and an explicit unresolved state where views disagree.

6. **Make evidence selection and validation independent.** The planned numerical refinement of semantic boxes, scalable conditional mask solver, automatic face/hinge binding and posed-ray integration remain unfinished. Full Cartesian mask enumeration still limits additional views. Add a bounded alternating mask/material search checked against exhaustive small fixtures, and freeze its evaluation domain. Keep an unseen-product/held-out-angle evaluation set, wrong-hue/reversed-gradient/opaque-lens controls, and human review of borderline cases. The current three familiar products establish useful behavior, not general reliability.

These are distinct bottlenecks. More AI calls cannot repair a missing geometric representation or supply a truly unobserved transmission measurement. The productive use of AI is choosing supported hypotheses and correspondences, followed by measurable geometry/material operations and observable rendering checks.

## Reproduction

```powershell
python -m unittest discover -s tests -q
python -m reconstruction.semantic_appearance_stage --job data/jobs/victoria-beckham-fresh-v10 --semantic-report data/semantic-material-probe-v1/case-A.json --output data/semantic-implementation/new-vb-run --cache data/semantic-implementation/new-vb-cache --api-key-env GEMINI_API_KEY --maximum-api-calls 2
python -m qa.semantic_optics_pilot --help
```

Each run requires a fresh output directory; existing output is evidence. API cache reservations remain separate and reusable. `qa.review_semantic_candidates` rerenders an existing material set after a renderer change without regenerating geometry or materials. Full job mode requires the existing configured local region/aperture engines and `--appearance-mode semantic_ar_v1`; use `--semantic-api-key-env`, `--semantic-cache` and an explicit call budget.

Local results: `data/semantic-implementation/vb-search-fixed-renderer`, `miumiu-search-v1`, `oakley-search-v1`, `oakley-position-check-v2`, and `vb-optics-pilot`. Structured geometry: `data/structured-geometry-v2/report.json`. These private data/assets remain outside Git.
