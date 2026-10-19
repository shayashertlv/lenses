# WP6 v2: Astra handles facets (design, 2026-10-10, revised after 4-lens critique)

> Superseded where they differ by [WP6_V3.md](WP6_V3.md) (2026-10-12): fit, warp faces, readings, seeing and the
> rewritten prompt. This file stays the record of v2 and of the builder core, seeing stack and lab plumbing v3 keeps.
> **Removed 2026-10-13** ([archive/20261019/REVIEW_20261013.md](archive/20261019/REVIEW_20261013.md) §7; tag `review-20261013-pre-cleanup`): fades
> (`fade`, `E_FADE_*`, `W_FADE_EARLY`; a spec with a non-null `fade` is now refused `E_SPEC`), the v1 measuring
> instrument (`check_relief`, `measure.py` §5 numbers), `draw.section_strip`, the outline-line functions and the v2
> owner-sheet / light / rig / expressivity proof scripts. Sections below that describe them are history.

## 0. Owner decision and goal

Owner words, 2026-10-10:
- "please rebuild wp6" (reply to the review that proposed judging WP6 by eye on photo-matched renders);
- "i just want astra to be able to handle facets and such, thats it. you can do whatever, even rebuild from scratch".

**Goal:** Astra can turn a model's flat or wrong temples into faceted temples that look like the product photos. It
does this with tools and seeing aids that work for any faceted-temple product. Prada PR 17WS is the test case, not the
definition.

**Acceptance:** the owner's eye on photo-matched renders beside the photos. Numbers are information for Astra and the
owner, never gates.

**Hard refusals** (broken meshes are not taste):
- closed 2-manifold;
- thickness ≥ t_min;
- declared ridge/valley = built;
- strict spec validity.

**Retired 2026-10-10:**
- WP6 v1's S1, R1–R4, G1–G5, P2, the P3 decal limit, the STOP-I/C/R/S/T/P rules, `relief_rules.py` and the frozen
  dependency table;
- the delegated "KEEP STOP-I/R1/R3" decision.

The review showed the true or owner-preferred temple fails R1, R3 and P3, and that facet depth is not measurable from
these photos through silhouettes. The v1 code is archived (§7) under the local tag `wp6-v1-final`.

**Not in scope:**
- WP2 stage D–E and WP7. The new tools follow WP2 conventions so stage D can register them.
- Front-frame faceting. The seeing upgrades in §3 are part-agnostic; the builder is temple-only.

## 1. v1 failures → v2 answers

| v1 failure | v2 answer |
|---|---|
| No one saw a render that matched a photo | §3: fitted camera + per-photo temple pose + studio-dome rig + linear exposure + the same contrast stretch on photo and render; a gloss-black facet-reveal pass |
| Rig hid depth (uniform light: +1 mm changed 0.35–0.79 L8) | §3.2: elevation-gradient dome; rig check §5 T2 |
| Base forced planar over a zone → 2.40 mm template damage | §2: absolute thickness, wedge base, no zone |
| Facets stop at zone_end, cannot fade | §2: facets anywhere; fades |
| Edge rounding hidden; Bevel clamps to 0.03 mm or self-intersects | §2.5: core roll (planar quarter-arc bands) |
| Valley built where the photo shows a ridge | §2: declared crease shapes checked; ridge/valley drawn on the chart, section strip and photo overlay |
| Affine photo↔chart map, 5.42 mm residual | §3.4: `points_px` → model (d, y, t) through the fitted camera and raster |
| One layout, one depth tweak, a text reply ended the run | §4: variants sheet, judgements tied to seen comparisons, finish preconditions, `tool_choice=required`, finish-gated stop |
| JSON echo ate about 40% of tokens; Astra missed "valley" in a long receipt | §4.5: output budget; crease lines first in every receipt |
| Prompt discouraged thickness change and pre-framed failure | §4.4 |

## 2. Builder v2: `g3d.temple/2` (g3d/temple/)

Chart as in v1:
- `d` = hinge_z − z (mm back from the hinge plane);
- `y` up;
- `X` = world x of the +X temple at opening 0;
- −X is the exact mirror (both temples come from one spec);
- the opening rotates about the vertical hinge axis.

### 2.1 Spec

| Key | Meaning |
|---|---|
| `schema` | `"g3d.temple/2"` |
| `hinge` | `{z_mm, axis_xz_mm \| null, opening_deg}`. A null axis is taken from the knuckles (temple_bpy.default_axis), never from the inner face. |
| `outline` | `{top: [[d,y]], bottom: [[d,y]], tip_round_mm}` (v1 rules) |
| `inner` | `[[d, x_in]]` vertical inner face, PL, any shape |
| `base` | `{top: [[d,t]], bottom: [[d,t]]}` base thickness at the top and bottom outline edges, linear in y between them (wedges) |
| `nodes` | `{name: [d, y, t]}`. `t` = number (mm thickness from the inner face, X = x_in(d) + t), `null` (solved) or `"base"` (the base thickness there). Virtual nodes outside the outline are allowed. |
| `facets` | `{label: {poly: [nodes], plane: "nodes" \| {crease: [n1,n2], rise_per_mm}, fade: null \| {d0, d1}}}`. A crease plane holds the line n1-n2: an edge of the poly or (amended 2026-10-12, after run 6) the ends of a straight run of its edges (`spec.crease_run`: the corners between lie within `RUN_LINE_MM` 0.5 mm of that line and take the plane's value; when both runs between the ends qualify, the one closer to the line; a run that a node snap bends is `E_SPEC`, and so is a run corner off the line that another facet meets on both run edges, whatever its plane: the two could only be one plane; checked before and after the snaps; an exact tie between the two runs goes to the run whose sorted corner names come first). Run 6's top triangle is a thin band whose lower edge runs straight through the logo corner; three far corners of a thin band define its tilt badly (`E_ILL_CONDITIONED`), and a crease on one edge carries that edge's line on past the corner (core wp6-core-2.3) |
| `creases` | `[{facets: [f1, f2 \| "base"], shape: "ridge" \| "valley"}]`, checked after the build |
| `chamfers` | as v1 (band cut along the top/bottom outline edge) |
| `roll_mm` | core edge rounding (§2.5); 0 = crisp |
| `options` | `t_min_mm` 1.0, `station_step_mm` 2.0, `planar_tol_mm` 1e-6, `kink_sharp_deg` 25, `border_band_mm` 2.0, `snap_mm` 0.05 |

- **Extension:** every PL table (`inner`, `base.top`, `base.bottom`) extends linearly beyond its end segments, so
  virtual nodes are never clamped (np.interp clamps; do not use it unextended).
- **Knot canonicalisation:** all table knots, node d, fade d0/d1 and crossing d values within `snap_mm` snap to one
  shared d set. Nodes that lay on the outline are re-snapped onto it. Every snap is reported. Anything that cannot be
  snapped is refused with `E_SHORT_EDGE`.
- The spec sha is the sha256 of the canonical JSON.

### 2.2 Planes, values, cells

1. **Facet planes in world X:** X = a + b·d + c·y.
   - Known points are given node values (numbers and "base") plus values handed on by facets already solved through
     shared arrangement vertices. Base-border vertices are **never** known points; v1's base-zero seeding
     (planes.py:109-115) is deleted.
   - Plane source is "nodes" (widest non-collinear triple) or crease + rise. Rise = mm of X gained per mm moved
     perpendicular from the crease into the facet, in world X; negative falls toward the inner face. The receipt
     also gives the rise relative to the local base gradient.
   - v1's propagation, `E_FACET_NOT_PLANAR`, `E_FACET_UNDERDETERMINED`, `E_ILL_CONDITIONED` and `W_CONDITIONING`
     carry over.
2. **Vertex value:**
   - inside or on a non-faded facet → that plane. Two facets meeting at a vertex must agree within `planar_tol_mm`,
     else `E_RELIEF_STEP`.
   - faded facet → X = (1 − s)·plane + s·base, s = smoothstep((d − d0)/(d1 − d0)).
   - otherwise base: X = x_in(d) + lerp_y(t_top(d), t_bot(d)).
3. **Fades:**
   - (amended 2026-10-11, run 2) neighbours may carry different fades: a vertex shared by facets with different
     fade weights takes the least-faded value, so a faded facet keeps a standing neighbour's height along their
     crease; a fade that would leave a step against a standing neighbour (a faded outer or roll triangle held
     ≥ 0.05 mm off its own fade and tilted > 25° from it) is refused with `E_FADE_CLIFF`; 10–25° warns `W_FADE_HOLD`;
     a fade that moves nothing ≥ 0.05 mm warns `W_FADE_INERT`. The former `E_FADE_NEIGHBOUR` pushed Astra to fade every facet;
   - (amended 2026-10-11, run 3) a fade that leaves its facet flat for under 5 mm and under a quarter of its own d
     extent (counted from the side the facet stands on) warns `W_FADE_EARLY` with the fix (fade later, alike across a
     shared crease): run 3's upper-back triangle faded from its own front corner and never read as a face of its own
     (core wp6-core-2.2);
   - (amended 2026-10-12, after run 6; tightened by the build's review) a crease stands to its ends: two facets both
     faded at least `FADE_CREASE_WEIGHT` (0.02) anywhere along the crease they share are refused `E_FADE_CREASE`,
     naming the crease, its d span, the point where it starts to fade, both fades, up to two more such creases and
     the fix (start the fades where it ends, or let one stand). The first version checked only creases reaching the
     outline; moving the end node 0.25 mm inside passed it with the same smoothed surface (review P1). Ending the
     crease at a node and fading both facets from there is not offered: a narrow base strip between them rebuilds the
     same smooth surface, a wide one a trough (review round 2). Runs 3-6 all faded the faces
     around the top triangle's back crease (run 3 and run 6 from d 47-48), and the owner saw a smooth, "not one piece"
     top triangle (run 6) and soft lines (run 2). `E_FADE_CLIFF` no longer advises fading the neighbour the same way
     (that is this refusal); it says start the fade where the crease ends; `W_FADE_EARLY` says "fade later, from where its creases end". `FX.fade` now ends its ridge where the fade starts; the former dying ridge (`ridge_to=60`) is kept
     for the fade-value tests with this check off. Checked right after the face labels, before the planes (core
     wp6-core-2.3);
   - cells are classed by their actual fade weight: a cell entirely before the fade is an ordinary flat facet cell;
   - stations inside [d0, d1] are spaced ≤ min(station_step, (d1 − d0)/6).
4. **Facet/base borders:** a border vertex takes the facet value. The core inserts a line `border_band_mm` into the
   base parallel to each facet/base border, so the ramp absorbing any facet–base mismatch has a defined width. The
   receipt reports per border the ramp height (max), slope (deg) and length. `W_BORDER_RAMP` fires when the slope is
   above 8°. There is no claim that "base" nodes make a curved base flush.
5. **Stations:**
   - every table knot, node d, facet-edge/outline crossing, fade d0/d1 and border-band end, subdivided to
     ≤ `station_step_mm` over the whole temple;
   - stations are kept when roll > 0 (the core roll needs none removed).
6. **Cells:**
   - facet, inner, strip and cap polygons are planar (self-check ≤ 1e-9 mm);
   - every outer cell not on one facet plane (base, faded, ramp) is emitted as the core's ear-clip triangles, never
     as an n-gon. Blender and the GLB therefore share the core's triangulation. Base-cell non-planarity is reported
     as information.
7. **Sharp edges** (v1 rule kept):
   - facet/facet and non-faded facet/base edges are always sharp;
   - crease edges between two different facets are sharp while the crease still stands (> 0.5° between the
     intended surfaces), also in faded zones (amended 2026-10-11); edges inside one faded facet and ramp edges are
     sharp only at a dihedral ≥ 6°;
   - strips, caps and kinks follow v1.

   Custom normals are face normals on non-faded facet polygons and auto-smooth elsewhere.
8. **Crease check:**
   - after the build, each declared pair must share at least one edge inside the outline;
   - its length-weighted built shape must equal the declared shape, else `E_CREASE_SHAPE` naming the pair, the built
     shape and the dihedral;
   - pairs below 2° report "flat" and a ridge/valley declaration on them is refused;
   - "base" pairs are judged per edge with length shares;
   - (amended 2026-10-11, after run 4) a node no values can build: a named node strictly inside the outline where
     three or more creases meet, all declared with one shape, one of them refused, and a facet's corner there
     straight (within `STRAIGHT_NODE_DEG` 2°) or reflex. A convex corner holding a straight line is a prism along
     it (a reflex corner makes every neighbour coplanar with the facet), so those creases cannot all be ridges (nor
     all valleys). The refusal then leads with a refused crease at that node and names the fix: "facet X runs
     straight through node C, so the 4 creases there cannot all be ridges: one is a valley or two of those faces
     are one (check the photos or facet_variants); keep X whole" (field `straight_node`; the old "do not
     redeclare" ending is dropped there). Why: the real face 4 is one face whose lower edge runs straight through
     the logo's back corner, where four creases meet. With every crease there declared a ridge no single-face
     version builds (a tool test refused ~8,500 candidates with `E_CREASE_SHAPE`). Run 2 reached its split that way:
     a single face (request 25) was refused, and under "fix node t, do not redeclare" it split the face (26-28).
     Run 4 split face 4 in its first layout, from the side and top views, before any refusal; the hint does not
     guard that path, the provisional-layout confirm step does. Run 3 declared one valley there and kept face 4
     whole.
9. **Receipt** (compact; §4.5):
   - one line per crease (`F1|F2 ridge 12.2° declared ridge`) and per warning or refusal, sides de-duplicated;
   - facet lean against the local base normal;
   - thickness min/max with location;
   - border ramps;
   - decal motion;
   - hinge leaf gap / knuckle contact;
   - a station table every 10 mm (d, y_top, y_bot, x_in, t_top, t_bot);
   - snaps.

   Full tables go to a file, not to Astra.

### 2.3 v1 → v2 conversion (tests and owner sheets only)

`g3d/temple/convert.py: v1_to_v2(spec1)`:
- node X = x_in_lin(d) + T_lin(d) + r, where the lin tables are v1's zone lines over [d0, zone_end] extended
  linearly; t = X − x_in_v2(d);
- crease planes: rise2 = rise1 + (dX_base/dd)·n_in,d (n_in from planes.crease_plane);
- `base.top = base.bottom = thickness`;
- `zone_end_d` is dropped.

Test: on the preferred spec cfc11127, the v2 surface evaluated at every v1 outer vertex by barycentric lookup, and the
reverse, agrees within 1e-6 mm over the v1 zone.

### 2.4 Template from an existing temple (g3d/temple/template.py, numpy, host)

`template_from_mesh(V, F, side, hinge_z, axis_xz) -> (spec2, report)`. V and F come from the exported head GLB
(glb.load). Internally it samples a station every 1 mm and per station measures:
- outline y_top/y_bot = section extent;
- x_in = min X over the section, excluding the top/bottom 12%;
- t_top/t_bot = a robust line fit of outer X against y over the middle 76%, evaluated at y_top/y_bot.

Then:
- **Tip:** d1 = the last station with height ≥ 50% of the median height over the last 5 mm, plus a fitted
  `tip_round_mm`; the length lost is reported.
- **Emitted chains:** RDP at 0.03 mm on one shared knot set (target ≤ 40 knots per chain), with the simplification
  error reported.
- **Roll:** `roll_mm` starts at 0. The coding agent records a suggested value from the section strip; Astra sets it.

Report (information): one-sided original→rebuild distance (mean, p95 and max per region: hinge zone, middle, bend,
tip) and the inner-face deviation. The critique probe predicts p95 ≈ 0.32 mm. Shown to the owner on sheet #2 as
original vs revision 0.

### 2.5 Core roll (replaces the Blender Bevel)

`roll_mm` = r is built in the numpy core as N = 8 stacked chamfer sub-bands along the top and bottom outline edges.
Vertical offset s from the edge has depth c(s) = r − sqrt(r² − (r − s)²), s ∈ [0, r].
- v1's chamfer lines and splits are reused, so every cell stays planar and crossings inside the band are exact.
- The strip's outer edge moves in to X − r, so the side silhouette is unchanged. Caps follow the profile.
- Polygons are tagged kind `roll`.

Limit: r ≤ min(min thickness − t_min, 0.45 × min outline height − chamfer widths). Nodes or crease crossings within r
of the outline are snapped or refused (`E_ROLL`, naming them).

`use_clamp_overlap`, `_triangulate_nonplanar`, the distance-based facet-polygon heuristics and the face-attribute
provenance are removed from temple_bpy.

### 2.6 Blender layer (g3d/temple_bpy.py)

`faceted_temple` accepts /2 only; /1 is converted by the caller.
- It refuses targets with more than one material slot (`E_MULTI_MATERIAL`; multi-material support is deferred).
- **Decals transfer by chart:** each decal vertex keeps its (d, y) and its standoff from the old surface, and is placed
  at the new X(d, y) + standoff along the new surface normal (mirrored per side). Motion is reported, with
  `W_DECAL_SPANS_CREASE`.
- New `build_variant_meshes(spec) -> {side: Mesh}`: builds unlinked mesh datablocks for non-mutating variant renders
  (§4).

## 3. Seeing: photo-matched compare (extends WP2 seeing, not a parallel system)

### 3.1 Camera basis with pose

`data/wp6/pose_arm_resume_20261010/all_views_validation/fits.json` (sha-bound) holds the photo cameras and θ. It is
written into a WP3b-format `cameras.json`:
- the frame and skip are copied verbatim from `data/wp6/wp3b_glossy/cameras.json` (sha e20a7529…), never recomputed
  from a head bbox;
- each image gets `theta_deg {"+1","-1"}`, `theta_observed {"+1","-1"}` (false for side views 01, 05 and black-side),
  `hinge_axis_xz_mm` = [|x|, z] mirrored per side, `colourway` (target | variant) and `fit_status`.

Import it with `compare.import_cameras_json`. `Basis` and `freeze_basis` carry the new optional fields; old bases
still load.

Photos:
- 01–06 = colourway target;
- black-side = variant, "camera transported from 05";
- black-angled and black-front = variant, cameras transported from 02 and 04 (2026-10-11, owner: "yes, build 1-3",
  item 2; `tests/proofs/wp6v2_basis.py` with `g3d_host/camera_similarity.py`);
- black-oblique is excluded (its camera is a copy of 02's, never fitted).

**Colourway cameras by transport** (amended 2026-10-11). A black photo's camera is its source photo's camera moved
through a 2D similarity fitted by contour ICP between the two photos' mattes (`camera_similarity.transport`); θ is
copied from the source (`theta_observed` false). Each record holds a `transport` block (the similarity, residuals,
IoU, the source camera, `registration_candidates`) and a supplied matte (`mattes/<name>.png`). The matte is chosen
per photo by its background: black-side keeps the dark-studio matte (a white-background matte takes in its floor
reflection); black-angled and black-front take a white-background matte (Lab L < 92 or chroma > 6, specks under
0.2 % of the image dropped, holes up to 0.2 % filled), because the dark matte (L < 60 or chroma > 8) drops the lit
light-grey temple tops. Each twin is registered onto both candidate mattes and keeps the one with the lower mean
contour against its source photo's matte (the matte that segments the black photo the way the source's matte
segments its own photo).

| Photo | From | Registered on | Photo to photo: mean contour, IoU | Model check: IoU, temple IoU (source photo's) |
|---|---|---|---|---|
| B2 black-angled | 02 | dark matte (02's matte drops the same lit strips); camera unchanged | 0.82 px, 0.992 | 0.934, 0.875 (0.928, 0.868) |
| B3 black-front | 04 | white-background matte | 1.09 px, 0.992; reverse p99 6.85 px | 0.977, 0.886 (0.975, 0.888) |

The model check (`model_check` in each twin's record) poses the original model at the transported camera with the
compare's raster and scores it against the supplied matte. It is not a fit IoU (`iou`), so `seeing.gain_view_rank`
still ranks the twins −1 and the gain views stay reference-04 (target) and black-side (variant). The brief prints
both the photo-to-photo numbers and the model check. Build review (2026-10-11, P1): B3's first camera, registered on
the dark matte, read 3.70 px / IoU 0.970 (reverse p99 44 px), drew the temple tops about 6 px (~0.7 mm) low and
scored temple IoU 0.720 against that matte; re-transported, its scale moved +0.77 % and its centre y −4.4 px. Every
other record is unchanged. The earlier bases are kept, git-ignored: `data/wp6v2/basis-pre-b2b3/`, `basis-pre-b3fix/`.

Astra's inputs hold the target colourway and, since run 2 (owner, 2026-10-11: "yes, make the fixes and run it
again"), the black photos B1 (black-side), B2 (black-angled) and B3 (black-front), each with its camera:
photo_compare shows photo / overlay / gloss-black render, and facet_variants takes one as a 4th photo.

### 3.2 Renderer (g3d/render.py; all opt-in, photo_v1 stays the default, pixel-stable)

- **`rig: "studio_dome_v1"`**, frozen and recorded:
  - the world is an elevation-only gradient (world direction Z → ColorRamp): floor ≈ 0.45, dark horizon band ≈ 0.12
    over −5°…+10°, ramp to 1.0 at 60°, bright above;
  - one large soft overhead-front area key, no rim, no side keys;
  - EEVEE, raytracing off, 16 samples.

  Values are tuned once on test geometry (critique scratch facet_rig.py) and frozen. Uniform worlds are forbidden for
  glossy passes, because every facet would reflect the same radiance.
- **Per-view `pose`:** `{"+1": {theta_deg, axis_xz_mm}, "-1": …}`.
  - The temp scene links `obj.copy()` of every rendered object, never the originals.
  - Material slots on the copies are set to link OBJECT.
  - A posed side's copies get matrix_world = T(axis)·R_y(±θ)·T(−axis)·M and parent None. The side's objects are
    partRole temple selected by bbox x sign (temple body, decals, temple knuckles, leaf, pin).
  - All copies are recorded in `TempRender.made`.
- **Per-view `mesh_overrides`:** `{object_name: mesh_datablock_name}` swaps the copy's data, used for non-mutating
  variants.
- **Passes:**
  - `native_linear`: the scene's materials, view transform Raw, 16-bit PNG, dither 0;
  - `gloss_black`: the temple-body and front-body materials on the copies replaced by black glossy acetate (base 0.01,
    coat 0.6, coat roughness 0.05, IOR 1.49, roughness 0.12); others keep theirs.

  `native`, `clay` and `normal` are unchanged.

**Amended 2026-10-11 (lighting, after phase 1–2).** The scene is Y-up, so the dome gradient runs along world Y. Rigs:
- `studio_dome_v1`: the ramp was re-tuned in-sample on T2, which is not a calibration. It shows depth (gloss
  +5.3 L8/mm raw) but reverses the facet order against the photos (upper-crease step −14 vs the photo's +15).
- `photo_env` (g3d_host/lighting.py): a per-job, camera-relative environment fitted by NNLS (differential basis;
  smoothing λ by leave-one-photo-out cross-validation) over 19 declared lobes. Fitted on FRONT acetate of the target
  non-side photos (02/03/04/06). Temples are held out. It gets the crease-step sign right (+3 vs +15) but the order
  of the other facets is still wrong (black-side Spearman −0.56). 1 mm moves gloss only 1.2–2.6 L8. The front
  barely constrains the light near the horizon (condition number 450), where the temple facets mirror.

No rig yet reproduces the photos' facet tone order. Facet tones are not a reliable depth cue at the few-degree level.
Astra's primary cues are therefore geometric: crease overlays (layout), outer-edge offsets in the top/rear photos
(thickness) and the section strip (ridge/valley). Shading is a qualitative aid. The owner chooses the rig by eye
from candidates (photo_env, studio_dome_v1, and a catalogue horizon-band prior declared before rendering).
**Owner choice 2026-10-11: `studio_dome_v1` ("c is definitely the best").** It is the job rig.

### 3.3 Compare (agent/seeing.py `compare_to_photos`, g3d_host/compare.py, g3d_host/pose.py new)

- **`g3d_host/pose.py`:**
  - `rot_about_hinge` (moved verbatim from relief_views before it is archived);
  - `temple_parts(fitmesh, side)`;
  - `posed(fitmesh, pose)` for host raster masks, metrics, crops and overlays, so host and render agree;
  - `chart_at(points_px, ...)` → model (d, y, X) per pixel via raster.cast with the unpose.
- **Gain:** linear, per channel for `native_linear` (white balance) and luma for `gloss_black`; one per colourway per job. It comes from the front acetate body only (not partRole frame
  hardware), mask eroded ≥ 12 px, on the best-registered target photo (04 for Prada). It is applied to every photo of
  that colourway. A variant colourway without a usable front uses its largest acetate region and is labelled so.
- **Stretch:** one monotone curve from the PHOTO's acetate p2–p98 (linear in L), applied identically to photo and
  render, and recorded. Astra gets stretched images by default.
- **Outputs per photo:** one image, stacked rows at the photo's native crop scale. Never upscale; long side ≤ 1024
  after any downscale; zoom only via an explicit crop. Rows:
  1. photo (stretched);
  2. render `native_linear` (gain + stretch);
  3. overlay (photo + model outline + crease polylines + iso-d every 10 mm / iso-y every 5 mm grid, mm labels).

  The `gloss_black` render row is optional and on by default for owner sheets. Creases come from mesh dihedrals
  ≥ 6° on temple parts (generic: works for any model), coloured orange = ridge, blue = valley. Occlusion comes from
  raster depth.
- **Crop:** `temple` = posed temple ROI ∪ photo temple matte, padded 8%; `full` = the whole object.
- **Text** (rounded, compact):
  - per photo: px/mm at the temple ROI, temple IoU, outer-edge offset in mm every 10 mm in d for the top/rear photos;
  - a note that a common shift of outline and creases is camera error;
  - `points_px` results;
  - a `compare_id`.
- **Default photos:** those whose temple ROI is ≥ 40k px (Prada: 01, 05, 02). Results are cached per (geometry
  fingerprint, photo, rig, passes).

### 3.4 Chart and section images (g3d/temple/draw.py, host, PIL)

- `chart_image(spec, build)`: facets shaded by lean, creases coloured ridge/valley, nodes `name t=`, thickness
  contours, decal and hinge footprints.
- `section_strip(builds, stations=(5,15,25,35,50,65,80))`: X-vs-y section polygons of the current revision, the
  original and the previous revision overlaid.

## 4. Astra facet tools (agent/temple_lab, evolved in place; cli/approval/budget/rehearsal kept)

### 4.1 Tools (7; strict JSON; text + ToolOutputImage detail=high; photo ids: targets `01…06`, other photos `B1…`)

| Tool | Does |
|---|---|
| `facet_inspect()` | Spec summary (10 mm station table, nodes, facets, creases; a solved node's t prints as `solve -> 5.41`, one in no facet as `solve (in no facet)`; a crease-mode facet's rise_per_mm as the spec holds it, never rounded: rounded to 0.001 and entered again, a band's rise misses its neighbours and is refused), chart, section strip vs original, receipt lines, checkpoints, budget footer. Every crease line between two facets carries its margin (`lab.crease_margins`, in the builder's terms: planes flat in X = x_in(d) + t): each face's corner farthest across the crease's line and that corner's offset in X from the other face's plane, extended (+ = a ridge by that corner; a t error that size at that corner flips it when no end of the crease is solved), then the solved corners that set it (its ends and those far corners, never the corners solved by design) with the facet each is solved from: "crown\|logo ridge 13.5° declared; P +3.0 / C +3.8 mm across; B solved from crown". A line over 170 characters is cut in its margin first, at a clause end (`lab.fit_line`) |
| `photo_image(photo, crop?)` | Any photo of the job (target, another colour with or without a camera) or a crop, with its transform |
| `photo_compare(photos?, crop?, points_px?, depths?)` | §3.3 compare of the applied head at photos with a camera (a photo of another colour: photo / overlay / gloss_black rows); offsets per temple named charted / mirrored; returns `compare_id`. `points_px` charts a pixel through the current model (its t is the model's). `depths` (added 2026-10-11 after run 5; ≤ 12 `{photo, d_mm, y_mm, uv}`) MEASURES t: the point (d, y, x_in + t), placed as the lab builds it and opened by the photo's temple pose, moves along a 3D line as t changes, and `pose.line_depth` returns the t whose projection is nearest `uv` exactly (a projective function of t), with px per mm of t there and the pixel's perpendicular miss (large: the pixel shows another (d, y)). Both temples are tried; the one whose outer face the camera sees is taken; when both or neither do (front and rear views: both depth lines can fall on one image row, so the perpendicular miss cannot tell them apart), the temple whose point at the model's t (outside the outline: the base t there, extended) projects nearest the pixel; with no such t, the smaller miss. Printed per reading with the build's own t there: "d 47 y 9.2: t 7.25 (model 5.41) 7.9 px/mm, miss 0.0 px charted". Refused (`E_ARGS`) before any render for a uv outside the photo, a photo without a camera or not in this compare, values out of range. Amended 2026-10-12 after run 6: a one-photo reading takes its (d, y) as exact, so a d error becomes a t error (run 6's hinge corner read 3.55 at an assumed d 0 in B2; the inward hinge-side edge came from it). Where the inner face turns fast (≥ 0.3 mm of x_in per mm of d, e.g. 0.78 at the hinge) the reading says so. A reading with `also` (1-3 more `{photo, uv}` of the same point, any photo with a camera) is a corner reading (`lab.corner_reading`): least squares over (d, y, X) of the pixels (each photo on the temple whose outer face it sees, else both tried), with each photo's residual in px and the t at the reading's own (d, y) for that X printed first, as the value to enter (review P1: printed second, the found point's own t read as the value, and at the hinge it is run 6's 3.55): "d 0 y 10.2 with B1: t 4.77 here (+-0.12 mm per px; X 70.71); the point the pixels show is at d -1.58 y 10.57 (t 3.55 there, model out); residual px B2 0.0, B1 0.0"; past 2 mm from the reading's (d, y) it adds "it lies .. mm from ..: check the picks". Refused when the pixel Jacobian's condition number exceeds 20 (the photos see the point along nearly one line: B2+B1 1.9, 03+05 1.4, the opposite side views 05+01 about 940, which mirroring puts along one line of sight) |
| `facet_edit(parent_revision, reset_layout, edits[], judgement?, note, apply=true, photos?)` | Atomic ordered edits (§4.2) on a copy → build. A refusal returns at once with the edit index and code. With apply: checkpointed apply to both temples → compare of the given or default photos → new `compare_id`. Receipt (added 2026-10-11 after run 5; amended after run 6's reviews): `margins` (the creases flipped against the head, listed first whatever their margin, then those with a solved corner that sets them or a margin under 1 mm at a corner with a t of its own; at most 3, grouped by their solved corners: "B solved: crown\|logo P -3.6/C -4.7"); `solved` (solved corners on the outline arrangement whose t moved ≥ 0.1 mm from the scene head's, never those solved by design: "B 3.09 (head 5.51)", "D 3.10 (new)"); `crease_vs_head` (every crease built with the other shape than in the head's revision, declared or not, against the head whatever the parent; `margins` names their solved corners: "r1: crown\|logo ridge 13.5° -> valley 16.2°"); `diff_vs_head` when the parent is not the head (applying a variant with no edits shows "no change" against the variant and its change against the head here). A refused build's message (≤ 400 characters, cut at a clause end before its note; `lab.refusal_message`): `E_CREASE_SHAPE` ends "; solved corners: B" (the solved ends and far corners of that crease, from the spec built without its declarations: `lab.refusal_solved`); `E_RELIEF_STEP` on a crease-mode facet (the refused one, else the neighbour whose value it misses, else another neighbour it takes values from) ends "; rise_per_mm 0.041638353 meets it" (`lab.relief_rise`: the misfit is linear in the rise, so one more build gives the root; at least 7 significant digits, more while the rounding alone misses; the printed value is confirmed to build the spec without its declarations), otherwise the misfit prints with 6 significant digits. Amended 2026-10-12 after run 6: `E_FACET_NOT_PLANAR` (a facet's own given values fit no plane) ends "; one solved instead: C -> 5.84 (-1.90), E -> 8.45 (+3.20)", each given corner solved alone (planes only, `build.solve_planes`), smallest move first, at most 4 (`lab.solve_options`); `E_ILL_CONDITIONED` ends "; corners without a value: E; a crease + rise plane can hold A-E (through C)" (`lab.conditioning_hint`); a crease plane on one edge of a longer straight run, or a nodes plane over one, adds "; or a crease plane on the run A-E (through C)" to its `E_FACET_NOT_PLANAR` note, and a crease-mode facet with one given corner at least 2 mm off its line adds the rise_per_mm that meets it (review P2: run 6's crown held the edge A-C, so only the tip could be solved, into the wing). A crease margin whose end lies on a crease plane says so ("C on a crease plane"): a t error at its far corner moves that end too |
| `facet_variants(parent_revision, variants[2..6]{label, edits[]}, photos[1..4], judgement?)` | Builds each variant and renders it through `mesh_overrides` into posed copies, so the live scene never changes. Photos (amended 2026-10-11, owner: "yes, build 1-3", item 3): 1..4 with a camera, each in an earlier compare, at most 3 of them target photos (a 4th is a photo of another colour; a job without one takes 3). A target photo's panels are the native render; a photo of another colour's are the gloss_black render with its compare's gain and stretch. Every render panel draws the photo's outline and that model's own crease lines (dihedral ≥ 6°, ridge orange, valley blue, depth-tested as the compare overlay; the sheet title says "creases from 6°"), so sheets also compare layouts. Returns one sheet per photo (photo, head, variants; ≤ 1280×900, 2 or 3 columns by the crop's aspect; a refused variant is a labelled panel) plus each variant's revision. Labels are distinct ignoring case, not 'head' and not ending in '_' (they are the render file names). The worker's render deadline is the 90 s table value minus a 15 s host margin (`loop.FACET_VARIANTS_HOST_MARGIN_S`). Each variant's text also carries `solved` and, when it flips a crease against the head, `crease_vs_head` (added 2026-10-11 after run 5), and `margins` (as facet_edit's, one per item); a refused variant's message is facet_edit's (the solved corners of an `E_CREASE_SHAPE`, the closing rise of an `E_RELIEF_STEP`) |
| `facet_restore(checkpoint, judgement?)` | Restores a checkpoint; `crease_vs_head`: the creases it builds with the other shape than the head it replaces, naming their solved setting corners (it has no margins): "r5: crown\|logo valley 16.2° -> ridge 13.5° (B solved)" |
| `facet_finish(status: matched\|stuck, best_checkpoint, summary, judgement)` | Exports best_checkpoint (validate_spec report included) and ends the run if accepted; otherwise refused with the missing precondition named, and the run continues |

**Judgement** = `{compare_id, versus_compare_id|null, overall: better|same|worse|mixed, photos: [{photo, verdict,
largest_difference ≤ 200 chars, at_d_mm|null, at_y_mm|null}], best_checkpoint}`.
- It is required on the next action tool after any compare and must name the latest `compare_id`, else
  `E_JUDGEMENT`.
- The host logs judgements. It never grades them.

**Finish preconditions** (procedural, never quality grading; 1–4 only since `2ca5be9`):
1. ≥ K distinct applied heads compared (K owner-set per run, default 4). A head counts only when its BUILT temple
   differs from every head already counted by ≥ 0.2 mm of normal surface distance or outline shift (computed by the
   host on the builds). Amended 2026-10-11: topology and crease-shape tests were dropped after five review rounds
   showed knife edges near any crease threshold; a real layout or ridge/valley change moves the surface.
2. ≥ 1 `facet_variants` call with ≥ 3 variants pairwise ≥ 0.3 mm apart.
3. best_checkpoint's geometry fingerprint has a compare on all target photos (from cache when available).
4. `stuck` only: the last 3 judgements cover 3 distinct heads and none says better.
5. Retired as a precondition in `2ca5be9` (owner, 2026-10-11, after run 4: "yes, build 1-3 and make the rule
   information only"): the outline line is now information only; `state.finish_missing` checks 1–4, and FINISH_DOC
   and the instructions state 1–4. In run 4 the rule kept Astra chasing one photo's numbers (13 of 14 judgements of
   03). The rule as it was (record): added 2026-10-11 after run 3, owner: "yes, implement changes"; `matched` only:
   the latest compare of best_checkpoint's revision at the top photo has its outline measured and within 1 mm of
   the common offset at every station. Per side with ≥ 4 measured stations, the common offset is the median of the
   photo-minus-model outer-edge offsets (a shift every station shares); deviations count to 0.1 mm as printed; a
   station is off when every such side that measured it lies more than 1 mm from that side's common offset, the same
   way; a station no such side measured, inside all their measured spans, is unmeasured (`state.outline_check`, still
   computed). Every compare that shows a target top photo still prints its outline line ("outline: common −0.6;
   >1 mm: d30 +2.1 …"), as information. Why it was added: run 3 finished "matched" while its own summary named
   "1–2 mm" top-view shoulder offsets, with the front block too slim from above (d30 +2.1, d40 +1.2) and too wide
   behind it (d60 −1.5). It was a stop rule for Astra, not the owner's acceptance (the owner judges by eye). The top
   camera is oblique (pitch 60°): a station is one image row through the model's outermost point near that d. No
   tool-test head met it, and the owner's earlier "preferred" shape fails it (PROGRESS.md).

### 4.2 Edits (strict anyOf union; the tool layer converts to and from the §2.1 maps; schema sha pinned in tests)

- `{op: set_node, name, d_mm, y_mm, t: {mode: mm|base|solve, mm: number|null}}`
- `{op: delete_node, name}`: refused if referenced unless the facet is deleted in the same call
- `{op: set_facet, label, poly: [name]{3..32}, plane: {mode: nodes|crease, crease: [n1,n2]|null, rise_per_mm:
  number|null}, fade: {d0_mm, d1_mm}|null}`
- `{op: delete_facet, label}`
- `{op: set_crease, facets: [f1, f2|"base"], shape: ridge|valley}`
- `{op: delete_crease, facets: [f1, f2]}`
- `{op: splice_chain, chain: outline.top|outline.bottom|inner|base.top|base.bottom, d_from_mm, d_to_mm, points: [[d,
  v]]}`
- `{op: offset_chain, chain, d_from_mm, d_to_mm, delta_mm, taper_mm}`
- `{op: set_scalar, key: roll_mm|tip_round_mm, value}`

`reset_layout` clears nodes, facets and creases.

### 4.3 Run settings

- `ModelSettings(tool_choice="required")` and `Agent(reset_tool_choice=False)`.
- `tool_use_behavior` = a custom function that is final only when facet_finish returns accepted.
- All three are bound into `RunSettings.approval_settings` and the tool-registry sha.
- **Fallback** (if gpt-6-astra rejects required tool choice): a text final output without an accepted finish
  continues with one factual host note, up to max_continuations, then ends with status `ended_without_finish`.
- **HARD_TIMEOUT_S** entries for all 7 tools (T5 timings × 3). A `threading.Lock` is acquired inside the worker thread
  with timeout 0; a busy call returns `E_BUSY` at once.
- **Context guard:** at 240k counted input tokens, one `call_model_input_filter` pass replaces superseded compare and
  variant images (not the latest 2, not best_checkpoint's) with text stubs, bound in the approval.
- **Budget:** cap and effort per run from the owner. Estimates use T5's real tool outputs with the fitted formula
  tokens ≈ 0.42·chars + 0.975·⌈W/32⌉⌈H/32⌉ + 112 per image. (Amended 2026-10-11, build review P2.) Output tokens per
  request come from the paid facet runs at effort max (`cli.OUTPUT_TOKENS`, budget.json: run 2 mean 1,635, run 3
  2,064, run 4 1,852): expected = run 4's 1,852, high = the highest run mean, 2,064. The old 580 / 851 priced every
  facet run about 30 % low (run 4's table priced 40 replies at $7.47; run 4 settled $8.21 in 32). facet_variants is
  priced at the most sheets one call of the job can return (`cli.variant_sheets`: 4 with a photo of another colour,
  else 3; the rehearsal's largest sheet repeated), and a request on a package whose calls return more sheets than
  its estimate table priced is refused.
- **Isolation:** the starting scene is a byte copy of the original. Revision 0 = template_from_mesh, applied at
  prepare. The preferred spec is never given to Astra.

### 4.4 Prompt (agent/temple_lab/instructions.txt, rewritten)

The prompt is general for any faceted temple and any photo set; the job's facts are in the brief (amended
2026-10-11, below). Must state concretely:
- **(a) Coordinates:** d mm back from the hinge; y mm up (y=0 named); t absolute thickness from the inner face; one
  spec drives both temples; the brief says which photos show which temple.
- **(b) Position** (amended 2026-10-11, twice): measure (d, y) with `points_px` in the views square-on to the outer
  face; confirm in other views, never measure there (a reading goes through the current model's depth). Its t is the
  model's, never a measurement: measure t with photo_compare's `depths` in the moderate-angle views (the brief gives
  their px per mm of t), reading a crease's corners, of both faces, in one photo (amended after run 6's review) on the
  temple whose outer face it shows. The grid is valid inside the model outline. A common offset of outline and creases
  is camera error. Amended 2026-10-12 after run 6: one photo takes the (d, y) as exact, so a d error becomes a t error
  (worst where the inner face turns, as at the hinge): each corner is given also in a photo from another direction
  (`depths`' `also`) and enter the t it prints for its (d, y). Cameras differ by about a mm, and so do one corner's
  readings in different photos (run 6's logo corner: 7.5-7.9 from the angled views, under 6.9 from the top view).
- **(b2) Lay out, then confirm** (added 2026-10-11; amended the same day, build review P1): lay the faces out in the
  square-on views with a first depth; that layout is provisional. Before refining depth, check it in a calibrated
  view that sees the outer face at a moderate angle: keep a crease only where that view also shows it, remove one it
  contradicts (no line although it clearly shows neighbouring creases of similar angle), add one only where both views
  show it. Every facet_edit that changes the layout compares at a square-on view, such a view and a glossy photo with
  a camera. When the views disagree on where a crease runs, a depth that puts it on the photo's line in both means
  depth, none means layout. A number (an offset or outline station) changes the layout only when both views show the
  new crease or its absence.
- **(c) Ridge or valley** (amended 2026-10-11, three times; the second after run 5, the third after run 6's review,
  both below): decided per crease the photos show, from evidence about that crease, before and apart from any bracket
  about something else: (1) the t of the corners that set it, its ends and each face's corner farthest across its line
  (as the host names them, `lab.crease_margins`), measured with `depths`; these get a measured t, never solve (a
  solved corner takes another facet's plane, extrapolated, and can flip the crease). The planes are flat in
  X = x_in(d) + t, not in t, so t is never extrapolated by hand: Astra enters the measured values, builds with apply
  false, reads the shape and margin the host prints (the receipt's crease lines and `margins`; facet_inspect for the
  head) and declares the shape the measured values build; a margin within the readings' error leaves (1) open; (2)
  the views that look along it (the brief names them): a valley never forms an outline, so where the photo's outline
  runs along the crease it is a ridge, unless another face or part forms that outline. The section strip shows what
  the model built; declare every crease. A crease-mode facet's corners off its crease edge are solved by design; a
  corner no view can read stays solve, and the note says so. A face that then has more measured corners than one
  plane holds (`E_FACET_NOT_PLANAR`) is kept whole and the corner the photos constrain least is solved and named
  in the note (amended by the final pre-run review: no fold or split is suggested); solving moves that corner (the
  receipt's `solved` shows how far), so the faces it bounds are compared. Amended 2026-10-12 after run 6 (owner: "the
  top triangle is still not one piece and being smoothen and not flat ... please review evidence thoroughly and the
  tools and instructions"): the corner solved is the one that moves least (the refusal lists each one's solved t).
  Run 6 kept the logo corner's thick reading (C 7.49), solved the crown's tip E from the crown's plane (9.8, about
  4 mm proud of the arm, the top view's -3..-4 mm at d 70-80) and then faded the back faces to hide it; the two-photo
  readings put the tip at 5.25 and C solved at 5.8. A crease-mode facet's corners other than its crease's ends are
  solved by design (the corners of a straight run included). With (1) and (2) open, Astra enters values within the
  readings' error that build a ridge between outer faces unless a bracket at the moderate-angle and glossy views shows
  a valley (the owner rejected an inward edge in runs 5 and 6; with corner readings run 6's hinge-side crease lies
  within the readings' error); a crease built flat (W_FLAT_CREASE) stays undeclared; the corner solved for a plane
  stays solved, and E_CREASE_SHAPE's "measure the solved corners first" leaves it out (review P1: the two rules
  looped on the logo corner). Every
  crease in `crease_vs_head` must be a flip Astra decided from that crease's evidence, else the change that made it is
  undone. Where one face's edge runs straight through a corner, the creases there cannot all be ridges (nor all
  valleys): read from the photos which one has the other shape (§2.2 step 8). On `E_CREASE_SHAPE`, first give any
  solved corner that sets that crease a measured t (the refusal names the two faces' solved corners,
  `lab.refusal_solved`); then declare the shape its measured corners build, or change node t values not measured (or
  the fade, as the refusal says); when the refusal says the creases at a corner cannot all have one shape, correct the
  declaration the photos contradict. Never redeclare just to match what unmeasured values build (the refusal's "do not
  redeclare"), never split a face to get a refused declaration built. ("The shading order" is no longer a cue; §3.2:
  no rig reproduces the photos' order.) A thin line brighter than both neighbours that stays on a crease from two
  directions supports a ridge; its absence is no evidence.
- **(d) Thickness** (amended 2026-10-11, build review P2): read the outer-edge offsets of the photos that print them
  (top and rear views, named in the brief) in mm; t_min 1.0; quote the station table; an offset common to every
  station is camera error. Every offset, like the outline line of a target top view (information only since
  `2ca5be9`, owner 2026-10-11), is one view's silhouette: weigh it with the other views, never change the layout or a
  crease's shape for it alone (amended after run 5): an outline offset measures how far out the outermost face
  reaches, not the shape of a crease below it.
- **(d2) Top and bottom faces** (added 2026-10-11, run 3; amended twice the same day): the side views see a band along
  the top or bottom edge almost edge-on, so they show only where it meets the outer face; place and confirm the
  band's own creases, and judge its tilt, in views that see it at a moderate angle (the brief names the views that see
  faces pointing up or down) and in the front/rear silhouettes; a face narrow from the side but broad from above
  points mostly up.
- **(e) Fades:** a crease that dies out ends in a fade or a small dihedral, never a hard edge extended beyond what the
  photos show. Amended 2026-10-12 after run 6: a crease between two facets stands to its ends (`E_FADE_CREASE`); to
  let one die out, end it at a node and fade both facets from there. Never fade to fix a thickness: a face standing
  out at its end has a wrong corner t; read that corner. (The text is cut to fit the 14,000-character cap: 13,974.)
- **(f) Roll:** highlight band width in px ÷ px/mm ≈ roll, a first guess only (it depends on the light).
- **(g) Creases** (amended 2026-10-11): a line is a crease only where photos from two different directions show it at
  the same place; the second must see that face at a moderate angle (an edge-on view's lack of a line is no
  evidence); with no such view, a line in two photos under different light is the best evidence, named in the note.
  Never merge two faces a visible crease separates, never split a face no photo shows divided; a silhouette corner
  can be a crease end, a bend in the edge or the silhouette passing between faces.
- **(h) Depth:** `facet_variants` brackets wide first, then ±1 mm; its panels draw each model's crease lines from
  6°, so it also compares layouts (a crease under 6° draws no line). One bracket answers one question (amended after
  run 5): its arms differ only in the values that question is about (a bracket about a crease's shape: an arm of each
  shape, differing in that crease's corner t and declaration), plus only the values the builder requires to keep
  shared corners at one height (`E_RELIEF_STEP`; amended after run 6's review, `rise_per_mm: solve` not being built;
  since the final pre-run review the refusal names a crease-mode facet's closing rise_per_mm);
  a depth wanted for something else that builds the other shape is evidence to weigh, never a reason to redeclare.
  Applying a variant shows `diff_vs_head`.
- **(i) Judgements:** name the largest difference per photo with its (d, y), and make the next edit address the
  largest one judged to be shape.
- **(j) Finish:** the preconditions, verbatim. Continue until the pairs read as the same object or the stuck
  condition holds.

Also:
- no fixed full-view sweeps, failure pre-framing, repo history or "WP6" jargon; ONE targeted confirmation view after
  a layout change (b2) replaces the former "no re-view rituals";
- no product or photo-set facts (colour, material, photo ids, "almost flat", top-photo-only rules): the brief carries
  them;
- every tool name appears in the prompt;
- `contract_problems` also scans parameter-schema descriptions for forbidden phrases.

**Amended 2026-10-11 (owner, after the review of Astra's guidance: "yes, build 1-3 and make the rule information
only"; decision 1: general instructions, the job's facts in the brief, a confirm-from-another-angle habit).**
- Why: 26 of 106 instruction sentences were not general (20 came from this product's failures). (b) "read (d, y) from
  the near-perpendicular side photos with `points_px`, never from oblique views" made the side direction the only
  source of layout. The old (g) "two photos" compared one direction with itself: B1 has 05's camera and 01 is its
  mirror. The run-4 sentence "A corner in the top photo's outline is a corner of the faces … One plane across it
  cannot make the corner" is deleted, because it is false in general (one face whose top edge bends makes that corner
  too). "Include B1 in every layout compare" and "include the top photo in variants" are replaced by choosing views
  by what they see. The ban on oblique views keeps one valid core: on a view at angle θ to the face, 1 mm of depth
  error moves a `points_px` reading by tan θ mm (0.24 mm at the side views, 1.9 mm at 02). So readings are measured
  square-on and only checked elsewhere.
- **The brief** (`cli.brief_text`, from the job's pose basis and photo rows) now gives, per photo:
  - the camera's yaw and pitch;
  - the temple whose outer face it sees, and the angle to that face (square-on < 30°, moderate 30–75°, edge-on ≥ 75°),
    with tan θ;
  - faces pointing up or down that it sees;
  - its number lines (offsets for top/rear views, the outline line for a target top view);
  - its camera's fit: the model-silhouette IoU and contour, or the photo-to-photo transport for B2/B3;
  - colour and finish from the job's photo rows (`COLOURWAY_PHOTOS` gained `finish`; no hard-coded "acetate" or "far
    more clearly").

  It then lists the direction groups (cameras within 10°, a mirrored side view counting as the same direction). For
  Prada: 01, 05, B1 | 02, B2 | 03 | 04, B3 | 06. Moderate-angle views: 02 and B2. It closes with revision 0's
  structure (no facets, no creases, roll 0, a wedge).
- Kept after the skeptic's review: the run-2 guidance "faces distinct in the photo but barely distinct in the render
  → the dihedral is probably too small" (confirm it, then raise it); no redeclare on `E_CREASE_SHAPE` (the 4 refusals
  in runs 2 and 4 were all fixed through node t); the outline-station mechanism; `E_FACET_UNDERDETERMINED` (collinear)
  vs `E_ILL_CONDITIONED` (narrow); facet_edit compares omit the start offsets. Not adopted: "prefer the fewest faces".
  Added: `E_RELIEF_STEP` (facets sharing a corner are changed together), and the facet_variants rules (1..4
  photos with a camera, each compared before, at most 3 target photos; crease lines on every render panel).
- Length: 12.2k characters (was 11.5k); the brief grows from 1.0k to 3.3k, and the first request from about 30.0k to
  31.3k tokens (`cli.first_request_tokens`). Tests: `tests/agent/test_temple_lab_cli.py` (general needed phrases,
  banned product phrases and photo ids, retired rules, the numbers whitelist, FINISH_DOC verbatim, the real-basis
  brief). No paid run has used the new prompt yet.

**Amended 2026-10-11 (the build review's confirmed findings, and the owner's run-4 complaint "its split into
two").**
- **The corner rule (c), new.** Face 4 is one face whose lower edge runs straight through the logo's back corner,
  where four creases meet. When one face's edge runs straight through a corner, the creases there cannot all be
  ridges (a convex corner holding a straight line is a prism along it): at least one is a valley, or two faces are
  coplanar. With every crease there declared a ridge, every single-face version is refused (`E_CREASE_SHAPE`,
  ~8,500 tool-test candidates). Run 2 reached its split through that refusal (requests 25-28) under "on
  `E_CREASE_SHAPE` … do not redeclare"; run 4 split in its first layout from the side and top views, which only the
  confirm step (b2) guards. Run 3 declared one valley there and kept face 4 whole. When no silhouette shows which
  crease is the valley, Astra brackets the candidates with facet_variants at the moderate-angle and glossy views. The instructions now state the
  rule, the builder's refusal names it (§2.2 step 8), and a declaration the photos contradict is corrected; never
  redeclared just to match the build, never "fixed" by splitting a face. This replaces the first record's "no
  redeclare on `E_CREASE_SHAPE`" for that case only; every other `E_CREASE_SHAPE` is still fixed through node t.
- **(b2), P1:** the first layout came from the square-on views alone (one direction: 01, 05, B1), and "the layout
  changes only when both views show … its absence" could never remove a crease the square-on view put in (that view
  is where its line was seen). The square-on layout is now provisional: a crease stays only where the moderate-angle
  view also shows it. "Both views" now applies only to changes a number prompts (an offset or outline station), as
  the pre-build skeptic's correction #9 meant.
- **(d), P2:** "a station that departs from it is shape, whether or not the start model shows it too" is deleted: the
  same stations feed the outline line, which is information, and the judgement rule then pushed a persistent
  station toward a layout change.
- **P3s:** the ridge/valley sentence covers creases the photos show and says the section strip is the model, not
  evidence; (d2) as above (the side views show only where a top band meets the outer face); the light sentence names
  the job's fixed light from the brief instead of "one fixed studio light"; the variants bullet says crease lines are
  drawn from 6°; the TOOLS photo_compare line is shorter (render row for target photos only).
- **The brief** (`cli.brief_text`) now also gives: the job's render light (`--rig`, described from g3d/render.py:
  `cli.RIG_TEXT`); the names "charted" and "mirrored" that the compare text prints per temple; the twins' check
  against the starting model next to their photo-to-photo numbers ("photo to photo: IoU 0.992, mean contour 1.1 px
  … checked against the starting model's silhouette: IoU 0.977, temple IoU 0.886" for B3); "No finish is given for
  01, …, 06" (finishes are job data given only for the other-colour photos, so no target photo satisfies the
  "glossy photo" compare requirement by default); and "edge rounding (roll_mm) is not known" (`brief_text(roll_mm=)`
  prints a known value). The offset-view list is `seeing.OFFSET_VIEWS`, imported, no longer a copy.
- Length: instructions 12,462 characters (was 12,236; the corner rule and the provisional layout added, other
  sentences shortened); the Prada brief about 4.0k characters (was 3.3k). Tests: `test_temple_lab_cli.py` (needed
  and retired phrases, the brief's light / temple names / finishes / edge rounding, the real-basis twin lines, the
  estimate's output tokens and variant sheets). No paid run has used this prompt yet.

**Amended 2026-10-11 (owner, after run 5: "the inward edge is the one thats really bugging me since the layout is
perfect. what caused it?", then "ok, fix it and run again, max 15$").**
- What happened (`package_005`, $0 review): the crown|logo crease (the top triangle against the logo face) was built an
  8.0° valley (r0014); the photos say ridge (about 80 %; with the corner depths read in B2 and 02 a ridge comes out in
  0.96 of perturbed readings, 0.81 with every error doubled). The crease's back corner B had t solve, so the builder
  took it from the crown's plane, extrapolated: 5.41 against 7.0-7.5 in B2 and 02. The crease's sign became a side
  effect: var013 (request 14, parent r0002, a 43.3° ridge) changed five values at once (C, R, E, P and an outline
  splice) to chase the top photo's outline and declared a valley in all three arms (7.9°, 28.4°, 42.2°); no single one
  of those changes flips the crease. Astra never saw B's solved value (the receipt listed none, inspect printed null),
  had no way to measure t in a moderate-angle view (`points_px` charts through the model: its t is the model's), and
  applying the chosen variant printed "diff: no change" (against the variant's own revision), so the flip was never
  shown against the head. The views that look along the crease (04, B3) were first compared at requests 24 and 28,
  after the decision. They are weak evidence here: in the valley head the top band forms that outline, in the ridge
  head r0002 the crown's edge at the crease does and matches B3 within ±0.34 mm, but moving the logo's far corner
  enough to flip the crease moves no outline by more than about 0.5 mm. Gloss decided nothing: no bright line on that
  crease in B1 or B2, and none on the logo's side edges, which are certain ridges.
- Tools (§4.1): `depths` measures t; receipts list solved corners and name them on crease lines; `crease_vs_head` and
  `diff_vs_head` state flips and changes against the head. Not built (recorded): a crease margin and flip point
  ("flips at P t≈5.0"), which surface forms the outline in each run of rows, process refusals for brackets that mix a
  crease flip with other changes, and `rise_per_mm: solve` (every ridge build of the $0 ridge test was refused
  `E_RELIEF_STEP` until the top band's rise was re-solved by hand).
- Instructions (general, no product facts): (b), (c), (d) and (h) as amended above, plus the gloss-line sentence.
  The old ridge/valley source "from the silhouettes of views that see it edge-on (a convex kink in the outline is a
  ridge, a notch a valley)" is retired: an outline shows a crease's shape only where that crease forms it. The TOOLS
  section became one paragraph (the tool descriptions state the rest; the offsets' print format is left to
  photo_compare's and facet_edit's descriptions, checked in the test).
- **The brief:** "points_px moves 1.91 mm per mm of depth error" became how finely `depths` reads t:
  `cli.t_px_per_mm`, the image length of 1 mm of t 50 mm behind the hinge of the temple the camera sees (unposed): 02
  "depths reads t at about 7.7 px per mm", B2 8.1 (run 5's own reading at B2's node B: 7.87; the photo review's
  7.4-8.0); square-on views "reading t with depths is unreliable here: about 2 px per mm". New line: "Front and rear
  views look along creases that run front to back: 04, 06, B3" (cameras within 25° of the temple's length).
- Length: instructions 13,952 characters (was 12,804). The test cap rose from 13,000 to 14,000: the new rules add
  ~1.9k, cutting repetition took back ~0.8k, and every remaining rule is a needed phrase. Brief 4.4k (was 4.2k). First
  request ≈ 45.2k characters / 33.1k tokens by the formula (run 5's estimate: 42.8k / 32.2k), the tool schemas'
  `depths` and receipt descriptions included. Tests: `test_temple_lab_cli.py` (needed phrases for every new rule,
  retired phrases, `t_px_per_mm` exact on an orthographic camera and equal to `pose.line_depth` on a perspective one,
  the real-basis brief lines); `loop.contract_problems` on the real registry of a scratch package (`tt_run6_pkg`, $0):
  no problem. No paid run has used this prompt yet.

**Amended 2026-10-11 (run 6's build reviews, confirmed by a verifier; $0, in-memory TOOL TESTs on copies of
`package_005`'s r0014).** The tools track built the host's crease margin (`lab.crease_margins`), the corners solved
by design (`lab.by_design`), the solved corners in an `E_CREASE_SHAPE` refusal (`lab.refusal_solved`), facet_restore's
`crease_vs_head` and the front-view temple choice in `depths`; the prompt uses them as follows.
- **P1, the ridge test done in t.** Rule (1) said "a ridge when a face's far corner has a smaller t than the other
  face's plane, extended, gives there". The builder's planes are flat in X = x_in(d) + t (`g3d/temple/planes.py`), and
  x_in curves (r0014: 65.93 / 63.16 / 62.32 at d 0 / 22.5 / 47). With A 4.5, P 6.0 and B 7.25, the crown plane
  extended to C gives t 7.92 in t but 11.69 in X. At C 8.0, inside the photos' 7.3-8.0, the hand test says valley
  (-0.08 / -0.02 mm) where the builder builds an 8.5° ridge; Astra would then have moved measured values to force the
  valley again. Now: never extrapolate t by hand; enter the measured values, build with apply false, read the shape
  and the host's margin (per face its corner farthest across the crease's line and that corner's offset in X from the
  other face's plane, + = a ridge there; facet_inspect prints it on every crease between two facets, receipts and
  variant items in `margins` only for a crease with a solved setting corner or a margin under 1 mm) and declare the
  shape the measured values build; a margin within the readings' error leaves (1) open. "Never redeclare just to match
  the build" now covers only values that were not measured. "A far corner of each face" was ambiguous: the crown's
  other corner R lies 1.18 mm on the logo's side of the A-B line, and read with R the rule contradicted the build on
  every revision (r0002, r0005, r0014); it is now "each face's corner farthest across its line, as the host names
  them".
- **P2, never solve.** Scoped to the crease being decided (its ends and those far corners). A crease-mode facet's
  corners off its crease edge are solved by design and never flagged: giving the top band's Pt any t is refused (its
  own solved value rounded, 5.33, fails `E_FACET_NOT_PLANAR` by -0.0009 mm). A corner no view can read (R and Pt lie
  on B2's top outline, their depth lines along the edge) stays solve and the note says so. Solving a far corner moves
  it (the corrected case's R 4.9 -> 8.68, and crown|tail_face 11.7° -> 41.3°), so the faces it bounds are compared.
  The review's "cannot be satisfied" was refuted: R qualifies as a corner away from the crease, and R solved with the
  top band's rise re-solved builds a 9.4° ridge.
- **P2, the bracket.** "Differing only in that crease's corner t and declaration" forbade the only ridge arm that
  builds: arms with B 6.75 or 7.75 alone are refused `E_RELIEF_STEP` at ∓0.0012 mm until the top band's rise_per_mm is
  re-solved. The arms may now also differ in "only the values the builder requires to keep shared corners at one
  height" (`rise_per_mm: solve` is not built).
- **P2, `E_CREASE_SHAPE`.** The refusal now ends "; solved corners: B"; the instructions give any solved corner that
  sets the crease a measured t first, then declare the shape the measured corners build or change values not
  measured (run 5's $0 ridge test had instead lowered P, the node the photos support). The refusal's own "do not
  redeclare" is named as that scoped rule.
- **P3:** "reading a crease's corners, of both faces, in one photo" (a camera's error is then common to all four: 02's
  fit has a mean contour of 7.2 px, about 0.9 mm of t); rule (2)'s "another part" became "another face or part" (in
  run 5 another facet, the top band, formed that outline).
- **Length:** the cap stays at 14,000 characters and is not raised again. The new rules were paid for by cutting what
  the tools state themselves, each now checked in the tool or parameter descriptions by the test: the edit order and
  reset_layout, refusal indices, the offsets' sign, the outline line's parts, facet_variants' photo limits, panels and
  revisions, rise_per_mm's sign, a fade's ends, the judgement's fields and the result footer. Instructions 13,897
  characters (was 13,952); first request ≈ 45.7k characters / 33.4k tokens by the formula (was 45.2k / 33.1k; the
  tools track's longer descriptions included).
- **Still open:** rule (2) has no tool behind it (T4, which surface forms each outline, is not built; where a crease
  falls in a view along it depends on the model's own t, so the check can confirm a ridge on a ridge build but cannot
  refute a valley head); T5 (refusals for brackets that mix a crease flip with other changes) and T6 (`rise_per_mm:
  solve`) are not built.
- Tests: `test_temple_lab_cli.py` (needed and retired phrases, the cut phrases found in the tool and parameter
  descriptions, the receipt's margins in facet_edit's and facet_variants' descriptions, FINISH_DOC verbatim, the
  cap); `loop.contract_problems` on the real registry of a scratch package (`tt_run6b_pkg`, $0): no problem. No paid
  run has used this prompt yet.

**Amended 2026-10-11 (the final pre-run review; $0, TOOL TESTs on the Prada fixture's chains and on copies).**
- **P2-1, `E_RELIEF_STEP` gave no closing value.** The rules route Astra through it (solving a corner, the
  one-question bracket's "values the builder requires"), and T6 (`rise_per_mm: solve`) is not built. The refusal now
  names it (`lab.relief_rise`): on an `E_RELIEF_STEP` that involves a crease-mode facet (the refused facet, else the
  neighbour whose value it misses, else another neighbour it takes values from) the worst misfit is linear in that
  facet's rise, so one more build at rise + 0.01 gives the root; the printed value must then build the spec without its crease declarations (a contradicted declaration is the next refusal, not this one), and
  the message ends "; rise_per_mm <value> meets it" (a neighbour's: "; F1 rise_per_mm ... meets it"), within the
  400-character refusal, in facet_edit and facet_variants. Seven significant digits do not build a crease plane
  through a neighbour's corner value: the builder's polygon self-check (`build.PLANAR_MM`, 1e-9 mm) needed 8-10 digits
  on the tool-test band (B 5.5 / 6.5 / 7.25: 0.041638353, 0.115845641, 0.1715011066), so the host prints at least 7
  and more while the rounding alone misses. Otherwise (no crease-mode facet, or no root that builds) the worst misfit
  prints with 6 significant digits instead of the core's 3. Each try is one build (~40 ms; 0.11-0.15 s per refusal
  measured).
- **P2-2, rule (c)'s solved corner.** "Solve a corner away from that crease" can push a corner no photo reads far (run
  5's corrected case: R 4.9 -> 8.68). Now: "keep it whole and solve the corner the photos constrain least, named in the
  note; it moves (the receipt's solved shows how far), so compare the faces it bounds". The "and no photo shows a
  fold" clause is gone: the instructions suggest no fold or split (the owner's layout is right, and a split is the
  failure fixed after run 4).
- **P3s.** facet_inspect's margin text says "a t error that size at that corner flips it (when no end of the crease is
  solved)" (a solved end moves with the plane too); a crease flipped against the head is always in the receipt's
  `margins` (first), even with a wide margin and no solved corner; the `E_CREASE_SHAPE` note lists only the refused
  crease's solved ends and far corners (the spec built again without its declarations, which shape nothing, so the
  margins' rule names them), not every solved corner of its two faces; `solved` leaves out the corners solved by design
  (a crease + rise facet's other corners move with its rise as designed); facet_inspect cuts a crease line's margin
  first (at a clause end, "..."); the §4.1 rows above describe the margin, the receipt's `margins`, the front-view
  temple choice of `depths` and facet_restore's `crease_vs_head`; facet_inspect prints rise_per_mm unrounded.
- **The rehearsal** (T5, `tests/proofs/astra_temple_lab.py`): after the context guard one scripted head (HM: a crown
  whose corner B is solved from its plane and a logo that takes B) makes a facet_edit receipt carry `margins` and
  `solved`, checked equal to the host's own summary of the build, so the estimate prices such a receipt.
- **§4.5 on that head.** Its first layout (`t5_wip6c`: the crown's given corners A (3, 8), P (32, 13.5), R (78, 1.5)
  tilt it 7.1° per mm, a W_CONDITIONING line) printed 1,577 characters at the five default photos, over the 1.5k
  budget; the check stays. Two receipt repetitions were cut (`lab.conditioning_line`: every W_CONDITIONING in one line,
  its phrase once; the W_BORDER_RAMP line's sign legend moved to facet_edit's description) and HM's crown was laid out
  wider (A (3, -3), P (30, 14): no conditioning warning). Recorded fact, not fixed here: run 5's applied facet_edit
  texts were 1,470-2,228 characters (7 of 8 over 1.5k; ~700 of 2,228 are 9-12 crease lines), so §4.5 holds for the
  scripted heads only and an estimate priced from the rehearsal's outputs prices facet_edit low.
- Instructions 13,937 characters (cap 14,000, unchanged). Tests: `test_temple_lab.py` (the closing rise in facet_edit
  and facet_variants, each confirmed by a build, with the band's corner moving; a neighbour band's rise; the 6-digit
  misfit; a flip in the margins with no solved corner; the `E_CREASE_SHAPE` note naming B and not R; by-design corners
  out of `solved`; the inspect line cut; the conditioning and ramp lines), `test_temple_lab_cli.py` (needed and
  retired phrases, the tool descriptions). Rehearsal `t5_wip6e` (the final code; `t5_wip6d` the same before the
  neighbour-rise candidates): 38 of 38 checks, $0, the margins head's facet_edit 1,491 characters (the largest).

### 4.5 Output budget

- Numbers are rounded (mm 0.01, deg 0.1); a rise_per_mm prints as the spec holds it (facet_inspect) or with at least
  7 significant digits (an `E_RELIEF_STEP`'s closing value), since 0.001 misses the builder's tolerances.
- The spec is echoed only by facet_inspect.
- facet_edit and facet_variants text is ≤ 1.5k chars; facet_apply-style receipts ≤ 1k.
- Every result ends with `settled $x / cap $y · context n tokens`.
- Tests assert the text lengths on the Prada fixture.

## 5. Proofs ($0) and owner sheets

| ID | What | Pass |
|---|---|---|
| T1 | Builder unit tests: fixtures and refusals, v1→v2 equivalence (§2.3), core roll on preferred-v2 at 0.3/0.6/1.0 mm (closed, Euler 2, planar cells, silhouette unchanged, measured width within 15%), crease check refuses c0003's valley, fade/border rules | all pass |
| T2 | Rig check (tool, not product): preferred render with node C at t and t + 1 mm, at 05 and 01, on facets bounded by C (overlay geometry, eroded 10 px) | median ΔL ≥ 3 L8 (native, beige) and ≥ 10 L8 (gloss_black). Information: preferred's upper-crease step within 2× of the photo's +17–18 L8; black-side facet brightness order Spearman ≥ 0.8; EEVEE vs Cycles (256 spp) per-facet ≤ 2 L8 once |
| T3 | Template on the Prada original | report numbers (§2.4) |
| T5 | Scripted rehearsal (about 2 min: prepare ~30–40 s, run ~90 s): real pinned SDK + MockTransport + real Blender, every tool, refused finish keeps the run going and accepted finish ends it, E_JUDGEMENT, E_BUSY, required tool choice, context guard, network tripwire; since 2026-10-11 also every photo in the first request (6 target + 3 colourway roles), a black photo in a facet_edit compare, facet_variants at a target and a black photo (gloss_black panels, each model's crease lines counted per panel), the B2/B3 compare (photo / overlay / gloss_black) with points_px, and the gloss gain still from black-side | all checks pass (31 on `review_t5`) |
| T5b | Expressivity: the coding agent as tool user reaches preferred-v2's layout and crease shapes from revision 0 through facet_edit only | ≤ 40 ops, no missing op |

Owner sheets:
- **Sheet #1**, as early as possible, before the builder rewrite: original / Astra c0003 / preferred (v1 build) at
  01–06 + black-side, native + gloss_black, stretched, beside the photos. Question: "do these renders show the
  temples fairly?"
- **Sheet #2:** original vs revision 0; preferred-v2 with roll; a depth strip (node C t −2/−1/0/+1 mm) at 05, 02 and
  black-side. Question: "can you see the depth change, and does the preferred look like the photos?"

Then a cost estimate and the owner's OK, then one paid Astra run from the original model, judged on a sheet #1-style
comparison.

## 6. Build order

1. **Archive + docs (§7)**, suite green, commit. **Seeing (§3)** in parallel, on disjoint files: render.py,
   seeing.py, compare.py, pose.py (new), plus their tests.
2. Owner sheet #1 (seeing done). **Builder v2 (§2)** in parallel: g3d/temple/*, temple_bpy.py, the core temple tests.
3. **Lab tools (§4)**: agent/temple_lab/*, worker method for variant renders, loop.py settings, tests, T5, T5b. Sheet
   #2.
4. Adversarial review of the whole diff, fixes, commit. Estimate. Owner OK. Paid run.

Process rules:
- Codex sessions work sequentially.
- Claude sessions with ultracode on may run workflows with disjoint file ownership.
- The coordinator commits with explicit paths.
- Heavy CPU (owner): process pools for renders and variants, within free-commit memory.

## 7. Archive (track F) and doc rewrite

- Local tag `wp6-v1-final` on the pre-archive HEAD (never pushed).
- `git rm`, after an import scan proves nothing kept imports them:
  - `g3d_host/relief_*.py` and `g3d_host/camera_similarity.py`, except modules still imported by agent/temple_lab
    until step 3 (listed and removed then). `camera_similarity.py` was restored from `wp6-v1-final` on 2026-10-11
    and is live again: it transports the colourway cameras (§3.1; `tests/proofs/wp6v2_basis.py`,
    `tests/test_camera_similarity.py`, which checks the restored functions verbatim against the tag);
  - `tests/proofs/wp6_*`;
  - `tests/fixtures/{camera,occlusion}_evidence.py`;
  - the tests that import archived modules.
- Strip archived imports from `tests/test_temple_real.py` and `tests/test_temple_blender.py`.
- Keep `m_temple_lab_build` and its WP2 test.
- `relief_views.rot_about_hinge` is moved to g3d_host/pose.py first (seeing track).
- **Plan docs:** move `PROGRESS.md`, `WP6_HANDOFF.md`, `WP6_DESIGN.md`, `ASTRA_TEMPLE_LAB.md` and
  `WP6_SESSION_PROMPT.md` (with the owner's uncommitted edit applied) to `plan/archive/20261010/`. A new
  `PROGRESS.md` (≤ 120 lines) has the status table, decisions with owner words, and a ledger.
- **Rewrite:**
  - glasses3d/AGENTS.md: every KEEP-STOP-I / frozen-threshold / WP6_HANDOFF line; the workflow rule as in §6;
    "heavy CPU";
  - root AGENTS.md:12-15;
  - PLAN.md §0 rule 6 ("WP6 gates remain unchanged") and the WP6 section (→ this file).
- The general rule stays: never loosen a gate to get a pass. If a gate is wrong, prove it (the true answer fails it)
  and ask the owner.
