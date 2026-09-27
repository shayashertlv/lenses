# Source-bound acceptance measurements

Design review and implementation status, 2026-09-23. Reservation/usage and photographic
part-extent producers in sections 1–2 are now implemented and wired into delivery;
see [the evaluation contract](EVALUATION_PHOTOS.md). Sections 3–4 remain unimplemented
physical-appearance producer designs. The frame-membership guard in section 5 is
implemented; see [its measured results](FRAME_APPEARANCE_GUARD_2026_09_23.md).
No acceptance thresholds are changed.

At the start of this review the acceptance path could not certify a real job: `bind_independent_measurements`
in `reconstruction/production_validation.py` cleared caller-supplied parts and
appearance scores and only recomputed held-out geometry. It now also recomputes
part extent and verifies complete reserved-photo usage/initializer history.
Physical appearance scores still remain unavailable. Completing that API requires
actual measurement producers, not accepting scalar claims.

There is also an information limit. Ordinary product JPEGs, particularly glasses
over clipped white backgrounds, do not generally identify physical RGB
transmission. A complete implementation must sometimes return `unmeasured`.
We can make the acceptance API reachable when existing input evidence is
sufficient; we cannot make every existing-photo job pass honestly.

## Evidence already available

| Existing producer/output | Usable numerical evidence | What it does not establish |
| --- | --- | --- |
| `multiview_intake.run_multiview_intake` | Pinned original/normalized image hashes, decoded pixel hashes, photo IDs and view labels; explicit duplicate omissions | Independent hold-out status: this intake currently promotes every owned photo into reconstruction |
| `refine_photos` camera/view-scene reports | Source-bound camera, normalization, per-photo temple state, source triangle roles and hinge contract | Correct semantic part identity or independent validation |
| `structured_refinement.GeometryEvidence` | Source image/geometry hashes; `face_ids`, `barycentric`, `targets_xy`, `sigma_xy`, optional contour normals | Independently observed correspondences merely because these arrays were supplied |
| `automatic_articulation.assess_photo_arm_state` | Frozen-pose projected arm boundaries and distances to photographed image edges | That an image edge belongs to that arm; its spatial hold-out cells are not independent photographs |
| `physical_group_stage` aperture files and ray composition | Image-only aperture alternatives with known domains; candidate visibility, optical core and contamination diagnostics | Ground truth lens membership; the masks and membership remain alternatives |
| `optical_group_asset.read_optical_group_candidate` | Verified final GLB geometry, actual attributes, canonical group IDs and optical descriptors, including strict compaction verification | Correct appearance or product identity |
| `optical_group_observations` / `project_effective_group_fields` | Final-candidate rays, group identity, intrinsic height, incidence, visible/stacked/rear fields, source-bound articulated projection | Known radiance behind or reflected by those rays |
| Joint observation NPZ + hashed `observations.json` | Native `xy`, `code_rgb`, `alpha_code`, `intrinsic_v`, `incidence_degrees`, `reflected_direction`, `rear_weight`, source hashes | `background_rgb` is a border-color hypothesis; unknown `rear_rgb` is not a measured template |
| `image_appearance_evidence.ground_semantic_regions` | Pinned source pixels, aperture consensus and eroded clean-region masks, excluded reflections/edges/alpha | Physical transmission, independently known illumination, or group association from an image alone |
| `appearance_anchors.estimate_transmission_from_contrast` | A conditional attenuation estimate and conservative interval from independently supplied aligned rear-template arrays | Automatic discovery of a real template, known camera response, or exclusion of reflection correlated with the template |
| `intrinsic_frame_appearance` track NPZ | Source faces/barycentrics, `sample_linear_rgb`, guarded `valid`, image membership/alpha, rest-frame view directions, baseline colors and corrections | Semantic frame identity or calibrated intrinsic material decomposition |
| `frame_surface_coverage` | Complete non-optical triangle-area denominator; independent area-stratified surface queries through guarded photos, with disclosed sampling allowance | Camera/mask systematic error; a finite sample is not an exact visibility certificate |
| Actual AR cards/runtime receipts | Real shader behavior, packaging parity, fixed pose/background render controls | Photographed ground-truth color or transmission; rendered backgrounds are synthetic controls |

The current joint fitter's spatial validation remains conditional on full-photo
semantic/numerical priors. It must not be relabeled as independent held-out
appearance. Likewise, a successful source hash check proves identity of an
artifact, not the truth of the information it contains.

## 1. Reserve evaluation evidence before reconstruction

Implemented by `evaluation_reservation` and `evaluation_stage`, including cached
semantic leak checks before initialization and a persistent single-candidate
consumption lock. The current geometry scorer discloses that it fits its nuisance
camera to the held-out silhouette; it does not yet separate nuisance-calibration
pixels from scoring pixels. The broader producer contract below retains that
remaining distinction.

Add a small producer before initialization, not at delivery:

```python
reserve_evaluation_views(owned_manifest, split_spec, output) -> reservation_receipt
verify_photo_usage(reservation_receipt, stage_receipts) -> verified_usage
```

`split_spec` identifies reserved photo IDs explicitly or uses a fixed, versioned
view-coverage rule before any candidate exists. The receipt snapshots bytes and
normalization provenance for all photos; records original SHA-256, normalized
SHA-256 and normalized pixel SHA-256; and separates reconstruction/evaluation
sets. Duplicate decoded images cannot straddle sets. A reservation policy must
leave the minimum reconstruction views available; insufficient inputs produce
an explicit unavailable-evaluation state.

Every provider, semantic, geometry, material, frame and candidate-selection stage
must record its actual image inputs. Validate these receipts against the
reservation before accepting the final candidate. The current complete-intake
receipt is a useful building block, but a list supplied by the caller is not a
complete use ledger. External semantic/provider cache reuse must verify its
original request manifest as well. Crops inherit their parent's source identity;
reencoding or cropping a reserved image does not make it a new training image.

Seal the selected final candidate and all reconstruction receipts before opening
evaluation photos. Camera/pose or lighting nuisance calibration on reserved
photos must be limited to declared calibration regions; scoring pixels cannot
fit those nuisance parameters. Using evaluation results to choose another
candidate consumes that hold-out: a repair may be useful, but its final score
needs a fresh reserved view or an explicit development-only scope.

The existing three development cases have already supplied their photos to
reconstruction/semantics. They cannot be made independent by moving filenames
into another folder. A fresh run can reserve one of their existing views before
all model calls, although fewer reconstruction views may reduce quality. The
current `measure_heldout_views` can then provide its existing frozen-geometry
measurement under its explicitly disclosed nuisance-camera scope.

## 2. A required-parts producer

Implemented by `required_parts.run_required_parts_stage()` and
`replay_required_parts()`. The producer replays image-only object consensus or
authored alpha, full-scene first hits, exclusive edge ownership and role extent.
Final-corner correspondence is exact; unsupported LOD lineage abstains. These
are conditional photographic support measurements, not ground-truth labels.

```python
measure_required_parts(final_model, optical_receipt, role_lineage_receipt,
                       photo_support_receipts, frozen_camera_receipts,
                       output, *, policy) -> measurement_receipt
```

The producer measures photographic support for the front, left temple and right
temple separately. Training photos can provide this corroboration, but the receipt
must say so; they do not become independent held-out performance evidence.
It must not call a role present just because the model has faces with that name.

1. Verify final candidate and optical receipt. Replay the source-to-final face
   lineage, including repair, partition and compaction; reject a role binding
   that cannot be mapped to the final triangles. LOD needs its own verified
   correspondence or remeasurement. No nearest-surface label copying silently
   replaces missing lineage.
2. Freeze image-only foreground/contour alternatives and their known domains
   before projecting the candidate. Store pixel coordinates, edge normals,
   source hashes and disagreement masks. A detector may propose support; its
   confidence or class label cannot be the measurement.
3. Reproject frozen role geometry and per-photo articulation. Retain first-hit,
   known-domain, unclipped samples. Store final face ID, barycentric coordinate,
   projected pixel, observed contour pixel, residual and visibility state.
4. Require supported extent along each temple from hinge region toward the tip,
   rather than many samples at one convenient contact. Require separate image
   support for both sides: one photographed edge cannot certify two projected
   arms. Count known contradictory contour/foreground evidence as failure;
   distinguish occlusion and unknown membership from absence.
5. Check evidence in more than one useful view when association is ambiguous.
   A copied arm, swapped side, flattened front or background line must lose
   support. Use fixed spatial calibration/scoring cells for any nuisance pose
   adjustment; never refit final geometry within this producer.

`assess_photo_arm_state` and `GeometryEvidence` supply useful projection and
array formats, but nearest edge distance alone is insufficient. The initial
producer should accept only unambiguous image-supported chains and leave
occluded, transparent or association-ambiguous parts unmeasured. Numerical
coverage/normal-distance limits belong in one versioned producer policy with
predeclared negative controls, not adjustable per product to obtain a pass.

## 3. Held-out color and gradient measurements

```python
measure_heldout_appearance(final_model, optical_receipt, reservation_receipt,
                          usage_receipts, photo_support_receipts,
                          photometric_control_receipts, output, *, policy)
```

Recompute final group rays with `project_effective_group_fields`; use image-only
aperture consensus/known domains, source alpha, and visibility to define the
evaluation domain. Persist native observed RGB, projected group/height/incidence,
support/exclusion masks, frozen predictions and per-pixel uncertainty bounds.
The denominator cannot silently shrink to the pixels a candidate explains well.

The hard part is the photographed illumination. Fitting arbitrary RGB lighting
to the same lens pixels being scored is circular. A valid photometric control
receipt must identify independently observed or calibrated lighting/exposure/
camera-response information, its source pixels and uncertainty. A white border
median supplies a displayed backdrop color, not the reflected environment or
scene-linear illumination. Generated texture, guessed neutral material, a
semantic clean box and the model's own rendered background are not controls.

Where controls identify the nuisance parameters, estimate them from separate
calibration pixels and freeze them before scoring. Where controls only bound
them, propagate those bounds through the final descriptor and rendering
equations. For automatic acceptance require a conservative error upper bound
below the unchanged 12-code color limit. Selecting the best explanation inside
a broad lighting interval establishes compatibility, not accuracy. If the
uncertainty cannot be bounded tightly enough, report `unmeasured`.

Measure gradient along the exported intrinsic lens-height coordinate, not image
rows. Compare independently supported top/bottom regions after their bounded
lighting/reflection effects. Preserve competing signs where the data permit
them; do not turn a weak observed gradient into a boolean match. A flat lens can
satisfy this gate only with evidence that bounds both the observed gradient and
the candidate's gradient near flat, under a fixed tolerance. The current
unbound `measure_appearance_controls` utility is not sufficient for this receipt.

An additional display-referred photo residual is useful for every job, but must
retain its conditional scope. It must not fill a physical-transmission field or
be advertised as material identification.

## 4. Transmission: sufficient evidence and the minimal producer

At a simplified lens ray, the camera records `I = f(T * B + R * L)`.
Unknown rear radiance `B`, reflected radiance `L` and camera response/exposure `f`
can compensate for a different transmission `T`. A clear-looking white lens,
pink back view, low reconstruction residual, or agreement between AI judges
does not remove that ambiguity. Converting a JPEG with the sRGB transfer curve
does not undo unknown camera tone mapping or studio exposure.

The existing contrast estimator fits `I = T * B + a + gx*x + gy*y` after
removing a plane from both observed and template fields. It is useful because
nonplanar rear structure can separate attenuation from smooth additive
reflection. Its output is correctly labeled `conditional_supported`.

```python
measure_contrast_transmission(final_model, optical_receipt,
                              template_correspondence_receipts,
                              photometric_response_receipts,
                              reflection_bound_receipts, output, *, policy)
```

Accept this input only when the existing photographs or associated measurement
records actually contain all of the following:

- An independently observed, unobstructed view of the **same** rear radiance
  pattern, with source pixels and a geometrically justified correspondence to
  the through-lens patch. Repeated-looking texture, a generated temple texture
  or inpainting is insufficient. Resolve refraction/registration uncertainty
  using image correspondences and include its error bound.
- Known or bounded scene-linear camera response, relative exposure and white
  balance for both observations. A documented compatible linear capture or
  source-bound calibration can provide this. An assumed generic JPEG response
  cannot establish it.
- Unclipped contrast after removing the smooth field, across enough independent
  spatial structure and the RGB channels being tested. The current estimator's
  minimum contrast/support/residual checks remain useful prerequisites.
- A defensible bound on reflected structure correlated with the rear template.
  Smooth residuals and agreement across views reject some wrong templates but
  cannot prove the absence of an exactly correlated reflection. Existing
  calibrated reflection controls or suitable independently known illumination
  can provide a bound; a semantic reflection label cannot.

Store the original/template pixel-coordinate arrays, masks, registration bounds,
linearized values, calibration and reflection-control source references, final
group/ray binding, estimated interval and all exclusions. Recompute from these
pinned inputs; do not accept a precomputed `transmission_rgb` as evidence.

For each measured group/height/incidence/channel, compare the final descriptor
with the full measured interval. With `Tpred` and `[Tlow, Thigh]`, use
`max(abs(Tpred-Tlow), abs(Tpred-Thigh))` as the conservative error bound. The
unchanged 0.12 gate passes only when that bound is small enough and required
coverage is present. An interval merely containing the candidate is not a pass.
Whole-lens claims require coverage of the relevant height/angle range; one
central patch does not certify a gradient or angular coating. Front/back
reciprocity in the model does not supply an unmeasured incident angle.

An existing exact-variant measured RGB/spectral transmission dataset with
traceable calibration and angle coverage could support a separate producer.
A single marketing VLT percentage or the word "clear" cannot satisfy the RGB
transmission gate. If the ordinary photos lack the evidence above, the minimal
additional information is a compatible independently observed rear template
plus radiometric/reflection bounds, or such a calibrated optical dataset.
This is a statement of missing information, not a request to change the user's
existing-product-photo constraint. Under that constraint some products must
remain `needs_review` for physical transmission.

## 5. Prevent frame tracks from sampling backdrop

The original `_observe_tracks` verified geometry visibility, camera binding and
alpha without independently establishing photographic foreground membership.
A misregistered projected arm could sample white backdrop consistently. The
implemented guard now rejects that path.

Blindly intersecting with `observe_image(...).mask` is also wrong as a general
solution. That mask is conservative backdrop contrast: white logos, white
frames, translucent frames and backdrop-colored patterns can be absent from it.
Conversely, shadows and rear objects can have strong contrast.

Implemented `build_frame_image_support(photo, region_directory, output)` returns pinned
native-grid tri-state masks: `known_frame`, `known_backdrop`, `unknown`. Start
with conservative independently grounded positives, not the complement of a
background test. A suitable initial correction domain is:

```text
geometric first-visible frame interior
  AND independently known image foreground/frame support
  AND non-optical/non-rear/non-reflection known support
  AND source alpha == 255
  AND bounded registration/visibility uncertainty
```

High-contrast evidence and agreement among image-only masks can propose
positive support, but contrast alone does not establish frame identity. Where
authored object alpha or independently established contour/feature evidence
supports a white logo or opaque white patch, it may remain eligible. Never fill
all frame-outline holes or extend a positive mask across a low-contrast region
solely because the candidate projects there.

Unsupported white/clear samples stay unknown: exclude them from **correction
and measured coverage**, while preserving their original texture. This avoids
erasing white logos to improve a score. Transparent frame pixels mix rear
radiance with frame response and need an explicit transparent-frame model;
the current opaque intrinsic-color stage should not reinterpret them as albedo.
An authored alpha cutout provides object-support evidence, not a radiometric
transmission measurement. Hidden RGB at alpha below 255 remains excluded.

The mask is integrated in `_photo_rasters`; `_observe_tracks` intersects `valid`
before exposure estimation and highlight separation. Independent area-stratified
surface samples use this same guarded observer for coverage; atlas texel density
cannot determine area credit. The stage persists mask hashes, membership per
track and exclusion counts. The complete
non-optical area denominator remains unchanged: lack of evidence lowers observed
coverage instead of making the unobserved surface disappear.

## Receipt contract, integration and bounded implementation order

Each new producer writes `report.json` plus hashed arrays/masks, with method and
policy version, final candidate SHA-256, optical/role binding references, source
photo byte and pixel hashes, implementation snapshot, complete usage/reservation
references, measurement scope, support and uncertainty. A receipt is an input
recipe: acceptance replays its supported producer against the current candidate
and trusted current-job history. Unknown methods and unsupported evidence types
remain unmeasured. Scalars in a receipt never bypass replay.

Implement in this order:

1. Reservation/usage ledger and `run_job` plumbing; exercise real held-out
   geometry end to end with a photo never sent to the provider or semantic model.
2. Frame membership guard and replayable parts arrays; derive required-parts
   status from pixels and final geometry, with copied/missing-arm negative controls.
3. The photometric-control and real-template input schemas/validators, then the
   contrast/color producers. Build calibrated positive fixtures and deliberately
   ambiguous fixtures first; do not invent control data for the cached products.
4. Register `parts`, `heldout_appearance` and `transmission` methods in
   `bind_independent_measurements`. Extend `run_delivery_stage`/job configuration
   to pass receipt references after final packaging. Remeasure after any geometry
   or appearance change; a verified lossless representation mapping can reuse
   measurements only if their semantic-array binding is explicitly checked.

Required controls: mutated source/candidate/array; reencoded reserved-photo leak;
semantic-cache leak; duplicated temple sharing one edge; background-edge arm;
fully occluded part; clipped white lens; wrong template; reversed gradient;
wrong hue; opaque candidate against observed contrast; broad transmission
interval; white logo; transparent frame; and known bright backdrop sampled by a
misregistered model. A positively calibrated fixture must traverse the actual
receipt reader and production gate, not just call `evaluate_acceptance` directly.

The honest completion criterion is twofold: complete source evidence can reach
the unchanged production gates, and insufficient ordinary photos cannot fake
that evidence. Passing the former in a controlled fixture does not certify the
three cached products; their real missing measurements must remain visible.
