# M5-fix-7: Prada run 2 hinge-side triangle investigation

2026-10-15. Read-only investigation at `69bf095`, branch `glasses3d`, primary checkout.
Job `prada-beige-86248315`, run `20261015T142422-a3e947`, delivery checkpoint **c0017**.
No paid call, product/job/production-code/gate change, or new delivery. Diagnostic copies are not product proposals.

Owner comment, verbatim:

> the issue i see is that the facets are missing the triangle which is mostly close to the lenses frame. it is seen a bit in some view but barely, not like it should be, theres a noticeable difference between the triangles in the reference images. other than that im pretty happy with the results

Owner instruction, verbatim: **"let codex investigate please"**.

## Finding

The face exists, but it is nearly coplanar with the large logo face. Its delivered bend is **1.7297 degrees**;
the most recessed point of the clipped face is only **0.3172 mm inward in X** from the extended logo plane.
The exporter did not remove the facet or blend away its normal boundary. Both final temple meshes have
byte-identical positions, normals and triangle indices to the selected second-stage export.

The important failure is in choosing and reviewing the shape. Stage 1 tried a convex crease and the existing
readings check refused it. Stage 2 used a new fit, whose crease direction was unresolved, but Astra explicitly
called it "flat within its error" alongside the tool's small-angle `flat` label. It left the crease undeclared and
never compared an isolated repair of this face. The relevant close-ups reached Astra, yet its final report did
not identify this exact weak triangle. The angle/light experiment below separates the shallow shape from how
the renderer lights it; it does not recover a uniquely correct physical angle from product-photo brightness.

## What each stage declared, measured and built

Request numbers refer to the archived transport responses and numbered tool journals beneath the run directory.
The child stages use different corner names; they are not directly interchangeable.

| Evidence | First temple stage | Second temple stage, selected delivery |
|---|---|---|
| Parent request / child requests | 13 / 14–44 | 48 / 49–70 |
| Private package | `e7644a295eabc9ff` | `a400ed17eaf07593` |
| Selected revision | r0007 | r0005 |
| Hinge-side face | `front`, G-A-D-H-J, clipped | `fore`, A-C-D, clipped |
| Adjacent logo face / common edge | A-C-D / A-D | A-B-C / A-C |
| Selected fit, with one-standard-deviation error | **−3.649° ±2.155°**, shallow valley | **+1.733° ±2.310°**, shallow ridge |
| Evidence state / declaration | open / none after refusal | open / never declared |
| Actual core normal angle | 3.6487° | 1.7297° |

**Stage 1.** Request 31 (`call_m8GyD4InIN4ecRhTPT7ETF7H`) declared `front|logo` a ridge. The fit refused it:
the declaration cost **+8.1 chi-squared**, above the unchanged **4.0** allowance, against readings H (2.0 SD),
D (1.8 SD), A (0.9 SD). Request 32 retried without the refused declaration and explicitly said it would keep the undeclared
valley rather than override the readings. Requests 39–41 moved shared lower corner D outward by 3/6 mm,
making 7.5°/17.7° ridges, but also moving other faces and the outline, at 8/16 SD from D's reading. Astra said
the first made a "Stronger triangular shape" but rejected its sign reversal and other mismatches. Request 44
finished `stuck`, retaining r0007 and explicitly reporting the shallow valley.

**Stage 2.** Parent request 46 refitted cameras before starting the second private lab. Requests 55/57 measured
reference-05 (left side) and reference-02 (angled left). Request 58 identified AB, BC and AC as genuine edges
in both photos, built the four-face layout, and returned the target as `flat1.7°; fit+1.7±2.3°, open`.
Request 66 (`call_MTlY7MddYGwAXeNxeStSKSVB`) selected r0005 with this exact object-note sentence:

> AB/BF are certain convex ridges from the fit; BC was open (5.7+-3.7 deg) and is declared ridge as unmeasured sign; AC is flat within its error, left undeclared.

That last clause conflicts with the actual evidence state. `shape: flat` labels a built bend below 2°.
Statistical flatness requires `abs(angle) + 2*sd < 2.5°`; here that sum is **6.3536°**, so the result is **open**.
The specialist instructions say to try a ridge for an open edge unless a photo shows a valley, but also say
to leave an edge undeclared when the fit calls it flat within its error. The combined labels invite the interpretation Astra made.

No second-stage edit or variant declared `fore|logo`. Request 67's refusals (+204.5/+207.5 chi-squared) were for
**aft|logo**, a different edge. Request 69 moved shared C to t=7/10 mm, yielding 10.2°/20.7° at the target and
5.3/12 SD from C's reading; these altered several faces and addressed the top arrangement. They were not an
isolated test of the hinge-side face. Request 70 finished `stuck` on r0005, claimed crisp facet-to-facet borders,
and described other top/hook/camera conflicts without naming the weak AC edge.

## What the photo measurements can establish

The selected second-stage readings are saved, not reconstructed from Astra's description:

| Corner | X reading, mm | Residual in photo 05 / 02, pixels | Chi-squared, 1 DOF |
|---|---:|---:|---:|
| A, top hinge end of AC | 69.120 ±0.268 | 4.968 / 5.734 | 19.316 |
| B, rear upper logo corner | 69.746 ±0.355 | 0.219 / 0.205 | 0.032 |
| C, extended lower apex | 71.236 ±0.443 | 16.495 / 16.906 | 20.904 |
| D, lower front face corner | 70.127 ±0.270 | 7.653 / 8.000 | 23.688 |

A, C and D each return `W_RESIDUAL`: the two picked rays disagree beyond their stated errors (tool comparison
point **8.8**, 99.7%). Some edges also run close to the epipolar slide direction, so matching a crossing does
not prove that the same physical edge was chosen. The opposite-side checks retained residual warnings.
The reported angle uncertainty is conditional on these picks, cameras and covariance assumptions; it is not
a validated measurement of the physical product. C/D being outside the outline is not itself an error:
the design deliberately intersects face edges beyond the clipped physical silhouette.
The small downstream fit cost measures agreement with the inferred 3D nodes; it does not clear the original
pixel disagreement. The fold summary should carry those original reading-consistency warnings alongside its
angle, rather than requiring the reviewer to recover them from earlier calls.

The original photos visibly separate the hinge-side triangle more than the delivered model in the saved
comparisons. They do **not** provide a trustworthy exact angle or millimetre depth through the existing
inconsistent readings. No angle was inferred from brightness, no picks were moved to force a preferred result,
and this investigation did not reopen camera calibration research.

Photo-first audit sheets preserve original native pixels, then overlay Astra's recorded crossings and their
fitted reprojections, using the same crop and display scale:

- `data/assembly/m5-fix-7/photo-picks/reference-05-recorded-picks.png`
- `data/assembly/m5-fix-7/photo-picks/reference-02-recorded-picks.png`
- Reproducer: `data/assembly/m5-fix-7/photo_pick_audit.py`; complete values in `photo-picks/audit.json`.

## Export and tool controls

Final GLB SHA-256: `6c37275210ac54a543b63d8ecb10404eebc4a43a3dfc4b989db90b0166a99477`.
Each temple has 10,044 triangles. Comparing the final GLB with the selected child export gives zero difference
in positions, normals and indices. Comparing GLB vertices with the core fit gives at most **0.00001432 mm**
of float-storage error. Its authored normal bends are **1.72957° / 1.72976°** for the two sides.
The builder's `W_FLAT_CREASE` reports the small bend; it does not flatten it. `soft: true` on the fore face
only concerns its border with the plain arm, not the facet-to-facet AC boundary.

A separate **new $0 in-memory control**, not an action Astra took, added only `fore|logo: ridge` to the final
design. Existing fit and build checks passed: **2.49609°** actual bend, **+0.115734 chi-squared**, closed manifold,
Euler 2, minimum thickness **1.000001 mm**. No model was saved. The final-stage gate therefore did not prevent
a ridge declaration. Its generic 2.5° minimum is still a small bend and is not proof of a visual repair.

Source references: `agent/temple_lab/instructions.txt:22`, `agent/temple_lab/lab.py:390`,
`g3d/temple/fit.py:3229`, `g3d/temple/build.py:1447`. No constants or acceptance rules were changed.

## What Astra actually saw and reported

Request 59 (`call_5husQ6HbQA20LUZ4YcfsbJKD`) requested and received native geometry-enabled side and angled
close-ups. Each shows photo/render, the target labelled about 2°, and a surface-direction view without lighting.
The faint hinge-side boundary is visible in those stored comparisons. Request 62's judgment discusses logo/aft
edges, an extra upper return and projected-corner conflicts; it does not call out the hinge-side triangle as
the remaining defect. Request 66's explicit flat-within-error note supplies the recorded explanation for
leaving it undeclared. Tool delivery proves that images reached Astra, not precisely how it perceived them.

The parent later received the warning, side/top AR comparisons and final native details. Request 104 acknowledges
that "Upper facet boundaries are less prominent than the photo" and describes inner-wall/hook differences,
but does not name this triangle, AC, or 1.73°. Thus the evidence supports an inadequate interpretation and
review of available images, not a claim that Astra had no close-ups or knowingly repaired this exact defect.

## Controlled AR experiment

The final capture contains **36 native images**, using the unchanged AR explicit-camera renderer and hardware
GPU: four angle variants through all six frozen final photo cameras, plus room/broad/side lighting and normal
controls for the built and built+10° cases in photos 02 and 05. The resulting face-normal angles are **1.7297°,
6.7297°, 11.7297°, 16.7297°**, chosen in advance. No search selected a preferred-looking angle.

All **six unchanged-baseline images are pixel-identical** to the delivered native drawings: zero changed pixels
per image. That proves faithful reproduction of this delivery's rendering, not physical correctness of its
frozen cameras. Local browser requests were confined to the local renderer/assets; no external transmission
or provider call occurred.

Diagnostic copies rotate the clipped target face around A-C on both arms and move the duplicate connected
closure vertices consistently. Each side affects **23 target triangles and 27 adjoining closure triangles**;
the logo face and every other primitive's positions/normals remain unchanged. Materials, triangle indices,
other parts, photo pose/openings/background and room lighting remain fixed. Maximum vertex motions are
**0.9145 / 1.8273 / 2.7366 mm** for the three larger folds. The adjoining closure ramp is distorted, most clearly
at +15°. These copies were not validated for product thickness/intersections and are **not delivery candidates**.

Visual result: increasing the angle makes the hinge-side triangle more separate under the same room light,
particularly in the angled view. The light-independent normal view confirms that this is a change in surface
direction. But visibility is also sensitive to light: the same 11.73° fold can become weak in the side-lit
reference-05 view. Increasing angle alone does not establish the correct photo appearance.

For illustration, median fore-minus-logo differences on the rendered interiors (0–255, Rec.709-weighted encoded
sRGB; not calibrated luminance or a likeness score) are:

| Photo / room light | Built 1.73° | 6.73° | 11.73° | 16.73° |
|---|---:|---:|---:|---:|
| 05, left side | −0.79 | −1.86 | −1.93 | −2.93 |
| 02, angled left | +0.93 | +2.00 | +3.07 | +4.86 |

At 11.73°, photo-02's drawing difference changes from +3.07 under room light to +6.72 broad / +8.21 side;
photo-05's side-light difference shrinks to −0.21. Masks use projected visible outer face interiors, eroded
4 pixels, excluding decoration and front occlusion. These are drawing-only sensitivity measurements: no
physical photo depth, optimum angle or acceptance threshold is derived from them.

Photo-first sheets under `data/assembly/m5-fix-7/renders/`:

- `reference-05-angle-series.png`, `reference-02-angle-series.png`, `reference-01-angle-series.png`.
- `reference-05-light-controls.png`, `reference-02-light-controls.png`.
- `reference-05-normal-control.png`, `reference-02-normal-control.png`.
- `diagnostic-gallery.html` indexes those photo-first close-up sheets. All six native full-camera drawing
  series are also saved as `01` through `06` / `plus-00`, `plus-05`, `plus-10`, `plus-15` / `room-beauty.png`.

Reproducers: `prepare_diagnostics.py`, `render_diagnostics.mjs`, `make_sheets.py`; capture receipts,
hashes and measured arrays are in `diagnostic-manifest.json`, `capture-report.json`, `analysis.json`.
The initial capture filename-collision attempt and an initial wrong-side crop/metric attempt are preserved
separately. Visual review caught both before reporting; neither is used in these final numbers or sheets.

## Recommended general fix — proposal only

1. Report **current built angle** separately from **measurement state: sign-certain / unresolved / flat within
   error**. Do not print a low-angle `flat` label as though it were evidence of a flat physical surface. Clarify
   the existing prompt rule; the successful ridge-only control shows this does not require a gate change.
2. For a photo-visible edge that remains visually lost, give Astra an isolated, local crease-angle comparison
   through the frozen photo cameras and in AR, preserving adjacent important faces. Report the reading residuals,
   wall thickness and affected geometry with every alternative. Changing a shared vertex and several faces at
   once is not a clean test of one triangle. Compare lighting controls where reflectance may be the cause.
3. Have the specialist and parent explicitly carry any unresolved named visible-face mismatch into their finish
   summaries, with the relevant photo/render crop. Valid geometry alone does not establish that the face reads
   like the photos. This is an inspection/reporting improvement, not a new gate.

Do not automatically override conflicting measurements with a chosen visual minimum. A future proposal to let
a visual angle declaration exceed the existing reading-cost refusal would change policy and needs separate owner
approval. The present evidence does not prove that gate wrong; this task implements none of the recommendations.

## Evidence and verification

Evidence under `data/assembly/m5-fix-7/`: full call-level trace `forensics/report.md`, with
`forensics/evidence-index.json`, raw extracts and `forensics/mesh-binding.json`; geometry measurements and
reproducer `geometry/geometry-audit.json`, `geometry/audit_geometry.py`; ridge-only control
`geometry/ridge-control.json`, `geometry/ridge_control.py`. Binary evidence and diagnostic copies remain local
and ignored by Git.

The final hash guard passed **225/225 protected files unchanged** (delivery, checkpoint and owner inputs),
recorded in `protected-assets-after.json`. The only tracked changes are this review and PROGRESS.md.
No provider/API call or paid run occurred. `git diff --check` passed.

ASSEMBLY section 1 regression checks ran sequentially, with BLAS threads set to 1, memory checked before the
pools, detached logs and PID/start/end/exit receipts:

| Check | Result | Time |
|---|---|---:|
| Main suite, excluding photoprint, `-n 8 --dist loadfile` | 3,581 passed, 2 skipped, 3 failed | 734.04 s |
| Photoprint, `-n 2 --dist loadfile` | 314 passed | 213.41 s |
| Isolated seeing module + native-image baseline + QoS test | 23 passed, 1 failed | 101.90 s |

The loaded failures were the previously recorded native-image baseline (203 pixels versus 114.24 allowed),
render timing (67.546 s versus 60 s), and the QoS test whose initial state was affected by the launcher's
descendant power-management watcher. The isolated run disabled that watcher, allowing the existing QoS test
to measure unmodified initial/child states. Both QoS and the seeing module passed. The sole remaining failure
is the known `test_render_dome.py::test_photo_v1_pixels_are_unchanged`: **199 native pixels versus 114.24**,
matching the earlier isolated baseline. It was not weakened or fixed in this read-only investigation.

Reproducible commands and exit receipts are in `data/assembly/m5fix7-full.receipt.json`,
`m5fix7-photoprint.receipt.json`, `m5fix7-isolated_qos.receipt.json`, with adjacent logs. The new evidence proof is
the immutable-mesh comparison, unchanged-gate ridge control and 36-image AR experiment above. No runtime code
was changed and no new regression test was added solely for these documentation changes.
