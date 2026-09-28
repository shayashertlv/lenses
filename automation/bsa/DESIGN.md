# BSA (Best-Source Assembly) — build contract for M0 + M1

Goal: product photos (front, back, left, right, angled) -> AR-ready glasses GLB. Each part comes
from the source that measures it best, parts are assembled by construction, and every output is
scored against the photos. M0 = harness + ground truth. M1 = the whole chain S0-S10 end to end on
the 5 cached products with ZERO paid calls.

## Hard rules

- No paid or network API calls (Tripo, Meshy, fal, Gemini, OpenAI). Use only cached assets.
- Do not modify `automation/reconstruction/`, `automation/qa/`, `ar/src/` or anything outside the files
  your task owns. Import from `reconstruction/` freely. `bsa/core.py` is owned by the lead: if you
  need a change there, put the helper in your own module and say so in your report.
- Held-out view `angled` is NEVER used to fit, choose or tune anything except a camera (see S3).
- Every mesh is placed in the MODEL frame (mm) and projected with the product's single `NormFrame`
  (`core.project_mm`). Never renormalize a mesh per candidate.
- Deterministic: same inputs -> byte-identical arrays/decisions. No randomness without a fixed seed.
- Honest reporting: numbers from real runs on all 5 products, failures included; look at your sheets.
- Probe code from the 2026-09-24 design session (local scratchpad, not kept): `eyewear_compiler/`, `wild_card/`,
  `generator_plus_fit/`, `key_colour/`, `visual_hull/` and the architect plan `pipeline-plan.md`. Promote ideas,
  not bugs: known bug `build3d.py:202,285`
  (`ss = Hs/Hfront` side scale) must not survive — side measurements go through S3 cameras.

## Frames and units

- MODEL frame: millimetres; +X = viewer's right in the front photo; +Y up; +Z toward the front
  camera (lenses face +Z). It is the canonical generator's frame (S1). The front piece width is
  `Product.front_width_mm` (140 mm unless product dimensions are supplied).
- Canonical generator = raw Tripo vertices mapped (x, y, z) -> (-z, y, x) (yaw -90), verified so the
  lens plate faces +Z (flip (x, z) -> (-x, -z) if not), then uniformly scaled so the FRONT PIECE
  width (geometry within 20 mm of the front-most depth) equals `front_width_mm`.
- `NormFrame` (in `core.py`) = bbox centre and max extent of the canonical generator in mm. All
  cameras are `reconstruction.camera.Camera` values in this normalized frame; they map to NATIVE
  photo pixels (u right, v down). `core.px_per_mm(camera, frame)`.
- Export frame (S9): metres, +Z front, +Y up, origin at the bridge underside on the symmetry axis
  (lowest point of the frame at x = 0 between the lenses), identity node transforms.

## Stage artifacts

Each stage writes `data/bsa/runs/<run>/<product>/<stage>/` via `core.stage_dir(run, product, stage)`:
`result.json` (+ `arrays.npz`, images, sheets). The M1 run name is `m1`. Downstream stages READ the
artifacts; they never recompute an upstream stage. Every stage module exposes
`run(product: str, run: str = "m1", force: bool = False) -> dict` returning the result dict, skipping
work when `result.json` exists unless `force`.

### S0 `s0_intake` — `bsa/intake.py`
Per view (all 5): matte and lens proposal at native resolution.
- arrays: `fg_<view>` bool HxW (glasses foreground), `lens_<view>` bool HxW (offline detector
  `contrast_crop` variant for front/back/angled; all-False for left/right).
- result: `views.<view>`: `shape`, `backdrop_rgb`, `fg_pixels`, `bbox_xyxy`, `flags` (e.g. `border_contact`,
  `floor_reflection_cut`, `low_resolution` (< 600 px glasses width), `mirror_iou_low`), `mirror_iou`
  (front/back only), `photo_sha256`.
- Method: quadratic Lab border backdrop model; foreground = contrast with low-chroma shadow rule
  (compiler `common.load_view`), 3 px hysteresis growth, floor-reflection flip test, keep components
  >= 0.2 % of the largest; union lens proposal into fg for front/back/angled, minus its backdrop OPENINGS
  (`lens_openings`: enclosed backdrop the proposal swallowed next to a tinted lens, millimetres from the front piece's
  width = the matte over the lens rows). An enclosed opening is a vent only when it opens onto the FRAME
  (`opening_onto_frame`): it continues beyond the proposal by >= 0.6 mm, or >= 30 % of its surroundings (the matte
  within 0.6 mm) is frame-like; a highlight on a tinted lens that touches the rim is surrounded by lens colour
  (12-18 %; oakley's brow vents 44-46 %). Shared with S2's vent step. Detector: `reconstruction.
  photo_apertures.OfflineLensApertureEngine(DETECTOR_WEIGHTS, runtime_dir=data/bsa/.detector_runtime)`.

### S1 `s1_generator` — `bsa/generator.py` (+ `bsa/raster.py`)
- arrays: `V` float32 (N,3) mm canonical, `F` int32 (M,3), `UV` float32 (N,2) or absent, `Vd`/`Fd`
  decimated (~24k faces) for camera fitting.
- files: `basecolor.jpg` (native), `metallic_roughness.png`, `normal.png` when present.
- result: `frame` (NormFrame dict), `raw_to_model` (4x4 list), `scale_mm_per_raw`, `front_z_mm`
  (front-most depth of the front piece), `front_width_mm`, `orientation_checks`, counts, `source_sha256`.
- `bsa/raster.py` (shared renderer, owned by S1 task): open3d `RaycastingScene` rendering of a mesh
  (mm + NormFrame) from a Camera at a given image shape: `render(mesh_V_mm, F, camera, frame, shape,
  stride=1) -> {mask, depth, face_id}`; supports a sub-sampled grid for speed and a region of interest.
  Orthographic when `camera.perspective == 0`, exact perspective otherwise, consistent with
  `reconstruction.camera.project`.

### S2 `s2_front` — `bsa/front.py`
The 2D front partition, in FRONT-photo native pixels (or the mirrored back photo when it is the larger
and the front is < 600 px wide; then all px refer to that source, recorded in `outline_source`).
- arrays: `frame_mask` bool (symmetrised matte minus lens polygons), `fg_sym` bool, `lens_label` int8
  (0 none, i = lens i), per lens i (1-based): `lens{i}_poly` float64 (K,2) closed outline in px (no
  repeated end point), `lens{i}_type` int8 (K,) 0 = frame-bounded, 1 = free edge, 2 = rimless edge,
  `lens{i}_rimw_px` float (K,) rim width outward.
- result: `outline_source` ('front' | 'back_mirrored'), `axis_x_px`, `width_px`, `layout` ('pair' |
  'single'), `rim_class` ('full' | 'half' | 'rimless' | 'mixed'), `lenses` [{`side` 'R'|'L'|'C'
  (viewer's right = +X), `area_px`, `centroid_px`, `type_fractions`}], `refinement` {median bevel
  offset mm, low-contrast share}, `flags`.
- Method: detector lens proposals -> point-typed edge refinement (generator_plus_fit `refine.py`:
  half-contrast crossing along normals, bevel offset for low-contrast points; free edges on the matte
  0.5 iso-contour; rimless edges ridge snap) -> SDF-averaged mirror symmetrisation (sigma 0.3 % W) ->
  arc-length Fourier K = 24 -> frame polygon = symmetrised matte minus lenses (no row band) -> rim field
  w(theta) -> layout and rim class. Millimetre conversions in S2 may use the provisional scale
  `front_width_mm / width_px`. The mirror axis is snapped to the half-pixel lattice (`mirror_field` is exact
  only when 2 x0 is an integer). (A rimless edge carried smoothly under its drill mounts was tried on
  2026-09-24 and reverted: the new outline moved hardware pixels into the S4 frame samples and broke miu's S4 lens
  surface, S6 lens sheet and AR load.)

### S3 `s3_cameras` — `bsa/cameras.py`
One frozen camera per photo, fitted to the canonical generator (decimated) with the S0 matte.
- result: `frame`, `cameras.<view>`: `camera` (dict), `iou`, `contour_mean_px`, `contour_p95_px`,
  `px_per_mm`, `starts`, `flags`; `consistency` (front-camera px/mm vs the S2 width, etc.).
- Method: multi-start yaw per view prior (front 0, back 180, left +90, right -90 — CHECK the sign
  convention against the renders — angled +/-20..65), pitch {-20, 0, 20, 40}, cross-seeding, then Powell
  on the silhouette IoU + boundary loss (like `reconstruction.camera.fit_camera` but using `bsa.raster`).
  The angled camera is fitted like the others (a camera, never a shape). Frozen afterwards: later
  stages may only apply a bounded refine (scale +/-2 %, rotation +/-3 deg, translation +/-2 % width).
  Detect: front IoU < 0.93 or another view < 0.85 -> flag and down-weight that view.

### S4 `s4_depth` — `bsa/depth.py`
- arrays: `grid_x`, `grid_y` (mm), `z_front` (ny,nx) mm smoothed front surface of the generator's front
  piece, `thickness` (ny,nx) mm, `valid` bool, per lens i: `lens{i}_surface` coefficients.
- result: `wrap` ('planar' | 'cylinder' + radius), fit residuals, lens surface models and residuals,
  convexity clamp use, flags.
- Library functions: `load_depth(product, run) -> DepthField` with `.z(x, y)`, `.thickness(x, y)`,
  `.lens_z(i, x, y)`; `lift_px(px, camera, frame, surface_fn) -> (K,3) mm` (ray from the front camera
  through each pixel intersected with the surface; exact for the Camera model).
- Method: front raycast of the generator at ~600 samples per width; front-most depth-continuous
  component; robust soft-L1 tensor B-spline (~8 mm knots); extrapolate beyond generator coverage with
  the low-order fit; cylinder when fitted wrap radius < 150 mm. Thickness = entry/exit distance clamped
  (acetate 1.5-9 mm, metal 0.8-2.5 mm by stroke width). Lens surface = robust quartic to plate hits
  inside the lens outline eroded 1.5 mm, convexity clamp R >= 50 mm, base-curve sphere if < 2,000 hits.

### S5 `s5_temples` — `bsa/temples.py` (+ `bsa/donor.py`)
Donor temples from the generator: a split plane behind each endpiece (the front-most allowed z = front piece
back face - 1 mm, moved up to 10 mm back to the simplest cross-section), keep the two lateral components beyond
it (with their generator UVs), refine each rigidly on the side silhouettes through the frozen S3 cameras, reject
crossings > 2, length mismatch > 8 %, gap > 3 mm, folded temples.
Front-piece DONORS (`donor.extract`, appended to `temple_<s>` as further closed components, face range
`donor_<s>_faces`): the generator's 3D parts the S6 plate does not model - endpiece/hinge blocks, pads and pad
arms, a metal bridge's back, all rimless hardware, the centre stem of a shield - cut cleanly on the S4 plate back
(0.3 mm into the plate), inside the source photo's footprint and every other fit view's silhouette. In the lens
area away from the plate the rule is "behind the generator's lens plate" (structure seen through the lens), kept
when anchored outside the lens area. A donor part that touches neither the plate, nor its side's arm, nor an
anchored part (within 0.5 mm) is dropped: it would float in the delivered model; S6 re-tests it on the delivered
geometry (below). HARDWARE = 8-connected non-rim frame components of at least 1 mm2 (a 1-2 px speck next to a lens
took a 3 mm disc of generator geometry whole). A projection crossing a camera's near plane is recorded and flagged
`donor_projection_failed` (REVIEW in S10). See the module docstring.
- arrays: `temple_R_V`, `temple_R_F`, `temple_R_UV`, `temple_L_*`, `donor_R_faces`, `donor_L_faces`, `hinge_R`,
  `hinge_L` (mm).
- result: `cut_z_mm`, per side {faces, length_mm, side_photo_length_mm, gap_mm, accepted, reason}, `donor` stats.
- A rejected ARM does not take its donors with it: S9 (and the S7/S8 calibration GLBs) export the donor face range
  alone (`export.temple_arrays`).

### S6 `s6_assembly` — `bsa/assemble.py`
Construct the front and lenses from S2 polygons lifted onto S4.
- arrays: `frame_V` mm, `frame_F`, `frame_region` int8 per face (0 front cap, 1 back cap, 2 wall),
  `frame_uv_px` float (per vertex, front-photo px of the front-cap vertices; -1 elsewhere),
  per lens i: `lens{i}_V`, `lens{i}_F`, `lens{i}_uv` (u across, v top->bottom in 0..1).
- result: triangles, watertightness per part, seam check (lens area - hole area == tuck band only,
  zero gap pixels in the front render), bridge underside point (mm), flags.
- Method (full detail in the `bsa/assemble.py` docstring): constrained Delaunay with 0.5 mm spacing on lens
  rings, <= 3 mm elsewhere; front cap lifted onto the S4 front surface by the exact source ray; the plate is a
  PRISM along the S4 depth axis (back cap = S4 thickness deeper); at lens-hole boundaries the wall runs along the
  EXACT source ray (hole = the S2 polygon in the source photo, also on a wrapped shield; beyond 70 deg - the graze
  limit past which no plate is built - the wall is shortened, never bent into the hole). Outer silhouette rule:
  where a pitched camera sees the back edge, the front edge moves inward so the back edge lands on the outline
  (normal and shift smoothed along the outline in mm).
  Visual-hull carving by the other fit views' mattes; the carve amounts are smoothed in mm (area-weighted
  Gaussian, sigma = 1.5 mm = the Gaussian whose half-power wavelength is one S4 knot, 8 mm, after a max over the
  same radius): per-vertex decisions made pleated rims and lens-hole notches. The band that holds a lens is never
  carved thinner than lens + 2 x 0.2 mm + 0.1 mm. Hardware parts are thin hidden cores (the visible hardware is the
  S5 donor). Lenses: outline grown by max(0.6 mm, 35 % rim width) on frame-bounded segments; free/rimless edges
  tuck 0.3 mm under photo material, S2 hardware or S5 geometry within 1.5 mm, not under the lens's own matte halo
  and never
  across a backdrop pixel (a vent stays a vent), and within 0.5 mm (along the ring) of a frame-bounded or tucking
  vertex always (corners); placed at the mid-depth of the rim AS BUILT (after the silhouette
  rule and the carve), 1.4-2 mm thick, contained in that rim; frame hole = unbuffered lens polygon.
  Vent walls (S2 `carve_class` 1) run along the exact source ray like lens holes (a vent stays see-through in the
  source view). The plate back meets S5 donor geometry within 1.5 mm directly behind it, 0.3 mm into it
  (`meet_donors`). Frame pieces that touch only a lens and are a temple crossing it (within 2 mm of the S5 arm's
  projection) or the lens's own edge band (a < 25 mm2 fragment hugging the outline) are not built
  (`temple_crossings`).
- Final donor anchoring (`donor_anchoring`): S5's donor components re-tested on the delivered geometry with the S10
  integrity rule (point-to-triangle, 0.5 mm, lenses and arms included): within 1.0 mm -> moved 0.3 mm into contact,
  farther -> dropped; stored as `donor_offset_<s>` / `donor_keep_<s>` and applied by `export.temple_arrays` (S7, S8,
  S9).

### S7 `s7_texture` — `bsa/texture.py`
- Front cap: front photo, sampled only inside the frame region eroded 2 px, padded outward (no backdrop
  or lens colour), highlight clamp. Back cap: registered mirrored back photo. Walls: closest-point
  re-bake from the generator texture (exclude source faces inside a 1.5 mm-dilated projection of the
  lens polygons within the plate depth band), per-channel gain+offset fitted between generator texture
  and front photo on the front cap. Temples: donor UVs + generator texture.
- Material factors (`fit_frame_material`): (metallic, roughness, base-colour gain) fitted in the actual AR runtime
  against the fit-view photos (slab dE00 of the opaque-part colour distribution); every fit view with >= 150
  comparable pixels is used (the front is not required; flag `ar_fit_front_view_unusable`); the gain is refined PER
  CHANNEL at the chosen (metallic, roughness) (a scalar gain desaturated tortoise after tone mapping) and verified by
  an actual render. A harness failure (`HarnessError`) flags `ar_fit_failed` (a REVIEW rule in S10) and keeps the
  old ORM rule; any other exception fails the stage.
- 2026-09-25, tried and rejected (run m2): metallic/roughness taken from the generator's own ORM map per material
  class (roughness 0.25-0.36 on the acetates, miu gold metallic 0.91 / roughness 0.40) with only the gain fitted.
  Under the runtime's room lighting BSA's flat, camera-facing front caps then show a silver-white glare (share of
  light neutral frame pixels in the front render: vb 0.5 -> 21.4 %, invu 0 -> 33.5 %, rayban 0.4 -> 25.3 %, oakley
  0 -> 10.7 %); a roughness ladder on the same gain shows the glare persists at 0.45 and 0.6 and is gone at 0.8, and
  miu's dielectric gain (fitted on 172 tortoise pixels) turned its nose pads salmon. The m1 fit above is restored
  byte-identical (texture.py sha256 556932bb...). Gloss needs rounded/curved front geometry or a lighting change
  (the M3 studio lighting), not a material factor.
- Low-resolution front (`front_low_resolution`) with the mirrored back photo as outline source: the front cap takes
  the back photo's detail (sampled where S6 placed each cap point in the source photo), the front photo's colour
  (low-pass at one front pixel, multiplicative detail) and a per-channel gain that gives the front-view render the
  front photo's mean frame colour. Frame pixels that are part lens colour next to the lens are neither sampled nor
  evaluated (`lens_mix_mask`); evaluation renders integrate each photo pixel over its footprint (>= 4 samples/mm).
- Fit views only: S7 never renders, measures or shows the held-out angled view (S10 evaluates it).
- outputs: images + per-part material plan in `result.json` (`materials`: name -> {texture file, uv set,
  factors}), gain, per-view frame colour differences, flags.

### S8 `s8_lens` — `bsa/lens.py`
- Photometry on the lens polygon eroded 2 mm minus highlights minus pixels where a temple/bridge is seen
  through the lens (render occluder mask from S3 cameras + generator), linear-light ratio to the S0
  backdrop. Back view ~ transmission; front = coat + transmission.
- result: `class` ('clear' | 'tint' | 'gradient' | 'mirror'), `tint_linear_rgb`, `gradient`
  {top_linear_rgb, bottom_linear_rgb, profile} or null, `mirror` {...} or null, `lens_appearance` (the runtime's
  canonical LensAppearance v1 descriptor, which S9 writes as `LENSES_lens_appearance`), `gltf_material` (only the flat
  KHR_materials_transmission fallback for other viewers), `edge_ring` (clear lenses, with its own descriptor),
  `mirror_calibration`, `evidence`, flags.
- Canonical optics: optical density over the lens-local height v (bottom 0 -> top 1) from the transmission profile;
  an uncoated lens reflects R(0) = 0.04 with Schlick. A mirror coat: R per incidence angle measured on the FRONT
  photo (R = front - kappa T(v), binned by the incidence angle on the constructed S6 lens through the S3 front
  camera: a wrapped shield is seen from 0 to ~55 deg in its own front photo), then made to match the photo in the
  actual AR runtime (`calibrate_canonical`, front view, the photo's backdrop colour): candidates = a scale grid on the
  measured coat up to R 0.95, a per-channel scale, and the coat FITTED per angle knot and channel (`fit_coat`: two
  calibration renders give each lens pixel's environment; bounded least squares on the Lab colour per 5-degree angle
  bin and per lens-height band, smoothness between knots, 0 <= R <= min(0.95, 1 - max T); stage 1 = the measured
  table's hue with one colour shift and a strength per knot, stage 2 = per-knot RGB anchored to stage 1 so no knot is
  re-tinted against the calibration room): every candidate rendered,
  the one with the lowest band dE00 chosen (`R = front - kappa T` zeroes a channel wherever the transmission is high:
  oakley rendered teal for violet). M1 interim; the angle table cannot represent position dependence (studio_v1
  fitting is M3).
- Lens-colour check (every class; `lens_colour_check`): the delivered lens rendered in the actual AR runtime (front
  view, photo backdrop colour) vs the front photo, dE00 of the mean colours per 8 lens-height bands, pixel weighted
  (`band_de00`): `rendered_dE00`, read by S10 (`lens_colour_mismatch` above 8). A harness failure flags
  `lens_colour_check_failed` (REVIEW). A harness failure of the calibration flags
  `mirror_calibration_failed` (REVIEW in S10); any other exception fails the stage. `evidence.s3_cameras_sha256`
  hashes the S3 cameras payload (not result.json, whose timings change on an identical recomputation).

### S9 `s9_export` — `bsa/export.py` (+ `bsa/contract.py`, `bsa/archeck.py`)
- `export.write_glb(parts, materials, path) -> dict`: nodes `frame`, `temple_R`, `temple_L`, `lens_R`/
  `lens_L` (pair) or `lens_C` (single); metres; bridge-underside origin; identity transforms; JPEG
  textures <= 2048; lens material transmission > 0 or a canonical descriptor; frame AND temples opaque or physically
  translucent (crystal or translucent acetate: `KHR_materials_transmission` + `ior` + `volume`, forced single-sided,
  flagged `<node>_material_translucent`; any lens descriptor on a frame or temple material is refused), hardware
  (metal) opaque. A lens is its FRONT SHEET; with a `lens_appearance` descriptor it is the runtime's canonical `front_sheet_v1` optics
  (`canonical_sheet`): `LENSES_lens_appearance` on the material, MESH extras `partRole: lens`,
  `lensSurfaceProfile: front_sheet_v1` (GLTFLoader copies mesh extras onto every primitive), TEXCOORD_0.y = the
  lens-local height of the stored float32 positions (bottom exactly 0, top exactly 1), every normal toward +Z, every
  triangle +Z-wound; mixing canonical and legacy optics raises (the runtime rejects it). No run name in the asset
  (identical exports of different runs are byte-identical).
- `contract.check(path) -> dict`: parses the GLB; verifies units (width 0.10-0.20 m), +Z front (lens
  centroid ahead of temple mass), origin, identity transforms, lens detection rule (transmission > 0 or the
  canonical descriptor), triangle count <= 100k, bytes <= 8 MB, per-part watertightness, no NaN.
- `m1_criterion_1`: contract ok AND runtime_compatible AND optical meshes detected >= the exported lens nodes.
- `archeck.run(glb_paths: dict[name, path], out_dir) -> dict`: loads each GLB in the actual AR renderer
  through `python -m qa.provider_comparison --manifest ... --output ... --ar-check` (from `automation/`),
  returns per-model status (`runtime_compatible` / rejected + error), optical meshes detected, render
  PNG paths. It must not change `qa/` or `ar/`.

### S10 `s10_gate` — `bsa/gate.py`
Score any GLB/model-frame mesh against the photos with the frozen S3 cameras (bounded refine only).
- Candidates: the BSA model, the previous route's `candidate.glb` (aligned into the model frame by a
  similarity fit to the generator), and a tilted-card control (the S2 frame+lens polygons on a tilted
  plane, 3 mm thick, no temples).
- Metrics per view: front piece (temples masked by the fitted temple projection) contour mean/p95 in mm
  and % width, IoU secondary; side views temple contour distance; lens edge vs S2 outline (mm); lens edge
  vs ground truth (front, frame-bounded segments, mm); seam gap pixels; head-phantom share (material with
  |x| < 0.3 W more than 0.15 W behind the front); contract.
- result: per candidate per view metrics, M1 criteria verdicts, decision READY / RETRY / REVIEW.
- Decision (`decide`): REVIEW when a `REVIEW_RULES` flag is raised (S2 outline doubts, lens views inconsistent, the
  gate's own validation, previous-candidate alignment, the S7/S8 harness fallbacks `ar_fit_failed` /
  `mirror_calibration_failed` read from their results, S5's `donor_projection_failed`, the lens-colour check
  (`lens_colour_mismatch`: S8's rendered lens more than 8 dE00 from the front photo, `lens_colour_check_failed`,
  `lens_colour_unchecked`), `no_lens_edge_criterion` when c2 is not applicable, and the
  model-only integrity checks `floating_part` / `rough_silhouette`); else RETRY when an applicable criterion failed
  or was not evaluated; else READY ("metrics pass", not a shopper verdict: the owner's blind rating is separate).
- c2 applicability comes from the GROUND TRUTH alone (`gt_frame_truth`: no truth file, or no non-occluded
  frame-bounded sample on the criterion photo); a measurement failure stays applicable and unevaluated (RETRY).
- c3 carries `margin_pct_w`, `temple_sensitivity_pct_w` (the temples_x085 fault's effect on the angled score) and
  `clean` (margin > sensitivity): a pass inside the gate's temple sensitivity is not a clean front-shape verdict.
- Integrity (model only, no photo, never the held-out view): floating components (every vertex > 0.5 mm from the
  surface of every other component, >= 1 mm2; lenses never flagged) and front-piece silhouette roughness in synthetic yaw +/-35 / roll 25
  views relative to the generator's (length / 1 mm-smoothed length; REVIEW above 1.5x the generator's excess; a
  weak signal: it separates the previous candidates (~1.0) from BSA (1.2-1.5) but does not see interior pleats).

### S11 `s11_look` — `bsa/look.py` (after the gate; added 2026-09-25)
Owner's direction: code builds and measures the shape; an editor (Astra, `gpt-6-astra`) judges and edits the LOOK.
S11 takes the S9 export as it is and may change material factors and the canonical lens descriptor, nothing else.
- Place in the chain: NOT in `core.STAGES` (core.py is in every stage's code closure; adding it there would mark every
  recorded stage of every run `code_changed`). The orchestrator's chain is `pipeline.ALL_STAGES` = STAGES +
  `s11_look`. Nothing reads S11: no DEPS entry names it and no module of an S0-S10 closure mentions it
  (`test_s10_never_reads_s11`). Its `final_decision` is S10's, copied: the look never upgrades or re-gates a decision,
  and it is not an M1 criterion. DEPS = S0 (photo boxes), S8 (lens class, S8's lens-colour check), S9 (the GLB), S10
  (decision, reasons, flags); it reads no camera.
- Inputs are frozen per session (`prepare_inputs`, sha-pinned; a changed S9 GLB, S10, S8 or S0 result refuses to
  reopen, `--fresh` starts anew): `model.s9.glb`, the S10 result, the S8 summary, the S0 boxes and the FIT-view
  photos only. The editor's context carries a FIT-ONLY view of S10 (`editor_s10`): the reasons and flags derived
  from the held-out view are removed and the decision is recomputed from the remaining criteria by the gate's own
  rule (withheld when that rule cannot reproduce the stored decision), so identical fit evidence with a different
  held-out outcome gives byte-identical editor context; result.json keeps the full record (2026-09-27).
- What the editor may change (strict forced function `edit_candidate`, `build_tools_schema`; <= 6 operations a turn):
  - `frame_material` (one frame/temple material or `all_frame`): `color_ratio_rgb` 0.5-2.0 MULTIPLIES the current
    baseColorFactor (S7's AR gain, not an albedo), clipped to [0.001, 1] and the clip recorded; absolute
    `roughness` 0.05-1 and `metallic` 0-1 (they multiply a metallicRoughness texture where one exists; the context
    gives that texture's area-weighted median G/B over the material's surface, its value groups with their surface
    shares - miu's temples: 63% metal at roughness 1.0, 36% non-metal at 0.3 - and the effective value,
    `mr_texture_medians`). At
    least one factor per operation; a plan that changes nothing (a ratio of 1, a restated factor) is rejected, not
    rendered (`normalize_look`).
  - `lens_look` (all lenses or one; a single-lens edit clones its material): transmission at the lens top and bottom
    0.005-1 per channel, head-on reflectance 0-0.95 (S8's cap), reflectance at 70 deg, roughness 0.02-0.5. Compiled
    against the lens's current descriptor (`compile_lens`); restating the current values is a no-op. Measured
    shapes are kept and NEVER amplified (the 2026-09-25 review: a +0.05 blue grazing edit on INVU's non-monotone
    table multiplied its 20-55 deg knots by ~18, a mirror in the front view; an OAKLEY "gentle gradient" gave an
    interior red transmission of 0.51 for a requested 0.22-0.30):
    - density (`_profile`): new ends on a MONOTONE measured channel rescale it affinely (the interior stays between
      the ends); any other channel keeps its measured deviations from the line between its ends and gets the new
      ends by an additive ramp in v. A head-on reflectance change with restated transmission shifts every density
      knot by ln(1 - R0n) - ln(1 - R0o): what is seen through the lens is kept exactly.
    - angle table: the context shows `grazing_reflectance_rgb` as the current 70-deg value (never a null beside
      `reflectance_at_70deg_rgb`); restating it or null keeps the current angular shape; a new head-on value moves
      the table by `_warp` (per channel the monotone map [0, R0o] -> [0, R0n], [R0o, 1] -> [R0n, 1]: dips stay dips,
      inside [0, 1]); a different 70-deg value adds (g - warp(r70o)) weighted by the Schlick ratio s(a)/s(70) (to 70
      deg; fading to 0 at 90), so no knot moves by more than the grazing change, 0 deg stays the head-on value and 70
      deg is exactly g (a 70-deg knot inserted when missing). A Schlick lens gets a 6-knot host table. A knot the
      offset pushes out of [0, 1] is clipped and reported (`angular_clipped`).
    Every knot-count change (density or angular) is a `knots_changed` note of the turn, and the context shows the
    knot counts now vs S9 (`angular`, `density_knots`). The lens sheet's caption names a measured angle table where
    one sets the colour (its reflectance at that angle vs head-on), not a parameter chosen by angle alone.
    The flat fallback baseColorFactor of an edited lens = its mean transmission (export.py's rule). The clear-lens
    edge ring is never edited.
  - Energy (`ENERGY_RULE`, stated in the prompt, the schema descriptions and `limits.lens_energy`): per channel,
    transmission (top and bottom) + head-on reflectance <= 1 (the stated transmission includes the reflection loss).
    A REQUESTED end above 1 - reflectance is capped there; a cap (or a baseColorFactor clip) makes the turn's own
    event `applied_with_adjustment` with requested and applied values, and the next context leads with it
    (`last_turn`, right after `current_revision`). An interior knot of a kept profile that is lighter inside than
    at its ends and would exceed the limit is capped and reported as `interior_clipped` (the requested ends were
    applied exactly; `last_turn.shape_clipped`), never as an energy cap of the request. Until 2026-09-25 the cap
    was silent: INVU's dry run lost a turn to it.
  - `restore` an earlier revision (exact bytes) and `finish` {improved | no_change_needed | best_effort, note,
    deliver_revision}, each alone; `deliver_revision` (null = current; must be a committed revision) reverts and
    finishes in one step (the dry run's last-turn trap: restore and finish could not share the last turn). A session
    that stops without a finish (turn limit, failed request) records the plan notes as `result.finish`
    (`recorded_by: host`, the last note plus every turn's note) and delivers the last revision the editor REVIEWED
    (the current revision of the last turn's snapshot): an edit made on the last turn is rendered and kept as
    evidence (`unreviewed_revision`, flag `look_final_edit_unreviewed`), never delivered unseen.
  - Never: a vertex position, normal, UV or index, a texture pixel, a part, a logo. A shape problem is named in the
    finish note (the prompt calls INVU's cyan stem geometry, not a colour to paint).
- Guards, all before a revision is committed (a failure rejects the plan, restores the state and records the error
  text for the editor's next turn): `geometry_check` (bufferView bytes and accessor/view records behind every
  POSITION, NORMAL, TEXCOORD_0 and index accessor, the attribute layout, every image, and `document`: the whole glTF
  JSON outside the editable material fields - the three PBR factors, the lens `appearance`, a material's name and
  extras - identical to S9, so texture bindings, samplers, texCoord, KHR_texture_transform, COLOR_0 / TANGENT /
  TEXCOORD_1, morph targets, sparse records and node transforms are locked; the BIN chunk byte-identical: a look
  writes the JSON chunk only); `contract.check`; the see-through floor (a CERTIFIED lower bound of the luminous
  transmission over the whole density gradient, not only at the knots: per interval a fine sample minus the
  smoothstep slope bound, 2026-09-27; >= 0.03 from head-on to 60 deg incidence, ISO 12312-1 category 4: a measured angle table can make a lens
  opaque at 20-30 deg while head-on stays clear; every m2 S9 lens is >= 0.047 there, and the floor follows S9 where
  an export is darker); an observation in the actual AR runtime that is runtime_compatible with every lens node
  detected.
- Observation per revision (~36 s: two harness launches; the labels, maps and sheets ~2 s), images in this order
  (7, 8 after the first edit; at most `MAX_IMAGES` 12; the context stays < 1 MiB):
  - S7's fit views on white (front, asset-back, yaw +80/-80) as [photo | render] sheets; each side render is paired
    with the photo of the side its camera sits on, by `camera_in_asset`, never by a fixed yaw sign (yaw +80 puts
    the camera at -X). Every view has ONE name (`fit_view_name`: harness view id, its yaw from `FIT_RENDER_VIEWS`,
    the camera side; +X = the wearer's left = the `*_R` parts, export's naming) used verbatim in the sheet header,
    the image label (= images.json) and the context's `views` table (the dry run saw "yaw +80" next to side_neg).
  - `material-map`: the revision's own GLB ray-cast through each fit render's exact camera (`label_views`, S7's
    labelling rule) in the front and one side view: one flat colour per material, dark material boundaries, lens
    pixels striped with the material seen through the lens (pale over the backdrop), and a legend (material, what
    edits it, nodes, pixel share per view) - so the editor sees which material paints e.g. a grey bridge.
  - `lens`: per view (front, left, right) a photo crop of the lens region (the render's lens box mapped into the
    photo's S0 glasses box; approximate) beside the render's lens crop, each filling a 600 x 500 tile, with the
    median incidence angle on the lens, the parameter that sets the colour there (head-on: transmission and
    normal_reflectance_rgb; steep, the side views: grazing_reflectance_rgb) and the current lens's reflectance /
    transmission at that angle (`lens_views` in the context). S8's lens-colour check is measured once; this sheet
    is per revision.
  - the try-on (front / yaw 35 / roll 25 on the checker, room light), each view cropped to the glasses and scaled
    to >= 640 px wide (`tryon_sheet`; renders are 720 x 480), plus, after the first edit, baseline-vs-current at the
    same crops.
  A material-map or lens-sheet failure is recorded (`sheet_errors`, the context's `image_problems`), never hidden
  and never a reason to reject the revision. Renders are synthetic AR poses, not the photo cameras; the editor
  judges by eye (no per-revision numeric comparison with the photos).
- Held-out rule: the angled PHOTO is never copied into the inputs, drawn into a sheet or sent (`prepare_inputs`
  copies `core.FIT_VIEWS` only; `load_inputs` refuses a held-out photo; tested with a magenta angled photo). The
  yaw-35 try-on render is a render of the asset, not that photo.
- Drivers: `none` (the pipeline default: r0000 = the S9 bytes, observed, verdict `not_edited`); `scripted` (a local
  JSON list of typed plans, no network); `manual` (`python -m bsa.look` only: writes a request package per turn and
  waits for `turn-NNNN.json`; `settle_turns` records a waiting turn as `awaiting_plan` and clears the job's
  AwaitingPlan error from a turn once its plan is applied, so an applied manual turn reads `applied` with an empty
  `error_type` everywhere, and a waiting session's report says `awaiting_plan`, not `needs_attention`); `astra`
  (PAID: `run_astra_job` with `LOOK_PROMPT`, one explicit request per turn, no retries, a failed or uncertain
  request stops the session as `needs_attention`).
- Authorization: `astra` needs `--authorize-paid-astra`, a ledger `--astra-budget` and `--astra-maximum-calls` 1..10
  (failed attempts count); every other driver refuses those flags. The ledger is ONE owner-named file per
  authorization and must live outside every `s11_look` folder (refused otherwise); from the pipeline
  `--look-driver astra --authorize-paid-astra --look-astra-maximum-calls N --look-astra-budget PATH`, the cap bounds
  the whole command (every product shares the ledger; a product listed twice runs once) and re-running the same
  command spends only what is left: a product is skipped (nothing set aside) when the ledger is exhausted, and
  `--plan` prints the calls used and the most the command could make. Each session names its request folders
  `turns/turn-NNNN/api-<session_id>` (run_astra_job's `request_folder` hook), so a fresh session never collides
  with an old reservation. A session's turns = min(`--astra-max-turns`, the calls left): the editor is never told
  of a turn it cannot pay for. result.json reports `paid_calls_used` (this session) and `ledger` (path, cap,
  reservations in total, superseded sessions included); pipeline summary.json `look_ledger`. Until 2026-09-25 each
  product had a ledger inside its own s11_look: `--fresh` renamed it away, so a crash after the paid call, a
  Ctrl-C mid-call or any stale re-run followed by the same command spent the cap again (probes: 2 posts for a cap
  of 1), and one command could spend 5 caps. Writing into the real m1 run needs `--allow-m1` (pipeline:
  `--allow-m1`, alias `--look-allow-m1`).
- Session provenance: `look.implementation()` pins look.py, archeck.py, contract.py, export.py, core.py, the
  job/transport/lens-schema files of reconstruction/ and, by bytecode (`pinned_functions`), texture's `glb_split` /
  `glb_pack` / `ar_render` / `glb_primitives` and the shared helpers it runs (reconstruction/job.py's JSON,
  inventory, verify and lock helpers, segmented_astra_session `digest` / `read`, segmented_providers `pin` /
  `verified`, the schema builders, `replace_with_retry`, qa.provider_benchmark `digest`); a change refuses to reopen
  a session. The per-observation renderer digest is the pipeline's `ar_runtime` digest (one file list). A manual
  plan binds to its package (`request.json`: the session's request folder, the context, images and schema digests):
  a plan written for another session or snapshot is refused. `--fresh` renames the old stage folder to
  `s11_look.superseded-<UTC stamp>` (never deleted).
- Outputs: `model.glb` (the final revision; the S9 bytes for r0000), `look.json` (cumulative patch, input/output
  sha256, checks), `result.json` (status, verdict, driver, revisions, turns, `paid_calls_used`, `s10_decision`,
  `final_decision`, `geometry_identical`, `contract_ok`, `ar_runtime_compatible`, flags), `sheets/`, `inputs/`,
  `session/`. An exception or a Ctrl-C leaves `result.json` with `status: failed`, `paid_calls_used` and `ledger`
  (the pipeline re-runs it); a hard kill leaves none (re-run reason `missing`). Either way the owner's ledger bounds
  what a re-run can spend.
- reconstruction/ (DESIGN hard rule 2 notwithstanding) got three backward-compatible hooks for S11:
  `AstraClient(instructions=)` (a default client describes itself exactly as before, so the live segmented sessions'
  stored bindings still match; `test_existing_segmented_session_bindings_still_match`), `run_astra_job(session_cls=,
  tools_schema=)` and the session's `request_folder` (default `api`). run_astra_job's `paid_calls_used` is the ledger
  total, as before; bsa.look counts its own session.

### Orchestration — `bsa/pipeline.py`
`python -m bsa.pipeline --run m2 --product vb [--from s4] [--to s8] [--force] [--plan]` runs the stages in order
(`--to` defaults to s11); `--all` runs the 5 products. `--run` is REQUIRED (until 2026-09-25 it defaulted to m1),
and a run that resolves to the real m1 folder (compared as folders, `look.is_real_m1`: `M1` or `x/../m1` count) is
refused for every stage unless `--allow-m1` (`--plan` stays read-only). The stage modules' own CLIs still default to
`--run m1` (changing them would mark every recorded stage `code_changed`). Writes `data/bsa/runs/<run>/summary.json` and a review sheet
per product (photos | BSA renders | S11 look renders when S11 ran | previous candidate renders). `BSA_DATA_DIR` (env)
moves the run artifacts (a scratch or reproducibility run, hermetic tests; the ground truth stays under
`data/bsa/ground_truth`).
- S11 (`look_all`): after the gate, in-process, one product at a time, for the products exported and gated without
  failure in this invocation (a product without an S9 GLB or an S10 result is skipped; the real m1 is skipped unless
  `--allow-m1`). Flags `--look-driver none|scripted|astra` (default none), `--look-script` (a plan list, or a
  folder of `<product>.json`), `--look-max-turns`, and the astra pass-through above (`--look-astra-budget` is
  required). Every S11 run is `--fresh`. It re-runs when stale, forced, when its result recorded `status: failed`,
  or when an EDITING driver is asked for and the recorded driver (or script sha256) differs; `none` never displaces
  an edited look, not even a stale one (it is kept, reported `skipped: stale edited look kept (...)`, and re-run
  only by an editing driver or `--from s11 --force`). Its fingerprint = its bsa closure + `ar_runtime` +
  `look_external` (the reconstruction/ files `look.implementation()` pins plus its `pinned_functions`).
- Staleness (make-like): a stage runs when its result is missing, older than a stage it reads (`DEPS`, derived from
  the code and enforced by `test_deps_cover_the_reads`, which also scans the helper modules of each stage's code
  closure), or written by other code. Code provenance: a stage's code = its module + every `bsa` module it imports
  (transitively) + `__init__`, and for S7/S8/S9/S11 the `ar_runtime` digest (ar/src/**/*.ts, ar/package.json,
  ar/qa/provider-comparison*.{mjs,html,js}, automation/qa/provider_comparison*.py: they choose or judge from renders
  of that runtime; until 2026-09-25 the harness glob matched no file). The pipeline fingerprints and
  imports everything before running (`load_code`) and writes `<stage>/code.json` per executed stage; states
  `current` / `code_changed` / `code_newer` (mtime fallback) / `unrecorded`; modules edited during a run are
  reported (`code_changed_during_run`). Not fingerprinted: reconstruction/ (except S11's `look_external`), data
  inputs.
- A product whose upstream chain failed is BLOCKED: it is not exported, AR-checked or gated from mixed-age
  artifacts (`blocked_products`), and the process exits non-zero.
- Run-level M1 verdicts need all 5 products (a partial run reports `pass: null`); c2 fails when any product whose
  c2 is applicable is not passed (an unevaluated applicable c2 is a failure, not a silent drop).

## Ground truth (M0) — `data/bsa/ground_truth/`
`<product>.json`: `photo` (view), `photo_sha256`, `image_size`, `lenses`: [{`side`, `points_px`
[[u, v], ...] closed polygon at native resolution, `segment_types` per point ('frame' | 'free' |
'rimless'), `method`: 'ai_visual_trace' (independent of the detector and of S2), `confidence`,
`notes`}], plus `<product>_overlay.png`. Two lenses per product (10 total).

## M1 pass criteria (from the plan)
1. 5/5 GLBs pass `contract.check` and load in the AR harness with every lens node detected.
2. Lens edge vs ground truth <= 0.3 mm mean on frame-bounded segments.
3. Held-out angled front-piece contour <= previous candidate + 0.1 % W on at least 4/5 products.
4. Zero seam gaps.
Then the owner's blind rating (prepared by the lead, not by the pipeline).

## Tests
`automation/tests/test_bsa_<module>.py`, stdlib `unittest`, synthetic fixtures for the maths plus
real-data smoke tests that skip when `data/` is missing. Keep each test file under ~90 s. Run with
`python -m unittest discover -s tests -p "test_bsa_*.py"` from `automation/`.
