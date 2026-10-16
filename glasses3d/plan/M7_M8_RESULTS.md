# M7/M8 implementation and copy proofs

Status: implementation, copy proofs and independent instruction reviews are complete.
Implementation commit: **d39f954**. The first fresh desktop test on
`prada-fresh-desktop-m7-m8-151f4802` found a facade-registration gap and was stopped before
geometry changed. The general fix, independent review and final assembly proof are complete. A fresh
full restart follows the fix commit.
These copies are not owner-accepted models. Visual limits and remaining geometric
creases are disclosed below; no delivered model was edited.

The starting branch is `glasses3d`, HEAD `7b9cd13`. All proofs use disposable copies;
no delivered model is edited. API calls, API spend and estimate requests: **zero**.
The question **"any other mesh editing tools astra might need?"** is recorded verbatim
in ASSEMBLY M8 and PROGRESS. The original four-defect request remains verbatim in
ASSEMBLY M7 and PROGRESS.

## Diagnosis of the delivered c0015

Source: Prada run 4, `20261016T092442-0c5459`, c0015. Delivered GLB SHA-256:
`0a5f32222ccd2e105671cbcdc8c36ceee8cb29f4759e80775cf25c2e23244b4d`.
The original photo-first room/side-light evidence is under
`data/assembly/m7/before-lights-v1`; measurements and saved-call replay are under
`data/assembly/m7/diagnosis`.

| Defect | Measured cause and producer |
|---|---|
| Vertical ridge inside the logo triangle | Run 4 request **63** applied a piecewise thickness multiplier along the arm. Its knot at depth 24 mm lies near the logo triangle's bottom corner and introduces a **9.664°** geometric slope break. Retained core vertices have maximum plane residual **1.369 mm**. The deformation also retains stale custom normals. It is not run 3 request 70's smooth-shading edit or a GLB exporter seam. |
| Temple facets are not planes | The same request 63 changes thickness and the arm's base offset along its length, bending previously fitted faces. Corresponding core-vertex residual maxima are front **0.207**, logo **1.369**, rear **0.618**, upper **2.652 mm**. The original upper face was explicitly declared a warped four-corner face. Current native footprint measurements include slightly different boundary samples: left maxima **0.200201 / 1.374582 / 0.622329 / 2.686606 mm**; right upper **2.70422 mm**. |
| Line at the front's upper outer corners | Initial request **9** introduced a provisional corner cut. Request **73** replaced it with a bisect along `side*x + 1.4*y = 82`, lowered the outside by `0.5*max(0, side*x + 1.4*y - 82)`, and marked edges sharp. Fourteen principal cut edges have geometric breaks **38.51–38.86°** and split shading normals about **38.5–43.2°**. This is authored geometry plus sharp shading, not export damage. |
| Ridged lens highlights | Request **63** rebuilt each lens with **6 rings × 80 outline samples**, 962 vertices and 1,920 triangles overall; the front has 880 triangles. Front edge lengths are median **2.9437**, 95th percentile **4.5509**, maximum **5.7568 mm**. Interior corner normals are continuous; the 160 sharp rim edges are intentional. The source front's geometric normal changes reach **4.6929°**. Coarse curved geometry and normal interpolation create the reflection stepping; no lens degenerate triangle was found. |

The plan's historical **464 zero-area temple triangles** are not a current c0015
count. Each delivered temple has one triangle below `1e-9 mm²`; 42 left and 36 right
are below `1e-6 mm²`. Native builder replay isolates four exactly zero-area Blender
ngon tessellation ears per temple, although the builder's own triangles have positive
area. Later raw edits also introduce small bevel slivers. The builder now uses its
canonical triangles for the affected polygons and retains aliases, UVs and normals.

The general temple rebuild also exposed an inner-print transfer bug: transfer always
used the outer chart. The fix chooses the chart from the original surface normal,
preserves stand-off and rejects an unsupported edge-facing chart. On the corrected
copy, inner lettering stays inside with movement below **0.000047 mm**.

## Implementation and edit contract

All new operations use the existing source-bound owner workflow: a short map label
and plain legend, real scratch previews of two or three distinct amounts, an explicit
owner-selected option, a scoped operation receipt, surface-following markings and
post-write verification. A scripted copy proof is not an owner confirmation.
Closed-solid, orientation, minimum-thickness and strict-spec checks remain in force.

| Operation | Scope and material limits |
|---|---|
| `flatten_face` | Fits the selected complete faces to a plane. Fixed borders must already lie on that plane; a conflicting border is refused. Explicit border movement includes the shown neighbouring influence and transports those normals. |
| `repair_shading` | Repairs selected loop normals in flat or smooth mode; vertex positions stay exact. |
| `refine_curved_surface` | Uses local cubic tangent interpolation and four-child triangle subdivision inside selected faces; new normals use shared quadratic endpoint/chord controls. Original points and outline stay exact by default. The normal field is continuous across shared edges, without a derivative-continuity promise. Boundary/ngon fans retain conformity; UVs interpolate per source face and preserve seams. Optional interior relaxation needs a supported fitted chart. Native thickness and marking checks still apply. |
| `material_adjust` | Selected material tint, alpha, roughness, transmission, metallic, coating and IOR. Shared materials are copied to isolate the target; linked inputs and unsupported per-object material-slot overrides are refused. |
| `edge_shape` | A true circular fillet on a selected straight convex edge with planar adjoining faces and selected end caps. Radius changes stay inside that selection. Sharpening reduces or removes a previously authored fillet with an unchanged stored recipe; arbitrary rounded topology is not guessed. |
| `thickness` | Offsets the selected side along its surface normals; the opposite side stays fixed. Generic thinning requires a declared positive minimum, and cannot lower an existing temple minimum. |
| `scale_region` | Scales selected vertices about a stated anchor, with the shown weights controlling transition to unchanged joins. |
| `bend_region` | A bend about a stated axis with quadratic falloff along a perpendicular direction; supported angles remain below 90°. |
| `transform_marking` | Moves, scales or rotates a whole marking in its surface chart, then projects it with its stand-off and verifies clearance. |
| `mesh_cleanup` | Removes selected exact duplicates and zero-area faces. It is not an approximate distance weld. |
| `consistent_normals` | Orients a selected complete closed shell consistently and corrects backward shading normals while retaining valid smooth/custom fields. |
| `symmetrize` | Explicit source/destination vertex pairs mirrored about world X=0; the source remains fixed. |
| `controlled_decimate` | Removes only selected coplanar interior fans that preserve the exact surface. Protects silhouette, boundary, sharp edges, UV/material boundaries, authored normal fields and markings. |

The exact minimum-thickness checker uses projected triangle intersections, including
the moved surface and its unchanged opposing surface. Unprovable charts, crossings,
moving opposing components and missing coverage refuse the edit. It does not silently
substitute a sparse sample or bounding-box thickness.

The native normal witness verifies Blender's exact stored split-normal encoding,
planned fan flags and decoded vector error. The planner supplies independent face
spaces only inside the shown normal influence; equal intended directions remain
visually continuous. Blender's inverse encoder can snap to an incorrect integer
angle near a basis boundary. A bounded deterministic search on a private mesh finds
a closer stored angle without changing geometry, intended normals, fan flags or the
existing roundoff bound. Capture repeats that encoding and verifies the actual saved
mesh. Nonfinite or unrepresentable results refuse; failed writes roll back. Earlier
proofs that restored old fan flags after encoding are superseded, not accepted evidence.

Main-run tools include `g3d.quadratic_lens` with explicit edge/chord sampling bounds
and analytic optical normals, plus the four mesh-health helpers. The lens helper's
thickness is axial and is explicitly not a normal-thickness certificate. Surface
reports measure the actual geometry, not a stale saved facet table.

`compare_look_to_photos` and finish review now include side-light close-ups for facet
flatness, front seams and lens ripple, alongside the unchanged review-light and worn
views. Measurements and unavailable diagnostics are **information, not a new gate**.
The tool descriptions distinguish intentional sharp edges from unintended seams.

`job edit-check` is a completely local declared mapping of every exact request span
to current executable operations. It binds implementation hashes and refuses missing,
stale, overlapping or unavailable mappings before a paid UNDERSTAND or EDIT round.
It does not claim to understand language; labelled visual understanding and owner
confirmation remain necessary. The reproducible M7/M8 mapping proof is
`tests/proofs/assembly_m7_capabilities.py`.

## Copy demonstrations

All paths below are local ignored evidence, not committed model assets.

The complete owner-readable page is `data/assembly/m7-m8-review-v1/index.html`.
Its images are copied byte-for-byte from the individual evidence galleries.
Final M7 combined proof: `data/assembly/m7/surface-copy-v10`; photo-first room and
side-light gallery: `final-gallery-v5`; extra fixed-camera reflections:
`reflection-final-v3`. The L1/L2/F1/F2 labelled group and both amounts are in
`surface-operations-gallery-v1`. These start from the already rebuilt temple copy.

Each lens front goes from **880 to 3,600 triangles**. Maximum geometric fold falls
**4.6929 → 3.3853°**, 95th percentile **1.2941 → 0.6536°**; no measured interior
normal seam or degenerate front triangle appears. Original points and outline stay
fixed. The highlight change is subtle in the chosen views; mesh measurements do not
measure optical ripple or establish owner acceptance.

On each front corner, **14 selected main cut edges** go from roughly **38–43°** shading
jumps to at most **0.0072°**. Every vertex stays fixed. The authored geometric crease
remains; one boundary edge still has a **35.37°** shading jump and six unselected back
edges retain their existing jumps (up to **44.21°**). Grouped measurements retain all
these edges rather than reporting only the improved subset.

The final half/full native normal audit passes exact stored-code, fan-space and
decoded-direction checks. Worst component error is **0.000540**, below the unchanged
**0.000775** roundoff bound. All saved-copy hashes remain unchanged during audit.

| Example | Evidence |
|---|---|
| Flatten and shading | `data/assembly/m7/owner-surface-v3` and `<operation>-gallery-v3`. A disclosed copied-leaf fixture has plane residual **0.150002 → 0.074997 → 0 mm**; shading error **45.001 → 22.498 → 0°** with fixed geometry. All six saved copies pass the final normal audit. |
| Material and surface marking | `data/assembly/m8/material-marking-v3`; `<operation>-gallery-current-v1`. Real product selections; material proof includes the worn view. |
| Round and sharpen | `data/assembly/m8/radius-v3`; `edge_round-gallery-current-v1`, `edge_sharpen-gallery-current-v1`. Disclosed copied-leaf fixture; radii **0.125 / 0.25 mm**, then **0.125 / 0 mm**, restoring the original at zero. |
| Thickness and falloff bend | `data/assembly/m8/thickness-bend-v1`; `<operation>-gallery-current-v1`. Actual arm leaf thickness **0.349998 → 0.425 → 0.5 mm**; arm-leaf bend **7.5 / 15°**. |
| Anchored scale | `data/assembly/m8/deform-v5/scale_region`; `scale_region-gallery-final-v3`. Temple tip X width **+6 / +12%** with a fixed join; both amounts pass native scope, minimum thickness, strict spec and final normal audit. |
| Mesh health | `data/assembly/m8/health-v3`; `<operation>-gallery-current-v1`. Disclosed faults inserted only in a copied leaf: duplicates **3 → 1 → 0**, zero-area faces **2 → 0 → 0**, backward normals corrected, X-symmetry error **0.199997 → 0.099998 → 0 mm**, coplanar fan triangles **16 → 14 → 12**. |

Each operation-preview gallery places the photo first and shows before, half-amount preview and full
amount on the same copied source under room and side light, with its actual labelled
selection. Separate diagnostic diagrams show the actual saved mesh for changes too
small or occluded to see in AR. Cleanup and planar decimation intentionally preserve
the visible surface; identical appearance is not fabricated improvement.

The plane-angle-preserving temple proposal is
`diagnosis/c0015-preserve-angle-inner-copy.blend/.glb`. All measured facet residuals
are below **0.00000437 mm**, with closed solids and **1.15 mm** minimum over the unchanged
1 mm requirement. Keeping those planes' angles requires moving shared chart corners:
maximum **6.734 mm** at B. This is a visible proposal, not permission to alter the
delivered model. A warped face cannot keep every original border point, its exact
angles and perfect planarity simultaneously.

## Validation

- Full Python suite excluding photoprint: **4,003 passed, 2 skipped, 4 failed**.
  The helper inventory assertion was updated to the new actual facade; it and the
  timing/UV checks pass in isolation (**4 passed**).
- The golden render failure predates this work. Independent checkout of `7b9cd13`
  fails the same frozen test (203 changed pixels); current fails with 199. All 18
  direct baseline/current comparisons pass the existing tolerance, differing by
  0–9 pixels each. The historical golden mismatch remains unresolved; no gate or
  golden image was changed. Evidence: `data/assembly/m7/golden-audit`.
- Photoprint: **313 passed**, one subprocess import failure when launched inside
  the package directory. That unchanged import test passes from the repository
  root (**1 passed**). No production or test rule was changed for it.
- AR suite: **506 passed**, TypeScript check passed.
- Final affected Python suite: **400 passed**. Final instruction/offline helper/
  desktop packet subset: **90 passed**; targeted native normal/fan/radius group:
  **16 passed**, plus the nonfinite-decoder regression.
- Both actual `job edit-check` CLI reports are ready at $0: all four M7 items and
  all seven M8 items. The exact verbatim request bytes are preserved on Windows.
  Current reports: `data/assembly/m7/capabilities-final-v5/{m7,m8}-cli-report.json`,
  regenerated after the last implementation change.
- After the final quadratic-normal change: **13** independent refinement/review
  tests, **131** instruction/preview tests and **23** map tests pass.
- Final `tests.proofs.assembly_m1` **passed in 200.172 seconds**: 13 scripted tool
  steps, 26 verified transport files, 64 verified image blobs, 35 verified AR images,
  strict delivery checks, **$0 actual API spend**. No estimator or owner-approval
  writer is called by the final offline fixture. Its synthetic approval is rejected
  by production preflight. Log: `data/assembly/m7/assembly-m1-final-v3.log`.
- Independent review of all changed emitted instruction texts and tool descriptions
  has **no remaining findings**. Evidence: `final-text-review-v3/review.json` and
  `final-text-review-v4/delta-review.json` under `data/assembly/m7`.
- Implementation committed **d39f954**. The first fresh desktop Astra xhigh attempt selected
  the documented `g3d.quadratic_lens`, but the code executor still allowed only the six older
  facade helpers and refused it. This integration gap escaped the initial instruction review.
  The attempt stopped at verified empty checkpoint `c0000`; a general registration fix,
  guarded-worker regressions and another independent review precede a fresh restart.
  Evidence: `data/assembly/desktop-prada-m7m8-v1/stopped-attempt-audit.json`. External API
  calls, network attempts and production transports are zero. The ordinary ledger retains
  the cancelled request's synthetic $0.8192 unknown reservation; it is not a charge, was not
  reconciled, and prevented the harness from claiming an all-zero final ledger.

The follow-up fix shares the explicit `lib.NAMES` registry with both static and runtime
facade checks and their refusal hints. The armed-worker regression also exposed lazy
health-helper imports; three internal dependencies now preload during trusted facade
setup. No audit or physical rule is relaxed. **272** safety/real-worker tests and the
existing **33** worker tests pass. The exact refused Astra script then succeeds unchanged
on a separate empty scratch scene, creating 13 objects without runtime violations.
Independent review has no remaining findings:
`data/assembly/m7/final-facade-review-v1/review.json`. The earlier text exports remain
byte-identical. Original stopped-attempt evidence and its synthetic ledger remain intact.
