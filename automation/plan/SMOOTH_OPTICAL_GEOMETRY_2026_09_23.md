# Smooth optical surfaces: implementation and evidence

`reconstruction/smooth_optical_geometry.py` adds a geometry-derived normal-field alternative while retaining the complete source positions, triangles, texture source, optical membership and lens coordinates. `run_smooth_optical_preparation` writes the standard preparation report/NPZ/export contract. The existing optical observer can load it with unchanged source-bound cameras and photo region proposals; optical samples and fits must be recomputed because incidence normals and the prepared-model hash change.

`fit_front_bend` first tries a single low-order depth graph. It also tests a two-skin construction when opposite-winding surface sets each supply substantial projected area. Each skin is fitted independently, using spatial cells held out from degree selection. The two-skin hypothesis requires overlapping projected triangle unions, consistent depth ordering, a small gap and similar normal directions. The effective normal is the derivative of the midpoint depth function. Winding is only a geometric partition; it does not establish physical front/back, true thickness, or the number of real refractive interfaces.

The initial single-surface experiment exposed a real modeling issue: generated closed lenses have two individually smooth skins. Mixing their depths into one cell median creates apparent roughness. The revised construction represents the two skins explicitly before deriving one effective field. A synthetic planar test also exposed and corrected a sampling bias: a median depth must use its actual sample XY, not the cell's mean XY. Coverage uses projected triangle unions because centroid occupancy changes with tessellation.

Unsupported constructions retain original normals. The original-normal alternative should also remain in appearance evaluation; smoothness of the generated geometry does not prove correct photographed curvature.

## Measured cached evidence

The fixed policy was applied to five groups across the three cached products. Worst-skin held-out 95th depth residual, relative to group width:

| Cached group | Residual | Skin overlap |
| --- | ---: | ---: |
| VB group-0002 | 0.542% | 98.72% |
| VB group-0004 | 0.438% | 98.87% |
| Miu group-0002 | 0.898% | 99.06% |
| Miu group-0006 | 0.612% | 97.99% |
| Oakley group-0004 | 1.016% | 98.94% |

The reported skin-normal disagreement is between approximately 1.4 and 5.5 degrees; depth gaps are approximately 2.9–4.4% of width. These pass the fixed 7.5-degree and 6%-width guards. See `data/smooth-optical-geometry/cached-five-groups-v2.json` for complete fits, source hashes and rejected simpler alternatives. These are geometric consistency measurements, not product-accuracy scores.

The actual AR harness rendered an unchanged-material Oakley before/after control. The smooth field produces coherent curved highlights across the declared optical surfaces. Opaque purple and gray cutout fragments remain, identifying optical membership/topology as a separate defect. Both assets pass the runtime contract. The surface-only control is under `data/smooth-optical-geometry/oakley-v4`; its numeric skin fit is the same as the final policy, while its overlap provenance predates the triangle-union metric. An earlier single-surface control remains under `oakley-v3` and must not be quoted as the final algorithm's fit metrics.

The final source also produced `data/smooth-optical-geometry/vb-v2`, with both lens groups changed and both control assets runtime compatible. Visual inspection shows coherent highlight shapes and reduced local shading discontinuity, while brown gradient transmission remains. The frames, existing generated texture artifacts and source contours are unchanged. Highlight movement is expected from different normals; no claim is made that the new highlights duplicate the product studio setup.

`qa/smooth_optical_geometry.py` reproduces a paired control from a pinned preparation, GLB and export receipt. It preserves the supplied material coefficients to isolate the normal change and optionally renders the actual AR cards. Its results are never labeled a material refit or accepted model.

Fourteen focused tests cover known planar/quadratic fields, unrelated shading normals, thin and thick shells, incompatible skin curvature, distant layers, inadequate support, non-graph surfaces, differing tessellation, input immutability, unsupported fallback and verified GLB roundtrips with exact triangles.

## Geometry work that is still missing

1. `structured_refinement.propose_part_bindings` still requires supplied source-face roles and hinges. An automatic grounding stage must return multiple supported face/hinge hypotheses, not infer identity from one component name or nearest isolated fragment.
2. The production refinement and ray consumers still use rigid geometry. `fit_front_cameras`, `fit_temple_poses`, and `pose_scene` need a shared view-state contract propagated through component projection, region priors, composition and optical observations. Otherwise one photograph's arm articulation can contaminate camera or frame-shape fitting.
3. The cage and this smooth-normal stage cannot create missing bridges, divide fused frame/lens geometry, correct connectivity or independently recover unseen depth. Construction hypotheses need photo-supported lens loops and connected bridge/rim/temple curves, with multi-view visibility and uncertainty. A smooth generator approximation alone is insufficient evidence to replace source positions.
4. Geometric acceptance needs independent per-part contour observations or held-out viewpoints. Current refinement matches candidate-guided edges on fitted views; this stage's spatial holdout measures only the representability of the generated surface.

The productive distinction is between improving an existing interface's response and proving the correct object geometry. This implementation does the former with bounded, inspectable evidence.
