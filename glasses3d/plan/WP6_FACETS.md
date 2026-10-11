# WP6 v2: Astra handles facets (design, 2026-10-10, revised after 4-lens critique)

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
| `facets` | `{label: {poly: [nodes], plane: "nodes" \| {crease: [n1,n2], rise_per_mm}, fade: null \| {d0, d1}}}` |
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
| `facet_inspect()` | Spec summary (10 mm station table, nodes, facets, creases), chart, section strip vs original, receipt lines, checkpoints, budget footer |
| `photo_image(photo, crop?)` | Any photo of the job (target, another colour with or without a camera) or a crop, with its transform |
| `photo_compare(photos?, crop?, points_px?)` | §3.3 compare of the applied head at photos with a camera (a photo of another colour: photo / overlay / gloss_black rows); offsets per temple named charted / mirrored; returns `compare_id` |
| `facet_edit(parent_revision, reset_layout, edits[], judgement?, note, apply=true, photos?)` | Atomic ordered edits (§4.2) on a copy → build. A refusal returns at once with the edit index and code. With apply: checkpointed apply to both temples → compare of the given or default photos → new `compare_id` |
| `facet_variants(parent_revision, variants[2..6]{label, edits[]}, photos[1..4], judgement?)` | Builds each variant and renders it through `mesh_overrides` into posed copies, so the live scene never changes. Photos (amended 2026-10-11, owner: "yes, build 1-3", item 3): 1..4 with a camera, each in an earlier compare, at most 3 of them target photos (a 4th is a photo of another colour; a job without one takes 3). A target photo's panels are the native render; a photo of another colour's are the gloss_black render with its compare's gain and stretch. Every render panel draws the photo's outline and that model's own crease lines (dihedral ≥ 6°, ridge orange, valley blue, depth-tested as the compare overlay; the sheet title says "creases from 6°"), so sheets also compare layouts. Returns one sheet per photo (photo, head, variants; ≤ 1280×900, 2 or 3 columns by the crop's aspect; a refused variant is a labelled panel) plus each variant's revision. Labels are distinct ignoring case, not 'head' and not ending in '_' (they are the render file names). The worker's render deadline is the 90 s table value minus a 15 s host margin (`loop.FACET_VARIANTS_HOST_MARGIN_S`) |
| `facet_restore(checkpoint, judgement?)` | Restores a checkpoint |
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
- **(b) Position** (amended 2026-10-11): measure (d, y) with `points_px` in the views square-on to the outer face;
  confirm in other views, never measure there (a reading goes through the current model's depth). The grid is valid
  inside the model outline. A common offset of outline and creases is camera error.
- **(b2) Lay out, then confirm** (added 2026-10-11; amended the same day, build review P1): lay the faces out in the
  square-on views with a first depth; that layout is provisional. Before refining depth, check it in a calibrated
  view that sees the outer face at a moderate angle: keep a crease only where that view also shows it, remove one it
  contradicts (no line although it clearly shows neighbouring creases of similar angle), add one only where both views
  show it. Every facet_edit that changes the layout compares at a square-on view, such a view and a glossy photo with
  a camera. When the views disagree on where a crease runs, a depth that puts it on the photo's line in both means
  depth, none means layout. A number (an offset or outline station) changes the layout only when both views show the
  new crease or its absence.
- **(c) Ridge or valley** (amended 2026-10-11): of a crease the photos show, from the silhouettes of views that see it
  edge-on (a convex kink is a ridge, a notch a valley); the section strip shows what the model built; declare every
  crease. Where one face's edge runs straight through a corner, the creases there cannot all be ridges (nor all
  valleys): read from the photos which one has the other shape (§2.2 step 8). On `E_CREASE_SHAPE`, fix node t (or the
  fade, as the refusal says), or, when the refusal says the creases at a corner cannot all have one shape, correct the
  declaration the photos contradict; never redeclare just to match the build, never split a face to get a refused
  declaration built. ("The shading order" is no longer a cue; §3.2: no rig reproduces the photos' order.)
- **(d) Thickness** (amended 2026-10-11, build review P2): read the outer-edge offsets of the photos that print them
  (top and rear views, named in the brief) in mm; t_min 1.0; quote the station table; an offset common to every
  station is camera error. Every offset, like the outline line of a target top view (information only since
  `2ca5be9`, owner 2026-10-11), is one view's silhouette: weigh it with the other views, never change the layout for
  it alone.
- **(d2) Top and bottom faces** (added 2026-10-11, run 3; amended twice the same day): the side views see a band along
  the top or bottom edge almost edge-on, so they show only where it meets the outer face; place and confirm the
  band's own creases, and judge its tilt, in views that see it at a moderate angle (the brief names the views that see
  faces pointing up or down) and in the front/rear silhouettes; a face narrow from the side but broad from above
  points mostly up.
- **(e) Fades:** a crease that dies out ends in a fade or a small dihedral, never a hard edge extended beyond what the
  photos show.
- **(f) Roll:** highlight band width in px ÷ px/mm ≈ roll, a first guess only (it depends on the light).
- **(g) Creases** (amended 2026-10-11): a line is a crease only where photos from two different directions show it at
  the same place; the second must see that face at a moderate angle (an edge-on view's lack of a line is no
  evidence); with no such view, a line in two photos under different light is the best evidence, named in the note.
  Never merge two faces a visible crease separates, never split a face no photo shows divided; a silhouette corner
  can be a crease end, a bend in the edge or the silhouette passing between faces.
- **(h) Depth:** `facet_variants` brackets wide first, then ±1 mm; its panels draw each model's crease lines from
  6°, so it also compares layouts (a crease under 6° draws no line).
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

### 4.5 Output budget

- Numbers are rounded (mm 0.01, rise 0.001, deg 0.1).
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
