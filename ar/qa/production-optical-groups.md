# Production effective-group conformance

This local harness exercises the actual `installCanonicalLensMaterials`,
`CanonicalLensLayers`, `NearestOpticalGroups`, and `EyewearShadow` implementation.
It constructs explicit synthetic group metadata and independent box recipes.
It does not exercise GLTFLoader, TryOnRenderer face fitting, hair segmentation,
camera capture, or photographic material identification. Those require separate
evidence. No asset or material is accepted by this harness.

Run from `ar/`, always with a new output directory:

```powershell
node qa/production-optical-groups.mjs --output=qa/output/production-optical-groups-new-run
```

The runner pins source hashes before and after execution, records the device,
browser warnings and errors, and saves a contact sheet and JSON report before
asserting success. A failed numerical check returns a nonzero exit code and
preserves the report. It uses local Vite and Playwright; no provider API or camera
is used.

## Fixed coverage and reference

Thirteen recipes cover closed and multipart groups, two distinct groups sharing
the same original material, gradients, a colored angular-reflectance table,
total mirrors, asymmetric rear reflection with reciprocal transmission,
reversed winding, four groups, and opaque geometry before,
inside, or between groups. Front, tilted, and back poses run through perspective
and orthographic camera transport and the actual directional shadow caster.
Black-environment controls isolate transmission. A fifth group must explicitly
fail in both the camera and shadow paths.

The reference derives triangles and slab intersections from the recipes rather
than renderer attributes or GPU depth. It reports continuous slab diagnostics
alongside fixed float32 shader transforms and direct viewport-to-n.8 nearest-even
conversion. Each run qualifies that conversion with independent exact clip
triangles. This arithmetic model is device-qualified; it is not a universal
proof of compiler arithmetic. The predeclared geometry-edge exclusion is 0.035
source units. No sample is excluded based on its measured error.

Receiver controls substitute known planes into the actual receiver and restore
the borrowed geometry afterward. Each of 81 screen pixels uses 20 independent
recipe-derived shadow texel comparisons with the existing contact and falloff
policy. Controls verify no event before a group, one event inside or behind a
closed group, one then two events between and behind distinct groups, and the
intervening opaque stop. The source image is uniform white. The sRGB8 output is
decoded before comparison.

## Qualified viewport reference and current result

The current harness passes all 80 camera, 39 shadow, nine receiver-plane and
two overflow cases at the original tolerances. Maximum linear RGB error is
5.457e-7, maximum normalized depth error is 1.122e-7, and maximum decoded sRGB8
receiver error is 0.003660. Source-pinned evidence is retained in
`qa/output/production-optical-groups-viewport-fixed-v2/report.json`.

The prior failure below was localized to an extra rounding operation in the
reference viewport conversion. Rounding `(clipX / W + 1)` to float32 before
scaling moved a vertex just below an n.8 midpoint onto that midpoint. This
changed the nearest-even snapped coordinate by one subpixel unit. Direct
viewport-to-fixed conversion predicts the previously failing depth as
0.39626202354216317, versus GPU 0.3962620198726654 (3.670e-9 difference).

This correction was independently checked before changing the shared oracle.
`viewport-subpixel-probe.mjs` rasterizes 150 predeclared triangles with exact
float32 clip inputs around subpixel ties, both coordinate signs/axes, and three
W scales. It contains no production matrix arithmetic or renderer geometry.
The direct model's maximum RGBA error is 7.731e-8; the intermediate-rounded
model's error is 1.936e-4, with 48 cases having different snapped coordinates.
Every full conformance run repeats this qualification and requires the same
backend as the production renderer. The independent diagnostic is saved in
`qa/output/production-optical-groups-viewport-probe-v1/report.json`.

The D3D11 specification requires interpolation from n.8 snapped coordinates;
the probe qualifies the conversion arithmetic on the recorded Intel/ANGLE
device rather than claiming that every device uses one expression graph.
See [D3D11 coordinate snapping](https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#3.4.1%20Coordinate%20Snapping).
No production renderer change, tolerance increase, sample exclusion, recipe
removal, or per-pixel choice between references was used for this correction.

## Historical result with the intermediate-rounded viewport reference

`qa/output/production-optical-groups-final-v1/report.json` is **failed** on Intel
Arc 140T / ANGLE D3D11, with stable production and harness source hashes:

| Check | Coverage | Maximum error | Fixed tolerance | Result |
| --- | --- | --- | --- | --- |
| Camera color, including final material draw | 68 cases / 29,523 pixels | 5.457e-7 | 5e-5 linear RGB | Pass |
| Camera nearest depth | Same camera samples | 1.122e-7 | 2e-6 normalized depth | Pass |
| Shadow transmission | 33 cases / 23,463 rays × four layers | 9.421e-6 | 5e-5 linear RGB | Pass |
| Shadow depth | Same shadow samples | 4.104e-6 | 2e-6 normalized depth | **Fail** |
| Actual receiver | Nine plane conditions / 81 pixels | 0.003660 | 0.008 decoded linear RGB | Pass |
| Fifth-group overflow | Camera and shadow | Both explicitly reject | Required | Pass |

The sole failing case is `multipart_same_group`, yaw 37 degrees and roll 21
degrees. At light texel (336, 385), layer zero, measured depth is
0.3962620198726654 and the fixed raster reference is 0.39625791642150004.
The maximum residual is exactly repeated in the preserved initial full run,
the arithmetic diagnostic, and the final expanded run. No production change,
threshold relaxation, or case removal was made. Camera transport and receiver
checks do not fail. There are no browser or shader errors; PMREM compiler
precision warnings are retained in the report.

The separate command below executes two predeclared GLSL transform-feedback
expressions on an independent context, without choosing an oracle per pixel:

```powershell
node qa/production-optical-groups.mjs --mode=arithmetic --output=qa/output/production-optical-groups-new-arithmetic
```

The preserved `production-optical-groups-arithmetic-v1` diagnostic found at most
5.97e-8 clip-coordinate difference between left-associated `P*M*v` and explicit
sequential matrix-vector evaluation, with no different n.8 snapped XY positions
among the 48 recipe vertices. That separate program does not establish the
compiler arithmetic of the complete production shader and does not explain or
resolve the failing shadow-depth check. Its clip-space expressions stopped
before the viewport conversion, which the later independent raster probe
identified as the cause. The historical failed reports remain unchanged.
