# glasses3d progress log

Format (PLAN.md §4): date, WP, what was done, commit hash, $0-proof numbers, open issues, next step.
A new session reads this right after PLAN.md §0 and resumes from the first unfinished step.

## Status

| WP | State | Commit |
|---|---|---|
| WP0 | **done, $0 proof passed** | (this commit; see git log) |
| WP1 | next: analysis running (read-only) | - |
| WP3, WP3b, WP4, WP5, WP6, WP2, WP7, WP8 | not started | - |

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
