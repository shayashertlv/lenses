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
   - a faded facet's crease neighbours must be base or carry the identical fade, else `E_FADE_NEIGHBOUR`;
   - a vertex shared by two facets with different fades is refused;
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
   - faded and ramp edges are sharp only at a dihedral ≥ 6°;
   - strips, caps and kinks follow v1.

   Custom normals are face normals on non-faded facet polygons and auto-smooth elsewhere.
8. **Crease check:**
   - after the build, each declared pair must share at least one edge inside the outline;
   - its length-weighted built shape must equal the declared shape, else `E_CREASE_SHAPE` naming the pair, the built
     shape and the dihedral;
   - pairs below 2° report "flat" and a ridge/valley declaration on them is refused;
   - "base" pairs are judged per edge with length shares.
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
- black-oblique is excluded (its camera is a copy of 02's, never fitted).

Astra's inputs hold the target colourway only (owner choice); variant photos appear on owner sheets.

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

### 4.1 Tools (7; strict JSON; text + ToolOutputImage detail=high; one photo-id scheme `01…06`)

| Tool | Does |
|---|---|
| `facet_inspect()` | Spec summary (10 mm station table, nodes, facets, creases), chart, section strip vs original, receipt lines, checkpoints, budget footer |
| `photo_image(photo, crop?)` | A target photo or crop, with its transform |
| `photo_compare(photos?, crop?, points_px?)` | §3.3 compare of the applied head; returns `compare_id` |
| `facet_edit(parent_revision, reset_layout, edits[], judgement?, note, apply=true, photos?)` | Atomic ordered edits (§4.2) on a copy → build. A refusal returns at once with the edit index and code. With apply: checkpointed apply to both temples → compare of the given or default photos → new `compare_id` |
| `facet_variants(parent_revision, variants[2..6]{label, edits[]}, photos[1..3], judgement?)` | Builds each variant and renders it through `mesh_overrides` into posed copies, so the live scene never changes. Returns one sheet per photo (photo, head, variants; ≤ 1280×900, panels ≤ 640 px, 2 columns; a refused variant is a labelled panel) plus each variant's revision |
| `facet_restore(checkpoint, judgement?)` | Restores a checkpoint |
| `facet_finish(status: matched\|stuck, best_checkpoint, summary, judgement)` | Exports best_checkpoint (validate_spec report included) and ends the run if accepted; otherwise refused with the missing precondition named, and the run continues |

**Judgement** = `{compare_id, versus_compare_id|null, overall: better|same|worse|mixed, photos: [{photo, verdict,
largest_difference ≤ 200 chars, at_d_mm|null, at_y_mm|null}], best_checkpoint}`.
- It is required on the next action tool after any compare and must name the latest `compare_id`, else
  `E_JUDGEMENT`.
- The host logs judgements. It never grades them.

**Finish preconditions** (procedural, never quality grading):
1. ≥ K distinct applied heads compared (K owner-set per run, default 4). Each differs from its predecessor by
   ≥ 0.2 mm max|ΔX|, or by a topology or crease-shape change, computed by the host.
2. ≥ 1 `facet_variants` call with ≥ 3 variants pairwise ≥ 0.3 mm apart.
3. best_checkpoint's geometry fingerprint has a compare on all target photos (from cache when available).
4. `stuck` only: the last 3 judgements cover 3 distinct heads and none says better.

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
  tokens ≈ 0.42·chars + 0.975·⌈W/32⌉⌈H/32⌉ + 112 per image.
- **Isolation:** the starting scene is a byte copy of the original. Revision 0 = template_from_mesh, applied at
  prepare. The preferred spec is never given to Astra.

### 4.4 Prompt (agent/temple_lab/instructions.txt, rewritten)

Must state concretely:
- **(a) Coordinates:** d mm back from the hinge; y mm up (y=0 named); t absolute thickness from the inner face; one
  spec drives both temples; photo 01 shows the other side.
- **(b) Position:** read (d, y) from the near-perpendicular side photos with `points_px`, never from oblique views.
  The grid is valid inside the model outline. A common offset of outline and creases is camera error.
- **(c) Ridge or valley:** judge from the top and rear silhouettes (a convex corner is a ridge, a notch a valley) and
  from the shading order; confirm in the section strip; declare every crease. On `E_CREASE_SHAPE`, fix node t; do not
  redeclare.
- **(d) Thickness:** read the top and rear outer-edge offsets in mm; t_min 1.0; quote the station table.
- **(e) Fades:** a crease that dies out ends in a fade or a small dihedral, never a hard edge extended beyond what the
  photos show.
- **(f) Roll:** highlight band width in px ÷ px/mm ≈ roll.
- **(g) Not creases:** a line is a crease only if it stays at the same (d, y) in two photos; reflections and curvature
  highlights move.
- **(h) Depth:** use `facet_variants` spanning ±1 mm for anything uncertain.
- **(i) Judgements:** name the largest difference per photo with its (d, y), and make the next edit address it.
- **(j) Finish:** the preconditions, verbatim. Continue until the pairs read as the same object or the stuck
  condition holds.

Also:
- no re-view rituals, fixed full-view sweeps, failure pre-framing, repo history or "WP6" jargon;
- every tool name appears in the prompt;
- `contract_problems` also scans parameter-schema descriptions for forbidden phrases.

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
| T5 | Scripted rehearsal: real pinned SDK + MockTransport + real Blender, every tool, refused finish keeps the run going and accepted finish ends it, E_JUDGEMENT, E_BUSY, required tool choice, context guard, network tripwire | all checks pass |
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
    until step 3 (listed and removed then);
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
