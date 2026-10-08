# WP6 faceted temples: final design

**Current status (2026-10-08): STOP-I retained; implementation already exists.** Read
[WP6_READINESS.md](WP6_READINESS.md) for actual module paths, retained instrument failures and the next starting
point. The original "Build it" verdict and predicted values below are design history, not current proof results.
The 2026-10-07 delegated decision keeps R1 and every frozen R3 clause. WP5's owner closeout does not waive them.

Written 2026-10-05 from the WP6 measurement and three independent designs, each prototyped at $0 on copies of the
Prada PRS 17WS scene (`8c82…/agent/scene.blend`, copy sha256 56274deb… unchanged), its six photos and the WP3b cameras,
in scratch `C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp6/`: tool-first **T** (`proto/`), measurement-first **M**
(`design/` P-A…P-E: `ptemple.py`, `peval/`, `relief_metrics.json`, `front_anchor.json`), tracing-first **Tr**
(`design/tf/`, `evidence.json`). **Base = Tr** (proof logic, tracing, slab control). Core = **M**'s shape family built on
**T**'s exact arrangement and refusals. [Bracketed] numbers are prototype results to be re-measured, not evidence that
WP6 is done. Photo numbering is PLAN §5: 01 side, 02 angled, 03 top, 04 front, 05 side, 06 rear.

## 0. Verdict

**Build it. The tool is not the risk; the instruments and the photos' agreement are.**
1. **Tool: proven feasible three times.** Three numpy cores built closed temples (χ = 2, no open edges) on the Prada
   copy: facets planar to 1.7e-14 [T] / 8e-12 mm [Tr], GLB facet corner normals within 0.027° [T] / 0.019° [Tr] of the
   face normal, `validate_spec` passes (width 144.3–144.6 mm), 0.05–0.11 s in headless Blender 5.2.
2. **The PLAN's bulge band does not measure facets** (all three designs). Vertical-section bulge over chord: delivered
   slab 0.62, M's prism 0.66–0.98 for relief 0 → 5.3 mm, T's fit 0.64; a 30° chamfered slab reads 2.23. M's plan-view
   bulge is monotone in relief but measures thickness: the delivered slab already scores 1.34, so a thicker slab passes.
   **Decision:** bulge is reported with a warning only (the PLAN calls 1.9–4.3 "only a sanity range"); the gates are
   the relief measures in §8 (PLAN correction 1).
3. **Cameras fitted with the delivered temples favour them.** On refit the delivered loses nothing (losses 0.1835 →
   0.1834, 0.1306 → 0.1305 [M]) while traced models gain 5–21%; every camera fitted with temples peaks at opening θ = 0
   [T]. Front-only fits are degenerate (04 pitch 0.09° vs 10.29°; 03 pitch 66.5 vs 58.75 [T, Tr]). So every model is
   graded at its own bounded refit and per-photo θ_k, with a 2×2 cross-camera check.
4. **The photos constrain three different quantities, and the design keeps them apart:**
   - *upper hinge-zone level* (lateral position of B/A): seen by 03 (0.58 mm per mm [Tr]), 06 and the 04 top rows;
   - *facet relief* (C proud of the base): **invisible in silhouettes** (relief 0 vs 5.3 changes 25 px in 06, 235 in
     03, 1,190 in 04 [M]); seen only through crease lines (05/01/02), crease transfer 05 → 02 and the front bound
     (C's bottom edge X ≤ 71.7 mm at d 22.4, ≤ 72.1 at d 30 [M]);
   - *F4 tilt*: no observable at all [Tr]; it is flagged `UNOBSERVED` and kept at its default.
5. **A zero-relief slab with the traced outline already beats the delivered GLB on top and rear** (03 IoU 0.8616 vs
   0.8436; 06 0.925 vs 0.914, hinge-zone error 1.63 vs 1.72 mm [Tr]). The PLAN's top/rear gates therefore test outline,
   plan and hinge angle, not relief. Every relief gate here must also beat a **fitted slab** built by the same tool.
6. **Main risk: the views disagree on level and relief by 1–3 mm today.** 03 and the 02 crease lines prefer X_C
   74.5–75.5, 06 prefers 72.5, the front caps C at ≈ 72.5 [Tr, with C−B fixed at 5.31]. Freeing C−B cuts the spread to
   1–1.5 mm, and at X_C 72.5 / C−B 2.5 the crease transfer is 0.36 mm [Tr]. The 5.3 ± 0.6 triangulation used the
   outer-face-plane lift that Tr showed biased (top edge −1.59…−1.99 mm at d 15–60), so it is re-measured inside the
   fit. **Prediction:** P0/P1 pass; shape gates pass (for the slab too); R1/R2 pass once F1's upper edge (01 creases at
   y ≈ 13) is modelled; S3 (consistency) is borderline and may fire STOP-C, which ends in an owner evidence sheet.

## 1. Scores (0–10) and what was taken

| Design | Fidelity | Proof/stop objectivity | Determinism | Testability | Simplicity | Taken |
|---|---|---|---|---|---|---|
| T | 7 | 6 | 9 | 9 | 5 | arrangement + exact plane propagation, refusals with fields, conditioning, single-plane share, camera bounds, θ_k, fixtures T1–T5, in-place backup/revert, decal auto-detection, diagnosis order |
| M | 7 | 7 | 7 | 8 | 7 | base + planar-relief shape family, front anchors/bound, normal-change crease flag, 2×2 cross-camera, rear α family + STOP-R, chamfered-slab trap, Bevel-applied rolls, hinge report |
| Tr | 8 | 9 | 7 | 9 | 6 | **base:** model-surface tracing, crease + rise planes, kink-sharp rule, θ from arm rows, slab control, S1 identifiability, S2 representability, S3 consistency, frozen rules file |

## 2. Conflicts resolved

| Topic | T | M | Tr | Decision and reason |
|---|---|---|---|---|
| Shape family | thickness above inner face; arm loft with fades | base x_in+T plus affine relief per facet | station loft; ruled arm cells | **M on T's arrangement**: exact by construction and the smallest family. Ruled cells dropped (27.5° normal spread behind B, 39.9° at the lower apex [Tr]) |
| Facets | crease graph; faces found | node polygons | polygons; crease + slope planes | **Polygons** (labels map 1:1 to Astra's reading) with planes from 3 nodes or **crease + rise** [Tr: near-collinear A-B-G tilted F1 26° from a 3 mm node error] |
| Over-determined facet | refuse with misfits | refuse > 0.005 mm | least-squares adjust ≤ 0.05 mm | **Refuse (T)**: the tool never edits Astra's numbers silently |
| Edge roll | core quarter arcs (not prototyped) | applied Bevel | none | **Applied Bevel on the outer long edges only**, re-checked; the inner edges stay crisp, so the side silhouette is unchanged; default 0 |
| Arm features | arm creases with fades | none | ruled | **`chamfers` along top/bottom only**: Prada's arm is plain; covers the open "F1 runs along the arm" reading |
| Relief gate | single-plane share, dihedral floor, shading rank, C−B ± 1 | plan bulge 1.9–4.3 | observables vs slab | **R1 single-plane share [T] + R2 normal change across photo creases [M] + R3 crease transfer [Tr] + R4 slab control [Tr]**; bulge reported only |
| Shading | rank ≥ 0.8 gate (unprototyped) | SH-2 predicts ≤ 1.7 L* where photos show 3.1–7.2 | residual 1.7–2.0 L* vs 2–4 between facets | **Report only.** Two measurements say a lighting model cannot grade facets |
| Cameras | bounded around WP3b | per-model local refit | refit on candidate | **Per-model refit inside T's bounds + M's 2×2**; delivered and slab get the same treatment |
| Rear 06 | θ_k only | α family, pitch [5, 16] | turntable seed, θ from arm rows | **M's family; Tr's grid then Brent** (local Brent alone found false basins at −8°) |
| Shared perspective | hypothesis | rejected (0.08–0.36 across views) | rejected (front worse at 0.166) | **Rejected** |
| C−B | prior 5.3 ± 0.6 and gated | conflicts with the front | free | **Free in the fit**; the 5.3 came from the biased plane lift (§0.6) |
| Level/relief conflict | front wins (D3) | STOP-C | S3 gate | **Front bound is a hard one-sided constraint; any other disagreement beyond max(1 mm, 2σ) is STOP-C.** No view is dropped, down-weighted or caveated (Tr's "owner-generated" report column is removed: all views are photos) |
| Outline tracing | rays ∩ face plane | column + row traces | render and compare on the model | **Tr**: plane lift is biased 1.6–2.0 mm; handles the ear hook [held-out other temple 0.16 top / 0.47 bottom mm] |
| Matte | replace default | new mode | new mode | **New `mode="glossy_floor"`, old default kept** so WP3b/WP5 numbers stay reproducible |
| Old geometry | in-place swap, fake-user backup | excluded collection | hidden + backup | **T**: object name, transform, materials and extras unchanged for the exporter and other tools |
| Deliverable opening | median θ_k | from 03 | not baked | **Parameter, default unchanged; θ_k reported only** (no photo-derived value is trustworthy while cameras peak at 0) |
| $0 parameters | helpers + fit; topology by hand | traced, then fit | topology + guide endpoints only | **Tr**: Claude supplies exactly what Astra will |

## 3. Tool `g3d.faceted_temple(spec, *, side="both", targets=None, decals="auto", dry_run=False) -> receipt`

**Frame.** Scene mm, +Y up, +Z front (WP3 verified). The spec describes the +X temple; −X is its exact mirror (faces
reversed). Parts are chosen by the sign of x, never by name (Astra's Prada "right" temple is +X, the wearer's left; the
near temple is +X in 05 and −X in 01). Station d = z_h − z at opening 0; y up; X = |x|.
The solid is {y_bot(d) ≤ y ≤ y_top(d), x_in(d) ≤ X ≤ x_in(d) + T(d) + r(d, y)}, then rotated by `opening_deg` about the
vertical hinge axis.

**Spec (`schema: "g3d.temple/1"`, JSON):**

| Key | Meaning |
|---|---|
| `hinge` | `{z_mm, axis_xz_mm, opening_deg}`. Default axis = bbox centre of the temple-role knuckles (Prada −10.6, (64.6, −10.1), 0) |
| `outline` | `top`, `bottom`: [[d, y]], d strictly increasing, sharing first d (hinge-end cap) and last d (tip). `tip_round_mm` rounds the tip corners. Includes the ear hook |
| `inner` | [[d, x_in]]: vertical inner face; must be linear over [0, `zone_end_d`] (flat where the photos show it) |
| `thickness` | [[d, T]]: base thickness; linear over the zone, so the base outer surface is a plane there |
| `zone_end_d` | end of the facet zone; outside it facets are refused (E_FACET_ON_BEND) |
| `nodes` | {name: [d, y, r \| null]}: r = mm outward from the base surface; null = solved. A node may lie outside the outline (Prada C) |
| `facets` | {label: {poly: [nodes], plane: "nodes" \| {crease: [n1, n2], rise_per_mm: s}}}. Polygons are clipped by the outline, may not overlap, and the rest of the zone is base (r = 0). `rise_per_mm`: relief gained per mm of (d, y) distance from the crease into the facet |
| `chamfers` | [{edge: top \| bottom, d: [d0, d1], width_mm, depth_mm}]: strip along an outline edge, planar in the zone, ruled beyond it |
| `roll_mm` | 0 = crisp (default); else Bevel on the outer long edges, 8 segments |
| `options` | `t_min_mm` 1.0, `station_step_mm` 2.0, `planar_tol_mm` 1e-6, `crease_sharp_deg` 6, `kink_sharp_deg` 25 |

Prada example (+X, traced values [M, Tr]): `nodes = {A: [-1.0, 10.55, r_A], B: [46.8, 9.52, r_B], C: [22.6, -16.3, r_C],
…}`, `facets = {F1: {poly: [A, B, Bt, At], plane: {crease: [A, B], rise_per_mm: -0.25}}, F2: [A, B, C], F3: [A, C, H],
F4: [B, C, E]}` (F1 ≈ 14° from the front photo; 30° is visibly too narrow [M]).

**Construction (numpy, float64, deterministic):**
1. Validate the spec: outline simple and single-valued in d (cable temples refused), zone linearity, node references.
2. Arrangement in (d, y): outline + facet edges + chamfer lines + stations (every knot, every ≤ 2 mm where the base or
   outline bends), clipped; half-edge faces, vertices deduplicated at 1e-7 mm; facet areas must tile within 1e-6 mm².
3. Planes by exact propagation [T]: 3 known non-collinear nodes (widest triangle) or crease + rise define a plane; null
   nodes inherit; repeat to a fixed point; any extra known node off its plane by > `planar_tol_mm` refuses with each
   misfit. Shared edges must agree (base edges at r = 0) within the tolerance. Conditioning = tilt change per mm of node
   error [Prada F2 2.2, F3 4.5, F4 3.2 °/mm].
4. Lift: outer X = x_in + T + r on each face's own plane; inner face = the same triangulation at x_in, reversed (each
   region ear-clipped once, used twice [T]); top/bottom strips, hinge-end cap, tip cap.
5. Sharp edges: facet creases and facet/base edges, chamfer lines while their dihedral ≥ 6°, face-to-strip junctions,
   outline kinks > 25° [Tr], caps. Custom normals = face normals on facets.
6. Mirror −X; rotate by the opening; self-check: closed 2-manifold, χ = 2, no duplicate directed edges, volume > 0,
   planarity, thickness ≥ t_min, no self-intersection.

**Blender layer (`g3d/temple_bpy.py`):** plans and validates fully, then writes once.
- Swaps the mesh data of each target temple object in place; the old mesh stays as `<mesh> | g3d pre-temple` with a
  fake user. `g3d.unfaceted_temple(obj)` restores it byte-identically. The WeightedNormal modifier is removed (the tool
  owns the normals) and its settings are kept in the backup record.
- `roll_mm > 0`: Bevel (limit by weight on the outer long edges, 8 segments, harden normals), applied, then re-checked:
  manifold, χ = 2, facet planarity ≤ 1e-4 mm (float32), side silhouette = outline ≤ 0.01 mm. Failure → E_ROLL, nothing
  written.
- Extras: `partRole: temple`, `region: temple_+x|temple_-x`, `g3d_temple` = canonical spec + core version + sha256 +
  facet table, allowlisted by `g3d/export_glb.py` (WP4's extras allowlist).
- Decals (`decals="auto"`, [T]): thin open or near-zero-thickness parts within 1 mm of the old outer face (Prada:
  PRADA/MILANO hallmarks, ribbon) are ray-projected along the old outer-face normal onto the new face + 0.05 mm
  [T prototype: 0 misses, max move 0.61 mm, all inside F2].
- Hinge parts are never moved. Report: leaf-to-inner-face gap [Prada 0.05 mm], knuckles 2/4 contact, frame knuckles
  1/3/5 penetration.

**Refusals** (nothing written; each names the facet/point/numbers): E_SPEC, E_TARGET (not a temple-role mesh, or shape
keys), E_OUTLINE, E_OUTLINE_NOT_SINGLE_VALUED, E_OUTLINE_NOT_SIMPLE, E_ZONE, E_FACET_ON_BEND, E_FACET_OUTSIDE,
E_FACET_OVERLAP, E_RELIEF_STEP, E_FACET_UNDERDETERMINED, E_FACET_NOT_PLANAR, E_ILL_CONDITIONED (> 10 °/mm), E_TOO_THIN
(point, facet and its defining points; F4 regression: −4.72 mm at (58, −5.30) [T]), E_CHAMFER, E_ROLL, E_TRIANGULATE,
E_NOT_CLOSED. **Warnings:** W_CONDITIONING (> 6 °/mm), W_FLAT_CREASE (< 2°), W_ARM_NORMAL_SPREAD (> 30°),
W_DECAL_SPANS_CREASE, W_HINGE_LEAF (gap > 0.2 mm or embedded > 0.5 mm), W_KNUCKLE_PENETRATION (> 0.1 mm).

**Receipt:** facet table (label, area, normal, lean against the inner normal, defining points, conditioning), crease
table (dihedral, ridge/valley), min/max thickness and where, stations every 5 mm (width, height, vbulge, pbulge), decal
moves, hinge report, the §5 geometry numbers, warnings. **Budget:** core ≤ 50 ms per temple [17 ms]; both temples in
Blender ≤ 1 s [44–114 ms].

## 4. Helper upgrades (RULES_AUDIT §A rows 1–4)

- `g3d.polygon_section(points, corners)`: closed 2D section; each corner `sharp`, `("chamfer", w)` or `("round", r)`
  with max(8, ⌈8·θ/90°⌉) segments; vertices carry a sharp flag (rows 1, 3).
- `g3d.loft_sections(stations, path=None, caps=True)`: per-station sections, equal counts by arc-length resampling
  between sharp anchors, shorter-diagonal quads, sharp longitudinal edges while ≥ 6°, per-strip planarity report (metal,
  wire and tapering temples; row 3).
- `g3d.check_round_corners(obj)`: the only shape rule kept ("≥ 8 points per 90° on rounded corners"), reported, never
  refused. No rounded-tube mandate (row 1).
- Row 2 (exporter normals) is done in WP3 and re-verified by T3 below; row 4 (prompt text) is WP7 (§12).

## 5. `check_relief`

**Geometry** (one numpy core in `g3d/temple/measure.py`; `g3d.check_relief()` on the evaluated mesh in Blender,
`g3d_host.relief_check.check_relief(glb, job=None)` on the GLB). Temples by role, else by position (behind the lens at
|x| > 45 mm); side by x sign; chart from `g3d_temple` extras, else PCA plus an inner-face plane fit. Stations every
2.5 mm: width, height, x_in, x_out, outer tilt, inner flatness, **vbulge** (the sections2 definition behind the PLAN's
0.28–0.66) and **pbulge** (plan-view thickness bulge), both report-only.
- **Single-plane share:** the largest area fraction of the d 2–50 outer face within 0.3 mm of one plane (candidates: each
  detected facet plane and the least-squares plane) [delivered 0.81, T pyramid 0.34, T fit 0.42].
- **Facet table:** from extras, else region-grown triangles with normals within 1° and area ≥ 4 mm²: normal, lean,
  planarity, dihedral and sign with each neighbour.
- Flags: **SLAB** (single-plane share ≥ 0.8 while the photos show ≥ 2 creases in the temple; a 30° chamfered slab must
  raise it), **INNER_NOT_FLAT** (> 0.05 mm where the photos show a flat inner face), **BULGE_SANITY** (vbulge outside
  1.9–4.3 mm; warning only).

**Photos** (host; needs the §6 cameras and θ_k):
- **Crease segments** [M]: Canny on L* (σ 1.2 px), probabilistic Hough ≥ 4 mm, step = median L* 3–7 px either side
  ≥ 3 L*; masks: decals + 6 px, hinge, lenses, L* > 97, a 5 px silhouette band. Only views with the outer face at
  ≥ 5 px/mm and viewing cosine ≥ 0.5, registered ≤ 1 mm at the temple (today 05 and 01).
- Per segment: distance to the nearest model crease and the model's **normal change dn** across it (±3–7 px):
  **FLAG** = no model crease within 2 mm and dn < 6°; **SHALLOW** = crease within 2 mm, dn < 6°; **OK** = dn ≥ 6°.
  6° is a policy constant [delivered ≤ 4.6° on all 10 side-view segments, 94 mm; M prism 5.8–11.6°]. A crease is
  **confirmed** when 05 (+X) and 01 (−X, mirrored) lift it to (d, y) lines within 0.5 mm median distance.
- **Crease transfer** [Tr]: each confirmed 05 crease lifted onto the model surface, projected into 02, distance to the
  02 photo crease in mm (works for any model, including a slab) [delivered 1.65].
- `INVENTED_CREASE` (advisory): a model crease ≥ 5° with no photo segment within 2 px in a view that sees it.
- `FRONT_BOUND`: any hinge-zone model point beyond X_max(d, y) − 0.2 mm, the bound recomputed at the model's own 04
  camera [M]; hinge-zone overshoot area is reported separately from arm tops (Tr found the two mixed, median d 112).
- `UNOBSERVED`: parameters with no observable (from S1; Prada F4 tilt).
- Shading residual against a lighting fit is reported, never gated.

**Astra gets** the numbers, flags, per-view crops with segments coloured by verdict over the model's creases, and a
relief sheet (sections at the photo nodes with the fitted values, their σ and the front bound).

## 6. Views, cameras and opening

- **Matte:** port scratch `matte2.matte3` into `g3d_host/matte.py` as `mode="glossy_floor"`: threshold 36 levels, same
  opening/closings, fill a hole only if its inside is not background-like (median > 18) or < 0.2% of the image, drop
  components ≤ 300 px, final 11 px vertical opening. Every step is monotone, so it can only remove pixels; a test
  asserts glossy ⊆ default on all 12 Prada/FT1119 photos and the Prada deltas (01 −122k … 04 −3k). The default stays;
  switching it is a WP3b follow-up after FT1119 is re-run. The WP3b Prada table is re-recorded beside the old one [01
  5.58 → 1.43 mm; 06 2.76 → 2.93, camera still unreliable].
- **Per-model refit** (every compared model: candidate, fitted slab, delivered): staged Powell from the glossy_floor
  WP3b camera within yaw ±3°, pitch ±2°, roll ±1°, scale ±3%, shift ±10 px, perspective ±0.1; the view's front-part
  contour may not degrade by > 0.1 mm. No shared perspective.
- **θ_k per photo and model** ∈ [−4°, +12°]: 1° grid, then bounded Brent, scored on arm rows (02/03 arm, 06 arm strip,
  04 arm tops above the frame [Tr: −4° adds 4,500 px]). Reported, never baked.
- **Rear 06:** pitch window [5°, 16°], perspective [0.03, 0.4], α grid then refine; the **family** = every α with loss ≤
  1.2 × the minimum [Prada α 4–8; Tr θ 5.7–6.9]. Gates hold at every α of the family.
- **Temple ROI**, fixed per comparison [M]: dilate20(union of every compared model's temple pixels over its θ family) ∪
  dilate10(matte minus dilate6(non-temple render)). Metrics: IoU, and symmetric contour distance in mm at the
  hinge-zone px/mm, truncated at 60 px.
- **Envelopes** (top, rear): per row the outer and inner edge and width of the temple strip, only on rows whose
  outermost render run is temple; rows split by the render hit's station into hinge zone (d < 50) and arm/hook (60–130).
- **Cross-camera 2×2** [M]: each model scored also at the other's cameras.
- `compare_temple_views(models, views="all")` returns all of the above plus overlays and a six-view sheet (photo,
  delivered, candidate). Budget: refit 60–150 s per model and view; rear family 6–8 min per model [M].

## 7. Tracing and the joint fit (host, $0, suggestions only)

| Helper (`g3d_host/temple_trace.py`) | Output | Accuracy [measured] |
|---|---|---|
| `trace_outline(job, ("05","01"))` | top/bottom chains: per column, photo vs render edge (±60 px), residual applied as the median within ±2.5 mm of each knot, 3 iterations; knots where 05 and 01 differ > 1 mm flagged and taken from the cleaner view | in-view 0.07–0.17 mm; held-out other temple 0.16 top / 0.47 bottom |
| `trace_creases(job, guides)` | guide endpoints (px, on gridded crops) snapped to the gradient ridge; corners as line intersections; lifted onto the current model surface; σ | A, B 0.3 mm; C 1.3 mm. Never take (d, y) from 02 (3–5 mm off) |
| `trace_plan(job, ("03","06"))` | inner line and arm thickness from each temple's own arm line (d 60–100) | ≈ 1 mm repeat, ±2–3 mm absolute |
| `trace_temple(job, guides)` | the three above + θ_k + a starting spec | — |

**`g3d_host/temple_fit.py: fit_temple(job, spec0, free="default", views="all")`**
- **Free (≤ 12, each bounded by its traced band):** x_in at 0 and at `zone_end_d`, zone thickness, r_A, r_B, r_C (C−B
  free), F1 rise, arm offset, tip offset, two plan knots. **Fixed:** the facet (d, y) layout, the tip station. The
  outline is re-traced by `trace_outline` each round, not optimised.
- **Nuisance:** §6 cameras and θ_k per view; rear family.
- **Objective:** Σ views (1 − ROI IoU + 4 × boundary/width) + crease-line reprojection on 05/01 + 05 → 02 crease
  transfer (1 mm ≈ 0.02). **Hard constraints:** the front bound; dn ≥ 3° at every confirmed crease.
- **Optimiser:** block coordinates (cameras and θ | shape: bounded Powell, maxfev 300), ≤ 4 rounds, stop when every
  parameter moves < 0.1 mm / 0.1° and the loss < 1%. Budget ≤ 90 min per product, heartbeat to the log every ≤ 60 s,
  detached runs with the EcoQoS opt-out; iteration mode (fixed cameras) ≈ 0.5 s per evaluation.
- **Output:** suggested spec, σ per parameter (finite-difference Jacobian), per-parameter profiles per view, a
  conflict table (views pulling a parameter apart by more than their noise), the S3 table, the `UNOBSERVED` list.
  It never writes the scene. The **fitted slab** is the same fit with every r fixed at 0 and no facets.

## 8. $0 proof (thresholds frozen in `g3d_host/relief_rules.py`; its commit hash goes in PROGRESS before any run)

**P0 tool** (`tests/fixtures/temple_fixtures.py`: no-facet slab, Prada pyramid, valley crease, facet touching the zone
end, top chamfer along the arm, rolled edges, virtual node, solved node, crease + rise F1, near-collinear F1, F4 thin
regression, 30° chamfered slab, analytic prism; both sides; plus the fitted Prada spec). Graded by an independent GLB
instrument (`tests/instruments/temple_p0.py`) that does not import `g3d/temple`:
- closed 2-manifold, χ = 2, 0 open/duplicate edges, volume > 0;
- planarity ≤ 1e-9 mm in the core [1.7e-14], ≤ 1e-4 after Blender, ≤ 2e-3 in the GLB; zone inner flatness ≤ 1e-9;
- dihedral = analytic ± 0.01°; outline reproduced ≤ 0.01 mm (with roll too);
- bit-identical rebuilds; host vs Blender ≤ 1e-9 mm before roll; −X = exact mirror;
- every §3 refusal fires with its fields and the scene digest unchanged;
- `validate_spec` passes; GLB facet corner normals ≤ 0.1° [0.027]; chamfers ≥ 6° and kinks > 25° sharp; arm normal
  spread ≤ 30° [Tr 27.5 → fixed by the family];
- decals 0 misses, move ≤ 1.0 mm, each inside one facet and ≤ 0.05 mm from its plane; source sha256 unchanged; revert
  byte-identical; budgets of §3.

**P1 instruments:** the analytic prism gives its known share, dihedrals and bulges; the 30° chamfered slab raises SLAB
(vbulge ≈ 2.2); the delivered GLB reproduces vbulge 0.62 ± 0.03, pbulge 1.34 ± 0.05, share 0.81 ± 0.03; on 05/01
every delivered segment is SHALLOW or FLAG; the glossy matte subset test passes.
**S1 identifiability** [Tr]: render the tool's own temple through the fitted cameras (each perturbed by its view's
measured contour error) as synthetic mattes plus Lambert-shaded crease steps; trace and fit from the slab. Must recover
the outline ≤ 0.2 mm, zone level ≤ 0.5 mm, r_B and r_C ≤ 0.5 mm, F1 rise ≤ 0.05, θ ≤ 0.3°. A parameter that fails
becomes `UNOBSERVED` unless R2/R3 depend on it (then STOP-I).
**P2 tracing:** outline in-view mean ≤ 0.2 mm, held-out other temple ≤ 0.5 mm outside flagged knots; A, B ≤ 0.5 mm and
C ≤ 1.5 mm between 05 and 01.
**P3 build:** on a copy, `faceted_temple(fitted spec)`, export, `validate_spec`; every source sha256 unchanged.

**Gates** (candidate, fitted slab and delivered, each at its own refit cameras and θ_k; all required):

| # | Gate | Prototype |
|---|---|---|
| G1 top 03 | temple-ROI IoU ≥ delivered's (PLAN) and whole-silhouette IoU ≥ delivered's; hinge-zone signed offset \|median\| ≤ 0.6 mm | ROI 0.774–0.776 vs 0.728 [M]; offset 0.27/0.03 at X_C 74.5, 1.42/1.18 at 72.5 [Tr] |
| G2 rear 06 | at every α of the family: ROI contour ≤ 0.8 × delivered; 2×2: candidate still better at the delivered's camera and the delivered worse at the candidate's; envelope hinge-zone outer median and arm-band outer/inner/width medians each ≤ delivered's | 0.77/0.60/0.55 vs 1.34/1.24/1.22 mm, cross 1.10–1.14; hinge rows 2.36/1.21/1.24 vs 2.98/2.75/2.63 [M] |
| G3 sides 01, 05 | ROI contour ≤ delivered's; outline mean ≤ 0.3 top, ≤ 0.5 mm bottom | 1.04–1.15 vs 1.57; 0.89–0.98 vs 1.11 [M]; 0.07–0.09 / 0.19–0.38 vs 0.78–0.80 / 1.71–1.78 [Tr] |
| G4 front 04 | ROI contour ≤ delivered + 0.05 mm; `FRONT_BOUND` clear | 0.31 (relief ≤ 2), 0.36 (3.5), 0.41–0.57 (4.5–5.3) vs 0.36 [M] |
| G5 angled 02 | ROI IoU ≥ delivered − 0.01 | contour 1.23–1.34 vs 1.32 [M] |
| R1 facets exist | single-plane share ≤ 0.5 | 0.42 vs 0.81 [T] |
| R2 creases | per side view: no FLAG ≥ 4 mm, length-weighted median dn ≥ 6°; every confirmed crease has a model crease within 1.0 mm with dn ≥ 3° | 10.4°/6.9° vs ≤ 4.6° [M]; T's fit v2 (AB 0.6°) fails |
| R3 transfer | 05 → 02 transfer ≤ 0.7 mm, and ≤ delivered − 0.5 and ≤ fitted slab − 0.5 | 0.36 (X_C 72.5, C−B 2.5), 0.66 (74.5), 2.28 (72.5, C−B 5.31) vs 1.65 [Tr] |
| R4 relief costs no silhouette | every view's ROI contour ≤ fitted slab's + 0.1 mm | — |
| V | `validate_spec` passes; W_HINGE_LEAF and W_KNUCKLE_PENETRATION clear; arview capture loads with no new refusal | — |

vbulge and pbulge are reported in every row of the sheet [expected below 1.9 for every rear-accepted candidate: 0.8–1.3
(Tr)]. **Then the owner judges** delivered vs candidate in arview (webcam) with the six-view sheet; the verdict goes to
the ledger.

## 9. Stop rule (evaluated in this order by `tests/proofs/wp6_prada.py`)

- **STOP-T:** any P0 failure that is not a code bug fixed in the same pass. Fixtures are never edited to pass.
- **STOP-I:** a P1 failure, or S1 cannot recover a parameter that R2/R3 depend on: the instruments cannot judge relief.
- **STOP-R:** for any model, every α of the rear family sits at a pitch bound, or its frame-fit guard is > 2.0 mm
  [1.56–1.70]. Report; never fall back to a single fixed camera.
- **STOP-S (representability, S2 [Tr]):** after the fit, a relief-seeing view (02, 03, 06) has a temple-ROI contour >
  1.5 × its front-region contour + 0.3 mm. Re-diagnose before adding parameters, in T's order: (i) the 04 pitch, which
  only the temples pin; (ii) the side-traced top edge against 04; (iii) the facet topology (F1's upper edge first).
- **STOP-C (consistency, S3 [Tr]):** for the zone level at B or the relief at C, an observable's own optimum (03 offset,
  02 crease lines, 05 → 02 transfer, 06 hinge-zone envelope) lies outside max(1.0 mm, 2σ) of the joint optimum, or the
  joint optimum needs the front bound active by > 0.5 mm. Report the S3 and conflict tables and the sheets to the owner
  [today: 3 mm with C−B fixed, 1–1.5 mm with it free]. Never move relief, drop a view or weight one down to pass.
- **STOP-P:** any gate fails with fitted parameters inside their bands. Re-diagnose by view and rows; never widen
  bands, edit values by hand, change thresholds after results, edit vertices, or make a paid call.

## 10. Module layout

| Path | Contents |
|---|---|
| `g3d/temple/` (numpy only) | `spec.py` (schema, codes), `arrange.py` (clipping, half-edge faces), `planes.py` (propagation, crease + rise, conditioning), `build.py` (lift, strips, caps, chamfers, sharp sets, self-checks), `measure.py` (stations, bulges, single-plane share, facet table, flags; shared by tool and check), `report.py` |
| `g3d/geom/{sections,loft}.py` | §4 helpers and `check_round_corners` |
| `g3d/temple_bpy.py` | mesh swap, sharp_edge + custom normals, Bevel roll + re-check, extras, decals, hinge report, backup; `faceted_temple`, `unfaceted_temple`, `check_relief` |
| `g3d/__init__.py`, `g3d/cli.py`, `g3d/export_glb.py` | lazy exports; `temple <spec.json> <receipt.json> --save-copy <new.blend>`, `check-relief`; allowlist `g3d_temple` |
| `g3d_host/matte.py` | `mode="glossy_floor"` |
| `g3d_host/temple_views.py` | ROI, per-model refit, θ_k, rear family, envelopes, front bound, 2×2, sheets, `compare_temple_views` |
| `g3d_host/temple_trace.py`, `temple_fit.py` | §7 helpers; `fit_temple`, profiles, conflict and S3 tables |
| `g3d_host/relief_check.py`, `relief_rules.py` | GLB → shared measure core, photo crease verdicts, transfer; frozen thresholds |
| `tests/` | `test_temple_{spec,arrange,planes,build,measure}.py`, `test_geom_sections_loft.py`, `test_matte_glossy.py`, `test_relief_photo.py` (synthetic creases), `test_temple_views.py` (rear fit recovers a synthetic α and perspective), `test_temple_trace_synthetic.py` (S1), `test_export_temple_extras.py`, `fixtures/temple_fixtures.py`, `instruments/temple_p0.py`, `blender/temple_build.py`, `test_temple_blender.py` (skips without Blender), `test_temple_real.py` (skips without data), `proofs/wp6_prada.py` |

## 11. Open risks

1. **View disagreement on level and relief** (§0.6): STOP-C is a real possible outcome; then the owner decides with the
   evidence sheets.
2. **Cameras decide the verdict:** 03 pitch 58.75 vs 66.5 front-only; rear yaw 171.6–187.3 across fits. Mitigated by
   per-model refits and the 2×2, not removed.
3. **Facet topology is Astra's reading:** F1 may run along the arm (02); the 01 creases at y ≈ 13 and the F4/arm
   boundary are untraced; F4 tilt is unobservable.
4. **Model limits:** flat vertical inner face in the zone, single-valued top/bottom (cable temples refused), facets
   only where the base is linear, plain arm except chamfers.
5. **The hinge barrels sit ≈ 3 mm inboard in 03** (ratio measurement): a frame-level discrepancy. The fit reports it to
   Astra; the tool never edits the frame.
6. **Ear hook:** Astra's 3.8 mm tip raise went the wrong way (photo 2.1 mm lower at d 125); render-and-compare traces
   it, but d ≥ 134 is flagged.
7. **Runtime:** the acceptance pass is ≈ 1–1.5 h per product; the live Astra loop must reuse cached cameras.
8. **Not prototyped:** Bevel roll on faceted edges, chamfers, the S1 round trip, the photo verdicts on the fitted spec.

## 12. Rule text for Astra (WP7)

> **Faceted temples.** When any photo shows creases, facets or a raised panel on a temple, build that temple only with
> `g3d.faceted_temple(spec)`. Never extrude a side outline for it and never edit temple vertices.
> 1. Run `trace_temple(job, guides)`: mark the crease endpoints you see on the gridded crops; it returns the outline,
>    plan path, hinge angles and lifted creases.
> 2. Write the facet topology you see as labelled polygons over the traced nodes.
> 3. Run `fit_temple(job, spec)` and use the values it returns. A parameter it reports as UNOBSERVED keeps its default.
> 4. Call the tool, then read `check_relief` and `compare_to_photos` for all six views, including top and rear. A FLAG
>    or SHALLOW photo crease, a SLAB flag, or FRONT_BOUND is a defect: fix it through the spec. A refusal names the
>    parameter to change.
> Sides follow the x sign (+X is the wearer's left), never part names.

A WP7 test asserts that this text names `g3d.faceted_temple`, `trace_temple`, `fit_temple`, `check_relief` and
`compare_to_photos`, and that each exists.

**PLAN corrections to record:** (1) WP6 bulge band → reported with a warning; gates R1–R4 (§0.2). (2) §1 failure 1:
top and rear views see outline, plan, hinge angle and hinge-zone level, not Prada's relief (25 / 235 px); relief is
judged from creases and the front bound. (3) The rear photo is scored only through a camera + hinge-angle family.
(4) The shading flag becomes normal change across photo creases. (5) The earlier "photo top edge 0.2–1.9 mm higher"
is withdrawn (plane-lift bias), and the 5.3 mm C−B triangulation is re-measured on the model surface.
