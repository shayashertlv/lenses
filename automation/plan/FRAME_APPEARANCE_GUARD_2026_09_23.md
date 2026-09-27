# Frame image membership and surface-area coverage

The frame stage now requires independent photographic support before it samples
a texture correction or counts observed surface area. It preserves unsupported
source texture. This fixes backdrop sampling and a mesh-density-dependent
coverage estimator; it does not establish accurate intrinsic frame materials.

## Implemented behavior

`reconstruction/frame_image_support.py::build_frame_image_support` reads the
pinned native photograph and saved image-only `contrast-object` region. It
recomputes the source contrast and five prompts, verifies the prior and all
15 original SAM mask hashes, then requires agreement across every alternative
and strong source contrast. Empty, excessive, border-touching or disagreeing
alternatives are not selected away. Projected candidate optical masks may only
subtract support. No model confidence, candidate projection or contrast pixel
alone supplies positive membership.

The native-grid membership classes are unknown, corroborated foreground and
authored-alpha exterior. Authored alpha can support an opaque white image patch;
partial-alpha pixels and hidden RGB are excluded. A white pattern in an opaque
JPEG may remain unknown: its original texture stays unchanged. These classes
do not certify semantic frame identity or physical opacity.

`intrinsic_frame_appearance._observe_tracks` applies membership before exposure
estimation and highlight separation. Track NPZ files now include geometric
eligibility, image membership and source alpha. The report saves per-photo
support masks and exclusions. The public `run_intrinsic_frame_stage` call is
unchanged and uses existing saved evidence; no extra inference/API call occurs.

## Coverage correction

The original estimator required samples near every corner and center of 16
subcells in each source triangle. This became an unintended triangle-density
test. A fixed plane with the same 4,225 observed world points scored 100% with
2/8 triangles, 56.05% with 32 triangles and 0% with 128 or more triangles.
The reproducer is `qa/frame_coverage_retessellation.py` and its saved result is
`data/build-five/frame-coverage-retessellation.json`.

Production now calls `frame_surface_coverage.sample_frame_surface` and
`assess_sampled_frame_surface_coverage`. It takes 32,768 reproducibly seeded,
equal-area strata over the complete non-optical surface, spatially orders source
triangles, and samples barycentric world points independently of UV texel
density. These points use the same posed cameras, first-hit visibility, authored
material/UV eligibility and guarded image membership as the frame observations.
Missing UV, untextured and unsupported surfaces remain in the area denominator.

The unchanged three-view/8-degree rule determines observed samples. The report
records the raw area estimate and deducts a one-sided Hoeffding sampling
allowance under the seeded independent-strata sampling model. At the default
budget that allowance is 0.00838268 of total frame area. This numerical allowance
does not bound systematic camera/segmentation error and is not an exact surface
visibility certificate. The acceptance threshold remains 0.60.

`frame-surface-coverage.npz` preserves sampled face IDs, barycentrics, world
points, area weights, authored eligibility, guarded observations, rest-frame
view directions and membership/alpha. Its hash is in the frame report. Sample
recipes are recomputed and checked; a caller cannot select only easy surface
points and retain the same denominator claim.

## Real five-view Oakley replay

The replay used the sealed selected semantic GLB/export and hypothesis-region
report from `data/build-five/oakley-all-views-job-v2`. The new output is
`data/build-five/oakley-frame-guarded-v2/report.json`.

| Measurement | Result |
| --- | ---: |
| Complete non-optical triangles | 576,519 |
| Texture tracks | 81,320 |
| Guarded tracks with three-view support | 344 |
| Independent area samples | 32,768 |
| Area samples with three angularly distinct supported views | 162 |
| Raw estimated observed area fraction | 0.00494385 (0.4944%) |
| Conservative observed fraction used by the gate | 0.0 |
| Corrected texture pixels | 0 |

The selected `baseline.glb` is byte-identical to the sealed input, SHA-256
`a95a8619b9a8f12039efbb049e32ff9e551b0a23264b7f07463cc0767e72b308`.
No visual change or new render is claimed. The fixed estimator exposes nonzero
measured support, but this product's guarded evidence is still far below the
required coverage. It does not justify automatic frame relighting.

## Validation

Thirty-three frame tests pass: existing articulation/texture/export checks,
complete-area checks and the new guards. Controls include a misregistered model
over white backdrop, a dark background line outside object-mask consensus,
white-pattern preservation, translucent/hidden alpha, mask/prompt mutation,
unsupported byte-identical output, missing UV/untextured denominator retention,
full/half-observed surface retessellation, insufficient angular diversity and
tampered sampling recipes.

Run `python -m unittest discover -s tests -p "test_*frame*.py" -q`.
