# Source-preserving optical groups: experimental decision

The maximum-Z surface conversion is frozen as an unsuccessful general preparation
path. We are evaluating a second representation that preserves the source lens
geometry. It is not yet a production rendering profile, a replacement for
`front_sheet_v1`, or evidence of correct lens identity.

## Why the representation needs to change

The final fixed eight-part audit of `optical_surface.py` rejected seven meaningful
lens candidates during marginal-cell patch assignment. Only a four-face silicone
fragment incorrectly tagged as a lens prepared. No complete glasses model prepared.
This regressed from an earlier implementation whose two Victoria Beckham surfaces
passed independent geometry and actual AR runtime checks. The old implementation,
arrays and failed experiments are retained; its success does not certify current
code. The final audit is under `data/optical-surface-envelope-final-v1/`, with source
SHA-256 `855cdd1c827cd54d1d41b7bc8c6914c5a0b7dfa7d2b22d05bcaea68ee674fe8f`.

Flattening an arbitrary generated lens into a single-valued front sheet introduces
new contour, hole, depth-discontinuity, normal and numerical-topology obligations.
Those changes are not inherently necessary for the target effective AR response.
Changing topology tolerances until each saved product passes would not establish a
general solution. The new experiment removes this conversion from the hypothesis.

## Proposed contract

An **optical group** denotes one physical optical element, with one effective
material and one shared intrinsic coordinate frame. The caller supplies its
membership explicitly. A group may span several source primitives or disconnected
pieces. Left and right lenses remain different groups even when their material is
shared; two actual lenses overlapping along a ray must not become one group.
Generated part names, connected components and transparency are insufficient to
verify that membership.

For each camera or light ray:

1. Find the nearest intersection of each group, using both triangle sides.
2. Retain only group events before the nearest opaque surface or receiver.
3. Order the surviving groups by depth.
4. Apply the canonical effective response once per group. Camera composition is
   `R * environment + T * behind`; light transmission is the product of the groups'
   transmission before the receiver.

The GPU hypothesis uses a nearest-depth target per group, then filters optical
draws against that exact depth. Simply discarding repeated group IDs from the
existing far-to-near peels would incorrectly retain a far/back interface.

The whole effective response occurs at the entry hit, including when a receiver
lies inside a closed mesh. This explicitly assumes a symmetric effective optical
response: face-forward normals and absolute incidence support that assumption.
It does not model thickness-dependent absorption, refraction, internal reflection,
separate back coatings, or partial travel through a volume.

Coordinates are derived once across all referenced vertices of the group in a
declared common coordinate frame. Per-primitive bounds must not restart a gradient.
Source positions and connectivity remain intact; valid supplied normals are
preserved with their transformation provenance. Derived normals and height UVs
remain hypotheses. Float32 export and actual loaded-attribute checks remain separate.

## Cases that must not silently pass

- Coincident surfaces belonging to different groups have undefined ordering.
- Same-group coincident surfaces with conflicting normals or other optical
  attributes can produce different responses despite identical depth. A nearest
  depth filter alone does not resolve this; agreement or an explicit tie contract
  is required before asset acceptance.
- Incorrect semantic grouping can hide an actual stacked lens or treat two sides
  of one lens as two materials. Geometry preservation does not fix that identity.
- A fifth visible group exceeds the current four-event composition capacity. The
  total group budget and render-target memory budget are separate limits.
- Singular normals, unsupported transforms and unverified optical coordinates
  cannot turn into default clear lenses or silently accepted geometry.

## Implementation boundaries

`reconstruction/optical_groups.py` prepares explicit groups and reports source
bindings, coordinate/normal provenance and topology diagnostics. It does not alter
the existing GLB exporter or claim verified product semantics.

`reconstruction/optical_group_raster.py` is a CPU geometry experiment. It preserves
original source-face references, rasterizes one nearest hit per group, retains all
events for overflow diagnosis, and marks cross-group/stop depth ties. Its optional
single-group sample fields use the actual nearest UVs and normalized interpolated
normals; multiple groups and undefined attributes remain unknown. Same-group ties
currently follow a source-face order and remain explicitly unverified.

`ar/qa/effective-optical-groups.*` is an isolated GPU prototype. It must compare both
camera and light/receiver events with independent analytic rays, including closed
meshes, reversed winding, disconnected pieces, distinct overlapping groups, rear
views, opaque content inside a group, total mirrors and attribute tie controls.
It does not change the production renderer or relax `front_sheet_v1` validation.

Production adoption requires the GPU proof, an explicit asset contract and tie
validation, preserved source attributes through export/import, aligned photo and
runtime sampling, and practical target-memory measurements. A straightforward
RGBA32F plus float-depth target uses roughly 20 bytes per pixel per group before
other renderer allocations. Desktop tests cannot establish mobile viability.

## Measured experiment

The source-preserving preparation produced arrays for all eight diagnostic source
parts in about 1.08 seconds total, retaining their positions, triangle indices and
authored normals. This includes the known suspect silicone part and source
primitives that may contain more than one physical lens. It is representation
coverage, not eight verified lens identities. The pinned manifest is
`data/optical-groups-v1/manifest.json`.

The CPU projection experiment then exercised all ten saved photographs and all
51 optical mask hypotheses in about 1.15 seconds of raster/attribute work. Its
original-camera normalization and source arrays were checked before use. Conditional
coordinate coverage ranged from 34.46% to 99.24% of eroded mask pixels; poor masks
or source labels remain visible rather than being removed. No distinct-group stack
or depth-tie pixel occurred in these saved poses. This cannot prove their absence
at other poses or agreement at same-group coincident surfaces. The report is
`data/optical-group-projection-v2/report.json`, SHA-256
`ffdb54b59d33d1ccaee917ce4558c58abc0623a9d4aad005fbaec99341574007`.

The isolated GPU prototype exercised 84 camera cases and 28 light cases. Four
groups were retained and a fifth rejected on both paths; identical coincident
attributes were invariant to draw order. Conflicting normals at coincident
same-group surfaces changed linear RGB by 0.0813, confirming that importer
validation is necessary. A separate bounded exact-patch validator now checks
that concern; it has not been integrated into the prototype or production import.

The strict GPU experiment **failed** its unchanged targets of 5e-5 linear RGB and
2e-6 depth. Maximum camera errors were 8.2661e-5 RGB and 9.9261e-8 depth; maximum
light/receiver RGB error was 1.6886e-5 and light depth error 2.5615e-6. Captured
shader values attribute the camera residual to the canonical angular evaluation's
GPU `acos` precision. The remaining light-depth discrepancy is unresolved.
The maximum camera RGB residual corresponds to at most 0.273 eight-bit sRGB codes
under the encoding derivative bound; this is numerical characterization, not
appearance acceptance. No tolerance was increased.

The final report and source hashes are under
`ar/qa/output/effective-optical-groups-v4-diagnostic/`; reproduction instructions
are in `ar/qa/effective-optical-groups.md`. The synthetic desktop workload averaged
8.97 ms at 1280×720 plus a 512² light pass over ten measured iterations. Its float
targets require approximately 152.5 MB before driver padding, geometry and browser
framebuffers. Mobile viability and real-asset runtime performance remain unmeasured.

## Exact coincident-patch checks and actual source geometry

`optical_group_validation.py` uses exact dyadic plane predicates and rational
positive-area clipping on the supplied arrays. Same-group coincidences require
equal intrinsic-V fields and a sufficient normal-field agreement proof. Different
groups cannot share a positive-area coincident patch. Varying-normal overlaps
outside that proof and capacity exhaustion remain explicitly unsupported.
Coordinates are never welded or quantized to make a check pass. Noncoplanar
intersection lines and finite GPU depth ties are outside this exact-patch check.

All eight source groups, totaling 257,498 optical triangles, were checked at
original float64 precision and after explicit float32 conversion. No positive-area
exact coplanar overlap occurred. This does not certify those candidate group IDs
or exclude raster-depth ties at arbitrary views. The report is
`data/optical-group-ties-v1/report.json`, SHA
`0fe124b7833ea56f8ef1b678e56974fc0e870b3f02a41bbcc3a91f2f5a1ba403`.
Twelve counterexample tests cover conflicting fields, tiny overlap, precision
collapse and bounded incomplete checks, including broad-phase float-range overflow
under widened integer budgets. The retained corpus pins the earlier checker
source; that final non-default range guard has focused regression coverage and
was not retroactively substituted into the archived corpus receipt.

The new `qa/source_optical_groups.py` bundles the same groups and every remaining
source triangle as explicitly opaque control geometry. The browser harness
`ar/qa/source-optical-groups.mjs` reuses the unchanged experimental renderer through
a pinned test-only export bridge. It retains all 1,033,521 triangles from the five
original GLBs, quantifies float32 upload changes and uses neutral/gradient-mirror
control materials; these are not photo-recovered materials.

The final run completed thirty front, turned/rolled and back cases. Of 20,160
fixed-grid samples, 2,518 hit optics and 200 exercised opaque stops. Sampled support
agreed with the independent source-array oracle, with no invalid samples or depth
ties. Maximum depth error 1.14005e-6 passed the unchanged 2e-6 target. The strict
RGB result remains **failed**: four neutral cases exceeded 5e-5, maximum 6.73515e-5.
Captured shader angles explain the residual through the same GPU `acos` issue.
Evaluating the CPU material at the captured GPU angle agreed within 1.1e-7.

Source/code pins remained stable and there were no browser errors, warnings or
HTTP errors. The tiny Miu silicone group received no sampled hits and remains
unmeasured. Perspective and real-source light/receiver transport were not tested.
Timings were collected during concurrent CPU fitting and are not performance
benchmarks. Evidence: `ar/qa/output/source-optical-groups-v2/`, report SHA
`741d804b8c6fe599a2a630d610ce12ef7f95cbbabbc02d6edd9231ddc086b216`.

Any production implementation must key nearest-map and material state by group
identity, not by shared source material: two physical groups can reuse one source
material and still require separate depth maps. Current production profile,
importer and rendering tolerances remain unchanged.

## Isolated arithmetic correction, unchanged tolerances

A fixed 24-term asin-series evaluation of
`acos(c) = 2*asin(sqrt((1-c)/2))`, for `c` between zero and one, was derived before
testing. On 1,056,953 deterministic float32 inputs, the tested Intel Arc/D3D11
backend's maximum angle error decreased from 6.760756e-5 to 2.272890e-7 radians.
The exact-arithmetic truncation bound is 3.943920e-10 radians; floating-point
evaluation dominates the measured error. This is neither an all-GPU bound nor
proof about arbitrary shader composition. Raw inputs/outputs and coefficients
are retained under `ar/qa/output/acos-precision-v1/`.

The same coefficients were then inserted solely through a pinned test-only shader
override. All thirty real-source cases passed the original targets: maximum RGB
error 1.708509e-5 and depth error 1.140051e-6. Depth/hit-coverage records were
identical to the builtin run, and displayed PNGs differed by at most one code.
The report is `ar/qa/output/source-optical-groups-taylor24-v2/report.json`, SHA
`7a720c45913c9171c2e711c5bc564cbb31bf7afce9fafd254430aac7d9978447`.

The unchanged 84 synthetic camera and 28 light cases also passed color checks
with that override: maximum camera RGB 2.984049e-7, light/receiver RGB 7.973685e-6.
The overall synthetic result still **fails** because light depth remains
2.561534e-6 against 2e-6, exactly unchanged. Its report is
`ar/qa/output/effective-optical-groups-taylor24-v1/report.json`, SHA
`d74ad1817fc9121447f637e4fbbdb0a64b6ffcfc4a0a746a887e1db2ca98e65c`.
No coefficients or thresholds were retuned, and no production files or original
synthetic harness files changed. General renderer adoption remains separate.

The remaining depth discrepancy was subsequently traced to the CPU reference's
double-precision vertex/viewport arithmetic near an n.8 raster snap boundary.
GPU nearest-group and peel depths already agreed. A separate fixed float32
multiply/add and viewport model, computed from analytic box corners and uploaded
matrices without GPU samples, was applied through a pinned QA-only override.
All **84 camera and 28 light cases** then passed the original thresholds: maximum
camera RGB/depth errors `1.797673e-7 / 1.152693e-7`, light/receiver RGB error
`7.096211e-6` and light depth error `5.543148e-8`. The same 36,470 camera, 47,804
light-peel and 35,851 receiver samples, poses, exclusions, negative controls and
continuous-reference diagnostics remain in the comparison.

There were 27 differing vertex snaps in 5,760 visits compared with the old
double reference; a separately declared fused-like alternative produced the
same snaps as the float32 reference in this suite. The arithmetic model does not
exactly reproduce every captured shader component and is not a portable GPU
specification or a universal error bound. This is device-qualified conformance,
not retrospective alteration of the earlier failed report. No production code
changed. The report is
`ar/qa/output/effective-optical-groups-taylor24-float32-v1/report.json`, SHA
`5464b8b7acc24fe1df96dfcc4144f13d439c821dff5878b7e69aacb5e6bb3000`;
`comparison.json` beside it binds the original and diagnostic reports.

## Material fitting is a separate unresolved problem

The frozen Victoria Beckham fit produced no candidate satisfying its photo policy.
Replaying the saved gradient candidate attributes the largest angled errors to
rim/rear-content boundaries. Front mask alternatives include or exclude a folded
temple that the candidate's rear-geometry projection frequently misses. A smaller
midheight residual is shared by both views. These observations suggest composition,
articulation and material-coordinate issues; they do not uniquely identify one cause.

The fixed spatial validation split missed every severe angled outlier in that
diagnostic. A low validation mean alone is therefore insufficient. The reusable
diagnostic must expose train/validation support across intrinsic height and rear
composition categories without changing masks, resplitting after observing errors,
or changing the fitting threshold.

`photo_lens_diagnostics.py` now performs that frozen replay. All 1,080 candidate
descriptors and their objectives across both VB groups reproduced, with maximum
measurement difference below 5.7e-14. There were 31 distinct photo/hypothesis/support
cells without validation for part 7 and 18 for part 8. Counts are conditional
coverage diagnostics, not independent failures or a new acceptance threshold.

The two existing gradient representatives use incompatible nuisance explanations
for the same photographs. At the same angled-view reflection direction, their
blue environment radiances are about 2.469 and 0.1345; their front-photo color gains
also differ. Their top blue transmissions differ by over two orders of magnitude.
Both supplied height coordinates increase upward, so a simple vertical UV reversal
does not explain the disagreement. Different rear-content support and incidence
remain confounders. This motivates joint per-photo lighting/calibration across
groups; it does not establish asymmetric manufactured lenses or justify forcing
both lens descriptors equal.

After representation, the next inference decisions are per-photo articulation and
composition support, lighting shared across optical groups in the same photo, and
material-family coverage. The current material separates vertical absorption from
angular reflection; it does not express a spatially varying mirror coating. Strong
mirrors also depend on reflected environment and surface normals, while roughness
remains an explicit prior in the current product-photo fitter. These are distinct
unknowns, not reasons to replace all of them with sampled photograph RGB.

The goal remains repeatable reconstruction from product photos. Runtime conformity,
software tests and fitting a saved development product each answer narrower
questions. Automatic acceptance still requires independent product/view evidence
and a declared policy for insufficient or contradictory inputs.
