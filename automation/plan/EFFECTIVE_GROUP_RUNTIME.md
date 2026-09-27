# Effective optical groups in the AR renderer

The local AR implementation accepts the explicitly declared
`effective_optical_group_v1_experiment` profile. It preserves closed and multipart
lens geometry and applies one effective response per group per ray. This is an
optical representation and transport implementation, not automatic lens grouping
or evidence that a reconstructed product has the right appearance.

## Contract

The importer validates actual float32 positions, normals, UVs and indexed
triangles, explicit group/member/source bindings, independent material descriptors,
and common baked coordinates. It rejects triangles whose corner-normal convex
hull contains zero, preventing an undefined normalized interpolant. This global
normal check is also enforced by the Python runtime-contract wrapper added during
[job adoption](GROUP_JOB_ADOPTION.md); an export receipt alone is not an AR guarantee.
One common bottom-to-top height mapping must be
compatible with independently rounded positions and UVs across the whole group.
Exact coplanar checks reject conflicting overlaps between groups and accept
same-group coincidences only when sufficient agreement of V and symmetric normal
fields is proved. Unsupported cases and bounded-check exhaustion throw; a checked
prefix cannot establish support. Hash-shaped node metadata is a consistency
declaration; it does not authenticate a source or replace the pinned GLB receipt.

Each group owns a material and nearest-depth texture, even if another group's
descriptor or original material is identical. Members of the same group share
the group's material state. Camera capture borrows the exact geometry, material
and vertex program used by the optical peels. Light capture likewise uses the
actual shadow caster. Capture samplers are cleared to avoid framebuffer feedback.

The camera selects one nearest event per group, then composes distinct groups
from far to near over the nearest opaque surface. The light selects one nearest
event per group, then peels groups near to far. A face receiver applies only
events before its own depth. Both consumers support four visible events and
explicitly reject a fifth. Empty group-map pixels have alpha 1; valid captured
depth is stored in float alpha and compared exactly during peeling.

Closed rear faces are retained but do not contribute a second pass through the
same group. Back views use the nearest back surface with symmetric incidence.
Normals are normalized at vertices and after interpolation. The group path uses
the shared Taylor24 incidence function whose tested GPU error was substantially
below the device's built-in `acos` error. The authored front-sheet path keeps its
prior incidence implementation.

## Ownership and compatibility

Targets belong to the camera/shadow helpers; source geometry and textures remain
owned by the parent renderer. Visibility includes parents, material visibility
and camera layers. Capture restores render state and material flags after errors.
Malformed import data is rejected before source materials are replaced. Import
creates separate mutable materials for distinct groups while allowing members
with equal descriptors to share one group state.

The existing `front_sheet_v1` and ordinary legacy asset paths remain available.
Mixed front-sheet/group profiles, mixed canonical/legacy optical interfaces and
transparent noncanonical frame materials are rejected for canonical imports.
The runtime does not infer a group from a material name or turn an ordinary
closed legacy lens into a canonical group.

## Limits

- This is one symmetric effective interaction, not volume optics: no refraction,
  internal reflections, thickness transport or distinct front/back coatings.
  Density may vary with lens height and reflection with angle; a spatially
  varying reflective coating `R(height, angle)` is not represented by this schema.
- Current import uses whole baked source parts. Source grouping remains an
  unverified hypothesis, including the known Miu silicone/main-part ambiguity.
- Exact geometric coincidence checks do not prove absence of finite GPU depth
  ties, noncoplanar intersection-line ambiguity or every raster boundary case.
  Geometry at the camera's exact near/far clip boundaries is not qualified.
- There are at most eight groups and four interactions per ray. Import has
  explicit triangle, member, integer-size and work budgets. Exceeding a budget
  is unsupported, not successful validation.
- Additional nearest maps cost approximately
  `20 * groups * (cameraWidth * cameraHeight + 512 * 512)` bytes, excluding driver
  overhead and the existing compositor. Two groups at 1280x720 require about
  45.2 MiB extra. Mobile memory, GPU timing and device portability remain unmeasured.
- Runtime compatibility and neutral-control previews do not measure photographic
  tint, gradient, mirror strength, geometry fidelity or AR fit on real wearers.

## Reproduction

The private corpus manifest binds five exported GLBs and their exporter receipts.
From `ar/`:

```powershell
node qa/prepared-optical-groups.mjs --manifest=../automation/data/effective-group-runtime-integration-v1/manifest.json --output=qa/output/prepared-optical-groups-new
npm test
npm run build
```

The prepared-asset harness uses actual `TryOnRenderer` loading, fitting, camera
composition and shadows at three synthetic poses. It verifies each optical
member's attributes and descriptor against its export receipt and checks group
material ownership. The controls use clear neutral descriptors and keep
`accepted=false`, `quality_verdict=unmeasured`.

Nothing is published by these commands.

## Retained import and regression evidence

All five saved exported neutral-control GLBs passed actual loading, fitting and
three-pose rendering with exact optical attribute and descriptor preservation.
The final report is `ar/qa/output/prepared-optical-groups-frozen-v1/report.json`,
SHA `08ef2b9f0b3aa624d64ebee63ac31f0b8d91f73b53aa5ec825b0f561d7376d05`.
Source hashes remained stable. There were no browser/shader errors or failed HTTP
responses; the existing PMREM precision warning was retained. The measured device
was Chromium 153 / ANGLE D3D11 on an Intel Arc 140T GPU. The fifteen previews use
neutral optics and synthetic fitting, not recovered product colors or wearers.

Final topology receipt:
`data/optical-group-runtime-topology-v3/report.json`, SHA
`6a6faf963e08d19125de0b49950735fccf20513cdc8cc5babf50f8297d5017fa`.
All eight declared optical parts / 257,498 triangles passed the stricter importer.
The independent 1,200-normal-field review retained its seed, all inputs, closest
point results and source hashes under
`data/effective-group-runtime-integration-v1/normal-hull-review/`; report SHA
`8bfbba1b3a32af7cd52cdceb4f3ea5eb14a38de733dc4ae08ac1b79a1fad725c`.

The final software suite passed **414 tests**. TypeScript, pinned-asset checks
and the build passed, retaining the existing bundle-size warning. Logs are under
`data/effective-group-runtime-integration-v1/`.

The existing front-sheet numerical harness passed its original thirteen response
cases, thirteen layered cases, structured reflections, rejection controls and
opaque display identity check. Its report is
`ar/qa/output/canonical-lens-runtime-after-effective-groups-final-v1/report.json`,
SHA `d1e0f67a09ecbcde32594c07ab9d26696dd87237b7a67c6d040a9606631f106d`.
All recorded numerical results, coverage and controls equal the earlier baseline
exactly after excluding only runtime timing fields. The separate comparison
receipt binds both reports at
`data/effective-group-runtime-integration-v1/front-sheet-comparison.json`.

The ordinary app smoke passed all ten checks for both shipped models, desktop
and mobile layouts, restart and all four held-frame protections. Screenshots
were inspected and the owned browser/preview closed. Report:
`ar/qa/output/effective-group-legacy-smoke-final-v1/report.json`, SHA
`9bf98d2ddc4ca4acde183591ec60d35db81881edaa4259158c69b72a2c16e560`.
The mobile layout ran on a desktop GPU and does not qualify phone performance.
Completion source/log pins are in
`data/effective-group-runtime-integration-v1/final-regression-receipt.json`.

The new group path's strict numerical GPU qualification remains **incomplete**.
Its failure and final controlled camera/light/receiver measurements are recorded
separately below; compatibility and regression results must not override that gate.

## Controlled production GPU result: one unresolved depth failure

`qa/production-optical-groups.mjs` exercises the actual installer, material,
nearest-group maps, camera compositor, shadow caster and receiver. Eleven fixed
recipes cover closed lenses, multiple members, equal descriptors in distinct
groups, reversed winding, front/back views, colored angular reflection with
gradients, total mirrors, opaque stops and four-group composition. Expected
geometry comes from analytic box recipes, with an independent device-qualified
float32 raster reference. It does not read rendered attributes or pixels to
construct the expected intersections.

| Check | Coverage | Maximum error | Fixed tolerance | Result |
|---|---:|---:|---:|---|
| Camera composition and display, linear RGB | 68 cases / 29,523 pixels | `5.456344e-7` | `5e-5` | Pass |
| Camera nearest-group depth | Same 68 cases | `1.121318e-7` | `2e-6` | Pass |
| Light transmission, linear RGB | 33 cases / 23,463 rays / four peels | `9.420098e-6` | `5e-5` | Pass |
| Light depth | Same 33 cases | `4.103452e-6` | `2e-6` | **Fail** |
| Actual receiver, sRGB8 decoded to linear | 81 before/inside/between/behind pixels | `0.003659066` | `0.008` | Pass |

The sole strict failure is the multipart group's light-depth comparison at yaw
37 degrees / roll 21 degrees, worst pixel `(336,385)`, layer 0. The same result
repeats in the initial run, diagnostic replay and final run. The camera and
transmission comparisons pass; that does not excuse the depth discrepancy.
Fixed transform-feedback probes found no changed n.8 XY snaps between the two
tested shader multiplication orders across 48 fixture vertices, so multiplication
association alone has **not** explained the failure. No production shader change,
threshold relaxation or sample removal was made to turn this into a pass.

Receiver controls retain the expected zero/one/two distinct-group event counts;
a closed group remains one event, and intervening opaque geometry blocks later
events. Both camera and light explicitly reject a fifth group. Complete declared
coverage and negative controls passed. These controlled receivers do not measure
real face geometry, hair or wearer appearance.
Independent review found no concrete receiver false-pass path: expected values
use the recipe oracle, required event counts are asserted, and missing coverage,
NaNs, no-op rendering or exceeded tolerances cannot receive a passing result.

Final report: `ar/qa/output/production-optical-groups-final-v1/report.json`, SHA
`8c07379703429a5c983930a476827714d40e00ad0dc0317ecfa02d9606dfbc89`,
overall status **failed**, with stable production source pins, no browser/shader
errors and the retained PMREM precision warning. The original failure and raw
arithmetic diagnostic remain under `production-optical-groups-first/` and
`production-optical-groups-arithmetic-v1/`. The new group path is experimental;
strict numerical qualification has not been granted.

The next independent integration step is specified in
[GROUP_JOB_ADOPTION.md](GROUP_JOB_ADOPTION.md): align the offline/runtime contracts,
replace the failed front-envelope preparation under an explicit profile, and
reuse the existing resumable joint fitter. None of that resolves semantic lens
partitioning, photographed articulation or color/lighting ambiguity by itself.
