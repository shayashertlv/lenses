# glasses3d progress log

Format (PLAN.md §4): date, WP, what was done, commit hash, $0-proof numbers, open issues, next step.
A new session reads this right after PLAN.md §0 and resumes from the first unfinished step.

## Status

| WP | State | Commit |
|---|---|---|
| WP0 | **done, $0 proof passed** | (this commit; see git log) |
| WP1 | in progress: A (1870359) and B-D2 (960ad54) committed; R/E/F/G running (workflow g3d-wp1-capture-proof) | 960ad54 |
| WP3 | **done, $0 proof passed** (review defects fixed + re-verified) | 6acfc5a |
| WP3b | port + $0 proof done; **waiting for the owner's WP5 registration threshold** | (WP3b commit) |
| WP4 | design done (plan/WP4_DESIGN.md, judged panel of 3); build running (workflow g3d-wp4-build: core+P0/P1, tools, proofs P2-P5); P6 visual after WP1 | - |
| WP5, WP6, WP2, WP7, WP8 | not started | - |

## 2026-10-05: WP0 (setup, ignore rules, port review)

Done:
- Branch `glasses3d` created from main 7e25722 (no push).
- `.gitignore`: the PLAN block, plus a deliberate re-include of `/glasses3d/tests/fixtures/**` (the global `*.png`/`*.jpg`
  rules otherwise hide fixtures) and a re-ignore of `*.glb`/`*.blend`/`*.blend1` inside fixtures, so no model binary can
  enter Git. Verified with `git check-ignore -v --no-index` on 21 sample paths (data/, output/, dist/, qa/output/,
  .venv/, venv/, glb, blend, blend1, webp, pkl, npz, arview/modeling, arview/site; fixtures png/jpg included; fixture
  glb/blend ignored; plain .py/.ts not ignored; .env ignored).
- `glasses3d/.venv` (Python 3.12.10): numpy 2.5.3, scipy 1.18.1, opencv-python 5.0.0.93, scikit-image 0.26.0,
  pillow 12.3.0, pytest 9.1.1, **PyPatchMatch 2.0.1 (Windows wheel `pypatchmatch-2.0.1-py3-none-win_amd64.whl`)**.
  Verified: `patchmatch.inpaint(image, mask, *, global_mask=None, patch_size=15)` fills a hole inside a uniform
  (200,80,40) region with exactly (200,80,40) and leaves every pixel outside the mask byte-identical.
- Reference data copied from `C:/Users/Shay/PycharmProjects/glasses3d-reference/` to `glasses3d/data/reference/`
  (ignored): projection/ 2.4 MB, junction/ 29 MB.
- Prototype constants repointed relative to `glasses3d/` (`Path(__file__).parents[3]`): make_uv_test.py `OLD`/`HERE`
  (HERE = `data/reference/projection/rerun/`), probe_fit.py and probe_project.py `HERE`, run.mjs `AR`
  (= `../../../../ar`), and all three ar_capture configs (`output` → `data/reference/ar_capture/<name>`; case paths →
  `data/reference/junction/copies/`). config-ab4.json also pointed at a dead temp folder; fixed too. The prototypes
  still import `automation/bsa` (prototype-only; the pipeline never will).

$0 proof (WP0.4): `make_uv_test.py` re-run from scratch (no cached cameras), 4 min:
- coverage Brow_L 0.727, Brow_R 0.712, Temple_L 0.833, Temple_R 0.570: identical to PLAN §1;
- re-fitted `cam_reference-0{2,3,4}.png.pkl` and `ft1119_uv_sources.png` byte-identical to the originals
  (the fit is deterministic);
- fit quality (WP3b baseline): ref-02 back IoU 0.897 / contour 5.46 px; ref-03 side: 'right' prior IoU 0.738 /
  11.47 px (the 'left' prior gives 0.384 / 34.1 px); ref-04 top: 'top' IoU 0.830 / 5.24 px ('topb' 0.699 / 10.6 px);
  earlier probe cameras: ref-05 front 0.898, ref-01 angled 0.869, ref-06 (elevated side, 'left' prior) 0.709.

- **PORT_REVIEW.md** (WP0.5): 17-agent review of the newest Studio (`source-before/blender_agent`, 52 files,
  12.5k lines): keep 6 / rewrite 23 / drop 23, five verdicts corrected by adversarial second reviewers; 53 test files
  reconciled; docs reviewed (facts carried forward); needs nothing provides listed. Full evidence in
  `plan/port_review/studio.json`. Audit-flagged gates (construction labels, selection citation gates, correction-family
  caps, source_field two-colour ramp) all dropped. No code copied yet (copies land in WP1/WP2/WP3).
- **Blender connection decision (with evidence, PORT_REVIEW §1): no MCP server; glasses3d owns a headless Blender
  worker** (`blender -b`, ephemeral-port JSON RPC, all Astra tools host-side). The community mcp-for-blender the
  Studio used bans numpy but allows bpy file I/O (the opposite of WP2's safe mode), refuses `-b`, and drops calls at
  180 s while Blender keeps running; the Lab MCP has no sandbox hook, ignores render output paths and is GPL outside
  the repo; both default to port 9876.

Facts recorded for owner decisions (PORT_REVIEW §2b): every praised Studio run used **max** effort (PLAN §2
recommends high); accepted FT1123 GLB = tomford-scratch-002/final-champagne sha 0e77e11e…; the description bug is
confirmed (studio_jobs.py:224/405-426); owner verdicts also depended on viewer settings (lens-reflection multiplier,
width, clip).

WP3 groundwork found while WP0 ran (not yet committed; lands with WP3):
- Studio scenes (8c82 agent/scene.blend, 3b8a original/scene.blend): METRIC, scale_length 0.001 (millimetres), scene
  property `blender_agent_axes` = '+Y up; +Z front'; **origin marker = scene property `mdl_bridge_underside`** (float[3]);
  every mesh has `partRole` and `LENSES_part_visibility`; Prada's scene has an "Inspection only | excluded from
  delivery" collection (camera + 3 lights).
- `g3d_host/glb.py`, `lens_id.py`, `spec.py` (validate_spec, exactly the §2 rows) + 47 passing tests. On real files:
  q001, Prada, INVU pass; ar_v4 crystal r9 fails ONLY the origin row (bridge underside at y +10.23 mm), which WP1's
  report must say.

Next step: WP1 (arview). The WP1 read-only analysis workflow (per-file fork verdicts, throw-site classification,
lens-id and capture design) is running.

## 2026-10-05 (later): WP1 / WP3 / WP3b in parallel (state for a resuming session)

- WP1 analysis (14 agents) -> `plan/port_review/arview.json`, PORT_REVIEW §7, binding decisions in `plan/WP1_DESIGN.md`.
  Stage A (verbatim copy, 135 files kept, 93 dropped) committed 1870359; `npm ci` done in `glasses3d/arview`; baseline was
  19 TS errors (imports of dropped modules). Stages B (build), C (lens id), D1 (load front-end + acceptance policy), D2
  (temples/clip/continuity/fit scale) run as one sequential workflow (implement -> adversarial review -> fix, max 2 rounds).
  Remaining after it: stage E (capture CLI), F (webcam page), G ($0 proof incl. r9 refusal recorded on main's ar/).
- WP3: `g3d/export_glb.py` (+ glb_patch, cli), `g3d_host/blender.py` (export_copy, export_and_validate), validator. $0 proof
  passed before review (flat facets, clearcoat 0.8, 64x48 embedded image, q001 + Prada scenes export and pass). The review
  (28 agents) reproduced real defects: upside-down/roll/pitch models passed; shields false-failed; unmeasurable origin
  passed; instancing ignored; validator crashed on bad indices; exporter corrupted custom normals on mirrored objects, used
  viewport evaluation, ignored excluded collections, lost relative textures silently; facet test could not detect a 40°
  re-smooth; texture test had no UVs. Calibrated thresholds (8 real models) now in WP1_DESIGN §1. Fix + re-verify running.
- WP3b: porting automation/bsa cameras into `g3d_host/` (camera, raster, cameras, matte, texture_utils, fitmesh,
  fit_views); acceptance = reproduce the prototype's three FT1119 fits exactly, then measure all 12 reference views
  (6 FT1119 vs q001, 6 Prada vs the delivery), report IoU and contour error in px and mm, then ASK THE OWNER for the WP5
  registration threshold. open3d 0.20.0 installed in the venv.

## 2026-10-05: WP3 done (6acfc5a)

- Exporter `g3d/export_glb.py` + validator `g3d_host/spec.py` (see the commit message for the full list).
- $0 proof: flat facets survive (12-gon temple, per-corner NORMAL directions read back), smooth parts stay smooth,
  sharp-edge / weighted-normal / mirrored / non-uniformly-scaled custom normals match Blender's evaluated corner normals
  within 0.025°, clearcoat 0.8 (roughness 0.05), UV-mapped 64x48 embedded image, transmission; the q001 and Prada
  scene.blend files export and pass validate_spec with their sha256 unchanged; all 41 unique Studio/live GLBs pass;
  ar_v4 r9 fails only the origin row (bridge underside +10.23 mm).
- Adversarial review: 28 agents, 25 reproduced defects (upside-down/roll/pitch models passed, shields false-failed,
  crashes on bad indices, mirrored custom normals corrupted, viewport-vs-render evaluation, excluded collections,
  relative textures lost, weak facet/texture tests). All fixed; re-verified by a separate agent; mutation tests 13/13
  killed. Suite: 228 passed, 1 skipped.
- Orientation thresholds (hard; calibrated on 8 real models): temple roots above the lens centre (real +4.0..+18.0 mm),
  left/right lens halves within 3 mm (real <= 0.16), temple slope < 15° (real -9.4..+4.7). Origin: frame + lens
  triangles sliced at x in {-1..1} mm (shields), x judged on the lens box; unmeasurable origin fails.
- Known limit: Draco/meshopt geometry is not decoded by the Python reader; affected rows become 'unverified'
  warnings only where a compressed primitive's POSITION bounds could change the result.
- Convention: KHR_materials_volume lengths are authored in METRES (Studio convention); validate_spec warns above 0.01.

## 2026-10-05: WP3b port + $0 proof done (waiting for the owner)

- Ported (PORT_REVIEW §9): `g3d_host/camera.py`, `raster.py` (open3d ray casting), `cameras.py` (ViewFit, priors incl.
  first-class top/topb and top_oblique_left/right, contour ICP, Powell, native_metrics), `matte.py` (quadratic
  background), `texture_utils.py` (de00, quantile_gain, rasterize_triangles, nearest_fill, sample_bilinear,
  visible_points), `fitmesh.py`, `fit_views.py` (driver + CLI). No runtime import of automation/reconstruction
  (a test enforces it).
- Exact reproduction: under **scipy 1.16.0 (now pinned)** the ported production path reproduces the prototype's three
  FT1119 cameras on all 21 fields within 1e-6. scipy 1.18.1 drifts up to 0.19° / 1.07 px (traced to a ~1e-8
  least_squares step difference amplified by accept/reject; the original automation code drifts identically).
- 12-view baseline (mean contour mm / p90 mm; px ÷ px/mm at the front pivot), outputs `data/wp3b/`:
  FT1119 vs q001: front 0.66/1.46, rear 0.68/1.53, top 0.69/1.84, angled 0.79/1.87, elevated side 1.05/2.81,
  side 1.44/6.16 (model shape differs: brow ~4 mm higher in the photo, fit turns to show the far temple).
  Prada vs delivery: front 0.26/0.59, top 1.24/3.33, angled 1.27/4.16, side-05 1.99/4.22, rear 2.76/11.4,
  side-01 5.58/20.6 (matte keeps floor reflections and fills the gap between temples: an upper bound; delivered
  temples also differ from the photos = the WP6 failure).
- Ref-06 (elevated side): PLAN §1's "needs a proper prior" is wrong: the photo sits at ~19° pitch; with seeds there
  the fit equals the 'left' result (IoU 0.708). The 0.71 ceiling comes from thin temples (contour error 1.05 mm).
- **Owner question (blocks WP3b "done" and WP5's stop rule): the registration threshold.** Recommended: mean contour
  <= 1.0 mm and p90 <= 2.5 mm per view used for projection (FT1119: front, rear, top, angled qualify).

## 2026-10-05: WP1 stages B-D2 committed (960ad54)

tsc 0 errors, node tests 476/476, qa 18/18, vite build OK; ar/ untouched. Each stage implemented, adversarially reviewed,
fixed. Stage D1's round-2 fix (default-scene-only parse via defaultSceneDocument/packGlb; webcam page listing failed §2
rows) was not re-reviewed inside that workflow: stage R of g3d-wp1-capture-proof reviews it and aligns arview's origin-x
and orientation checks with the fixed validator (agreement test on shared fixtures). Remaining WP1: R, E (capture CLI),
F (webcam page `arview serve`), G ($0 proof: r9 refusal on main's ar/, captures of r9 / q001 / Prada / mm copy / renamed
multi-mesh copy / temples-removed copy, Python-vs-arview agreement table, q001 vs prototype seated junction).
PORT_REVIEW §7: `tests/lens-appearance.test.ts` row still says keep (dropped in stage B with its module) - fix at the WP1 commit.

## 2026-10-05: WP4 design (plan/WP4_DESIGN.md)

Judge panel: 3 independent designs (robust 45, minimal 47, physical 42 of 60), synthesized on the minimal base. Measured facts
that decided it: all four real lenses (FT1119 q001, Prada, Oakley, INVU) are closed manifold solids with a back cap translated
along -Z and one wall ring; FT and INVU caps are toric (no sphere growth); §3 defaults as hard thresholds would fail accepted
models (FT eyewire capture 0.40 vs 0.45; Prada 65% of stations reach 0.8; INVU top rail 0.10), so pass = §3 lower bound,
target = §3 default, per station, growth only where the grown edge stays hidden; 42-45% of each shield edge is exposed and
never grown (Oakley must move 0 vertices). FT1119 browline: hide_eyewire with per-station lift (0.84 mm median) over x
11.6-56.2 mm; must match the prototype seated+rimsplit numbers (top edge inside 100%, band 0, rim split 78-102 deg).
Open risk the owner may want to know: under §3, Prada (~26% of nodes, <=0.45 mm) and INVU (~267 top nodes, <=0.9 mm) change
at HIDDEN stations inside opaque material, plus Prada's rim sharpening (intended rimsplit fix).
