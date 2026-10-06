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

## Implementation notes (stage "core", 2026-10-05; review fix rounds 1 and 2)

Landed: `g3d/seat/{geom,intervals,stations,rules,report}.py`, `g3d_host/seat_check.py` (Open3DBackend, `check_glb`,
CLI; called by `blender.export_and_validate` as a `seating` section), `tests/instruments/{seat_grid,seat_p0}.py`,
`tests/fixtures/seat_fixtures.py`, `tests/test_seat_{geom,rules,backends,fixtures,real,check,instrument}.py`. Proof
numbers: `glasses3d/data/wp4/p0_p1.json`. Deviations from the text above, each measured:
1. **Classes.** LIPPED = c(0.5) ≥ −1.0 and the FRONT held (covered, or frame at depth 0.05 within 1.0 mm); BACKED =
   front not covered and frame only at the back; NEAR = the rest within reach. Read literally, §3 makes INVU's
   top-rail gap points (184; frame at every depth, neither face covered) BACKED/NEAR, i.e. visible by design and
   never grown, against P5 and §2 'Shields'. The relaxation only widens the **semi_rimless required set** (all 549
   INVU top points; Oakley's 173 rear-seat points stay BACKED). The full-rim thin rule (metal, browline outside its
   ranges) still needs the front corner covered along +n or the station ENCLOSED, as §7 says: a V-ridge eyewire
   with no front lip (cap −0.28 / 0.00 / +0.35 / 0.00 / −0.38 by depth) is 348/348 LIPPED and FAILS as metal.
   FT and Prada have no uncovered required station, so their verdicts are unchanged.
2. **Capture from the lens surface:** the lens is the backend's last part and each depth line's edge is where it
   leaves the lens, not the corner-to-corner rung (a chamfer measures its 0.30, a bullnose its 0.80; R's `cap` did
   the same).
3. **Culling per closed component**, never per triangle (a triangle cull opens the mesh). INVU check 3.4 s.
4. Bodies record χ instead of requiring 2 (a drilled lens is genus 1). Slivers below 1e-6 mm² join their cap: FT caps
   are 3198 = every fan triangle (R's 3196 dropped 2).
5. Exact coincidences in synthetic fixtures: cover lines start 0.10 + 1e-4 mm inside, class thresholds allow 1e-4 mm,
   band columns sit at half-column offsets, interval merge tolerance 1e-4 mm.
6. **Band candidates by membership, not innermost** (review round 2). Held parts = innermost mid-depth owner at
   ≥60% of the owned stations OUTSIDE the hidden ranges (§6 step 1). After the lift the brow is innermost inside
   them: on q001 the eyewire's share over all stations would fall from 0.94 to 0.61, and a crude 1.0 mm in-memory
   lift gives 0.626, with the eyewire innermost at 5 of 117 in-range stations. A station is a candidate when a held
   part has material on the mid-depth line within the §6 window [−1.5, 4] mm of the edge, and another frame part in
   the window reaches farther out (by >1e-4 mm), whichever of the two starts first. The brow is never held, so FT's
   brow-under-saddle corner stays out. Band columns run −2…+4 mm (the instrument's u range).
   FT input: Eyewire under Brow only, 144 stations per lens (117 in the P2 ranges, all graded). Front p50 0.60, max
   0.90 mm, >0.05 at 70%; back p50 0.775; area outside p50 0.90 mm². The designers' `ft_eyewire_hidden.py` is
   ported (`seat_grid.eyewire_band`): 49 upper stations per lens, front band at 75.5%, 0.60 median / 0.78 max, back
   0.80 median (their numbers). At those frames all 49 are core candidates and agree within 0.025 / 0.02 (L) and
   0.03 / 0.03 mm (R), front / back. Round 1's innermost rule skipped 11 of them (x 11.6–19.5).
   The crude lift: 117/117 graded (front 0.175, back 0.375 mm left by its shear). The review's probe (brow innermost,
   eyewire 0.2 mm proud of its back face) now FAILS: back 0.70 mm, area 0.14 mm², the instrument's numbers.
7. **Components.** Host frame parts = closed components of `partRole` frame nodes (all non-lens nodes when a file
   has no roles); the 0.3 mm thickness is for LENS bodies only. A frame component is excluded only as a marking:
   closed, 2V/A < 0.05 mm (`MARKING_MAX_MM`; markings measured 0.002–0.028, thinnest real frame solids 0.12–0.19)
   and lying on a lens; the nylor cord (0.29) and a 0.55 mm wire (0.27) are frame parts. On the real files this adds
   FT's T-returns, T-verticals and pad stalks and Oakley's pad grips and icon seats as solids, with no class or verdict
   change. A lens-node component that is neither a body nor a marking on one fails the file (`E_OPEN`, `E_THIN`;
   `E_EMPTY` for a lens node with nothing usable).
   Review round 2 adds three failures, all naming the node:
   - An open frame component within 6 mm of a rim box fails the file (`E_OPEN_PART`, with its boundary-edge count),
     and that lens is not graded. §8 only lists open parts as 'excluded'. Dropping one turned the stations of a
     0.10 mm channel with uncapped ends into EXPOSED (VISIBLE_BY_DESIGN), so a semi_rimless lens PASSED on a clip.
   - A skipped lens primitive fails the file: `E_UNREADABLE`, or `E_EMPTY` when its node has no other lens
     geometry. Before, a Draco or LINES Lens_R vanished and the file passed on Lens_L.
   - An undecoded frame surface fails the file (`E_UNREADABLE_PART`): compressed or broken, triangle mode, and
     within 6 mm of a rim by its POSITION min/max box, or of unknown position. Frame points/lines only warn.
   None of the four real GLBs has any of these, so no real verdict changes.
8. **No vacuous pass.** A non-rimless type with 0 required stations fails. Inference needs an ENCLOSED/LIPPED station
   (none → frame type unknown, not graded, FAIL: a lens with no frame, or pushed 5–6.7 mm out of its ring). An
   inferred type is graded provisionally (`graded_pass`) but `pass` and `ok` need a declared type (`g3d_seat`, or
   the argument), so until the exporter allowlist lands an export's seating section says ok = false with "frame
   type not declared" unless the caller passes `export_and_validate(..., seating={"frame_type": ...})`.
   UNMEASURED stations count as required on semi_rimless (§8's 1% limit applies).
9. Report: sector values are summarised per rule (browline: "in_ranges" min over depths vs "outside_ranges"
   mid-depth); a station row's `node` is the owning rim vertex as a body-vertex id (`Frames.loop` holds the oriented
   loop; `Frames.node` indexes it, not `body.fl`).
10. Not computed yet: crystal overlap and the stale-cutter flag (null with a note on a translucent capturing part).

**P0** (at the instrument's stations; share of stations within 0.075 mm). *Graded* (binding: the capture check_glb
grades vs the instrument's occupancy-only §3 measurement, run boundaries bisected to 5e-5 mm): FT L/R 100%, Prada
L/R 100%, INVU 100%, Oakley 99.74% (1 station: a 0.021 mm latch-housing sliver between grid samples); |Δ| p99
≤ 1e-4 mm. *Designers' definition* (core intervals converted to the port's own capture, as R compared): FT L 99.28%,
FT R 100%, Prada 100%, Oakley 99.74%, INVU 100%, p95 0.044–0.048 mm. Loops 137.983 / 143.615 / 383.465 / 458.062 mm;
caps 3198 / 11005 / 4288 / 28224; riders: Prada etch, OAKLEY, PRIZM, INVU print. **P1** host: numpy vs open3d
< 1e-4 mm on all four GLBs (max 9.8e-5), within the 1e-3 test bound on every fixture; classes, owners and band
candidates identical, 0 re-casts; two check runs byte-identical. Before seating (declared types): Prada 94/355 failing nodes per lens, INVU 268/684, Oakley PASS,
FT browline with the P2 ranges 31/32 failing nodes plus the graded band (front max 0.80 mm).

**Open, owner decision: two capture definitions.** The designers' instrument counts frame material lying INSIDE the
lens body without reaching its edge as capture (first material from u = −4 mm); §3 (and R's graded `cap`) counts
only material containing the edge, else the gap to the next. Core-§3 vs the port's own definition: FT L 99.28%
(1 station), FT R and Prada 100%, Oakley 70.31% (114 of 384 stations; of 122 depth values 111 are such detached
material, 115 at depths 0.75/0.95, 1 at mid-depth), INVU 98.47% (7, all detached, depths 0.05/0.25). The §3
reading also jumps at knife edges: 13 Oakley back-depth rows have the channel ending 0.004–0.038 mm short of the
lens edge, read as gaps of 0.54–1.14 mm to the brow (the port reads 0.60–0.90 captured). Today's verdicts are
unaffected (Oakley and INVU are graded by mid-depth thin rules); deep-rule verdicts (acetate, browline in ranges)
on interpenetrating frames would differ.

## Implementation notes (stage "tools", 2026-10-05)

Landed: `g3d/seat/{plan,deform,profile,check}.py` (check.py = the §8 check over nodes, moved out of
`g3d_host/seat_check.py`, which now reads the GLB and delegates, so Blender runs the identical grading),
`intervals.Stacked`, `stations.band_widths` (extracted from `buried_band`), `g3d/seat_bpy.py` (BVHBackend, write-back,
normals, sharp edges, bmesh re-wall, EXACT Boolean + cutters, backups; the five tools), lazy exports in
`g3d/__init__.py`, `g3d/cli.py` `seat` / `hide-eyewire` / `check-seating` / `run` (`--save-copy` to a new path only,
`--export`), the exporter allowlist (`SEAT_EXTRAS`), tests `test_seat_tools.py` (host, 20), `test_seat_blender.py`
(52 + 1 strict xfail), `test_export_seat_extras.py` (2), `tests/blender/{seat_build,seat_run,seat_extras_export}.py`,
fixtures `browline_scene` and `notched_outline`. Numbers: `glasses3d/data/wp4/p1_blender.json`.

**P1, Blender half (passed).** BVH vs numpy and open3d at Blender's own frames: max |difference| 8.4e-5 mm over 13
fixture lenses (12 scenes) and INVU (1494 points); classes, owners and re-casts identical. Blender check on the scene
vs host check on the GLB the WP3 exporter writes from it: 0 verdict differences at every station, captures equal to
the reports' 1e-4 mm rounding, on the 12 fixture scenes, FT / Prada / Oakley / INVU and after seating (metal, the
transformed pair, the browline fixture, FT P2). A second seat_lens call moves 0 vertices and returns already_seated
(metal, both pair lenses, custom normals, browline, INVU); the tool also runs that second call itself after every
write and rolls back otherwise.

Deviations from the text above, each measured:
1. **BVHBackend:** one BVHTree over every part with a triangle -> part table; a (line, part) with an odd count is
   recomputed exactly (float64 Moller-Trumbore, the numpy test). BVHTree is not watertight (Oakley: 1 odd line of
   6216, room off by 6.7e-3 mm after the 0.05 deg re-cast); its `epsilon` = 1e-6 made 379 Oakley / 1209 INVU lines odd.
2. **Clamp reasons.** Two not in §4: NO_FRAME (a required depth has no frame within REACH) and a node that owns a
   VISIBLE_BY_DESIGN or UNMEASURED point never grows (reported as UNHIDEABLE: it would extend an exposed edge). A
   blocked point is named by the first clamp in §4's order that alone blocks it (an aperture 2.5 mm too large is
   NEEDS_GROWTH although its grown corner is also unhideable). Blocked points add no growth; a node never grows more
   than the smallest limit of any point it owns. OTHER_PART keeps 0.10 mm (the hidden margin; §7 gives none).
3. **CONCAVE** radius from the circle through the loop points 0.3 mm (§3's chord) before and after the node; 1.0 mm
   read a 0.54 mm notch radius as 0.88.
4. **Rounded rims:** on a bullnose the first facet (11.25 deg) joins the cap at theta = 20, so the rim loop is one ring
   out; growing the loop alone tilted that facet and a fresh split moved the loop (second call p0 0.29, growth again).
   Cap vertices within the 0.8 mm normal inset whose faces deviate from the station normal by more than theta/2 move
   with their node; quadric fits use cap vertices outside the band. Sphere caps deviate ~1 deg: nothing changes on
   FT, Prada, Oakley, INVU or the other fixtures.
5. **Correction pass:** residual = the node's planned target - v at still-failing points of moved nodes (<= 2
   passes); the last re-measure is reused for the final, fresh plan (what a second call computes).
6. **Normals:** moved cap corners rotate with the quadric normal; moved wall corners take the face normal; Blender
   then marks the split rim edges sharp itself (custom-normals fixture: unmoved corners within 0.0052 deg).
7. **hide_eyewire:** (a) lens nodes take the ring lifts interpolated linearly at their arc parameter (the eyewire
   between rigid rings is that blend); (b) rings move along the lens surface's own path, L u + dn(L) n with dn from the
   §4 step 6 cap quadrics, and lambda* is solved along that path (fixed point; L u alone shifted q001's
   lens-in-eyewire capture by -0.009 mm everywhere); (c) that capture is compared along the pre-move u (on q001's
   temporal ramp end the post-move frame rotates ~20 deg: -0.022 mm read, 0.006 along the move); (d) a ring short of
   the 0.10 mm margin by <= 0.005 mm (the verdict tolerance) is buried: a second call re-tests along the moved lens's
   frames and found 0.0005-0.0014 mm.
8. **Writes:** a per-call rollback copy plus persistent backups of two kinds (`| g3d pre-seat`, `| g3d pre-hide`), so
   unseat_lens after hide_eyewire keeps the lift; unhide_eyewire also drops the lens's pre-seat backup and g3d_seat.
   g3d_seat `sharpened` is true only when the tool marked the rim (an already split Oakley rim, 47-92 deg, would fail
   the check's p5 >= 60).
9. **Profiles** need one quad wall ring with a rung for every rim vertex, no duplicate rim vertices and no custom
   normals (else E_PROFILE_WALL / E_PROFILE_NORMALS); a profiled lens refuses a second call (E_ALREADY_PROFILED).
   `uv='extrapolate'` is refused (not built); UV stretch is not reported.

Proof-stage previews (the binding P2-P5 grading by the grid instrument is the next stage):
- **P2 FT1119 q001 in Blender:** hide_eyewire lift 1.0286 mm, section area +-0.8%, slope 0.444, band 0, second call
  already_hidden; browline seat 58 lens vertices per side, top 103-104 points all ENCLOSED, min-over-depth p5
  0.678 / 0.696, mid-depth p50 0.808; GLB rim split p5 78.1, p95 101.9 deg; both checks PASS; validate_spec passes.
- **P5:** Oakley 0 vertices moved, lens digest identical, check PASS. INVU seated: 268 nodes / 536 vertices, max
  0.883 mm, all 699 required points PASS, second call 0; 7.4 s alone (20.8 s in the 6-way parallel test, budget 5 s).
- **P4 fixtures (host):** acetate matrix min-over-depth p5 0.7945-0.7997 on all 8 constructions; metal former
  failures 0.372-0.449 at their nodes (median 0.449), 130 vertices moved; crystal V-bevel h 0.4041, EXACT 0.25-0.35 s, removed = cutter ^ frame
  (136.084 mm3), lens ^ frame 0.0, gap 0.028-0.029, frame closed with chi unchanged; nylor groove with the existing
  cord, a created cord overlapping the lens by 0.0053 mm3; t 1.4 E_THICKNESS.

Open, for the owner/designers (not tuned):
- **A. Prada (P3) is refused** by §4 step 11's 30 deg face-rotation limit: 3 wall slivers 0.044-0.046 mm wide rotate
  54.9 / 54.9 / 56.5 deg (area ratio 1.72-1.80, inside 0.5-2.5) because node growth (the largest need of the points a
  node owns) changes by 0.064 mm across a 0.046 mm rim segment. The strict per-node taper and the rotation limit cannot
  both hold on Prada's 22 sub-0.05 mm segments. Options: exempt wall faces from the rotation limit, grade the
  rotation on caps only, or let growth taper across sliver segments.
- **B. The §8 check fails a grooved crystal lens** (352 of 352 points LIPPED, FAIL): the 0.03 mm groove clearance
  reads as a 0.028 mm gap at every depth under §3's capture definition (the open "two capture definitions"
  decision). The check still does not compute the crystal overlap.
- **C. P4 narrow eyewire** (0.5 mm web): room 0.40 - 0.20 leaves exactly pass (0.30), so 171 of the 175 failing
  points are clamped to pass, not blocked (1 EXITS_MATERIAL, 3 UNHIDEABLE); the planned growth then rotates a wall
  face 30.1 deg and the call is refused (E_POSTCHECK). §10 expects every top station EXITS_MATERIAL and 0 moved.
- **D. A 0.54 mm notch:** CONCAVE blocks the notch points, but full growth beside them would fold the rim; the call is
  refused (E_POSTCHECK, message names the blocked points), never written. Any blocked point inside a failing run
  makes such a step.
- **E. Speed:** INVU seat 7.4 s, Oakley check 6.8 s (buried-band columns over 23 parts).

## Implementation notes (stage "tools", review fix round 1, 2026-10-05)

Numbers: Blender suite `tests/test_seat_blender.py` 57 passed + the Prada strict xfail (78 s); host suite 494 passed,
1 skipped, 2 xfailed. Scratch numbers and the q001 / INVU copies: `glasses3d/data/wp4/fix1/`.
1. **hide_eyewire section area** is now the exact plane section of the eyewire mesh (shoelace over the triangle
   cuts, window u -2..4 mm, |n| <= 6 mm) in the pre-move station planes carried along by the move
   (`deform.carried_frames`). The old figure had two artifacts: the post-move frames turn up to ~24 deg on a ramp, and
   column sampling (0.025 mm) moves a whole 1.8 mm column across a wall, 0.045 mm2 = 4% of q001's 1.06 mm2. The
   review's seven q001 ranges refused at 2.9-4.1% now read 0.02-0.04%; **P2: 0.15% (L) / 0.14% (R)** (the 0.8% in
   the stage notes was the artifact).
2. **Band at every station.** The post-check grades every selected point (carried frames) AND the check's graded
   set on the moved scene (in-range band candidates, `stations.buried_band(..., only=)`), front/back 0 and area
   < 0.005 mm2. Rings lifted: those nearest a selected station plus every ring holding an end of an eyewire edge
   that spans one (`plan._covering_rings`; a blocked one names the range end). q001 Right [11.6, 30.0]: before,
   'lifted' with 2 stations (x 29.38, 29.82) at back 0.375 / front 0.150 mm, area 0.263 mm2; now lifted with 0 at
   all 51 graded stations, browline seat 18 vertices, Blender check PASS. Without the spanning rings the fixture's
   own range left 0.05 mm at x -45.80 (the check's limit, so it passed); the post-check now refuses that.
3. **Lens follow correction (§6 step 5).** On steep ramps the rings move along their own u, so the eyewire face at a
   node moved a few um off the interpolated ring lift: q001 [-50, -36] read +0.0221 mm (> 0.02) at the ramp foot
   x -51.8 and was refused. One linear correction (g - dc, measured in the carried frames) brings every tried range
   to <= 0.0009 mm. All ten tried q001 ranges (the review's seven, P2 L/R, [11.6, 30.0]) now lift, a second call is
   already_hidden, browline seating is seated and its second call moves 0, and the check passes (host, open3d, on
   the q001 arrays; [11.6, 30.0], [-56.2, -40] and P2 also in Blender).
   **P2 in Blender:** lift 1.0286 mm, slope 0.445, band 0 at 115 graded stations, 444 eyewire + 37 lens nodes (74
   vertices) per side (was 432 / 36), nothing below the centre; seat 58 vertices per side; both checks PASS; GLB rim
   split front p5 78.1, back p95 101.9 deg.
4. **unhide_eyewire** restores the eyewire and the lens recorded in its `g3d_backups.held_lens` (or `lens=`, which
   must be partRole 'lens') and nothing else; hide_eyewire refuses a lens that is not partRole 'lens' or differs
   from the recorded one. Report key `eyewire_restored` (the `eyewire` key keeps the name). Two-eyewire fixture
   `browline_pair`: unhiding Eyewire_L leaves Eyewire_R / Lens_R geometry, g3d_seat, ranges and backups identical.
5. **Declared ranges = union** of every call's (`rules.merge_ranges`, `ranges_effective` in the report); the
   post-check grades the union.
6. **Pass floor (owner decision open).** `capture_mm` that would put pass below the §3 lower bound is refused
   (acetate needs >= 0.80, metal / browline / semi_rimless >= 0.45: capture_mm can only raise the bar), and the check
   clamps an extras-supplied pass to that bound with a warning. A 0.15 mm gap lens declared {pass -0.20, target 0}
   now FAILS. §4's "pass keeps the type's gap" and §8's "frame type from g3d_seat" disagree here; the owner should
   say whether capture_mm may lower pass at all.
7. **Profiled lenses** (V-bevel, groove): original vertices verified after the re-wall (stored = planned, unmoved
   bit-identical), then the binding check runs; a failure returns `partial` + `W_CHECK_FAILS` and g3d_seat status
   partial. The crystal fixture is now `partial` (352/352 FAIL, open item B); nylor stays seated (check PASS).
8. **Speed (corrects "INVU 7.4 s alone" above):** the stage measured 20.8 s only in the 6-way parallel run; the
   review measured 25.0 / 19.8 s standalone. 10.8 s of a 17.0 s profiled call was `geom.nearest_on_polyline` over
   every lens vertex (`_rounded_zone`, `station_frames`). With a grid-limited search (`max_dist`, bit-identical
   results, verified on INVU frames / band and the fixtures) and the rounded-zone test restricted to faces near the
   rim: **INVU writing call 10.0 s standalone** (11.3 s in the parallel suite; second call 4.1-4.8 s; dry run 10.1 s
   as the first call). Still 2x the 5 s budget: station_frames ~1.2 s x 3 calls, measure ~0.8 s x 3, and the
   in-tool second call 3.3 s.
9. `g3d.__getattr__` turns the host ImportError into AttributeError (hasattr is False); `g3d.SEAT_TOOLS` lists the
   five names without bpy. `check_lens_seating` takes a single name for lenses / frame_parts, and its error dict
   carries `reasons`.

## Implementation notes (stage "tools", review fix round 2, 2026-10-06)

Numbers: Blender suite `tests/test_seat_blender.py` 64 passed + the Prada strict xfail (160 s, 6 Blender processes);
new host tests `tests/test_seat_review_fixes2.py` (9). Scratch outputs: `glasses3d/data/wp4/pytest_r2/`.
1. **Halfway points** (`stations.Frames.owners`, `plan.halfway_growth`). A segment split into an even number of
   points has one exactly at w = 0.5; stage 1 gave it to the segment's START node, and a mirrored lens's oriented loop
   runs the other way. Measured with that rule on FT q001 after the P2 hide (host, Blender's arrays): left vs mirrored
   right up to 0.0542 mm (16 vertices > 1e-3, 6 > 0.01); the metal fixture seated while its mirror was refused
   (35.5 deg face rotation at the nasal run end). Now a halfway point belongs to both ends: its limits cap both
   (node caps, the point's cap is the tighter), it holds neither (a visible-by-design or UNMEASURED point holds the
   node it is NEARER to), and its growth is taken in full by an end with no growth of its own, else met by the mean
   of the two ends (both raised by any shortfall); also in the correction pass and the PASS -> FAIL margins. Rejected,
   measured on the fixtures: full growth to both ends everywhere (the metal -0.30 / 0.10 junction node read 0.611,
   beyond §10 P4's 0.44-0.47), the mean alone (metal refused on both sides, 37.0 deg), holding both ends (the nylor
   fixture's channel ends stop short: TRANSITION 6/161, check FAIL). Results: **P2 in Blender, left = mirrored right
   within 1.7e-5 mm** (the input pair is exact); metal 132 vertices on both sides (130 before), former failures
   0.370-0.455; nylor 110 (112), check PASS; INVU 538 vertices / 269 nodes, max 0.885 mm (536 / 0.883), check PASS;
   FT 58 per side, both checks PASS, GLB rim split front p5 78.1; Oakley 0 moved; acetate matrix p5 0.7945-0.7997.
2. **The tool's status carries the check's verdict** (`deform.state_check`): rules.check_verdict over the fresh plan on
   the result (FAIL, BLOCKED, UNMEASURED > 1%, TRANSITION > 3%, full-rim EXPOSED/BACKED). A failure makes seat_arrays
   `partial` with W_CHECK_FAILS and the check's reasons (`bodies[i].check`). A captured type with no required station
   is refused **E_NOT_HELD** before planning (semi_rimless with no part, or the channel 6 mm away; nylor_nocord with
   frame_parts=[] in Blender, nothing written). New fixture `short_channel` (channel over the top 20%): every FAIL
   grows but TRANSITION 4/67 > 3%, so the tool says partial and the Blender and host checks fail with that reason.
   A profiled lens keeps the plan's status (`plan_status`) until the binding check after the profile.
3. **Custom normals on a sharpened rim.** A sharp edge does not re-derive custom normals; the moved cap corners kept
   the rotated smooth rim normal (GLB rim split p5 38.5). With custom normals the order is now move -> sharp edges ->
   normals, and the corners of a rim being sharpened take the cap's own normal (corner-angle weighted cap faces at
   that vertex) and the wall's face normal; unmoved corners off that rim stay within 0.1 deg (0.0052). Custom-normals
   fixture: rim split front p5 78.45 deg, Blender and host checks PASS. g3d_seat `sharpened` is written only when the
   stored rim's split, measured as the check does (`rim_split_now`), has front p5 >= 60 deg on every body; else
   W_RIM_SPLIT_LOW (the edges stay marked).
4. **Backups keep their order.** g3d_backups records `seq` (the order of the seat / hide backups), the lens's
   `hidden_by` and the declared ranges before the first lift (`hide_ranges_before`). unhide_eyewire refuses (E_ARGS,
   nothing changed) a `lens=` other than the recorded held lens, any `lens=` when the eyewire has no hide backup, and
   a lens another eyewire moved; it touches the lens only when the eyewire's backup is restored, and puts the ranges
   back. A seat done after the hide is undone with it (as before); one done BEFORE the hide is kept (pre-seat backup
   and g3d_seat stay), and unseat_lens refuses **E_ORDER** until the eyewire is unhidden (that pre-seat mesh predates
   the lift). Browline fixture: seat (metal, capture 0.7, 256 vertices) -> hide -> unseat refused -> unhide (seat
   kept, meshes = seated) -> unseat (original meshes exact, no g3d property or backup left).
5. Minor: the CLI parses its options inside the receipt (a missing value, an unknown option, an extra argument ->
   refused receipt E_ARGS; no receipt path -> usage on stderr); export_and_validate reports unknown `seating` keys in
   the seating section instead of raising; `g3d.__all__ = ["SEAT_TOOLS"]` (the host star import works).
6. Whole suite: 522 passed, 1 skipped, 3 xfailed without test_seat_blender.py (23.6 min, most of it the photoprint
   and camera-fit tests), plus test_seat_blender.py 64 passed + 1 xfailed. INVU writing seat_lens 6.5 s standalone
   (second call 2.7 s; 19.1 s inside the parallel suite while another pytest run held the CPU); still above the 5 s
   budget.

## Implementation notes (stage "proof", 2026-10-06): §10 P2-P5

Ran P2, P3, P4 and P5 as written: 26 headless Blender 5.2 runs on copies (`glasses3d/data/wp4/proof/`, runner
`tests/proofs/wp4_proof.py`, Blender helpers `tests/proofs/wp4_proof_bpy.py`), the WP3 exporter on the input AND the
result of every case, graded by `tests/proofs/wp4_proof_grade.py` into `glasses3d/data/wp4/proof.json` (224
records: each criterion by the core (tool report, Blender check, host check_glb on the GLB) and by the instrument on
the GLB, or on raw Blender arrays / digests). The four source .blend sha256 are unchanged. No tool file was changed.
**Stop rule not triggered:** P2 passes all 67 records; Oakley 0 vertices moved; every seat call's moved set equals
the band of FAIL nodes recomputed from its before-state (q001 58/58 per side, metal 132, the 8 acetate constructions,
INVU 538/538, Prada 0). Verdicts: **P2 PASS; P3 FAIL; P4 FAIL (10 records); P5 FAIL (4 records).** Re-checked from the
produced files by `tests/test_seat_real.py` (a fresh grading equals the record); instrument unit tests
`tests/test_seat_proof_metrics.py`.

Findings (measured, not tuned):
1. **P3 Prada refused** (open item A): E_POSTCHECK, a 0.046 mm wall sliver rotates 56.7 deg (limit 30). Nothing is
   written, so 'every point >= 0.60' reads 0.363 (core) / 0.367 (instrument) and the rim split stays 0 deg. Before:
   95 of 355 nodes fail per lens; one sits at |x| 14.05 (M's band says 10-14).
2. **Self-intersection is not refused:** a lens whose front cap is pushed 0.8 mm through its back cap (closed,
   manifold, consistently wound; `seat_fixtures.self_intersecting`) is seated. No self-intersection test exists in
   the core; §10 P4 lists it as a refusal.
3. **Correction pass vs §10 targets:** §4 step 7 corrects only points still below PASS, so grown points stop
   between pass and target. INVU: 187 of 261 inside-run former failures read below 0.45 (p50 0.4496, min 0.4249;
   38 below 0.445); metal: one node at the -0.30 / 0.10 junction reads 0.426 (< 0.44). Both checks pass.
4. **Narrow eyewire** (open item C): the 0.5 mm web clamps the top to exactly pass (EXITS_MATERIAL limit 0.20), so
   the points stay FAIL, not BLOCKED(EXITS_MATERIAL); the call is refused (31.5 deg face rotation), 0 moved.
5. **Nylor channel ends:** inside the run the flats read >= 0.449, but the channel-end points (TRANSITION, never
   grown) read 0.205, and the run-end ramp 0.35-0.45: 'top >= 0.44 on the flats' fails as written.
6. **Existing nylor cord overlaps the grooved lens 0.74 mm3** (Blender Boolean; instrument 0.77): the fixture cord is
   centred 0.30 mm inside the edge along the horizontal outline normal, the groove is cut along the lens-surface u
   (tilted ~10 deg on the R120 cap), so the cord pokes out of the notch along the whole exposed arc. An existing cord
   is never moved (§5). A created cord: 0.0052 mm3, diameter 0.599-0.600, ends 0.97 / 0.97 mm in the channel.
7. **Grown front edge vs the true cap:** sphere, toric, flat, solidified, flipped <= 0.0002 mm; coarse 32-segment
   0.0174 mm (the quadric over few points); chamfer +0.031 and bullnose +0.0135 mm change (their front loops are off
   the analytic cap by construction).
8. Oakley top (descriptive in §10): 6 ENCLOSED + 323 LIPPED, mid-depth p95 0.833 (core) / 0.835 (instrument) > 0.80.

Instrument additions (`tests/instruments/seat_proof_metrics.py`, no code shared with g3d/seat): the port's u-flip
test moved from mid-depth to 20% of the depth (a 0.6 mm groove left Pm - 0.4 u outside the lens and flipped u at 63 of
140 stations); back corners by the station plane where the port finds no one-triangle rung (re-walled, chamfered,
bullnose walls); graded capture from refined exact rows (the port's 9-level grid reads a 0.80 capture as 0.75 and is
reported only); the eyewire section perpendicular to its own axis; the groove cutter rebuilt by the §5 definition with
the outline's Pappus factor (removed 136.084 vs 136.078 mm3). Readings of §10 text: '~N' as +-10%; 'outside the
ranges' = below the lens centre or > 3 mm (the ramp) beyond the x-range (an unmoved station inside the ramp re-orients
0.41 deg, 0.00016 mm, because its normal averages moved cap faces).

## Implementation notes (stage "proof", review fix round 1, 2026-10-06)

Re-ran P2-P5 (26 headless Blender runs on copies, `glasses3d/data/wp4/proof/`; the four source sha256 unchanged) and
re-graded: `proof.json` 251 records. **P2 PASS (71/71); P3 FAIL (10 fail, 10 not applicable); P4 FAIL (8 of 105);
P5 FAIL (4, 1 not applicable). Stop rule not triggered.** Correction to the stage note above: its "P4 FAIL (10
records)" counted the crystal case as passing; its binding verdict is now a record (`P4.crystal.check`) and FAILS.
1. **Self-intersection refusal** (§10 P4): `geom.self_intersections` (every edge against the triangles sharing
   neither end vertex; closed float64 segment-triangle test; uniform grid, each pair tested once in the cell of its
   boxes' overlap corner) runs in `stations.build_body`, so seat_lens refuses `E_SELF_INTERSECTING` before planning
   and both checks fail the lens. The test is closed because the fixture's caps share their (x, y) grid and cross
   only edge-on-edge (a strict test found 0 of the 204 crossings). Equal to an O(E x F) reference on the fixture;
   0 crossings on FT x2, Prada x2, Oakley, INVU and every other fixture lens. Cost per body (host): 0.06 s (Oakley)
   to 0.44 s (Prada, INVU); memoised on the exact arrays (an INVU seat call computes it twice, 0.84 s each in
   Blender on a loaded machine). `P4.refusal.selfx`: refused, 512 edges, nothing written.
2. **Groove acceptance** (`seat_bpy.groove_attempt`, used by the tool and the proof): closed / chi / components are
   judged on the evaluated mesh's own connectivity, welding only split seams (`geom.weld_seams`). The closed control
   cutter is now accepted with MANIFOLD and EXACT (removed 27.6229 = expected; an exact weld everywhere reads 4
   non-manifold edges, chi 2); the open cutter is rejected (MANIFOLD removed 0 vs 1647.5 mm3 expected).
3. **Nylor fixture cord** is built in the §3 station frame (`seat_fixtures.station_axes` / `cord_station`,
   independent code, within 1.7e-6 deg of the core's frames): existing-cord overlap 0.0025 mm3 (Blender Boolean) /
   0.0 (instrument), was 0.74 / 0.77 with the horizontal cord. The tool still never fits a groove to an existing
   cord: a real cord off the §5 groove overlaps, as the old fixture did.
4. **Bookkeeping:** run_state.json records the sha256 of the 20 tool sources (g3d/seat/*, seat_bpy, the exporter,
   the Blender runners, the fixture builder) and a combined digest per case; test_seat_real skips "stale proof" when
   any differs. A record with nothing to measure is `not_applicable` (pass null, not counted): the four P3 records
   per refused lens, the instrument moved-node record where nothing moved. Every seat call also gets two moved-set
   records independent of the planner's FAIL list: moved nodes are nodes of check-FAIL points (FT 29/29 per side,
   metal 66, matrix 128 / 32, INVU 269), and each moved node's nearest instrument station on the before-GLB fails
   or neighbours one that does (0 violations; FT graded on a new export after the lift).
5. **P2 ramp reading (owner to confirm):** 'outside the ranges' excludes the 3 mm lift ramp beyond each x-range
   end. Inside it, above the lens centre, the MOVED stations (x -57.23..-56.20 / 56.20..57.23) gain mid-depth
   capture +0.145 (core) / +0.095 L, +0.127 R (instrument) and min-over-depth up to +0.888 / +0.844, +0.869 mm;
   unmoved ramp stations change <= 0.0002 mm at mid-depth (instrument 0), but the core's min over depth drops by
   up to 0.035 mm there (its back corner lies on back-loop segments whose vertices moved). Nasal and bottom: 0.

Open, owner decisions (nothing tuned):
- **A (P3 Prada)** unchanged: refused, wall slivers rotate 56.5 deg. Scratch measurement, not landed: with wall faces
  exempt from the rotation limit the host core (open3d, delivery GLB) seats both lenses: 191 GLB vertices each,
  max delta 0.4386 mm, every point >= 0.6038 after, caps rotate <= 0.27 deg, 3 wall faces 56.5 deg (>= 0.0037 mm2).
- **Correction pass vs §10 inside-run targets** unchanged: INVU 187 of 261 inside-run former failures below 0.45
  (p50 0.4496, min 0.4249; instrument 38.4% OK), metal one node 0.4259 (instrument 0.4417-0.4538, passes).
- **B** (crystal check FAILS 352/352), **C** (narrow eyewire refused by a 31.5 deg rotation, 0 BLOCKED), **D**
  (notch refused by E_POSTCHECK), nylor channel ends 0.205, matrix cap knife-edges (coarse 0.0174, chamfer +0.031,
  bullnose +0.0135 mm), Oakley top p95 0.833: unchanged.
- HEAD (852915c, another session) holds the WP4 proof scripts and instruments while the modules they import are
  untracked: commit WP4 as one unit.
- **Speed**, measured while another session ran 10 worker processes at 100% CPU (not a valid standalone number):
  INVU writing seat 17.3 s (dry run 11.7, second call 7.1), Blender check 6.0 s; Oakley Blender check 9.8 s, 85% of
  it in `buried_band`'s line intervals. INVU profile: station_frames 2.5 s x 3, measure_body 4.3 s x 3,
  nearest_on_polyline 4.0 s over 11 calls. Budget 5 s still exceeded; re-measure on an idle machine before WP7.

## Implementation notes (stage "proof", review fix round 2, 2026-10-06)

Re-ran P2-P5 (27 headless Blender runs on copies: the 26 cases plus the review control `nylor_outline`; the four
source sha256 unchanged) and re-graded: `proof.json` 253 records. **P2 PASS (71/71); P3 FAIL (20 pass, 10 fail,
10 not applicable); P4 FAIL (98 of 107 pass, 9 fail); P5 FAIL (30 pass, 4 fail, 1 not applicable). Stop rule not
triggered** (Oakley 0 moved; every moved set equals the band of FAIL nodes; P2 complete).
1. **Cord-lens overlap is measured by the tool and the check (major).** An existing cord was never measured: the
   stage-proof cord overlapped the grooved lens by 0.7393 mm3 and seat_lens said `seated`, the check passed.
   - `seat_bpy._nylor_cord`: for an existing cord, every closed component holding the groove centre on the exposed
     sector gets an EXACT Boolean with the grooved lens, on its own (a cord merged into the channel's object would
     otherwise add the channel's buried edge): `cord_lens_overlap_mm3`, `cord_parts`. Above 0.01 mm3 (existing or
     created cord) seat_lens returns `partial` with `W_CORD_OVERLAP`, g3d_seat status partial.
   - `check.cord_overlap` (both checks, nylor lenses): frame parts holding the groove centre at the
     VISIBLE_BY_DESIGN points, and vol(lens ^ part) in their station planes (`stations.part_overlap`: columns along
     n every 0.01 mm of u over [-3, +1] mm from the mid-depth edge point, exact along n, Pappus factor, cast only
     where a 0.1 mm probe finds the part). Reported per part; above 0.01 mm3 the lens FAILS (`rules.check_verdict`
     `cord_overlap_mm3`; §8's PASS list names only the crystal overlap, so this is a reading of §5 'the check reports
     cord-lens overlap' + §10 P4's 0.01, owner to confirm). The channel is left out on purpose: where it holds the
     lens the edge is buried in it by design (§3 capture), 39.46 mm3 on the grown nylor fixture.
   - Accuracy: analytic half-buried 12-gon torus within 0.02% (numpy and open3d); on the fixtures tool (Boolean) /
     check / instrument: station cord 0.00245 / 0.00149 / 0.0, created cord 0.0052 / 0.00511 / 0.0055, stage-proof
     cord 0.7393 / 0.7345 (exposed sector only) / 0.7734. Cost: nylor Blender check 1.1 s.
   - The proof grades `P4.nylor(_nocord).cord_overlap` (core) from the tool's report and both checks (the proof's
     own Boolean is gone); `P4.nylor_outline.cord_overlap_reported` (review control, not a §10 line): tool partial +
     W_CORD_OVERLAP 0.7393, both checks fail the lens on it, instrument 0.7734 (within 10%). Tests:
     `tests/test_seat_proof_fixes2.py` (host, 5), `test_nylor_existing_cord_off_the_groove_is_reported` (Blender).
2. **Browline instrument verdict** (`wp4_proof_grade.instrument_failing`): the deep rule only where the station is
   layered (two frame parts on its mid-depth row within the §6 window, the instrument's own occupancy test); the
   loop bottom at the same x takes the metal rule. ft-mid per lens: 37 failing stations (was 98), 0 below the lens
   centre (was 59); moved nodes 29 per side, 0 violations.
3. **Notch:** `P4.refusal.notch` now FAILS: §10's 'notch CONCAVE' counts only a refusal from the CONCAVE clamp or the
   gate; the fixture is refused by E_POSTCHECK (a 166.2 deg face fold, area ratio 0.138, around 4 CONCAVE-blocked
   points whose neighbours kept full growth). Open item D.
4. **Core records measure the result:** crystal bevel angle / h and the nylor grooves are measured on the exported
   lens through the core's station frames, open3d backend and `lens_edge` (`core_profiles`): bevel 120.23-120.49 deg,
   h 0.4033-0.4041 (352 stations); grooves width 0.593-0.600, depth 0.5975-0.6001, centre <= 0.0032 (350 stations).
5. **Corrections to the round-1 notes:** P3 Prada's wall sliver rotates **56.7 deg** in the Blender proof (56.5 was
   the host scratch measurement). P2 ramp, both ends: MOVED ramp stations, core mid-depth -0.0339 / -0.0351 to
   +0.145, min-over-depth -0.0589 to +0.888 (L / R); the instrument on fixed station lines -0.0012 / -0.0005 to
   +0.095 / +0.127 mid-depth and +0.0096 / +0.0152 to +0.844 / +0.869 over depth, so the core's negative side is its
   frames re-built on the moved lens, not a loss of capture. Unmoved ramp stations: core mid-depth >= -0.0002,
   min-over-depth >= -0.0352; instrument 0.
6. Speed in the proof (6 Blender processes in parallel plus a pytest run; not standalone): INVU seat 13.4 s, Blender
   check 4.3-4.6 s; Oakley check 6.6-6.8 s; FT seat 1.8 / 2.0 s.

## Implementation notes (stage "proof", fix round: open items A-G, 2026-10-06)

Re-ran P2-P5 (27 headless Blender 5.2 runs on copies, `glasses3d/data/wp4/proof/`; the four source sha256
unchanged) and re-graded: `proof.json` 253 records. **P2 PASS (71/71); P3 34 of 40 (the 6 failures are two readings
below, nothing the tool does); P4 PASS (107/107); P5 PASS (32 pass, 1 not applicable, 2 report-only). Stop rule not
triggered:** Oakley 0 moved; every seat call's moved set equals the band of its FAIL nodes (q001 58 per side, Prada
190 per lens, metal, the 8 acetate constructions, INVU 538), every moved node fails by the check and by the
instrument. Each item was diagnosed on the host first (open3d, the proof's own input arrays and GLBs) before code
changed; `tests/test_seat_proof_fixes3.py` (14 host tests) pins them.

**A. Prada (P3) folds: the faces are WALL faces, not cap faces.** The post-check's failures were 4 triangles of 2 wall
quads: at a run start node 348 -> 349 across a 0.0444 mm rim segment, growth 0 -> 0.0682 mm (56.8 deg, area x1.80),
and inside a run 191 -> 192 across 0.0046 mm, 0.4348 -> 0.4304 mm (42.8 deg). A wall face spans one rim segment and
the rung; a growth step d across a segment of length e turns it by atan(d / e). Cap faces stay in the surface (the
quadric normal offset), so spreading the move over cap rings cannot help a wall face, whose vertices are all rim
vertices. Fix, the "band falloff along the loop" (`plan.fold_slopes`, `plan.fold_total`, `deform.refine_fold`):
- per segment the largest step the face allows, S = tan(30 deg) |e_perp| / |u . m| (e_perp: the segment across the
  rung, m the face normal; the post-check's own 30 deg);
- node growth = the largest S-Lipschitz function under the nodes' limits (0 at every node that does not grow, so the
  strict taper of §2 stands), then the smallest S-Lipschitz function over that: growing nodes are raised (never past
  a limit) or, beside a node that does not grow, lowered to meet it. A point the lowering leaves below pass + 0.05 is
  BLOCKED(FOLD) (none on Prada: node 349 0.0682 -> 0.0260, its point still 0.604; node 192 0.4304 -> 0.4321);
- the slope is first order: the exact displacement turned the limit-bound faces by 30.1-31.0 deg (the segment
  shortens where the outward directions converge). `refine_fold` displaces with the plan, measures every moved face as
  the post-check does, scales the slope of each segment past the limit (less the float32 storage margin
  atan(2 eps |x| / edge), eps = 2^-23 x 2) by tan(limit) / tan(angle) and replans (<= 8 times). It runs inside
  `deform.measure_state`, so the proof's expected moved set (`wp4_proof_bpy.node_state`, which now passes the polygon
  edges) is the refined plan's; correction passes refine their own increment against the original faces.
The post-check is unchanged. P3 in Blender: seated, 190 vertices per lens, every point >= 0.6038 at all depths after
(instrument 0.6229 / 0.6197 refined, 0.575 / 0.578 on its 9-level grid against its 0.55), max delta 0.4386 mm, volume
gain 21.17 vs sum g t ds 21.32 mm3, GLB rim split front p5 78.38 deg, etch bit-identical.

**B. The crystal check (`stations.groove_seat`, rule `apex`).** §5 cuts the groove with a 0.03 mm clearance, so on
every depth line the frame begins 0.028-0.035 mm beyond the lens edge and §3's 'interval containing the edge' reads a
gap at every depth: 352/352 FAIL on exact bevel and groove numbers. In a groove the lens is held between the lips; its
capture is how far each depth's edge reaches beyond the lips' inner face (PLAN §3: 'the lens extends 0.8 mm beyond the
visible aperture'). For a lens whose `g3d_seat` declares `profile: vbevel` the check reads the lips on lines along u
just outside the lens faces (front corner + 0.06 n, back corner - 0.06 n: beyond the clearance, inside the lip),
takes the aperture linear in depth between them (a planar face: where §3 reads it on each depth line before the cut),
and counts a point groove-seated when every depth line meets frame within 0.06 mm (§5's gap band) and both lips hold
frame over the corner. Rule `apex`: the mid-depth value (the apex) when every depth lies beyond the lips, else the min
over depths. The deep rule's min over depths can never pass a V-bevel: §4 step 8 grows the corners to target - h on
purpose (corners 0.396, apex 0.80). Without the declaration, with a clearance outside 0.06, or with a short apex the
lens still FAILS (host tests on `seat_fixtures.vbevel_lens` / `grooved_ring`: a flat-cap §5 bevel in a swept §5
groove reads 0.80 at every point; undeclared -0.03, clearance 0.10 not seated, apex 0.50 FAIL; numpy = open3d). P4
crystal in Blender: tool seated, Blender and host checks PASS 352/352 ENCLOSED, apex 0.7998-0.8005 (core) /
0.7987-0.7998 (instrument, against the frame before the groove), gap 0.028-0.029 (tool) / 0.0279-0.0355.

**C. The correction pass reaches the target.** Two causes, measured: (1) §4 step 7 corrected only points below pass,
so a capture response under 1 left grown points between pass and target (moving the edge g along the curved surface
raised the capture by 0.96-1.06 g); (2) on INVU a station's back corner sits up to 0.93 mm (1.7 segments, p50 0.44)
along the loop from its node's rung partner (wall extruded along z, station planes leaning up to 31 deg; FT, Prada
and the fixtures 0.06-0.25 mm), and its depth lines cross the triangulated wall between them: raising the station's
own node by 0.01 mm raised its mid-depth capture by 0.0006-0.0012 mm, every node by 0.00998, so a correction that
grows only the short nodes converged by about half per pass (12-36 of 261 still below 0.45 after two). Now:
- the correction grows the node points of moved nodes inside their failing runs (>= the 2 mm run-end ramp) that read
  below their planned target, by target + 1e-4 mm (CLASS_EPS_MM, the float32 noise) - value, spread over the nodes
  whose rungs bound the wall the station's depth lines cross (`deform.station_reach`: from node k to the rung nearest
  its back corner; node k alone where the wall lies in the station plane), each raised node's grown corner tested for
  cover (UNHIDEABLE); points below pass are still corrected anywhere (§4 step 7); still at most two passes;
- ramp and inter-node points are not corrected to their target: a run-end point sits beside a node that does not
  grow (correcting it pushed the metal fixture's nasal run-start node to 0.94 mm and 0.617), and a point between two
  nodes moves with their interpolation (correcting those raised the -0.30 / 0.10 junction nodes to 0.477 / 0.468);
- in a correction the CONCAVE radius is the original outline's (the moved outline's kinks are the growth's own; its
  radius minus the growth already applied counted that growth twice and blocked the metal junction's correction),
  and a correction never moves a node the first pass did not (a halfway point could hand an unmoved neighbour growth).
P4 metal: former failures inside runs 0.4500-0.4553 (core) / 0.4500-0.4639 (instrument), run ends >= 0.3696. P5 INVU:
261 inside-run former failures 0.4500-0.4856 (0 below 0.45; was 187), ends >= 0.4542, max delta 0.8911 mm, 538
vertices. Rejected, measured: a per-node secant (divide by the measured response): 22 still short; moving each
back-loop vertex with the station whose plane passes through it: INVU 28 short and the q001 lift slope 0.517 > 0.5
(a lens node 5 um from a ring at the ramp foot).

**D. Nylor channel ends are not 'flats' stations of §10's 'top >= 0.44'.** The 0.205 points are the channel-end
stations whose node borders the exposed sector: the visible halfway point beside them has NO_FRAME, which caps both its
nodes at 0, and growing that node would move the exposed outline by up to 0.6 g at the channel end (§10 P4 'exposed
outline unchanged +-0.01', §12 'never extend an exposed edge'). They are TRANSITION by §4 step 4 (blocked, c >= 0,
front covered, within 3 mm of a visible point) and §8 allows them (<= 3%: 2 of 161). The next ring of points is the
§4 step 3 ramp (pass + 0.05 at the run end, the target 2 mm in). The tool is unchanged; the grader reads 'top >= 0.44
on the flats' as P4 metal and P5 INVU read their former failures: at the node points the check failed before (joined
on raw rows, run ends from the plan before), >= 0.44 >= 2 mm inside the run, >= 0.35 nearer its end, or TRANSITION
(<= 3% of required); the proof job records the nylor after-state (`ns_after`). Instrument: its own transition
stations (held, within 3 mm of the channel end, flats >= 0 and < 0.35), the ramp from the ends of the remaining held
run. P4 nylor: inside 0.4501 (51 node points), 2 TRANSITION of 161 at 0.2053 / 0.2054; instrument inside 0.4477, run
ends 0.3636, 2 transition stations (0.1902 / 0.1961; its 1 mm spacing makes them 2 of 64, so §8's 3% is graded on
the core's required points). The point between a TRANSITION node and its grown neighbour reads 0.3039 (PASS), as the
points between nodes P5 INVU does not grade either.

**E. Refusal reasons.** (1) Narrow eyewire: the 0.5 mm web holds at most web - 0.20 = 0.30 mm = pass with 0.20 mm of
material left (0.305 along the tilted u): a knife edge the 0.005 mm verdict tolerance decided. A capacity clamp
(NEEDS_GROWTH, EXITS_MATERIAL, OTHER_PART, CONCAVE: how far the edge can go, the point's own limits) that leaves a
point below pass + 0.05 (§4 step 3's least target) now BLOCKS it; UNHIDEABLE and limits inherited through a shared
node keep §4 step 4's 'below pass' (the same floor on them blocked the metal fixture's nasal run start at 0.2965-0.323
and its correction then overshot the next node). Every top station BLOCKED(EXITS_MATERIAL) (113/113), 175/348 blocked
-> E_TOO_MANY_BLOCKED, 0 moved. Real models: no clamped failing point on FT, Prada, Oakley or INVU, no verdict
change. (2) Notch: any required point BLOCKED(CONCAVE) refuses the plan, `E_CONCAVE` ('the outline turns inward with a
radius under twice the growth it needs'; 4 points, radius 0.474 mm), nothing written (§10 P4 'Refusals: ... notch
CONCAVE'). Without the gate the fold limit would have tapered the neighbours into a pinched, partial seat.

**F. The grown front edge follows the cap.** The quadric was fitted through its origin to the cap vertices outside the
band PLUS the whole front loop; on a chamfer or a bullnose the rim ring lies off the cap (0.064 / 0.011 mm), which bent
the fit (+0.031 / +0.0135 mm change), and a 32-segment lens has fewer than 12 cap vertices within 6 mm, so it fell back
to the plane normal to the station normal (0.0174 mm). Now the fit uses the cap's own vertices outside the band (never
the rim loops), has an intercept (dn = z(0, g) - z(0, 0): the rim vertex keeps its offset), and takes the 12 nearest
when 6 mm holds fewer (W_LOW_CONFIDENCE_FIT; the tangent plane only below 6 vertices). Matrix: change <= 0.0001
(chamfer), 0.0 (rounded, coarse), 0.0003 (toric). FT q001's front cap has 4-9 such vertices within 4 mm, so its fits
widen to 6 mm: P2 still 71/71 (lift 1.0284 mm, slope 0.445, 58 vertices per side, left = mirrored right exactly).

**G. Oakley 'top LIPPED p95 <= 0.80' is report-only** (`P5.oakley.top_lipped`, core and instrument; measured p95
0.8327 / 0.8346, 6 ENCLOSED + 323 LIPPED): an upper bound on capture conflicts with §7 (over-capture above the §3
maximum is a warning; seat_lens never shrinks a lens), Oakley moves 0 vertices by design, and the owner accepted
Oakley OO9208 v2 as perfect on 2026-10-06 (`data/ledger/owner_verdicts.jsonl`). Its check PASS and zero-move records
stay graded. The grader's `Case.add(..., report_only=True)` gives status `report_only` (pass null, counted apart).

Still failing in P3, not changed (readings for the owner):
- `P3.before_failing_where` (core, both lenses): one failing node of 95 per lens sits at |x| 14.05, outside M's
  'nasal x 10-14' prose; it describes the input, not the result (instrument: 100% inside).
- `P3.cover_unchanged`: 2.13 mm (core) / 2.07 (instrument) at the temporal nodes (x -58.9). The grader compares the
  signed cover value; at a corner buried in the acetate (every Prada station is ENCLOSED, the value is negative) that
  value is how far the frame extends behind the cover line's start, which grows as the corner moves 0.41 mm deeper
  into a thickening rim. Front and back stay covered 100% and the edge inside 100%. Whether 'cover unchanged +-0.05'
  means the cover share or a lip gap on uncovered-by-material corners is the owner's reading; no criterion was changed.

Speed (6 Blender processes plus a 6-process pytest run at once, not standalone): INVU seat 42.7 s, Prada 13.4 / 13.9 s,
FT 4.4 / 5.0 s. Host, open3d, standalone: INVU 8.6 -> 12.7 s (the second correction pass and the spread's cover
test), Prada 3.3 s. Further above the 5 s budget.

## Implementation notes (stage "proof", fix round A-G: review fix round 1, 2026-10-06)

Re-ran P2-P5 (27 headless Blender 5.2 runs on copies, `glasses3d/data/wp4/proof/`; the four source sha256 unchanged)
and re-graded: `proof.json` 261 records. **P2 PASS (71/71); P3 34 of 40 (the same 6 readings as before, plus 2
report-only); P4 110 of 112 graded, 2 FAIL (the narrow eyewire knife edge, item 2) and 1 owner-pending exception
(item 3); P5 PASS (32, 1 not applicable, 2 report-only). Stop rule not triggered:** Oakley 0 moved; every seat call's
moved set equals the band of its FAIL nodes (now also the narrow eyewire's partial seat, 96 = 96), every moved node
fails by the check and by the instrument. Host tests `tests/test_seat_proof_fixes4.py` (8, new);
`test_seat_proof_fixes3.py` narrow test rewritten; `test_seat_real.py` 23; `test_seat_blender.py` 66.
1. **Groove reading and crystal overlap (major).** The fix round's groove reading counted a point as groove-seated
   whenever every depth read >= -0.06 mm, so an edge IN frame material qualified; with the fix round's own fixtures
   a V-bevel buried in an ungrooved crystal ring and one cutting 0.05 mm into a groove too small for it both passed
   344/344. Now:
   - `stations.groove_seat` seats a point only when every depth has a real clearance, the frame beginning
     0.02-0.06 mm beyond the edge (§5's gap band, `GROOVE_GAP_MIN_MM` 0.02), and records it (`Measurement.groove`);
     the 'apex' rule grades only groove-seated points at the apex, every other point by the deep rule;
   - `check.crystal_overlap`: vol(lens ^ part) for every translucent part that captures the edge at any depth, in
     the station planes of every point (`stations.part_overlap`, the cord method), graded <= 0.01 mm3 (§8) by both
     checks (`crystal_overlap_mm3`, `crystal`);
   - §8's stale-cutter flag: `seat_bpy.check_lens_seating` compares the `g3d_groove_cutter | <lens>` digest with the
     lens (`stale_cutter`, a warning; not a FAIL reason, §8 lists it in the report, not the PASS list). The host
     reports null (a GLB carries no cutter); the overlap stands for it.
   Host (numpy = open3d within 1e-3): solid ring (`seat_fixtures.solid_ring`) FAIL 344/344, deep 0.396, overlap
   127.58 mm3; `grooved_ring(clearance=-0.05)` FAIL, deep 0.05, overlap 15.99 mm3; clearance 0.01 not seated, FAIL;
   the §5 groove: overlap 0.0, PASS at 0.80. Blender (`test_seat_blender.py` crystal_seat): seated check overlap 0,
   stale false; with the g3d_groove Boolean switched off the check FAILS (overlap > 0.01, 0 seated); after a lens
   vertex moves 0.001 the cutter is flagged stale. Proof: `P4.crystal.overlap` tool Boolean / Blender check / host
   check 0.0 / 0.0 / 0.0; review control `P4.crystal_control.no_groove_fails` (the profiled lens in the frame before
   the groove): FAIL 352/352, 0 seated, overlap 126.74 mm3 (instrument 127.23). No real model has a translucent
   capturing part, so no real verdict changes.
2. **§4 step 4's threshold restored (major, owner decision).** The fix round blocked a point when a capacity clamp
   (NEEDS_GROWTH, EXITS_MATERIAL, OTHER_PART, CONCAVE) left it below pass + 0.05, with UNHIDEABLE and inherited
   limits kept at pass because the uniform rule broke the metal fixture: a binding-rule change shaped by fixture
   outcomes. Withdrawn: every clamp, FOLD included, blocks below pass (less the 0.005 tolerance) as §4 step 4 says.
   The narrow eyewire is a **knife edge in the fixture**: the 0.5 mm web holds web - 0.20 = 0.30 mm = pass; the top
   points' capacity (value + EXITS_MATERIAL limit) is 0.3046-0.3067 for pass 0.30, so all 113 failing top points
   are clamped just above pass and grown, none BLOCKED(EXITS_MATERIAL). The tool writes a partial seat (96 vertices,
   34 BLOCKED: EXITS_MATERIAL 2, UNHIDEABLE 7, FOLD 25; min after -0.305; the check fails), so
   `P4.metal_narrow.exits_material` and `.zero_moved` FAIL (graded, with the knife-edge note), and the moved set is
   graded like every seat call (96 = 96, 48 moved nodes all failing by check and instrument). Owner: either amend
   §4 step 4/5 and §12 to a capacity floor of pass + 0.05 (one rule, with the UNHIDEABLE / inherited exceptions
   justified in the design), or keep §4 and change the fixture (a web below 0.50 mm). Kept and owner to confirm,
   both outside §4 steps 4-5 and §12: the gate **E_CONCAVE** (any required point BLOCKED(CONCAVE) refuses the lens;
   §10 P4 lists 'notch CONCAVE' among the refusals; under the restored threshold it fires only when a CONCAVE limit
   leaves a point below pass: the notch, 4 points, radius 0.474 mm), and the reason **FOLD** (the fold limit as a
   clamp). Real models: no clamped failing point on FT, Prada, Oakley or INVU; their moved sets are unchanged.
3. **'Top >= 0.44 on the flats' grades every required point (minor).** Core (host check on the exported GLB): >= 0.44
   at every required point >= 2 mm from the channel end (153, min 0.4499); within 2 mm (8 points): >= 0.35, or
   TRANSITION, which the core counts and §8 caps at 3% (2 of 161: 0.2053 / 0.2054). Two points there pass the check
   but read below the ramp's 0.35: **0.3039 and 0.3044** (one per channel end, between a channel-end node that never
   grows and its grown neighbour). They are not graded silently: `P4.nylor.top_flats_exception` is `owner_pending`
   (pass null; the case carries `pass_pending_owner`); rejecting them fails the record. Instrument: the channel end
   is the midpoint between the last held and the first unheld station (1 mm spacing); its transition station is
   defined by POSITION (the held station nearest each channel end, at most one per end, flats >= 0: 0.1902 /
   0.1961); every other held station within 2 mm reads >= 0.35 (2, min 0.3636), the rest >= 0.44 (60, min 0.4477).
4. **Margins of by-construction passes (minor).** `proof.json` summary `margins` and `margins_note`: P5 INVU inside-run
   former failures min 0.450048 against >= 0.45 (margin 4.8e-5 mm: the correction aims at target + 1e-4); metal
   inside-run min 0.450014 (0.010 above 0.44, 1.4e-5 above the 0.45 target); P3 Prada largest moved face 29.9648 deg
   on the stored arrays against the post-check's 30 (0.035 deg; 2 faces above 29.9; `P3.face_rotation.*`,
   report-only, not a §10 line). The criteria and the post-check are unchanged; a re-measurement on another backend
   or machine can flip these records.
5. **Speed (minor).** All bit-identical (raw deltas of 16 host cases, fixtures and INVU / Prada / Oakley, compared
   before and after; station frames compared with HEAD's on 17 bodies):
   - `station_reach` once per seat from the original lens, keyed by node and mapped onto each pass's points. Per
     pass on the moved lens it was identical on every fixture and INVU; on Prada 2 of 355 nodes sat on a back-corner
     knife edge (w = 0.5 between two 0.02 mm rungs), with no change to the result;
   - replans: `plan_growth(cache=)` computes the point plan (verdicts, clamps, the unhide test, point growth) once
     per replan family; refine_fold's replans rerun only the fold limit and the node aggregation; the spread's
     unhide test is memoised;
   - the band and refine_fold's last displacement are reused by grow_body (`BodyState.band`, `SeatPlan.disp`);
   - `station_frames` measures each 32-point block against the cap faces inside its box grown by 2 x 5 mm (the only
     faces it can select, in their original order): INVU 2.93 -> 1.21 s per call, three calls per seat.
   Host, open3d, paired with HEAD under the same load (another session at 30-60% CPU, not idle): INVU 8.2-9.7 s vs
   HEAD 8.8-11.0 s (it was 1.5-1.6x HEAD); Prada 3.4 s (HEAD refused it). Proof (6 Blender processes in parallel):
   INVU 18.7 s (42.7 s in the fix round), Prada 7.6 / 7.3 s (13.4 / 13.9). Still above §9's 5 s per lens;
   re-measure on an idle machine before WP7.

## Implementation notes (stage "proof", fix round A-G: review fix round 2, 2026-10-06)

Re-ran P2-P5 (27 headless Blender 5.2 runs on copies, 3 min 43 s with the grading; the four source sha256 unchanged)
and re-graded: `proof.json` 262 records. **P2 PASS (71/71); P3 34 of 40 graded (the same 6 readings, plus 2
report-only); P4 109 of 112 graded, 3 FAIL (`P4.metal.inside_runs` core, new under every-point grading; the narrow
eyewire's two records) and 2 owner-pending (the nylor end-zone reading, core and instrument); P5 31 of 32 graded,
1 FAIL (`P5.invu.former_failures` core, new under every-point grading), 1 not applicable, 2 report-only. Stop rule
not triggered:** Oakley 0 moved; every seat call's moved set equals the band of its FAIL nodes; every moved node
fails by the check and by the instrument. The two new FAILs come from the grading, not from the tool: the only tool
change (item 2) records which fold pass blocked a point and adds it to messages; the plan is unchanged (metal 132,
narrow eyewire 96, INVU 538 vertices moved, as before). Host tests: `tests/test_seat_proof_fixes5.py` (4, new),
`test_seat_proof_fixes3.py` (metal and INVU every-point tests), `test_seat_real.py` (round-2 records);
`pytest tests/test_seat_*.py`: 344 passed in 8 min (the proof not stale, a fresh grading equals the record).

1. **Every point is graded, and the node-point minima were not the minima (major).** `wp4_proof_grade.former_failures`
   joined the before and after states on node points only, and the correction pass corrects node points only
   (`plan._point_plan`, the `on_node` mask), so the records passed by construction. Now (`run_classes`) every
   REQUIRED after-state point is graded whose owning nodes (its node; between nodes, both ends of its rim segment)
   lie >= 2 mm inside a former failing run (>= 0.35 when they lie in the run nearer its end), by the Blender check
   state (`ns_after`) AND by the host check_glb on the exported GLB (matched by position, 358 of 358 / every one).
   - **Metal** (P4.metal.inside_runs, core: FAIL): 168 inside-run points, min **0.4253** (host **0.4252**) at
     (-8.33, 7.20) and 0.4262 at (-8.05, 6.97), the two points (w 2/3 and 1/3) between junction nodes 71 (0.4500)
     and 72 (0.4501); 57 points read below the tool's 0.45 target, 2 below §10's 0.44; max 0.4613; node points alone
     0.4500-0.4553 (60). Run ends 0.3696 (12). The instrument (65 stations, 1 mm apart) reads 0.4500-0.4639 and
     passes: its stations do not sample that segment's interior below 0.44.
   - **INVU** (P5.invu.former_failures, core: FAIL): 519 inside-run points, 19 below 0.45, min **0.44885** at
     (-60.40, 22.74) (halfway along segment 608-609); host 16 below, min 0.4489; node points alone 261, min 0.450048.
     Ends 0.4542. The instrument's share (0.9932 >= 0.97) passes.
   - **Why the tool was not changed (measured, owner item).** Growth is linear along a rim segment; the eyewire's
     capture steps non-linearly inside segment 71 (before seating -0.291 at node 71, -0.086 halfway, 0.102 at node 72).
     A point between nodes moves by a convex combination of its two nodes' moves, so raising it by r raises at least
     one node by r: bringing the w = 2/3 point to 0.45 puts a node at >= **0.4748** (w = 1/3: 0.4739), past §10's
     0.47 (the record carries `to_reach_target_a_node_reads_ge` per miss). The target at every point and <= 0.47 at
     every node cannot both hold on that segment. §10's band alone is reachable (both nodes +0.0144: points >= 0.44,
     nodes about 0.4645) only by aiming the points between nodes at 0.44, a number no design rule names (it is §10's
     grading bound): not done (stop rule). INVU's shortfall is reachable (a node at >= 0.4512; P5 has no upper bound),
     but correcting every point is one rule for both lenses and pushes the metal junction past 0.47 (the earlier fix
     round measured it: junction nodes 0.477 / 0.468). Owner: (a) keep node-point correction (both records fail §10
     as written: metal 0.425, INVU 19 points at 0.4489-0.4499); (b) correct every inside-run point (INVU passes, the
     metal junction nodes read >= 0.474: P4 fails on 0.47); (c) a §10 reading for a segment the frame's capture steps
     across (a design change).
   - **Corrections.** "INVU's lowest grown point is 0.450048" and "the metal fixture's lowest point is 0.450014"
     (review fix round 1, item 4, and its report), and item C's "P4 metal: former failures inside runs 0.4500-0.4553
     ... P5 INVU: 261 inside-run former failures 0.4500-0.4856 (0 below 0.45)", are node-point readings; over every
     inside-run point the minima are **0.44885 (INVU) and 0.4252 (metal)**. `proof.json` summary `margins` now hold
     every-point minima (P4.metal.inside_runs.min -0.0148, P5.invu.former_failures.inside -0.00115: failing
     records) and `margins_note` says so. P4.nylor.top_flats already graded every required point (host check).
2. **FOLD blocks spread along a run (major; behaviour unchanged, now reported).** Narrow eyewire
   (`SF.metal_scene(radial_web=0.5)`, host numpy = the proof's Blender plan counts): 34 BLOCKED = EXITS_MATERIAL 2,
   UNHIDEABLE 7, FOLD 25. The nasal -0.30 run (nodes 57-71) is stopped at both ends by 6 non-FOLD blocks: at nodes
   56/57 one EXITS_MATERIAL (value 0.110, limit 0.196) and two UNHIDEABLE (limit 0.196), at nodes 70/71 three
   UNHIDEABLE (limits 0.393 / 0.206). Fold pass 1 lowers each neighbour of a stopped node to one segment's slope
   (0.56-0.57 mm) where it needs 0.755, leaving it below pass: 4 points BLOCKED(FOLD), **direct**. Each later pass
   stops the next node: **21 knock-on** over passes 2-7, meeting at node 64. Every node of the run ends at 0 growth,
   and the partial seat leaves the run at -0.305 (36 FAIL after); with only their own clamps (0.613 mm) its points
   would have reached about 0.308 (pass 0.30). Control: the 0.9 mm web has no FOLD block. (The review's "about 2
   primary blocks" is 6 seeds at both ends, measured.) Now `SeatPlan.fold_generation` records the pass that blocked
   each point, `counts()['fold_blocks']` = {direct, knock_on, passes} (the tool report's before/after counts, the
   proof's `P4.metal_narrow.exits_material.measured.fold_blocks`), and E_TOO_MANY_BLOCKED's message and
   E_POSTCHECK's blocked list name the split. `test_seat_proof_fixes5.py` pins it: the split adds up, every knock-on
   block lies within one segment of an earlier one, the whole run is blocked and ungrown, and the control. **Owner
   decision 2 (FOLD) must be taken with this disclosed:** one node that cannot grow inside a run whose need exceeds a
   segment's fold slope leaves the whole run ungrown. Alternatives (design changes): spread the fold limit over more
   nodes (a taper toward the stopped node), or refuse (a gate) instead of a partial seat when FOLD blocks cascade.
3. **Item E(1) is still open (major, owner).** Unchanged: `P4.metal_narrow.exits_material` and `.zero_moved` FAIL
   (partial seat, 96 vertices moved, top capacity 0.3046-0.3067 against pass 0.30, 0 of 113 top points
   BLOCKED(EXITS_MATERIAL)); the gate E_CONCAVE (`plan.py`, the gate block) and the reason FOLD are rule additions
   outside §4 steps 4-5 and §12, and `P4.refusal.notch` passes only through E_CONCAVE. Owner: amend §4 steps 4-5 and
   §12 to a capacity floor of pass + 0.05 for these clamps, or keep §4 and give the fixture a web below 0.50 mm; and
   confirm or reject E_CONCAVE and FOLD with item 2's spreading disclosed. Then re-run P4.
4. **Correction: an accepted final HAS a translucent capturing part (minor).** Review fix round 1, item 1 said "No
   real model has a translucent capturing part, so no real verdict changes". False for the Tom Ford FT1123 crystal
   the owner accepted on 2026-10-06 (`data/ledger/owner_verdicts.jsonl`, GLB sha256 0e77e11e...): its 'Crystal front
   - integral bridge and endpieces.001' captures both lenses, and check_glb now adds 'crystal overlap 32.207 mm3 >
   0.01' per lens (32.20685 / 32.20689 mm3). Its verdict was already FAIL (400 of 400 stations FAIL the deep rule as
   the inferred acetate), so the verdict does not change, but an accepted model has a new failure reason. Host check
   4.6 s with frame_type acetate, 5.4 s without (this machine, now; the review measured 0.9 -> 4.5 s and 2.4 -> 7.3 s
   on a first call): within §9's 10 s. **For the owner:** `seat_lens` on that model would take the crystal front as
   translucent and V-bevel the lenses and cut a groove into the front (§5, §11.2), which changes an accepted look; it
   must not be run there without the owner's decision.
5. **The nylor end-zone reading goes to the owner as one decision (minor).** `P4.nylor.top_flats` (core) is now
   graded where §10's "flats" certainly applies: every required point >= 2 mm from a channel end >= 0.44 (153, min
   0.4499), no end-zone point failing the check, TRANSITION <= 3% (2 of 161). The reading for the 8 points within 2
   mm (the run-end ramp to 0.35 plus TRANSITION) is `P4.nylor.top_flats_end_zone`, owner_pending: TRANSITION 0.2053 /
   0.2054, below 0.35 but passing the check 0.3039 / 0.3044, ramp 0.408 / 0.4081 / 0.4299 / 0.43; rejecting it fails
   P4.nylor.top_flats at all 8. The instrument's record is split the same way (inside 60 stations, min 0.4477; its end
   zone 2 stations min 0.3636 and 2 transition stations 0.1902 / 0.1961, pending; a station missing even the reading
   fails it). `P4.nylor.top_flats_exception` is gone (subsumed).
6. **CORRECTION_AIM_MM = 1e-4 is an owner item (minor).** `deform.CORRECTION_AIM_MM` (= `stations.CLASS_EPS_MM`)
   exists so that corrected node points land at or above the target instead of about 1e-5 mm under it
   (float-noise compensation): node points clear 0.45 by 4.8e-5 (INVU) and 1.4e-5 (metal), and without it they land
   about 1e-5 below. It is kept and listed in `proof.json` summary `owner_decisions`: accept it explicitly, or remove
   it and grade the target with a stated measurement tolerance. The grader was not given a tolerance: at §4 step 2's
   0.005 INVU's 0.44885 would pass, a threshold change for the owner to make, not the grader. Even with the constant,
   19 INVU points between nodes read below 0.45 (item 1).
7. **Stray profile dump (minor).** `C:/Users/Shay/PycharmProjects/lenses/p.out` (15 KB, 2026-10-06 06:11, untracked: a
   cProfile of `geom.self_intersections` from the speed work) was moved out of the repository into the session
   scratchpad (`.../scratchpad/r2/p.out`), never committed. Profiling output stays in scratch.

`proof.json` summary also lists `owner_decisions` (items 1, 2, 3, 6) beside `owner_readings` (now including the nylor
end-zone reading).

## Implementation notes (stage "proof", re-run after the A-G fix round, 2026-10-06)

Re-ran P2-P5 as §10 writes them, with item G's change (`P5.oakley.top_lipped` report-only) and no other criterion
change: 27 headless Blender 5.2 runs on copies plus the grading, 3 min 6 s (`data/wp4/proof_final_run.log`). The four
source sha256 are unchanged (ea79d97b..., 56274deb..., a142d3c3..., bd7d2a91..., re-hashed after the run). No tool
source changed since the review fix round 2 run. The only edit is the runner `tests/proofs/wp4_proof.py`: it now keeps the
previous `proof.json` records under `superseded` (newest first). Each entry holds the old summary, cases, run timings
and tool digests, plus `compared_with_the_new_grading` (status, measured and timing-only changes, record by record).
- **Reproduced.** 262 records. Against the superseded grading: 0 status changes, 0 measured changes, one timing-only
  change (`P4.crystal.exact_time` 0.533 -> 0.464 s). The tool digests differ only by the runner's own hash.
- **Verdicts:** P2 PASS 71/71. P3: 34 of 40 graded pass, 6 FAIL, plus 2 report-only. P4: 109 of 112 graded pass,
  3 FAIL, plus 2 owner-pending. P5: 31 of 32 graded pass, 1 FAIL, plus 1 not applicable and 2 report-only (G).
  **Stop rule not triggered:** Oakley moved 0 vertices, and every seat call's moved set equals the band of its FAIL
  nodes (q001 58 per side, Prada 190 per lens, metal 132, narrow eyewire 96, the 8 acetate constructions, INVU 538).
  Every moved node fails by the check and by the instrument.
- **Remaining FAILs** are all unchanged and described in the rounds above. Nothing was tuned:
  - `P3.before_failing_where` (core, L/R): one of 95 failing input nodes lies at |x| 14.047, 0.047 mm outside M's
    'nasal 10-14' prose. This describes the input. The instrument puts 100% inside.
  - `P3.cover_unchanged` (core 2.13, instrument 2.07 / 2.06 mm): the grader compares the signed cover value. At a
    corner buried in acetate, that value grows as the corner moves deeper. Front and back stay covered 100%.
  - `P4.metal.inside_runs` (core): two points between the junction nodes 71 and 72 read 0.4253 / 0.4262, below 0.44.
    The instrument passes, with a minimum of 0.4500.
  - `P4.metal_narrow.exits_material` and `.zero_moved`: the narrow eyewire is a knife edge. Top capacity is
    0.3046-0.3067 against a pass of 0.30, so the tool writes a partial seat (96 vertices) instead of
    BLOCKED(EXITS_MATERIAL).
  - `P5.invu.former_failures` (core): 19 of 519 inside-run points between corrected nodes read 0.4488-0.4499, below
    0.45. The instrument passes (share 0.9932).
- **Owner-pending, not graded:** `P4.nylor.top_flats_end_zone` (core and instrument). Read literally, §10's 'top >=
  0.44 on the flats' fails at all 8 end-zone points: TRANSITION 0.2053 / 0.2054, 0.3039 / 0.3044, and the ramp
  0.408-0.430. The record keeps the case's verdict open (`pass_pending_owner`).
- **Tests:** `pytest -q tests/test_seat_*.py`: 344 passed in 8 min 22 s, with the proof not stale and a fresh grading
  equal to the record (`data/wp4/pytest_final_seat.log`). The rest of the suite ran 45 min under load
  (`data/wp4/pytest_final_rest.log`): 548 passed, 1 skipped (`test_cameras_repro`, the scipy fingerprint), 2 xfailed,
  and 2 failed. Both failures are in `tests/test_temple_relief_photo.py` (another workflow's untracked temple files; it
  imports no seat code): `test_faceted_model_explains_every_photo_crease` (1 segment, needs >= 3) and
  `test_confirmation_and_r2` (1 confirmed crease, needs >= 2).
  Speed was measured beside another session's WP6 proof run, so it is not a standalone figure: INVU case 43.3 s,
  Prada 32.6 s, FT 33.4 s (Blender wall time per case, 6 processes in parallel).
