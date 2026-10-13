# WP6 v3: measure, fit, see (design, 2026-10-12)

**Status: complete.** The owner closed WP6 on 2026-10-13 after paid run 8 ("you know what. im satisfied with wp6, lets wrap this").
Result and the items left open are in PROGRESS.md "WP6 v3".

Owner, 2026-10-12, after the WP6 investigation (`data/wp6v2/review_20261012/FINDINGS.md`): "please fix wp6. do very very
hard work!!! come back to me when you think we are good to go with a paid run. please do your work with the conclusions
from before. +max utilize this machine for faster runs".

This file is the design for the rebuild. WP6_FACETS.md stays the record of v2 (its §2-§4 still describe the builder
core, the seeing stack and the lab plumbing that v3 keeps). Where they differ, this file wins.

## 0. What the investigation established (the inputs to this design)

1. The real top triangle (face 4, "crown") is not one plane. Its lower edge bends outward at the logo's back corner K
   by +1.5..+2.0 mm (range 1.0..2.5 across camera models; the angled photos show a 4.1-4.4 deg kink where a plane gives
   <= 0.71). An exact-planar builder had to put that misfit somewhere, and each run's newest rule chose where.
2. Every v2 run put the crown's tip at d 85-96.5; the photos end it at d 75.5-77.2. A tip is the crossing of two
   edges; a pick that slides along an edge is invisible to a two-photo residual.
3. Crisp-corner two-photo readings are good (0.2-0.5 mm). Most 1-2 mm "camera disagreement" was picks on different
   features. Back corners carry a camera theta term (~1.3 mm per degree at d 75-85).
4. Photo 03's outer outline at d 10-40 cannot be matched together with the other eight photos under the job's camera
   model (up to +2.2 mm). The logo face measured from its printed letters is one plane (rms 0.14 mm).
5. No render light (7 tested) reproduces the photos' facet tones, even on the unedited frame. Tone in a photo marks
   where an edge is, never its size or sign.
6. Astra measures crisp points and names defects well; it misreads how faces end and which feature a pick is on, and it
   executes the newest rule or a refusal's offered remedy literally.
7. Harmful tools: corners solved from another face's extrapolated plane, single-option refusal notes, fades, the 03/06
   offset tables, render tone as a depth cue, an overlay that draws mesh folds and hides creases under 6 deg, micro
   tolerances (22 % of v2 spend went on refused calls).

## 1. Principles (each answers a finding)

- P1 (1, 3, 7) **Readings are soft, the fit is the host's job.** Astra gives readings with their error; the host fits
  all faces jointly by weighted least squares and reports every misfit in mm and sigma. Nothing is "solved" by
  extrapolating one face's plane into another's corner, and no refusal asks for 8-digit numbers.
- P2 (1) **A face may warp.** A facet with 4+ corners may be "warp": a smooth surface through its corners with straight
  edges and no internal line (mean value coordinates). Flat stays the default; the fit says when read corners do not
  fit one plane and lists the ways (warp, fold, another reading) in no order.
- P3 (2, 3) **Points come from lines.** A corner where two edges meet, and every tip, is read as the crossing of two
  traced edges in each photo; the host intersects, triangulates, and warns when an edge runs along the line of sight
  between the photos.
- P4 (5) **Geometry is judged geometrically.** Compares draw the model's own creases (by identity, every angle, with
  their angle and sign) on the photo, give a zoom at native scale, and offer a light-independent geometry view.
  Tone is never a depth cue.
- P5 (4, 7) **No misleading numbers.** The top/rear offset tables, the "start" column and the outline line leave
  Astra's view. Square-on side views print their top/bottom outline offsets (reliable). Known camera-model conflicts
  are job data in the brief, worded as camera limits, never as doubts about a photo.
- P6 (6) **Host messages are facts.** Refusals and warnings state what is so and list every way out or none, never
  one offered remedy; the prompt teaches the object and an order of reasoning, with few rules, each with its reason.
- P7 **Process.** Every change is checked against all earlier owner complaints (§8), not the last one; my own $0 tool
  tests are recorded in full.

## 2. Builder (g3d/temple: spec.py, planes.py, build.py, temple_bpy.py, report.py, draw.py) — track A

Additions to `g3d.temple/2` (the canonical form emits new keys only when present, so every existing spec keeps its
sha256 and builds bit-identically):

- Facet plane modes (spec `facets[label].plane`):
  - `"nodes"` (unchanged; flat through its node values);
  - `{"crease": [n1, n2], "rise_per_mm": s}` (unchanged; legacy, no longer offered to Astra);
  - NEW `{"coef": [a, b, c]}`: the facet's plane is X = a + b d + c y exactly (the fit's output). Its corners' values,
    where given, must lie on it within planar_tol_mm (else E_FACET_NOT_PLANAR). No conditioning check (W_CONDITIONING /
    E_ILL_CONDITIONED apply to typed nodes and crease planes only).
  - NEW `"warp"`: X over the facet = mean value coordinates (Floater 2003) interpolation of its corner values in (d, y).
    Needs >= 4 corners, a simple polygon, every corner value known (a number, "base", or a value handed on by another
    facet at that corner); otherwise E_SPEC / E_FACET_UNDERDETERMINED. Along each edge it is linear (straight 3D edges,
    so neighbours that share the corner values meet without a step); with coplanar corner values it equals the plane.
    A known value on its boundary that is not a corner must equal the edge's linear interpolation within planar_tol
    (else E_RELIEF_STEP).
- Facet key NEW `"soft": true` (default false, emitted only when true): its borders with the base are soft. The border
  band there is SOFT_BAND_MM = 6.0 wide (others keep border_band_mm), and its facet/ramp and ramp edges are smooth-shaded
  unless their built dihedral is >= SOFT_SHARP_DEG = 25. Creases between two facets are never soft.
- Planes module: every facet gets a surface object with `X(d, y)`, `grad(d, y)` -> (dX/dd, dX/dy) and `kind`
  ("nodes" | "crease" | "coef" | "warp"). Flat kinds keep `coef`; a warp surface also carries `coef` = its least-squares
  plane through the corners (display only: lean, charts) and `corners` [(name, d, y, X)]. Grep every `.coef` use
  (g3d/temple/*, agent/temple_lab/*, tests) and make each one correct for warp (exact values must use X()).
- Build: cells of a warp facet are a new cell kind `"warp"`: triangulated (the arrangement's ear-clip triangles, as base
  and faded cells), smooth-shaded with the design surface's normal at each vertex; a warp facet's edges with any other
  region are crease edges (sharp while the design dihedral there is >= CREASE_STANDS_DEG), its internal edges smooth.
  Stations inside a warp facet every <= station_step_mm as now. (Amended after phase 1: the "< 0.01 mm chord error on 2 mm
  strips" premise was false, 0.044-0.058 mm measured on a 20 x 10 mm face with one corner 1.5-2 mm off; the builder adds a
  second refinement pass inside warp facets, reports the measured `chord_mm`, and warns W_WARP_CHORD above 0.01 mm: unit
  cases <= 0.004 mm, 518 realistic random layouts <= 0.0085 mm, the run-7 layout fitted 0.0034 mm.)
  Phase-1 probe (v3/probe1.md): with run 7's layout and the photo readings, a warp crown fits its readings (warp 1.33 mm)
  and builds crown|logo 17.1 deg, logo|lower 10.5 deg, crown|shoulder 36.9 deg; the flat crown puts the misfit on the tip
  (E +2.63 mm, a 2.94 mm step at the zone end). The warp's normals turn about 10 deg between H (d 36) and K (d 47): every
  interpolation with straight edges puts the turn there (MVC and a d-ruled surface give the same slopes, scratch
  warp_compare.py), so the bright patch at K under studio_dome_v1 is the light meeting a real 10 deg turn, not the
  interpolation; the photos show the turn only as a slight brightening. The owner sheet shows warp, fold and flat.
- Fixes found in the review (each with a test):
  - one tolerance: the polygon self-check (build.PLANAR_MM, 1e-9) becomes planar_tol_mm (1e-6), so a spec planes.solve
    accepts never fails E_TRIANGULATE on its own rounding;
  - a facet|base pair's shape and dihedral in the crease tables and in `_check_creases` are the facet's design surface
    against the BASE surface at the border (not the 2 mm ramp cell);
  - E_FACET_NOT_PLANAR / E_RELIEF_STEP keep their meaning for typed specs.
- New codes: REFUSALS += "E_FIT_SHAPE", "E_FIT"; WARNINGS += "W_FACE_NOT_FLAT", "W_FIT_PRIOR", "W_FIT_OFF_READING"
  (raised by fit.py, listed here so TempleError accepts them).
- Blender layer (temple_bpy): warp cells use the core's per-vertex design normals (as faded cells' smooth fans do);
  soft borders smooth. The exported GLB must equal the core within 1e-4 mm (existing check) and keep every sharp edge.
- Report / draw: warp facets print their lean from the least-squares plane and are drawn with a "warp" hatch; nothing
  else changes.

## 3. Fit (NEW g3d/temple/fit.py, numpy only, host) — track B

`fit_spec(design: dict) -> FitResult`. The design spec is a g3d.temple/2 dict in which a node's t may also be
`{"read": {"id": str, "X": float, "sd": float, "d": float, "y": float, "photos": [str]}}` (a reading: world X of the
+X temple at opening 0, chart frame, at the node's (d, y)). Facet planes are "nodes" (flat), "warp", or legacy crease
dicts (flat, with the equality "slope across the crease = rise_per_mm"). The output `FitResult.spec` is a pure /2 spec:
every node in a facet has a numeric t; flat facets carry `{"coef": [...]}`, warp facets "warp"; everything else
(outline, inner, base, creases, chamfers, roll, options, soft, fades) is copied unchanged.

- Unknowns: X_n for every node in any facet. Observations: a reading (X, sd); a typed number t (X = x_in(d) + t, sd =
  TYPED_SD_MM 0.5); "base" is a hard value (the base surface at (d, y)); null (solve) has only the weak prior PRIOR_SD_MM
  5.0 toward the base surface at (d, y) (so every fit is well posed; a node whose posterior sd exceeds PRIOR_FLAG_MM 2.0
  is reported W_FIT_PRIOR: "set by no reading or face").
- Equalities: each flat facet's corners lie on one plane (corners beyond its widest triple are linear combinations);
  a node that lies on another facet's boundary without being its corner lies on that facet's surface (T-junction);
  legacy crease planes fix their slope across the crease. Eliminate exactly (null space); the output must satisfy them
  to 1e-9 mm so the builder accepts it.
- Inequalities: thickness X_n - x_in(d_n) >= t_min at every node; each declared crease built with its shape and at
  least MIN_CREASE_DEG 2.5 deg. A crease's angle is measured across its shared edge at the edge's midpoint: with n the
  unit (d, y) normal of the edge pointing from F1 into F2 and g_i = grad X_i . n (F2 = "base": the base gradient),
  ridge <=> g1 - g2 > 0, angle = atan(g1) - atan(g2) (deg; + ridge). Linear in the unknowns for fixed (d, y); the
  minimum-angle constraint is linearized (s1 - s2 >= (1 + s_bar^2) tan(MIN_CREASE_DEG)) and re-solved to convergence
  (<= 5 passes).
- Solve: weighted least squares with an exact active-set QP (numpy only; tests compare with scipy SLSQP on random
  problems). Deterministic. Target < 50 ms for 30 nodes.
- Declarations: the readings-only fit (no shape constraints) gives chi2_free. If the constrained fit's chi2 exceeds
  chi2_free by more than ACCEPT_DCHI2 4.0 (about 2 sigma), the fit refuses E_FIT_SHAPE (TempleError) naming, per
  declared crease that costs, its readings-only angle +- sd, the cost, and the 3 readings that move most for it (mm and
  sigma). No remedy is offered or ranked.
- Uncertainty: covariance from the reduced normal matrix at the active set; every crease's angle gets an sd; a crease is
  "certain" when |angle| > 2 sd and > FLAT_CREASE_DEG, else "open" (a declaration then sets it).
- Flatness: a flat facet with >= 4 observed corners whose residuals give chi2 > 4 per extra corner is W_FACE_NOT_FLAT:
  "face crown: its corners do not fit one flat face: K +1.6 mm (3.9 sd) from the plane of A, H, E; ways: warp (one
  face, no line), a fold (two faces), another reading of a corner" (fixed wording, ways in that fixed order, no
  ranking). A warp facet reports its warp: the largest corner distance from its least-squares plane.
- `FitResult` fields: `spec`, `nodes` {name: {X, t, sd_X, source: read|typed|base|solve, obs_X, obs_sd, residual_mm,
  residual_sd, prior}}, `faces` {label: {kind, corners, warp_mm | flat_misfit_mm, chi2, not_flat}}, `creases`
  [{pair, deg, sd_deg, shape, certain, declared, dchi2}], `chi2`, `chi2_free`, `n_obs`, `warnings` [{code, message, ...}],
  `to_dict()`, and `report_lines(limit=12) -> list[str]` (compact, <= 140 characters each, fit and face lines first,
  then crease lines, then warnings).

## 4. Readings (NEW agent/temple_lab/readings.py, host) — track C

`read_point(tb, basis, name, picks, *, kind) -> dict` and `reading_text(rec) -> str`, reusing lab.corner_reading's
model (pose, opening, mirrored temple, temple choice) with:
- kinds: `"corner"` (a pixel per photo) and `"crossing"` (per photo two traced edges, each 2..6 points; the host fits
  each edge as a line, intersects them, and uses the crossing pixel with its 2x2 covariance from the edge fits).
- 2..4 photos with a camera; refused (ValueError -> lab E_ARGS) when the pixel Jacobian's condition number exceeds 20.
- Error: pick sd per photo (`sd_px`, default 1.0 px, plus CAMERA_PX_SD 1.0 px in quadrature) propagated by the
  least-squares covariance (anisotropic for crossings), plus a camera theta term (each photo's theta moved
  THETA_SD_DEG 0.3 deg, the X change added in quadrature). Report sd_X, sd_d, sd_y and their parts.
- Warnings (fixed wording, facts only): W_ALONG_LINE (a traced edge within 10 deg of the epipolar direction to another
  photo of the reading: "a slide along this edge does not show in the residual"); W_SHALLOW_CROSSING (edges cross at
  < 15 deg in a photo: its d error is sd_px / sin(angle)); W_RESIDUAL (a residual > 3 px: "the pixels are not one
  point"); W_ONE_FIT (every camera of the reading comes from one fit: B1 is 05's camera moved, B2 02's, B3 04's: "their
  camera error is shared").
- Record: {name, kind, d, y, X, t (at d), sd_X, sd_X_pick, sd_X_camera, sd_d, sd_y, photos, uv (the pixel used per
  photo), res_px, crossing_deg, edge_epipolar_deg, cond, warnings, model_t}. Readings do not depend on the model except
  through x_in (t) and the start point.
- `pick_sheet(basis, rec, out_path)`: one image per reading, a crop of each photo around its pixel with the picks, the
  traced edges and the reprojected point drawn (so Astra sees which feature it picked).

## 5. Seeing (agent/seeing.py, g3d_host/compare.py, g3d/render.py as needed) — track D

- `compare_to_photos(..., zoom=None, model_edges=None)`:
  - `zoom` = [{"rid", "box": [x0, y0, x1, y1] native px, "geometry": bool}] (<= 3): per item one image, a 2 x 2 grid
    (photo | render) / (photo + model outline + model edges | geometry render or the render with edges), each panel the
    box at scale k = min(ZOOM_MAX_K 2.0, 760 / w, 500 / h) (never below the box's native size when it fits), render at
    that scale (no upscaled render). Target photos: native_linear render; other colours: gloss_black. The geometry
    panel is the existing "normal" pass. Text: the box, k, px/mm shown.
  - `model_edges` = [{"pair": "crown|logo", "shape": "ridge" | "valley" | "flat", "deg": float, "pts": [[d, y, X], ...]}]
    in the +X temple's chart (the lab sends them from the build); seeing poses them per photo and side (the mirrored
    temple = side -1), projects with occlusion by the render's depth, and draws THEM instead of mesh dihedral edges in
    every overlay: ridge orange, valley blue, flat grey; 2 px from 6 deg, 1 px below; in zoom panels each edge is
    labelled with its angle ("16" / "-3") at its longest visible run. Without model_edges the old mesh rule is kept
    (owner sheets, old tests).
  - square-on side views (view_kind "side"): `outline_offsets_mm` {charted|mirrored: {top: [{d_mm, offset_mm}],
    bottom: [...]}} every 10 mm, photo minus model along the image's up direction (top) and down (bottom), from d 10 to
    the tip; stations where the frame or the other temple covers the edge are null.
  - fix: the stack scale uses the rows actually stacked (agent/seeing.py:1195).
  - passes per photo: target photos native_linear only (no gloss_black row); other colours gloss_black only.
  - the top/rear `outer_edge_offsets_mm` are still computed (owner sheets) but the lab no longer prints them.

## 6. Lab, tools, state, brief (agent/temple_lab) — tracks E1-E3

Tools (strict JSON, the run's registry; facet_restore and photo_compare `depths` removed):

| Tool | Does |
|---|---|
| facet_inspect | head: design values, fitted values, readings table, the object note, fit report, creases, chart image (facets labelled) |
| photo_image | a photo or native crop, optional `enhance` (contrast stretch) and `scale` up to 3 for small crops |
| photo_read (NEW) | named readings: `points` [{name, kind: corner/crossing, picks: [{photo, uv \| null, edges: [[[u, v], ...], [[u, v], ...]] \| null, sd_px \| null}]}] (<= 8), `drop` [names]. Stores each reading (replacing one of the same name), returns its text and a pick sheet image per reading |
| photo_compare | photos, crop temple/full, `zoom` [{photo, box, geometry}] (<= 3), points_px; model edges drawn by identity; side views print outline offsets |
| facet_edit | as v2 plus `object` (text <= 3,000: the faces, corners, edges with shape and evidence, where the faceting ends, what is uncertain; required on the first facet_edit and whenever the layout changes); edits: set_node t {mode: reading \| mm \| base \| solve} (reading: the stored reading of the node's name; d_mm/y_mm null = the reading's), set_facet {plane: flat \| warp, soft: bool} (no fade, no crease/rise), set_crease, delete_*, splice/offset chains, set_scalar |
| facet_variants | as v2; panels cropped to the charted temple at native scale (<= 2), a clean render panel beside the edged one; each arm's fit lines |
| facet_finish | preconditions (below) |

- Edit flow: apply_edits -> design spec (revision file, sha of the design) -> fit_spec -> fitted spec
  (`revisions/rNNNN.fit.json` with the FitResult) -> build_temple. `Lab.design(rid)` / `Lab.spec(rid)` (fitted) /
  `Lab.build(rid)`. The worker receives the fitted spec only. E_FIT_SHAPE and fit warnings are returned like builder
  refusals and warnings.
- Receipt order: fit lines (fit_lines), then crease lines (built angle and the fit's sd, declared shape), then builder
  warnings, solved moves vs head, crease_vs_head, diff_vs_head. A typed t on a node that has a stored reading of its name
  prints "typed E 6.50: its reading 5.27 +- 0.40 (+3.1 sd)" (W_FIT_OFF_READING) as a fact.
- Removed from Astra's view: fades, crease+rise planes, solve-option notes, refusal recipes, the 03/06 offset tables,
  the "start" column, the outline line, W_CONDITIONING, lean_deg, section strip, inherited revision-0 warnings,
  facet_restore, one-photo depths.
- Judgements: up to 3 entries per photo (the 3 largest differences), each with (d, y).
- Finish preconditions: (1) >= K distinct applied heads (unchanged); (2) one facet_variants call with >= 3 arms
  pairwise >= 0.3 mm (unchanged); (3) best_checkpoint compared at every target photo and every other-colour photo with a
  camera; (4) an object note exists; (5) stuck: since the latest applied head, a facet_variants call judged, and the
  last 3 judgements cover 3 distinct heads with none "better".
- Brief (cli.brief_text): as v2 plus camera lineage (B1 <- 05, B2 <- 02, B3 <- 04: transported, theta copied), reading
  precision by point type, the job's camera-model conflicts (job data, `CAMERA_CONFLICTS`), the light's behaviour
  ("the render light does not reproduce the photos' tones; in the photos a tone step marks where an edge is"), and the
  scope (the whole temple to the tip: outline chains are editable).
- Prompt: rewritten by the coordinator after the tools exist (target <= 9,000 characters, raised to 9,500 on
  2026-10-13 for two sentences the blind stand-in test showed missing; teaches the object and an
  order of reasoning; no product facts). The test pins tool names, banned retired phrases and the cap only.

## 7. Proofs ($0) before any paid run

1. Unit suites of every track; the full suite from the repo root (`-n 10 --dist loadfile`).
2. T5 rehearsal (MockTransport, real Blender) covering every v3 tool, refusals and finish.
3. TOOL TEST from the photos: on a package copy, the coordinator acts as Astra through the v3 tools only (readings by
   pixel picks, layout, warp/flat choices, compares), from revision 0, and records every call. Never a deliverable.
4. Owner sheet: photo | tool-test warp | tool-test fold | tool-test flat | run 7 | preferred, at B1, B2, 05, 02, 03,
   04, 06, plus the 3D viewer; the complaint checklist (§8) filled in for each.
5. Adversarial review of the whole diff (several lenses), fixes, re-review.
6. Cost estimate; the owner's OK.

## 8. Owner-complaint checklist (every earlier complaint, checked on every candidate)

| Run | Complaint | Check |
|---|---|---|
| v1 | still not good | crown|logo a ridge; faces 2 and 3 separate; nothing cut at a zone end |
| 2 | red line missed | the 2|3 crease (K-D) exists |
| 2 | top triangle barely noticeable / not pulled out | crown|logo and crown|shoulder ridges at the photos' angles (crown-geometry, fresh picks: about 18 and 37-40 deg; target.md's 15-25 / 21-37 came from an off-crown A and a short tip) |
| 2 | unwanted line from the logo apex | no crease the photos do not show (overlay at B1, B2) |
| 2 | lines very soft | every facet/facet crease sharp in the export; no fades |
| 3 | top triangle not recognized | crown one face to its tip, tip at d 75-78 |
| 4 | split into two | no crease inside the crown |
| 5, 6 | inward edge | crown|logo a ridge; logo|nose not a visible valley (|angle| < 3 deg or a ridge) |
| 6 | smoothened, not one piece | no fades; crown one face |
| 7 | not good (unnamed) | logo|lower >= 6 deg (photos ~10); no hard dark join at the facet zone end; arm outline within 1 mm in the side views |
