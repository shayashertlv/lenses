# WP4 lens seating: final design

Written 2026-10-05 from three independent designs (robust **R**, minimal **M**, physical **P**), each prototyped at $0 on
copies (scratch `C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp4-design/`: `wp4proto/` = R, `wp4_mc/` = M, `pmf/` +
`bl_out/` = P). Base = **M** (simplest, strictest no-op). Grafts: R's traps, normals handling, re-wall builder, Boolean
check, fixture matrix and §3 thresholds; P's mounting classes, the hidden clamp and the arview crystal finding. Every
number in brackets is a prototype result to be re-measured by the implementation, not evidence that WP4 is done.

## 0. Verdict

Feasible and testable at $0. Strongest evidence: R's full chain in headless Blender 5.2 on a q001 copy (hide_eyewire →
seat_lens → rim sharpening → WP3 exporter → the independent open3d grid instrument on the GLB,
`wp4proto/instrument_ft_wp4_glb.json`): top whole edge inside the brow 100% (reference q1-seated-rimsplit 100%), top
mid-depth capture 0.66/0.80/0.84 mm, eyewire band 0 at every upper station (reference worst 0.20 / 0.34 mm), rim split
78.2/90.0/101.8° (identical), outline 47.40 × 36.35 mm, 74 lens + 444 eyewire vertices per side, 4.9 s. The final
design keeps that and adds P's rule that seating never changes a visible outline, which turns R's 24 Oakley moves at
rear seats into 0 (P: 12 failing Oakley stations, all backed-only, 0 grown; `pmf/generic_oakley_repair_0.3.json`).

Findings that change the plan or other WPs:
1. **The exporter drops seating metadata:** `g3d/export_glb.py:617` deletes mesh custom properties and `:625-628`
   gives the exported objects `partRole` only, so the `g3d_seat` extras all three designs rely on never reach the GLB.
   WP4 adds an allowlist (§9).
2. **arview cannot show a groove through crystal:** `arview/src/render/renderer.ts:884` lists every lens material in
   `lookThroughHidden` and `translucent-look-through.ts:73` hides them from the crystal image (WP1 follow-up A-1, §11).
3. **A tool's own numbers are not the proof.** P's core reported min brow capture 0.447 mm (3 interior depths); the
   grid instrument measured 0.39/0.40 (p5/p50) on the same output over the full rung (`pmf/measure_ft.json`). Every
   proof below is graded by an instrument the tool does not share (§10).

## 1. Principles

One numpy core (`g3d/seat/`) serves Blender (Python 3.13, numpy 2.3.4), the host and all three functions, so tool and
check cannot disagree. Only the missing depth is added, only where the result stays hidden; a passing station's
vertices stay bit-identical. Growth moves vertices; topology changes only for the crystal V-bevel and the nylor groove,
on the lens wall. Nothing is assumed about how Astra built the lens. Refuse rather than guess; plan, validate, then
write once, always with a backup (the Blender MCP has no undo).

## 2. Conflicts between the designs, resolved

| Topic | R | M | P | Decision and reason |
|---|---|---|---|---|
| Geometric query | exact plane slices | line intervals | line intervals | **Line intervals** behind a backend interface: simpler; numpy vs open3d agree to 9.6e-5 mm [P], BVH vs open3d to 1.3e-4 mm [M] |
| Stations | resampled 0.5 mm | rim vertices | rim vertices | **Rim vertices are the move nodes; measurement points every ≤0.5 mm**, each owned by the nearer node: the check covers the whole edge and moves stay vertex-exact |
| Vertices moved | wall + feature-depth band | 2 rim corners | caps within 3 mm inward | **Wall vertices + both rim loops** (= M's corners on a one-ring wall; covers multi-ring/rounded walls). P moved 1974 INVU vertices and reshades the cap |
| Growth taper | floor + ramp | strict run-end ramp | 3 mm bumps beyond failing stations | **Strict**: P's bumps move vertices of passing stations (PLAN WP4 regression forbids it); M saw a dilated taper move 1 passing Prada station |
| Pass threshold | §3 lower bound | §3 lower bound, owner asked | one 0.30 floor | **§3 lower bound for every type**: the owner approved §3; growth is hidden by construction and proven by pixel diffs; PLAN expects moves "wherever the check fails" |
| Shields | `shield` 0.30/0.45 | `shield` contact 0.0/0.10 | `semi_rimless` plain | **`semi_rimless`, `lower_edge='plain'`, 0.30/0.45**: §3's channel mount; M's contact rule re-legalises the edge-to-edge failure WP4 exists to remove |
| Rear seats (frame only behind the lens) | grown (Oakley 24 vertices) | passed via "best" depth | visible by design | **P**: never change a visible outline; BACKED stations are not grown (Oakley 0 moves) |
| Brow-sector depth | 0.60/0.80 deep | 0.45 | 0.45 | **0.60/0.80, whole edge, in brow ∪ eyewire**: PLAN says q001 must match the reference (min capture p50 0.86); R reached 0.63/0.76 (p5/p50), M 0.46/0.47, P 0.39/0.40 by the instrument |
| Browline sector | auto "deep material" | ranges | ranges | **Ranges** written by hide_eyewire: Astra's photo reading, explicit and auditable |
| Lens during hide_eyewire | follows the eyewire | fixed later by seat_lens | fixed later | **R**: each tool leaves a valid scene; without it the capture at the ramp end fell 0.40 → 0.30 mm |
| Eyewire margin / ramp | 0.10 / smoothstep 3 mm | 0.05 / slope 0.5 | 0.10 / smoothstep 3 mm | **0.10 / smoothstep 3 mm** plus a slope check ≤0.5 mm/mm |
| Eyewire move unit | per vertex | rigid rings | per vertex | **Rigid rings when the tube has uniform rings** (FT 128 × 12), else per vertex with strain reported |
| Custom normals | save all, restore all | never written | reset moved corners | **R**: moving 25 Oakley vertices naively changed unmoved corners by up to 36.8°; restoring all kept them within 0.046° (`wp4proto/out/oakley_normals.json`). No move = untouched |
| Weld | exact only | 1e-4 mm | 1e-4 mm | **Exact positions only, writes as deltas**: a 1e-4 weld collapses q001's 128-gon fan [R]; writing welded positions collapsed 68 slivers [P] |
| Cap/wall split | local-normal region grow | dihedral 20–60° | dihedral 20–60° | **Dihedral thresholds** (exact on all four lenses) **plus R's guard**: never grow into a face >75° from the local surface normal (rounded rims) |
| Rim sharpening | creased, not split | also skip if hidden | always unless split | **Creased and not split, hidden or not**: the smoothed rim normal tilts the visible outer cap ring; PLAN says sharpen. Prada changes only in that band |
| Crystal Boolean | applied, rollback | live modifier | live modifier, EXACT | **Live modifier, EXACT** (MANIFOLD fallback) **with R's volume equality check**, which catches MANIFOLD's silent no-op |
| V-bevel height | 0.10 flats | no flats (0.462) | 0.10 flats | **0.10 mm flats**: h = 0.404 mm on a 1.6 mm edge |
| Wall rebuild | re-wall builder | bmesh subdivide | bmesh subdivide | **Builder**: subdivide on a triangulated wall gave 68–103 non-manifold edges [R] |
| Nylor groove | Boolean tube, exposed part | from the cord | wall profile all round + cord | **P's all-round profile via the builder**: no lens Boolean, no groove ends; the cord is created only if none exists |
| Max growth | 2.0 | 1.5 | — | **1.5 mm**: real maxima 0.43 (Prada), 0.88 (INVU), 0.82 (FT brow after the lift) |
| Captured vs exposed | gap 0.5 (shield) | 1.0 | 1.0 | **1.0 mm**: no real station lies at 1–3 mm; R's 0.5 sits on INVU's 0.45–0.5 mm slits |
| Revert | mesh snapshot | receipt coordinates | rest attribute | **Backup mesh datablock** (fake user) + `unseat_lens`/`unhide_eyewire`: exact, also after topology changes |
| Host backend | numpy (binding) | open3d | open3d | **open3d on the host**, numpy as the reference in tests, plus the independent grid instrument |
| Edge material slot | — | — | per-lens copy | **Dropped**: a copy would diverge when Astra edits the lens material; A-1 can proxy lens materials instead |
| Depth samples | 9 | 5 | 3 | **5**, at fractions 0.05/0.25/0.5/0.75/0.95 of the rung (insets avoid corner ambiguity) |

## 3. Shared core

**Input.** World mm, `co @ M^T + t` per object (every INVU object carries (0, −0.06, −2.2) mm); deltas go back as
`M3^-1 Δ`. The lens is read from its base mesh (geometry modifiers or shape keys on a lens refuse); frame parts are read
evaluated (Oakley latch housings and INVU endpieces carry Bevels). Hidden x-ranges live in the asset frame (x minus
`mdl_bridge_underside` x), so the host can use them.

**Lens body.** Weld bit-identical positions only, for topology. A body is one closed manifold component, χ=2, effective
thickness 2V/A ≥0.3 mm; an open or thinner component is a **rider** (marking), excluded from bodies and from frame
solids (Prada etch 0.022 mm, OAKLEY 0.003, PRIZM 0.002, INVU print). **Caps:** union-find over edges with dihedral < θ,
θ = 20, 30 … 60°, never entering a face >75° from the local surface normal (least-spread direction of face centres
within 7 mm; stops leaks across rounded rims); accept the first θ whose two largest patches face opposite ways
(mean-normal dot < −0.5) and hold ≥75% of the area (expected: the built caps, FT 3196, Prada 11005, Oakley 4288, INVU
28224 triangles [R]); else `E_NO_CAPS`. **Rim loops** = cap/wall boundaries; the longest is the outer edge; inner loops
(drilled holes) are reported, never grown. **Band** = wall vertices + both rim loops; back-loop vertices follow their
rung partner (polygon edges, never diagonals: Prada has 0.019 mm rim segments [M]); multi-ring walls map by arc.

**Station frame** per measurement point: `n` = area-weighted normal of front-cap faces within 5 mm and ≥0.8 mm inside
the edge (rim vertex normals point ~80° off on a rounded rim [R]); `t` = loop chord walking ≥0.3 mm each way, ⊥ n;
`u = t × n` with the loop oriented by signed area about the mean front normal (safe in nose notches [P]); back corner =
station plane ∩ back loop nearest the front corner.

**Line intervals.** `intervals(origins, dirs, parts)` → per line and part, inside intervals from even/odd crossings over
the whole line (cast from 400 mm back). Odd counts re-cast rotated 0.05° about `t` (≤3 times), then UNMEASURED (never a
pass, never moved). Frame triangles are culled to the rim box + 6 mm (INVU's 52k-vertex nose trim). Backends:
`NumpyBackend` (reference, grid-culled Möller–Trumbore), `BVHBackend` (Blender), `Open3DBackend` (host).

**Per point** (lines along `u` at the 5 depths, along ±n from the corners): capture `c(f) = −a` for the merged frame
interval [a, b] containing the edge, else the first interval starting within REACH 3.0 mm (negative = gap); `room(f) =
b`; owner part; `cover_front` = the +n line from (front corner − 0.10·u) meets frame within 6 mm; `cover_back` along
−n. **Class** [P]: ENCLOSED (c ≥ 0 at all depths), LIPPED (c(0.5) ≥ −1.0, front covered), BACKED (frame within reach,
front not covered), EXPOSED (nothing within 3.0 mm), NEAR (gap 1.0–3.0). Sectors by the direction of `u`.

## 4. `g3d.seat_lens(lens, frame_parts, frame_type, capture_mm=None, *, lower_edge=None, translucent=None, uv='keep', dry_run=False)`

`frame_parts=None` means render-visible `partRole` frame objects minus riders. `translucent=None` = auto: a capturing
part with transmission > 0.05, alpha < 0.98 or a volume. `capture_mm` sets the target; pass keeps the type's gap (0.20
acetate, 0.15 others); outside [0, 1.5] refuses. Returns a report with status `seated`, `already_seated`, `partial` or
`refused`; geometry problems never raise. Lenses and parts may be objects or names.

1. **Pre-flight** (§3); edge thickness = median rung length; below the profile minimum → `E_THICKNESS`. Browline needs
   ranges (from `g3d_hidden_x_ranges_mm` on the lens/eyewire) or refuses `E_NO_RANGES`.
2. **Measure** every point; rule value `v` per §7 (deep = min over depths; thin = c(0.5), or the flats min(c(0.05),
   c(0.95)) on a grooved edge). Verdict: PASS if v ≥ pass − 0.005; on `semi_rimless`/`rimless`, BACKED, EXPOSED and
   NEAR are VISIBLE_BY_DESIGN; otherwise FAIL.
3. **Need** in each run of failing points: `target_i = pass + 0.05 + (target − pass − 0.05)·smoothstep(δ_i / 2 mm)`
   (δ = arc distance to the nearer run end), `need_i = target_i − v_i`. Node growth `g` = max need of its points;
   every other node gets exactly 0.
4. **Clamps**, each recorded; a clamp that leaves v < pass makes the point BLOCKED(reason): `NEEDS_GROWTH` (g > 1.5);
   `EXITS_MATERIAL` (room − g < 0.20 mm at a required depth; brow ∪ eyewire in browline ranges); `OTHER_PART` (enters a
   non-frame part or the other lens); `UNHIDEABLE` (grown front corner neither inside frame nor covered along +n with
   the 0.10 margin, after trying 0.9g…0.1g [P]); `CONCAVE` (g > 0.5ρ). On `semi_rimless` an unhideable point is
   VISIBLE_BY_DESIGN; within 3 mm of a visible-by-design point, a clamped point with c ≥ 0 and front covered is
   TRANSITION.
5. **Gates:** >20% of required stations BLOCKED → `E_TOO_MANY_BLOCKED`; full-rim types with >2% EXPOSED/BACKED →
   `E_FULLRIM_EXPOSED` ("the outline, the parts passed or the frame type is wrong"). Nothing moves on a gate.
6. **Displacement** of each band vertex: `Δ = g·u + Δn·n`, with `Δn` from a local quadric
   `z = a t² + b tu + c u² + d t + e u` fitted to that cap's vertices within 4 mm (≥12 points; widen to 6 mm; else the
   tangent plane with `W_LOW_CONFIDENCE_FIT`); front fit for front-loop vertices, back fit for back-loop vertices,
   depth-fraction blend on the wall. No fitted sphere (FT and INVU caps are toric; FT's sphere fit is off by 0.68 mm).
   Expected size of Δn: 0.013–0.020 mm at g = 1.0–1.33 [M]. Riders lying on a moved face follow it barycentrically.
7. **Correction pass:** re-measure; moved stations still below pass get the residual, at most twice (a 32-segment lens
   reached 0.69–0.73 after one pass [R]). Still failing → `partial`.
8. **Edge profile** (§5) when translucent or `lower_edge='nylor'`; corners are grown to target − h first.
9. **Rim sharpening:** skip if already split (≥95% of rim edges sharp, or ≥95% of wall faces flat, or custom normals
   split ≥60° at ≥95% of rim corners: Oakley 496/496 sharp, INVU 0/684 smooth) or rounded (rim dihedral < 30° on most
   edges). Otherwise set `sharp_edge` on both rim loops and any profile loops. FT: 256 edges per lens.
10. **Normals:** with custom normals and any moved vertex, save every corner normal first and set all afterwards
    (unmoved = saved; moved cap corners rotated with the fitted normal; wall corners = face normal); unmoved corners stay
    within 0.1° (`W_NORMALS_REENCODED`). UVs are kept, stretch reported (FT outer band 1.66×); `uv='extrapolate'` for
    image-textured lenses.
11. **Post-checks on the planned arrays, then once after writing** (failure → restore, `E_POSTCHECK`): topology
    unchanged unless step 8 ran; moved faces rotate ≤30° and keep area ratio 0.5–2.5; the outline has no reversed
    segment; no station goes PASS → FAIL; vertices outside moved nodes bit-identical; volume gain = Σ g·t_edge·ds
    ±15%; a second call moves 0 vertices and returns `already_seated`.

Writes touch only rows with Δ ≠ 0, and every raw duplicate of a welded vertex gets the same Δ. Before the first write,
`lens.data.copy()` is kept as `<mesh> | g3d pre-seat` (fake user; the earliest backup wins). The lens gets the
`g3d_seat` property: tool version, frame type, pass/target, translucent, lower_edge, ranges, mesh digests, verdict
counts. The full per-station table is only in the returned report.

## 5. Edge profiles and the crystal groove

**Re-wall builder** [R] (numpy plan, bmesh apply): delete the wall faces; for each front-loop vertex build the profile
and bridge it to the back loop by arc parameter (unequal counts work); recompute normals; cap UVs untouched.
- **V-bevel** (translucent capturing part): b = t_edge − 2·0.10, h = (b/2)/tan 60° (120°, h ≤ 0.8); front corner →
  shoulder (0.10 inside the face) → apex at mid-depth + h·u → shoulder → back corner. Apex at the target. Minimum
  edge 1.3 mm (§3).
- **Nylor groove** (`lower_edge='nylor'`): 0.60 × 0.60 mm rectangular notch centred at mid-depth, all round (the top is
  hidden in the channel). Minimum edge 1.8 mm. If no frame part lies in the groove along the exposed sector, add
  `<lens> | nylor cord`: closed 12-sided Ø0.60 tube on the groove centreline, ends 1.0 mm into the channel, partRole
  frame, material of the channel part. An existing cord is never moved; the check reports cord–lens overlap.

**Groove in a translucent frame:** cutter = the profiled lens inflated 0.03 mm along angle-weighted normals, object
`g3d_groove_cutter | <lens>` in collection `g3d_cutters`, `hide_render`, no partRole (the exporter lists it as excluded),
lens digest stored. Each frame part it intersects gets a live Boolean DIFFERENCE modifier `g3d_groove` (EXACT, viewport
and render on, before any WeightedNormal/Bevel). Evaluated post-check: closed, χ and components unchanged; removed
volume = vol(cutter ∩ frame) ±1%; vol(lens ∩ frame) ≤0.01 mm³; groove gap 0.02–0.06 mm; ≥0.5 mm of frame left on
station lines. Failure → retry MANIFOLD, then remove both, `E_BOOLEAN`. All three prototypes: closed, χ unchanged,
overlap 0.000 mm³, EXACT 0.07–0.10 s.

## 6. `g3d.hide_eyewire(eyewire, under, hidden_x_range_mm, *, lens=None, margin_mm=0.10, ramp_mm=3.0, lift_max_mm=2.5, dry_run=False)`

1. Eyewire and `under` closed. Held lens = the lens whose stations have this eyewire as innermost owner at ≥60%, or
   `lens=` (`E_NO_HELD_LENS`). Selected stations: asset x in a range AND `under` material along +u starting within
   [−1.5, 4] mm of the eyewire (excludes the loop bottom at the same x).
2. Rings: components of eyewire edges with |direction · t| < 0.5; uniform sizes → rigid rings, s = circular mean;
   otherwise per-vertex s, strain reported.
3. `λ*` per station = smallest τ ≥ 0 such that every ring vertex moved by τ·u lies inside an `under` interval shrunk
   by the margin, on lines through p and p ± margin·n. Already buried → 0 (never lowered); none ≤ lift_max → BLOCKED.
4. `L(s) = max_k λ*_k·(1 − smoothstep(|s − s_k| / ramp))`: full lift across each range, 0 within 3 mm outside it.
5. Move rings rigidly by L·u and the held lens's band by L (§4 step 6), so lens-in-eyewire capture stays within
   ±0.02 mm. Same backup/revert; writes `g3d_hidden_x_ranges_mm` on the eyewire and the lens.
6. Post-checks (failure → restore): eyewire area outside `under` 0 at selected stations; band along ±n 0; section area
   ±1% (the fix test's vertical lift sheared 1.06 → 1.13 mm² [P]); slope ≤0.5 mm/mm; nothing below the lens centre moves.

Then `seat_lens(..., 'browline')` (R: 80–81 brow stations failed after the lift, grown 0.26 median / 0.82 max, 0 left).
**Host helper** `g3d_host.seat_visual.photo_x_range(job, view, px_columns)` [M] casts the WP3b-fitted camera's rays
through those photo columns onto `under` and returns asset x mm plus the view's contour error, so Astra never guesses
millimetres. Only views within the owner's registration threshold qualify.

## 7. Defaults per frame type (§3; all overridable)

| frame_type | Required stations | Rule value | Pass / target mm (§3 range) | Exposed or backed | Profile | Min edge |
|---|---|---|---|---|---|---|
| `acetate` | all | deep: min over 5 depths (ENCLOSED) | 0.60 / 0.80 (0.6–1.0) | refused | flat; V-bevel if translucent | 1.3 with bevel |
| `metal` | all | thin: c(0.5) + front covered (LIPPED) | 0.30 / 0.45 (0.3–0.6) | refused | flat; V-bevel if translucent | 1.3 with bevel |
| `browline` | all | in ranges: deep in brow ∪ eyewire, eyewire hidden; elsewhere metal | 0.60 / 0.80 in ranges; 0.30 / 0.45 elsewhere | refused | flat | — |
| `semi_rimless`, `lower_edge='plain'` (shields) | LIPPED | thin | 0.30 / 0.45 | visible by design, never grown | flat | — |
| `semi_rimless`, `lower_edge='nylor'` | LIPPED | thin on the flats | 0.30 / 0.45 | visible by design | groove 0.6 × 0.6 all round + cord | 1.8 |
| `rimless` | none | report only | — | all visible | flat (sharpening only) | — |

Constants: REACH 3.0; captured/exposed gap 1.0; g_max 1.5; run-end ramp 2.0; material left 0.20; hidden margin 0.10;
point spacing ≤0.5; 5 depths; groove clearance 0.03; bevel flats 0.10; eyewire margin 0.10, ramp 3.0, lift_max 2.5.
Over-capture above the §3 maximum (Prada top 1.30 > 1.0) is a warning; seat_lens never shrinks.

## 8. `check_lens_seating`

**Geometric (binding).** In Blender, `g3d.check_lens_seating(lenses=None, frame_parts=None, frame_type=None,
hidden_x_range_mm=None)` (BVH, evaluated meshes) for Astra mid-session. On the host, `g3d_host.seat_check.check_glb(glb)`
(open3d) runs in `export_and_validate` after `validate_spec`, reported to Astra as a separate `seating` section (§2 of
PLAN stays the only spec). Host input: metres × 1000, exact weld per node, lenses from `lens_id.classify` + body
recognition, frame parts = closed non-lens components within 6 mm of the rim minus riders, frame type from node extras
`g3d_seat`, else an argument, else inferred and warned (>20% exposed → semi_rimless; ENCLOSED majority → acetate;
else metal).

Report per lens: counts per class and verdict; capture p5/p50/p95 per sector with the depth named; share ≥ pass and ≥
target; worst gap and worst deficit with xyz and sector; hidden share front/back; over-capture warnings; riders and
open parts excluded; minimum edge; rim split p5/p50 from the GLB normals; crystal overlap mm³ and stale-cutter flag.
**Buried-part band, always:** for every eyewire with another frame part outward along u, the band visible along ±n
(max mm, share > 0.05 mm). Inside declared ranges it is graded; elsewhere it is advisory, so q001's 0.60 mm band at
76% of upper stations is reported even before Astra declares ranges.

**PASS** when, over required stations: 0 FAIL, 0 BLOCKED, UNMEASURED ≤1%, TRANSITION ≤3% (failing points within
3 mm of a visible-by-design station with c ≥ 0 and front covered); full-rim types 0 EXPOSED/BACKED; browline band
≤0.05 mm and eyewire area outside the brow 0.00 mm² in ranges; crystal overlap ≤0.01 mm³; and, when `g3d_seat` says
the rim was sharpened, rim split p5 ≥60° in the GLB (catches an export that lost the sharp edges).

**Visual (advisory; needs WP1 stage E).** `arview capture <glb> --poses junction` (front + yaw ±30 × pitch ±20), beauty +
part-id (one palette entry per node, legend in report.json). Per captured station, labels are read along the projected
segment front corner → corner + 3 mm·u (`projectAssetPoints`): **GAP** = lens → background ≥2 px → frame; **BAND** =
lens → buried part ≥2 px → outer part. Beauty: the `band_metrics.py` white% (L > 215, saturation < 40) and median
steps > 18 levels per column. Flags go to Astra beside the photo crop at the WP3b camera; metal is never inferred from
photo pixels (the photo truth enters through Astra's ranges and frame type).

## 9. Module layout (budget: ≤5 s per lens in Blender, R measured FT 2.2 s per side; ≤10 s per GLB on the host)

| Path | Contents |
|---|---|
| `glasses3d/g3d/seat/geom.py` | numpy only: exact weld map, closed/manifold/χ/components, volume, normals, cap/wall split, rim loops, rungs, quadric fit |
| `.../seat/intervals.py` | `LineIntervals` protocol, `NumpyBackend`, merge, parity re-cast |
| `.../seat/stations.py` | `LensBody`, measurement points, station frames, `measure()` (capture, room, cover, class, owner, sector) |
| `.../seat/rules.py` | `FRAME_TYPES` (frozen dataclasses citing §3), rule values, verdicts, check verdict (shared by tool and check) |
| `.../seat/plan.py` | `plan_growth()` → `SeatPlan`; `plan_lift()` → `EyewirePlan`; clamps, gates, ramps |
| `.../seat/deform.py` | band displacement, correction pass, riders, array post-checks |
| `.../seat/profile.py` | re-wall plans (V-bevel, groove), cutter inflation, cord tube |
| `.../seat/report.py` | report schema and summaries |
| `glasses3d/g3d/seat_bpy.py` | thin bpy layer: world arrays, `BVHBackend`, delta write-back, normals save/restore, sharp edges, bmesh re-wall, Boolean + cutters, backups; `seat_lens`, `hide_eyewire`, `check_lens_seating`, `unseat_lens`, `unhide_eyewire` |
| `glasses3d/g3d/__init__.py` | lazy `__getattr__` exposing those five, so host imports of `g3d.seat` never import bpy |
| `glasses3d/g3d/cli.py` | `seat`, `hide-eyewire`, `check-seating` commands (JSON args → receipt; optional `--save-copy` to a new path, never the input) |
| `glasses3d/g3d/export_glb.py` | copy the allowlist `g3d_seat`, `g3d_hidden_x_ranges_mm` from each source object to its export duplicate (node extras) |
| `glasses3d/g3d_host/seat_check.py` | GLB → lens bodies → `Open3DBackend` → the same core; CLI `python -m glasses3d.g3d_host.seat_check <glb>`; called by `blender.export_and_validate` |
| `glasses3d/g3d_host/seat_visual.py` | junction capture analysis (GAP, BAND, white%, steps), photo crops, `photo_x_range` |
| `glasses3d/tests/` | `test_seat_geom.py`, `test_seat_rules.py`, `test_seat_backends.py`, `test_seat_fixtures.py`, `fixtures/seat_fixtures.py` (deterministic numpy builder), `blender/seat_build.py`, `blender/seat_run.py`, `test_seat_blender.py` (skips without Blender), `test_seat_real.py` (skips without data), `instruments/seat_grid.py` (port of the scratch open3d `seating.py`, tests only), `test_export_seat_extras.py` |

## 10. $0 proof plan and pass criteria

Headless Blender on copies under `glasses3d/data/wp4/` (every source's sha256 unchanged afterwards). Each case is graded
by the core AND by `instruments/seat_grid.py` on the exported GLB. Brackets: prototype result.

**P0, core vs instrument** (FT L/R, Prada L/R, Oakley, INVU, at the instrument's station frames): |Δ capture| ≤0.075 mm
at ≥99% of stations [p95 0.047–0.062, R]; front loops 138.0 / 143.6 / 383.5 / 458.1 mm ±0.1; cap triangle counts as
in §3; riders found: Prada etch, OAKLEY, PRIZM, INVU print.

**P1, consistency:** numpy, BVH and open3d within 1e-3 mm on all fixtures and INVU; Blender check on a scene vs host
check on its GLB: same verdict at every station, capture within 0.01 mm; two runs bit-identical; a second call moves 0.

**P2, FT1119 q001 browline** (copy of `3b8a…/original/scene.blend`): `hide_eyewire(Eyewire_L/R, Brow_L/R,
((−56.2, −11.6),) / ((11.6, 56.2),))`, `seat_lens(Lens_L/R, [Brow, Eyewire, Saddle_bridge, Pad_*], 'browline')`, export.
- top whole edge inside ≥98% [100%]; top min-over-depth capture p5 ≥0.55 (instrument grid 0.05) [0.63–0.64]; top
  mid-depth p50 0.75–1.00 [0.80]; the check FAILS on the input with the same ranges and PASSES after;
- eyewire band ≤0.05 mm at ≥98% of upper stations, worst ≤0.20 front / 0.34 back [0]; eyewire area outside the brow
  0.00 mm²; section area ±1%; lift ≤1.1 mm [1.023]; no vertex below the lens centre moved; outside the ranges capture
  unchanged (0.40 bottom / 0.35 nasal) and those vertices bit-identical;
- GLB rim split p5 ≥75°, p95 ≤105° [78.2 / 101.8]; outline height 36.0–36.6 mm [36.35], width ≤47.5 [47.40], perimeter
  ≤140.5 [140.2]; lens vertices moved ≤100 per lens [74], eyewire ≤460 [444]; left = mirrored right within 0.01 mm;
  validate_spec passes.

**P3, Prada delivery** (copy of `8c82…/agent/scene.blend`), `seat_lens(Lens L/R, [Front], 'acetate')`: before ≈93 of 355
nodes fail per lens (nasal x 10–14, temporal |x| 57.4–59) [M]; after: every point ≥0.60 at all depths (instrument
≥0.55), whole edge inside and front/back covered 100%, cover unchanged ±0.05; max Δ ≤0.45 mm [0.434]; moved vertices
≤200 per lens [186], all in failing nodes; volume gain within 15% of Σ g·t·ds [≈20 mm³]; etch bit-identical; GLB rim
split p5 ≥75°.

**P4, synthetic fixtures** (`fixtures/seat_fixtures.py`, built by `blender/seat_build.py`):
- **metal:** lens 50 × 38 mm, R120, t 1.6, eyewire 0.9 × 1.8 mm; capture 0.10 on the top 40%, 0.45 elsewhere, −0.30
  on the nasal 10%. After: former failures 0.44–0.47 inside runs, ≥0.35 at run ends; all else bit-identical; front
  covered 100%. With a 0.5 mm radial eyewire: every top station `EXITS_MATERIAL`, 0 moved.
- **nylor:** top 45% in a channel at 0.20, 0.6 mm cord on the bottom, t 2.0. After: top ≥0.44 on the flats; groove
  0.60 ±0.02 wide and deep, centred ±0.05; lens closed χ=2; exposed outline unchanged ±0.01; cord–lens overlap
  ≤0.01 mm³. Without a cord: created cord Ø0.60 ±0.02, ends 1.0 ±0.1 mm in the channel. t 1.4: `E_THICKNESS`.
- **crystal:** translucent frame, aperture inset 0.10, lens R120, t 1.6. Bevel 120 ±1°, h 0.404 ±0.005, apex 0.80
  ±0.02; groove gap 0.02–0.06; lens ∩ frame ≤0.01 mm³; frame closed, χ unchanged; removed = cutter ∩ frame ±1%;
  EXACT ≤2 s; an open cutter through MANIFOLD is caught by the volume check.
- **robustness matrix** (acetate ring, inset 0.10): min-over-depth p5 ≥0.78 for sphere, toric 147.9/94.25, flat
  extruded, solidified, 0.3 chamfer, flipped winding, rounded rim and coarse 32-segment lenses [0.792–0.795 on the first
  six, R]; grown front edge within 0.01 mm of the true cap. Refusals: open lens, self-intersection, Bevel modifier on
  the lens, notch `CONCAVE`, aperture 2.5 mm too large `NEEDS_GROWTH` → `E_TOO_MANY_BLOCKED`; two fused components
  are analysed separately; a drilled hole is reported, not grown.

**P5, regression on the accepted finals** (`semi_rimless`, `'plain'`). For both: the moved set equals the band of FAIL
nodes not BLOCKED, recomputed from the before-state; every other vertex and every non-lens object bit-identical; no
PASS → FAIL.
- **Oakley 3dff8674:** 0 vertices moved; lens digest, `custom_normal` and the 496 sharp rim edges identical; lens
  primitive re-exports byte-identical; check PASS (top LIPPED 0.65–0.80; nose saddle and latch housings visible by
  design) [P: 12 failing, all BACKED, 0 grown].
- **INVU c8e81c72:** ≈267 top nodes fail; 18 bottom BACKED nodes visible by design [P]. After: former failures ≥0.45
  inside runs, ≥0.35 at run ends, or TRANSITION (≤3%); grown front corners covered 100% [81% before]; max Δ ≤0.95 mm
  [0.885]; moved vertices ≤600; exact write-back through the (0, −0.06, −2.2) translation; walls flat; print untouched.
- **Stop rule:** any Oakley move, any moved vertex outside failing nodes, or a missed P2 criterion → stop and report;
  never tune constants to pass.

**P6, visual** (after WP1 stage E): q001 junction views: eyewire part-id pixels 0 in ≥98% of columns in all four
(baseline 2–3 px over 73–89%), 0 GAP, white% and steps ≤ q1-seated-rimsplit in the same run (+0.5 pt / +1). Prada,
Oakley, INVU before/after: changes >2 grey levels only within 4 px of captured outlines, no new GAP or BAND.

## 11. Open risks

1. **Visual check waits on WP1 stage E** (capture CLI, per-node part-id). The geometric proof (P0–P5) does not.
2. **Crystal grooves are invisible in arview** until A-1 renders lens materials as an opaque tinted proxy in the
   look-through pass (`renderer.ts:884`, `translucent-look-through.ts:73`). The owner accepted the FT1123 crystal
   without a visible groove; showing one changes that look, so the owner judges it.
3. **Crystal and nylor are proven only on fixtures:** no real translucent or nylor model exists.
4. **Accepted models change at hidden stations** under §3: Prada ≈26% of nodes (≤0.45 mm, inside opaque acetate),
   INVU ≈267 top nodes (≤0.9 mm, inside the rail), plus Prada's rim sharpening. To keep accepted models untouched the
   lever is one row each in §7 (acetate pass 0.30; semi_rimless pass 0.0).
5. **The exporter allowlist touches a reviewed module** (WP3's invariance snapshot must still pass). Until it lands the
   host infers the frame type and cannot grade browline ranges.
6. **Rounded rims, multi-ring walls and the correction pass** were exercised only by R's fixtures (0.638 on a rounded
   rim before its band fix, 0.693 on the coarse lens before the correction pass).
7. **INVU speed** (R 22.6 s, P 33.8 s per lens) depends on the culling and the BVH/open3d backends. Any moved lens
   with authored normals is re-encoded (≤0.05°), so it is never bit-identical outside the band.
8. **Ranges come from photos:** a wrong range hides real metal or leaves a band; `photo_x_range` is as good as the WP3b
   fit (FT front 0.66 mm mean contour error) and the registration threshold is still the owner's open question.
9. **Scratch collisions:** the prototypes shared `dumps/ft_wp4proto` and `lens_ft_wp4proto_*`; the numbers cited come
   from separate outputs (`wp4proto/instrument_ft_wp4_glb.json`, `pmf/measure_ft.json`, `wp4_mc/measure_mc_ft045s.json`).

## 12. Rule text for Astra (WP7)

> **Lens seating.** A lens is mounted in its frame, never butted edge to edge against it. Choose `frame_type` from the
> photos: `acetate` (plastic full rim), `metal` (closed metal eyewire), `browline` (metal eyewire under an acetate
> brow), `semi_rimless` (top held in a channel or rail; `lower_edge="nylor"` when a cord shows, otherwise `"plain"`,
> which also covers wrap shields) or `rimless` (drilled). Crystal or translucent rims are detected from their materials.
> 1. Browline only: where the photos show NO metal under the brow, get the x-ranges with `photo_x_range`, then call
>    `g3d.hide_eyewire(eyewire, under=brow, hidden_x_range_mm=[(x0, x1), ...])`. Outside them the eyewire stays visible.
> 2. When lenses and front are final, call `g3d.seat_lens(lens, frame_parts=[the parts that hold this lens],
>    frame_type=...)` once per lens. It grows the lens edge along the lens's own surface only where it is short and only
>    where the result stays hidden: 0.8 mm (0.6–1.0) into acetate and under a browline brow, 0.45 mm (0.3–0.6) into an
>    eyewire or channel. In crystal frames it adds the 120° V-bevel and cuts the matching groove, which show through
>    the frame. For nylor it cuts the 0.6 × 0.6 mm cord groove (edge ≥1.8 mm) and adds a cord if there is none. It
>    sharpens the lens rim. Leave `capture_mm` unset unless the product specs give a value.
> 3. Run `g3d.check_lens_seating()`: it must pass. The same check runs on every exported GLB and comes back to you.
> 4. Compare the arview junction captures (yaw ±30°, pitch ±20°) with the photos. A line, band or gap at the
>    lens–frame junction that the photos do not show is a defect; a cord, a rimless edge or an exposed shield edge that
>    the photos show is correct.
>
> Never move, scale, curl or thicken lens rim vertices by hand, and never extend an exposed edge. A refusal or blocked
> station names its cause ("needs 2.30 mm growth (limit 1.5): the aperture is larger than the lens", "the grown edge
> would leave the rim's back face"): fix that lens, frame or frame type and call the tool again. Never override the check.

A WP7 test asserts that this text names `g3d.seat_lens`, `g3d.hide_eyewire`, `check_lens_seating` and `photo_x_range`,
and that all four exist.
